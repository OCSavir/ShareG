# ShareG

**ShareG** is a cross-platform local-network file & text sharing app for
**Windows**, **Linux**, and **Android**. Devices on the same LAN discover each
other automatically and transfer text, files, and folders directly over
TCP — with **no internet connection, no cloud, and no third-party server**.

```
                Local Network
   ┌─────────────────────────────────────┐
   │                                     │
Windows PC                        Android Phone
   │                                     │
   └──────────── UDP Multicast ─────────┘
                 (Discovery)
                     │
                     ▼
              TCP Connection
                     │
                     ▼
          File / Text Transfer
```

## Architecture

| Layer | Implementation |
|---|---|
| UI | Flet (Flutter-rendered Python UI) — `shareg/ui.py` |
| Backend facade | `shareg/backend.py` — the single object the UI talks to |
| Discovery | UDP multicast (group `239.255.83.72:50711`), 2 s announcements — `shareg/discovery.py` |
| Transfer | Raw TCP sockets with a length-prefixed frame protocol — `shareg/protocol.py` |
| Pairing | Accept/Reject prompt on first contact; persisted per device — `shareg/pairing.py` |
| Health | Background TCP ping (heartbeat) of paired devices — `shareg/heartbeat.py` |

Strictly standard-library networking (raw UDP + TCP sockets). No SMB, no FTP,
no WebSocket relay, no OS-specific networking: the same wire protocol runs on
all three platforms.

### Wire protocol (TCP)

Every frame is `4-byte big-endian length + payload`. A session opens with a
`pair_request` handshake (device id, name, port); the receiver answers
`pair_accept` / `pair_reject` based on its persisted trust store, or shows the
user a prompt for unknown devices. Text is one JSON frame; files stream as
`file_begin` → binary chunks → `file_end`, and folder/multi-file batches are
wrapped in `folder_begin` / `folder_end`. Received files land in
`Downloads/ShareG` (or app storage on Android), with the folder structure
preserved and path-traversal sanitized.

## Features

- **Send Text tab** — Paste / Clear / Send buttons, all functional.
- **Send Files tab** — pick single files, multiple files, or whole folders;
  every file format is supported; chunked transfer (256 KiB) with a live
  progress bar; folders arrive with their tree intact.
- **Pairing & trust** — first contact shows *"Do you want to connect to this
  device?"* (Accept / Reject). Accepted devices are stored persistently and
  connect automatically afterwards; rejected devices stay blocked. Decisions
  survive restarts.
- **Connection health** — paired devices are pinged in the background every
  5 s; the UI flips them to Disconnected automatically when they go offline.
- **Branding** — the name *ShareG* is used everywhere; a custom generated icon
  is wired into the runtime window (`assets/icon.ico`), Windows packaging
  (`assets/icon_windows.ico`), and Linux/Android packaging (`assets/icon.png`).

## Project layout

```
shareg/
  constants.py   protocol & tuning constants
  paths.py       per-OS data/download dirs
  identity.py    persistent device identity (device_id + name)
  discovery.py   UDP multicast announcer/listener + peer expiry
  protocol.py    TCP framing, chunked file streaming, transfer server
  pairing.py     persistent trusted/blocked device store
  heartbeat.py   background health monitor
  backend.py     facade wiring all of the above (UI-facing API)
  ui.py          Flet UI (Send Text / Send Files tabs, devices, activity log)
main.py          desktop entry point
run.py           headless node harness (testing/debugging)
tools/           icon generators (stdlib-only)
tests/           pytest suite (discovery, transfers, pairing, health)
```

## Running from source

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python main.py
```

Run two instances on one machine (or two machines) to try it out:
launch ShareG on both devices, wait a couple of seconds until each appears in
the other's **Nearby devices** list, click a device, then send text or files.

Platform-specific notes and packaging commands: [build/](build/).

## Tests

```bash
python -m pytest tests/ -v
```

Covers: mutual UDP discovery + peer expiry, text transfer (incl. large/unicode),
single/multi-file/folder/large-file transfers with progress, pairing
(first-contact prompt, acceptance, rejection, persistence across restart,
no re-prompt for known devices), disconnection detection via heartbeat,
protocol framing, and path-traversal protection.

## Building distributables

With the Flet CLI (`pip install flet-cli`), from the project root:

```bash
flet build windows   # standalone Windows exe
flet build linux     # Linux bundle
flet build apk       # signed/unsigned Android APK
```

The `[tool.flet]` section of `pyproject.toml` sets the product name
(`ShareG`), icon, and Android permissions (INTERNET, multicast/Wi-Fi state,
and storage). See `build/android.md` for JDK/SDK setup.
