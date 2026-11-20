"""
Configuration loading.

One JSON file, sensible defaults for everything, and an access token
that gets generated automatically the first time the agent starts.
"""

from __future__ import annotations

import copy
import json
import os
import secrets

DEFAULTS: dict = {
    "agent": {
        # 127.0.0.1 means "this PC only". Change to 0.0.0.0 when you
        # want the phone to reach it -- but only ever on your own LAN,
        # never forwarded from the internet.
        "bind": "127.0.0.1",
        "port": 7420,
        # Generated on first run if left empty. Every request must send
        # it as the X-Agent-Token header.
        "token": "",
        "poll_seconds": 10,
        "history_length": 8640,  # ~24h at 10s
    },
    "drobo": {
        # The REAL driver, by default, with automatic discovery. This
        # said "mock" long after Drobo5NDriver was working -- a leftover
        # from when the protocol was still unmapped -- which meant a
        # fresh install's first run monitored a SIMULATED healthy Drobo
        # while claiming to watch the user's array. For storage-health
        # software, showing fake good news by default is the one
        # first-run outcome that must never ship. The simulator is still
        # one edit away ("driver": "mock"), and the demo launcher uses
        # its own config.mock.json.
        "driver": "drobo5n",
        "host": "auto",
        "username": "",
        "password": "",
    },
    "updates": {
        # DORMANT BY DEFAULT, and that is the shipping state.
        #
        # The machinery is built (see updates.py) so it exists before it
        # is needed rather than being assembled in a hurry on release
        # day. With enabled false it opens no sockets and contacts
        # nothing at all -- not a default endpoint that happens to 404,
        # but no network activity whatsoever. There is a test that
        # boobytraps urllib to prove it.
        #
        # To switch on later: set enabled true and point manifest_url at
        # an https:// JSON file carrying {"version", "download_url",
        # "sha256"}. The agent will only ever TELL you an update exists;
        # downloading and installing stays a human decision.
        "enabled": False,
        "manifest_url": "",
        "channel": "stable",
        # Seconds between checks, once enabled. Daily -- this is a NAS
        # dashboard, not a browser.
        "check_seconds": 86400,
    },
    "alerts": {
        "temperature_warning_c": 50.0,
        "temperature_critical_c": 60.0,
        "capacity_warning_fraction": 0.85,
        "capacity_critical_fraction": 0.95,
        # Don't re-send the same alert more often than this.
        "resend_seconds": 3600,
    },
    "notify": {
        # console | ntfy
        "backends": ["console"],
        "ntfy": {
            "server": "https://ntfy.sh",
            "topic": "",
        },
    },
    "photos": {
        "enabled": True,
        # Where uploaded photos land. Point this at a mapped drive or
        # UNC path on the Drobo, e.g. "Z:\\Photos" or
        # "\\\\drobo\\Photos".
        "target_dir": "",
        # Files are filed as target_dir/YYYY/YYYY-MM/name
        "organize_by_date": True,
        # The largest file sent in one request, and the largest sent in
        # resumable chunks (a long 4K video). max_file_gb was read with
        # this same 32 but missing here until 2026-11-02, so it never
        # appeared in a generated config.json.
        "max_upload_mb": 512,
        "max_file_gb": 32,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """
    `base` with `override` laid over it, recursively, as a new dict.

    Deep rather than dict.update, so a config.json that sets only one
    key of a block (say "alerts": {"resend_seconds": 60}) keeps the
    defaults for every other key in that block instead of losing them.
    `base` is copied first, so DEFAULTS is never modified.
    """
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ConfigFileError(ValueError):
    """The config file is there but cannot be read as JSON text."""


def _read_config_file(path: str) -> dict:
    """
    The config file's own contents, or {} when there is no file yet.

    Raises SystemExit, with where and what, for a file that is not
    valid JSON or not UTF-8: it is a file the user probably just
    edited by hand, and a stack trace from json's internals would not
    tell them which.
    """
    try:
        return _parse_config_file(path)
    except ConfigFileError as exc:
        raise SystemExit(str(exc)) from None


def _parse_config_file(path: str) -> dict:
    """
    What _read_config_file reads, raising ConfigFileError rather than
    SystemExit, so a caller inside the running agent (the select
    route, which saves the Drobo it was pointed at) can answer with the
    reason instead of losing a server thread to SystemExit.
    """
    if not os.path.exists(path):
        return {}
    # utf-8-SIG, not utf-8: it reads plain UTF-8 unchanged and also
    # tolerates a leading byte-order mark. Windows Notepad and
    # PowerShell's `Set-Content -Encoding utf8` both write that mark,
    # so anyone editing this file by hand on Windows produces a config
    # the plain reader rejected with a raw JSONDecodeError traceback and
    # no hint of the cause. Hit for real on 2026-08-07 by exactly that
    # route.
    with open(path, "r", encoding="utf-8-sig") as config_file:
        try:
            return json.load(config_file)
        except json.JSONDecodeError as exc:
            # A config file the user probably just edited. Say where and
            # what, rather than unwinding a stack from json's internals.
            raise ConfigFileError(
                f"[config] {path} is not valid JSON: {exc.msg} "
                f"(line {exc.lineno}, column {exc.colno}).\n"
                f"[config] Nothing was changed. Fix the file, "
                f"or delete it to start again from defaults."
            ) from None
        except UnicodeDecodeError:
            # Not UTF-8 at all -- usually UTF-16, which Windows
            # PowerShell 5.1's `>` and Out-File write by default, as
            # does Notepad's "UTF-16 LE". Said plainly for the same
            # reason as above, and so the update check stops (exit 3)
            # instead of reading an empty config.
            raise ConfigFileError(
                f"[config] {path} is not UTF-8 text. Save it as UTF-8 "
                f"and try again.\n"
                f"[config] Nothing was changed."
            ) from None


def load(path: str) -> dict:
    """Read config from disk,
    filling in anything missing from DEFAULTS.

    Also the first-run step: a config with no access token gets one,
    written straight back to `path`, so the token is stable across
    restarts and the apps only have to be given it once.
    """
    on_disk_config = _read_config_file(path)
    merged_config = _deep_merge(DEFAULTS, on_disk_config)

    # Generate an access token on first run and write it back, so the
    # value is stable across restarts and the apps can be configured
    # once.
    if not merged_config["agent"]["token"]:
        merged_config["agent"]["token"] = secrets.token_urlsafe(24)
        save(path, merged_config)
        print(f"[config] generated a new access token and saved it to {path}")

    return merged_config


def save(path: str, config_data: dict) -> None:
    """
    Write the config to `path`, creating its folder if need be.

    Written to a temporary file and then moved into place with
    os.replace, which is atomic: a crash or a full disk mid-write
    leaves the old config intact rather than a half-written one, and
    this file holds the access token every app depends on.
    """
    parent_dir = os.path.dirname(os.path.abspath(path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as config_file:
        json.dump(config_data, config_file, indent=2)
    os.replace(temporary_path, path)


def save_drobo_settings(path: str, changes: dict) -> None:
    """
    Write `changes` into the "drobo" block of the config file at
    `path`, leaving every other key as the file has it.

    The file is read again from disk rather than rewritten from the
    agent's copy in memory: it is a file people edit by hand while the
    agent runs, and DEFAULTS would otherwise be written into it too.
    A file that has gone missing is refused rather than recreated,
    because a recreated file would carry no access token and the next
    start would hand every app a new one. Raises ConfigFileError, or
    OSError when the file cannot be written; either way the file on
    disk is as it was (see save()).
    """
    if not os.path.exists(path):
        raise ConfigFileError(
            f"{display_path(path)} is no longer there, so the choice "
            f"could not be kept. Start the agent again to recreate it.")
    on_disk_config = _parse_config_file(path)
    if not isinstance(on_disk_config, dict):
        raise ConfigFileError(
            f"{display_path(path)} does not hold a JSON object, so the "
            f"choice could not be kept.")
    drobo_block = on_disk_config.get("drobo")
    if not isinstance(drobo_block, dict):
        drobo_block = {}
    drobo_block.update(changes)
    on_disk_config["drobo"] = drobo_block
    save(path, on_disk_config)


def display_path(path: str) -> str:
    """
    `path` made absolute and safe to show, with the user's own folder
    written as %LOCALAPPDATA%, %USERPROFILE% or ~.

    The agent names its config file in words other people read: the
    "cannot reach" alert that testers paste into public issues, and
    the browser's token page, which anyone on the LAN can load without
    a token. A Windows profile path carries the account name, so it is
    written the way an installed copy's documents already write it
    (%LOCALAPPDATA%\\DroboDashboardReborn\\config.json).
    """
    absolute_path = os.path.abspath(path)
    for variable in ("LOCALAPPDATA", "USERPROFILE"):
        shortened = _with_folder_replaced(
            absolute_path, os.environ.get(variable, ""), f"%{variable}%")
        if shortened:
            return shortened
    shortened = _with_folder_replaced(
        absolute_path, os.path.expanduser("~"), "~")
    return shortened or absolute_path


def _with_folder_replaced(absolute_path: str, folder: str,
                          placeholder: str) -> str:
    """
    `absolute_path` with its leading `folder` written as
    `placeholder`, or "" when the path is not inside that folder. The
    comparison ignores case where the file system does (Windows).
    """
    if not folder or folder == os.sep:
        return ""
    folder = os.path.abspath(folder)
    normalized_path = os.path.normcase(absolute_path)
    normalized_folder = os.path.normcase(folder)
    if normalized_path == normalized_folder:
        return placeholder
    if not normalized_path.startswith(normalized_folder.rstrip(os.sep)
                                      + os.sep):
        return ""
    return placeholder + absolute_path[len(folder.rstrip(os.sep)):]
