# iOS app — DroboPix replacement

Automatic photo and video backup from an iPhone to a Drobo. Distributed by
sideloading, not the App Store.

**Status: not started.** This folder holds only this plan; no app code exists
yet, because building an iPhone app needs a Mac (see
[What you need](#what-you-need)). The agent side it talks to is finished and
tested.

---

## Why it lives in this repo

The app and the agent share one contract: the JSON API in
[`agent/README.md`](../agent/README.md). Splitting them across repos means every
endpoint change is two commits in two places with nothing tying them together.
One repo, `ios/` alongside `agent/`.

*(The custom-firmware fork is separate for a different reason — opposite risk
profiles, where a mistake destroys hardware. That reasoning doesn't apply here.)*

---

## What it does not need

- **No web address, no domain, no cloud account.** The app points at an IP and a
  token — `http://192.0.2.50:7420`. On your own network nothing leaves the
  house.
- **No App Store, no review.** It's sideloaded.
- **No myDrobo, no relay.** Away from home, Tailscale gives you the same LAN
  address with no ports open to the internet. The original relay capped around
  8 Mbps; this won't.

DroboPix worked the same way — it paired by scanning a QR code off the Dashboard
screen and then talked straight to the NAS over Wi-Fi. The cloud relay belonged
to DroboAccess, a different app.

---

## What you need

| | |
|---|---|
| **A Mac** | Non-negotiable. Xcode is macOS-only and there is no legitimate way around it. A Mac mini or a rented cloud Mac both work. |
| **Xcode** | Free |
| **Apple ID** | Free tier signs an app for **7 days**, then it stops launching |
| **Apple Developer Program** | $99/yr, extends signing to **1 year** |

**Recommendation: pay the $99.** Not for the store — for the difference between
re-signing every week and forgetting about it for a year. It's also required for
push notifications, if the app ever wants them.

Sideloading routes: Xcode direct install, or SideStore/AltStore which can
re-sign over Wi-Fi so the 7-day cycle mostly disappears. AltStore PAL (Apple's
sanctioned marketplace route) is EU/Japan/Brazil only and not relevant here.

---

## What "constantly back up" actually means on iOS

Set expectations here, because iOS will not do what the phrase implies.

**No third-party app is allowed to run continuously in the background.** What
exists instead:

| Mechanism | When it fires | Reliability |
|---|---|---|
| App in foreground | Immediately | Total |
| **Geofence / region monitoring** | On arriving home | **High — this is the one to build on** |
| Wi-Fi SSID check | Combined with the above | Confirms you're on the right network |
| `BGProcessingTaskRequest` | OS decides; usually charging + Wi-Fi + idle | Opportunistic, often overnight |
| `PHPhotoLibraryChangeObserver` | New photo taken | Only while the app is running |
| Silent push (APNs) | Agent triggers it | Needs the paid account and a push pipeline |

**Realistic behaviour: photos back up when you get home, and catch up overnight
on the charger.** Not second-by-second. That's the ceiling for any iOS app, not
a limitation of this design.

DroboPix used geofence **and** SSID together — it wasn't a gimmick, it was the
only dependable way to get woken up. Worth copying exactly.

---

## What it cannot do

**Backed-up photos will not appear inside the iPhone Photos app.** iOS gives
third parties no API to add a library source to Photos.app. Nothing can do this;
it isn't a matter of effort.

The nearest achievable things:

- **A File Provider extension** — makes the Drobo appear in the **Files** app,
  the way Dropbox does. Genuinely achievable, and the closest to what people
  usually mean.
- **An in-app gallery** — browse what's been backed up, inside our own app.
- **The Files app's built-in SMB client** — already works today with zero code,
  since the 5N speaks SMBv2. Try this before building anything.

Reading *from* the camera roll is different and completely standard — that's
PhotoKit, and it's how the backup works.

---

## How backup works

The agent already implements the receiving half, and it's tested.

### Step 1 — ask what's missing

```
POST /api/photos/have     {"hashes": ["<sha256>", ...]}
   -> {"missing": ["<sha256>", ...]}
```

The phone hashes what it holds and uploads only what the agent has never seen.
Reinstalling the app does not re-upload the camera roll.

### Step 2 — upload

Small photos, one shot:

```
POST /api/photos          body = raw bytes
      X-Filename: IMG_0042.HEIC
      X-Taken-At: 1700000000        (unix seconds)
```

Videos, chunked and resumable — **this is the path video must take**, because a
4K clip is gigabytes and iOS will suspend a background transfer part-way:

```
POST /api/photos/begin    {"filename": "IMG_7788.MOV", "size": 2400000000,
                           "sha256": "<hash>", "taken_at": 1700000000}
   -> {"status": "ready", "received": 0}          (or "duplicate")

POST /api/photos/chunk    body = chunk bytes
      X-Upload-Id: <sha256>
      X-Offset: 0
   -> {"received": 1048576, "complete": false}

POST /api/photos/finish
      X-Upload-Id: <sha256>
   -> {"status": "stored", "path": "2023/2023-11/IMG_7788.MOV"}
```

**The upload id is the file's own sha256**, so resuming needs no bookkeeping on
the phone. Call `begin` again with the same hash after a suspension and you're
told exactly how many bytes already arrived. `GET /api/photos/session?id=<hash>`
answers the same question without starting anything.

Guarantees worth knowing when writing the client:

- The agent **recomputes the hash** before filing anything. A corrupted or
  mismatched upload is discarded, not stored.
- **Resuming preserves the original capture date.** Only send `taken_at` on the
  first `begin`.
- A wrong `X-Offset` isn't fatal — the reply carries the true position, so the
  client can seek and continue.
- Files land in `YYYY/YYYY-MM/`, duplicates are detected by content, and two
  different files with the same name both survive.

### Step 3 — pairing

```
GET /api/pair  ->  {"url": "http://192.0.2.50:7420", "token": "...", ...}
```

```
GET /api/pair.svg  ->  the same thing as a scannable QR code
```

Everything the phone needs in one payload, and a QR code so setup is a camera
scan rather than typing a 32-character token on a phone keyboard. DroboPix
paired exactly this way. The QR encoder is stdlib-only
([`agent/drobo_agent/qr.py`](../agent/drobo_agent/qr.py)) — no dependency.

Both endpoints require the token, since they carry it.

---

## Building for yourself now, publishing later

Nothing about the plan changes. But a handful of decisions are **cheap now and
expensive to retrofit**, so make them on day one even though the app is only
for you.

| Decision | Why it's expensive later |
|---|---|
| **Pick the real bundle ID now** (`com.yourname.droboreborn`) | Changing it makes iOS treat the app as a different app: Keychain entries, stored settings and backup state all vanish. Renaming after a year of your own photos is the worst time to find out. |
| **Never hardcode your IP or token** | The single thing that most often makes a personal app unshippable. The pairing flow already exists — use it from the first build. |
| **Stay inside App Store–legal APIs** | Anything that only works because it's sideloaded is a dead end. No private APIs, no entitlements you can't justify. |
| **Write real permission strings** | `NSPhotoLibraryUsageDescription`, `NSLocationAlwaysAndWhenInUseUsageDescription`, `NSLocalNetworkUsageDescription` are all required. Review rejects vague ones, and they're user-facing anyway. |
| **Add `PrivacyInfo.xcprivacy` early** | Apple requires a privacy manifest. Trivial to add now, tedious to reconstruct later. |
| **Keep a demo mode** | See below — it's both a development convenience and a review requirement. |

### App Transport Security

iOS blocks plain HTTP by default. For an app that talks to a device on your own
network, **`NSAllowsLocalNetworking`** is the exemption Apple actually sanctions
— it doesn't weaken security for anything else, and it's how every NAS client
on the store works.

One thing to verify rather than assume: whether ATS treats a **Tailscale**
address (`100.64.0.0/10`, carrier-grade NAT space) as local. If it doesn't,
remote access would need the agent to serve TLS. Not a problem for LAN-only use,
which is where this starts.

### The review consideration nobody expects

An app that does nothing without a Drobo *and* a running agent **will be
rejected** — a reviewer has neither. Every self-hosted client app hits this.

The fix is a **demo mode**: sample data, no server needed. You need one anyway
to develop the UI without hardware, exactly like the agent's mock driver. Build
it as you go and the review problem is already solved. Retrofitting it under
rejection pressure is miserable.

### What publishing does not require

- No change of architecture — sideloaded and store builds are the same code,
  different signing.
- No cloud, no accounts, no backend. It's a client for software the user runs
  themselves, like a Home Assistant or Synology client.
- No policy problem. Apps that talk to your own NAS are unremarkable and
  plentiful on the store.

---

## Build order

1. **Pair and upload one photo by hand.** Prove the round trip before anything
   else. Store the URL and token in the Keychain.
2. **Full-library sync in the foreground**, using `have` then upload. Get
   progress and cancellation right here, where you can watch it.
3. **Chunked video upload** on `URLSession` background transfers.
4. **Geofence + SSID wake**, then `BGProcessingTask` as the overnight catch-up.
5. **In-app gallery** of what's been backed up.
6. **File Provider extension** — the Files-app integration.

Steps 1–3 need no special entitlements. Step 4 needs "Always" location
permission, which the app must justify clearly to the user in its permission
prompt.

---

## Design notes

- **Keychain for the token**, never `UserDefaults`.
- **Wi-Fi only by default**, with an explicit opt-in for cellular. Copy the
  DroboPix behaviour.
- **Never delete anything from the phone.** Backup only. The moment an app can
  delete originals, a bug becomes data loss.
- **Show what hasn't backed up yet**, not just what has. A backup tool that
  can't tell you what it's missing isn't trustworthy.
- **Aim at what iOS can't already do.** The Files app browses the Drobo over SMB
  for free. The reason this app exists is automatic photo backup, health and
  alerts — not file browsing.
