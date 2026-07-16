from citecheck.core import CrossrefClient, ERROR, OK, WARNING, check_reference
from citecheck.parsers import Reference


def make_client(records, resolves=False):
    """A CrossrefClient whose transport is a dict: doi -> message | None.

    `resolves` controls the injected doi.org handle check for DOIs not in the
    Crossref records (True => "resolves but not in Crossref").
    """
    return CrossrefClient(
        _fetch=lambda doi: records.get(doi),
        _resolve=lambda doi: resolves,
    )


GOOD = {
    "DOI": "10.1371/journal.pone.0312345",
    "title": ["Sleep as a transdiagnostic node in BELL disorders"],
    "author": [{"family": "Kim", "given": "Hyeon"}],
    "issued": {"date-parts": [[2024, 5, 1]]},
    "type": "journal-article",
}


def test_verified_reference_is_ok():
    ref = Reference(
        raw="",
        doi="10.1371/journal.pone.0312345",
        title="Sleep as a transdiagnostic node in BELL disorders",
        author="Kim",
        year=2024,
    )
    res = check_reference(ref, make_client({ref.doi: GOOD}))
    assert res.status == OK


def test_missing_doi_warns():
    res = check_reference(Reference(raw="something"), make_client({}))
    assert res.status == WARNING
    assert "No DOI" in res.findings[0].message


def test_unresolvable_doi_errors():
    ref = Reference(raw="", doi="10.9999/nope")
    res = check_reference(ref, make_client({}))  # fetch returns None
    assert res.status == ERROR
    assert "does not resolve" in res.findings[0].message


def test_title_mismatch_warns():
    ref = Reference(
        raw="",
        doi="10.1371/journal.pone.0312345",
        title="A completely unrelated paper about penguins",
    )
    res = check_reference(ref, make_client({ref.doi: GOOD}))
    assert res.status == WARNING
    assert any("Title mismatch" in f.message for f in res.findings)


def test_year_mismatch_warns():
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345", year=2019)
    res = check_reference(ref, make_client({ref.doi: GOOD}))
    assert any("Year mismatch" in f.message for f in res.findings)


def test_retraction_errors():
    retracted = dict(GOOD, type="retraction")
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345")
    res = check_reference(ref, make_client({ref.doi: retracted}))
    assert res.status == ERROR
    assert any("RETRACTED" in f.message for f in res.findings)


def test_lookup_failure_is_warning_not_error():
    def boom(doi):
        raise TimeoutError("network down")

    ref = Reference(raw="", doi="10.1371/journal.pone.0312345")
    res = check_reference(ref, CrossrefClient(_fetch=boom))
    assert res.status == WARNING
    assert "Lookup failed" in res.findings[0].message


# --- retraction detection (real Crossref shapes) ----------------------------

def test_retraction_via_update_by():
    """A retracted *article* carries `update-by`, not type=retraction."""
    msg = dict(GOOD, **{"update-by": [{"type": "retraction", "DOI": "10.1/notice"}]})
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345")
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert res.status == ERROR
    finding = next(f for f in res.findings if "RETRACTED" in f.message)
    assert "10.1/notice" in finding.message  # notice DOI surfaced


def test_retraction_via_update_to():
    msg = dict(GOOD, **{"update-to": [{"type": "retraction"}]})
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345")
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert res.status == ERROR


def test_retraction_via_relation_key():
    msg = dict(GOOD, relation={"is-retracted-by": [{"id": "10.1/x"}]})
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345")
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert res.status == ERROR


def test_plain_article_not_retracted():
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345")
    res = check_reference(ref, make_client({ref.doi: GOOD}))
    assert not any("RETRACTED" in f.message for f in res.findings)


# --- year comparison --------------------------------------------------------

def test_year_matches_any_date_field():
    """Online 2023 / print 2024: citing either year must not warn."""
    msg = dict(
        GOOD,
        **{
            "published-online": {"date-parts": [[2023, 12, 1]]},
            "published-print": {"date-parts": [[2024, 2, 1]]},
        },
    )
    del msg["issued"]
    for cited in (2023, 2024):
        ref = Reference(raw="", doi="10.1371/journal.pone.0312345", year=cited)
        res = check_reference(ref, make_client({ref.doi: msg}))
        assert not any("Year mismatch" in f.message for f in res.findings), cited


def test_year_mismatch_still_detected():
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345", year=1990)
    res = check_reference(ref, make_client({ref.doi: GOOD}))
    assert res.status == WARNING
    assert any("Year mismatch" in f.message for f in res.findings)


# --- subtitle handling ------------------------------------------------------

def test_subtitle_not_a_false_title_mismatch():
    msg = dict(GOOD, title=["Deep learning"], subtitle=["a comprehensive review of methods"])
    ref = Reference(
        raw="",
        doi="10.1371/journal.pone.0312345",
        title="Deep learning: a comprehensive review of methods",
    )
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert not any("Title mismatch" in f.message for f in res.findings)


# --- author diacritics ------------------------------------------------------

def test_author_diacritics_folded():
    msg = dict(GOOD, author=[{"family": "Müller"}])
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345", author="Muller")
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert not any("author mismatch" in f.message.lower() for f in res.findings)


def test_author_mismatch_detected():
    msg = dict(GOOD, author=[{"family": "Anderson"}])
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345", author="Zhang")
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert any("author mismatch" in f.message.lower() for f in res.findings)


def test_author_matches_any_listed_author():
    msg = dict(GOOD, author=[{"family": "Anderson"}, {"family": "Kim"}])
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345", author="Kim")
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert not any("author mismatch" in f.message.lower() for f in res.findings)


# --- resolvable-but-not-in-crossref -----------------------------------------

def test_unknown_but_resolvable_is_warning_not_error():
    ref = Reference(raw="", doi="10.5281/zenodo.123456")
    res = check_reference(ref, make_client({}, resolves=True))
    assert res.status == WARNING
    assert any("not in Crossref" in f.message for f in res.findings)


def test_unknown_and_unresolvable_is_error():
    ref = Reference(raw="", doi="10.9999/nope")
    res = check_reference(ref, make_client({}, resolves=False))
    assert res.status == ERROR
    assert any("does not resolve" in f.message for f in res.findings)


# --- malformed records must not crash the batch -----------------------------

import pytest


@pytest.mark.parametrize(
    "bad",
    [
        {"update-to": "x"},                       # str where list expected
        {"title": "Hello"},                       # str where list expected
        {"author": "nope"},
        {"author": [{}]},
        {"issued": {"date-parts": [[]]}},
        {"issued": {"date-parts": [[None]]}},
        {"issued": None},
        {"relation": "weird"},
        {"update-by": [None, "x", 3]},
    ],
)
def test_malformed_record_does_not_crash(bad):
    msg = dict(GOOD, **bad)
    ref = Reference(raw="", doi="10.1371/journal.pone.0312345", title="X", year=2024, author="Kim")
    res = check_reference(ref, make_client({ref.doi: msg}))
    assert res.status in (OK, WARNING, ERROR)  # returned a result, did not raise


# --- caching ----------------------------------------------------------------

def test_fetch_is_cached_per_doi():
    calls = []

    def counting_fetch(doi):
        calls.append(doi)
        return GOOD

    client = CrossrefClient(_fetch=counting_fetch, _resolve=lambda d: False)
    doi = "10.1371/journal.pone.0312345"
    for _ in range(3):
        check_reference(Reference(raw="", doi=doi), client)
    assert calls == [doi]  # fetched once, then served from cache


# --- user-agent / mailto ----------------------------------------------------

def test_default_user_agent_has_no_real_email():
    ua = CrossrefClient(mailto=None).user_agent
    assert "example.com" in ua  # placeholder only, no user address


def test_mailto_lands_in_user_agent():
    ua = CrossrefClient(mailto="me@lab.org").user_agent
    assert "me@lab.org" in ua


# --- retraction notice DOI fall-through -------------------------------------

def test_retraction_notice_falls_through_to_doi_bearing_field():
    from citecheck.core import _retraction_notice
    msg = {
        "update-by": [{"type": "retraction"}],  # matches but no DOI
        "update-to": [{"type": "retraction", "DOI": "10.1/notice"}],
    }
    assert _retraction_notice(msg) == "10.1/notice"


def test_retraction_notice_relation_narrowed():
    # is-retraction-of identifies the *notice*, not a retracted paper.
    from citecheck.core import _is_retracted
    assert _is_retracted({"relation": {"is-retraction-of": [{"id": "x"}]}}) is False
    assert _is_retracted({"relation": {"is-retracted-by": [{"id": "x"}]}}) is True


# --- _crossref_years shapes -------------------------------------------------

def test_crossref_years_shapes():
    from citecheck.core import _crossref_years
    assert _crossref_years({"issued": {"date-parts": [[2020, 1, 1]]}}) == {2020}
    assert _crossref_years({"issued": {"date-parts": [["2020"]]}}) == {2020}
    assert _crossref_years(
        {"published-print": {"date-parts": [[2024]]}, "issued": {"date-parts": [[2023]]}}
    ) == {2023, 2024}
    assert _crossref_years({"issued": {"date-parts": [[]]}}) == set()


# --- resolve caching --------------------------------------------------------

def test_resolve_is_cached_per_doi():
    calls = []

    def counting_resolve(doi):
        calls.append(doi)
        return True

    client = CrossrefClient(_fetch=lambda d: None, _resolve=counting_resolve)
    doi = "10.5281/zenodo.1"
    for _ in range(3):
        check_reference(Reference(raw="", doi=doi), client)
    assert calls == [doi]


# --- real network transport (monkeypatched, no sockets) ---------------------

def test_fetch_network_404_returns_none(monkeypatch):
    import urllib.error
    import urllib.request

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = CrossrefClient()
    assert client.fetch("10.9999/nope") is None  # 404 => None, no retry


def test_fetch_network_retries_then_raises(monkeypatch):
    import urllib.error
    import urllib.request
    import time as _time

    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    client = CrossrefClient(retries=2)
    import pytest as _pytest
    with _pytest.raises(urllib.error.URLError):
        client.fetch("10.1/x")
    assert len(attempts) == 3  # initial + 2 retries


def test_resolve_network_status(monkeypatch):
    import urllib.error
    import urllib.request

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    assert CrossrefClient().resolve("10.1/x") is True

    def http404(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "NF", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", http404)
    assert CrossrefClient().resolve("10.9/none") is False


# --- text-mode title containment (hyphen/punctuation robustness) -------------

def test_text_title_check_tolerates_punctuation():
    from citecheck.parsers import Reference as R
    msg = dict(GOOD, title=["Neural networks: a review of deep-learning methods for COVID-19"])
    ref = R(
        raw="Kim H. Neural-networks — a review of deep learning methods for COVID-19. Nature. 2021. doi:10.1/x",
        doi="10.1/x",
        heuristic_fields=True,
    )
    res = check_reference(ref, make_client({"10.1/x": msg}))
    assert not any("wrong DOI" in f.message for f in res.findings)


def test_text_title_check_flags_unrelated_title():
    from citecheck.parsers import Reference as R
    msg = dict(GOOD, title=["Penguin foraging behaviour in Antarctic waters"])
    ref = R(
        raw="Kim H. A randomized trial of aspirin in stroke patients. Lancet. 2019. doi:10.1/x",
        doi="10.1/x",
        heuristic_fields=True,
    )
    res = check_reference(ref, make_client({"10.1/x": msg}))
    assert any("wrong DOI" in f.message for f in res.findings)
