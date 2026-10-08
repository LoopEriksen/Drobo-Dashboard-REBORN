# Running the agent in the background

**Short answer: yes — and it should be a separate background process, not code
living inside the Dashboard window.**

That's not extra work; it's less. The agent and the Dashboard app want opposite
lifetimes: you want monitoring to run all the time, and you want the window to
be openable and closable without consequence. Keeping them separate gets both,
and it's why the agent talks HTTP instead of being a library.

```
   Drobo 5N
      |
      | (the old proprietary protocol -- one place, one implementation)
      |
   Drobo Agent  ---- runs at boot, no window, always watching
      |
      |  http://127.0.0.1:7420  (plain JSON)
      |
      +---- Dashboard window   -- open it, close it, doesn't matter
      +---- iPhone             -- over the LAN, or Tailscale from anywhere
```

What you get by splitting them:

- **Alerts work when the window is closed.** A drive failing at 3am is exactly
  when nobody has the Dashboard open.
- **Photo backup works when you're not logged in.** The phone can upload as
  soon as it's on the home Wi-Fi, whether or not anyone has touched the PC.
- **Crashing the UI doesn't stop monitoring**, and restarting the UI is instant.
- **The Dashboard becomes easy to build** — it just draws JSON.
- **You can move the agent to a Raspberry Pi later** and change nothing else.
  Only the address the apps point at changes.

---

## Pick a start-up method

### Option A — Task Scheduler, at boot (recommended)

Runs whether or not anyone is logged in, which is the whole point.

Use `pythonw.exe` rather than `python.exe` so no console window appears. Find
its path with:

```bash
py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"
```

Then create the task (run this in an **Administrator** PowerShell, and
substitute the real paths):

```bash
schtasks /create /tn "Drobo Agent" /sc onstart /ru SYSTEM /rl HIGHEST /f /tr "\"C:\Path\To\pythonw.exe\" \"C:\Users\<you>\...\Drobo Dashboard REBORN\agent\run_agent.py\""
```

Start it now without rebooting:

```bash
schtasks /run /tn "Drobo Agent"
```

Check it's alive:

```bash
curl http://127.0.0.1:7420/api/ping
```

That answers once the agent is up, whether or not it has found the Drobo;
`/api/status` (with the token) says which, in `reachable`.

**It starts whether or not the Drobo can be found.** A PC that boots before
its Drobo, or before its own network is up, runs the agent anyway: the
dashboard answers, the critical "Cannot reach the Drobo" alert is raised, and
discovery runs again on every poll until the Drobo answers, with no restart.
Until 2026-09-26 the agent exited instead when automatic discovery found
nothing -- and since this task has no window and no restart, a PC that booted
first ran no monitoring at all.

What still stops it at start-up is a device that answers but is provably not
your Drobo (a different serial from the `expected_esa_id` in `config.json`),
or a `config.json` that is not valid JSON. `pythonw` has no window to show the
reason in, so if `/api/ping` does not answer, run `py agent/run_agent.py` once
in a terminal and read it there.

Stop or remove it:

```bash
schtasks /end /tn "Drobo Agent"
```

```bash
schtasks /delete /tn "Drobo Agent" /f
```

> **One catch with `/ru SYSTEM`:** the SYSTEM account has no mapped drives, so
> `photos.target_dir` must be a **UNC path** (`\\drobo\Photos`), not a mapped
> letter (`Z:\Photos`). If the share needs a password, run the task as your own
> user account instead — replace `/ru SYSTEM` with `/ru <your-username>` and
> add `/rp *` to be prompted, plus `/it` if you'd rather it only run while
> you're logged in.

### Option B — Startup folder, at login (simplest)

Good enough while you're still developing, and much easier to see what's going
on. Press `Win+R`, type `shell:startup`, and drop in a shortcut to:

```
"C:\Path\To\pythonw.exe" "...\agent\run_agent.py"
```

Downside: nothing runs until someone logs in.

### Option C — a real Windows Service (later)

Cleanest for a shipped product: proper service lifecycle, recovery on crash,
`services.msc` integration. Needs a wrapper such as NSSM or `pywin32`, so it's
worth doing if it is ever wanted. The MSI exists (built 2026-07-25,
tester-ready 2026-09-02) and deliberately does **not** install a service: the
Start Menu entry runs `Launch-DroboDashboardReborn.bat`, which starts the
agent in its own minimised window and then the app. A service remains an
option, not a plan.

---

## While you're developing

Just run it in a terminal and watch the log:

```bash
py agent/run_agent.py
```

Ctrl-C stops it cleanly. Set up Option A once things are stable.

---

## How the Dashboard app will talk to it

Three things it needs, from the agent's `config.json` (the installed app looks
in `%LOCALAPPDATA%\DroboDashboardReborn\config.json` first, and walks up to
`agent/config.json` only for the dev-tree layout):

- the address — `http://127.0.0.1:7420`
- the token — sent as the `X-Agent-Token` header on every request
- that's it

Draw the main screen from `GET /api/status`, subscribe to `GET /api/events` for
live updates instead of polling, and read `GET /api/history` for graphs. If the
agent isn't running, the app says so in its state pills and stays usable; it
does not try to talk to the Drobo itself, and it starts no scheduled task —
the launcher starts the agent alongside the app. **Only one component
ever speaks the Drobo protocol.**

---

## Reaching it from the iPhone

1. Change `agent.bind` to `0.0.0.0` in `config.json` and restart the agent.
2. Allow the port through Windows Firewall on the **private** profile only:

```bash
netsh advfirewall firewall add rule name="Drobo Agent" dir=in action=allow protocol=TCP localport=7420 profile=private
```

3. The phone uses `http://<pc-lan-ip>:7420` with the same token.

**Never forward this port on your router.** For access away from home, install
Tailscale on the PC running the agent and on the phone — then the phone reaches
the same LAN address from anywhere, with no ports open to the internet. That is
the replacement for Drobo's dead myDrobo relay, and it's faster than the
original was.
