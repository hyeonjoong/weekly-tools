"""`python -m irbpack` 진입점."""

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover - 실행 경로
    sys.exit(main())
