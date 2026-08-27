"""리포트 문구 규율 — 금지어 grep·출처 표기·커버리지 자백 위치·CSV 가드.

금지어 목록은 report.py 와 독립적으로 여기 하드코딩한다(같은 상수를
import 하면 검증이 동어반복이 된다). '진단'이라는 단어 자체는 면책
문구("진단이 아닙니다")에 필요하므로, 단정형 표현만 금지한다.
"""

import os
import subprocess
import sys

import pytest

from circadia.report import csv_guard

ROOT = os.path.join(os.path.dirname(__file__), "..")
EXAMPLES = os.path.join(ROOT, "examples")

# 진단 단정형 금지어 — 하나라도 나오면 이 도구는 의료기기 영역을 침범한다.
# 라운드 1 C2: 완곡 진단("장애로 보입니다", "의심됩니다", "위험이 높습니다",
# "당신은 …입니다. 치료를 받아…")이 구멍이었음이 실증되어 스템을 확장했다.
FORBIDDEN = [
    "진단합니다", "진단됩니다", "진단되었", "진단명", "진단이 필요합",
    "장애입니다", "장애가 있", "장애로 보", "장애일 가능",
    "질환이 있", "질환입니다",
    "환자입니다", "환자로 보", "증후군", "위험군입니다", "위험이 높",
    "의심됩니다", "이 의심",
    "치료가 필요", "치료를 받", "불면증입니다", "처방",
    "당신은",
]

# C2 검증에 쓰였던 실제 우회 문장들 — FORBIDDEN 이 다시 물러지면 여기서 죽는다
EVASIVE_MUTANTS = [
    "일주기리듬 수면-각성 장애로 보입니다.",
    "불면증이 의심됩니다. 건강 위험이 높습니다.",
    "당신은 저녁형입니다. 치료를 받아 보세요.",
]


def test_forbidden_list_catches_known_evasive_mutants():
    """스크리닝 테스트의 테스트 — 알려진 완곡 진단 3문장이 전부 걸려야 한다."""
    for sentence in EVASIVE_MUTANTS:
        assert any(stem in sentence for stem in FORBIDDEN), \
            f"금지어 목록이 이 완곡 진단을 놓칩니다: {sentence!r}"


def run_cli(*args):
    """설치 없이 소스로 실행 — stdout 텍스트 반환."""
    proc = subprocess.run(
        [sys.executable, "-m", "circadia", *args],
        cwd=ROOT, capture_output=True, text=True)
    return proc


@pytest.fixture(scope="module")
def regular_report():
    d = os.path.join(EXAMPLES, "규칙적_1주_애플건강")
    proc = run_cli(os.path.join(d, "심박.csv"), "--steps",
                   os.path.join(d, "걸음.csv"), "--sleep",
                   os.path.join(d, "수면.csv"))
    assert proc.returncode == 0
    return proc.stdout


@pytest.fixture(scope="module")
def irregular_report():
    d = os.path.join(EXAMPLES, "불규칙_1주_삼성헬스")
    proc = run_cli(os.path.join(d, "심박.csv"), "--steps",
                   os.path.join(d, "걸음.csv"), "--sleep",
                   os.path.join(d, "수면.csv"))
    assert proc.returncode == 0
    return proc.stdout


# ---------------------------------------------------------------- 금지어

def test_no_diagnostic_assertions_in_reports(regular_report, irregular_report):
    for text in (regular_report, irregular_report):
        for phrase in FORBIDDEN:
            assert phrase not in text, f"진단 단정형 문구 발견: {phrase!r}"


def test_no_diagnostic_assertions_in_docs():
    for fname in ("README.md", "사용법.md", "실행.command"):
        with open(os.path.join(ROOT, fname), encoding="utf-8") as fh:
            text = fh.read()
        for phrase in FORBIDDEN:
            assert phrase not in text, f"{fname}: 진단 단정형 문구 발견 {phrase!r}"


def test_disclaimer_present_in_report_and_readme(regular_report):
    assert "의료기기가 아니" in regular_report
    assert "수면클리닉" in regular_report
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    assert "의료기기가 아니" in readme
    assert "수면클리닉" in readme


# ---------------------------------------------------------------- 출처·구조

def test_every_reference_range_has_citation(regular_report):
    """참고범위를 말하는 줄에는 반드시 출처가 있어야 한다."""
    cited = ("Van Someren 1999", "Phillips 2017", "Windred 2023",
             "Roenneberg 2012", "Hirshkowitz 2015", "ABPM")
    for line in regular_report.split("\n"):
        if "— 참고범위" in line:     # 해석 줄만 — 면책 문구의 일반 언급은 제외
            assert any(c in line for c in cited), f"출처 없는 참고범위 줄: {line!r}"


def test_all_five_method_citations_present(regular_report):
    for c in ("Van Someren 1999", "Phillips 2017", "Windred 2023",
              "Roenneberg 2012", "Cornelissen 2014"):
        assert c in regular_report, f"인용 누락: {c}"


def test_interpretation_lines_use_direction_phrasing(irregular_report):
    """해석은 '참고범위 대비 낮음/높음' 또는 '참고범위 내' 구조만 쓴다."""
    assert ("참고범위 대비 낮음" in irregular_report
            or "참고범위 대비 높음" in irregular_report)
    # 불규칙 시나리오: SRI 낮음·사회적 시차 높음이 설계 의도
    sri_line = next(l for l in irregular_report.split("\n") if l.startswith("SRI"))
    assert "참고범위 대비 낮음" in sri_line
    sjl_line = next(l for l in irregular_report.split("\n") if "사회적 시차" in l)
    assert "참고범위 대비 높음" in sjl_line


def test_coverage_confession_is_at_the_top(regular_report):
    lines = regular_report.split("\n")
    cov_i = next(i for i, l in enumerate(lines) if "데이터 커버리지" in l)
    cos_i = next(i for i, l in enumerate(lines) if "코사이너" in l)
    assert cov_i < cos_i
    assert cov_i <= 4          # 제목 바로 다음
    assert "보간하지 않았습니다" in regular_report


def test_gaps_and_dropped_days_confessed(irregular_report):
    assert "갭(3시간 이상)" in irregular_report
    assert "제외한 날(빈 미달 — 보간하지 않음)" in irregular_report
    assert "착용률" in irregular_report


# ---------------------------------------------------------------- CSV 가드

def test_csv_guard_neutralizes_formulas_keeps_numbers():
    assert csv_guard("=SUM(A1:A9)") == "'=SUM(A1:A9)"
    assert csv_guard("+82-10-1234") == "'+82-10-1234"
    assert csv_guard("@import") == "'@import"
    assert csv_guard("-100") == "-100"        # SRI 음수는 숫자 그대로
    assert csv_guard("-0.5") == "-0.5"
    assert csv_guard("정상") == "정상"
    assert csv_guard("") == ""


def test_metrics_csv_has_no_live_formula_cells(tmp_path):
    d = os.path.join(EXAMPLES, "규칙적_1주_삼성헬스")
    out = tmp_path / "결과"
    proc = run_cli(os.path.join(d, "심박.csv"), "--sleep",
                   os.path.join(d, "수면.csv"), "--out-dir", str(out))
    assert proc.returncode == 0
    import csv as _csv
    with open(out / "지표.csv", encoding="utf-8-sig") as fh:
        for row in _csv.reader(fh):
            for cell in row:
                if cell and cell[0] in "=@+":
                    pytest.fail(f"가드 안 된 셀: {cell!r}")
                if cell.startswith("-"):
                    float(cell)     # '-' 시작이면 반드시 숫자여야 함
