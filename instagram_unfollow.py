#!/usr/bin/env python3
"""Safely unfollow Instagram accounts listed in a JSON file.

Preview mode is the default. Pass --execute to perform account changes.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SESSION_FILE = PROJECT_DIR / ".instagram_session.json"
DEFAULT_HISTORY_FILE = PROJECT_DIR / ".unfollow_history.jsonl"
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_username(raw_value: str) -> str | None:
    """Turn a username, @handle, or Instagram profile URL into a username."""
    value = raw_value.strip()
    if not value:
        return None

    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        if parsed.netloc.lower() not in {
            "instagram.com",
            "www.instagram.com",
            "m.instagram.com",
        }:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return None
        value = parts[0]

    value = value.removeprefix("@").strip().rstrip("/")
    if not USERNAME_RE.fullmatch(value):
        return None
    return value.lower()


def extract_usernames(payload: Any) -> list[str]:
    """Extract usernames from simple lists and common Instagram export shapes."""
    found: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        username = normalize_username(value)
        if username and username not in seen:
            seen.add(username)
            found.append(username)

    def walk(value: Any, allow_string: bool = False) -> None:
        if isinstance(value, str):
            if allow_string:
                add(value)
            return

        if isinstance(value, list):
            for item in value:
                walk(item, allow_string=True)
            return

        if not isinstance(value, dict):
            return

        for key in ("username", "user_name", "handle"):
            if key in value:
                add(value[key])
                return

        # Instagram's data export commonly puts the username in either title
        # or string_list_data[0].value.
        if "string_list_data" in value and isinstance(value["string_list_data"], list):
            for item in value["string_list_data"]:
                if isinstance(item, dict):
                    add(item.get("value"))
            return

        if "title" in value:
            add(value["title"])

        for key in (
            "usernames",
            "people",
            "users",
            "accounts",
            "following",
            "relationships_following",
        ):
            if key in value:
                walk(value[key], allow_string=True)

    walk(payload, allow_string=isinstance(payload, list))
    return found


def load_targets(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            payload = json.load(file)
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    usernames = extract_usernames(payload)
    if not usernames:
        raise ValueError(
            "No valid Instagram usernames were found. See people.example.json for supported formats."
        )
    return usernames


def load_completed(path: Path) -> set[str]:
    completed: set[str] = set()
    if not path.exists():
        return completed

    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    print(
                        f"Warning: ignored malformed history line {line_number} in {path}.",
                        file=sys.stderr,
                    )
                    continue
                if entry.get("status") in {"success", "not_following"}:
                    username = normalize_username(str(entry.get("username", "")))
                    if username:
                        completed.add(username)
    except OSError as exc:
        raise ValueError(f"Could not read history file {path}: {exc}") from exc
    return completed


def append_history(path: Path, username: str, status: str, detail: str = "") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "username": username,
        "status": status,
    }
    if detail:
        entry["detail"] = detail

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        file.flush()


def protect_secret_file(path: Path) -> None:
    """Restrict a session file on platforms that support POSIX-style modes."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


def preview(usernames: Iterable[str], total: int, skipped: int, limit: int) -> None:
    targets = list(usernames)
    print(f"Loaded {total} unique username(s).")
    if skipped:
        print(f"Skipping {skipped} username(s) already completed in history.")
    print(f"This run would process {len(targets)} username(s) (limit: {limit or 'unlimited'}).")
    for username in targets[:50]:
        print(f"  - @{username}")
    if len(targets) > 50:
        print(f"  ... and {len(targets) - 50} more")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or unfollow Instagram usernames from a JSON file."
    )
    parser.add_argument("json_file", type=Path, help="JSON file containing target usernames")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform unfollows (without this flag, only preview the targets)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the final interactive confirmation (requires --execute)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum targets this run; 0 means all remaining targets (default: 10)",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=30.0,
        help="Minimum seconds between unfollows (default: 30)",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=60.0,
        help="Maximum seconds between unfollows (default: 60)",
    )
    parser.add_argument(
        "--retry-completed",
        action="store_true",
        help="Do not skip usernames already marked successful in the history file",
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        default=DEFAULT_SESSION_FILE,
        help=f"Private saved-session path (default: {DEFAULT_SESSION_FILE.name})",
    )
    parser.add_argument(
        "--history-file",
        type=Path,
        default=DEFAULT_HISTORY_FILE,
        help=f"JSON Lines progress path (default: {DEFAULT_HISTORY_FILE.name})",
    )
    return parser


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.limit < 0:
        parser.error("--limit must be 0 or greater")
    if args.delay_min < 0 or args.delay_max < 0:
        parser.error("delays cannot be negative")
    if args.delay_max < args.delay_min:
        parser.error("--delay-max must be greater than or equal to --delay-min")
    if args.yes and not args.execute:
        parser.error("--yes can only be used with --execute")


def login(username: str, password: str, session_file: Path):
    try:
        from instagrapi import Client
        from instagrapi.exceptions import TwoFactorRequired
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies. Run: python -m pip install -r requirements.txt"
        ) from exc

    client = Client()
    client.delay_range = [1, 3]

    if session_file.exists():
        try:
            client.load_settings(session_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Could not load {session_file}. Move that private session file aside and try again: {exc}"
            ) from exc

    try:
        client.login(username, password)
    except TwoFactorRequired:
        code = os.getenv("INSTAGRAM_VERIFICATION_CODE", "").strip()
        if not code:
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "Instagram requested 2FA. Set INSTAGRAM_VERIFICATION_CODE for this run."
                )
            code = getpass.getpass("Instagram 2FA code: ").strip()
        if not code:
            raise RuntimeError("A 2FA code is required.")
        client.login(username, password, verification_code=code)

    expected_username = normalize_username(username)
    actual_username = normalize_username(str(client.account_info().username))
    if actual_username != expected_username:
        raise RuntimeError(
            f"The saved session belongs to @{actual_username}, not @{expected_username}. "
            "Use a different --session-file or move the existing session file aside."
        )

    session_file.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(session_file)
    protect_secret_file(session_file)
    return client


def run_unfollow(args: argparse.Namespace, usernames: list[str]) -> int:
    try:
        from dotenv import load_dotenv
        from instagrapi.exceptions import (
            ChallengeRequired,
            ClientError,
            ClientThrottledError,
            FeedbackRequired,
            PleaseWaitFewMinutes,
            RateLimitError,
            UserNotFound,
        )
    except ImportError:
        print(
            "Missing dependencies. Run: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    load_dotenv(PROJECT_DIR / ".env")
    account_username = os.getenv("INSTAGRAM_USERNAME", "").strip()
    password = os.getenv("INSTAGRAM_PASSWORD", "")
    if not account_username or not password:
        print(
            "Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in .env before using --execute.",
            file=sys.stderr,
        )
        return 2

    own_username = normalize_username(account_username)
    if not own_username:
        print("INSTAGRAM_USERNAME is not a valid username.", file=sys.stderr)
        return 2

    if own_username in usernames:
        usernames = [name for name in usernames if name != own_username]
        print(f"Safety check: removed your own username (@{own_username}) from the targets.")

    if not usernames:
        print("There are no remaining usernames to process.")
        return 0

    if not args.yes:
        print(f"\nThis will unfollow {len(usernames)} account(s) from @{own_username}.")
        confirmation = input('Type "UNFOLLOW" to continue: ').strip()
        if confirmation != "UNFOLLOW":
            print("Cancelled; no account changes were made.")
            return 0

    print(f"Logging in as @{own_username} using a reusable private session...")
    try:
        client = login(account_username, password, args.session_file)
    except Exception as exc:
        print(f"Login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "If Instagram requested a challenge, complete it in the official app/site before retrying.",
            file=sys.stderr,
        )
        return 1

    print("Login succeeded. Starting unfollows.")
    succeeded = 0
    unchanged = 0
    failed = 0

    stop_errors = (
        ChallengeRequired,
        ClientThrottledError,
        FeedbackRequired,
        PleaseWaitFewMinutes,
        RateLimitError,
    )

    print("Loading your current following list to resolve account IDs safely...")
    following_by_username: dict[str, str | int] = {}
    scanned = 0
    target_set = set(usernames)
    try:
        for followed_user in client.iter_user_following_v1(client.user_id, amount=0):
            scanned += 1
            followed_username = normalize_username(str(followed_user.username))
            if followed_username in target_set:
                following_by_username[followed_username] = followed_user.pk
    except stop_errors as exc:
        print(
            f"Instagram stopped the following-list request ({type(exc).__name__}). "
            "No unfollows were attempted.",
            file=sys.stderr,
        )
        return 1
    except ClientError as exc:
        print(
            f"Could not load the following list ({type(exc).__name__}). "
            "No unfollows were attempted.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Scanned {scanned} followed account(s); "
        f"{len(following_by_username)} of this batch are currently followed."
    )

    try:
        for index, username in enumerate(usernames, start=1):
            print(f"[{index}/{len(usernames)}] Unfollowing @{username}...", end=" ", flush=True)
            user_id = following_by_username.get(username)
            if user_id is None:
                unchanged += 1
                append_history(
                    args.history_file,
                    username,
                    "not_following",
                    "The account was not present in the current following list.",
                )
                print("not currently followed")
                continue
            try:
                changed = client.user_unfollow(user_id)
                if changed:
                    succeeded += 1
                    append_history(args.history_file, username, "success")
                    print("done")
                else:
                    unchanged += 1
                    append_history(
                        args.history_file,
                        username,
                        "not_changed",
                        "Instagram did not report a new unfollow action.",
                    )
                    print("not changed")
            except UserNotFound as exc:
                failed += 1
                append_history(args.history_file, username, "user_not_found", str(exc))
                print("user not found")
            except stop_errors as exc:
                failed += 1
                append_history(args.history_file, username, "stopped", type(exc).__name__)
                print(f"stopped ({type(exc).__name__})")
                print(
                    "Instagram requested a cooldown or verification. Stopping immediately; do not loop-retry.",
                    file=sys.stderr,
                )
                break
            except ClientError as exc:
                failed += 1
                append_history(args.history_file, username, "failed", type(exc).__name__)
                print(f"failed ({type(exc).__name__})")
                print("Stopping after an Instagram API error to avoid repeated actions.", file=sys.stderr)
                break

            if index < len(usernames):
                delay = random.uniform(args.delay_min, args.delay_max)
                print(f"Waiting {delay:.1f} seconds...")
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\nStopped by you. Completed entries are saved and will be skipped next time.")

    print(
        f"Finished: {succeeded} unfollowed, {unchanged} unchanged, {failed} failed/stopped. "
        f"History: {args.history_file}"
    )
    return 0 if failed == 0 else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(args, parser)

    try:
        all_usernames = load_targets(args.json_file)
        completed = set() if args.retry_completed else load_completed(args.history_file)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    remaining = [username for username in all_usernames if username not in completed]
    skipped = len(all_usernames) - len(remaining)
    targets = remaining if args.limit == 0 else remaining[: args.limit]

    preview(targets, len(all_usernames), skipped, args.limit)
    if not args.execute:
        print("\nPreview only; no login or account changes were made.")
        print(f"To run this batch: python {Path(__file__).name} {args.json_file} --execute")
        return 0

    return run_unfollow(args, targets)


if __name__ == "__main__":
    raise SystemExit(main())
