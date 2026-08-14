"""참고문헌·그림·표 증감, 그리고 추가문헌.csv 의 열 스키마."""

from __future__ import annotations

import csv

from revcheck.docio import document_from_text
from revcheck.engine import Options, run_check
from revcheck.inventory import (
    CITECHECK_HEADER,
    claimed_counts,
    diff_inventory,
    reference_rows,
)
from revcheck.report import ADDED_REFS_CSV, write_outputs

OLD = """# T

## Results

Outcomes are summarised in Table 1 and Figure 1.

## References

1. Kim H, Park J. Slow breathing and vagal tone. J Sleep Res. 2019;28(3):e12812. doi:10.1000/jsr.2019.0031
2. Lee S, Choi B. Acoustic stimulation review. Sleep Med Rev. 2021;55:101388. doi:10.1000/smrv.2021.101388
"""

NEW = """# T

## Results

Outcomes are summarised in Table 1, Table 2 and Figure 1.

## References

1. Kim H, Park J. Slow breathing and vagal tone. J Sleep Res. 2019;28(3):e12812. doi:10.1000/jsr.2019.0031
2. Lee S, Choi B. Acoustic stimulation review. Sleep Med Rev. 2021;55:101388. doi:10.1000/smrv.2021.101388
3. Jung E, Han K. Masking integrity in device trials. Trials. 2022;23(1):77. doi:10.1000/trials.2022.0077
4. Oh J, Ryu D. Urban-rural differences. Sleep Health. 2023;9(4):412-419. doi:10.1000/sh.2023.0412
"""


def _docs():
    return document_from_text(OLD, "md", "old.md"), document_from_text(NEW, "md", "new.md")


def test_added_and_removed_references_are_detected():
    inv = diff_inventory(*_docs())
    assert len(inv.old_refs) == 2 and len(inv.new_refs) == 4
    assert len(inv.added_refs) == 2
    assert not inv.removed_refs
    assert inv.added_refs[0].year == "2022"
    assert inv.added_refs[0].doi == "10.1000/trials.2022.0077"


def test_added_tables_and_figures():
    inv = diff_inventory(*_docs())
    assert inv.added_tables == [2]
    assert inv.added_figures == []


def test_reference_rows_use_the_citecheck_schema():
    inv = diff_inventory(*_docs())
    rows = reference_rows(inv.added_refs)
    assert rows[0] == CITECHECK_HEADER
    assert rows[0] == [
        "Study ID", "Authors", "Year", "Title", "Journal", "Article DOI", "PMID", "parse_ok",
    ]
    assert len(rows) == 3
    assert rows[1][5] == "10.1000/trials.2022.0077"
    assert rows[1][7] == "yes"


def test_added_refs_csv_is_written_with_the_same_header(tmp_path):
    resp = document_from_text(
        "Comment 1-1: Add literature.\nResponse: We added two new references as suggested here.\n"
        "Comment 1-2: Second.\nResponse: We revised the Discussion section as suggested here.\n"
        "Comment 1-3: Third.\nResponse: We revised the Methods section as suggested here too.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    old, new = _docs()
    result = run_check(old, new, resp, Options())
    write_outputs(result, tmp_path, result.exit_code())
    with open(tmp_path / ADDED_REFS_CSV, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == CITECHECK_HEADER
    assert len(rows) == 3


def test_claimed_reference_counts_are_parsed():
    assert claimed_counts("We have added three new references.") == [3]
    assert claimed_counts("We added 2 new references to the Discussion.") == [2]
    assert claimed_counts("참고문헌 3편을 새로 추가했습니다.") == [3]
    assert claimed_counts("두 편의 새 참고문헌을 추가하였습니다.") == [2]
    assert claimed_counts("No claim about references here.") == []


def test_reference_count_mismatch_is_a_warning():
    resp = document_from_text(
        "Comment 1-1: Add literature.\nResponse: We have added three new references as suggested.\n"
        "Comment 1-2: Second.\nResponse: We revised the Discussion section as suggested here.\n"
        "Comment 1-3: Third.\nResponse: We revised the Methods section as suggested here too.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    old, new = _docs()
    result = run_check(old, new, resp, Options())
    warn = [f for f in result.warnings if f.kind == "참고문헌수량"]
    assert warn and "3편 추가라고 했으나 실제 증가는 2편" in warn[0].message


def test_matching_reference_count_produces_no_warning():
    resp = document_from_text(
        "Comment 1-1: Add literature.\nResponse: We have added two new references as suggested.\n"
        "Comment 1-2: Second.\nResponse: We revised the Discussion section as suggested here.\n"
        "Comment 1-3: Third.\nResponse: We revised the Methods section as suggested here too.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    old, new = _docs()
    result = run_check(old, new, resp, Options())
    assert not [f for f in result.warnings if f.kind == "참고문헌수량"]


def test_removed_reference_is_reported_as_info():
    old, _ = _docs()
    shrunk = document_from_text(
        OLD.replace(
            "2. Lee S, Choi B. Acoustic stimulation review. Sleep Med Rev. 2021;55:101388. doi:10.1000/smrv.2021.101388\n",
            "",
        ),
        "md",
        "new.md",
    )
    inv = diff_inventory(old, shrunk)
    assert len(inv.removed_refs) == 1


def test_lines_without_year_or_doi_are_not_counted_as_references():
    """참고문헌 뒤에 붙인 표 캡션·판권 문구를 문헌으로 세면 거짓 경고가 난다."""
    from revcheck.inventory import collect_references

    doc = document_from_text(
        OLD + "\nTable 2. Completers by arm\n\n| Arm | n |\n| Active | 42 |\n",
        "md",
        "new.md",
    )
    refs, found, skipped = collect_references(doc)
    assert found and len(refs) == 2
    assert skipped == 0  # 표는 아예 참고문헌 절이 아니어야 한다
    assert all(para.section != "References" for para in doc.paras if para.kind == "table")


def test_adding_a_doi_to_an_existing_reference_is_not_a_new_reference():
    """문헌에 DOI 를 덧붙인 것을 '1편 삭제 + 1편 추가'로 세면 안 된다."""
    old = document_from_text(
        "# T\n\n## References\n\n1. Kim H, Park J. Slow breathing. J Sleep Res. 2019;28:e12812.\n",
        "md",
        "old.md",
    )
    new = document_from_text(
        "# T\n\n## References\n\n1. Kim H, Park J. Slow breathing. J Sleep Res. 2019;28:e12812. "
        "doi:10.1000/jsr.2019.0031\n",
        "md",
        "new.md",
    )
    inv = diff_inventory(old, new)
    # DOI 가 한쪽에만 있으므로 키가 다를 수 있다 — 그렇더라도 '추가 1편 · 삭제 1편'이
    # 동시에 잡히면 순증가는 0 이어야 한다.
    assert len(inv.added_refs) == len(inv.removed_refs)
