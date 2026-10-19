# Drobo Agent

The always-on piece. It watches the Drobo, keeps history, raises alerts,
receives photo backups from the phone, and serves all of it as plain JSON.

The Windows app and the iOS app are both just clients of this. Neither of them
knows the Drobo protocol exists. The protocol itself lives in the separate
library in [`../sdk/`](../sdk/); inside the agent,
[`drobo_agent/drivers.py`](drobo_agent/drivers.py) uses it for the regular
readings and `drobo_agent/api.py` for on-demand requests such as settings
changes, shares and DroboApps.

Python 3.10 or newer, standard library only; developed and tested on 3.14,
and the installer bundles 3.14.2 so nothing needs installing on a tester's
machine.

The full list of what the agent exposes, with the status of each part, is
[`../docs/capabilities.md`](../docs/capabilities.md); how it came to be built
this way is [`../docs/development-history.md`](../docs/development-history.md).

## In this folder

| | |
|---|---|
| `run_agent.py` | Start here: the one file that runs the agent |
| `check_updates.py` | What the installed "Check for Updates" shortcut runs: the update check in `drobo_agent/updates.py`, from any folder and on the bundled Python, which will not find the agent's code the way `python -m` expects. Exit status 0 up to date or switched off, 1 an update is available, 2 could not tell, 3 the check could not run |
| `config.json` | Your own settings, created beside `run_agent.py` on the first run from source: the access token and, if you set up email alerts, your email server's password. Only on your PC, and git ignores it. Never share or commit it |
| `config.example.json` | A sample settings file to read or copy; the agent never reads it by itself. Note that it sets `"driver": "mock"` (the simulator), while the `config.json` the agent creates on its first run uses your real Drobo (`"driver": "drobo5n"`) |
| `config.mock.json` | Appears after the first run of `START WITH SIMULATED DROBO (testing).bat`: the simulated Drobo's settings and its own access token. Git ignores it |
| `drobo_agent/` | The agent's code, one file per job: `api.py` (the web API; its opening comment lists every route), `webui.py` (the web dashboard page), `monitor.py` (polls the Drobo, keeps history, decides alerts), `drivers.py` (the real Drobo driver and the simulator), `notify.py` (sends alerts: console, ntfy, email), `photos.py` (receives phone photo backups), `backup.py` (copies the Drobo to an external drive), `updates.py` (the update check, off by default), `config.py` (reads `config.json` and makes the token), `qr.py` (the pairing QR code), `models.py` (the data shapes the agent adds), `__init__.py` (makes the agent use the `sdk/` beside it). It speaks the Drobo's protocol only through the library in [`../sdk/`](../sdk/), `drobo_nasd`; photo and drive backups use the Drobo's shares like any other network folder |
| `tests/` | Ten test suites that need no hardware. Only in the source repository, not in an installed copy |

---

## Run it

The commands on this page run from inside this `agent` folder. From the
repository's top folder, put `agent/` in front of the file name instead, for
example `py agent/run_agent.py`.

```bash
py run_agent.py
```

First run generates `config.json` with an access token and prints the
dashboard's address. Open it and the page asks once for that token, or start
with `--open` to have the browser opened already signed in. The token itself
is never printed: that console is what testers are asked to paste when
something goes wrong. Running from source, that file sits next
to `run_agent.py`; the **installed** copy is started with
`--config "%LOCALAPPDATA%\DroboDashboardReborn\config.json"` so the token
survives an uninstall or upgrade of the program folder (see
`../windows/installer/TESTING.md`).

## The web dashboard

Served at `/`. One self-contained page — no frameworks, no CDN, no build step.
Device header with live state, capacity bar, a card per drive bay with
temperatures, active alerts sorted worst-first, and a 24-hour temperature
sparkline. Updates arrive over Server-Sent Events, so it changes the moment the
Drobo does rather than on a refresh timer. Light and dark follow the system
setting.

**This is the fastest route to a Drobo UI on your iPhone**: open the agent's
address in Safari (after changing `agent.bind`, see [Security](#security)) and
it works today — no Xcode, no Mac, no developer account.
It also served as the layout prototype for the Windows app (which shipped as
WPF, not WinUI 3); if a panel doesn't
work here it won't work there.

The page shell carries no data and needs no token, so you can bookmark it. It
asks for the token once and keeps it in `sessionStorage`. If you pass
`?token=...` in the URL it's saved and then scrubbed out of the address bar so
it doesn't linger in history.

When the mock driver is running, a simulator panel appears with buttons to fail
or pull any bay — the quickest way to see the whole alert path work end to end.

```bash
py run_agent.py --once
```

Takes a single reading, prints it as JSON, exits. Useful for checking a driver.

```bash
py tests/test_agent.py
```

Starts a real agent on port 7431 of this PC (a fixed port, so run one copy
of the tests at a time), exercises every endpoint, checks the answers, and
stops the agent again. Then, on port 7432, it starts the real Drobo driver
with discovery made to find nothing, and checks the agent still serves and
raises the "cannot reach" alert; no Drobo is contacted. 272 checks as of 2026-10-19 (the number grows with
the API; to count it yourself in PowerShell:
`(py tests\test_agent.py | Select-String -SimpleMatch "  PASS ").Count`, or in
Git Bash: `py tests/test_agent.py | grep -c "  PASS "`), no hardware needed.
Run it after any change.

This is one of thirty test suites in the project: ten under
`agent/tests/` (`test_agent`, `test_backup`, `test_config_api`,
`test_drivers`, `test_droboapps_api`, `test_identify_api`,
`test_notification_simulation`, `test_notify`, `test_shares_api`,
`test_updates`), sixteen under
`sdk/tests/` (the protocol: esatm, discovery, config, config writes, shares,
share writes and lifecycle, write arbitration, sysinfo, event log, identify
and friends), and four under `tools/`. Every suite runs standalone with
`py <file>`, from its own folder or from the repository's top folder, and none
needs real hardware.

---

## Two drivers: fake data for testing, real data for the actual Drobo

`Drobo5NDriver` is real and works against real hardware — it reads the status
greeting the device pushes on TCP 5000, discovers the device by mDNS when its
address changes, and tops up chassis temperature/uptime from the command
channel on TCP 5001. `config.json` uses `"driver": "drobo5n"` unless you
change it, and every layer above it (API, alerts, notifications, both apps)
runs on the real array, nothing further to build.

The `mock` driver still exists, and is still useful: it simulates a Drobo 5N
(five bays, four populated, drifting temperatures, capacity creeping upward)
so the whole stack can be built and tested without a physical device on hand,
and so failure paths can be triggered on demand rather than waited for.

You can break the fake Drobo on demand to prove the alert path works:

```bash
curl -X POST -H "X-Agent-Token: YOUR_TOKEN" http://127.0.0.1:7420/api/mock/fail/3
curl -X POST -H "X-Agent-Token: YOUR_TOKEN" http://127.0.0.1:7420/api/mock/reset
```

The actions: `fail/<bay>`, `warn/<bay>`, `pull/<bay>`,
`battery/<warning|failed>`, `power/<warning|failed>`,
`write/<none|hangs_up|refused|unconfirmable>` (how every simulated settings or
share change ends, until reset), and `reset`.

The simulated Drobo also has one account, **user `admin`, password `admin`**,
with read/write on every share. `POST /api/mock/sign-in` with
`{"share", "username", "password"}` says whether that login would open that
share, as `{"ok", "win32_error", "error"}`, and the Windows app's Connect dialog
uses it whenever the agent runs the mock driver. It refuses with 409 on any
other driver, so it can never be aimed at a real device.

**Still simulated, even against the real driver:** cache battery, fan and PSU
(the device event log left this list on 2026-08-21; it is fetched for real
now). That's a hardware/protocol limit, not a gap in this
codebase: on a real 5N those commands are accepted and then answer empty or
time out, measured repeatedly, and the chassis plausibly has no such sensors
to report. The UI labels these fields as simulated rather than passing fake
readings off as measurements.

---

## API

Everything except `/api/ping` needs the `X-Agent-Token` header.
`/api/events` also accepts `?token=` because browsers can't set headers on an
EventSource.

The routes most people touch. The complete list — forty-odd today, including
the config writes, share create/delete, DroboApps, the event log and
DroboSync — is the module docstring at the top of `drobo_agent/api.py`, which
is kept current; `../docs/capabilities.md` maps each to its status.

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The web dashboard. No auth — the shell holds no data. |
| GET | `/api/ping` | Liveness and version. No auth — safe for discovery. |
| GET | `/api/status` | Everything: device, capacity, drives, overall state |
| GET | `/api/device` | Identity and firmware |
| GET | `/api/drives` | Per-bay detail |
| GET | `/api/capacity` | Capacity numbers, including `used_fraction` |
| GET | `/api/alerts?all=1` | Active alerts, or the full history |
| GET | `/api/history?limit=N` | Trimmed series for graphing |
| GET | `/api/netcheck?host=` | Whether this PC is even on the Drobo's network — the usual reason one "vanishes". `/api/status` already carries this as `network_hint`; this is for a device picker with an address and no snapshot. |
| GET | `/api/events` | Server-sent events — live snapshots and alerts |
| GET | `/api/photos/stats` | How much has been backed up |
| POST | `/api/photos/have` | `{"hashes":[...]}` → `{"missing":[...]}` |
| POST | `/api/photos` | Raw file body; `X-Filename` + `X-Taken-At` headers |
| POST | `/api/photos/begin` | Start a chunked upload (videos) |
| POST | `/api/photos/chunk` | Append a chunk; `X-Upload-Id` + `X-Offset` |
| POST | `/api/photos/finish` | Verify hash and file it; `X-Upload-Id` |
| GET | `/api/photos/session?id=` | How many bytes of an upload we already hold |
| GET | `/api/pair` | URL + token for the phone, in one payload |
| GET | `/api/pair.svg` | The same as a scannable QR code |
| POST | `/api/mock/<action>/<bay>` | Fault injection, mock driver only |

---

## Photo backup

The DroboPix replacement, receiving half. It talks to the Drobo purely as a
file share, so **it works today with no protocol reverse engineering at all.**

Set `photos.target_dir` to a share on the Drobo — a mapped drive (`Z:\Photos`)
or a UNC path (`\\drobo\Photos`).

The upload flow is two steps, and the first one is what makes it cheap:

1. The phone sends the SHA-256 hashes of photos it's holding to
   `/api/photos/have`. The agent replies with just the ones it has never seen.
2. The phone uploads only those, one at a time, to `/api/photos`.

That means an interrupted backup resumes for free, and reinstalling the app
doesn't re-upload your entire camera roll.

Files are filed as `target_dir/YYYY/YYYY-MM/name`, using the `X-Taken-At` date
the phone supplies. Duplicates are detected by content, so the same photo sent
twice is stored once — but two *different* photos that happen to share a name
(`IMG_0042.JPG` is not unique across devices) are both kept, with `~1`
appended. Uploads are written to a `.part` file and renamed into place, so a
dropped connection can't leave a half-written photo in your library.

Filenames from the phone are stripped to a safe basename before use — a
filename like `../../../escape.jpg` lands inside the target directory, not
outside it. Only image and video extensions are accepted; this is not a
general-purpose file drop.

---

## Alerts

Alerts are sticky: they stay active until the condition clears, so the apps can
show "here's what's wrong right now" rather than a stream of one-off messages.
Long-running problems are re-sent every `resend_seconds` so they don't get
forgotten, and a "Resolved:" message goes out when a condition clears.

Rules that fire today — plus battery, PSU and fan warning/failed rules, which
stay silent on a 5N that reports none of those sensors but fire from the mock
and the notification simulation: drive failed, drive reporting a problem, drive over the
temperature thresholds, **chassis temperature over threshold** (warning at
50 °C, critical at 60 °C, from the real `eCmdGetSysInfo` reading), array over
the capacity thresholds, and Drobo unreachable.

To get alerts on your phone, add `"ntfy"` to `notify.backends` and set a topic:

```json
"notify": {
  "backends": ["console", "ntfy"],
  "ntfy": { "server": "https://ntfy.sh", "topic": "drobo-<pick-a-long-random-name>" }
}
```

Install the ntfy app, subscribe to that topic, done. **Pick a long random topic
name and treat it like a password** — on the public ntfy.sh server, anyone who
knows the topic can read it.

This is the stopgap until the iOS app has proper push notifications, and it's
worth having regardless: it works whether or not the app is installed.

---

## Security

- **Binds to `127.0.0.1` by default** — this PC only. Change `agent.bind` to
  `0.0.0.0` when you want the phone to reach it, and only ever on your own LAN.
- **Never port-forward this.** For access away from home, put Tailscale on the
  machine running the agent.
- The token is a shared secret in plain HTTP. That's fine on a home LAN;
  it is not fine on an untrusted network.
- `config.json` contains the access token and, if you set up email alerts,
  your email server's password. It's in `.gitignore` — keep it that way.
