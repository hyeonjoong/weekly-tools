"""Make ``rocdx`` importable without installing it.

pytest inserts the *rootdir* on ``sys.path`` only when it is the working
directory, so ``python3 -m pytest /path/to/rocdx-진단정확도분석`` from elsewhere
would otherwise fail to import the package. Adding the project root here keeps
`python3 -m pytest` working from any directory and in any editor's test runner.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
