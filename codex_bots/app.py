"""Flask application factory and JSON API."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from .codex_service import CodexService
from .db import Store

ALLOWED_SHAPES = {"orb", "hex", "squircle", "diamond"}
ALLOWED_COLORS = {"#7957e8", "#0bbf9f", "#ff7a1a", "#e34b70", "#2687e9", "#a36d3f"}


def create_app(test_config: dict | None = None) -> Flask:
    root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        instance_path=str(root / "instance"),
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    app.config.from_mapping(
        DATABASE=str(root / "instance" / "codex_bots.sqlite3"),
        BOT_WORKSPACE=str(root / "workspace"),
        CODEX_MODEL=os.environ.get("CODEX_BOT_MODEL", "gpt-5.6-sol"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        SYNC_JOBS=False,
        TEST_RUNNER=None,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    store = Store(app.config["DATABASE"])
    service = CodexService(
        store,
        app.config["BOT_WORKSPACE"],
        model=app.config["CODEX_MODEL"],
        synchronous=app.config["SYNC_JOBS"],
        runner=app.config.get("TEST_RUNNER"),
    )
    app.extensions["bot_store"] = store
    app.extensions["codex_service"] = service

    @app.get("/")
    def index():
        return render_template("index.html", model=app.config["CODEX_MODEL"])

    @app.get("/api/state")
    def state():
        bots = store.list_bots()
        selected_id = request.args.get("bot_id") or (bots[0]["id"] if bots else None)
        selected = store.get_bot(selected_id) if selected_id else None
        return jsonify(
            {
                "bots": [_public_bot(bot) for bot in bots],
                "selected": _public_bot(selected) if selected else None,
                "messages": store.get_messages(selected_id) if selected_id else [],
                "runtime": _runtime_status(app.config["CODEX_MODEL"]),
            }
        )

    @app.post("/api/bots")
    def create_bot():
        body = request.get_json(silent=True) or {}
        name = _clean_text(body.get("name"), 40)
        title = _clean_text(body.get("title"), 70)
        description = _clean_text(body.get("description"), 800)
        if not name or not title or not description:
            return jsonify({"error": "Name, job, and description are required."}), 400
        bot = store.create_bot(
            name=name,
            title=title,
            description=description,
            color=body.get("color") if body.get("color") in ALLOWED_COLORS else "#2687e9",
            shape=body.get("shape") if body.get("shape") in ALLOWED_SHAPES else "diamond",
        )
        return jsonify(_public_bot(bot)), 201

    @app.post("/api/bots/<bot_id>/messages")
    def send_message(bot_id: str):
        bot = store.get_bot(bot_id)
        if not bot:
            return jsonify({"error": "Bot not found."}), 404
        body = request.get_json(silent=True) or {}
        content = _clean_text(body.get("content"), 12_000)
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        if not content and not attachments:
            return jsonify({"error": "Write a message or attach a file."}), 400
        if service.is_active(bot_id):
            return jsonify({"error": f"{bot['name']} is already working."}), 409

        safe_attachments = []
        for item in attachments[:6]:
            if not isinstance(item, dict):
                continue
            filename = secure_filename(str(item.get("name") or ""))
            saved_path = Path(str(item.get("path") or "")).resolve()
            workspace = Path(app.config["BOT_WORKSPACE"]).resolve()
            if filename and saved_path.is_relative_to(workspace):
                safe_attachments.append({"name": filename, "path": str(saved_path)})

        display_content = content or "Review the attached file."
        store.add_message(
            bot_id=bot_id,
            role="user",
            author_name="You",
            content=display_content,
            metadata={"attachments": safe_attachments},
        )
        attachment_context = ""
        if safe_attachments:
            attachment_context = "\n\nAttached local files:\n" + "\n".join(
                f"- {item['name']}: {item['path']}" for item in safe_attachments
            )
        try:
            service.submit(bot_id, display_content + attachment_context)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"ok": True}), 202

    @app.post("/api/bots/<bot_id>/stop")
    def stop_bot(bot_id: str):
        if not store.get_bot(bot_id):
            return jsonify({"error": "Bot not found."}), 404
        return jsonify({"ok": service.interrupt(bot_id)})

    @app.post("/api/uploads")
    def upload():
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "Choose a file to attach."}), 400
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({"error": "That filename is not supported."}), 400
        upload_dir = Path(app.config["BOT_WORKSPACE"]) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / filename
        stem, suffix, counter = target.stem, target.suffix, 2
        while target.exists():
            target = upload_dir / f"{stem}-{counter}{suffix}"
            counter += 1
        file.save(target)
        return jsonify({"name": target.name, "path": str(target.resolve())}), 201

    @app.get("/api/health")
    def health():
        return jsonify(_runtime_status(app.config["CODEX_MODEL"]))

    return app


def _public_bot(bot: dict | None) -> dict | None:
    if not bot:
        return None
    keys = {
        "id",
        "name",
        "title",
        "description",
        "color",
        "shape",
        "status",
        "status_text",
        "latest_message",
        "latest_at",
        "created_at",
        "updated_at",
    }
    return {key: value for key, value in bot.items() if key in keys}


def _clean_text(value, maximum: int) -> str:
    text = re.sub(r"\x00", "", str(value or "")).strip()
    return text[:maximum]


@lru_cache(maxsize=4)
def _runtime_status(model: str) -> dict:
    codex_bin = os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not codex_bin:
        return {"ready": False, "model": model, "version": None, "message": "Codex CLI not found"}
    try:
        process = subprocess.run(
            [codex_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        version = (process.stdout or process.stderr).strip().splitlines()[-1]
        return {"ready": process.returncode == 0, "model": model, "version": version, "message": "Ready"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ready": False, "model": model, "version": None, "message": str(exc)}
