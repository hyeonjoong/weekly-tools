"""실파일 known-answer 회귀.

이 툴을 만든 이유가 된 실제 사고를 **숫자로** 고정한다. 원고 파일은
저장소에 들어오지 않는다 — 경로가 없으면 통째로 skip 되고, 여기 적힌
것은 익명화된 수치(고아 2개·중복 3쌍·앵커 4개 …)뿐이다.

사고 요약: `수정5`(10:32)에서 Fig. 4 그림 두 장이 본문 앵커를 잃었고
`수정10`(12:14)까지 여섯 판본·1시간 42분 동안 그대로 돌아다녔다.
그 사이 공저자에게 나갔다. 화면으로는 보이지 않는 사고다.
"""

import glob
import os

import pytest

from packlist import EXIT_CRITICAL, EXIT_OK
from packlist.analysis import analyse
from packlist.docxpkg import read_docx
from packlist.envelope import choose_manuscript, scan_envelope
from packlist.textrefs import extract_refs

#: 실제 원고 폴더. 저장소에 파일은 들어가지 않는다. 다른 컴퓨터에서는
#: ``PACKLIST_REAL_CORPUS`` 로 가리키거나, 없으면 통째로 skip 된다.
BASE = os.environ.get(
    "PACKLIST_REAL_CORPUS",
    os.path.expanduser("~/Downloads/02_프로젝트/논문_투고/01_BELL001_수면신경/MoA논문작성"),
)
VERSIONS = os.path.join(BASE, "_이전본")
SUBMITTED = os.path.join(BASE, "npjDM_투고본_2026-08-19")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(VERSIONS),
    reason="실제 원고 폴더가 이 컴퓨터에 없습니다 (저장소에는 절대 넣지 않습니다)",
)

PREFIX = "BELL001_npjDM_manuscript_"


def resolve(pattern: str) -> str:
    """판본 하나의 파일명을 glob 으로 찾는다.

    파일명을 그대로 적어 두면 공저자의 이름이 공개 저장소에 남는다.
    여기에는 **판본 번호만** 적고 실제 이름은 이 컴퓨터에서만 풀린다.
    """
    hits = sorted(glob.glob(os.path.join(VERSIONS, pattern)))
    if len(hits) != 1:
        pytest.skip(f"판본을 특정할 수 없습니다: {pattern} ({len(hits)}개)")
    return os.path.basename(hits[0])


# (판본 glob, 미디어수, 중복쌍, 고아수, 본문앵커수, 기대 종료코드)
TABLE = [
    ("*수정3_재코멘트.docx", 6, 3, 0, 6, EXIT_OK),
    ("*수정3_재코멘트_*.docx", 6, 3, 0, 6, EXIT_OK),
    ("*_수정4.docx", 6, 3, 0, 6, EXIT_OK),
    ("*수정4_*.docx", 6, 3, 0, 6, EXIT_OK),
    ("*_수정5.docx", 6, 3, 2, 4, EXIT_CRITICAL),
    ("*_수정6.docx", 6, 3, 2, 4, EXIT_CRITICAL),
    ("*_수정7.docx", 6, 3, 2, 4, EXIT_CRITICAL),
    ("*_수정8.docx", 6, 3, 2, 4, EXIT_CRITICAL),
    ("*_수정9.docx", 6, 3, 2, 4, EXIT_CRITICAL),
    ("*_수정10.docx", 6, 3, 2, 4, EXIT_CRITICAL),
    ("*_수정11.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정12.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정13.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정14.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정15.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정16.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정17.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정18.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정19.docx", 4, 0, 0, 4, EXIT_OK),
    ("*_수정20.docx", 4, 0, 0, 4, EXIT_OK),
    ("rev13_*.docx", 6, 2, 0, 6, EXIT_OK),
    ("rev15_*.docx", 6, 2, 0, 6, EXIT_OK),
    ("rev16_*.docx", 4, 0, 0, 4, EXIT_OK),
    ("rev18_*.docx", 6, 2, 0, 6, EXIT_OK),
]

ORPHANED = {"image3.png", "image30.png"}


def _report(folder, filename):
    env = scan_envelope(folder)
    chosen = choose_manuscript(env, filename)
    info = read_docx(env.root / chosen.name, chosen.name)
    return analyse(env, chosen, info, [], compare_titles=False), info


@pytest.mark.parametrize("pattern,media,dups,orphans,anchors,code", TABLE,
                         ids=[row[0].strip("*_") for row in TABLE])
def test_every_version_matches_measured_values(pattern, media, dups, orphans, anchors, code):
    report, info = _report(VERSIONS, resolve(pattern))
    assert len(info.media) == media
    assert len(info.duplicate_groups()) == dups
    assert len(info.orphans) == orphans
    assert len(info.body_anchored) == anchors
    assert report.exit_code == code


def test_exactly_six_versions_are_critical():
    """숫자를 늘려 만회하지 않는다 — 치명은 정확히 여섯 판본이다."""
    critical = [row[0] for row in TABLE if row[5] == EXIT_CRITICAL]
    assert len(critical) == 6
    assert all("수정%d." % n in "".join(critical) for n in range(5, 11))


def test_orphan_free_versions_report_zero_orphan_criticals():
    """오탐 억제 — 고아 0인 18개 판본에서 고아 치명이 한 건도 없어야 한다."""
    clean = [row[0] for row in TABLE if row[3] == 0]
    assert len(clean) == 18
    for pattern in clean:
        report, _ = _report(VERSIONS, resolve(pattern))
        assert not [f for f in report.findings if f.code == "ORPHAN_MEDIA"], pattern


def test_duplicates_are_never_critical():
    for pattern in (row[0] for row in TABLE if row[2] > 0):
        report, _ = _report(VERSIONS, resolve(pattern))
        dup = [f for f in report.findings if f.code == "DUP_MEDIA"]
        assert dup and dup[0].severity == "경고", pattern


@pytest.mark.parametrize("pattern", [f"*_수정{n}.docx" for n in range(5, 11)])
def test_orphan_files_are_the_expected_two(pattern):
    _, info = _report(VERSIONS, resolve(pattern))
    assert {m.basename for m in info.orphans} == ORPHANED


def test_baseline_detects_the_anchor_regression():
    """`--baseline 수정4 → 수정5` 가 '지난 회차 앵커 → 이번 회차 고아'를 잡는다."""
    env = scan_envelope(VERSIONS)
    current = choose_manuscript(env, resolve("*_수정5.docx"))
    base = choose_manuscript(env, resolve("*_수정4.docx"))
    info = read_docx(env.root / current.name, current.name)
    base_info = read_docx(env.root / base.name, base.name)
    report = analyse(env, current, info, [],
                     baseline=(base.name, base_info, env.total_bytes),
                     compare_titles=False)
    regression = [f for f in report.findings if f.code == "ANCHOR_REGRESSION"]
    assert regression and regression[0].severity == "치명"
    assert len(regression[0].detail) == 3      # 그림 2개 + 기준 판본 한 줄


def test_no_regression_between_two_clean_versions():
    env = scan_envelope(VERSIONS)
    current = choose_manuscript(env, resolve("*_수정4.docx"))
    base = choose_manuscript(env, resolve("*수정3_재코멘트.docx"))
    info = read_docx(env.root / current.name, current.name)
    base_info = read_docx(env.root / base.name, base.name)
    report = analyse(env, current, info, [],
                     baseline=(base.name, base_info, env.total_bytes),
                     compare_titles=False)
    assert not [f for f in report.findings if f.code == "ANCHOR_REGRESSION"]


@pytest.mark.parametrize("pattern,comments", [
    ("*_수정10.docx", 89),
    ("*_수정19.docx", 87),
    ("*_수정20.docx", 87),
])
def test_comment_counts_match_hand_recorded_values(pattern, comments):
    """사용자가 손으로 세어 둔 값과 일치한다."""
    _, info = _report(VERSIONS, resolve(pattern))
    assert info.comment_count == comments


@pytest.mark.parametrize("pattern,paragraphs", [
    ("*_수정4.docx", 214),
    ("*_수정5.docx", 212),
    ("*_수정10.docx", 215),
    ("*_수정20.docx", 213),
])
def test_paragraph_counts_are_the_documented_rule(pattern, paragraphs):
    """문단 수는 사람이 손으로 센 값(211/212)과 다르다 — 맞추지 않고 규칙을 적는다."""
    _, info = _report(VERSIONS, resolve(pattern))
    assert info.raw_paragraph_count == paragraphs


@pytest.mark.skipif(not os.path.isdir(SUBMITTED), reason="투고 봉투 폴더가 없습니다")
def test_submitted_envelope_finds_twelve_supplementary_promises():
    env = scan_envelope(SUBMITTED)
    chosen = choose_manuscript(env, None)
    info = read_docx(env.root / chosen.name, chosen.name)
    refs = extract_refs(info.paragraphs)
    assert len(refs.supp) == 12
    assert {f"S{n} Table" for n in range(1, 7)} <= set(refs.supp)
    assert {f"S{n} Fig" for n in range(1, 7)} <= set(refs.supp)


@pytest.mark.skipif(not os.path.isdir(SUBMITTED), reason="투고 봉투 폴더가 없습니다")
def test_submitted_envelope_keeps_promises_unverified_without_expect():
    """`--expect` 없이는 '없음'이 아니라 '대조불가' 여야 한다 — 매 회차 울면 안 된다."""
    env = scan_envelope(SUBMITTED)
    chosen = choose_manuscript(env, None)
    info = read_docx(env.root / chosen.name, chosen.name)
    report = analyse(env, chosen, info, [])
    assert {row.verdict for row in report.promises} == {"대조불가"}
    assert report.exit_code == EXIT_OK


@pytest.mark.skipif(not os.path.isdir(SUBMITTED), reason="투고 봉투 폴더가 없습니다")
def test_submitted_envelope_confesses_the_three_subfolders():
    env = scan_envelope(SUBMITTED)
    assert len(env.subdirs) == 3
    chosen = choose_manuscript(env, None)
    info = read_docx(env.root / chosen.name, chosen.name)
    report = analyse(env, chosen, info, [])
    assert len(report.coverage.excluded_dirs) == 3
