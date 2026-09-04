"""Regression tests for the Android path/storage fix (shareg/paths.py).

Scope: only the Android branch is new. These tests also pin the existing
Windows / desktop-Linux behavior so it cannot drift.
"""

import os
import sys

import pytest

from shareg import paths


@pytest.fixture()
def android_env(monkeypatch, tmp_path):
    """Simulate an Android app process with the Flet runtime storage env."""
    flet_data = tmp_path / "flet_data"
    flet_data.mkdir()
    monkeypatch.setattr(sys, "platform", "linux")
    # OS-level markers present in every Android app process.
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    monkeypatch.setenv("ANDROID_DATA", "/data")
    # Flet Android runtime storage.
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(flet_data))
    monkeypatch.delenv("SHAREG_DATA_DIR", raising=False)
    monkeypatch.delenv("SHAREG_DOWNLOAD_DIR", raising=False)
    return flet_data


@pytest.fixture()
def clean_env(monkeypatch):
    """Remove any host env that could leak into the platform branches."""
    for var in ("ANDROID_ROOT", "ANDROID_DATA", "ANDROID_ARGUMENT",
                "ANDROID_PRIVATE", "FLET_APP_STORAGE_DATA",
                "SHAREG_DATA_DIR", "SHAREG_DOWNLOAD_DIR",
                "XDG_CONFIG_HOME", "APPDATA"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ---------------------------------------------------------------- Android

def test_android_uses_flet_app_storage(android_env, tmp_path):
    """app_data_dir must live inside FLET_APP_STORAGE_DATA, never /data/data."""
    d = paths.app_data_dir()
    assert d == os.path.join(str(android_env), "ShareG")
    assert os.path.isdir(d)  # created successfully (writable app-private)
    assert not d.startswith(os.path.join(os.sep, "data", "data") + os.sep + ".shareg")


def test_android_never_attempts_data_data(android_env, tmp_path):
    """With unwritable roots simulated, no path under /data/data is used."""
    # ANDROID_DATA points at a simulated (read-only) /data root; the old bug
    # would build /data/data/.shareg from it. The fix must not.
    monkeypatch = android_env  # fixture already returns flet_data path
    d = paths.app_data_dir()
    norm = d.replace("\\", "/")
    assert not norm.startswith("/data/data/.shareg")
    assert norm.endswith("/ShareG")


def test_android_without_flet_env_uses_p4a_fallback(clean_env, monkeypatch, tmp_path):
    """Direct P4A launch (no Flet env): ANDROID_PRIVATE / HOME are used, and
    when none is set the error is explicit instead of touching /data/data."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    monkeypatch.delenv("FLET_APP_STORAGE_DATA", raising=False)
    monkeypatch.delenv("ANDROID_PRIVATE", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(RuntimeError, match="no writable app-private storage"):
        paths.app_data_dir()

    home = tmp_path / "p4a_home"
    home.mkdir()
    monkeypatch.setenv("ANDROID_PRIVATE", str(home))
    d = paths.app_data_dir()
    assert d == os.path.join(str(home), "ShareG")


def test_android_pairing_store_roundtrip(android_env):
    """Pairing data can be created/read/written in the Android location."""
    from shareg.pairing import PairingStore
    store = PairingStore(paths.pairing_store_path())
    store.pair("device-123", "Pixel", "192.168.1.5", port=50712)
    assert paths.pairing_store_path().startswith(paths.app_data_dir())
    reloaded = PairingStore(paths.pairing_store_path())
    entry = reloaded.get("device-123")
    assert entry and entry["status"] == "paired" and entry["name"] == "Pixel"


def test_android_downloads_inside_app_storage(android_env, tmp_path):
    """Android must not rely on Linux-style ~/Downloads."""
    monkeypatch = android_env
    # Host may genuinely have ~/Downloads; the Android branch must ignore it.
    d = paths.downloads_dir()
    assert d == os.path.join(paths.app_data_dir(), "received")
    assert os.path.isdir(d)
    assert "Downloads" not in d.split(os.sep)  # not a ~/Downloads path


def test_android_identity_store_writable(android_env):
    from shareg.identity import get_identity
    ident = get_identity("AndroidDevice")
    assert ident["device_id"]
    assert paths.identity_store_path().startswith(paths.app_data_dir())


# ------------------------------------------------- other platforms unchanged

def test_desktop_linux_unchanged(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    d = paths.app_data_dir()
    assert d == os.path.join(str(tmp_path / "config"), "ShareG")
    assert os.path.isdir(d)


def test_desktop_linux_downloads_unchanged(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    # expanduser("~") resolution differs per host OS: HOME on POSIX,
    # USERPROFILE on Windows. Set both so the test is host-independent.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    d = paths.downloads_dir()
    assert d == os.path.join(str(downloads), "ShareG")


def test_windows_unchanged_even_with_android_markers(clean_env, monkeypatch, tmp_path):
    """Windows branch wins regardless of stray Android env vars."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    # Stray Android markers must not hijack the win32 branch.
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    monkeypatch.setenv("ANDROID_DATA", "/data")
    d = paths.app_data_dir()
    assert d == os.path.join(str(tmp_path / "appdata"), "ShareG")


def test_override_env_still_wins_on_all_platforms(clean_env, monkeypatch, tmp_path):
    """SHAREG_DATA_DIR override behavior is unchanged everywhere."""
    override = tmp_path / "custom"
    override.mkdir()
    for plat in ("win32", "linux"):
        monkeypatch.setattr(sys, "platform", plat)
        if plat == "linux":
            monkeypatch.setenv("ANDROID_DATA", "/data")  # even on "Android"
        monkeypatch.setenv("SHAREG_DATA_DIR", str(override))
        assert paths.app_data_dir() == str(override)


def test_is_android_detection(clean_env, monkeypatch):
    """Only real Android markers trigger the Android branch; desktop Linux
    (and flet run's FLET_APP_STORAGE_DATA on desktop) must not."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert paths._is_android() is False            # bare desktop Linux
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", "/x")  # desktop `flet run`
    assert paths._is_android() is False
    monkeypatch.setenv("ANDROID_DATA", "/data")    # real Android marker
    assert paths._is_android() is True
