"""새 CLI 옵션들 — 제외어 / 정렬 / CSV 섹션 / 실행정보 / 입력형식 자동판별."""

import json
from pathlib import Path

import pytest

from pubgap.cli import build_parser, main
from pubgap.report import CSV_SECTIONS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sleep_pubmed.xml"
EXAMPLE_CSV = ROOT / "examples" / "sleep_export.csv"


def _json_run(capsys, argv):
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# --exclude-term / --exclude-terms-file
# --------------------------------------------------------------------------- #
def test_exclude_term_removes_topic_everywhere(capsys):
    base = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json"])
    assert any(t == "Sleep" for t, _ in base["top_mesh"])

    out = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json",
                             "--exclude-term", "Sleep"])
    assert all(t != "Sleep" for t, _ in out["top_mesh"])
    assert all("Sleep" not in (g["term_a"], g["term_b"]) for g in out["gaps"])
    # 논문 편수 자체는 그대로(주제만 뺀 것).
    assert out["n_articles"] == base["n_articles"]


def test_exclude_term_is_case_insensitive_and_exact(capsys):
    out = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json",
                             "--exclude-term", "sLeEp"])
    terms = [t for t, _ in out["top_mesh"]]
    assert "Sleep" not in terms
    # 부분일치로 지우면 안 된다 — 'Sleep Initiation...' 은 남아야 한다.
    assert "Sleep Initiation and Maintenance Disorders" in terms


def test_exclude_terms_file_with_comments(tmp_path, capsys):
    f = tmp_path / "stop.txt"
    f.write_text("# 검색어 자체\nSleep\n\n  Heart Rate  \n", encoding="utf-8")
    out = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json",
                             "--exclude-terms-file", str(f)])
    terms = [t for t, _ in out["top_mesh"]]
    assert "Sleep" not in terms and "Heart Rate" not in terms


def test_missing_exclude_file_is_a_clean_error(tmp_path, capsys):
    rc = main(["--from-file", str(EXAMPLE), "--exclude-terms-file",
               str(tmp_path / "nope.txt")])
    assert rc == 2
    assert "제외어 파일" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# --gap-sort
# --------------------------------------------------------------------------- #
def test_gap_sort_deficit_orders_by_missing_papers(capsys):
    out = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json",
                             "--gap-sort", "deficit"])
    deficits = [g["deficit"] for g in out["gaps"]]
    assert deficits == sorted(deficits, reverse=True)
    assert out["gap_sort"] == "deficit"


def test_gap_sort_deficit_is_default(capsys):
    """기본 정렬은 deficit — 문서가 '착수 후보는 deficit 으로 보라'고 권하므로
    기본값도 거기에 맞춰야 한다(예전엔 기본이 lift 라 문서와 어긋났다)."""
    out = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json"])
    deficits = [g["deficit"] for g in out["gaps"]]
    assert deficits == sorted(deficits, reverse=True)
    assert out["gap_sort"] == "deficit"


def test_gap_sort_lift_available(capsys):
    out = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json",
                             "--gap-sort", "lift"])
    lifts = [g["lift"] for g in out["gaps"]]
    assert lifts == sorted(lifts)
    assert out["gap_sort"] == "lift"


def test_invalid_gap_sort_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--from-file", "x", "--gap-sort", "bogus"])


# --------------------------------------------------------------------------- #
# --csv-section
# --------------------------------------------------------------------------- #
# 각 섹션이 실제로 **데이터 행**을 내야 한다(헤더만 나오면 렌더러가 죽은 것).
_CSV_EXPECTED_HEADER = {
    "gaps": "term_a,term_b,observed",
    "yearly": "year,n_articles",
    "journals": "journal,n_articles",
    "mesh": "term,n_articles",
    "emerging": "term,early_count,recent_count",
    "declining": "term,early_count,recent_count",
    "evidence": "tier,label,count,share",
    "topic-evidence": "term,n_articles,n_interventional",
}


@pytest.mark.parametrize("section", CSV_SECTIONS)
def test_every_csv_section_renders_rows(section, capsys):
    rc = main(["--from-file", str(EXAMPLE), "--format", "csv", "--csv-section", section])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("﻿")            # 엑셀 한글용 BOM
    lines = out.lstrip("﻿").splitlines()
    assert lines[0].startswith(_CSV_EXPECTED_HEADER[section]), lines[0]
    # 번들 예시는 모든 섹션에 내용이 있다 — 헤더만 나오면 회귀다.
    assert len(lines) >= 2, f"{section} 섹션에 데이터 행이 없다"


def test_csv_yearly_section_content(capsys):
    main(["--from-file", str(EXAMPLE), "--format", "csv", "--csv-section", "yearly"])
    lines = capsys.readouterr().out.lstrip("﻿").splitlines()
    assert lines[0] == "year,n_articles"
    assert lines[1] == "2015,1"


# --------------------------------------------------------------------------- #
# 실행 정보(재현용)
# --------------------------------------------------------------------------- #
def test_meta_block_present_by_default_and_removable(capsys):
    main(["--from-file", str(EXAMPLE)])
    md = capsys.readouterr().out
    assert "실행 정보(재현용)" in md and "sha256" in md

    main(["--from-file", str(EXAMPLE), "--no-meta"])
    assert "실행 정보(재현용)" not in capsys.readouterr().out


def test_meta_records_input_fingerprint_and_params(capsys):
    data = _json_run(capsys, ["--from-file", str(EXAMPLE), "--format", "json",
                              "--gap-max-lift", "0.4"])
    meta = data["meta"]
    assert meta["tool"] == "pubgap"
    assert meta["input"]["format"] == "xml"
    assert len(meta["input"]["sha256"]) == 64
    assert meta["input"]["bytes"] == EXAMPLE.stat().st_size
    assert meta["params"]["gap_max_lift"] == 0.4
    assert meta["generated_at"].endswith("Z")


def test_reports_are_identical_apart_from_meta(capsys):
    """--no-meta 를 쓰면 같은 입력·옵션에서 바이트 단위로 재현 가능해야 한다."""
    main(["--from-file", str(EXAMPLE), "--no-meta"])
    a = capsys.readouterr().out
    main(["--from-file", str(EXAMPLE), "--no-meta"])
    b = capsys.readouterr().out
    assert a == b


# --------------------------------------------------------------------------- #
# 입력 형식 자동 판별 (end-to-end)
# --------------------------------------------------------------------------- #
def test_csv_example_gives_same_report_as_xml(capsys):
    main(["--from-file", str(EXAMPLE), "--no-meta"])
    from_xml = capsys.readouterr().out
    main(["--from-file", str(EXAMPLE_CSV), "--no-meta"])
    from_csv = capsys.readouterr().out
    # 첫 줄(입력 경로 라벨)만 다르고 나머지는 동일해야 한다.
    assert from_xml.splitlines()[1:] == from_csv.splitlines()[1:]


def test_ris_file_end_to_end(tmp_path, capsys):
    ris = tmp_path / "export.ris"
    ris.write_text(
        "".join(
            f"TY  - JOUR\nTI  - Paper {i}\nJO  - J Sleep\nPY  - {2015 + i % 8}\n"
            f"KW  - *Sleep\nKW  - {'Heart Rate/physiology' if i % 2 else 'Electroencephalography'}\n"
            f"AN  - {900000 + i}\nER  -\n\n"
            for i in range(24)
        ),
        encoding="utf-8",
    )
    rc = main(["--from-file", str(ris), "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_articles"] == 24
    assert dict(data["top_mesh"])["Sleep"] == 24


def test_keyword_only_input_falls_back_with_notice(tmp_path, capsys):
    ris = tmp_path / "kw.ris"
    ris.write_text(
        "".join(
            f"TY  - JOUR\nTI  - P{i}\nPY  - 2020\nKW  - sleep\nKW  - hrv\n"
            f"AN  - {800000 + i}\nER  -\n\n"
            for i in range(5)
        ),
        encoding="utf-8",
    )
    rc = main(["--from-file", str(ris), "--format", "json"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "저자 키워드를 주제로 사용" in cap.err
    assert dict(json.loads(cap.out)["top_mesh"])["sleep"] == 5


def test_major_only_on_input_without_major_topics_warns(tmp_path, capsys):
    ris = tmp_path / "nomajor.ris"
    ris.write_text(
        "TY  - JOUR\nTI  - A\nPY  - 2020\nKW  - Sleep/physiology\nAN  - 1\nER  -\n",
        encoding="utf-8",
    )
    main(["--from-file", str(ris), "--major-topics-only"])
    assert "대표(별표) MeSH 주제가 하나도 없어" in capsys.readouterr().err


def test_directory_as_input_is_a_clean_error(tmp_path, capsys):
    rc = main(["--from-file", str(tmp_path)])
    assert rc == 2
    assert "디렉터리" in capsys.readouterr().err
