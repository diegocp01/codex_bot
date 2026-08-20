"""Codex SDK execution and local multi-Bot orchestration."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .browser_runtime import browser_config_overrides, browser_status
from .db import Store
from .roles import build_instructions, build_output_schema

LOGGER = logging.getLogger(__name__)


class RunStopped(Exception):
    """Raised when the user stops a run before it returns a result."""


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    turn: Any = None
    cancel_requested: bool = False


class CodexService:
    """Runs one persistent Codex thread per Bot and coordinates handoffs."""

    def __init__(
        self,
        store: Store,
        workspace: str | Path,
        *,
        model: str = "gpt-5.6-sol",
        max_workers: int = 3,
        synchronous: bool = False,
        runner=None,
        browser_root: str | Path | None = None,
    ) -> None:
        self.store = store
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.synchronous = synchronous
        self.runner = runner or self._run_with_sdk
        self.browser_root = Path(browser_root or self.workspace.parent / "instance").resolve()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="codex-bot")
        self._active: dict[str, ActiveRun] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        bot_id: str,
        prompt: str,
        *,
        origin_bot_id: str | None = None,
        depth: int = 0,
        trigger: str = "user",
        routine_id: str | None = None,
    ) -> str:
        run = self.store.create_run(
            bot_id=bot_id,
            prompt=prompt,
            trigger=trigger,
            routine_id=routine_id,
        )
        run_id = run["id"]
        with self._lock:
            if bot_id in self._active:
                self.store.set_run_status(run_id, "failed", "This Bot is already working.")
                raise RuntimeError("This Bot is already working.")
            self._active[bot_id] = ActiveRun(run_id=run_id)
        self.store.set_status(bot_id, "working", "Thinking with Codex")
        args = (bot_id, prompt, origin_bot_id, depth, run_id, routine_id)
        if self.synchronous:
            self._execute(*args)
        else:
            try:
                self.executor.submit(self._execute, *args)
            except Exception:
                with self._lock:
                    self._active.pop(bot_id, None)
                self.store.set_run_status(run_id, "failed", "The local worker could not start.")
                self.store.set_status(bot_id, "error", "Could not start")
                raise
        return run_id

    def interrupt(self, bot_id: str) -> bool:
        with self._lock:
            handle = self._active.get(bot_id)
            if handle:
                handle.cancel_requested = True
        if not handle:
            return False
        self.store.set_run_status(handle.run_id, "stopping")
        self.store.set_status(bot_id, "working", "Stopping")
        if handle.turn is None:
            return True
        try:
            handle.turn.interrupt()
            return True
        except Exception:
            LOGGER.exception("Failed to interrupt Bot %s", bot_id)
            return False

    def is_active(self, bot_id: str) -> bool:
        with self._lock:
            return bot_id in self._active

    def _execute(
        self,
        bot_id: str,
        prompt: str,
        origin_bot_id: str | None,
        depth: int,
        run_id: str,
        routine_id: str | None,
    ) -> None:
        try:
            bot = self.store.get_bot(bot_id)
            if not bot:
                raise RuntimeError("Bot not found.")
            with self._lock:
                active = self._active.get(bot_id)
                if not active or active.cancel_requested:
                    raise RunStopped()
            self.store.set_run_status(run_id, "working")
            payload = self.runner(bot, prompt, depth)
            with self._lock:
                active = self._active.get(bot_id)
                if active and active.cancel_requested:
                    raise RunStopped()
            message = str(payload.get("message") or "I finished, but did not return a summary.").strip()
            self.store.add_message(
                bot_id=bot_id,
                role="assistant",
                author_name=bot["name"],
                content=message,
            )
            if origin_bot_id and origin_bot_id != bot_id:
                self.store.add_message(
                    bot_id=origin_bot_id,
                    role="assistant",
                    author_name=bot["name"],
                    content=message,
                    kind="handoff_result",
                    metadata={"from_bot": bot_id},
                )
            if depth < 2:
                self._dispatch_handoffs(bot, payload.get("handoffs") or [], depth)
            self.store.set_run_status(run_id, "completed")
            if routine_id:
                self.store.set_routine_result(routine_id, "completed")
            self.store.set_status(bot_id, "idle", "")
        except RunStopped:
            self.store.add_message(
                bot_id=bot_id,
                role="system",
                author_name="Codex Bots",
                content="This run was stopped before completion.",
                kind="stopped",
            )
            self.store.set_run_status(run_id, "stopped")
            if routine_id:
                self.store.set_routine_result(routine_id, "stopped")
            self.store.set_status(bot_id, "idle", "")
        except Exception as exc:
            LOGGER.exception("Codex Bot %s failed", bot_id)
            error_text = self._friendly_error(exc)
            self.store.add_message(
                bot_id=bot_id,
                role="system",
                author_name="Codex Bots",
                content=error_text,
                kind="error",
            )
            if origin_bot_id and origin_bot_id != bot_id:
                self.store.add_message(
                    bot_id=origin_bot_id,
                    role="system",
                    author_name=bot["name"],
                    content=f"The handoff could not finish. {error_text}",
                    kind="error",
                )
            self.store.set_status(bot_id, "error", "Needs attention")
            self.store.set_run_status(run_id, "failed", str(exc).strip() or type(exc).__name__)
            if routine_id:
                self.store.set_routine_result(routine_id, "failed")
        finally:
            with self._lock:
                self._active.pop(bot_id, None)

    def _run_with_sdk(self, bot: dict, prompt: str, depth: int) -> dict:
        codex_bin = os.environ.get("CODEX_BIN") or shutil.which("codex")
        if not codex_bin:
            raise RuntimeError("Codex CLI was not found in PATH. Install it and run `codex login`.")

        teammate_context = self.store.recent_teammate_context(bot["id"])
        context_block = f"\n\nRecent teammate context:\n{teammate_context}" if teammate_context else ""
        teammates = self.store.list_bots()
        instructions = build_instructions(
            bot,
            str(self.workspace),
            allow_handoffs=depth < 2,
            teammates=teammates,
        )
        overrides = (
            browser_config_overrides(bot["id"], self.browser_root)
            if browser_status()["ready"]
            else ()
        )
        config = CodexConfig(
            codex_bin=codex_bin,
            cwd=str(self.workspace),
            config_overrides=overrides,
        )
        with Codex(config) as codex:
            if bot.get("codex_thread_id"):
                try:
                    thread = codex.thread_resume(
                        bot["codex_thread_id"],
                        cwd=str(self.workspace),
                        model=self.model,
                        sandbox=Sandbox.workspace_write,
                        approval_mode=ApprovalMode.auto_review,
                        developer_instructions=instructions,
                    )
                except Exception:
                    LOGGER.warning("Could not resume thread for %s; starting a new one", bot["id"])
                    thread = self._start_thread(codex, bot, instructions)
            else:
                thread = self._start_thread(codex, bot, instructions)

            turn = thread.turn(
                prompt + context_block,
                model=self.model,
                sandbox=Sandbox.workspace_write,
                approval_mode=ApprovalMode.auto_review,
                output_schema=build_output_schema([item["id"] for item in teammates]),
            )
            with self._lock:
                active = self._active.get(bot["id"])
                if not active:
                    raise RunStopped()
                active.turn = turn
                cancel_requested = active.cancel_requested
            if cancel_requested:
                raise RunStopped()
            result = turn.run()
            raw = result.final_response or "{}"
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"message": raw, "handoffs": []}
            if not isinstance(payload, dict):
                payload = {"message": str(payload), "handoffs": []}
            return payload

    def _start_thread(self, codex: Codex, bot: dict, instructions: str):
        thread = codex.thread_start(
            cwd=str(self.workspace),
            model=self.model,
            sandbox=Sandbox.workspace_write,
            approval_mode=ApprovalMode.auto_review,
            developer_instructions=instructions,
            service_name="codex-bots",
        )
        thread.set_name(f"Codex Bots · {bot['name']}")
        self.store.set_thread_id(bot["id"], thread.id)
        return thread

    def _dispatch_handoffs(self, source_bot: dict, handoffs: list, depth: int) -> None:
        seen: set[str] = set()
        for handoff in handoffs[:2]:
            target_id = str(handoff.get("to_bot") or "")
            task = str(handoff.get("task") or "").strip()
            if not task or target_id == source_bot["id"] or target_id in seen:
                continue
            target = self.store.get_bot(target_id)
            if not target or self.is_active(target_id):
                continue
            seen.add(target_id)
            label = f"Handed to {target['name']}: {task}"
            self.store.add_message(
                bot_id=source_bot["id"],
                role="system",
                author_name=source_bot["name"],
                content=label,
                kind="handoff_out",
                metadata={"to_bot": target_id},
            )
            self.store.add_message(
                bot_id=target_id,
                role="system",
                author_name=source_bot["name"],
                content=task,
                kind="handoff_in",
                metadata={"from_bot": source_bot["id"]},
            )
            self.submit(
                target_id,
                f"Handoff from {source_bot['name']}:\n{task}\n\nComplete this focused assignment and report back.",
                origin_bot_id=source_bot["id"],
                depth=depth + 1,
                trigger="handoff",
            )

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        message = str(exc).strip()
        lower = message.lower()
        if "auth" in lower or "login" in lower or "unauthorized" in lower:
            return "Codex is not signed in. Open a terminal, run `codex login`, then try again."
        if "model" in lower and ("not found" in lower or "unsupported" in lower):
            return "The selected GPT-5.6 model is not available on this Codex account. Set `CODEX_BOT_MODEL` to an available Codex model and restart."
        return f"Codex could not complete this turn: {message or type(exc).__name__}"
