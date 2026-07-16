"""CLI-level tests. All use an injected client so no network is touched."""

import json

import pytest

from citecheck.cli import run, _sanitize, _decode
from citecheck.core import CrossrefClient

GOOD = {
    "DOI": "10.1371/journal.pone.0312345",
    "title": ["Sleep as a transdiagnostic node in BELL disorders"],
    "author": [{"family": "Kim"}],
    "issued": {"date-parts": [[2024, 5, 1]]},
    "type": "journal-article",
}


def fake_client(records, resolves=False):
    return CrossrefClient(_fetch=lambda d: records.get(d), _resolve=lambda d: resolves)


def write(tmp_path, name, data, encoding="utf-8"):
    p = tmp_path / name
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding=encoding)
    return str(p)


# --- exit-code contract -----------------------------------------------------

def test_exit_zero_when_all_ok(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.1371/journal.pone.0312345}}")
    code = run([path, "--delay", "0"], client=fake_client({"10.1371/journal.pone.0312345": GOOD}))
    assert code == 0


def test_exit_one_on_error(tmp_path):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.9999/nope}}")
    code = run([path, "--delay", "0"], client=fake_client({}, resolves=False))
    assert code == 1


def test_exit_two_on_empty_input(tmp_path):
    path = write(tmp_path, "empty.bib", "   \n  \n")
    code = run([path, "--delay", "0"], client=fake_client({}))
    assert code == 2


def test_exit_two_on_unreadable(tmp_path):
    code = run([str(tmp_path / "missing.bib"), "--delay", "0"], client=fake_client({}))
    assert code == 2


def test_strict_promotes_warning_to_failure(tmp_path):
    # No-DOI reference => WARNING. Normal exit 0, strict exit 1.
    path = write(tmp_path, "r.bib", "@book{k, title={No DOI here}}")
    assert run([path, "--delay", "0"], client=fake_client({})) == 0
    assert run([path, "--delay", "0", "--strict"], client=fake_client({})) == 1


# --- non-UTF-8 input --------------------------------------------------------

def test_non_utf8_file_does_not_crash(tmp_path, capsys):
    data = "@article{k, author={김현중}, doi={10.1371/journal.pone.0312345}}".encode("cp949")
    path = write(tmp_path, "k.bib", data)
    code = run([path, "--delay", "0"], client=fake_client({"10.1371/journal.pone.0312345": GOOD}))
    assert code == 0
    assert "not UTF-8" in capsys.readouterr().err


def test_decode_helper_fallbacks():
    assert _decode("hello".encode("utf-8")) == ("hello", "utf-8")
    text, enc = _decode("café".encode("latin-1"))
    assert enc != "utf-8"


# --- JSON output shape ------------------------------------------------------

def test_json_output_shape(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.9999/nope}}")
    run([path, "--json", "--delay", "0"], client=fake_client({}, resolves=False))
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list) and len(payload) == 1
    item = payload[0]
    assert set(item) == {"label", "doi", "pmid", "journal", "status", "findings"}
    assert item["status"] == "error"
    assert item["findings"][0].keys() >= {"severity", "message"}


# --- ANSI / control-char injection ------------------------------------------

def test_ansi_escape_stripped_from_output(tmp_path, capsys):
    malicious = "@article{k, title={Hi\x1b[2J\x1b[31mFAKE}, doi={10.1/x}}"
    path = write(tmp_path, "m.bib", malicious)
    records = {"10.1/x": dict(GOOD, title=["totally different penguin paper"])}
    run([path, "--delay", "0", "--verbose"], client=fake_client(records))
    out = capsys.readouterr().out
    assert "\x1b" not in out  # no raw escape bytes reach the terminal


def test_sanitize_strips_controls():
    assert _sanitize("a\x1b[31mb\x07c") == "a[31mbc"
    assert _sanitize("normal text") == "normal text"


# --- duplicate DOI detection ------------------------------------------------

def test_duplicate_doi_flagged(tmp_path, capsys):
    bib = (
        "@article{a, doi={10.1371/journal.pone.0312345}}\n"
        "@article{b, doi={10.1371/journal.pone.0312345}}"
    )
    path = write(tmp_path, "d.bib", bib)
    run([path, "--json", "--delay", "0"], client=fake_client({"10.1371/journal.pone.0312345": GOOD}))
    payload = json.loads(capsys.readouterr().out)
    assert all(any("Duplicate DOI" in f["message"] for f in item["findings"]) for item in payload)


# --- malformed bibtex reporting ---------------------------------------------

def test_malformed_entry_reported(tmp_path, capsys):
    bib = "@article{good, doi={10.1371/journal.pone.0312345}}\n@article{bad, title={oops"
    path = write(tmp_path, "b.bib", bib)
    run([path, "--delay", "0"], client=fake_client({"10.1371/journal.pone.0312345": GOOD}))
    assert "malformed" in capsys.readouterr().err


# --- decoding: BOM / UTF-16 -------------------------------------------------

def test_decode_utf8_bom_stripped():
    text, enc = _decode("café".encode("utf-8-sig"))
    assert text == "café" and enc == "utf-8-sig"


def test_decode_utf16_bom():
    text, enc = _decode("@article{k}".encode("utf-16"))
    assert "article" in text and enc == "utf-16"


def test_decode_override():
    text, enc = _decode("café".encode("latin-1"), override="latin-1")
    assert text == "café" and enc == "latin-1"


# --- --delay validation -----------------------------------------------------

@pytest.mark.parametrize("bad", ["-1", "nan", "inf", "-inf", "abc"])
def test_delay_rejects_bad_values(tmp_path, bad, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.1/x}}\n@article{k2, doi={10.1/y}}")
    with pytest.raises(SystemExit) as exc:
        run([path, "--delay", bad], client=fake_client({}))
    assert exc.value.code == 2  # argparse usage error, not a traceback


# --- exit code 3 on lookup failure (offline) --------------------------------

def test_offline_lookup_failure_is_inconclusive(tmp_path):
    def boom(doi):
        raise TimeoutError("offline")

    client = CrossrefClient(_fetch=boom, _resolve=lambda d: False)
    path = write(tmp_path, "r.bib", "@article{k, doi={10.1371/journal.pone.0312345}}")
    code = run([path, "--delay", "0"], client=client)
    assert code == 3  # not 0 — must not read as a clean pass


# --- --strict in JSON mode --------------------------------------------------

def test_strict_json_mode(tmp_path):
    path = write(tmp_path, "r.bib", "@book{k, title={No DOI}}")
    assert run([path, "--json", "--delay", "0"], client=fake_client({})) == 0
    assert run([path, "--json", "--strict", "--delay", "0"], client=fake_client({})) == 1


# --- text-mode heuristic suppression ----------------------------------------

def test_text_mode_suppresses_year_author_false_positives(tmp_path, capsys):
    # "2000 patients" misparses the year; author "Effect" is bogus. With a DOI
    # whose real title IS mentioned in the line, no year/author/title warning.
    record = dict(
        GOOD,
        title=["Effect of aspirin in patients with acute ischaemic stroke"],
        author=[{"family": "Chen"}],
        issued={"date-parts": [[2024]]},
    )
    records = {"10.1371/journal.pmed.0020124": record}
    text = ("Effect of aspirin in 2000 patients with acute ischaemic stroke. "
            "Lancet. 2019. doi:10.1371/journal.pmed.0020124")
    path = write(tmp_path, "refs.txt", text)
    code = run([path, "--format", "text", "--json", "--delay", "0"], client=fake_client(records))
    out = capsys.readouterr().out
    assert "mismatch" not in out.lower()
    assert "wrong doi" not in out.lower()
    assert code == 0


def test_text_mode_catches_swapped_doi_via_title(tmp_path, capsys):
    # A DOI pointing to a *different* real paper: the cited prose won't mention
    # the Crossref title, so text mode flags it even though author/year are off.
    records = {"10.1371/journal.pmed.0020124": dict(GOOD, title=["Penguin foraging in Antarctic waters"])}
    text = "Smith J. A randomized trial of aspirin in stroke patients. Lancet. 2019. doi:10.1371/journal.pmed.0020124"
    path = write(tmp_path, "refs.txt", text)
    run([path, "--format", "text", "--json", "--delay", "0"], client=fake_client(records))
    payload = json.loads(capsys.readouterr().out)
    assert any("wrong DOI" in f["message"] for f in payload[0]["findings"])


def test_text_mode_bare_doi_not_flagged(tmp_path, capsys):
    # A bare-DOI line has no prose — must NOT be flagged as a wrong-title.
    records = {"10.1371/journal.pmed.0020124": dict(GOOD, title=["Anything at all here"])}
    text = "10.1371/journal.pmed.0020124"
    path = write(tmp_path, "refs.txt", text)
    code = run([path, "--format", "text", "--json", "--delay", "0"], client=fake_client(records))
    out = capsys.readouterr().out
    assert "wrong doi" not in out.lower()
    assert code == 0


# --- more end-to-end coverage -----------------------------------------------

def test_unknown_encoding_exit_two(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.1/x}}")
    code = run([path, "--encoding", "bogus-codec", "--delay", "0"], client=fake_client({}))
    assert code == 2
    assert "unknown encoding" in capsys.readouterr().err


def test_resolvable_not_in_crossref_cli(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.5281/zenodo.1}}")
    code = run([path, "--delay", "0"], client=fake_client({}, resolves=True))
    out = capsys.readouterr().out
    assert code == 0  # warning, not error
    assert "not in Crossref" in out


def test_retraction_cli_exit_one(tmp_path, capsys):
    rec = dict(GOOD, **{"update-by": [{"type": "retraction", "DOI": "10.1/notice"}]})
    path = write(tmp_path, "r.bib", "@article{k, doi={10.1371/journal.pone.0312345}}")
    code = run([path, "--delay", "0"], client=fake_client({"10.1371/journal.pone.0312345": rec}))
    out = capsys.readouterr().out
    assert code == 1
    assert "RETRACTED" in out


def test_summary_counts(tmp_path, capsys):
    bib = (
        "@article{ok, doi={10.1371/journal.pone.0312345}}\n"
        "@article{bad, doi={10.9999/nope}}\n"
        "@book{nodoi, title={No DOI}}"
    )
    path = write(tmp_path, "m.bib", bib)
    run([path, "--delay", "0"], client=fake_client({"10.1371/journal.pone.0312345": GOOD}, resolves=False))
    out = capsys.readouterr().out
    assert "1 verified against Crossref" in out
    assert "could not be verified" in out


def test_resolve_transient_failure_not_hard_error(tmp_path, capsys):
    # doi.org glitch on a not-in-Crossref DOI must be inconclusive, not an error.
    def flaky_resolve(doi):
        raise TimeoutError("doi.org timeout")

    client = CrossrefClient(_fetch=lambda d: None, _resolve=flaky_resolve)
    path = write(tmp_path, "r.bib", "@article{k, doi={10.1234/maybe.real}}")
    code = run([path, "--delay", "0"], client=client)
    out = capsys.readouterr().out
    assert "does not resolve" not in out  # no false hard error
    assert code == 3  # inconclusive


# --- new input formats end-to-end -------------------------------------------

RIS_DOC = """TY  - JOUR
AU  - Ioannidis, John P. A.
TI  - Why Most Published Research Findings Are False
JO  - PLoS Medicine
PY  - 2005
DO  - 10.1371/journal.pmed.0020124
ER  - 
"""


def test_ris_input_auto_detected(tmp_path, capsys):
    rec = dict(GOOD, title=["Why Most Published Research Findings Are False"],
               author=[{"family": "Ioannidis"}], issued={"date-parts": [[2005]]})
    path = write(tmp_path, "refs.ris", RIS_DOC)
    code = run([path, "--delay", "0", "--json"],
               client=fake_client({"10.1371/journal.pmed.0020124": rec}))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload[0]["doi"] == "10.1371/journal.pmed.0020124"


def test_csl_json_input_auto_detected(tmp_path, capsys):
    doc = ('[{"id":"k","type":"article-journal","DOI":"10.9999/nope",'
           '"title":"Broken","author":[{"family":"Smith"}],'
           '"issued":{"date-parts":[[2020]]}}]')
    path = write(tmp_path, "refs.json", doc)
    code = run([path, "--delay", "0", "--json"], client=fake_client({}, resolves=False))
    payload = json.loads(capsys.readouterr().out)
    assert code == 1  # broken DOI -> error
    assert payload[0]["status"] == "error"


# --- report formats ---------------------------------------------------------

def test_csv_report(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.9999/nope}, journal={Nature}}")
    run([path, "--report", "csv", "--delay", "0"], client=fake_client({}, resolves=False))
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert lines[0] == "label,doi,pmid,journal,status,findings"
    assert "10.9999/nope" in lines[1]
    assert "error" in lines[1]


def test_csv_injection_neutralised(tmp_path, capsys):
    # A journal field starting with '=' must be quote-prefixed, not left as a
    # live spreadsheet formula.
    path = write(tmp_path, "r.bib", '@article{k, doi={10.1/x}, journal={=HYPERLINK("evil")}}')
    run([path, "--report", "csv", "--delay", "0"], client=fake_client({"10.1/x": GOOD}))
    out = capsys.readouterr().out
    assert "'=HYPERLINK" in out  # neutralised
    assert ",=HYPERLINK" not in out  # never a bare leading '='


def test_markdown_report(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.9999/nope}}")
    run([path, "--report", "markdown", "--delay", "0"], client=fake_client({}, resolves=False))
    out = capsys.readouterr().out
    assert "# citecheck report" in out
    assert "| Status | Reference | DOI | Findings |" in out
    assert "✗" in out


def test_json_flag_still_works(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.9999/nope}}")
    run([path, "--json", "--delay", "0"], client=fake_client({}, resolves=False))
    json.loads(capsys.readouterr().out)  # valid JSON


def test_report_json_equivalent_to_json_flag(tmp_path, capsys):
    path = write(tmp_path, "r.bib", "@article{k, doi={10.9999/nope}}")
    run([path, "--report", "json", "--delay", "0"], client=fake_client({}, resolves=False))
    json.loads(capsys.readouterr().out)


# --- duplicate PMID ---------------------------------------------------------

def test_duplicate_pmid_flagged(tmp_path, capsys):
    bib = ("@article{a, title={T}, note={PMID: 16060722}}\n"
           "@article{b, title={T}, note={PMID: 16060722}}")
    path = write(tmp_path, "d.bib", bib)
    run([path, "--json", "--delay", "0"], client=fake_client({}))
    payload = json.loads(capsys.readouterr().out)
    assert all(any("Duplicate PMID" in f["message"] for f in item["findings"])
               for item in payload)


def test_duplicate_pmid_not_double_reported_with_doi(tmp_path, capsys):
    # Same DOI twice already flags Duplicate DOI; don't also add Duplicate PMID.
    bib = ("@article{a, doi={10.1/x}, note={PMID: 111}}\n"
           "@article{b, doi={10.1/x}, note={PMID: 111}}")
    path = write(tmp_path, "d.bib", bib)
    run([path, "--json", "--delay", "0"], client=fake_client({"10.1/x": GOOD}))
    payload = json.loads(capsys.readouterr().out)
    for item in payload:
        msgs = [f["message"] for f in item["findings"]]
        assert any("Duplicate DOI" in m for m in msgs)
        assert not any("Duplicate PMID" in m for m in msgs)


# --- --pubmed cross-check (injected client) ---------------------------------

def test_cli_pubmed_retraction_exit_one(tmp_path, capsys):
    from citecheck.core import PubMedClient
    rec = {"pubtype": ["Retracted Publication"],
           "articleids": [{"idtype": "doi", "value": "10.1371/journal.pone.0312345"}]}
    pm = PubMedClient(_fetch=lambda p: {"999": rec}.get(p))
    path = write(tmp_path, "r.bib",
                 "@article{k, doi={10.1371/journal.pone.0312345}, pmid={999}}")
    code = run([path, "--pubmed", "--delay", "0"],
               client=fake_client({"10.1371/journal.pone.0312345": GOOD}), pubmed=pm)
    out = capsys.readouterr().out
    assert code == 1
    assert "RETRACTED according to PubMed" in out


def test_cli_pubmed_flag_gates_the_crosscheck(tmp_path, capsys):
    # A PMID-bearing ref whose PubMed record is RETRACTED. Without --pubmed and
    # with no injected PubMed client, the cross-check must NOT run (exit 0);
    # nothing may reach the network (the conftest guard would fail the test if it
    # tried). This proves PubMed is genuinely opt-in.
    path = write(tmp_path, "r.bib",
                 "@article{k, doi={10.1371/journal.pone.0312345}, pmid={999}}")
    code = run([path, "--delay", "0"],
               client=fake_client({"10.1371/journal.pone.0312345": GOOD}))
    assert code == 0  # no PubMed => no retraction error


def test_cli_injected_pubmed_used_even_without_flag(tmp_path, capsys):
    # When a PubMed client is injected (as tests do), it is honoured directly —
    # the --pubmed flag only controls creation of a *real* network client.
    from citecheck.core import PubMedClient
    rec = {"pubtype": ["Retracted Publication"],
           "articleids": [{"idtype": "doi", "value": "10.1371/journal.pone.0312345"}]}
    pm = PubMedClient(_fetch=lambda p: {"999": rec}.get(p))
    path = write(tmp_path, "r.bib",
                 "@article{k, doi={10.1371/journal.pone.0312345}, pmid={999}}")
    code = run([path, "--delay", "0"],
               client=fake_client({"10.1371/journal.pone.0312345": GOOD}), pubmed=pm)
    assert code == 1  # injected PubMed client runs, retraction => error


def test_cli_pubmed_lookup_failure_exit_three(tmp_path, capsys):
    # A PubMed lookup failure must make the run exit 3 (inconclusive), never a
    # false clean pass — the exit-3 contract the docs advertise for --pubmed.
    from citecheck.core import PubMedClient

    def boom(p):
        raise TimeoutError("pubmed offline")

    pm = PubMedClient(_fetch=boom)
    path = write(tmp_path, "r.bib",
                 "@article{k, doi={10.1371/journal.pone.0312345}, pmid={999}}")
    code = run([path, "--delay", "0"],
               client=fake_client({"10.1371/journal.pone.0312345": GOOD}), pubmed=pm)
    assert code == 3


def test_cli_retraction_notice_not_flagged_exit_zero(tmp_path, capsys):
    # "Retraction of Publication" is the notice, not a retracted source — the
    # PMID cross-check must NOT error on it.
    from citecheck.core import PubMedClient
    notice = {"pubtype": ["Retraction of Publication"],
              "articleids": [{"idtype": "doi", "value": "10.1371/journal.pone.0312345"}]}
    pm = PubMedClient(_fetch=lambda p: {"999": notice}.get(p))
    path = write(tmp_path, "r.bib",
                 "@article{k, doi={10.1371/journal.pone.0312345}, pmid={999}}")
    code = run([path, "--delay", "0"],
               client=fake_client({"10.1371/journal.pone.0312345": GOOD}), pubmed=pm)
    assert code == 0


def test_json_doi_field_sanitized(tmp_path, capsys):
    # A DOI carrying a C1 control char (0x9b = CSI) must be stripped in the JSON
    # report too, consistent with text/csv/markdown.
    path = write(tmp_path, "r.bib", "@article{k, title={T}, doi={10.1234/abc\x9b31mPWNED}}")
    run([path, "--json", "--delay", "0"], client=fake_client({}, resolves=False))
    out = capsys.readouterr().out
    assert "\x9b" not in out
    payload = json.loads(out)
    assert "\x9b" not in (payload[0]["doi"] or "")
