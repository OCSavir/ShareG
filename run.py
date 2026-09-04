"""Headless test harness entry point: runs two ShareG backends locally.

Used by the test suite and for debugging without a GUI:
    python run.py --role a --name DeviceA
    python run.py --role b --name DeviceB
"""

import argparse
import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shareg.backend import ShareGBackend  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ShareG headless node")
    parser.add_argument("--name", required=True, help="device name to announce")
    parser.add_argument("--data-dir", required=True, help="per-node data dir")
    parser.add_argument("--seconds", type=int, default=30, help="run duration")
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    os.environ["SHAREG_DATA_DIR"] = args.data_dir

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    events = []

    backend = ShareGBackend(
        device_name=args.name,
        on_log=lambda m: print(m, flush=True),
    )
    backend.set_ui_callbacks(
        on_pair_prompt=lambda did, name, ip, box: _auto_answer(name, box, events),
        on_text_received=lambda s, t: events.append(("text", s, t)),
        on_files_received=lambda s, f, fs: events.append(("files", s, f, fs)),
    )
    backend.start()

    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        backend.stop()
    return 0


def _auto_answer(peer_name: str, box: dict, events: list) -> None:
    """Auto-accept pairing in headless mode (tests pass --yes instead)."""
    print(f"[pairing] request from {peer_name} — auto-accepting (headless)", flush=True)
    box["answer"] = True
    box["event"].set()


if __name__ == "__main__":
    sys.exit(main())
