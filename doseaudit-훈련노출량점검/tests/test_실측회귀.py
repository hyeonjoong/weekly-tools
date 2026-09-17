"""실측 회귀 — 이 툴의 척추.

전부 **실제 로그 30개를 읽어 센 값**이다. 예상이 아니다. 저장소에 실데이터는
들어오지 않으므로, 데이터가 있는 기기에서만 돌고 없으면 skip 한다.
Access Code 는 저장소에 남기지 않기 위해 **SHA-256 앞 16자**로만 적는다.
"""

import datetime
import hashlib
import statistics

import pytest

from conftest import REAL_LOGS, REAL_TRACKER, real_data
from doseaudit.audit import Config, run_audit
from doseaudit.dose import STATUS_ALL_ZERO, STATUS_NO_DATA, check_vendor_summaries
from doseaudit.logs import load_bundle
from doseaudit.rules import ActiveDayRule, Window

pytestmark = real_data

# ── 실측값 (2026-09-17 기획 세션 + 빌드 세션에서 두 번 독립 확인) ────────────
N_WORKBOOKS = 30
N_SHEETS = 180
N_DATA_ROWS = 27_868
N_ZERO_SECONDS = 9_933
ZERO_RATIO_PCT = 35.6
N_REGULAR = 25_948
N_INDIVIDUAL = 1_920
FIRST_DATE = datetime.date(2026, 1, 31)
LAST_DATE = datetime.date(2026, 6, 21)
EMPTY_MODULE = "발성훈련"
N_VENDOR_SHEETS = 150
N_TRACKER_ROWS = 30
N_TRACKER_MISMATCH = 23
N_TRACKER_AGREE = 7
DELTA_MIN, DELTA_MAX = 1, 7

#: 트래커와 **일치한** 7명. 여기서 치명이 뜨면 오탐이다.
AGREE_CODE_HASHES = {
    "845468b3bf7113f1", "f3f3c1577e0ab47b", "721e0021b0cbba1b", "da691d69b366aa64",
    "06297d4150972da1", "9eabde8a82e76660", "d455a240c61fea64",
}


def code_hash(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(REAL_LOGS)


@pytest.fixture(scope="module")
def result():
    return run_audit(Config(
        logs=REAL_LOGS, tracker_path=REAL_TRACKER,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-06-23"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))


# ── 파싱 ─────────────────────────────────────────────────────────────────────
def test_워크북_30개(bundle):
    assert bundle.n_workbooks == N_WORKBOOKS
    assert bundle.unreadable == []


def test_시트_180장(bundle):
    assert bundle.n_sheets == N_SHEETS


def test_데이터_27868행(bundle):
    assert bundle.n_data_rows == N_DATA_ROWS


def test_날짜_범위(bundle):
    assert bundle.date_span() == (FIRST_DATE, LAST_DATE)


def test_해석하지_못한_행이_하나도_없다(bundle):
    assert bundle.parse_fail_rows == []


def test_180장_전부_5열_규격이다(bundle):
    """한 장이라도 달랐다면 `load_bundle` 이 거절해 여기까지 오지 못한다."""
    assert sum(len(s.modules) for s in bundle.subjects) == N_SHEETS


def test_0초_행_9933건_35_6퍼센트(bundle):
    zeros = sum(1 for s in bundle.subjects for r in s.rows() if r.seconds == 0)
    assert zeros == N_ZERO_SECONDS
    assert round(100.0 * zeros / bundle.n_data_rows, 1) == ZERO_RATIO_PCT


def test_훈련유형_REGULAR_25948_INDIVIDUAL_1920(bundle):
    counts = {}
    for subject in bundle.subjects:
        for row in subject.rows():
            counts[row.ttype] = counts.get(row.ttype, 0) + 1
    assert counts == {"REGULAR": N_REGULAR, "INDIVIDUAL": N_INDIVIDUAL}
    assert N_REGULAR + N_INDIVIDUAL == N_DATA_ROWS


def test_발성훈련은_30개_파일_전부에서_0행(bundle):
    empty = sum(1 for s in bundle.subjects if s.modules[EMPTY_MODULE].is_empty)
    assert empty == N_WORKBOOKS


def test_모듈은_여섯_개다(bundle):
    assert len(bundle.module_order) == 6
    assert EMPTY_MODULE in bundle.module_order


# ── 벤더 [요약] — 산수는 맞고, 의미가 다르다 ────────────────────────────────
def test_벤더_요약_재계산은_150대_150_일치(bundle):
    """이 툴은 '벤더가 틀렸다'고 말하지 않는다. 산수는 맞다."""
    check = check_vendor_summaries(bundle)
    assert check.n_checked == N_VENDOR_SHEETS
    assert check.n_agree == N_VENDOR_SHEETS
    assert check.mismatches == []


def test_빈_모듈_시트는_벤더_대조에서_빠진다(bundle):
    check = check_vendor_summaries(bundle)
    assert check.n_empty_with_block == N_WORKBOOKS
    assert check.n_no_block == 0


def test_벤더는_소요시간을_소수_두_자리로_인쇄한다(bundle):
    """자릿수를 정수로 고정해 비교하면 150 중 68 이 '불일치'로 둔갑한다."""
    from doseaudit.dose import printed_decimals
    from doseaudit.logs import VENDOR_MEAN_REPS, VENDOR_MEAN_SECONDS
    seen_seconds, seen_reps = set(), set()
    for subject in bundle.subjects:
        for module in subject.modules.values():
            if not module.vendor or not module.rows:
                continue
            seen_seconds.add(printed_decimals(module.vendor[VENDOR_MEAN_SECONDS]))
            seen_reps.add(printed_decimals(module.vendor[VENDOR_MEAN_REPS]))
    assert seen_seconds <= {0, 1, 2} and 2 in seen_seconds
    assert seen_reps == {0}


def test_벤더가_빈_모듈을_평균_0으로_인쇄한다(bundle):
    """산수가 맞는데도 그대로 베끼면 '0을 측정했다'가 된다."""
    vendor = bundle.subjects[0].modules[EMPTY_MODULE].vendor
    assert vendor
    assert all(v.startswith("0") for v in vendor.values())


def test_벤더_요약_일치는_치명이_아니다(result):
    """일치는 치명이 아니다 — 그러나 **일치했다는 사실은 소리 내어 말한다**."""
    from doseaudit.audit import LEVEL_CRITICAL, LEVEL_INFO
    assert result.vendor.mismatches == []
    vendor_findings = [f for f in result.findings if "벤더" in f.title]
    assert len(vendor_findings) == 1
    assert vendor_findings[0].level == LEVEL_INFO
    assert "150/150" in vendor_findings[0].title
    assert not any(f.level == LEVEL_CRITICAL and "벤더" in f.title for f in result.findings)
    body = "\n".join(vendor_findings[0].lines)
    assert "산수는 맞습니다" in body and "의미" in body


# ── 0 과 빈 모듈의 구분 ─────────────────────────────────────────────────────
def test_발성훈련은_데이터_없음_상태다(result):
    assert result.modules[EMPTY_MODULE].status == STATUS_NO_DATA


def test_전부_0초인_모듈이_둘_있다(result):
    all_zero = [m.name for m in result.modules.values() if m.status == STATUS_ALL_ZERO]
    assert set(all_zero) == {"소리스케치북", "음소쌍"}


def test_전부_0초인_모듈은_0제외평균을_내지_않는다(result):
    for name in ("소리스케치북", "음소쌍"):
        assert result.modules[name].seconds.nonzero_mean is None


def test_0초가_아닌_모듈은_평균이_나온다(result):
    assert result.modules["사전자가진단"].seconds.total_mean == pytest.approx(4.19, abs=0.01)
    assert result.modules["사후자가진단"].seconds.total_mean == pytest.approx(3.84, abs=0.01)


# ── 트래커 대조 ──────────────────────────────────────────────────────────────
def test_트래커는_30행이고_실명_열을_열지_않는다(result):
    assert len(result.tracker.rows) == N_TRACKER_ROWS
    assert len(result.tracker.dropped_columns) == 2
    assert result.tracker.grid_columns == 145


def test_불일치_정확히_23명(result):
    assert len(result.comparison["diffs"]) == N_TRACKER_MISMATCH


def test_일치_정확히_7명(result):
    assert len(result.comparison["agree"]) == N_TRACKER_AGREE


def test_차이는_플러스_1에서_7(result):
    assert (result.comparison["delta_min"], result.comparison["delta_max"]) == (DELTA_MIN, DELTA_MAX)


def test_부호가_전원_동일하다_트래커가_더_작다(result):
    assert result.comparison["uniform_sign"] is True
    assert all(d.delta > 0 for d in result.comparison["diffs"])


def test_일치한_7명이_바로_그_7명이다(result):
    assert {code_hash(c) for c in result.comparison["agree"]} == AGREE_CODE_HASHES


def test_일치한_7명에서는_치명이_0건이다(result):
    """오탐 억제 — 검출기보다 먼저 지켜야 하는 성질."""
    flagged = {d.code for d in result.comparison["diffs"]}
    for code in result.comparison["agree"]:
        assert code not in flagged
    from doseaudit.artifacts import tracker_rows
    _header, rows = tracker_rows(result)
    listed = {r[0] for r in rows}
    for code in result.comparison["agree"]:
        assert code not in listed


def test_피험자_집합이_양쪽에서_같다(result):
    assert result.comparison["only_tracker"] == []
    assert result.comparison["only_logs"] == []


def test_트래커_진행률은_트래커_안에서_정합적이다(result):
    """손으로 잘못 들어간 값은 `완료일수` **하나**이고 나머지는 거기서 파생됐다."""
    assert result.comparison["progress_bad"] == []


# ── 노출률과 정의 민감도 ────────────────────────────────────────────────────
def test_중앙값_노출률은_51퍼센트다(result):
    values = result.adherence_values()
    assert len(values) == N_WORKBOOKS
    assert round(statistics.median(values)) == 51


def test_상한_없이는_100퍼센트를_넘는_값이_나온다(result):
    assert max(result.adherence_values()) > 100


def test_정의를_바꾸면_중앙값이_크게_움직인다(result):
    """이 표의 전부는 "정의를 안 적으면 이만큼 달라진다"는 한 문장이다."""
    medians = {r["label"]: r["median"] for r in result.sensitivity}
    assert len(medians) >= 5
    assert round(medians["any-row"]) == 51
    assert round(medians["min-modules=2"]) == 47
    assert round(medians["min-seconds=60"]) == 16
    assert max(medians.values()) - min(medians.values()) > 30


def test_CSR_v0_6_의_61퍼센트는_어느_정의에서도_재현되지_않는다(result):
    """CSR §3.4 는 median 61% · range 11–100% · 11/23 을 보고한다.

    빌드 세션에서 (분모 창 4가지) × (로그/트래커) × (상한 유무) × (고정 8·12주)
    16가지를 전수로 돌려 보았고 **어느 것도 61% 를 내지 않았다.** 이 사실이
    이 툴의 존재 이유다 — 정의가 문서에 적혀 있지 않으면 되돌릴 수 없다.
    """
    for row in result.sensitivity:
        assert round(row["median"]) != 61


def test_전체_판정은_치명_2건_종료코드_1(result):
    from doseaudit.audit import LEVEL_CRITICAL
    assert result.count(LEVEL_CRITICAL) == 2
    assert result.exit_code == 1


def test_산출물에_실명도_LoginID_도_없다(result, tmp_path):
    """트래커 첫 열은 실명이고, 워크북 파일명 가운데 토큰은 Login ID 다.

    실제 값을 이 파일에 적을 수 없으므로, **원본에서 읽어 와서** 대조한다 —
    저장소에는 해시도 이름도 남지 않는다.
    """
    import os
    import re

    from doseaudit.artifacts import write_all
    from doseaudit.report import render_console
    from doseaudit.xlsxread import read_workbook

    # 트래커 원본에서 실명과 Login ID 를 직접 읽어 온다(대조용, 저장하지 않는다).
    _sheet, rows = read_workbook(REAL_TRACKER)[0]
    header = [c.strip() for c in rows[0][1]]
    name_idx = header.index("환자명")
    login_idx = header.index("Login ID")
    secrets = set()
    for _row_no, cells in rows[1:]:
        for idx in (name_idx, login_idx):
            if idx < len(cells) and cells[idx].strip():
                secrets.add(cells[idx].strip())
    # 워크북 파일명의 가운데 토큰(Login ID)도 모은다.
    for filename in os.listdir(REAL_LOGS):
        parts = os.path.splitext(filename)[0].split("_")
        if len(parts) >= 3:
            secrets.add(parts[1])
    assert len(secrets) >= N_WORKBOOKS

    out = str(tmp_path / "out")
    os.makedirs(out)
    names = write_all(result, out, render_console(result))
    for artifact in names:
        with open(os.path.join(out, artifact), encoding="utf-8-sig") as fh:
            text = fh.read()
        for secret in secrets:
            assert secret not in text, "%s 에 개인식별 값이 새어 나왔습니다" % artifact
        # 한글 성명 패턴이 남아 있지 않은지도 본다(모듈명·리포트 어휘는 제외).
        assert not re.search(r"[가-힣]{2,4}\s*\(\d{4}", text)


def test_불일치_목록에_23명만_들어간다(result):
    from doseaudit.artifacts import tracker_rows
    _header, rows = tracker_rows(result)
    assert len(rows) == N_TRACKER_MISMATCH


def test_피험자별노출량은_30행이다(result):
    from doseaudit.artifacts import exposure_rows
    _header, rows = exposure_rows(result)
    assert len(rows) == N_WORKBOOKS


def test_리포트가_실데이터에서도_자백을_낸다(result):
    from doseaudit.report import CONFESSION_HEADER, render_console
    assert CONFESSION_HEADER in "\n".join(render_console(result))
