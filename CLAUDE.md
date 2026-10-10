# Mac → iPhone notifications — working notes for Claude

## Status: shipped and running. Do not re-open it as unfinished work.

The bridge went live on 10 Sep 2026 and is still running — verified 11 Sep 2026:
`launchctl list | grep notify` shows `com.kenny.notify-bridge` with a live PID.
The LaunchAgent at `~/Library/LaunchAgents/com.kenny.notify-bridge.plist` is
bootstrapped under `/usr/bin/python3`, which already has Full Disk Access.

Two things follow, and they are decisions rather than gaps:

- **No deadline, and none is needed.** This is finished personal tooling, not a
  portfolio project in flight.
- **No Reminders list, on purpose.** Kenny retired this project's list in
  Sep 2026 because the work is done. The portfolio-wide rule is "every project
  with code gets a tracked list" — this project is an explicit exception.
  **Do not create one.** Raise anything worth doing in conversation instead.

## The one thing that will trip you up

**The live ntfy topic exists only in the installed plist**, never in the repo.
The committed `com.kenny.notify-bridge.plist` still carries the `CHANGE-ME`
placeholder — deliberately, so the topic is not published in git history. The
repo therefore cannot tell you what the phone subscribes to. Read it with:

```sh
grep -A4 ProgramArguments ~/Library/LaunchAgents/com.kenny.notify-bridge.plist
```

Never copy the real topic back into the repo copy.

## How it works, and why it looks awkward

macOS has no public API that hands you notifications as they arrive. What it
has is the SQLite database Notification Center writes every notification into:

```
~/Library/Group Containers/group.com.apple.usernoted/db2/db
```

So `notify-bridge.py` tails that database and posts each new row to ntfy.
Each record's payload is a binary plist inside a BLOB, which is why the script
parses rather than just reads.

Reading that file requires **Full Disk Access** for whatever runs the script —
already granted to `/usr/bin/python3` here. `--check` reports whether you have
it and which binary to grant it to; that is the first thing to run if delivery
silently stops.

## Already fixed — don't rediscover these

Commit `61f0467` fixed header encoding and made TLS failures report themselves
by name instead of dying quietly, and documented the silent-stall failure mode.
If notifications stop arriving, the order to check is: agent still loaded
(`launchctl list`) → Full Disk Access (`--check`) → `bridge.log` → the phone is
still subscribed to the topic in the plist.

## Layout

- `notify-bridge.py` — the whole bridge.
- `com.kenny.notify-bridge.plist` — the LaunchAgent template, with the topic
  placeholdered.
- `bridge.log` — runtime log, gitignored.
