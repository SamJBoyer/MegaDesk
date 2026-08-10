"""Print launch instructions for the Supervisor MegaDesk canvas plugin."""

from __future__ import annotations


def main() -> None:
    print(
        "Supervisor FE (supervisor_frontend) runs as a MegaDesk canvas plugin.\n"
        "  conda activate <MegaDesk-env>\n"
        "  pip install -e ../../MegaDesk-contracts\n"
        "  pip install -e .[canvas]\n"
        "  1. Start MegaDesk: python main.py  (from MegaDesk-Canvas/)\n"
        "  2. Catalog sidebar -> supervisor -> place on canvas\n"
        "  3. The Supervisor BE starts automatically on drop\n"
        "  4. Double-click the placard for the operator panel"
    )


if __name__ == "__main__":
    main()
