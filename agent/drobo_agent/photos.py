"""
DroboPix-style photo backup -- the receiving half.

The phone's job is to notice new photos and offer them. This module's
job is to decide what it already has, file what it doesn't, and never
lose or duplicate anything. It talks to the Drobo purely as a file share
(SMB), so it works today with zero protocol reverse engineering.

The important trick is the /api/photos/have endpoint: before uploading,
the phone sends the hashes of the photos it's holding and gets back the
ones the agent has never seen. That means an interrupted backup resumes
cheaply, and re-installing the app doesn't re-upload your entire camera
roll.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

# Anything not on this list is refused. Keep it tight -- this endpoint
# accepts files from a phone, so it should not become a general-purpose
# file drop.
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".tif",
    ".tiff",
    ".dng", ".raw", ".cr2", ".nef", ".arw",
    ".mov", ".mp4", ".m4v", ".avi", ".hevc",
}


_UNSAFE_FILENAME_CHARACTERS_RE = re.compile(r"[^A-Za-z0-9._-]+")
_INDEX_FILENAME = ".drobo-agent-photo-index.json"
_UPLOAD_SESSION_DIRNAME = ".drobo-agent-uploads"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Longest filename safe_filename() keeps. Well inside the 255 most
#: filesystems allow, leaving room for a "~N" collision suffix and the
#: ".part" of an upload in progress.
MAX_FILENAME_CHARACTERS = 180

#: The index is rewritten at most this often during a burst of uploads
#: (see _save_index); flush() and the force flag write it at once.
INDEX_SAVE_MIN_INTERVAL_SECONDS = 5.0

#: How much of a file is read at a time while hashing it. A video can be
#: gigabytes, so it is hashed in pieces rather than read whole.
HASH_READ_CHUNK_BYTES = 1024 * 1024


class PhotoStoreError(Exception):
    """
    An upload was refused or could not be stored. The message is meant
    for the phone: the API sends it back as the reason.
    """


def safe_filename(name: str) -> str:
    """
    Turn whatever the phone sent into a filename that cannot escape the
    target directory. Strips any path components, then anything exotic.
    """
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = _UNSAFE_FILENAME_CHARACTERS_RE.sub("_", name)
    name = name.lstrip(".") or "photo"
    return name[:MAX_FILENAME_CHARACTERS]


def _accepted_filename(filename: str) -> str:
    """
    safe_filename(filename), or PhotoStoreError when its extension is
    not in ALLOWED_EXTENSIONS.
    """
    name = safe_filename(filename)
    extension = os.path.splitext(name)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise PhotoStoreError(
            f"file type {extension or '(none)'} is not accepted")
    return name


def _free_path(absolute_path: str) -> str:
    """
    `absolute_path`, or the first "stem~N.ext" beside it that does not
    exist yet.

    Two different photos can share a filename (IMG_0001.JPG is not
    unique across devices), so a new file gets a free name rather than
    overwriting one that is already backed up.
    """
    stem, suffix = os.path.splitext(absolute_path)
    collision_number = 0
    while os.path.exists(absolute_path):
        collision_number += 1
        absolute_path = f"{stem}~{collision_number}{suffix}"
    return absolute_path


def _set_capture_time(absolute_path: str, taken_at: float) -> None:
    """
    Give a stored file its capture time as its modified time, so a file
    browser sorts the library by when photos were taken. Best effort: a
    share that refuses it still holds the photo.
    """
    try:
        os.utime(absolute_path, (taken_at, taken_at))
    except OSError:
        pass


def _read_session_metadata(metadata_path: str) -> dict:
    """An upload session's saved filename, total size, capture time."""
    return json.load(open(metadata_path, encoding="utf-8"))


class PhotoStore:
    """
    The photo library on the Drobo, and the index of what is in it.

    The index maps each file's sha256 to its path in the library, which
    is what lets the phone ask "which of these do you not have?" without
    sending a single photo. Every method that reads or changes the index
    takes the lock, because uploads arrive on the API's request threads.
    """

    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled", True))
        self.target_dir = config.get("target_dir") or ""
        self.organize_by_date = bool(config.get("organize_by_date", True))
        # Single-shot POST limit. Anything larger must use the chunked
        # session API -- see begin()/append()/finish() below.
        self.max_single_upload_bytes = (
            int(config.get("max_upload_mb", 512)) * 1024 * 1024)
        # Ceiling for a chunked upload.
        # iPhone 4K video runs to several GB.
        self.max_chunked_file_bytes = (
            int(config.get("max_file_gb", 32)) * 1024 * 1024 * 1024)

        self._lock = threading.Lock()
        self._index: dict[str, str] = {}  # sha256 -> relative path
        self._index_dirty = False
        self._last_index_save_at = 0.0
        self._loaded = False

    # -- index ---------------------------------------------------------

    @property
    def index_path(self) -> str:
        """
        The index file, kept inside the library itself so it travels
        with the photos it describes.
        """
        return os.path.join(self.target_dir, _INDEX_FILENAME)

    def load(self) -> None:
        """
        Get ready to receive photos: check the target folder, read the
        index, and rebuild it from disk if it is missing but the folder
        is not empty.

        A target folder that is not configured or cannot be created
        turns photo backup off (and says so) instead of stopping the
        agent: the Drobo's health matters more than its photo backup.
        """
        if not self.enabled:
            return
        if not self.target_dir:
            print("[photos] no target_dir configured -- photo backup is off",
                  flush=True)
            self.enabled = False
            return
        try:
            os.makedirs(self.target_dir, exist_ok=True)
        except OSError as exc:
            print(f"[photos] cannot use "
                  f"target_dir {self.target_dir!r}: {exc}", flush=True)
            self.enabled = False
            return
        had_index = self._read_index_file()
        self._loaded = True

        # THE INDEX IS A CACHE, NOT THE TRUTH.
        # The files on the Drobo are.
        #
        # Without this, losing the index file means the agent believes
        # it has nothing, `missing()` says "send me everything", and the
        # phone re-uploads an entire camera roll over Wi-Fi -- hours and
        # gigabytes to re-copy files that are already sitting right
        # there.
        #
        # So when the index is absent or unreadable and the folder is
        # NOT empty, rebuild it from what is actually on disk before
        # anyone asks what we have. Only in that case: hashing a large
        # library is real I/O over SMB and has no business running on
        # every ordinary startup.
        if not had_index and self._has_any_files():
            print("[photos] no usable index but the folder has files -- "
                  "rebuilding it from disk rather than asking for "
                  "everything again",
                  flush=True)
            summary = self.rescan()
            print(f"[photos] rebuilt index from {summary['found']} file(s)",
                  flush=True)

        print(f"[photos] {len(self._index)} file(s) already backed up to "
              f"{self.target_dir}", flush=True)

    def _read_index_file(self) -> bool:
        """
        Load the saved index into self._index. Returns False when there
        is no usable one -- absent, or unreadable (reported, and the
        index left empty) -- so load() knows to consider a rescan.
        """
        if not os.path.exists(self.index_path):
            return False
        try:
            with open(self.index_path, "r",
                      encoding="utf-8") as index_file:
                self._index = json.load(index_file)
        except (OSError, ValueError) as exc:
            print(f"[photos] index unreadable: {exc}", flush=True)
            self._index = {}
            return False
        return True

    def _has_any_files(self) -> bool:
        """Cheap check for "is there anything here at all",
        without hashing.
        """
        for _root, _dirs, names in self._walk():
            if names:
                return True
        return False

    def _walk(self):
        """
        Walk the backup folder, skipping our own bookkeeping.

        Dot-prefixed entries are ours (the index, and
        .drobo-agent-uploads full of half-finished .part files). Hashing
        an in-progress upload would index a truncated file under the
        hash of its complete self -- which is exactly the kind of quiet
        corruption this whole module exists to avoid.
        """
        for root, dirs, names in os.walk(self.target_dir):
            dirs[:] = [directory_name for directory_name in dirs
                       if not directory_name.startswith(".")]
            photo_names = [file_name for file_name in names
                           if not file_name.startswith(".")
                           and os.path.splitext(file_name)[1].lower()
                           in ALLOWED_EXTENSIONS]
            yield root, dirs, photo_names

    def _library_files(self):
        """The full path of every photo _walk() finds, one at a time."""
        for root, _dirs, names in self._walk():
            for name in names:
                yield os.path.join(root, name)

    def rescan(self) -> dict:
        """
        Rebuild the index from the files actually present, and report
        the drift.

        Two failure modes this fixes, both of which end in the phone and
        the agent disagreeing about what is backed up:

          - **Index lost or corrupt.** Without a rescan the agent asks
            for the whole camera roll again.
          - **A file deleted off the Drobo.** The index still lists its
            hash, so `missing()` answers "already have it" and the
            phone never re-sends. The photo is gone and the system
            says it is safe -- the worse of the two, because nothing
            ever surfaces it.

        Returns {"found", "added", "dropped", "unchanged"} so a caller
        can say what actually changed rather than just "done".

        Two things it keeps, both lost until 2026-11-02: an indexed file
        it could not read (counted unchanged, since "I could not open
        it" is not evidence of absence), and an upload that finished
        while it was hashing (the rebuilt index used to replace the
        live one wholesale).
        """
        with self._lock:
            index_before_walk = dict(self._index)
        rebuilt, found_file_count, unreadable_paths = self._hash_library()
        with self._lock:
            return self._merge_rescan(index_before_walk, rebuilt,
                                      found_file_count, unreadable_paths)

    def _hash_library(self) -> tuple[dict[str, str], int, set[str]]:
        """
        (sha256 -> relative path for every readable photo, how many
        were read, the relative paths that could not be read).
        Runs without the lock, so uploads carry on while it hashes.
        """
        rebuilt: dict[str, str] = {}
        found_file_count = 0
        unreadable_paths: set[str] = set()
        for full_path in self._library_files():
            relative_path = os.path.relpath(
                full_path, self.target_dir).replace("\\", "/")
            try:
                digest = self._hash_file(full_path)
            except OSError:
                # Unreadable right now (SMB hiccup, a lock). Skip it
                # rather than treat it as absent -- see _merge_rescan,
                # which keeps its index entry.
                unreadable_paths.add(relative_path)
                continue
            found_file_count += 1
            rebuilt[digest] = relative_path
        return rebuilt, found_file_count, unreadable_paths

    def _merge_rescan(self, index_before_walk: dict[str, str],
                      rebuilt: dict[str, str], found_file_count: int,
                      unreadable_paths: set[str]) -> dict:
        """
        Make the rebuilt index the live one, keeping what the walk could
        not vouch for, save it, and return rescan()'s summary. Caller
        holds the lock.
        """
        # A hash we already knew whose file is genuinely gone gets
        # dropped; one we simply could not read stays, because "I
        # could not open it" is not evidence of absence.
        kept_unreadable = {
            digest: relative_path
            for digest, relative_path in index_before_walk.items()
            if relative_path in unreadable_paths}
        uploaded_during_walk = {
            digest: relative_path
            for digest, relative_path in self._index.items()
            if digest not in index_before_walk}
        indexed_digests = set(index_before_walk)
        found_digests = set(rebuilt)
        kept_digests = set(kept_unreadable) - found_digests
        self._index = {**rebuilt, **kept_unreadable, **uploaded_during_walk}
        self._index_dirty = True
        self._save_index(force=True)
        return {"found": found_file_count,
                "added": len(found_digests - indexed_digests),
                "dropped": len(indexed_digests - found_digests
                               - kept_digests),
                "unchanged": len(indexed_digests & found_digests)
                + len(kept_digests)}

    @staticmethod
    def _hash_file(path: str) -> str:
        """
        The file's sha256, read HASH_READ_CHUNK_BYTES at a time so a
        multi-gigabyte video never has to fit in memory.
        """
        hasher = hashlib.sha256()
        with open(path, "rb") as photo_file:
            for block in iter(lambda: photo_file.read(HASH_READ_CHUNK_BYTES),
                              b""):
                hasher.update(block)
        return hasher.hexdigest()

    def _save_index(self, force: bool = False) -> None:
        """Throttled so a burst of uploads doesn't rewrite the index
        every time.
        """
        if not self._index_dirty:
            return
        if (not force and time.time() - self._last_index_save_at
                < INDEX_SAVE_MIN_INTERVAL_SECONDS):
            return
        temporary_path = self.index_path + ".tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as index_file:
                json.dump(self._index, index_file)
            os.replace(temporary_path, self.index_path)
            self._index_dirty = False
            self._last_index_save_at = time.time()
        except OSError as exc:
            # A sync-client or SMB lock is transient -- keep the
            # in-memory index and try again on the next upload.
            print(f"[photos] could not write index: {exc}", flush=True)

    def flush(self) -> None:
        """
        Write the index now if it has unsaved changes. _save_index is
        throttled, so the last few uploads of a burst can still be only
        in memory; the agent calls this on the way out.
        """
        with self._lock:
            self._save_index(force=True)

    # -- queries -------------------------------------------------------

    def missing(self, hashes: list[str]) -> list[str]:
        """Given hashes the phone holds,
        return the ones we don't have.
        """
        with self._lock:
            return [digest for digest in hashes
                    if digest and digest not in self._index]

    def stats(self) -> dict:
        """What /api/photos/stats reports: settings and file count."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "target_dir": self.target_dir,
                "files_stored": len(self._index),
                "organize_by_date": self.organize_by_date,
                "max_upload_mb": self.max_single_upload_bytes // (1024 * 1024),
            }

    # -- ingest --------------------------------------------------------

    def _relative_destination(self, filename: str, taken_at: float) -> str:
        """
        Where a photo goes in the library: YYYY/YYYY-MM/name by its
        capture time (local time, as the person who took it would file
        it), or the library's top level when organize_by_date is off.
        """
        if not self.organize_by_date:
            return filename
        local_capture_time = time.localtime(taken_at)
        return os.path.join(
            time.strftime("%Y", local_capture_time),
            time.strftime("%Y-%m", local_capture_time), filename
        )

    # -- chunked, resumable upload -------------------------------------
    #
    # Needed because a 4K video is gigabytes and iOS will suspend a
    # background transfer part-way through. The upload id IS the file's
    # sha256, so resuming needs no bookkeeping on the phone: call
    # begin() again with the same hash and you are told how many bytes
    # already arrived.
    #
    #     begin(name, size, sha256)  -> {"status": "ready",
    #                                    "received": N}
    #                                or {"status": "duplicate"}
    #                                   (already have it)
    #     append(sha256, offset, chunk) -> {"received": N}
    #     finish(sha256)             -> {"status": "stored",
    #                                    "path": ...}

    @property
    def session_dir(self) -> str:
        """
        Where uploads in progress live: inside the library, so finishing
        one is a rename on the same share rather than a copy, and
        dot-prefixed, so _walk() never indexes a half-sent file.
        """
        return os.path.join(self.target_dir, _UPLOAD_SESSION_DIRNAME)

    def _session_paths(self, digest: str) -> tuple[str, str]:
        """
        (partial file, metadata file) for an upload. Named by the
        sha256, which is what lets a resumed upload find its own bytes.
        """
        return (os.path.join(self.session_dir, digest + ".part"),
                os.path.join(self.session_dir, digest + ".json"))

    def _require_ready(self, digest: str) -> None:
        """
        Refuse a session call before load(), or with an upload id that
        is not a sha256 -- the id becomes a filename, so nothing else
        may reach the disk.
        """
        if not self.enabled or not self._loaded:
            raise PhotoStoreError("photo backup is not ready")
        if not _SHA256_RE.match(digest or ""):
            raise PhotoStoreError("upload id must be a lowercase hex sha256")

    def _check_chunked_size(self, total_bytes: int) -> None:
        """Refuse a declared size that is zero or over the file cap."""
        if total_bytes <= 0:
            raise PhotoStoreError("size must be positive")
        if total_bytes > self.max_chunked_file_bytes:
            raise PhotoStoreError(
                f"{total_bytes} bytes is over the "
                f"{self.max_chunked_file_bytes // (1024**3)} GB per-file "
                f"limit")

    def begin(self, filename: str, total_bytes: int, digest: str,
              taken_at: float | None = None) -> dict:
        """
        Start an upload session, or resume one. Returns "duplicate" when
        the file is already backed up, otherwise "ready" with how many
        bytes have already arrived, so the phone sends only the rest.
        """
        self._require_ready(digest)
        self._check_chunked_size(total_bytes)
        name = _accepted_filename(filename)

        with self._lock:
            if digest in self._index:
                return {"status": "duplicate", "sha256": digest,
                        "path": self._index[digest]}

            os.makedirs(self.session_dir, exist_ok=True)
            partial_path, metadata_path = self._session_paths(digest)
            received_bytes = self._resume_offset(partial_path, total_bytes)

            # A resume must not clobber the original capture date. The
            # phone only sends taken_at on the first begin(); if we
            # overwrote it here every interrupted upload would end up
            # filed under today.
            capture_time = taken_at
            if capture_time is None:
                capture_time = self._saved_capture_time(metadata_path)
            with open(metadata_path, "w", encoding="utf-8") as metadata_file:
                json.dump({"filename": name, "total": total_bytes,
                           "taken_at": capture_time or time.time()},
                          metadata_file)
            if not os.path.exists(partial_path):
                open(partial_path, "wb").close()
            return {"status": "ready", "sha256": digest,
                    "received": received_bytes, "total": total_bytes}

    @staticmethod
    def _resume_offset(partial_path: str, total_bytes: int) -> int:
        """
        How many bytes of the upload are already here. A partial file
        longer than the declared size is stale or corrupt, so it is
        deleted and the upload starts over from 0.
        """
        received_bytes = (os.path.getsize(partial_path)
                          if os.path.exists(partial_path) else 0)
        if received_bytes > total_bytes:      # stale or corrupt; start over
            received_bytes = 0
            try:
                os.remove(partial_path)
            except OSError:
                pass
        return received_bytes

    @staticmethod
    def _saved_capture_time(metadata_path: str) -> float | None:
        """
        The capture time an earlier begin() saved for this upload, or
        None when there is no readable one.
        """
        if not os.path.exists(metadata_path):
            return None
        try:
            return json.load(
                open(metadata_path, encoding="utf-8")
            ).get("taken_at")
        except (OSError, ValueError):
            return None

    def append(self, digest: str, offset: int, chunk: bytes) -> dict:
        """
        Add one chunk at `offset`, flushed to disk before replying, so
        "received" never counts bytes a crash could lose. A chunk for
        the wrong offset is not written: the reply says where the upload
        really is, and the phone carries on from there.
        """
        self._require_ready(digest)
        if not chunk:
            raise PhotoStoreError("empty chunk")
        with self._lock:
            partial_path, metadata_path = self._session_paths(digest)
            if not os.path.exists(metadata_path):
                raise PhotoStoreError("no upload session; call begin first")
            session_metadata = _read_session_metadata(metadata_path)
            received_bytes = os.path.getsize(partial_path)
            if offset != received_bytes:
                # Not an error the phone can't recover from -- tell it
                # where we are.
                return {"status": "offset-mismatch",
                        "received": received_bytes,
                        "total": session_metadata["total"]}
            if received_bytes + len(chunk) > session_metadata["total"]:
                raise PhotoStoreError("chunk would exceed the declared size")
            with open(partial_path, "r+b") as part_file:
                part_file.seek(offset)
                part_file.write(chunk)
                part_file.flush()
                os.fsync(part_file.fileno())
            received_bytes += len(chunk)
            return {"status": "ok", "received": received_bytes,
                    "total": session_metadata["total"],
                    "complete": received_bytes >= session_metadata["total"]}

    def session_progress(self, digest: str) -> dict:
        """
        Where an upload stands, without changing anything: "duplicate",
        "none" (no session yet), or "ready" with the bytes received.
        """
        self._require_ready(digest)
        with self._lock:
            if digest in self._index:
                return {"status": "duplicate", "path": self._index[digest]}
            partial_path, metadata_path = self._session_paths(digest)
            if not os.path.exists(metadata_path):
                return {"status": "none", "received": 0}
            session_metadata = _read_session_metadata(metadata_path)
            return {"status": "ready",
                    "received": (os.path.getsize(partial_path)
                                 if os.path.exists(partial_path) else 0),
                    "total": session_metadata["total"]}

    def finish(self, digest: str) -> dict:
        """Verify the assembled file really is what was promised, then
        file it.
        """
        self._require_ready(digest)
        with self._lock:
            if digest in self._index:
                return {"status": "duplicate", "sha256": digest,
                        "path": self._index[digest]}
            partial_path, metadata_path = self._session_paths(digest)
            if (not os.path.exists(metadata_path)
                    or not os.path.exists(partial_path)):
                raise PhotoStoreError("no upload session to finish")
            session_metadata = _read_session_metadata(metadata_path)
            size_bytes = os.path.getsize(partial_path)
            if size_bytes != session_metadata["total"]:
                return {"status": "incomplete", "received": size_bytes,
                        "total": session_metadata["total"]}

            # Never trust the declared hash --
            # recompute before filing anything.
            if self._hash_file(partial_path) != digest:
                os.remove(partial_path)
                os.remove(metadata_path)
                raise PhotoStoreError(
                    "content does not match the declared "
                    "sha256; upload discarded")

            relative_path = self._move_into_library(
                session_metadata["filename"], session_metadata["taken_at"],
                partial_path)
            os.remove(metadata_path)
            self._index[digest] = relative_path
            self._index_dirty = True
            self._save_index(force=True)
            return {"status": "stored", "sha256": digest,
                    "path": relative_path, "bytes": size_bytes}

    def _move_into_library(self, name: str, taken_at: float,
                           source_path: str) -> str:
        """Move a completed upload into the library.
        Caller holds the lock.
        """
        relative_path = self._relative_destination(name, taken_at)
        absolute_path = os.path.join(self.target_dir, relative_path)
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        absolute_path = _free_path(absolute_path)
        os.replace(source_path, absolute_path)
        _set_capture_time(absolute_path, taken_at)
        return os.path.relpath(
            absolute_path, self.target_dir).replace("\\", "/")

    # -- single-shot upload --------------------------------------------

    def _check_single_upload(self, file_bytes: bytes) -> None:
        """Refuse an upload before load(), or one empty or too big."""
        if not self.enabled:
            raise PhotoStoreError("photo backup is disabled")
        if not self._loaded:
            raise PhotoStoreError("photo store not ready")
        if len(file_bytes) > self.max_single_upload_bytes:
            raise PhotoStoreError(
                f"file is {len(file_bytes)} bytes, over the "
                f"{self.max_single_upload_bytes // (1024 * 1024)} MB limit"
            )
        if not file_bytes:
            raise PhotoStoreError("empty file")

    def ingest(
        self, filename: str, file_bytes: bytes, taken_at: float | None = None
    ) -> dict:
        """
        Store a whole file sent in one request, unless the library
        already has it. The hash is worked out here from the bytes
        themselves, so a duplicate is caught however the phone named
        it; with no capture time, now is used.
        """
        self._check_single_upload(file_bytes)
        name = _accepted_filename(filename)

        digest = hashlib.sha256(file_bytes).hexdigest()
        taken_at = taken_at or time.time()

        with self._lock:
            if digest in self._index:
                return {
                    "status": "duplicate",
                    "sha256": digest,
                    "path": self._index[digest],
                }

            relative_path = self._relative_destination(name, taken_at)
            absolute_path = os.path.join(self.target_dir, relative_path)
            os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
            absolute_path = _free_path(absolute_path)
            relative_path = os.path.relpath(absolute_path, self.target_dir)

            self._write_atomically(absolute_path, file_bytes)
            _set_capture_time(absolute_path, taken_at)

            self._index[digest] = relative_path.replace("\\", "/")
            self._index_dirty = True
            self._save_index()

            return {
                "status": "stored",
                "sha256": digest,
                "path": self._index[digest],
                "bytes": len(file_bytes),
            }

    @staticmethod
    def _write_atomically(absolute_path: str, file_bytes: bytes) -> None:
        """
        Write the file beside its final name as ".part", flush it to
        disk, then rename it into place, so the library never holds a
        half-written photo under a real name. On failure the ".part" is
        removed and PhotoStoreError raised.
        """
        temporary_path = absolute_path + ".part"
        try:
            with open(temporary_path, "wb") as part_file:
                part_file.write(file_bytes)
                part_file.flush()
                os.fsync(part_file.fileno())
            os.replace(temporary_path, absolute_path)
        except OSError as exc:
            try:
                os.remove(temporary_path)
            except OSError:
                pass
            raise PhotoStoreError(
                f"could not write to the Drobo: {exc}") from exc
