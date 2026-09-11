import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXAMPLES = os.path.join(ROOT, "examples")

#: 번들 예제에 맞는 표준 인자. 여러 테스트가 그대로 쓴다.
SPEC_ARGS = [
    "--subtest", "자음:목표=2음절초성,문항=18",
    "--subtest", "모음:목표=1음절중성,문항=12",
    "--subtest", "일음절:목표=전체,문항=10",
    "--subject-pattern", "^S[0-9]+$",
    "--answer-key", "언어검사_정답",
    "--summary", "total-최종",
]


@pytest.fixture(scope="session")
def examples_dir():
    return EXAMPLES


@pytest.fixture(scope="session")
def package_dir():
    return os.path.join(ROOT, "jamoscore")


@pytest.fixture(scope="session")
def repo_root():
    return ROOT


@pytest.fixture
def spec_args():
    return list(SPEC_ARGS)
