"""ShareG entry point.

Run with:  flet run main.py        (dev)
or:        python main.py          (uses flet's built-in desktop runtime)
"""

import sys

import flet as ft

from shareg.ui import ShareGApp


def main(page: ft.Page) -> None:
    ShareGApp(page)


if __name__ == "__main__":
    ft.run(main)
