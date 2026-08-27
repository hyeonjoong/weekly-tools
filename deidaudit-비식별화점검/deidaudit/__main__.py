"""`python3 -m deidaudit` 진입점."""

import sys

from .cli import run

if __name__ == "__main__":  # pragma: no cover - 진입점
    sys.exit(run())
