"""Cross-platform persistent-storage locations for ShareG."""

from __future__ import annotations

import os
import sys


def app_data_dir() -> str:
    """Per-user writable config dir: %APPDATA%/ShareG, ~/.config/ShareG, or app files dir on Android."""
    override = os.environ.get("SHAREG_DATA_DIR")
    if override:
        path = override
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "ShareG")
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ShareG")
    elif sys.platform.startswith("linux"):
        if os.path.isdir("/data/data"):  # Android (Termux-style P4A layout)
            base = os.environ.get("HOME") or "/data/data"
            path = os.path.join(base, ".shareg")
        else:
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
    """Where received files land: standard Downloads/ShareG when writable."""
    override = os.environ.get("SHAREG_DOWNLOAD_DIR")
    if override:
        path = override
    else:
        base = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(base):
            path = os.path.join(base, "ShareG")
        else:
            path = os.path.join(app_data_dir(), "received")
    os.makedirs(path, exist_ok=True)
    return path
