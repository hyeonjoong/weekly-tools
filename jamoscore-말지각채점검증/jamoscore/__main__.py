"""``python3 -m jamoscore`` 로 실행할 수 있게 한다."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
