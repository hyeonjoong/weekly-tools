"""Allow ``python3 -m medpath ...`` as the canonical entry point.

Without this module the package could only be run as ``python3 -m medpath.cli``,
which is not what the help text or the docs tell people to type.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
