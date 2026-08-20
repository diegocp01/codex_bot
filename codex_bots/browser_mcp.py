"""A small, isolated Playwright MCP server for Codex Bots."""

from __future__ import annotations

import argparse
import ipaddress
import re
import socket
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings.sources.utils import IncompleteFieldDefinitionWarning

warnings.filterwarnings("ignore", category=IncompleteFieldDefinitionWarning)

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright


RISKY_ACTION = re.compile(
    r"\b(buy|purchase|pay|transfer|send|place order|checkout|publish|post|tweet|share|upload|"
    r"delete|remove|confirm|submit|apply|accept offer|sign contract|book|reserve|save changes)\b",
    re.IGNORECASE,
)
INTERACTIVE_SELECTOR = "a, button, input, textarea, select, [role=button], [role=link], [contenteditable=true]"


def _is_public_url(url: str, *, allow_private: bool = False) -> tuple[bool, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "Only http and https URLs are allowed."
    if parsed.username or parsed.password:
        return False, "URLs containing credentials are not allowed."
    if allow_private:
        return True, ""
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False, "Local and private-network pages are blocked."
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)}
    except socket.gaierror:
        return False, "The hostname could not be resolved."
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            return False, "Local and private-network pages are blocked."
    return True, ""


class BrowserSession:
    def __init__(self, profile: Path, *, headless: bool = True, allow_private: bool = False) -> None:
        self.profile = profile
        self.headless = headless
        self.allow_private = allow_private
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.refs: dict[str, object] = {}

    async def ensure_page(self) -> Page:
        if self.page and not self.page.is_closed():
            return self.page
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.profile),
            headless=self.headless,
            accept_downloads=False,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            service_workers="block",
        )
        await self.context.route("**/*", self._route_request)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self.page

    async def _route_request(self, route) -> None:
        allowed, _ = _is_public_url(route.request.url, allow_private=self.allow_private)
        if allowed:
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        self.page = None
        self.context = None
        self.playwright = None
        self.refs = {}

    async def snapshot(self) -> str:
        page = await self.ensure_page()
        self.refs = {}
        lines = [
            f"Page: {await page.title() or 'Untitled'}",
            f"URL: {page.url}",
            "Interactive elements:",
        ]
        elements = page.locator(INTERACTIVE_SELECTOR)
        for index in range(min(await elements.count(), 120)):
            locator = elements.nth(index)
            try:
                if not await locator.is_visible():
                    continue
                ref = f"e{len(self.refs) + 1}"
                self.refs[ref] = locator
                role = await locator.get_attribute("role") or await locator.evaluate(
                    "el => el.tagName.toLowerCase()"
                )
                label = (
                    await locator.get_attribute("aria-label")
                    or await locator.get_attribute("placeholder")
                    or await locator.get_attribute("name")
                    or await locator.inner_text(timeout=500)
                    or await locator.get_attribute("value")
                    or "unlabelled"
                )
                lines.append(f"[{ref}] {role}: {' '.join(label.split())[:180]}")
            except Exception:
                continue
        body = " ".join(((await page.locator("body").inner_text(timeout=1500)) or "").split())
        lines.extend(["", "Visible page text:", body[:8000]])
        return "\n".join(lines)

    async def locator(self, ref: str):
        locator = self.refs.get(ref)
        if locator is None:
            await self.snapshot()
            locator = self.refs.get(ref)
        if locator is None:
            raise ValueError(f"Unknown element reference: {ref}. Take a new snapshot and use one of its refs.")
        return locator


def build_server(profile: Path, *, headless: bool = True, allow_private: bool = False) -> FastMCP:
    session = BrowserSession(profile, headless=headless, allow_private=allow_private)
    server = FastMCP(
        "Codex Bots Browser",
        instructions=(
            "Use snapshots and element refs to operate one isolated Chromium profile. "
            "High-impact clicks, password entry, downloads, and private-network navigation are blocked."
        ),
        log_level="ERROR",
    )

    @server.tool(description="Open a public web page in the isolated Bot browser and return a fresh snapshot.")
    async def browser_open(url: str) -> str:
        allowed, reason = _is_public_url(url, allow_private=allow_private)
        if not allowed:
            return f"Navigation blocked: {reason}"
        page = await session.ensure_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        return await session.snapshot()

    @server.tool(description="Inspect the current page as text plus stable refs for visible interactive elements.")
    async def browser_snapshot() -> str:
        return await session.snapshot()

    @server.tool(description="Click a visible element by snapshot ref. High-impact actions require the user to take over.")
    async def browser_click(ref: str) -> str:
        locator = await session.locator(ref)
        label = " ".join(
            filter(
                None,
                [
                    await locator.get_attribute("aria-label"),
                    await locator.get_attribute("title"),
                    await locator.inner_text(timeout=500),
                ],
            )
        )
        surrounding = await locator.evaluate(
            "el => [el.getAttribute('href'), el.getAttribute('formaction'), el.closest('form')?.innerText].filter(Boolean).join(' ')"
        )
        if RISKY_ACTION.search(f"{label} {surrounding}"):
            return f"Click blocked: “{label[:160]}” may cause a high-impact external action. Ask the user to complete it manually."
        await locator.click(timeout=10_000)
        await (await session.ensure_page()).wait_for_timeout(500)
        return await session.snapshot()

    @server.tool(description="Fill a text field by snapshot ref. Password fields are never filled by this tool.")
    async def browser_type(ref: str, text: str) -> str:
        locator = await session.locator(ref)
        if ((await locator.get_attribute("type")) or "").lower() == "password":
            return "Typing blocked: ask the user to enter passwords and other secrets directly."
        await locator.fill(text, timeout=10_000)
        return await session.snapshot()

    @server.tool(description="Press a keyboard key, such as Enter, Escape, ArrowDown, or Tab, on the current page.")
    async def browser_press(key: str) -> str:
        if key.lower() in {"enter", "space", "printscreen", "meta+v", "control+v"}:
            return "That key is blocked. Use a referenced click so consequential actions can be reviewed."
        page = await session.ensure_page()
        await page.keyboard.press(key)
        await page.wait_for_timeout(350)
        return await session.snapshot()

    @server.tool(description="Capture the current viewport so the Bot can visually inspect the page.")
    async def browser_screenshot() -> Image:
        return Image(data=await (await session.ensure_page()).screenshot(type="png"), format="png")

    @server.tool(description="Close the isolated browser process. The per-Bot profile remains on this machine.")
    async def browser_close() -> str:
        await session.close()
        return "Browser closed. The local profile was preserved."

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex Bots Playwright MCP server")
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--visible", action="store_true", help="Show the Chromium window")
    parser.add_argument("--allow-private", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    build_server(args.profile, headless=not args.visible, allow_private=args.allow_private).run("stdio")


if __name__ == "__main__":
    main()
