#!/usr/bin/env python3
"""Local-only control dashboard for the Instagram cleanup script."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from instagram_unfollow import (
    DEFAULT_HISTORY_FILE,
    PROJECT_DIR,
    extract_usernames,
    load_targets,
    normalize_username,
)


DEFAULT_TARGETS_FILE = PROJECT_DIR / "people.json"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
COMPLETED_STATUSES = {"success", "not_following"}

app = Flask(__name__, template_folder="dashboard", static_folder="dashboard/static")


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and normalize_username(str(entry.get("username", ""))):
                entries.append(entry)
    return entries


def target_snapshot(target_file: Path, history_file: Path) -> dict[str, Any]:
    try:
        usernames = load_targets(target_file)
        target_error = None
    except ValueError as exc:
        usernames = []
        target_error = str(exc)

    latest: dict[str, dict[str, Any]] = {}
    for entry in read_history(history_file):
        username = normalize_username(str(entry.get("username", "")))
        if username:
            latest[username] = entry

    target_set = set(usernames)
    completed = {
        username
        for username, entry in latest.items()
        if username in target_set and entry.get("status") in COMPLETED_STATUSES
    }
    successes = {
        username
        for username, entry in latest.items()
        if username in target_set and entry.get("status") == "success"
    }
    skipped = {
        username
        for username, entry in latest.items()
        if username in target_set and entry.get("status") == "not_following"
    }
    error_statuses = {"failed", "stopped", "user_not_found", "not_changed"}
    errors = {
        username
        for username, entry in latest.items()
        if username in target_set and entry.get("status") in error_statuses
    }

    accounts = []
    for username in usernames:
        entry = latest.get(username, {})
        status = str(entry.get("status", "pending"))
        if username not in completed and status not in error_statuses:
            status = "pending"
        accounts.append(
            {
                "username": username,
                "status": status,
                "timestamp": entry.get("timestamp"),
                "detail": entry.get("detail", ""),
            }
        )

    return {
        "total": len(usernames),
        "completed": len(completed),
        "remaining": max(0, len(usernames) - len(completed)),
        "unfollowed": len(successes),
        "skipped": len(skipped),
        "errors": len(errors),
        "accounts": accounts,
        "target_error": target_error,
    }


class CleanupRunner:
    def __init__(self, target_file: Path, history_file: Path) -> None:
        self.target_file = target_file.resolve()
        self.history_file = history_file.resolve()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.pause_after_batch = threading.Event()
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.running = False
        self.mode = "idle"
        self.phase = "Ready"
        self.started_at: str | None = None
        self.batch_number = 0
        self.cooldown_until: float | None = None
        self.logs: list[str] = []
        self.last_exit_code: int | None = None

    def log(self, message: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        with self.lock:
            self.logs.append(f"[{timestamp}] {message.rstrip()}")
            self.logs = self.logs[-300:]

    def public_state(self) -> dict[str, Any]:
        with self.lock:
            cooldown_seconds = 0
            if self.cooldown_until:
                cooldown_seconds = max(0, round(self.cooldown_until - time.time()))
            return {
                "running": self.running,
                "mode": self.mode,
                "phase": self.phase,
                "started_at": self.started_at,
                "batch_number": self.batch_number,
                "cooldown_seconds": cooldown_seconds,
                "pause_requested": self.pause_after_batch.is_set(),
                "logs": list(self.logs),
                "last_exit_code": self.last_exit_code,
                "target_file": self.target_file.name,
            }

    def start(self, config: dict[str, Any]) -> tuple[bool, str]:
        with self.lock:
            if self.running:
                return False, "A cleanup run is already active."
            snapshot = target_snapshot(self.target_file, self.history_file)
            if snapshot["target_error"]:
                return False, snapshot["target_error"]
            if snapshot["remaining"] == 0:
                return False, "There are no remaining accounts."

            self.stop_event.clear()
            self.pause_after_batch.clear()
            self.running = True
            self.mode = config["mode"]
            self.phase = "Starting"
            self.started_at = datetime.now(timezone.utc).isoformat()
            self.batch_number = 0
            self.cooldown_until = None
            self.last_exit_code = None
            self.logs = []
            self.thread = threading.Thread(
                target=self._worker,
                args=(config,),
                daemon=True,
                name="instagram-cleanup-runner",
            )
            self.thread.start()
            return True, "Cleanup started."

    def request_pause(self) -> tuple[bool, str]:
        with self.lock:
            if not self.running:
                return False, "No cleanup run is active."
            self.pause_after_batch.set()
            self.log("Pause requested; the current batch will finish first.")
            return True, "The run will pause after the current batch."

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            if not self.running:
                return False, "No cleanup run is active."
            self.stop_event.set()
            process = self.process
            self.phase = "Stopping"
            self.log("Immediate stop requested.")
        if process and process.poll() is None:
            process.terminate()
        return True, "Stop requested."

    def _run_batch(self, config: dict[str, Any]) -> int:
        command = [
            sys.executable,
            "-u",
            str(PROJECT_DIR / "instagram_unfollow.py"),
            str(self.target_file),
            "--execute",
            "--yes",
            "--limit",
            str(config["batch_size"]),
            "--delay-min",
            str(config["delay_min"]),
            "--delay-max",
            str(config["delay_max"]),
            "--history-file",
            str(self.history_file),
        ]
        process_env = os.environ.copy()
        process_env["PYTHONUNBUFFERED"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=process_env,
            creationflags=creationflags,
        )
        with self.lock:
            self.process = process

        assert process.stdout is not None
        for line in process.stdout:
            self.log(line)
        return_code = process.wait()
        with self.lock:
            self.process = None
        return return_code

    def _worker(self, config: dict[str, Any]) -> None:
        self.log(
            f"Queue started: mode={config['mode']}, batch={config['batch_size']}, "
            f"delay={config['delay_min']}-{config['delay_max']}s."
        )
        try:
            while not self.stop_event.is_set():
                before = target_snapshot(self.target_file, self.history_file)["remaining"]
                if before == 0:
                    with self.lock:
                        self.phase = "Complete"
                    self.log("Every target has been completed.")
                    break

                with self.lock:
                    self.batch_number += 1
                    self.phase = f"Running batch {self.batch_number}"
                self.log(f"Starting batch {self.batch_number} with {before} remaining.")
                exit_code = self._run_batch(config)
                with self.lock:
                    self.last_exit_code = exit_code

                if self.stop_event.is_set():
                    self.log("Run stopped. Saved history will be used on resume.")
                    break
                if exit_code != 0:
                    with self.lock:
                        self.phase = "Stopped on Instagram warning or error"
                    self.log(
                        "The batch returned an error. Continuous mode stopped automatically."
                    )
                    break

                after = target_snapshot(self.target_file, self.history_file)["remaining"]
                progressed = before - after
                self.log(f"Batch {self.batch_number} completed; {progressed} target(s) cleared.")
                if after == 0:
                    with self.lock:
                        self.phase = "Complete"
                    self.log("Every target has been completed.")
                    break
                if progressed <= 0:
                    with self.lock:
                        self.phase = "Stopped: no progress"
                    self.log("No progress was recorded, so the queue stopped safely.")
                    break
                if config["mode"] == "batch" or self.pause_after_batch.is_set():
                    with self.lock:
                        self.phase = "Paused" if self.pause_after_batch.is_set() else "Batch complete"
                    self.log("Queue paused. Start it again whenever you are ready.")
                    break

                cooldown_seconds = config["cooldown_minutes"] * 60
                with self.lock:
                    self.phase = "Cooling down"
                    self.cooldown_until = time.time() + cooldown_seconds
                self.log(f"Cooling down for {config['cooldown_minutes']} minute(s).")
                if self.stop_event.wait(cooldown_seconds):
                    self.log("Run stopped during cooldown.")
                    break
                with self.lock:
                    self.cooldown_until = None
                if self.pause_after_batch.is_set():
                    with self.lock:
                        self.phase = "Paused"
                    self.log("Queue paused before the next batch.")
                    break
        except Exception as exc:  # Keep the local server alive and expose a safe diagnostic.
            with self.lock:
                self.phase = "Dashboard error"
                self.last_exit_code = 1
            self.log(f"Dashboard runner error: {type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.running = False
                self.process = None
                self.cooldown_until = None


runner = CleanupRunner(DEFAULT_TARGETS_FILE, DEFAULT_HISTORY_FILE)


def parse_run_config(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode", "batch"))
    if mode not in {"batch", "all"}:
        raise ValueError("Mode must be batch or all.")

    batch_size = int(payload.get("batch_size", 10))
    delay_min = float(payload.get("delay_min", 30))
    delay_max = float(payload.get("delay_max", 60))
    cooldown_minutes = int(payload.get("cooldown_minutes", 30))
    if not 1 <= batch_size <= 25:
        raise ValueError("Batch size must be between 1 and 25.")
    if not 10 <= delay_min <= 600 or not 10 <= delay_max <= 600:
        raise ValueError("Delays must be between 10 and 600 seconds.")
    if delay_max < delay_min:
        raise ValueError("Maximum delay must be at least the minimum delay.")
    if not 5 <= cooldown_minutes <= 720:
        raise ValueError("Cooldown must be between 5 and 720 minutes.")
    return {
        "mode": mode,
        "batch_size": batch_size,
        "delay_min": delay_min,
        "delay_max": delay_max,
        "cooldown_minutes": cooldown_minutes,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    snapshot = target_snapshot(runner.target_file, runner.history_file)
    snapshot.pop("accounts")
    return jsonify({**snapshot, **runner.public_state()})


@app.get("/api/accounts")
def api_accounts():
    snapshot = target_snapshot(runner.target_file, runner.history_file)
    return jsonify({"accounts": snapshot["accounts"]})


@app.post("/api/run")
def api_run():
    try:
        config = parse_run_config(request.get_json(silent=True) or {})
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    ok, message = runner.start(config)
    return jsonify({"ok": ok, "message": message}), 200 if ok else 409


@app.post("/api/pause")
def api_pause():
    ok, message = runner.request_pause()
    return jsonify({"ok": ok, "message": message}), 200 if ok else 409


@app.post("/api/stop")
def api_stop():
    ok, message = runner.stop()
    return jsonify({"ok": ok, "message": message}), 200 if ok else 409


@app.post("/api/targets")
def api_targets():
    if runner.public_state()["running"]:
        return jsonify({"ok": False, "message": "Stop the active run before changing targets."}), 409
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "message": "Choose a JSON file first."}), 400
    raw = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "message": "The JSON file is too large."}), 400
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
        usernames = extract_usernames(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return jsonify({"ok": False, "message": f"Invalid JSON: {exc}"}), 400
    if not usernames:
        return jsonify({"ok": False, "message": "No valid Instagram usernames were found."}), 400

    destination = DEFAULT_TARGETS_FILE
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"count": len(usernames), "accounts": usernames}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    runner.target_file = destination.resolve()
    runner.log(f"Loaded {len(usernames)} unique targets from an uploaded JSON file.")
    return jsonify({"ok": True, "message": f"Loaded {len(usernames)} unique targets."})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Instagram cleanup dashboard.")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS_FILE)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_FILE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    runner.target_file = args.targets.resolve()
    runner.history_file = args.history.resolve()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
