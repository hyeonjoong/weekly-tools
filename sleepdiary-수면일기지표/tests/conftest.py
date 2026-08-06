import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def examples_dir():
    return os.path.join(ROOT, "examples")


@pytest.fixture
def trial_csv(examples_dir):
    return os.path.join(examples_dir, "sleep_diary_trial.csv")


@pytest.fixture
def korean_csv(examples_dir):
    return os.path.join(examples_dir, "수면일기_한글_cp949.csv")
