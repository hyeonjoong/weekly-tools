"""불변식(property) 테스트 — 무작위 코퍼스를 쓸어 담아 '항상 참이어야 하는 것'을 고정.

hypothesis 같은 외부 의존성 없이, 고정 시드의 결정론적 스윕으로 같은 효과를 낸다
(이 저장소는 런타임 의존성 0 을 유지한다). 시드를 고정했으므로 실패는 항상 재현된다.
"""

import json
import math
import random

import pytest

from pubgap import analyze
from pubgap.records import Article
from pubgap.report import build_report, json_safe, render_csv, render_markdown
from pubgap.report import CSV_SECTIONS

TERMS = [f"Term {i}" for i in range(14)]
# 체크 태그를 어휘에 넣어야 **대상집단 축**도 이 성질검사(표 구조·JSON 유효성·
# 모든 CSV 섹션)에 함께 걸린다. 넣기 전에는 그 절이 늘 비어 통과했다.
CHECK_TAG_TERMS = ["Humans", "Male", "Female", "Adult", "Middle Aged", "Aged",
                   "Aged, 80 and over", "Child", "Animals"]
PUB_TYPES = [
    [], ["Journal Article"],
    ["Journal Article", "Randomized Controlled Trial"],
    ["Journal Article", "Observational Study"],
    ["Journal Article", "Review"],
    ["Journal Article", "Clinical Trial"],
    ["Meta-Analysis", "Randomized Controlled Trial"],
]


def _random_corpus(rng: random.Random, n_max: int = 40):
    n = rng.randint(0, n_max)
    arts = []
    for i in range(n):
        k = rng.randint(0, 5)
        year = rng.choice([None] + list(range(2012, 2027)))
        arts.append(
            Article(
                pmid=rng.choice([str(i), "?", "0"]),
                year=year,
                journal=rng.choice(["J A", "J B", "저널 | C"]),
                title="t",
                mesh=(rng.sample(TERMS, k)
                      + rng.sample(CHECK_TAG_TERMS, rng.randint(0, 4))),
                keywords=rng.sample(["kw1", "kw2"], rng.randint(0, 2)),
                pub_types=list(rng.choice(PUB_TYPES)),
            )
        )
    return arts


def _corpora(count=120, seed=20260730):
    rng = random.Random(seed)
    for _ in range(count):
        yield rng, _random_corpus(rng)


# --------------------------------------------------------------------------- #
# 공백 통계의 불변식
# --------------------------------------------------------------------------- #
def test_gap_invariants_hold_across_random_corpora():
    checked = 0
    for rng, arts in _corpora():
        gaps = analyze.gap_pairs(
            arts,
            top_k=rng.randint(0, 8),
            min_expected=rng.choice([0.0, 0.5, 2.0]),
            max_lift=rng.choice([0.5, 1.0, 5.0]),
        )
        n_topical = sum(1 for a in arts if a.mesh)
        for g in gaps:
            checked += 1
            assert 0.0 <= g.p_value <= 1.0
            assert 0.0 <= g.q_value <= 1.0
            assert g.q_value >= g.p_value - 1e-12, "q 는 p 보다 작을 수 없다"
            assert g.observed <= min(g.count_a, g.count_b)
            assert g.count_a <= n_topical and g.count_b <= n_topical
            assert g.expected == pytest.approx(g.count_a * g.count_b / n_topical)
            assert g.deficit == pytest.approx(g.expected - g.observed)
            assert 0.0 <= g.jaccard <= 1.0
            assert 0.0 <= g.cosine <= 1.0 + 1e-12
            assert -1.0 <= g.npmi <= 1.0
            assert g.observed_early + g.observed_recent <= g.observed
            assert g.gap_trend in ("empty", "closing", "widening", "stable", "unknown")
            assert g.term_a != g.term_b
            for c, ac, cb in g.bridges:
                assert c not in (g.term_a, g.term_b)
                assert ac >= analyze.BRIDGE_MIN_SUPPORT
                assert cb >= analyze.BRIDGE_MIN_SUPPORT
    assert checked > 50, "충분히 많은 공백을 실제로 검사해야 의미가 있다"


def test_gap_ordering_is_deterministic_and_a_permutation():
    for rng, arts in _corpora(count=40):
        base = analyze.gap_pairs(arts, top_k=6, min_expected=0.0, max_lift=5.0)
        keys = sorted((g.term_a, g.term_b) for g in base)
        for key in analyze.GAP_SORTS:
            resorted = analyze.sort_gaps(list(base), key)
            assert sorted((g.term_a, g.term_b) for g in resorted) == keys
            # 같은 입력을 뒤집어 넣어도 같은 순서가 나와야 한다(동률 결정론성).
            again = analyze.sort_gaps(list(reversed(base)), key)
            assert [(g.term_a, g.term_b) for g in again] == [
                (g.term_a, g.term_b) for g in resorted
            ]


def test_sort_keys_are_actually_monotone_in_their_key():
    """정렬 키가 무시돼도 통과하지 않도록, 각 키가 실제로 정렬됐는지 본다."""
    checks = {
        "lift": lambda vals: vals == sorted(vals),
        "deficit": lambda vals: vals == sorted(vals, reverse=True),
        "q": lambda vals: vals == sorted(vals),
        "expected": lambda vals: vals == sorted(vals, reverse=True),
        "npmi": lambda vals: vals == sorted(vals),
    }
    getters = {
        "lift": lambda g: g.lift, "deficit": lambda g: g.deficit,
        "q": lambda g: g.q_value, "expected": lambda g: g.expected,
        "npmi": lambda g: g.npmi,
    }
    seen_nontrivial = 0
    for _rng, arts in _corpora(count=60):
        base = analyze.gap_pairs(arts, top_k=8, min_expected=0.0, max_lift=5.0)
        if len(base) < 3:
            continue
        seen_nontrivial += 1
        for key, ok in checks.items():
            vals = [getters[key](g) for g in analyze.sort_gaps(list(base), key)]
            assert ok(vals), f"{key} 정렬이 단조가 아님: {vals}"
    assert seen_nontrivial > 10


# --------------------------------------------------------------------------- #
# 근거(설계) 통계의 불변식
# --------------------------------------------------------------------------- #
def test_evidence_profile_invariants():
    for _rng, arts in _corpora():
        prof = analyze.evidence_profile(arts)
        assert prof["n_typed"] + prof["n_unknown"] == prof["n_articles"] == len(arts)
        assert 0.0 <= prof["coverage"] <= 1.0
        assert 0.0 <= prof["interventional_share"] <= 1.0
        assert sum(t["count"] for t in prof["tiers"]) == prof["n_typed"]
        if prof["n_typed"]:
            assert sum(t["share"] for t in prof["tiers"]) == pytest.approx(1.0)
        assert prof["n_interventional"] <= prof["n_typed"]


def test_topic_evidence_invariants():
    for _rng, arts in _corpora():
        for t in analyze.topic_evidence(arts, top_k=8, min_articles=2):
            assert 0 <= t.n_interventional <= t.n_articles
            assert t.rest_n >= 0 and t.rest_interventional >= 0
            assert t.rest_interventional <= t.rest_n
            assert t.share == pytest.approx(t.n_interventional / t.n_articles)
            assert 0.0 <= t.p_value <= 1.0 and 0.0 <= t.q_value <= 1.0
            assert t.q_value >= t.p_value - 1e-12
            assert sum(t.tier_counts.values()) == t.n_articles


# --------------------------------------------------------------------------- #
# 초기하 꼬리확률의 수학적 성질
# --------------------------------------------------------------------------- #
def test_hypergeom_tail_is_monotone_and_reaches_one():
    rng = random.Random(7)
    for _ in range(300):
        N = rng.randint(1, 400)
        K = rng.randint(0, N)
        n = rng.randint(0, N)
        kmax = min(K, n)
        prev = -1.0
        for k in range(-1, kmax + 1):
            v = analyze.hypergeom_lower_tail(N, K, n, k)
            assert 0.0 <= v <= 1.0
            assert v >= prev - 1e-12, "k 에 대해 단조 비감소여야 한다"
            prev = v
        assert analyze.hypergeom_lower_tail(N, K, n, kmax) == 1.0


def test_benjamini_hochberg_invariants():
    rng = random.Random(11)
    for _ in range(200):
        m = rng.randint(0, 30)
        ps = [rng.choice([0.0, 1.0, rng.random()]) for _ in range(m)]
        qs = analyze.benjamini_hochberg(ps)
        assert len(qs) == m
        for p, q in zip(ps, qs):
            assert 0.0 <= q <= 1.0
            assert q >= p - 1e-12
        # p 순서로 정렬하면 q 는 비감소(step-up 의 단조성).
        ordered = [q for _p, q in sorted(zip(ps, qs))]
        assert all(a <= b + 1e-12 for a, b in zip(ordered, ordered[1:]))


# --------------------------------------------------------------------------- #
# 변환의 멱등성 / 불변성
# --------------------------------------------------------------------------- #
def test_transforms_are_idempotent():
    from pubgap.records import apply_include_keywords, apply_major_only

    for _rng, arts in _corpora(count=40):
        for fn in (analyze.strip_check_tags, apply_major_only, apply_include_keywords):
            once = fn(arts)
            twice = fn(once)
            assert [a.mesh for a in once] == [a.mesh for a in twice], fn.__name__
        once = analyze.drop_terms(arts, ["Term 1", "term 2"])
        assert [a.mesh for a in once] == [
            a.mesh for a in analyze.drop_terms(once, ["Term 1", "term 2"])
        ]


def test_transforms_never_mutate_input():
    for _rng, arts in _corpora(count=30):
        before = [list(a.mesh) for a in arts]
        analyze.strip_check_tags(arts)
        analyze.drop_terms(arts, ["Term 0"])
        analyze.gap_pairs(arts, top_k=6, min_expected=0.0, max_lift=5.0)
        analyze.evidence_profile(arts)
        assert [list(a.mesh) for a in arts] == before


# --------------------------------------------------------------------------- #
# 출력 형식의 불변식
# --------------------------------------------------------------------------- #
def _split_cells(line: str):
    cells, cur, i = [], "", 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if line[i] == "|":
            cells.append(cur)
            cur = ""
        else:
            cur += line[i]
        i += 1
    cells.append(cur)
    return cells


def _assert_tables_well_formed(md: str):
    header = None
    for line in md.splitlines():
        if not line.startswith("|"):
            header = None
            continue
        n = len(_split_cells(line))
        if header is None:
            header = n
        else:
            assert n == header, f"표 칸 수 불일치({n} vs {header}): {line}"


def test_reports_always_render_and_json_is_strictly_valid():
    for rng, arts in _corpora():
        rep = build_report(
            arts, "질의 | with pipe",
            gap_top_k=rng.randint(0, 8),
            gap_min_expected=rng.choice([0.0, 2.0]),
            gap_max_lift=rng.choice([0.5, 5.0]),
            gap_sort=rng.choice(analyze.GAP_SORTS),
            total_available=rng.choice([None, len(arts), len(arts) + 500]),
        )
        md = render_markdown(rep)
        assert md.startswith("# 연구 동향·공백 리포트")
        _assert_tables_well_formed(md)

        blob = json.dumps(json_safe(rep), ensure_ascii=False, allow_nan=False)
        restored = json.loads(blob)
        _assert_all_floats_finite(restored)

        for section in CSV_SECTIONS:
            text = render_csv(rep, section=section)
            assert text.startswith("﻿")
            assert len(text.lstrip("﻿").splitlines()) >= 1


def _assert_all_floats_finite(obj):
    if isinstance(obj, float):
        assert math.isfinite(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _assert_all_floats_finite(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_all_floats_finite(v)


def test_report_is_deterministic_for_the_same_input():
    for _rng, arts in _corpora(count=25):
        a = render_markdown(build_report(arts, "q"))
        b = render_markdown(build_report(list(arts), "q"))
        assert a == b
