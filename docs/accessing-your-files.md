# Getting to your files on the Drobo

Measured against the live Drobo 5N on 2026-07-26.

If Windows says it can't reach your Drobo's shared folders, **nothing is wrong
with the Drobo.** Windows changed. This page explains what changed, and what to
do about it.

---

## The short version

Your Drobo offers its folders to "Everyone". On the network that means a
**guest** login. Windows 11 stopped accepting guest logins to file servers, so
Windows refuses before it even talks to the Drobo.

**The fix: connect with a user name and password instead of as a guest.** The
Shares tab in Drobo Dashboard REBORN does this for you.

---

## What we actually measured

| Test | Result |
|---|---|
| Is the Drobo on the network? | Yes — replies to ping |
| Is the file-sharing port open? | Yes — TCP 445 accepts connections |
| Is the Drobo healthy? | Yes — every drive green, pushing status normally |
| `net view \\<drobo>` | **Access is denied** |
| Opening a share as a guest | **Fails: `0xC05D0003`** |

`0xC05D0003` is Windows' code `STATUS_SMB_GUEST_LOGON_BLOCKED`. It means
literally "I refuse to log in as a guest", and Windows produces it *without
contacting the device*.

## Why Windows does this

Two Windows file-sharing settings decide this:

```
EnableInsecureGuestLogons : True or False
RequireSecuritySignature  : True     <-- this one
```

Windows 11 24H2 turned `RequireSecuritySignature` on by default. Every file
share connection must now be cryptographically signed. **A guest session cannot
be signed** — there's no account to sign as. So the guest path is dead no matter
what the other setting says.

This is a sensible security change. It just happens to break every NAS that
relies on guest access, and the Drobo — last updated in 2023 — is one of them.

## Three ways out, best first

### 1. Connect as a named user — recommended

Use the **Shares** tab in the app. It asks for your Drobo user name and
password and hands them straight to Windows.

This is the right answer because:

- Nothing on your PC is made less secure.
- Windows can sign the connection, so it's happy.
- Windows Credential Manager can remember it, so you do this once.

Your password goes directly to Windows through `WNetAddConnection2`. It is
deliberately *not* done by running `net use`, because a password typed on a
command line is visible to every other program running on the machine.

It is never sent to the agent, never logged, and never written into any file
this project owns. There is exactly one place it can persist, and only if you
tick *Save this login on this PC*: Windows' Credential Manager, which encrypts
it under your Windows account. Then Windows itself authenticates with it next
time, whether you reach the share through this app or through File Explorer,
and this app never needs to see it again. Windows will not hand a saved share
password back to a program that asks, which is why the box in the dialog stays
empty when a login is already saved.

### 2. Give the shares a real user on the Drobo

If the shares are set to "Everyone" with no user account, there may be no
credential to type. The original Dashboard could create share users. Ours
cannot yet — creating a user has never been observed on the wire, so its
format is unknown and this project will not guess one. (The project now
performs other, captured, vetted writes; see `capabilities.md`.)

Until then the Drobo's own admin account is usually the way in.

### 3. Re-enable guest logins — not recommended

It is possible to turn Windows' guest restrictions back off. **We are not going
to walk you through it, and the app will never do it for you.**

The reason: it weakens SMB security for *every* server this PC talks to, not
just the Drobo, and it re-opens the class of attack the change was made to
prevent. Option 1 achieves the same result with none of that.

If you decide you want it anyway, it's your machine and your call — but it's a
Windows security setting, so change it yourself, deliberately, rather than
having a program do it quietly.

---

## Worth checking: is your Drobo's network running at a tenth speed?

The device reports its own port state. A slow link looks like this:

```
PortSpeed  100
PortDuplex full
```

**A Drobo 5N has a gigabit port.** Linked at 100 Mbit/s, every file copy runs
at roughly a tenth of what it should — call it 11 MB/s instead of 110 MB/s.
Nothing warns you about this; it just feels slow forever.

Usual causes, in the order worth checking:

1. **The network cable.** A damaged cable, or an old Cat 5 one, will fall back
   to 100 Mbit. This is by far the most common cause. Swap it for a Cat 5e or
   Cat 6 cable — they cost very little.
2. **The wall socket or patch panel**, if the cable runs through a wall. A
   broken pair in the run does the same thing.
3. **The switch or router port.** Try a different port; try a known-gigabit one.

When the link is slow, the app shows a warning on both the Shares tab and the
web dashboard, and it disappears on its own once the link comes up at 1000.

---

## Note for later: firmware version

The live unit reports firmware **4.3.1-8.126.117497**. The image unpacked in
`vendor/` is **4.2.1**, which is where the protocol and command table came from.
The two agree on everything observed so far. Worth knowing when a command
behaves differently from what the 4.2.1 binary suggests.

---

## Security check on the admin account — 2026-07-26

Prompted by a reasonable worry: a drive mapping can authenticate as `admin`
without Windows holding a saved password for it. Does that make the account
passwordless?

**No. It is not exposed.** Two independent read-only checks:

1. **A blank password is rejected.** Presenting `admin` with an empty password
   makes the Drobo fall back to granting a *guest* session instead of
   authenticating the account — which is itself the proof that it refused the
   empty credential. (Windows then blocks the guest session, giving the same
   `0xC05D0003` as everywhere else on this page.)

2. **The device does not disclose the real password.** Asking for
   `DRINasAdminConfig` returns a `<Password>` field made of *identical*
   characters — a mask — and a single-character `<EncryptedPassword>`.

This **corrects our own documentation**, which previously asserted the Drobo
hands out admin credentials in the clear to anyone who completes the
serial-only login. That was never measured. On firmware 4.3.1 it is wrong.

Two things stay true regardless:

- **The redaction stays.** `config_cmd._strip_secrets()` still strips every
  credential-shaped field before it can reach a log, the API, or the history
  buffer. One firmware checked is not a guarantee for every firmware, every
  model, or every config section.
- **`<ValidPassword>` reads `0` on a device that demonstrably has a password.**
  So it does not mean "no password set". Its actual meaning is unknown — add it
  to the list of Drobo status codes we report but do not interpret.
