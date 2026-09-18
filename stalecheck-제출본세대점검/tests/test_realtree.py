# -*- coding: utf-8 -*-
"""실측 회귀 — 이 툴의 척추.

2026-09-18 기획 세션이 사용자의 `논문_투고/` 전수를 해시해 센 값을 그대로 박아 둔다.
**실데이터는 저장소에 들어오지 않는다** — 여기에 등장하는 것은 숫자와 SHA-256 뿐이고,
트리가 없는 컴퓨터에서는 전부 skip 된다.

이 숫자들이 중요한 이유: 정규화가 무너지면 `정규화로 사라지는 오탐 11쌍`이 곧바로
불일치로 올라오고, 이 툴은 투고 때마다 우는 체커가 된다.
"""

import hashlib
import os

import pytest

from conftest import REAL_TREE, real_tree_only
from stalecheck import engine, scanning, verdicts
from stalecheck.normalize import content_hash, raw_hash

pytestmark = real_tree_only

#: 기획 세션이 센 16개 논문 폴더 (트리 루트 기준 상대경로)
PAPERS = (
    "01_BELL001_수면신경/_제출됨/BELL_Paper3_Sleep_7th_Domain",
    "01_BELL001_수면신경/_제출됨/임산부임상/DHJ-26-1629_리비전대응",
    "02_BELL002_청각재활/7.청력지표_인지선별_증분가치 (2026-07-23)",
    "02_BELL002_청각재활/BELL_난청인지_NHANES_최종",
    "02_BELL002_청각재활/Ototoxicity_Functional_Endpoints_ScopingReview",
    "03_기타_아이디어/논문작업/1.호흡표적관여_수면앱 (2026-07-17)/WITHDRAWN_원고_제출금지",
    "03_기타_아이디어/논문작업/10.DTx종결점_추적기간_지속성 (2026-07-23)",
    "03_기타_아이디어/논문작업/12.DTx시험_투명성감사_등록결과보고 (2026-07-23)",
    "03_기타_아이디어/논문작업/13.DTx사용적합성_다자델파이프로토콜 (2026-07-23)",
    "03_기타_아이디어/논문작업/14.DTx시험_유해사례보고_감사 (2026-07-23)",
    "03_기타_아이디어/논문작업/2.워치HRV_결측정보성 (2026-07-17)",
    "03_기타_아이디어/논문작업/4.공개수면데이터_심박변이 (2026-07-17)",
    "03_기타_아이디어/논문작업/5.청각재활앱_실사용참여궤적 (2026-07-23)",
    "03_기타_아이디어/논문작업/6.자율수면제한_처방행동격차 (2026-07-23)",
    "03_기타_아이디어/논문작업/8.DTx피벗_대조맹검_증거기준 (2026-07-23)",
    "03_기타_아이디어/논문작업/9.지각수면질_야간결정요인 (2026-07-23)",
)

PIVOT = "03_기타_아이디어/논문작업/8.DTx피벗_대조맹검_증거기준 (2026-07-23)"


def _require_paper(rel):
    """트리 루트는 이미 있다(모듈 skipif 통과). 그 안의 폴더가 없으면 **실패**다.

    여기서 skip 하면 폴더 이름이 한 글자만 바뀌어도 회귀 13건이 조용히 사라진다.
    """
    path = os.path.join(REAL_TREE, rel)
    assert os.path.isdir(path), (
        "실측 회귀 대상 폴더가 없습니다: %s\n"
        "  폴더가 옮겨졌거나 이름이 바뀌었다면 PAPERS 목록과 기대값을 함께 "
        "갱신해야 합니다(조용히 건너뛰지 않습니다)." % rel)


def _analyze(rel, include_archives=False):
    work = os.path.join(REAL_TREE, rel)
    packages = [os.path.join(work, c)
                for c, _ in scanning.find_package_candidates(work)]
    assert packages, (
        "봉투 후보를 하나도 찾지 못했습니다: %s — 이건 건너뛸 일이 아니라 "
        "봉투 인식 자체가 깨졌다는 뜻입니다." % rel)
    return engine.analyze(
        work, packages, check_siblings=False,
        exclude_pattern=None if include_archives else scanning.DEFAULT_EXCLUDE_PATTERN,
    )


def _totals(include_archives):
    pairs = same = mismatch = norm_only = 0
    ext = {}
    affected = 0
    for rel in PAPERS:
        _require_paper(rel)
        a = _analyze(rel, include_archives)
        n_mis = len(a.rows) - len(a.same_rows)
        pairs += len(a.rows)
        same += len(a.same_rows)
        mismatch += n_mis
        norm_only += len(a.normalized_only_rows)
        for row in a.rows:
            if row.verdict.label != verdicts.SAME:
                key = os.path.splitext(row.work_rel)[1].lower()
                ext[key] = ext.get(key, 0) + 1
        if n_mis:
            affected += 1
    return dict(pairs=pairs, same=same, mismatch=mismatch, norm_only=norm_only,
                raw_same=same - norm_only, raw_mismatch=mismatch + norm_only,
                ext=ext, affected=affected)


@pytest.fixture(scope="module")
def totals_archived():
    """기획 세션의 측정 조건 그대로(아카이브 폴더를 작업본에 포함)."""
    return _totals(include_archives=True)


@pytest.fixture(scope="module")
def totals_default():
    """출시 기본값(아카이브 제외)."""
    return _totals(include_archives=False)


# ------------------------------------------------- 기획서 실측값 (아카이브 포함)
def test_pair_count_is_245(totals_archived):
    assert totals_archived["pairs"] == 245


def test_raw_byte_matches_are_196(totals_archived):
    assert totals_archived["raw_same"] == 196


def test_raw_byte_mismatches_are_49(totals_archived):
    assert totals_archived["raw_mismatch"] == 49


def test_normalization_silences_exactly_11(totals_archived):
    """정규화가 지워야 하는 오탐은 정확히 11쌍이다. 더도 덜도 아니다."""
    assert totals_archived["norm_only"] == 11


def test_real_mismatches_are_38(totals_archived):
    assert totals_archived["mismatch"] == 38


def test_mismatch_extension_breakdown(totals_archived):
    assert totals_archived["ext"] == {".py": 17, ".pdf": 9, ".png": 5,
                                      ".docx": 5, ".md": 2}


def test_affected_folders_are_6_of_16(totals_archived):
    assert totals_archived["affected"] == 6
    assert len(PAPERS) == 16


def test_all_11_normalized_false_positives_are_pdf():
    """11건이 전부 PDF 였다 — 하나라도 다른 확장자면 정규화 전제가 틀린 것이다."""
    exts = set()
    for rel in PAPERS:
        _require_paper(rel)
        a = _analyze(rel, include_archives=True)
        for row in a.normalized_only_rows:
            exts.add(os.path.splitext(row.work_rel)[1].lower())
    assert exts == {".pdf"}


def test_the_196_raw_matches_are_byte_identical_on_disk(totals_archived):
    """'일치'로 분류한 쌍이 정말 디스크에서 같은 파일인지 **다시 해시해** 확인한다.

    `same_rows` 를 순회하며 라벨을 확인하면 정의상 항상 참이라 아무것도 못 잡는다.
    여기서는 툴의 판정을 버리고 원시 바이트/정규화 해시를 독립적으로 다시 센다.
    """
    checked = 0
    for rel in PAPERS:
        base = os.path.join(REAL_TREE, rel)
        a = _analyze(rel, include_archives=True)
        for row in a.same_rows:
            work = os.path.join(base, row.work_rel)
            pkg = os.path.join(base, row.pkg_rel)
            if row.raw_differs:
                assert raw_hash(work) != raw_hash(pkg), row.work_rel
            else:
                assert raw_hash(work) == raw_hash(pkg), row.work_rel
            assert content_hash(work)[0] == content_hash(pkg)[0], row.work_rel
            checked += 1
    assert checked == 196 + 11


def test_the_11_normalized_pairs_differ_only_in_metadata(totals_archived):
    """오탐 11쌍이 '정규화 후 같아졌다'는 주장을 바이트로 되짚는다.

    원시 해시는 **달라야** 하고(그래서 `diff -r` 이 운다), 정규화 해시는 **같아야**
    한다(그래서 이 툴은 조용하다). 둘 중 하나라도 어긋나면 툴의 존재 이유가 없다.
    """
    checked = 0
    for rel in PAPERS:
        base = os.path.join(REAL_TREE, rel)
        a = _analyze(rel, include_archives=True)
        for row in a.normalized_only_rows:
            work = os.path.join(base, row.work_rel)
            pkg = os.path.join(base, row.pkg_rel)
            assert raw_hash(work) != raw_hash(pkg), row.work_rel
            assert content_hash(work)[0] == content_hash(pkg)[0], row.work_rel
            assert row.verdict.label == verdicts.SAME
            checked += 1
    assert checked == 11


# ------------------------------------------------- 출시 기본값 (아카이브 제외)
def test_default_excludes_eight_archive_pairs(totals_default, totals_archived):
    """아카이브를 빼면 짝이 8쌍 줄고, 줄어든 것은 전부 '일치'였다."""
    assert totals_default["pairs"] == 237
    assert totals_archived["pairs"] - totals_default["pairs"] == 8
    assert totals_default["mismatch"] == totals_archived["mismatch"] == 38


def test_default_keeps_the_same_11_false_positives(totals_default):
    assert totals_default["norm_only"] == 11


def test_default_extension_breakdown_unchanged(totals_default):
    assert totals_default["ext"] == {".py": 17, ".pdf": 9, ".png": 5,
                                     ".docx": 5, ".md": 2}


# ------------------------------------------------- 실물 3건
@pytest.fixture(scope="module")
def pivot():
    _require_paper(PIVOT)
    return _analyze(PIVOT)


def _row(analysis, needle):
    for row in analysis.rows:
        if row.work_rel.endswith(needle):
            return row
    return None


def test_pivot_pairs_and_matches(pivot):
    assert len(pivot.rows) == 41
    assert len(pivot.same_rows) == 20


def test_pivot_criticals_are_20(pivot):
    assert len(pivot.critical_rows) == 20


def test_pivot_has_one_package_newer_warning(pivot):
    assert len(pivot.warn_rows) == 1


def test_deposit_missing_the_computed_values_block(pivot):
    """재현성 예치본이 논문의 숫자를 만들지 못한다 — 실제 사고 ①."""
    row = _row(pivot, "analysis/06_synthesis.py")
    assert row is not None
    assert row.verdict.label == verdicts.CRITICAL_STALE_PACKAGE
    assert (row.diff.added, row.diff.removed) == (29, 0)
    assert any("three values the manuscript reports" in p for p in row.diff.preview)


def test_deposit_missing_pdf_fonttype_line(pivot):
    """예치본을 돌리면 저널이 거부하는 Type 3 폰트 그림이 나온다 — 실제 사고 ②."""
    row = _row(pivot, "analysis/08_prisma_figure.py")
    assert row is not None
    assert row.verdict.label == verdicts.CRITICAL_STALE_PACKAGE
    assert (row.diff.added, row.diff.removed) == (5, 0)


def test_pdf_fonttype_line_present_in_work_absent_in_package(pivot):
    """작업본에는 있고 **예치본에는 없다** — 양쪽을 다 열어야 성립하는 주장이다."""
    row = _row(pivot, "analysis/08_prisma_figure.py")
    work = open(os.path.join(REAL_TREE, PIVOT, row.work_rel), encoding="utf-8").read()
    pkg = open(os.path.join(REAL_TREE, PIVOT, row.pkg_rel), encoding="utf-8").read()
    assert "pdf.fonttype'] = 42" in work
    assert "pdf.fonttype" not in pkg


def test_figure_in_envelope_is_one_generation_behind(pivot):
    """봉투 안 그림이 한 세대 낡았다 — 실제 사고 ③."""
    row = _row(pivot, "figures/fig5_tiered_framework.png")
    assert row is not None
    assert row.verdict.label == verdicts.CRITICAL_STALE_PACKAGE
    assert row.work_size == 599103 and row.pkg_size == 601491
    assert row.work_mtime > row.pkg_mtime


def test_figure_png_content_hashes(pivot):
    """실데이터를 담지 않고 SHA-256 으로만 고정한다."""
    row = _row(pivot, "figures/fig5_tiered_framework.png")
    work = os.path.join(REAL_TREE, PIVOT, row.work_rel)
    assert raw_hash(work).startswith("617dd233ff5292d1")


def test_synthesis_py_content_hashes(pivot):
    row = _row(pivot, "analysis/06_synthesis.py")
    work = os.path.join(REAL_TREE, PIVOT, row.work_rel)
    assert raw_hash(work).startswith("54c60c10c27a0adf")


def test_pivot_is_the_only_folder_with_21_mismatches():
    _require_paper(PIVOT)
    a = _analyze(PIVOT)
    assert len(a.rows) - len(a.same_rows) == 21


# ------------------------------------------------- 조용해야 하는 폴더
QUIET = "03_기타_아이디어/논문작업/2.워치HRV_결측정보성 (2026-07-17)"


def test_quiet_folder_has_no_criticals():
    """치명 0건이 나와야 하는 폴더. 여기서 울면 이 툴은 못 쓴다."""
    _require_paper(QUIET)
    a = _analyze(QUIET)
    assert len(a.critical_rows) == 0
    assert len(a.rows) == 30
    assert len(a.same_rows) == 30


def test_quiet_folder_exit_code_is_zero():
    _require_paper(QUIET)
    import io
    from stalecheck import cli
    work = os.path.join(REAL_TREE, QUIET)
    packages = [os.path.join(work, c)
                for c, _ in scanning.find_package_candidates(work)]
    args = ["--work", work]
    for p in packages:
        args += ["--package", p]
    out = io.StringIO()
    code = cli.main(args + ["--quiet"], stdout=out, stderr=io.StringIO())
    assert code == 0


def test_real_tree_run_does_not_modify_inputs():
    """전수 실행 후 입력 트리의 mtime·크기가 그대로인지 확인한다."""
    _require_paper(QUIET)
    work = os.path.join(REAL_TREE, QUIET)

    def snapshot():
        h = hashlib.sha256()
        for dirpath, dirnames, filenames in os.walk(work):
            dirnames.sort()
            for fn in sorted(filenames):
                p = os.path.join(dirpath, fn)
                st = os.lstat(p)
                h.update(os.path.relpath(p, work).encode("utf-8", "surrogateescape"))
                h.update(("%d|%d|%d" % (st.st_size, st.st_mtime_ns, st.st_ino)).encode())
                if os.path.isfile(p) and not os.path.islink(p):
                    # 크기·시각만 보면 같은 초 안의 같은 길이 덮어쓰기를 놓친다.
                    h.update(raw_hash(p).encode())
        return h.hexdigest()

    before = snapshot()
    _analyze(QUIET)
    assert snapshot() == before
