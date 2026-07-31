"""근거 지형(연구 설계)·근거 공백이 리포트에 실제로 실리는지 + 통계 손계산 대조.

회귀 배경: `evidence_profile`/`topic_evidence` 는 구현돼 있었지만 `build_report` 가
호출하지 않아 어떤 출력에도 나타나지 않았다(사실상 죽은 코드).
"""

import json

import pytest

from pubgap.analyze import (
    evidence_profile,
    evidence_tier,
    fisher_exact_two_sided,
    topic_evidence,
)
from pubgap.records import Article, load_articles
from pubgap.report import build_report, render_csv, render_markdown
from pubgap.cli import main

from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sleep_pubmed.xml"


def _mk(pmid, mesh, pts, year=2020):
    return Article(
        pmid=pmid, year=year, journal="J", title="t",
        mesh=list(mesh), pub_types=list(pts),
    )


# --------------------------------------------------------------------------- #
# tier 판정
# --------------------------------------------------------------------------- #
def test_tier_picks_highest_not_first():
    # PubMed 는 RCT 에 보통 세 타입을 함께 단다 — 가장 높은 tier 로 대표해야 한다.
    assert evidence_tier(["Journal Article", "Clinical Trial",
                          "Randomized Controlled Trial"]) == "rct"
    assert evidence_tier(["Review", "Meta-Analysis"]) == "systematic_review"
    assert evidence_tier(["Journal Article", "English Abstract"]) == "other"
    assert evidence_tier([]) == "other"


def test_tier_tolerates_whitespace():
    assert evidence_tier(["  Randomized Controlled Trial "]) == "rct"


# --------------------------------------------------------------------------- #
# evidence_profile — 설계 미상은 분모에서 빠진다
# --------------------------------------------------------------------------- #
def test_profile_excludes_untyped_from_denominator():
    arts = [
        _mk("1", ["A"], ["Randomized Controlled Trial"]),
        _mk("2", ["A"], ["Observational Study"]),
        _mk("3", ["A"], []),   # 설계 미상 — 분모 제외
        _mk("4", ["A"], []),
    ]
    prof = evidence_profile(arts)
    assert prof["n_articles"] == 4
    assert prof["n_typed"] == 2
    assert prof["coverage"] == pytest.approx(0.5)
    assert prof["n_interventional"] == 1
    assert prof["interventional_share"] == pytest.approx(0.5)  # 1/2, 1/4 가 아니다


def test_profile_empty_corpus_is_safe():
    prof = evidence_profile([])
    assert prof["n_typed"] == 0 and prof["coverage"] == 0.0
    assert prof["interventional_share"] == 0.0


# --------------------------------------------------------------------------- #
# topic_evidence — Fisher 2×2 를 직접 재계산해 대조
# --------------------------------------------------------------------------- #
def test_topic_evidence_matches_hand_built_2x2():
    arts = (
        [_mk(f"x{i}", ["EEG"], ["Observational Study"]) for i in range(6)]
        + [_mk(f"y{i}", ["HRV"], ["Randomized Controlled Trial"]) for i in range(6)]
    )
    rows = {t.term: t for t in topic_evidence(arts, top_k=5, min_articles=3)}
    eeg = rows["EEG"]
    assert (eeg.n_articles, eeg.n_interventional) == (6, 0)
    assert eeg.rest_n == 6 and eeg.rest_interventional == 6
    expected_p = fisher_exact_two_sided(0, 6, 6, 0)
    assert eeg.p_value == pytest.approx(expected_p)
    # 정렬: 개입비율이 낮은 주제가 먼저(구현을 다시 돌려 기대값을 만들지 않는다).
    assert [t.term for t in topic_evidence(arts, top_k=5, min_articles=3)] == ["EEG", "HRV"]
    assert eeg.share == 0.0 and rows["HRV"].share == 1.0


def test_topic_evidence_skips_thin_topics():
    arts = [_mk("1", ["A"], ["Review"]), _mk("2", ["A", "B"], ["Review"])]
    terms = [t.term for t in topic_evidence(arts, top_k=5, min_articles=3)]
    assert terms == []  # 두 주제 모두 3편 미만


def test_topic_evidence_ignores_untyped_articles():
    arts = [_mk(f"u{i}", ["A"], []) for i in range(10)]
    assert topic_evidence(arts, top_k=5) == []


def test_topic_evidence_qvalues_are_monotone_in_p():
    arts = (
        [_mk(f"a{i}", ["A", "C"], ["Randomized Controlled Trial"]) for i in range(8)]
        + [_mk(f"b{i}", ["B", "C"], ["Observational Study"]) for i in range(8)]
        + [_mk(f"c{i}", ["C"], ["Review"]) for i in range(4)]
    )
    rows = topic_evidence(arts, top_k=5, min_articles=3)
    ordered = sorted(rows, key=lambda t: t.p_value)
    qs = [t.q_value for t in ordered]
    assert qs == sorted(qs)  # BH q 는 p 순서에서 단조
    assert all(0.0 <= t.q_value <= 1.0 for t in rows)
    assert all(t.q_value >= t.p_value - 1e-12 for t in rows)


# --------------------------------------------------------------------------- #
# 리포트 통합
# --------------------------------------------------------------------------- #
def test_report_includes_evidence_sections():
    rep = build_report(load_articles(EXAMPLE), "example")
    assert rep["evidence"]["n_typed"] == 28
    assert rep["topic_evidence"], "주제별 근거 공백 행이 있어야 한다"
    md = render_markdown(rep)
    assert "근거 지형" in md
    assert "개입연구가 상대적으로 적은 주제" in md
    assert "무작위배정 임상시험(RCT)" in md


def test_report_evidence_can_be_disabled():
    rep = build_report(load_articles(EXAMPLE), "example", evidence=False)
    assert "evidence" not in rep and "topic_evidence" not in rep
    assert "근거 지형" not in render_markdown(rep)


def test_markdown_says_so_when_no_design_info():
    arts = [_mk(str(i), ["A", "B"], []) for i in range(10)]
    md = render_markdown(build_report(arts, "q"))
    assert "연구 설계 정보(PublicationType)가 없어" in md


def test_low_coverage_warning_is_shown():
    arts = [_mk("1", ["A"], ["Review"])] + [_mk(str(i), ["A"], []) for i in range(2, 10)]
    md = render_markdown(build_report(arts, "q"))
    assert "커버리지가" in md


def test_cli_evidence_csv_sections(tmp_path, capsys):
    rc = main(["--from-file", str(EXAMPLE), "--format", "csv",
               "--csv-section", "topic-evidence"])
    assert rc == 0
    out = capsys.readouterr().out.lstrip("﻿")
    assert out.splitlines()[0].startswith("term,n_articles,n_interventional")
    assert "Electroencephalography" in out


def test_cli_no_evidence_flag(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--format", "json", "--no-evidence"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "evidence" not in data


def test_evidence_csv_section_renders_tiers():
    rep = build_report(load_articles(EXAMPLE), "example")
    text = render_csv(rep, section="evidence").lstrip("﻿")
    assert text.splitlines()[0] == "tier,label,count,share"
    assert "rct" in text
