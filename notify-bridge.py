#!/usr/bin/env python3
"""Forward macOS notifications to an iPhone on the same network.

macOS has no public API that hands you notifications as they arrive.
What it does have is a SQLite database the Notification Center writes
every notification into, so this tails that database and pushes each new
row to the phone.

    ~/Library/Group Containers/group.com.apple.usernoted/db2/db

Reading it needs Full Disk Access for whatever runs this -- Terminal, or
the launchd agent's python. That is a real macOS permission prompt and
the one manual step here; `--check` tells you whether you have it.

Delivery is over the local network via ntfy (https://ntfy.sh): the phone
subscribes to a topic in the free ntfy app and this posts to it. Two
modes:

    --server https://ntfy.sh        the public relay (default)
    --server http://<mac-ip>:2586   a `ntfy serve` on this Mac, so
                                    nothing leaves the LAN

The public relay is the zero-setup path, but topics on it are readable
by anyone who guesses the name, and your notification text is somebody
else's server's problem. Self-hosting is one `brew install ntfy` away
and is what "same network" really means; --server switches between them
with nothing else changing.

Usage:
    ./notify-bridge.py --check
    ./notify-bridge.py --topic my-secret-topic-name
    ./notify-bridge.py --topic my-topic --server http://192.168.1.5:2586
    ./notify-bridge.py --topic my-topic --only "Messages,Calendar"
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DB = Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"

# Apple stores the delivery time as seconds since 2001-01-01 UTC, not
# since the Unix epoch. Adding this converts one to the other.
APPLE_EPOCH_OFFSET = 978_307_200


def check() -> int:
    """Say whether this process can actually read the database."""
    if not DB.exists():
        print(f"✗ no notification database at {DB}")
        print("  This path is macOS 10.14+. On an older system it is elsewhere.")
        return 1

    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        con.execute("SELECT count(*) FROM record").fetchone()
        con.close()
    except sqlite3.OperationalError as err:
        print(f"✗ cannot read it: {err}")
        print()
        print("  This is Full Disk Access, not file permissions.")
        print("  System Settings → Privacy & Security → Full Disk Access,")
        print("  and add the program running this (Terminal, iTerm, or the")
        print("  python3 binary if you are running it under launchd).")
        print()
        print(f"  The binary is: {sys.executable}")
        return 1

    print("✓ notification database is readable")
    return 0


def connect() -> sqlite3.Connection:
    # Read-only, so a bug here can never corrupt the notification
    # database -- it is live, and Notification Center is writing to it.
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def decode(row: sqlite3.Row) -> dict | None:
    """Pull title, subtitle and body out of one record.

    The payload is a binary plist in a BLOB. Its shape varies by the app
    that posted it, and several keys are optional, so everything here is
    a lookup with a default rather than an index.
    """
    try:
        payload = plistlib.loads(row["data"])
    except Exception:
        return None

    req = payload.get("req", {})
    title = req.get("titl") or ""
    subtitle = req.get("subt") or ""
    body = req.get("body") or ""

    if not (title or subtitle or body):
        return None

    return {
        "app": row["identifier"] or "",
        "title": title,
        "subtitle": subtitle,
        "body": body,
        "at": (row["delivered_date"] or 0) + APPLE_EPOCH_OFFSET,
    }


def latest_rowid(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT COALESCE(max(rec_id), 0) AS m FROM record").fetchone()
    return int(row["m"])


def fetch_since(con: sqlite3.Connection, after: int) -> list[dict]:
    """Every decodable notification with a rowid above `after`.

    Keyed on rec_id rather than on the timestamp: the clock can move,
    two notifications can share a second, and the id only ever grows.
    """
    rows = con.execute(
        """
        SELECT r.rec_id, r.delivered_date, r.data, a.identifier
        FROM record r
        LEFT JOIN app a ON a.app_id = r.app_id
        WHERE r.rec_id > ?
        ORDER BY r.rec_id
        """,
        (after,),
    ).fetchall()

    out = []
    for row in rows:
        item = decode(row)
        if item:
            item["rec_id"] = row["rec_id"]
            out.append(item)
    return out


def push(server: str, topic: str, item: dict, timeout: float) -> bool:
    """Send one notification. Returns whether it landed."""
    # ntfy takes the body as the request body and the rest as headers.
    # Headers must be latin-1 encodable, and a notification title is
    # routinely not -- an em dash or a Chinese app name is enough -- so
    # the title goes out RFC 2047 encoded, which ntfy decodes.
    title = " · ".join(p for p in (item["app"], item["title"]) if p)
    text = "\n".join(p for p in (item["subtitle"], item["body"]) if p) or title

    request = urllib.request.Request(
        f"{server.rstrip('/')}/{topic}",
        data=text.encode("utf-8"),
        headers={
            "Title": encode_header(title),
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        print(f"  ! delivery failed: {err}", file=sys.stderr)
        return False


def encode_header(value: str) -> str:
    """RFC 2047 for anything a latin-1 header cannot carry."""
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        from base64 import b64encode

        return "=?UTF-8?B?" + b64encode(value.encode("utf-8")).decode("ascii") + "?="


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report whether the database is readable, then exit")
    parser.add_argument("--topic", default=os.environ.get("NOTIFY_TOPIC", ""),
                        help="ntfy topic; treat it as a password on the public server")
    parser.add_argument("--server", default=os.environ.get("NOTIFY_SERVER", "https://ntfy.sh"),
                        help="ntfy server (default https://ntfy.sh)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="seconds between polls (default 2)")
    parser.add_argument("--only", default="",
                        help="comma-separated bundle-id substrings to forward; "
                             "everything else is ignored")
    parser.add_argument("--exclude", default="",
                        help="comma-separated bundle-id substrings to drop")
    parser.add_argument("--backfill", action="store_true",
                        help="also send what is already in the database "
                             "(default: only what arrives from now on)")
    parser.add_argument("--dry-run", action="store_true",
                        help="decode and print, send nothing -- use this to see "
                             "what would be forwarded before any of it leaves the Mac")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if args.check:
        return check()

    if not args.topic and not args.dry_run:
        print("--topic is required. On the public server anyone who knows the")
        print("topic name reads your notifications, so pick something nobody")
        print("will guess:")
        print()
        print("  ./notify-bridge.py --topic \"$(openssl rand -hex 12)\"")
        return 2

    if check() != 0:
        return 1

    only = [s.strip().lower() for s in args.only.split(",") if s.strip()]
    exclude = [s.strip().lower() for s in args.exclude.split(",") if s.strip()]

    con = connect()

    # Start from the end unless asked otherwise, so starting the bridge
    # does not replay every notification the Mac has ever shown.
    last = 0 if args.backfill else latest_rowid(con)

    if args.dry_run:
        print("→ dry run: decoding only, nothing is sent")
    else:
        print(f"→ {args.server.rstrip('/')}/{args.topic}")
    if not args.dry_run:
        print(f"  subscribe to '{args.topic}' in the ntfy app on the iPhone")
    if only:
        print(f"  forwarding only: {', '.join(only)}")
    if exclude:
        print(f"  dropping: {', '.join(exclude)}")
    print("  ctrl-c to stop", flush=True)

    while True:
        try:
            items = fetch_since(con, last)
        except sqlite3.OperationalError as err:
            # The database is live; a writer can lock it briefly. Not
            # fatal, and not worth a stack trace.
            print(f"  ! read failed, retrying: {err}", file=sys.stderr)
            time.sleep(args.interval)
            continue

        for item in items:
            last = max(last, item["rec_id"])
            app = item["app"].lower()

            if only and not any(s in app for s in only):
                continue
            if exclude and any(s in app for s in exclude):
                continue

            label = item["title"] or item["body"][:40]
            print(f"  {item['app']}: {label}", flush=True)

            if args.dry_run:
                continue

            push(args.server, args.topic, item, args.timeout)

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(0)
