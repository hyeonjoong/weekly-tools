"""Missing-DOI recovery: search Crossref for a reference that cites no DOI.

The bar here is deliberately high. An unverifiable reference is an annoyance; a
*confidently wrong* DOI suggestion is a defect the author may paste straight
into the manuscript. These tests pin the conservatism as much as the feature.
"""

import pytest

from citecheck.core import (
    OK,
    SUGGEST_OVERLAP_THRESHOLD,
    SUGGEST_TITLE_THRESHOLD,
    CrossrefClient,
    WARNING,
    _bibliographic_query,
    _score_candidate,
    check_reference,
)
from citecheck.parsers import Reference

TITLE = "Sleep as a transdiagnostic node in BELL disorders"

HIT = {
    "DOI": "10.1371/journal.pone.0312345",
    "title": [TITLE],
    "author": [{"family": "Kim", "given": "Hyeon"}],
    "issued": {"date-parts": [[2024, 5, 1]]},
    "container-title": ["PLOS ONE"],
}


def client(items, **kw):
    return CrossrefClient(
        _fetch=lambda d: None, _resolve=lambda d: False, _search=lambda q: items, **kw
    )


def suggest(ref, items):
    return check_reference(ref, client(items), suggest_missing=True)


def suggestion_of(res):
    return next((f.message for f in res.findings if "confident match" in f.message), None)


# --- the happy path ---------------------------------------------------------


def test_structured_reference_gets_its_doi_suggested():
    ref = Reference(raw="", title=TITLE, author="Kim", year=2024, journal="PLOS ONE")
    msg = suggestion_of(suggest(ref, [HIT]))
    assert msg is not None
    assert "10.1371/journal.pone.0312345" in msg
    assert "Kim (2024)" in msg  # the match is shown so the author can eyeball it


def test_free_text_reference_gets_its_doi_suggested():
    """The dominant real case: a reference list pasted out of Word, no DOIs."""
    ref = Reference(
        raw=f"Kim H, Lee S. {TITLE}. PLOS ONE. 2024;19:e0312345.",
        year=2024,
        author="Kim",
        heuristic_fields=True,
    )
    assert "10.1371/journal.pone.0312345" in suggestion_of(suggest(ref, [HIT]))


def test_suggestion_is_off_by_default():
    """No opt-in => no search at all (it costs an extra Crossref call per ref)."""
    calls = []
    c = CrossrefClient(
        _fetch=lambda d: None,
        _resolve=lambda d: False,
        _search=lambda q: calls.append(q) or [HIT],
    )
    res = check_reference(Reference(raw="", title=TITLE, year=2024), c)
    assert calls == []
    assert suggestion_of(res) is None


def test_best_candidate_wins_over_a_weaker_one():
    weak = dict(HIT, DOI="10.1/weak", title=["Sleep as a transdiagnostic node in OTHER disorders"])
    ref = Reference(raw="", title=TITLE, year=2024)
    assert "10.1371/journal.pone.0312345" in suggestion_of(suggest(ref, [weak, HIT]))


# --- the conservatism that matters ------------------------------------------


def test_a_different_paper_is_not_suggested():
    other = dict(HIT, DOI="10.1/other", title=["Metformin dosing in type 2 diabetes"])
    ref = Reference(raw="", title=TITLE, year=2024)
    assert suggestion_of(suggest(ref, [other])) is None


def test_year_disagreement_is_a_hard_reject_even_on_an_identical_title():
    """Crossref happily returns a same-titled work from another year (a later
    edition, a conference/journal pair). That is the confusion we must not add."""
    ref = Reference(raw="", title=TITLE, year=1999)
    assert _score_candidate(ref, HIT) == 0.0
    assert suggestion_of(suggest(ref, [HIT])) is None


def test_year_only_on_one_side_does_not_reject():
    assert _score_candidate(Reference(raw="", title=TITLE), HIT) > 0
    assert _score_candidate(Reference(raw="", title=TITLE, year=2024), dict(HIT, issued={})) > 0


def test_title_just_below_threshold_is_dropped():
    ref = Reference(raw="", title="Sleep as a transdiagnostic node in BELL conditions today")
    score = _score_candidate(ref, HIT)
    assert score == 0.0  # below SUGGEST_TITLE_THRESHOLD => no guess


def test_thresholds_are_stricter_than_the_comparison_thresholds():
    """Suggesting a DOI needs more evidence than checking one the author gave."""
    assert SUGGEST_TITLE_THRESHOLD > 0.80
    assert SUGGEST_OVERLAP_THRESHOLD > 0.50


def test_bare_doi_less_stub_is_never_guessed_at():
    """Too little prose to corroborate against — silence beats a guess."""
    ref = Reference(raw="see above", heuristic_fields=True)
    assert _score_candidate(ref, HIT) == 0.0


def test_candidate_without_a_title_scores_zero():
    assert _score_candidate(Reference(raw="", title=TITLE), {"DOI": "10.1/x"}) == 0.0


def test_candidate_without_a_doi_is_not_suggested():
    ref = Reference(raw="", title=TITLE, year=2024)
    assert suggestion_of(suggest(ref, [{k: v for k, v in HIT.items() if k != "DOI"}])) is None


def test_subtitle_is_considered_when_the_citation_includes_it():
    rec = dict(HIT, title=["Sleep and BELL"], subtitle=["a cohort study"])
    ref = Reference(raw="", title="Sleep and BELL: a cohort study", year=2024)
    assert _score_candidate(ref, rec) >= SUGGEST_TITLE_THRESHOLD


# --- failure handling -------------------------------------------------------


def test_search_failure_is_inconclusive_not_a_clean_pass():
    def boom(q):
        raise OSError("crossref down")

    c = CrossrefClient(_fetch=lambda d: None, _resolve=lambda d: False, _search=boom)
    res = check_reference(
        Reference(raw="", title=TITLE, year=2024), c, suggest_missing=True
    )
    assert res.status == WARNING
    assert any(f.message.startswith("Lookup failed") for f in res.findings)


@pytest.mark.parametrize("items", [[], None, "not a list", [None], ["a string"], [[]]])
def test_malformed_search_results_never_raise(items):
    ref = Reference(raw="", title=TITLE, year=2024)
    res = check_reference(ref, client(items), suggest_missing=True)
    assert suggestion_of(res) is None
    assert res.status == WARNING  # still "no DOI found"


def test_a_reference_that_has_a_doi_is_never_searched():
    calls = []
    c = CrossrefClient(
        _fetch=lambda d: HIT,
        _resolve=lambda d: True,
        _search=lambda q: calls.append(q) or [],
    )
    check_reference(
        Reference(raw="", doi="10.1371/journal.pone.0312345", title=TITLE, author="Kim", year=2024),
        c,
        suggest_missing=True,
    )
    assert calls == []


# --- the query we send ------------------------------------------------------


def test_query_uses_structured_fields_when_present():
    q = _bibliographic_query(
        Reference(raw="ignored raw text", title=TITLE, author="Kim", year=2024, journal="PLOS ONE")
    )
    assert TITLE in q and "Kim" in q and "PLOS ONE" in q and "2024" in q
    assert "ignored raw text" not in q


def test_query_falls_back_to_the_raw_line_for_free_text():
    ref = Reference(raw="Kim H, Lee S. Some paper. PLOS ONE. 2024.", heuristic_fields=True)
    assert _bibliographic_query(ref).startswith("Kim H, Lee S.")


def test_query_is_bounded_and_whitespace_collapsed():
    ref = Reference(raw="word " * 5000)
    q = _bibliographic_query(ref)
    assert len(q) <= 500
    assert "  " not in q


def test_query_collapses_newlines_in_a_wrapped_title():
    q = _bibliographic_query(Reference(raw="", title="A long\n  wrapped   title"))
    assert q == "A long wrapped title"


def test_empty_reference_makes_no_search():
    calls = []
    c = CrossrefClient(
        _fetch=lambda d: None, _resolve=lambda d: False, _search=lambda q: calls.append(q) or []
    )
    check_reference(Reference(raw=""), c, suggest_missing=True)
    assert calls == []


# --- caching of searches ----------------------------------------------------


def test_repeated_searches_hit_the_transport_once():
    calls = []
    c = CrossrefClient(
        _fetch=lambda d: None, _resolve=lambda d: False, _search=lambda q: calls.append(q) or [HIT]
    )
    for _ in range(3):
        check_reference(Reference(raw="", title=TITLE, year=2024), c, suggest_missing=True)
    assert len(calls) == 1


# --- NEVER recommend citing a retracted paper -------------------------------
#
# The worst bug this tool has had. `_suggest_doi` skipped the retraction check
# entirely, and because `_crossref_title_candidates` strips the publisher's
# "RETRACTED: " marker, the one visual cue was deleted too. Live result:
#   "Crossref has a 100%-confident match — consider citing DOI 10.1016/s0140-6736(97)11096-0"
# i.e. the tool actively recommended Wakefield 1998, and exited 0.

from citecheck.core import ERROR  # noqa: E402

WAKEFIELD_HIT = {
    "DOI": "10.1016/s0140-6736(97)11096-0",
    "type": "journal-article",
    "title": [
        "RETRACTED: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
        "and pervasive developmental disorder in children"
    ],
    "author": [{"family": "Wakefield"}],
    "issued": {"date-parts": [[1998]]},
    "updated-by": [{"DOI": "10.1016/s0140-6736(10)60175-4", "type": "retraction"}],
}

WAKEFIELD_REF = Reference(
    raw="Wakefield AJ, Murch SH. Ileal-lymphoid-nodular hyperplasia, non-specific "
        "colitis, and pervasive developmental disorder in children. Lancet. 1998;351:637-641.",
    title="Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and "
          "pervasive developmental disorder in children",
    author="Wakefield",
    year=1998,
)


def test_a_retracted_match_is_an_error_not_a_recommendation():
    res = suggest(WAKEFIELD_REF, [WAKEFIELD_HIT])
    assert res.status == ERROR
    msg = next(f.message for f in res.findings if "RETRACTED" in f.message)
    assert "consider citing" not in msg
    assert "do not cite it without checking" in msg
    assert "10.1016/s0140-6736(97)11096-0" in msg  # still named, for lookup


def test_a_retracted_match_carries_the_retracted_code():
    res = suggest(WAKEFIELD_REF, [WAKEFIELD_HIT])
    assert "retracted" in {f.code for f in res.findings}
    assert "doi-suggestion" not in {f.code for f in res.findings}


def test_a_retracted_match_is_caught_by_the_title_marker_alone():
    """Crossmark is often absent; the "RETRACTED: " title prefix is the only
    signal. It must survive into the suggestion path."""
    title_only = {k: v for k, v in WAKEFIELD_HIT.items() if k != "updated-by"}
    res = suggest(WAKEFIELD_REF, [title_only])
    assert res.status == ERROR


def test_search_asks_crossref_for_the_retraction_fields(monkeypatch):
    """A `select` that omits `updated-by` makes a candidate's retraction
    invisible no matter how careful the scoring is.

    Asserted against the URL actually built, NOT against the function's source:
    a source-text check passes on the strength of the explanatory comment above
    the select, which is exactly the kind of test that let `update-by` live for
    three hardening rounds.
    """
    import urllib.parse
    import urllib.request

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        raise urllib.error.URLError("stop here — we only want the URL")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = CrossrefClient(retries=0)
    with pytest.raises(Exception):
        c._search_network("some reference")

    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    selected = set(query["select"][0].split(","))
    for field in ("updated-by", "relation", "type", "DOI", "title", "author", "issued"):
        assert field in selected, f"search select drops {field!r} — retractions go unseen"


HINDAWI_NOTICE = {
    # A Hindawi/MDPI retraction NOTICE is titled "Retracted: <original title>".
    # Stripped of its marker it is title-identical to the paper it retracts, so
    # it matches perfectly and would be suggested as if it were the paper.
    "DOI": "10.1155/2024/9893472",
    "type": "journal-article",
    "title": ["Retracted: MicroRNA-133b Inhibition Restores EGFR Expression and "
              "Accelerates Diabetes-Impaired Wound Healing"],
    "issued": {"date-parts": [[2024]]},
}


def test_a_retraction_notice_is_never_suggested_as_the_paper():
    ref = Reference(
        raw="MicroRNA-133b Inhibition Restores EGFR Expression and Accelerates "
            "Diabetes-Impaired Wound Healing",
        title="MicroRNA-133b Inhibition Restores EGFR Expression and Accelerates "
              "Diabetes-Impaired Wound Healing",
        year=2024,
    )
    res = suggest(ref, [HINDAWI_NOTICE])
    assert suggestion_of(res) is None  # never "consider citing" the notice
    assert res.status == ERROR


def test_a_clean_match_is_still_recommended_normally():
    """The guard must not break the feature it protects."""
    res = suggest(Reference(raw="", title=TITLE, author="Kim", year=2024), [HIT])
    assert res.status == WARNING
    assert "consider citing" in suggestion_of(res)


def test_a_retracted_candidate_loses_to_nothing_rather_than_being_swapped():
    """If the only good match is retracted, we report that — we must not quietly
    fall through to a worse, non-retracted candidate."""
    unrelated = dict(HIT, DOI="10.1/other", title=["Something else entirely here"])
    res = suggest(WAKEFIELD_REF, [WAKEFIELD_HIT, unrelated])
    assert res.status == ERROR
    assert suggestion_of(res) is None
