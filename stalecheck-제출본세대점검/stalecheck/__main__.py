"""`python3 -m stalecheck` 진입점."""

from .cli import exit_with, main

if __name__ == "__main__":
    exit_with(main())
