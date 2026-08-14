"""테스트 공용 도구. 모든 테스트는 **완전히 오프라인**이다."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # 설치 없이도 테스트가 돌게 한다
    sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "examples"


# ── 합성 3종 세트(마크다운) ────────────────────────────────────────────────
OLD_MD = """# Sleep trial

## Methods

Participants were randomised 1:1 to the active or the sham device.

## Results

The mean ISI decreased by 5.2 points (SD 3.1) in the active arm.

Adherence was 82% in the active arm and 79% in the sham arm.

## Discussion

The effect size is comparable with brief behavioural interventions.
"""

NEW_MD = """# Sleep trial

## Methods

Participants were randomised 1:1 to the active or the sham device. Allocation was concealed in sequentially numbered opaque envelopes.

## Results

The mean ISI decreased by 5.2 points (SD 3.1) in the active arm.

Adherence was 82% in the active arm and 79% in the sham arm.

## Discussion

The effect size is comparable with brief behavioural interventions.
"""

RESPONSE_MD = """Response to Reviewers

Reviewer 1

Comment 1-1: How was allocation concealed?
Response: We have added a sentence to the Methods section describing concealment:
"Allocation was concealed in sequentially numbered opaque envelopes."

Comment 1-2: Please confirm the adherence figures.
Response: The adherence figures are unchanged and are reported in the Results section
exactly as in the original submission, with no reanalysis performed.

Comment 1-3: The discussion is adequate.
Response: We thank the reviewer for this positive assessment and have left the Discussion
section unchanged in this revision.
"""


@pytest.fixture
def trio(tmp_path):
    """(old, new, response) 세 파일 경로를 만들어 준다."""

    def make(old: str = OLD_MD, new: str = NEW_MD, resp: str = RESPONSE_MD, suffix: str = ".md"):
        paths = []
        for name, text in (("old", old), ("new", new), ("resp", resp)):
            path = tmp_path / f"{name}{suffix}"
            path.write_text(text, encoding="utf-8")
            paths.append(str(path))
        return paths

    return make


@pytest.fixture
def run_cli(capsys):
    """CLI 를 돌리고 (종료코드, 표준출력) 을 돌려준다."""

    from revcheck.cli import main

    def run(*args):
        code = main([str(a) for a in args])
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    return run
