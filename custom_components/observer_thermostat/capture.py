"""Traffic capture for the Observer Thermostat API server.

Home Assistant independent: a bounded in-memory ring (surfaced via diagnostics)
plus an optional size-rotated JSONL file written from a background thread.
"""

from __future__ import annotations

import datetime
import json
import logging
import logging.handlers
import os
import queue
import re
import time
from collections import deque
from itertools import count
from typing import Any

from .const import (
    CAPTURE_FILE_BACKUPS,
    CAPTURE_FILE_MAX_BYTES,
    CAPTURE_HEARTBEAT_SECONDS,
    CAPTURE_NOISY_ENDPOINTS,
    CAPTURE_RING_SIZE,
)

_SAFE_HEADERS = ("content-type", "content-length", "user-agent", "host")
_ids = count()
# Installer/owner details that appear in thermostat payloads
_SENSITIVE_TAGS = re.compile(
    r"<(pin|phone|email|street1|street2|scrLockoutCode)>[^<]*</\1>"
)


class CaptureLog:
    """Record request/response pairs with the serial number redacted."""

    def __init__(self, serial: str, maxlen: int = CAPTURE_RING_SIZE) -> None:
        self._serial = serial
        self._ring: deque[dict[str, Any]] = deque(maxlen=maxlen)
        # last recorded entry (and its monotonic time) per noisy (method, endpoint)
        self._last_noisy: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
        self._logger: logging.Logger | None = None
        self._queue_handler: logging.handlers.QueueHandler | None = None
        self._listener: logging.handlers.QueueListener | None = None
        self._file_handler: logging.Handler | None = None

    def redact(self, text: str) -> str:
        if not text:
            return text
        if self._serial:
            text = text.replace(self._serial, "<SERIAL>")
        return _SENSITIVE_TAGS.sub(r"<\1>REDACTED</\1>", text)

    def add(
        self,
        *,
        method: str,
        path: str,
        query: str,
        remote: str | None,
        headers: dict[str, str],
        req_body: str,
        status: int,
        resp_body: str,
    ) -> None:
        endpoint = path.rstrip("/").split("/")[-1].lower()
        if endpoint == "dealer":  # installer name/phone/address: never record
            req_body = "REDACTED (dealer contact details)"
        noisy_key = (method, endpoint)
        now = time.monotonic()
        if endpoint in CAPTURE_NOISY_ENDPOINTS:
            previous = self._last_noisy.get(noisy_key)
            if (
                previous is not None
                and previous[0]["req_body"] == self.redact(req_body)
                and previous[0]["status"] == status
                and now - previous[1] < CAPTURE_HEARTBEAT_SECONDS
            ):
                previous[0]["repeats"] = previous[0].get("repeats", 0) + 1
                previous[0]["last_repeat"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
                return

        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "method": method,
            "path": self.redact(path),
            "query": self.redact(query),
            "remote": remote,
            "headers": {
                k: self.redact(v) for k, v in headers.items() if k.lower() in _SAFE_HEADERS
            },
            "req_body": self.redact(req_body),
            "status": status,
            "resp_body": self.redact(resp_body),
        }
        self._ring.append(entry)
        if endpoint in CAPTURE_NOISY_ENDPOINTS:
            self._last_noisy[noisy_key] = (entry, now)
        if self._logger is not None:
            self._logger.info(json.dumps(entry, separators=(",", ":")))

    def entries(self, limit: int | None = None) -> list[dict[str, Any]]:
        items = list(self._ring)
        return items[-limit:] if limit else items

    # ── File output (blocking calls: run these in an executor) ─────

    def start_file(self, directory: str) -> None:
        """Start writing captures to a rotating JSONL file."""
        if self._logger is not None:
            return
        os.makedirs(directory, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(directory, "capture.jsonl"),
            maxBytes=CAPTURE_FILE_MAX_BYTES,
            backupCount=CAPTURE_FILE_BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        q: queue.SimpleQueue = queue.SimpleQueue()
        logger = logging.getLogger(f"observer_thermostat.capture.{next(_ids)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        self._queue_handler = logging.handlers.QueueHandler(q)
        logger.addHandler(self._queue_handler)
        self._listener = logging.handlers.QueueListener(q, handler)
        self._listener.start()
        self._file_handler = handler
        self._logger = logger

    def stop_file(self) -> None:
        if self._logger is None:
            return
        if self._queue_handler is not None:
            self._logger.removeHandler(self._queue_handler)
        if self._listener is not None:
            self._listener.stop()
        if self._file_handler is not None:
            self._file_handler.close()
        self._logger = self._queue_handler = self._listener = self._file_handler = None
