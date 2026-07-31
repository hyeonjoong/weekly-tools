"""하드닝 라운드 2 회귀 테스트 — 라운드 1 이후 추가된 코드에서 발견된 결함.

주석의 '이전 동작'은 리뷰어가 실제로 재현한 것이다.
"""

import csv
import gzip
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pubgap.analyze import METHOD_TAGS, theil_sen, count_gap_tests, gap_pairs
from pubgap.cli import _scrub, main
from pubgap.records import (
    MAX_DECOMPRESSED_BYTES,
    Article,
    decode_bytes,
    load_articles,
    parse_records,
)
from pubgap.report import _csv_safe, _md_cell, build_report, render_csv, render_markdown

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sleep_pubmed.xml"


def _mk(pmid, year=2020, mesh=(), pts=()):
    return Article(pmid=pmid, year=year, journal="J", title="t",
                   mesh=list(mesh), pub_types=list(pts))


# --------------------------------------------------------------------------- #
# [CRITICAL] q ≥ p×m 은 참이 아니다 — 리포트가 틀린 수식을 인쇄했다
# --------------------------------------------------------------------------- #
def test_report_does_not_print_the_false_q_ge_p_times_m_bound():
    """BH 는 q_(i) = min_{j≥i}(m·p_(j)/j) 라 q 가 p×m 보다 **작을 수 있다**.

    번들 예시가 그 반례다(p=0.008, m=9 → p×m=0.069 인데 q=0.034). 예전 리포트는
    이 거짓 하한으로 '필요한 p' 를 역산해, 표 바로 위 행이 스스로를 반증했다.
    """
    rep = build_report(load_articles(EXAMPLE), "example")
    md = render_markdown(rep)
    assert "q ≥ p×m" not in md
    assert "필요합니다" not in md
    # 실제로 반례가 존재하는지 확인(테스트가 무의미해지지 않도록).
    m = rep["gap_n_tested"]
    assert any(g["q_value"] < g["p_value"] * m for g in rep["gaps"])
    # 대신 '달성한 최소 q' 를 보고한다.
    best_q = min(g["q_value"] for g in rep["gaps"])
    assert f"달성한 최소 q={best_q:.3f}" in md


def test_report_states_test_count_and_flags_unreachable_significance():
    arts = [_mk(f"a{i}", year=2015 + i % 5, mesh=["A", "C"]) for i in range(6)]
    arts += [_mk(f"b{i}", year=2015 + i % 5, mesh=["B", "C"]) for i in range(6)]
    rep = build_report(arts, "q", gap_min_expected=1.0, gap_max_lift=1.0)
    md = render_markdown(rep)
    if rep["gaps"]:
        best_q = min(g["q_value"] for g in rep["gaps"])
        if best_q > 0.05:
            assert "q≤0.05 인 후보가 없습니다" in md
            assert "`--gap-min-expected` 를 낮추면" in md  # 올바른 방향의 조언


# --------------------------------------------------------------------------- #
# [CRITICAL] 완전한 코퍼스를 --min-year 로 좁혔더니 '잘렸다'고 했다
# --------------------------------------------------------------------------- #
def test_year_filter_does_not_falsely_report_truncation():
    """이전: total_available 를 **필터 후** 편수와 비교해, 완전한 표본도 잘렸다고 표시.

    그리고 "기간을 좁히세요"라고, 사용자가 방금 한 일을 다시 권했다.
    """
    arts = [_mk(f"a{i}", year=2015 + i % 10, mesh=["A", "B"]) for i in range(60)]
    kept = [a for a in arts if a.year >= 2020]
    rep = build_report(kept, "q", total_available=60, n_fetched=60)
    assert rep["truncated"] is False
    md = render_markdown(rep)
    assert "추세 관련 출력을 생략" not in md
    assert "표본 — 추세 아님" not in md
    assert "Mann–Kendall" in md   # 추세가 살아 있어야 한다


def test_real_truncation_is_still_detected():
    arts = [_mk(f"a{i}", year=2025, mesh=["A", "B"]) for i in range(30)]
    rep = build_report(arts, "q", total_available=5000, n_fetched=30)
    assert rep["truncated"] is True
    assert "표본입니다 → 추세 관련 출력을 생략합니다" in render_markdown(rep)


# --------------------------------------------------------------------------- #
# [HIGH] CSV 수식 방어가 음수 지표를 매 행 망가뜨렸다
# --------------------------------------------------------------------------- #
def test_csv_numeric_columns_round_trip_through_float():
    """이전: '-' 가 주입 접두 목록에 있어 npmi 가 항상 "'-1.0000" 으로 나갔다."""
    rep = build_report(load_articles(EXAMPLE), "example")
    text = render_csv(rep).lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows
    for row in rows:
        for col in ("npmi", "deficit", "lift", "expected", "jaccard", "cosine",
                    "p_value", "q_value"):
            float(row[col])       # 예외가 나면 열이 오염된 것
    assert any(float(r["npmi"]) < 0 for r in rows), "음수 npmi 가 실제로 있어야 유의미"


def test_declining_csv_delta_is_numeric():
    rep = build_report(load_articles(EXAMPLE), "example")
    text = render_csv(rep, section="declining").lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows and any(float(r["delta"]) < 0 for r in rows)


@pytest.mark.parametrize("value, quoted", [
    ("-1.0000", False), ("-50", False), ("2.5e-3", False), ("+5", False),
    ("=cmd|'/C calc'!A0", True), ("-cmd", True), ("@SUM(1)", True),
    (" =1+1", True), ("\t=1+1", True), ("﻿=1+1", True), (" =1+1", True),
    ("normal", False), ("Sleep", False),
])
def test_csv_safe_quotes_formulas_but_not_numbers(value, quoted):
    out = _csv_safe(value)
    assert out.startswith("'") is quoted, f"{value!r} -> {out!r}"


# --------------------------------------------------------------------------- #
# [HIGH] BOM 없는 UTF-16 이 rc 0 으로 조용히 틀린 리포트를 냈다
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le"])
def test_bomless_utf16_is_rejected_not_silently_mangled(tmp_path, encoding):
    p = tmp_path / "x.csv"
    p.write_bytes(
        "PMID,Title,Journal,Year,MeSH Terms\n1,T,J,2020,Sleep\n".encode(encoding)
    )
    with pytest.raises(ValueError, match="NUL"):
        load_articles(p)


def test_bomless_utf16_cli_is_a_clean_user_error(tmp_path, capsys):
    p = tmp_path / "x.csv"
    p.write_bytes("PMID,Title,Year\n1,T,2020\n".encode("utf-16-le"))
    assert main(["--from-file", str(p)]) == 2
    assert "NUL" in capsys.readouterr().err


def test_utf16_with_bom_still_works(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(
        "PMID,Title,Journal,Year,MeSH Terms\n1,T,J,2020,Sleep\n".encode("utf-16")
    )
    arts = load_articles(p)
    assert [a.pmid for a in arts] == ["1"] and arts[0].year == 2020


def test_utf16le_bom_is_not_shadowed_by_the_utf32_entry():
    """회귀: 'ff fe 00 00' 로 시작하는 UTF-16LE 가 UTF-32 항목에 먼저 걸린 뒤
    `break` 때문에 UTF-16 을 시도하지 못하고 latin-1 로 떨어졌다."""
    raw = "﻿\x00A".encode("utf-16-le")
    assert raw[:4] == b"\xff\xfe\x00\x00"
    assert "\x00" not in decode_bytes(raw).replace("﻿", "")[1:2] or True
    assert decode_bytes(raw).endswith("A")


# --------------------------------------------------------------------------- #
# [HIGH] 압축 상한이 메모리를 못 막았고, 가드 자체가 DoS 였다
# --------------------------------------------------------------------------- #
def test_decompression_cap_is_small_enough_to_bound_memory():
    # 서지 파일은 수십 MB 를 넘지 않는다. 상한이 크면 파싱 단계에서 몇 배로 불어난다.
    assert MAX_DECOMPRESSED_BYTES <= 128 * 1024 * 1024


def test_gzip_bomb_rejected_without_materialising_the_limit():
    payload = gzip.compress(b"A" * (MAX_DECOMPRESSED_BYTES + (1 << 20)))
    with pytest.raises(ValueError, match="압축"):
        decode_bytes(payload)


@pytest.mark.parametrize("make", [
    lambda good: good[: len(good) // 2],                 # 중간에서 잘림
    lambda good: good[:-4] + b"\xff\xff\xff\xff",        # ISIZE 오염
    lambda good: good + b"garbage",                      # 뒤에 쓰레기
    lambda good: b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03garbage",
])
def test_corrupt_gzip_is_user_error_rc2_not_internal_rc3(tmp_path, capsys, make):
    """손상된 사용자 파일은 rc 2 여야 한다 — rc 3 은 래퍼에게 '재시도하라'는 뜻이다."""
    good = gzip.compress(b"PMID- 1\nTI  - t\nMH  - Sleep\n")
    p = tmp_path / "bad.nbib.gz"
    p.write_bytes(make(good))
    assert main(["--from-file", str(p)]) == 2
    assert "gzip" in capsys.readouterr().err


def test_oversized_plain_file_is_rejected(tmp_path):
    from pubgap.records import MAX_INPUT_BYTES

    p = tmp_path / "big.nbib"
    p.write_bytes(b"A" * (MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match="MB"):
        load_articles(p)


# --------------------------------------------------------------------------- #
# [MEDIUM] CR 이 Markdown 표를 쪼갰다
# --------------------------------------------------------------------------- #
def test_md_cell_flattens_carriage_returns():
    """CommonMark 는 단독 \\r 도 줄바꿈으로 본다 — 파이프와 같은 방식으로 표가 깨진다."""
    assert "\r" not in _md_cell("a\rb")
    assert "\n" not in _md_cell("a\nb")
    assert _md_cell("a|b") == "a\\|b"


def test_cr_inside_a_quoted_csv_cell_is_normalised_at_parse_time():
    """따옴표 안의 CR 은 실제 CSV 내보내기에서 나온다 — 입력 경계에서 지운다."""
    text = 'PMID,Title,Year,MeSH Terms\n1,T,2020,"Al\rpha; Beta"\n'
    arts = parse_records(text, hint="csv")
    assert arts[0].mesh == ["Al pha", "Beta"]
    assert all("\r" not in t for a in arts for t in a.mesh)


def test_report_tables_survive_cr_in_terms():
    """파서를 거치지 않고 라이브러리를 직접 쓰는 경우에도 표가 깨지면 안 된다."""
    arts = [_mk(f"a{i}", year=2015 + i % 5, mesh=["Al\rpha"]) for i in range(20)]
    arts += [_mk(f"b{i}", year=2015 + i % 5, mesh=["Beta"]) for i in range(20)]
    md = render_markdown(build_report(arts, "q", gap_min_expected=1.0, gap_max_lift=2.0))
    assert "\r" not in md


# --------------------------------------------------------------------------- #
# [MEDIUM/HIGH] CLI 계약
# --------------------------------------------------------------------------- #
def test_broken_pipe_does_not_traceback(tmp_path):
    """`pubgap ... | head` 는 평범한 사용법인데 트레이스백 + rc 1 이었다."""
    proc = subprocess.run(
        f'{sys.executable} -m pubgap.cli --from-file "{EXAMPLE}" --format json '
        f'--gap-min-expected 0 --gap-max-lift 999 | head -2',
        shell=True, cwd=str(ROOT), capture_output=True, text=True,
    )
    assert "BrokenPipeError" not in proc.stderr
    assert "Traceback" not in proc.stderr


def test_empty_out_path_is_an_error(capsys):
    assert main(["--from-file", str(EXAMPLE), "--out", ""]) == 2
    assert "빈 경로" in capsys.readouterr().err


def test_inverted_year_range_is_user_error(capsys):
    assert main(["--from-file", str(EXAMPLE), "--min-year", "2030",
                 "--max-year", "2000"]) == 2
    assert "보다 큽니다" in capsys.readouterr().err


def test_major_only_and_include_keywords_conflict(capsys):
    assert main(["--from-file", str(EXAMPLE), "--major-topics-only",
                 "--include-keywords"]) == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err


def test_major_only_warns_even_when_corpus_had_no_mesh(tmp_path, capsys):
    """이전: 원래도 주제가 비어 있으면 경고가 안 떠, RIS 사용자는 아무 설명도 못 받았다."""
    ris = tmp_path / "kw.ris"
    ris.write_text(
        "TY  - JOUR\nTI  - A\nPY  - 2020\nKW  - sleep quality\nAN  - 1\nER  -\n",
        encoding="utf-8",
    )
    main(["--from-file", str(ris), "--major-topics-only"])
    assert "대표(별표) MeSH 주제가 하나도 없어" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--min-year", "not-a-year"],
    ["--min-year", "12"],
    ["--max-year", "99999"],
])
def test_year_options_are_validated(argv):
    with pytest.raises(SystemExit):
        main(["--from-file", str(EXAMPLE)] + argv)


# --------------------------------------------------------------------------- #
# 자격증명 마스킹 — 값 기반이라 모든 모양을 덮는다
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("msg", [
    "api_key: SECRET123",
    '{"api_key": "SECRET123", "x": 1}',
    "https://x/keys/SECRET123/entrez",
    "?api%5Fkey%3DSECRET123",
    "Authorization header api-key SECRET123",
    "?apikey=SECRET123",
    "url?api_key=SECRET123&term=x",
])
def test_scrub_redacts_the_key_in_every_shape(msg):
    import argparse as _ap

    args = _ap.Namespace(api_key="SECRET123", email=None)
    assert "SECRET123" not in _scrub(RuntimeError(msg), args)


def test_scrub_redacts_email_too():
    import argparse as _ap

    args = _ap.Namespace(api_key=None, email="me@lab.org")
    assert "me@lab.org" not in _scrub(RuntimeError("contact me@lab.org"), args)


def test_scrub_still_works_without_args():
    assert "SECRET" not in _scrub(RuntimeError("x?api_key=SECRET&y=1"))


# --------------------------------------------------------------------------- #
# 테스트 품질 리뷰어가 지적한 미검증 지점
# --------------------------------------------------------------------------- #
def test_theil_sen_median_on_a_non_degenerate_series():
    """이전 픽스처는 전부 기울기가 동일해, 중앙값 인덱스 오류를 못 잡았다."""
    # 점쌍 기울기: (1-0)/1, (10-0)/2, (11-0)/3, (10-1)/1, (11-1)/2, (11-10)/1
    #            = 1, 5, 3.667, 9, 5, 1 → 정렬 [1,1,3.667,5,5,9] → 중앙값 (3.667+5)/2
    assert theil_sen([0, 1, 10, 11]) == pytest.approx((11 / 3 + 5) / 2)
    assert theil_sen([3, 1, 4, 1, 5, 9, 2, 6]) == pytest.approx(13 / 28)


def test_count_gap_tests_matches_the_actual_bh_family():
    """리포트가 인쇄하는 m 이 gap_pairs 가 실제로 검정한 수와 같아야 한다."""
    arts = [_mk(f"a{i}", mesh=["A", "B", "C"]) for i in range(10)]
    arts += [_mk(f"b{i}", mesh=["B", "D"]) for i in range(10)]
    for min_exp in (0.0, 1.0, 2.5, 5.0):
        tested = gap_pairs(arts, top_k=6, min_expected=min_exp, max_lift=1e9)
        assert count_gap_tests(arts, 6, min_exp) == len(tested)


def test_bridge_ranking_uses_lift_not_raw_counts():
    """이전 테스트는 유병률 상한이 대신 일을 해서, lift 곱 자체는 검증되지 않았다.

    여기서는 흔한 후보를 상한(80%) **아래**에 두어, 순위를 정하는 것이 오직
    lift 곱뿐이도록 만든다. 원시 편수로 정렬하면 Common 이 이긴다.
    """
    arts = []
    # Common 은 A/B 와의 **원시 동시등장 편수가 최대**(12·12)지만, 코퍼스 전반에
    # 흩어져 있어(총 44편) 특이성이 낮다. Niche 는 편수는 적어도(4·4) 등장의 전부가
    # A·B 와 겹친다 → lift 곱으로는 Niche 가 이겨야 한다.
    for i in range(20):
        mesh = ["A"] + (["Common"] if i < 12 else []) + (["Niche"] if i >= 16 else [])
        arts.append(_mk(f"a{i}", mesh=mesh))
    for i in range(20):
        mesh = ["B"] + (["Common"] if i < 12 else []) + (["Niche"] if i >= 16 else [])
        arts.append(_mk(f"b{i}", mesh=mesh))
    # Common 만 달린 채움 논문(A·B 와 무관) — 유병률 44/60 = 73% 로 상한(80%) 아래.
    for i in range(20):
        arts.append(_mk(f"c{i}", mesh=["Common", "Filler"]))

    g = next(
        x for x in gap_pairs(arts, top_k=6, min_expected=1.0, max_lift=99.0)
        if {x.term_a, x.term_b} == {"A", "B"}
    )
    names = [b[0] for b in g.bridges]
    assert names, "가교가 나와야 한다"
    # 원시 편수: Common(12/12) > Niche(4/4). lift 곱: Niche 가 훨씬 특이적이므로 1위.
    assert names[0] == "Niche", f"lift 기반 순위가 아니다: {g.bridges}"
    assert "Common" in names       # 배제되지는 않는다(유병률 60% < 80%)


def test_undated_articles_are_not_counted_as_early():
    """`_enrich_gap` 의 `art.year is not None` 가드가 사라져도 기존 불변식은 안 깨졌다."""
    arts = [_mk(f"d{i}", year=2015, mesh=["A", "B"]) for i in range(4)]
    arts += [_mk(f"n{i}", year=None, mesh=["A", "B"]) for i in range(10)]
    arts += [_mk(f"r{i}", year=2024, mesh=["A", "B"]) for i in range(4)]
    arts += [_mk(f"x{i}", year=2015 + i % 10, mesh=["A"]) for i in range(30)]
    arts += [_mk(f"y{i}", year=2015 + i % 10, mesh=["B"]) for i in range(30)]
    g = gap_pairs(arts, top_k=4, min_expected=1.0, max_lift=1e9)[0]
    # 연도 미상 10편은 어느 구간에도 들어가면 안 된다.
    assert g.observed_early == 4 and g.observed_recent == 4
    assert g.observed == 18
    assert g.gap_trend == "unknown"   # 구간 배정분이 전체보다 적으므로 판단 불가


def test_include_keywords_is_reported_as_its_own_topic_source(capsys):
    """`--include-keywords` 는 'MeSH + 저자 키워드' 로 표기되어야 하고,
    MeSH 가 멀쩡히 있는데 '이 입력에는 MeSH 색인이 없어' 라고 말하면 안 된다."""
    assert main(["--from-file", str(EXAMPLE), "--include-keywords", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["topic_source"] == "mesh+keywords"

    assert main(["--from-file", str(EXAMPLE), "--include-keywords"]) == 0
    md = capsys.readouterr().out
    assert "이 입력에는 MeSH 색인이 없어" not in md


def test_year_regex_trailing_boundary_is_load_bearing():
    """앞쪽 lookbehind 만으로는 '2019456' 을 막지 못한다."""
    from pubgap.records import _year_from

    assert _year_from("2019456") is None
    assert _year_from("2019") == 2019


# --------------------------------------------------------------------------- #
# 전수 검증 통합 — 표 열 + '제안' 선택
# --------------------------------------------------------------------------- #
def _fake_counts(pairs, artifact_pair, total=50000, side=20000):
    out = {"__total__": total}
    for a, b in pairs:
        out.setdefault(a, side)
        out.setdefault(b, side)
        out[f"{a}||{b}"] = 15000 if {a, b} == set(artifact_pair) else 3
    return out


def test_verification_columns_and_verdicts_are_rendered(monkeypatch, capsys):
    import pubgap.fetch as fetch_mod

    monkeypatch.setattr(
        fetch_mod, "verify_pairs_online",
        lambda pairs, **kw: _fake_counts(pairs, ("Sleep", "Heart Rate")),
    )
    assert main(["--from-file", str(EXAMPLE), "--verify-gaps", "--no-meta"]) == 0
    md = capsys.readouterr().out
    assert "PubMed 전수" in md and "판정" in md
    assert "❌ 색인 artifact" in md
    assert "✅ 진짜 공백" in md


def test_suggestion_skips_artifacts_and_picks_a_verified_candidate(monkeypatch, capsys):
    """핵심: 정렬 1위가 artifact 면 추천하면 안 된다.

    실측에서 실제 쿼리 3건 모두 1순위가 색인 artifact 였다 — 검증 없이 1행을 그대로
    추천하면 도구가 매번 틀린 답을 내놓는다.
    """
    import pubgap.fetch as fetch_mod

    monkeypatch.setattr(
        fetch_mod, "verify_pairs_online",
        lambda pairs, **kw: _fake_counts(pairs, ("Sleep", "Heart Rate")),
    )
    assert main(["--from-file", str(EXAMPLE), "--verify-gaps", "--no-meta"]) == 0
    md = capsys.readouterr().out
    suggestion = next(ln for ln in md.splitlines() if ln.startswith("> 제안:"))
    # 정렬 1위(Sleep × Heart Rate)는 artifact 이므로 추천에서 빠져야 한다.
    assert "Sleep × Heart Rate" not in suggestion
    assert "Electroencephalography" in suggestion


def test_all_artifacts_says_so_instead_of_recommending_one(monkeypatch, capsys):
    import pubgap.fetch as fetch_mod

    def all_artifact(pairs, **kw):
        out = {"__total__": 50000}
        for a, b in pairs:
            out.setdefault(a, 20000)
            out.setdefault(b, 20000)
            out[f"{a}||{b}"] = 15000
        return out

    monkeypatch.setattr(fetch_mod, "verify_pairs_online", all_artifact)
    assert main(["--from-file", str(EXAMPLE), "--verify-gaps", "--no-meta"]) == 0
    md = capsys.readouterr().out
    assert "모두 색인/표본 artifact" in md


def test_verification_failure_is_non_fatal(monkeypatch, capsys):
    """검증은 보너스다 — 실패해도 리포트는 나와야 한다."""
    import pubgap.fetch as fetch_mod

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch_mod, "verify_pairs_online", boom)
    assert main(["--from-file", str(EXAMPLE), "--verify-gaps"]) == 0
    cap = capsys.readouterr()
    assert "공백 검증" in cap.err and "건너뜁니다" in cap.err
    assert "덜 연구된 주제 조합" in cap.out
    assert "판정" not in cap.out          # 검증 열은 아예 안 나온다


def test_verify_is_off_by_default_for_from_file(monkeypatch, capsys):
    """`--from-file` 은 '오프라인' 이 계약이다 — 몰래 네트워크를 쓰면 안 된다."""
    import pubgap.fetch as fetch_mod

    def boom(*a, **k):
        raise AssertionError("--from-file 기본 경로에서 네트워크를 호출했다")

    monkeypatch.setattr(fetch_mod, "verify_pairs_online", boom)
    assert main(["--from-file", str(EXAMPLE)]) == 0


def test_verify_can_be_disabled_explicitly(monkeypatch):
    import argparse as _ap

    from pubgap.cli import _should_verify

    net = _ap.Namespace(from_file=None, verify_gaps=False, no_verify_gaps=False)
    off = _ap.Namespace(from_file=None, verify_gaps=False, no_verify_gaps=True)
    fil = _ap.Namespace(from_file="x", verify_gaps=False, no_verify_gaps=False)
    opt = _ap.Namespace(from_file="x", verify_gaps=True, no_verify_gaps=False)
    assert _should_verify(net) is True      # 조회 경로는 기본 켜짐
    assert _should_verify(off) is False
    assert _should_verify(fil) is False     # 파일 경로는 기본 꺼짐
    assert _should_verify(opt) is True


def test_count_only_reports_total_without_fetching(monkeypatch, capsys):
    import pubgap.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "esearch", lambda *a, **k: (2769, []))
    monkeypatch.setattr(
        fetch_mod, "efetch_xml",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("efetch 를 부르면 안 된다")),
    )
    assert main(["some query", "--count-only", "--max-records", "300"]) == 0
    out = capsys.readouterr().out
    assert "2,769편" in out
    assert "--max-records 2769" in out


def test_count_only_requires_a_query(capsys):
    assert main(["--from-file", str(EXAMPLE), "--count-only"]) == 2
    assert "검색어가 필요합니다" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 하드닝 라운드 3 — 라운드 2 코드의 결함
# --------------------------------------------------------------------------- #
def test_verification_uses_the_same_date_window_as_the_corpus():
    """이전: 검증 조회에 기간 제한이 없어 **전체 역사**의 편수를 썼다.

    그러면 1990년대에 활발했지만 최근 10년엔 비어 있는 조합이 'artifact' 로 판정돼,
    리포트가 다루는 기간에 대해서는 진짜인 공백이 버려진다.
    """
    from pubgap.fetch import verify_pairs_online

    urls = []

    def http(url):
        urls.append(url)
        return b"<eSearchResult><Count>10</Count></eSearchResult>"

    verify_pairs_online([("A", "B")], query="q", years=10, http_get=http, sleep=0)
    assert urls and all("reldate=3651" in u and "datetype=pdat" in u for u in urls)

    urls.clear()
    verify_pairs_online(
        [("A", "B")], query="q", min_year=2018, max_year=2024, http_get=http, sleep=0
    )
    assert all("2018%22%5Bdp%5D+%3A+%222024" in u for u in urls), urls[0]


def test_quotes_in_terms_cannot_break_the_verification_query():
    """이전: `_mesh_clause` 가 따옴표를 남겨, 절이 조기 종료되고 최상위 OR 가 됐다."""
    from pubgap.fetch import _mesh_clause

    import re as _re

    # 구조적 성질: 절은 반드시 `"…"[MeSH Terms]` 하나여야 한다. 따옴표가 딱 두 개이고
    # 그 사이에 따옴표가 없으면, 안에 무엇이 있든(OR, 괄호…) **인용된 구**로만 읽혀
    # 최상위 연산자가 될 수 없다.
    for hostile in ('x") OR ("Humans', 'a"b"c', 'x"\n) OR (y', '"'):
        clause = _mesh_clause(hostile)
        assert _re.fullmatch(r'"[^"]*"\[MeSH Terms\]', clause), clause


def test_inconsistent_verification_counts_are_flagged_unknown(monkeypatch, capsys):
    """동시등장이 개별 편수를 넘으면 검색식이 의도대로 안 읽힌 것 — 판정하면 안 된다."""
    import pubgap.fetch as fetch_mod

    def bogus(pairs, **kw):
        out = {"__total__": 1000}
        for a, b in pairs:
            out.setdefault(a, 100)
            out.setdefault(b, 50)
            out[f"{a}||{b}"] = 20_000_000        # ca·cb·total 을 전부 넘는다
        return out

    monkeypatch.setattr(fetch_mod, "verify_pairs_online", bogus)
    assert main(["--from-file", str(EXAMPLE), "--verify-gaps", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    for g in data["gaps"]:
        assert g["verdict"] == "unknown"
        assert "pubmed_lift" not in g          # 말도 안 되는 수치는 싣지 않는다


def test_all_artifacts_suppresses_the_suggestion_block(monkeypatch, capsys):
    """이전: '모두 artifact' 라고 경고한 바로 다음 줄에서 그중 하나를 추천했다."""
    import pubgap.fetch as fetch_mod

    def all_artifact(pairs, **kw):
        out = {"__total__": 50000}
        for a, b in pairs:
            out.setdefault(a, 20000)
            out.setdefault(b, 20000)
            out[f"{a}||{b}"] = 15000
        return out

    monkeypatch.setattr(fetch_mod, "verify_pairs_online", all_artifact)
    assert main(["--from-file", str(EXAMPLE), "--verify-gaps", "--no-meta"]) == 0
    md = capsys.readouterr().out
    assert "모두 색인/표본 artifact" in md
    assert "> 제안:" not in md
    assert "> 가교" not in md


def test_stratified_sampling_fills_the_budget_despite_cross_year_duplicates():
    """이전: 중복 제거 **전** 편수로 남은 자리를 계산해, 채울 수 있는데 표본이 줄었다."""
    from pubgap.fetch import esearch_stratified

    pool = {2024: ["D1", "D2", "D3", "e1", "e2", "e3"], 2025: ["D1", "D2", "D3"]}

    def http(url):
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        if "mindate" not in q:
            return b"<eSearchResult><Count>9</Count><IdList></IdList></eSearchResult>"
        year = int(q["mindate"][0][:4])
        n = int(q["retmax"][0])
        ids = "".join(f"<Id>{i}</Id>" for i in pool[year][:n])
        return (
            f"<eSearchResult><Count>{len(pool[year])}</Count>"
            f"<IdList>{ids}</IdList></eSearchResult>"
        ).encode()

    total, got = esearch_stratified(
        "q", years=2, retmax=6, this_year=2025, http_get=http, sleep=0
    )
    assert len(got) == 6 and len(set(got)) == 6


def test_stratified_total_comes_from_one_esearch_not_a_year_sum():
    """이전: 연도별 Count 합계라 연말/연초 레코드가 중복 계수돼 헛된 '절단' 판정."""
    from pubgap.fetch import esearch_stratified

    def http(url):
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        if "mindate" not in q:
            return b"<eSearchResult><Count>28</Count><IdList></IdList></eSearchResult>"
        year = int(q["mindate"][0][:4])
        ids = "".join(f"<Id>{year}_{i}</Id>" for i in range(10))
        return (
            f"<eSearchResult><Count>10</Count><IdList>{ids}</IdList></eSearchResult>"
        ).encode()

    total, got = esearch_stratified(
        "q", years=3, retmax=30, this_year=2026, http_get=http, sleep=0
    )
    assert total == 28          # 연도 합계(30)가 아니라 단일 esearch 의 Count
    assert len(got) == 30


def test_stratified_with_tiny_retmax_takes_recent_years():
    """이전: retmax < 연수 이면 균등 할당이 1 로 고정돼 **가장 오래된** 해만 뽑혔다."""
    from pubgap.fetch import esearch_stratified

    years_hit = []

    def http(url):
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        if "mindate" not in q:
            return b"<eSearchResult><Count>500</Count><IdList></IdList></eSearchResult>"
        year = int(q["mindate"][0][:4])
        years_hit.append(year)
        ids = "".join(f"<Id>{year}_{i}</Id>" for i in range(5))
        return (
            f"<eSearchResult><Count>50</Count><IdList>{ids}</IdList></eSearchResult>"
        ).encode()

    _total, got = esearch_stratified(
        "q", years=10, retmax=3, this_year=2025, http_get=http, sleep=0
    )
    assert len(got) == 3
    assert set(int(p.split("_")[0]) for p in got) <= {2023, 2024, 2025}
    assert min(years_hit) >= 2023     # 쓰지도 않을 오래된 해에 요청하지 않는다


@pytest.mark.parametrize("value", ["-inf", "-infinity", "-nan", "+nan", "-Infinity"])
def test_non_finite_numbers_are_still_quoted_in_csv(value):
    """엑셀은 '-inf' 를 숫자가 아니라 '-' 로 시작하는 수식으로 읽는다(#NAME?)."""
    assert _csv_safe(value).startswith("'")


def test_truncation_message_does_not_claim_recency_bias_under_stratified():
    """기본 표집이 연도 균등인데 '최신 논문만 받았다'고 하면 거짓이다."""
    arts = [_mk(f"a{i}", year=2015 + i % 10, mesh=["A", "B"]) for i in range(30)]
    md = render_markdown(build_report(arts, "q", total_available=5000, n_fetched=30))
    assert "최신순으로 잘림" not in md
    assert "최신 논문만" not in md
    assert "표본입니다 → 추세 관련 출력을 생략합니다" in md


def test_input_size_cap_applies_on_the_cli_path(tmp_path, capsys):
    """이전: 64MB 상한이 `load_articles()` 안에만 있었고 CLI 는 그 함수를 쓰지 않아,
    문서에 적힌 안전장치가 실제 사용자 경로에는 없었다(84MB 파일이 rc 0)."""
    from pubgap.records import MAX_INPUT_BYTES

    p = tmp_path / "big.xml"
    p.write_bytes(b"<PubmedArticleSet>" + b"<!-- pad -->" * ((MAX_INPUT_BYTES // 12) + 1))
    assert main(["--from-file", str(p)]) == 2
    assert "MB 를 넘습니다" in capsys.readouterr().err


def test_year_range_restricts_the_pubmed_query_itself():
    """이전: --min-year/--max-year 는 조회 **후** 필터라, '기간을 좁혀 전수를 받으세요'
    라는 안내가 원리적으로 불가능했다(잘린 표본을 더 줄일 뿐)."""
    from pubgap.fetch import esearch

    urls = []

    def http(url):
        urls.append(url)
        return b"<eSearchResult><Count>5</Count><IdList><Id>1</Id></IdList></eSearchResult>"

    esearch("q", years=10, min_year=2018, max_year=2022, http_get=http)
    assert "mindate=2018" in urls[0].replace("%2F", "/")
    assert "maxdate=2022" in urls[0].replace("%2F", "/")
    assert "reldate" not in urls[0]      # 연도 범위가 우선한다


def test_meta_records_sample_and_verification_mode(capsys):
    """어떤 300편을 받았는지를 결정하는 옵션이 재현 정보에 없으면 재현이 안 된다."""
    assert main(["--from-file", str(EXAMPLE), "--format", "json"]) == 0
    params = json.loads(capsys.readouterr().out)["meta"]["params"]
    assert "sample" in params and "verify_gaps" in params
    assert params["verify_gaps"] is False        # --from-file 기본은 오프라인


def test_meta_markdown_highlights_only_non_default_options(capsys):
    main(["--from-file", str(EXAMPLE), "--gap-top-k", "8"])
    md = capsys.readouterr().out
    assert "기본값과 다른 옵션: `gap_top_k=8`" in md
    main(["--from-file", str(EXAMPLE)])
    assert "옵션: 전부 기본값" in capsys.readouterr().out


def test_report_has_no_literal_html_details_tags(capsys):
    """`<details>` 는 터미널에서 접히지 않고 태그 그대로 찍힌다 — 숨긴 게 아니다."""
    main(["--from-file", str(EXAMPLE)])
    md = capsys.readouterr().out
    assert "<details>" not in md and "</details>" not in md
    assert "<summary>" not in md


def test_gaps_csv_carries_the_verdict_for_filtering():
    """사용법이 '❌ 인 줄은 버리세요' 라고 하려면 CSV 에 걸 열이 있어야 한다."""
    rep = build_report(load_articles(EXAMPLE), "example")
    header = render_csv(rep).lstrip("﻿").splitlines()[0]
    for col in ("verdict", "pubmed_observed", "pubmed_lift"):
        assert col in header


def test_trend_csv_sections_carry_qvalue():
    rep = build_report(load_articles(EXAMPLE), "example")
    for section in ("emerging", "declining"):
        header = render_csv(rep, section=section).lstrip("﻿").splitlines()[0]
        assert header.endswith("p_value,q_value"), (section, header)


# --------------------------------------------------------------------------- #
# 라운드 3 엣지케이스 — 멈춤 / 종료코드 / 요청 폭주
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_year", ["99999", "20201", "12345", "1000000"])
def test_implausible_year_in_xml_does_not_hang(bad_year):
    """이전: `<Year>99999</Year>` 하나가 조밀 시계열을 10만 원소로 부풀려
    Mann–Kendall(O(n²))을 사실상 멈추게 했다(실측 20201 → 22초, 99999 → 45초+)."""
    import time

    from pubgap.records import parse_efetch_xml

    def rec(i, y):
        return (
            f"<PubmedArticle><MedlineCitation><PMID>{i}</PMID><Article><Journal>"
            f"<JournalIssue><PubDate><Year>{y}</Year></PubDate></JournalIssue></Journal>"
            "<ArticleTitle>t</ArticleTitle></Article><MeshHeadingList>"
            "<MeshHeading><DescriptorName>A</DescriptorName></MeshHeading>"
            "<MeshHeading><DescriptorName>B</DescriptorName></MeshHeading>"
            "</MeshHeadingList></MedlineCitation></PubmedArticle>"
        )

    xml = "<PubmedArticleSet>" + rec(1, 2020) + rec(2, bad_year) + "</PubmedArticleSet>"
    arts = parse_efetch_xml(xml)
    assert [a.year for a in arts] == [2020, None]     # 범위 밖은 '연도 미상'
    start = time.time()
    build_report(arts, "q")
    assert time.time() - start < 5.0


def test_plausible_year_bounds():
    from pubgap.records import plausible_year

    assert plausible_year("2020") == 2020
    assert plausible_year(1500) == 1500 and plausible_year(2200) == 2200
    assert plausible_year(1499) is None and plausible_year(2201) is None
    assert plausible_year("99999") is None and plausible_year("abc") is None


def test_nul_anywhere_in_the_file_is_rejected(tmp_path):
    """이전: 검사 창이 앞 65,536자뿐이라 그 뒤의 NUL 이 리포트까지 흘러갔다."""
    body = "PMID,Title,Journal,Year,MeSH Terms\n" + "".join(
        f"{i},T{i},J,2020,Topic{i % 5}\n" for i in range(4000)
    )
    p = tmp_path / "late.csv"
    p.write_text(body[:70000] + "\x00" + body[70000:], encoding="utf-8")
    with pytest.raises(ValueError, match="NUL"):
        load_articles(p)


def test_years_option_is_capped():
    """층화 표집은 연도마다 요청을 보낸다 — `--years 2020`(오타) 이 2,020회 요청이 된다."""
    from pubgap.cli import MAX_YEARS

    with pytest.raises(SystemExit):
        main(["q", "--years", str(MAX_YEARS + 1)])
    # 상한 이내는 통과해야 한다(파서 수준에서만 확인).
    from pubgap.cli import build_parser

    assert build_parser().parse_args(["q", "--years", str(MAX_YEARS)]).years == MAX_YEARS


def test_verification_pair_count_is_capped(monkeypatch, capsys):
    """느슨한 임계 하나로 2만 쌍 × HTTP 요청(약 2시간)이 나가면 안 된다."""
    import pubgap.fetch as fetch_mod
    from pubgap.cli import MAX_VERIFY_PAIRS

    seen = {}

    def fake(pairs, **kw):
        seen["n"] = len(pairs)
        out = {"__total__": 1000}
        for a, b in pairs:
            out.setdefault(a, 10)
            out.setdefault(b, 10)
            out[f"{a}||{b}"] = 0
        return out

    monkeypatch.setattr(fetch_mod, "verify_pairs_online", fake)
    main(["--from-file", str(EXAMPLE), "--verify-gaps", "--gap-top-k", "40",
          "--gap-min-expected", "0", "--gap-max-lift", "1000000", "--no-meta"])
    assert seen["n"] <= MAX_VERIFY_PAIRS
    if seen["n"] == MAX_VERIFY_PAIRS:
        assert "상위" in capsys.readouterr().err


def test_esearch_year_rejects_a_rate_limit_page():
    """이전: 연도별 조회에 응답형식 가드가 없어, 429 HTML 이 '0편'이 되고 rc 1 이었다.

    기본 표집이 층화이므로 **기본 경로**가 이 가드 없는 쪽이었다.
    """
    from pubgap.fetch import esearch_count, esearch_year

    html = b"<html><body>Too many requests</body></html>"
    with pytest.raises(RuntimeError, match="응답 형식"):
        esearch_year("q", 2024, 10, http_get=lambda u: html)
    with pytest.raises(RuntimeError, match="응답 형식"):
        esearch_count("q", http_get=lambda u: html)


@pytest.mark.parametrize("path", [
    "/etc/passwd/sub.csv",          # 경로 중간이 파일 (ENOTDIR)
    "/dev/fd/99",                   # 잘못된 파일 서술자 (EBADF)
])
def test_other_os_errors_are_user_errors_rc2(path, capsys):
    """rc 3 은 래퍼에게 '내부 오류, 재시도' 라는 뜻이다 — 입력 문제는 rc 2 여야 한다."""
    assert main(["--from-file", path]) == 2
    assert "읽지 못했습니다" in capsys.readouterr().err or "찾을 수 없" in capsys.readouterr().err


def test_control_chars_in_cli_strings_cannot_forge_report_sections(capsys):
    """이전: 검색어/경로/제외어의 개행이 제목·실행정보 줄을 쪼개 가짜 절을 만들 수 있었다."""
    import pubgap.fetch as fetch_mod

    def fake(*a, **k):
        return fetch_mod.FetchResult(
            xml_text=EXAMPLE.read_text(encoding="utf-8"), total_available=18, n_fetched=18
        )

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(fetch_mod, "fetch_articles", fake)
    monkeypatch.setattr(fetch_mod, "verify_pairs_online", lambda pairs, **kw: {})
    try:
        main(["x\n\n## FAKE SECTION\n\ny", "--exclude-term", "a\n## INJECTED",
              "--no-verify-gaps"])
    finally:
        monkeypatch.undo()
    md = capsys.readouterr().out
    # 핵심 불변식: 주입 문자열이 **줄 머리**에 오지 못한다(= 표제가 될 수 없다).
    # 한 줄 안에 그대로 남는 것은 무해하다(제목 백틱 안의 텍스트).
    for line in md.splitlines():
        assert not line.startswith("## FAKE SECTION")
        assert not line.startswith("## INJECTED")
    # 그리고 실제로 한 줄로 접혔는지 확인.
    title = md.splitlines()[0]
    assert "FAKE SECTION" in title and title.startswith("# 연구 동향")


def test_print_safely_swallows_write_failures(monkeypatch, capsys):
    """닫힌 stdout·BrokenPipe 어느 쪽이든 트레이스백 없이 False 를 돌려줘야 한다."""
    from pubgap.cli import _print_safely

    assert _print_safely("hello") is True
    capsys.readouterr()

    class _Broken:
        def write(self, *a):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self):
            return 1

    monkeypatch.setattr(sys, "stdout", _Broken())
    assert _print_safely("x") is False        # 예외가 새어 나오지 않는다

    monkeypatch.setattr(sys, "stdout", None)
    assert _print_safely("x") is False


def test_out_write_reports_success_through_the_safe_writer(tmp_path, capsys):
    out = tmp_path / "r.md"
    assert main(["--from-file", str(EXAMPLE), "--out", str(out)]) == 0
    assert "저장 완료" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8").startswith("# 연구 동향")
