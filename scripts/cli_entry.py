"""Entry point used by PyInstaller to build bookletize.exe."""
import sys

from bookletize.cli import main

if __name__ == "__main__":
    sys.exit(main())
