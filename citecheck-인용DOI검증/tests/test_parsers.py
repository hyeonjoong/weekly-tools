from citecheck.parsers import find_doi, find_year, parse_bibtex, parse_references, parse_text

BIB = r"""
@article{kim2024sleep,
  title   = {Sleep as a transdiagnostic node in {BELL} disorders},
  author  = {Kim, Hyeon J. and Lee, Sara and Park, Min},
  journal = {PLOS ONE},
  year    = {2024},
  doi     = {10.1371/journal.pone.0312345},
}

@book{strunk1999,
  title = {The Elements of Style},
  author = {Strunk, William and White, E. B.},
  year = {1999}
}
"""


def test_find_doi_strips_trailing_punctuation():
    assert find_doi("see 10.1371/journal.pone.0312345.") == "10.1371/journal.pone.0312345"
    assert find_doi("(doi: 10.1000/xyz123)") == "10.1000/xyz123"
    assert find_doi("no doi here") is None


def test_find_year():
    assert find_year("Published in 2024 somewhere") == 2024
    assert find_year("no year") is None


def test_parse_bibtex_extracts_fields():
    refs = parse_bibtex(BIB)
    assert len(refs) == 2
    first = refs[0]
    assert first.key == "kim2024sleep"
    assert first.doi == "10.1371/journal.pone.0312345"
    assert first.year == 2024
    assert first.author == "Kim"
    assert "transdiagnostic node" in first.title
    # Braces inside the title must be stripped.
    assert "{" not in first.title and "}" not in first.title


def test_parse_bibtex_author_without_doi():
    refs = parse_bibtex(BIB)
    book = refs[1]
    assert book.doi is None
    assert book.author == "Strunk"


def test_auto_detect_text():
    text = "Kim H, Lee S. A study. PLOS ONE. 2024. doi:10.1371/journal.pone.0312345"
    refs = parse_references(text, fmt="auto")
    assert len(refs) == 1
    assert refs[0].doi == "10.1371/journal.pone.0312345"
    assert refs[0].author == "Kim"
    assert refs[0].year == 2024


def test_parse_text_paragraphs():
    text = "Ref one 10.1000/a\n\nRef two 10.1000/b"
    refs = parse_text(text)
    assert [r.doi for r in refs] == ["10.1000/a", "10.1000/b"]


# --- DOI normalisation edge cases -------------------------------------------

def test_bibtex_doi_as_url():
    for val in (
        "https://doi.org/10.1371/journal.pone.0312345",
        "http://dx.doi.org/10.1371/journal.pone.0312345",
        "doi:10.1371/journal.pone.0312345",
        "DOI: 10.1371/journal.pone.0312345",
    ):
        refs = parse_bibtex("@article{k, doi={%s}}" % val)
        assert refs[0].doi == "10.1371/journal.pone.0312345", val


def test_doi_with_parentheses_preserved():
    # Elsevier/Lancet-style DOI containing balanced parentheses.
    assert find_doi("see 10.1016/S0140-6736(97)11096-0.") == "10.1016/s0140-6736(97)11096-0"


def test_doi_wrapping_paren_stripped():
    assert find_doi("(doi: 10.1000/xyz123)") == "10.1000/xyz123"


def test_long_registrant_code_doi():
    assert find_doi("10.1234567890/abc") == "10.1234567890/abc"


# --- @string / @comment must not swallow the next entry ---------------------

def test_string_macro_does_not_swallow_next_entry():
    bib = '@string{pone = "PLOS ONE"}\n\n@article{real2024, title={A real paper}, doi={10.1/x}, year={2024}}'
    refs = parse_bibtex(bib)
    assert [r.key for r in refs] == ["real2024"]
    assert refs[0].doi == "10.1/x"


def test_comment_entry_skipped():
    bib = "@comment{this is a note, ignore}\n@article{a, title={T}, doi={10.1/y}}"
    refs = parse_bibtex(bib)
    assert [r.key for r in refs] == ["a"]


# --- unterminated braces are bounded, do not consume following entries ------

def test_unterminated_entry_does_not_eat_next():
    bib = "@article{good, title={fine}, doi={10.1000/abc}}\n@article{bad, title={oops"
    refs = parse_bibtex(bib)
    keys = [r.key for r in refs]
    assert "good" in keys  # good must survive


def test_count_malformed_entries():
    from citecheck.parsers import count_malformed_entries
    good = "@article{a, title={ok}, doi={10.1000/x}}\n@book{b, title={ok}}"
    assert count_malformed_entries(good) == 0
    bad = "@article{a, doi={10.1000/x}}\n@article{b, title={oops"
    assert count_malformed_entries(bad) == 1


def test_nested_at_entry_in_field_value_not_dropped():
    # A field value that literally contains "@type{key," must not steal the entry.
    bib = "@article{real, title={On parsing @inproceedings{x, y} entries}, doi={10.1000/REAL}}"
    refs = parse_bibtex(bib)
    assert [r.key for r in refs] == ["real"]
    assert refs[0].doi == "10.1000/real"


# --- auto-detect must not misfire on emails ---------------------------------

def test_auto_detect_email_is_text_not_bibtex():
    text = "Smith J (smith@uni.edu). A study {n=5}. 2020. doi:10.1000/abc123"
    refs = parse_references(text, fmt="auto")
    assert len(refs) == 1
    assert refs[0].doi == "10.1000/abc123"


def test_auto_detect_real_bibtex():
    refs = parse_references("@article{k, title={T}, doi={10.1/z}}", fmt="auto")
    assert refs[0].key == "k"


# --- parse_text splitting ---------------------------------------------------

def test_text_multiline_single_ref_with_whitespace_blankline():
    # Blank line contains a space; both refs must stay intact (not shattered).
    text = "Smith J. A long title\nthat wraps. 10.1000/a\n \nJones K. Another. 10.1000/b"
    refs = parse_text(text)
    assert [r.doi for r in refs] == ["10.1000/a", "10.1000/b"]


def test_text_lines_without_blank_split_per_line():
    text = "Ref one 10.1000/a\nRef two 10.1000/b\nRef three 10.1000/c"
    refs = parse_text(text)
    assert [r.doi for r in refs] == ["10.1000/a", "10.1000/b", "10.1000/c"]


# --- first-author surname branches ------------------------------------------

def test_first_author_no_comma_form():
    from citecheck.parsers import _first_author_surname
    assert _first_author_surname("John von Neumann and Alan Turing") == "Neumann"


# --- normalize_doi_field (direct) -------------------------------------------

def test_normalize_doi_field_variants():
    from citecheck.parsers import normalize_doi_field
    assert normalize_doi_field("10.1/x") == "10.1/x"  # short registrant ok in field
    assert normalize_doi_field("https://doi.org/10.1371/journal.pone.1?utm=x") == "10.1371/journal.pone.1"
    assert normalize_doi_field("doi:10.1000/ABC") == "10.1000/abc"
    assert normalize_doi_field("") is None
    assert normalize_doi_field("not a doi at all") is None


def test_doi_query_string_stripped():
    from citecheck.parsers import normalize_doi_field
    assert normalize_doi_field("10.1371/journal.pone.0312345?utm_source=browser") == "10.1371/journal.pone.0312345"
    assert normalize_doi_field("10.1371/journal.pone.0312345#fragment") == "10.1371/journal.pone.0312345"


def test_unterminated_midfile_swallows_following_and_is_counted():
    from citecheck.parsers import count_malformed_entries
    bib = "@article{a, doi={10.1000/a}}\n@article{bad, title={oops\n@article{c, doi={10.1000/c}}"
    refs = parse_bibtex(bib)
    keys = [r.key for r in refs]
    assert "a" in keys            # entry before the break survives
    assert count_malformed_entries(bib) >= 1  # the break is reported
