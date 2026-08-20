"""Durable local routine scheduling and execution."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .codex_service import CodexService
from .db import Store

LOGGER = logging.getLogger(__name__)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SCHEDULE_KINDS = {"manual", "hourly", "daily", "weekdays", "weekly"}


def normalize_schedule(
    kind: str,
    time_local: str = "09:00",
    weekday: int = 0,
    timezone_name: str = "local",
) -> dict:
    kind = str(kind or "manual").lower()
    if kind not in SCHEDULE_KINDS:
        raise ValueError("Choose manual, hourly, daily, weekdays, or weekly.")
    if kind in {"daily", "weekdays", "weekly"} and not TIME_PATTERN.fullmatch(time_local):
        raise ValueError("Choose a valid local time in HH:MM format.")
    if kind == "weekly" and weekday not in range(7):
        raise ValueError("Choose a valid weekday.")
    timezone_name = str(timezone_name or "local")
    if timezone_name != "local":
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone.") from None
    return {
        "kind": kind,
        "time_local": time_local,
        "weekday": weekday,
        "timezone": timezone_name,
    }


def next_run_at(schedule: dict, now: datetime | None = None) -> str | None:
    kind = schedule.get("kind", "manual")
    if kind == "manual":
        return None
    timezone_name = schedule.get("timezone", "local")
    zone = datetime.now().astimezone().tzinfo if timezone_name == "local" else ZoneInfo(timezone_name)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=timezone.utc)
    if kind == "hourly":
        candidate = local_now + timedelta(hours=1)
    else:
        hour, minute = (int(part) for part in schedule.get("time_local", "09:00").split(":"))
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        if kind == "weekdays":
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        elif kind == "weekly":
            target = int(schedule.get("weekday", 0))
            while candidate.weekday() != target:
                candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class RoutineScheduler:
    def __init__(
        self,
        store: Store,
        service: CodexService,
        *,
        poll_seconds: float = 5,
        autostart: bool = True,
    ) -> None:
        self.store = store
        self.service = service
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if autostart:
            self.start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="codex-bots-routines",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(self.poll_seconds * 2, 1))

    def run_now(self, routine_id: str) -> str:
        routine = self.store.get_routine(routine_id)
        if not routine:
            raise LookupError("Routine not found.")
        return self._launch(routine)

    def tick(self) -> int:
        now = datetime.now(timezone.utc)
        lease = now + timedelta(minutes=2)
        routines = self.store.claim_due_routines(
            now.isoformat(timespec="milliseconds"),
            lease.isoformat(timespec="milliseconds"),
        )
        launched = 0
        for routine in routines:
            self.store.advance_routine(routine["id"], next_run_at(routine["schedule"]))
            try:
                self._launch(routine)
                launched += 1
            except Exception as exc:
                status = "skipped-busy" if "already working" in str(exc).lower() else "failed"
                self.store.set_routine_result(routine["id"], status)
                LOGGER.warning("Routine %s could not start: %s", routine["id"], exc)
        return launched

    def _launch(self, routine: dict) -> str:
        bot = self.store.get_bot(routine["bot_id"])
        if not bot:
            raise LookupError("The routine's Bot no longer exists.")
        if self.service.is_active(bot["id"]):
            raise RuntimeError(f"{bot['name']} is already working.")
        self.store.add_message(
            bot_id=bot["id"],
            role="user",
            author_name=f"Routine · {routine['name']}",
            content=routine["prompt"],
            kind="routine_run",
            metadata={"routine_id": routine["id"]},
        )
        return self.service.submit(
            bot["id"],
            routine["prompt"],
            trigger="routine",
            routine_id=routine["id"],
        )

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.tick()
            except Exception:
                LOGGER.exception("Routine scheduler tick failed")
