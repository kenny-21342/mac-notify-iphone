# Mac notifications on your iPhone

Forwards macOS notifications to an iPhone on the same network.

## How it works, and the one thing that makes it awkward

macOS has no public API that hands you notifications as they arrive.
What it has is a SQLite database that Notification Center writes every
notification into:

    ~/Library/Group Containers/group.com.apple.usernoted/db2/db

So the bridge tails that database and posts each new row to your phone.
Each record's payload is a binary plist inside a BLOB, which is why the
script parses rather than just reads.

Reading that file needs **Full Disk Access** for whatever runs the
script. That is the one manual step, and `--check` tells you whether you
have it and which binary to grant it to.

Delivery uses [ntfy](https://ntfy.sh): a free iPhone app that subscribes
to a topic, and an HTTP POST from here to publish to it.

## Quick start

```bash
./notify-bridge.py --check                       # is the database readable?
./notify-bridge.py --dry-run                     # see what would be sent, send nothing
./notify-bridge.py --topic "$(openssl rand -hex 12)"
```

Install **ntfy** from the App Store, subscribe to the same topic, done.

If deliveries fail while `curl https://ntfy.sh` works, the shebang has
picked a python.org interpreter with no CA bundle. Run it as
`/usr/bin/python3 notify-bridge.py …`, which is what the LaunchAgent does.

## Keep it on your network

The default posts to `ntfy.sh`, a public relay. That is the zero-setup
path, and the trade is real: **anyone who knows your topic name can read
your notifications**, and the text passes through someone else's server.
Hence the random topic name above — on the public server the topic *is*
the password.

For "same network" in the literal sense, run your own:

```bash
brew install ntfy
ntfy serve --listen-http :2586
./notify-bridge.py --topic anything --server http://192.168.1.5:2586
```

Then point the iPhone app at `http://192.168.1.5:2586` as its server.
Nothing leaves the LAN. The cost is that it only works while the phone
is on your network — no notifications from the bus.

## Filtering

Everything is forwarded by default, which is usually too much.

```bash
# only these apps
./notify-bridge.py --topic … --only "MobileSMS,Messages,ical,mail"

# everything except the noisy ones
./notify-bridge.py --topic … --exclude "softwareupdate,maccy,terminal"
```

Match is a case-insensitive substring of the bundle id, so `ical` catches
`com.apple.ical`. `--dry-run` prints the bundle ids as they arrive, which
is the easy way to build the list.

## Running it in the background

`com.kenny.notify-bridge.plist` is a LaunchAgent. Replace the three
`REPLACE-WITH-ABSOLUTE-PATH` markers and `CHANGE-ME`, then:

```bash
cp com.kenny.notify-bridge.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kenny.notify-bridge.plist
```

Note it must be a LaunchAgent, not a daemon: the notification database
is in your home directory and the process has to be you to read it.
Grant Full Disk Access to the **python binary**, not to Terminal —
launchd starts python directly, so that is what macOS checks.

## When it silently stops forwarding

It fails quietly by design — nothing arrives, and the Mac gives no sign.
Work down this list; each step tells you which half is broken.

**1. Is the agent even running?**

```bash
launchctl list | grep notify-bridge      # no output means it is not loaded
tail -20 ~/mac-notify-iphone/bridge.log
```

A missing log file means it has never started. Re-`bootstrap` it.
`KeepAlive` restarts a crash, so a crash loop shows as a log that keeps
repeating its first few lines.

**2. Can it still read the database?**

```bash
/usr/bin/python3 notify-bridge.py --check
```

Run it with the *same interpreter the agent uses*, because Full Disk
Access is granted per binary. A `✗ cannot read it` here after months of
working usually means a macOS update reset the privacy grant — re-add
the binary in System Settings.

**3. Is the database still where and what it was?**

This is the fragile part, and the reason this section exists. The bridge
reads `~/Library/Group Containers/group.com.apple.usernoted/db2/db`,
which Apple does not document and has moved before. A macOS update can
change any of it without a word. Check, in order:

```bash
ls -l ~/Library/Group\ Containers/group.com.apple.usernoted/db2/db
sqlite3 "$HOME/Library/Group Containers/group.com.apple.usernoted/db2/db" \
    ".schema record" "SELECT count(*) FROM record"
```

- **File gone** → Apple moved the store. Find the new one and change `DB`
  in the script.
- **`no such table: record`**, or no `rec_id` / `app_id` / `data` /
  `delivered_date` column → the schema changed. `fetch_since` is the only
  query, so it is a one-place fix.
- **Rows exist but nothing forwards** → the schema held and the *payload*
  changed. `--dry-run` decodes without sending; if it prints bundle ids
  but no titles, the binary plist keys moved. `decode` reads `req.titl`,
  `req.subt` and `req.body`; dump one row's plist and look for their
  replacements.

**4. Is the phone half broken instead?**

Post to the topic by hand. If this shows up on the phone, the Mac side is
at fault; if it does not, the problem is the topic, the server URL, or
the ntfy app's subscription.

```bash
curl -d "test" https://ntfy.sh/YOUR-TOPIC
```

**5. `delivery failed` in the log**

The bridge names its own cause here. Two are common:

- **`has no CA certificates`** — a python.org interpreter that never had
  `Install Certificates.command` run. `curl` works on the same Mac, which
  makes it look like a network problem. This is why the LaunchAgent
  pins `/usr/bin/python3`, and why `./notify-bridge.py` can fail while the
  agent succeeds: the shebang picks up whatever `python3` is on `PATH`.
- **connection refused** — self-hosted `ntfy serve` is not running, or the
  Mac's IP changed. Give it a DHCP reservation.

## Limits worth knowing

- **It polls**, every 2 seconds by default. Not instant, and not a push.
- **Notifications only.** It cannot forward an app's *actions*, and
  replying on the phone does nothing back on the Mac.
- **It reads a private database.** Apple does not document this schema
  and has moved it before. If a macOS update empties your feed, the path
  or the plist keys are where to look — see *When it silently stops
  forwarding* above.
- **Everything is plaintext over HTTP** if you self-host without TLS.
  On a home LAN that is usually fine; on school wifi it is not.
