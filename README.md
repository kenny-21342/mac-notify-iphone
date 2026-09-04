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

## Limits worth knowing

- **It polls**, every 2 seconds by default. Not instant, and not a push.
- **Notifications only.** It cannot forward an app's *actions*, and
  replying on the phone does nothing back on the Mac.
- **It reads a private database.** Apple does not document this schema
  and has moved it before. If a macOS update empties your feed, the path
  or the plist keys are where to look.
- **Everything is plaintext over HTTP** if you self-host without TLS.
  On a home LAN that is usually fine; on school wifi it is not.
