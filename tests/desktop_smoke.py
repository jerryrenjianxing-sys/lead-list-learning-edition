"""Compatibility entry point for the packaged Electron lifecycle smoke."""
import sys
from pathlib import Path

from electron_desktop_smoke import main


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
