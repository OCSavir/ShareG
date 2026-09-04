"""Cross-platform persistent-storage locations for ShareG."""

from __future__ import annotations

import os
import sys


def _is_android() -> bool:
    """True when running inside an Android app process.

    Android reports ``sys.platform == "linux"``, so detect it by the OS-level
    environment markers every Android app process carries (ANDROID_ROOT /
    ANDROID_DATA are set by the OS itself; ANDROID_ARGUMENT / ANDROID_PRIVATE
    by python-for-android). None of these exist on desktop Linux, and
    ``FLET_APP_STORAGE_DATA`` alone is NOT a marker because desktop
    ``flet run`` dev mode sets it too.
    """
    return sys.platform.startswith("linux") and bool(
        os.environ.get("ANDROID_ROOT")
        or os.environ.get("ANDROID_DATA")
        or os.environ.get("ANDROID_ARGUMENT")
        or os.environ.get("ANDROID_PRIVATE")
    )


def app_data_dir() -> str:
    """Per-user writable config dir: %APPDATA%/ShareG, ~/.config/ShareG, or the
    Flet/Android app-private storage dir on Android."""
    override = os.environ.get("SHAREG_DATA_DIR")
    if override:
        path = override
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "ShareG")
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ShareG")
    elif _is_android():
        # Android sandboxes each app: nothing under /data/data is writable
        # except the app's own private dir, which the Flet runtime exposes as
        # FLET_APP_STORAGE_DATA (the packaged runtime also makes it the
        # process cwd). Create the ShareG subdirectory inside it.
        base = (
            os.environ.get("FLET_APP_STORAGE_DATA")     # Flet app runtime
            or os.environ.get("ANDROID_PRIVATE")        # python-for-android
            or os.environ.get("HOME")                   # P4A sets HOME to the app files dir
        )
        if not base:
            # No known-writable base: refuse instead of attempting an
            # unwritable path (e.g. anything under /data/data).
            raise RuntimeError(
                "ShareG on Android: no writable app-private storage found "
                "(FLET_APP_STORAGE_DATA / ANDROID_PRIVATE / HOME are unset); "
                "the app must be launched through the Flet Android runtime"
            )
        path = os.path.join(base, "ShareG")
    elif sys.platform.startswith("linux"):
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        path = os.path.join(base, "ShareG")
    else:
        path = os.path.join(os.path.expanduser("~"), ".shareg")
    os.makedirs(path, exist_ok=True)
    return path


def identity_store_path() -> str:
    return os.path.join(app_data_dir(), "shareg_identity.json")


def pairing_store_path() -> str:
    return os.path.join(app_data_dir(), "shareg_paired_devices.json")


def downloads_dir() -> str:
    """Where received files land: standard Downloads/ShareG when writable.

    On Android the Linux-style ~/Downloads does not exist in the app sandbox;
    received files go to a ShareG subfolder of the app-private storage.
    """
    override = os.environ.get("SHAREG_DOWNLOAD_DIR")
    if override:
        path = override
    elif _is_android():
        path = os.path.join(app_data_dir(), "received")
    else:
        base = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(base):
            path = os.path.join(base, "ShareG")
        else:
            path = os.path.join(app_data_dir(), "received")
    os.makedirs(path, exist_ok=True)
    return path
