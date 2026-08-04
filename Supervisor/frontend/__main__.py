"""Print launch instructions for the Supervisor Executive canvas plugin."""

from __future__ import annotations


def main() -> None:
    print(
        "Supervisor frontend runs as an Executive canvas plugin.\n"
        "  conda activate <MegaDesk-env>\n"
        "  pip install -e ../megadesk\n"
        "  pip install -e ../Executive\n"
        "  pip install -e .[canvas]\n"
        "  1. Start Executive: python main.py  (from the Executive repo)\n"
        "  2. Drop-in sidebar -> supervisor -> place on canvas\n"
        "  3. The commander BE starts automatically on drop\n"
        "  4. Double-click the placard for the operator panel"
    )


if __name__ == "__main__":
    main()
