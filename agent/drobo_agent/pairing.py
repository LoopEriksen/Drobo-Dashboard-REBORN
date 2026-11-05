"""
Pairing a phone with the agent, and the photo-only token each phone
gets.

Until 2026-11-05 GET /api/pair answered with the agent's own admin
token, and /api/pair.svg drew that token into a QR code that was also a
sign-in link to the whole dashboard. Nothing used pairing yet, but a
phone paired that way could have done everything the dashboard can,
deleting a share included, and a photo of the QR code on the screen
was the admin token for whoever took it.

Pairing is two steps now:

  1. On the PC, GET /api/pair (admin token) issues a pairing code. It
     is random, it can be claimed once and only for
     PAIRING_CODE_LIFETIME_SECONDS, and it is withdrawn when another is
     issued or when MAX_FAILED_CLAIMS wrong codes have been tried
     against it. The QR code carries it after "#pair=", in the URL's
     fragment, which a browser never sends to a server, so it cannot
     reach an access log.
  2. The phone trades the code for a token of its own (POST
     /api/pair/claim, no admin token). The token is in that one answer
     and nowhere else: this module keeps only its SHA-256. It opens the
     photo-upload routes and nothing else (api.py says which), and
     revoking it touches neither the admin token nor any other phone.

The phones are kept in a JSON file beside the agent's config.json
(path_beside_config), and never on a Drobo share: a phone's name is
often its owner's name, and the photo library is a share other people
on the network may be able to read.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time

#: The file the phones are kept in, beside a config file named
#: config.json. Other config files get their own; see
#: path_beside_config.
PAIRED_DEVICES_FILENAME = "paired_devices.json"

#: How long a pairing code can be claimed: long enough to pick up the
#: phone and point its camera, short enough that a code seen on a
#: screen is soon worth nothing.
PAIRING_CODE_LIFETIME_SECONDS = 10 * 60

#: Random bytes in a pairing code: 128 bits, 22 characters once
#: base64url-encoded, which keeps the QR code small.
PAIRING_CODE_BYTES = 16

#: Wrong codes tried against one pairing code before it is withdrawn.
#: Nobody guesses 128 bits in five tries or in five billion; the limit
#: is there so that a run of wrong claims ends the code instead of
#: going on unnoticed.
MAX_FAILED_CLAIMS = 5

#: Random bytes in a phone's token: 256 bits.
DEVICE_TOKEN_BYTES = 32

#: Random bytes in the id a phone is listed and revoked by. Not a
#: secret: it only has to be unique among one agent's phones.
DEVICE_ID_BYTES = 8

#: The longest phone name kept. The list only has to say which phone
#: is which.
MAX_DEVICE_NAME_CHARACTERS = 64

#: A phone's last_used_at is saved at most this often, so a burst of
#: chunk uploads does not rewrite the file once per chunk.
LAST_USED_SAVE_INTERVAL_SECONDS = 60

#: What each paired phone is listed with, by GET /api/photos/devices.
LISTED_FIELDS = ("device_id", "device_name", "created_at", "last_used_at")

#: What is saved for each phone: the listed fields and the SHA-256 of
#: its token, which is never listed. The token itself is never saved.
SAVED_FIELDS = LISTED_FIELDS + ("token_sha256",)

#: One answer for every refused code -- wrong, expired, used or
#: withdrawn -- so a refusal says nothing about the code that is
#: waiting.
CODE_REFUSED_MESSAGE = (
    "that pairing code is wrong, has expired or has already been used -- "
    "show a new one on the PC and scan that")

_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


class InvalidClaimError(ValueError):
    """
    A claim this module cannot even check: no code, or a device name
    that is not printable text of at most MAX_DEVICE_NAME_CHARACTERS.
    Answered 400. It is not a guess at the code, so it neither counts
    as a wrong code nor uses the code up.
    """


class PairingCodeRefusedError(Exception):
    """
    A claim whose code is not the one waiting: wrong, expired, already
    used, or withdrawn. Answered 401, always with CODE_REFUSED_MESSAGE.
    """


class PairedDevicesNotSavedError(Exception):
    """
    The paired-devices file could not be written. The message says what
    that means for the claim or revocation that needed it.
    """


def path_beside_config(config_path: str) -> str:
    """
    Where the phones paired with the agent that runs on `config_path`
    are kept: the same folder, under a name that follows the config's
    own. config.json's are in paired_devices.json, config.mock.json's
    in paired_devices.mock.json, other.json's in
    paired_devices.other.json.

    One file per config file, because the simulator's config.mock.json
    sits in the same folder as the real config.json: a phone paired
    with the simulator must not be able to upload to the real agent.
    """
    folder, config_name = os.path.split(os.path.abspath(config_path))
    config_stem = os.path.splitext(config_name)[0]
    if config_stem == "config":
        return os.path.join(folder, PAIRED_DEVICES_FILENAME)
    variant = (config_stem[len("config."):]
               if config_stem.startswith("config.") else config_stem)
    return os.path.join(folder, f"paired_devices.{variant}.json")


def _sha256_hex(text: str) -> str:
    """
    The SHA-256 of `text`'s UTF-8 bytes, in hex. surrogatepass, because
    a JSON body can carry a lone surrogate, which plain UTF-8 refuses
    to encode: text like that can never match, and has to be refused
    like any other wrong text rather than raise.
    """
    return hashlib.sha256(
        text.encode("utf-8", "surrogatepass")).hexdigest()


def _checked_device_name(device_name) -> str:
    """
    The phone's name as it is kept: trimmed, and printable text of 1
    to MAX_DEVICE_NAME_CHARACTERS characters, or InvalidClaimError.

    Refused rather than cut or cleaned, so the list never shows a name
    the phone did not send. Printable, because the list is what the PC
    revokes phones by: a newline or a right-to-left override in a name
    could make one phone read as another there.
    """
    if not isinstance(device_name, str) or not device_name.strip():
        raise InvalidClaimError(
            "device_name is required: a name for this phone, so the PC "
            "can tell it from any other")
    trimmed_name = device_name.strip()
    if not trimmed_name.isprintable():
        raise InvalidClaimError("device_name must be printable text")
    if len(trimmed_name) > MAX_DEVICE_NAME_CHARACTERS:
        raise InvalidClaimError(
            f"device_name is longer than {MAX_DEVICE_NAME_CHARACTERS} "
            f"characters")
    return trimmed_name


def _checked_code_digest(pairing_code) -> str:
    """
    The SHA-256 of the code a claim sent, or InvalidClaimError when it
    sent none. Digests are what get compared, so the comparison takes
    the same time whatever was sent and however long it was.
    """
    if not isinstance(pairing_code, str) or not pairing_code:
        raise InvalidClaimError(
            "code is required: the pairing code from the PC's QR code")
    return _sha256_hex(pairing_code)


def _is_device_record(record) -> bool:
    """Whether a saved entry has every field, each of its kind."""
    if not isinstance(record, dict):
        return False
    last_used_at = record.get("last_used_at")
    return (isinstance(record.get("device_id"), str)
            and bool(record["device_id"])
            and isinstance(record.get("device_name"), str)
            and isinstance(record.get("created_at"), (int, float))
            and (last_used_at is None
                 or isinstance(last_used_at, (int, float)))
            and isinstance(record.get("token_sha256"), str)
            and _SHA256_HEX_RE.fullmatch(record["token_sha256"]) is not None)


def _failure_reason(exc: Exception) -> str:
    """
    What went wrong with the file, without its path. An OSError's own
    text names the full path, and in an installed copy that is inside
    the user's profile folder: this reaches the console a tester may
    paste into a public issue, and answers the PC's app may show.
    """
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


class PairedDevices:
    """
    The phones paired with this agent, and the pairing code waiting to
    be claimed, if one is.

    Every method takes the lock: the API calls in from its request
    threads, and a claim, a revocation and a phone's uploads can all
    arrive at once. Built with no path, as build_http_server does for
    the test suites that never pair a phone, it keeps the phones in
    memory only, so a test can never write into a real agent's folder.
    """

    def __init__(self, path: str | None = None, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        # device_id -> the saved record, SAVED_FIELDS.
        self._devices: dict[str, dict] = {}
        # The waiting code itself is kept, in memory only and only
        # while it can be claimed, so /api/pair.svg can draw the code
        # /api/pair has just handed out instead of withdrawing it.
        # Claims are checked against its digest.
        self._waiting_code: str | None = None
        self._waiting_code_sha256 = ""
        self._waiting_code_expires_at = 0.0
        self._wrong_claim_count = 0
        self._load()

    # -- the pairing code ----------------------------------------------

    def issue_code(self) -> dict:
        """
        A new pairing code, as {"pairing_code", "pairing_expires_at"}
        (unix seconds). An earlier code nobody claimed is withdrawn, and
        the count of wrong claims starts again from zero.
        """
        with self._lock:
            return self._issue_code_locked()

    def waiting_code(self) -> dict:
        """
        The code waiting to be claimed, in issue_code()'s shape, or a
        new one when none is waiting.
        """
        with self._lock:
            if self._code_is_claimable_locked():
                return self._code_payload_locked()
            return self._issue_code_locked()

    def _issue_code_locked(self) -> dict:
        """issue_code(), for a caller that holds the lock."""
        self._waiting_code = secrets.token_urlsafe(PAIRING_CODE_BYTES)
        self._waiting_code_sha256 = _sha256_hex(self._waiting_code)
        self._waiting_code_expires_at = (self._clock()
                                         + PAIRING_CODE_LIFETIME_SECONDS)
        self._wrong_claim_count = 0
        return self._code_payload_locked()

    def _code_payload_locked(self) -> dict:
        """The waiting code and its expiry. Caller holds the lock."""
        return {"pairing_code": self._waiting_code,
                "pairing_expires_at": int(self._waiting_code_expires_at)}

    def _code_is_claimable_locked(self) -> bool:
        """Whether a code is waiting, still in time. Caller locks."""
        return (self._waiting_code is not None
                and self._clock() < self._waiting_code_expires_at)

    def _withdraw_code_locked(self) -> None:
        """No code is waiting any more. Caller holds the lock."""
        self._waiting_code = None
        self._waiting_code_sha256 = ""
        self._wrong_claim_count = 0

    def _check_code_locked(self, supplied_sha256: str) -> None:
        """
        Return when `supplied_sha256` is the digest of the code that is
        waiting; otherwise raise PairingCodeRefusedError. A wrong code
        counts against the waiting one, and the MAX_FAILED_CLAIMS-th
        withdraws it. Caller holds the lock.
        """
        if not self._code_is_claimable_locked():
            self._withdraw_code_locked()
            raise PairingCodeRefusedError(CODE_REFUSED_MESSAGE)
        if secrets.compare_digest(supplied_sha256,
                                  self._waiting_code_sha256):
            return
        self._wrong_claim_count += 1
        if self._wrong_claim_count >= MAX_FAILED_CLAIMS:
            self._withdraw_code_locked()
            print(f"[pair] the pairing code was withdrawn after "
                  f"{MAX_FAILED_CLAIMS} wrong codes were tried against it",
                  flush=True)
        raise PairingCodeRefusedError(CODE_REFUSED_MESSAGE)

    # -- pairing and revoking a phone ----------------------------------

    def claim(self, pairing_code, device_name) -> dict:
        """
        Pair a phone: check its name and the code, keep the phone, and
        return {"device_id", "device_token"} -- the one time its token
        exists anywhere but on the phone.

        The name is checked first, so a name the phone has to fix does
        not count as a wrong code. The phone is saved before the code
        is used up, so no phone is handed a token that a restart would
        forget; if the save fails, the code can still be claimed.
        """
        trimmed_name = _checked_device_name(device_name)
        supplied_sha256 = _checked_code_digest(pairing_code)
        with self._lock:
            self._check_code_locked(supplied_sha256)
            record, device_token = self._new_device_record(trimmed_name)
            self._devices[record["device_id"]] = record
            try:
                self._save_locked()
            except OSError as exc:
                del self._devices[record["device_id"]]
                raise PairedDevicesNotSavedError(
                    f"the phone was not paired: the list of paired phones "
                    f"could not be saved ({_failure_reason(exc)})") from exc
            self._withdraw_code_locked()
        return {"device_id": record["device_id"],
                "device_token": device_token}

    def _new_device_record(self, device_name: str) -> tuple[dict, str]:
        """
        A new phone's saved record, and its token. The record holds the
        token's SHA-256, never the token.
        """
        device_token = secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
        record = {"device_id": secrets.token_hex(DEVICE_ID_BYTES),
                  "device_name": device_name,
                  "created_at": int(self._clock()),
                  "last_used_at": None,
                  "token_sha256": _sha256_hex(device_token)}
        return record, device_token

    def revoke(self, device_id: str) -> bool:
        """
        Unpair one phone; False when no phone has that id. Its token is
        refused from this call on.

        Removed before the save, not after it, so a file that cannot be
        written cannot keep a revoked phone working. A failed save does
        mean a restart before the next good one would bring the phone
        back, and PairedDevicesNotSavedError says so.
        """
        with self._lock:
            if self._devices.pop(device_id, None) is None:
                return False
            try:
                self._save_locked()
            except OSError as exc:
                raise PairedDevicesNotSavedError(
                    f"the phone's token is refused from now on, but the "
                    f"list of paired phones could not be saved "
                    f"({_failure_reason(exc)}), so restarting the agent "
                    f"before it is saved would let that phone back in"
                ) from exc
        return True

    def list_devices(self) -> list[dict]:
        """
        Every paired phone, oldest first, with LISTED_FIELDS only:
        never its token's digest.
        """
        with self._lock:
            records = sorted(self._devices.values(),
                             key=lambda record: record["created_at"])
            return [{field: record[field] for field in LISTED_FIELDS}
                    for record in records]

    # -- a phone's token -----------------------------------------------

    def device_for_token(self, device_token: str) -> str | None:
        """
        The id of the paired phone `device_token` belongs to, or None.
        A match counts as the phone being used: its last_used_at moves,
        and is saved at most every LAST_USED_SAVE_INTERVAL_SECONDS.
        """
        if not device_token:
            return None
        supplied_sha256 = _sha256_hex(device_token)
        with self._lock:
            record = self._record_for_digest_locked(supplied_sha256)
            if record is None:
                return None
            self._note_use_locked(record)
            return record["device_id"]

    def _record_for_digest_locked(self, supplied_sha256: str) -> dict | None:
        """
        The phone whose token has this SHA-256, or None. Compared with
        secrets.compare_digest against the digests, which are all this
        agent keeps of any token. Caller holds the lock.
        """
        for record in self._devices.values():
            if secrets.compare_digest(supplied_sha256,
                                      record["token_sha256"]):
                return record
        return None

    def _note_use_locked(self, record: dict) -> None:
        """
        Move a phone's last_used_at to now, unless it moved less than
        LAST_USED_SAVE_INTERVAL_SECONDS ago. Caller holds the lock.

        A failed save is reported and the request carries on: when a
        phone was last seen matters less than the photo it is sending,
        and the next save writes it anyway.
        """
        now_seconds = int(self._clock())
        last_used_at = record["last_used_at"]
        if (last_used_at is not None and now_seconds - last_used_at
                < LAST_USED_SAVE_INTERVAL_SECONDS):
            return
        record["last_used_at"] = now_seconds
        try:
            self._save_locked()
        except OSError as exc:
            print(f"[pair] could not save when a phone was last seen: "
                  f"{_failure_reason(exc)}", flush=True)

    # -- the file ------------------------------------------------------

    def _load(self) -> None:
        """
        Read the saved phones. No file means no phone is paired yet. A
        file that cannot be read is reported and read as no phones, so
        a damaged file can lock phones out but never let one in; the
        next phone paired replaces it.
        """
        if self.path is None or not os.path.exists(self.path):
            return
        try:
            saved_records = self._read_saved_records()
        except (OSError, ValueError) as exc:
            print(f"[pair] {os.path.basename(self.path)} could not be "
                  f"read ({_failure_reason(exc)}), so no phone is paired "
                  f"until it is fixed; pairing a new phone replaces it",
                  flush=True)
            return
        for record in saved_records:
            if not _is_device_record(record):
                continue
            self._devices[record["device_id"]] = {
                field: record[field] for field in SAVED_FIELDS}

    def _read_saved_records(self) -> list:
        """
        The saved file's list of phones. Raises OSError, or ValueError
        for a file that is not JSON or not the shape _save_locked
        writes.
        """
        with open(self.path, encoding="utf-8") as devices_file:
            saved = json.load(devices_file)
        saved_records = (saved.get("devices")
                         if isinstance(saved, dict) else None)
        if not isinstance(saved_records, list):
            raise ValueError("it holds no list of devices")
        return saved_records

    def _save_locked(self) -> None:
        """
        Write every phone to self.path: a temporary file first, then
        os.replace, so a crash part-way leaves the last good list rather
        than half of one. Raises OSError. Caller holds the lock.
        """
        if self.path is None:
            return
        temporary_path = self.path + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as devices_file:
            json.dump({"devices": list(self._devices.values())},
                      devices_file, indent=2)
        os.replace(temporary_path, self.path)
