"""``python3 -m revcheck`` 로도 실행할 수 있게 한다."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover - 진입점
    raise SystemExit(main())
