"""The HTML report and the CSV field-separator sniffer."""
import html
import os
import re
from pathlib import Path

import pytest

from paperforge.cli import main
from paperforge.engine import evaluate
from paperforge.manifest import (
    ManifestError,
    _sniff_delimiter,
    load_manifest,
    parse_csv_manifest,
    parse_manifest,
)
from paperforge.report import render_html
from paperforge.templates import parse_template_pack


# These four CLI tests drive the shipped example files. Anchoring on the repo
# root keeps them runnable from any working directory (they used to fail with
# "템플릿 팩을 찾을 수 없습니다" when pytest was invoked from elsewhere).
_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_JSON = str(_ROOT / "examples" / "clinical_manifest.json")
CLINICAL_PACK = str(_ROOT / "examples" / "clinical_pack.json")


def _tpl(effect=None, **over):
    t = {
        "id": "t1", "title": "제목", "required": ["clinical"],
        "hypothesis": "가설", "predictors": ["p"], "outcomes": ["o"],
        "analysis": "분석", "design": "설계", "journal": "저널",
        "novelty": "신규성",
        "effect": effect or {"type": "two_group", "d": 0.5},
    }
    t.update(over)
    return [t]


def _run(datasets=None, templates=None, **kwargs):
    manifest = parse_manifest({
        "study": "연구",
        "datasets": datasets or [
            {"name": "crf", "modality": "clinical", "n": 200,
             "variables": ["arm", "endpoint"]},
        ],
    })
    tpl = parse_template_pack(templates or _tpl())
    return manifest, evaluate(manifest, templates=tpl, **kwargs)


# --- HTML -------------------------------------------------------------------

def test_html_is_a_complete_standalone_document():
    manifest, results = _run()
    page = render_html(manifest, results, 0.05, 0.80)
    assert page.startswith("<!DOCTYPE html>")
    assert page.rstrip().endswith("</html>")
    assert '<meta charset="utf-8">' in page
    # Self-contained: no external assets and no scripts to strip before sharing.
    assert "<script" not in page.lower()
    assert "src=" not in page and "http" not in page


def test_html_carries_the_same_headline_numbers_as_the_matrix():
    manifest, results = _run()
    page = render_html(manifest, results, 0.05, 0.80)
    r = results[0]
    assert f"<td>{r.required_n}</td>" in page
    assert f"<td>{r.available_n}</td>" in page
    assert r.feasibility_label in page
    assert html.escape(r.justification) in page


def test_html_escapes_untrusted_template_and_manifest_text():
    """A template pack is third-party JSON; a manifest column is whatever the
    spreadsheet allowed. Neither may inject markup into a shared report."""
    evil = '<script>alert("x")</script>'
    manifest, results = _run(
        datasets=[{"name": "d", "modality": "clinical", "n": 200,
                   "variables": [evil]}],
        # `caveats` is a free-form string list copied verbatim from the pack into
        # the per-idea notes, so it is the widest untrusted channel of all.
        templates=_tpl(title=evil, hypothesis=evil, novelty=evil,
                       caveats=['<img src=x onerror=alert(1)>']),
    )
    manifest.study = evil
    manifest.warnings.append(evil)
    page = render_html(manifest, results, 0.05, 0.80)
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_html_reports_warnings_and_the_empty_case():
    manifest, results = _run()
    manifest.warnings.append("경고 하나")
    page = render_html(manifest, results, 0.05, 0.80)
    assert "경고 하나" in page and 'class="warn"' in page

    empty = render_html(manifest, [], 0.05, 0.80)
    assert "매칭되는 아이디어가 없습니다" in empty
    assert empty.rstrip().endswith("</html>")
    assert "<table>" not in empty


def test_html_row_count_matches_the_result_count():
    manifest, results = _run(
        datasets=[{"name": "d", "modality": "clinical", "n": 200,
                   "variables": ["a"]},
                  {"name": "l", "modality": "lab", "n": 200,
                   "variables": ["b"]}],
        templates=_tpl() + [dict(_tpl()[0], id="t2", required=["lab"])],
    )
    page = render_html(manifest, results, 0.05, 0.80)
    assert len(results) == 2
    assert len(re.findall(r"<tr class=", page)) == 2


def test_html_shows_the_settings_that_changed_the_numbers():
    manifest, results = _run(dropout=0.2, n_tests=3, max_n=500)
    page = render_html(
        manifest, results, 0.05, 0.80, dropout=0.2,
        settings={"n_tests": 3, "max_n": 500, "repeats": 2, "icc": 0.3,
                  "sided": 1, "feasible_only": True},
    )
    assert "Bonferroni" in page
    assert "중도탈락 가정: 20%" in page
    assert "최대 500명" in page
    assert "단측검정" in page
    assert "충분 가능&#x27;만 표시" in page or "'충분 가능'만 표시" in page


def test_cli_writes_html(tmp_path):
    out = tmp_path / "report.html"
    code = main([MANIFEST_JSON,
                 "--templates", CLINICAL_PACK,
                 "--html", str(out), "--top", "2"])
    assert code == 0
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>")
    assert "요약 매트릭스" in page


def test_cli_rejects_html_colliding_with_another_output(tmp_path, capsys):
    path = tmp_path / "same"
    code = main([MANIFEST_JSON,
                 "--csv", str(path), "--html", str(path)])
    assert code == 2
    assert "같은 경로" in capsys.readouterr().err


def test_cli_html_write_failure_is_a_clean_exit(tmp_path, capsys):
    code = main([MANIFEST_JSON,
                 "--html", str(tmp_path / "no" / "such" / "dir.html")])
    assert code == 2
    assert "출력 파일 쓰기 오류" in capsys.readouterr().err


# --- CSV delimiter sniffing -------------------------------------------------

SEMI = "모달리티;n;변수\n임상결과;120;arm;endpoint\n"
COMMA = "modality,n,variables\nclinical,120,arm;endpoint\n"


def test_semicolon_export_is_read_not_rejected():
    """Excel writes ';' whenever the OS list separator is ';'."""
    m = parse_csv_manifest(SEMI.replace(";endpoint", "|endpoint"), delimiter=None)
    assert [d.modality for d in m.datasets] == ["clinical"]
    assert m.datasets[0].n == 120
    assert any("세미콜론" in w for w in m.warnings)


def test_tab_separated_file_named_csv_is_read():
    text = "modality\tn\tvariables\nclinical\t88\ta;b\n"
    m = parse_csv_manifest(text, delimiter=None)
    assert m.datasets[0].n == 88
    assert m.datasets[0].variables == ["a", "b"]
    assert any("탭" in w for w in m.warnings)


def test_comma_stays_the_default_and_warns_about_nothing():
    m = parse_csv_manifest(COMMA, delimiter=None)
    assert m.datasets[0].n == 120
    assert not any("구분자" in w for w in m.warnings)


def test_explicit_delimiter_is_still_honoured():
    """Passing a delimiter must not silently re-sniff a different one."""
    with pytest.raises(ManifestError):
        parse_csv_manifest(SEMI, delimiter=",")


def test_sniffing_prefers_the_separator_that_finds_a_modality_column():
    """Header column count alone would pick the wrong separator here.

    ``modality;n|variables|study`` splits into 4 fields on '|' but only 3 on
    ';' — yet only the ';' split yields a usable ``modality`` column. Ranking on
    field count alone (or dropping the modality term) picks '|' and the file
    then fails with "modality 열이 필요합니다" about a column it plainly has.
    """
    assert _sniff_delimiter("modality;n|variables|study\n") == ";"
    m = parse_csv_manifest(
        "modality;n|variables|study\nclinical;50|a;b|S\n", delimiter=None
    )
    assert m.datasets[0].modality == "clinical"


def test_sniffing_is_not_fooled_by_delimiters_inside_quoted_cells():
    text = 'modality;n;notes\nclinical;50;"a, b, c, d"\n'
    m = parse_csv_manifest(text, delimiter=None)
    assert m.datasets[0].modality == "clinical"
    assert m.datasets[0].n == 50


def test_sniffing_is_not_fooled_by_pipes_used_as_variable_separators():
    """'|' is documented as an in-cell variable separator AND a candidate."""
    text = "modality,n,variables\nclinical,50,a|b|c|d|e\n"
    assert _sniff_delimiter(text) == ","
    m = parse_csv_manifest(text, delimiter=None)
    assert m.datasets[0].variables == ["a", "b", "c", "d", "e"]


def test_load_manifest_sniffs_a_mislabelled_csv(tmp_path):
    path = tmp_path / "inventory.csv"
    path.write_text(SEMI, encoding="utf-8")
    m = load_manifest(str(path))
    assert m.datasets[0].n == 120
    assert m.study == "inventory"


def test_load_manifest_keeps_tsv_explicit(tmp_path):
    path = tmp_path / "inv.tsv"
    path.write_text("modality\tn\nclinical\t7\n", encoding="utf-8")
    m = load_manifest(str(path))
    assert m.datasets[0].n == 7
    assert not any("구분자" in w for w in m.warnings)


def test_a_file_genuinely_missing_modality_still_says_so():
    with pytest.raises(ManifestError) as exc:
        parse_csv_manifest("name;count\nfoo;3\n", delimiter=None)
    assert "modality" in str(exc.value)


def test_sniffer_survives_leading_comments_and_blank_lines():
    text = "# 2026 인벤토리\n\n모달리티;n\n임상결과;42\n"
    m = parse_csv_manifest(text, delimiter=None)
    assert m.datasets[0].n == 42


def test_sniffer_survives_an_empty_file():
    with pytest.raises(ManifestError):
        parse_csv_manifest("", delimiter=None)


def test_sniffed_semicolon_file_runs_end_to_end(tmp_path, capsys):
    path = tmp_path / "inv.csv"
    path.write_text("모달리티;n;변수\n임상결과;300;arm|endpoint\n", encoding="utf-8")
    code = main([str(path), "--templates", CLINICAL_PACK,
                 "--no-builtin"])
    assert code == 0
    assert "세미콜론" in capsys.readouterr().out


# --- regression: test/security review round 3 --------------------------------

def test_csv_neutralises_spreadsheet_formula_injection():
    """--csv exists to be opened in Excel, and pack text lands in cells."""
    from paperforge.report import render_csv

    import csv as _csv
    import io as _io

    # A leading tab/CR is stripped by template validation before it ever
    # reaches a cell, so the payloads that actually survive to --csv are the
    # four formula leads.
    payloads = ["=cmd|'/C calc'!A0", "+1+1", "@SUM(1:1)", "-2+3"]
    for payload in payloads:
        _, results = _run(templates=_tpl(title=payload, journal=payload))
        rows = list(_csv.reader(_io.StringIO(render_csv(results))))
        cells = dict(zip(rows[0], rows[1]))
        # The cell still READS as the original text; it is just no longer a
        # formula, because Excel only evaluates a cell whose first character is
        # one of = + - @.
        for field in ("title", "journal"):
            assert cells[field] == "'" + payload
            assert cells[field][:1] not in ("=", "+", "-", "@", "\t", "\r")


def test_csv_leaves_real_numbers_alone():
    """A negative number is arithmetic, not an injection — don't quote it."""
    from paperforge.report import render_csv

    _, results = _run()
    header, row = render_csv(results).splitlines()[:2]
    cols = dict(zip(header.split(","), row.split(",")))
    assert cols["required_n"] == str(results[0].required_n)
    assert not cols["required_n"].startswith("'")
    assert cols["rank"] == "1"


def test_manifest_text_cannot_emit_terminal_control_sequences():
    """A CSV someone e-mailed must not clear the reader's screen or retitle
    their terminal window when the report is printed."""
    evil = "STUDY\x1b[2J\x1b[1;31mHACKED\x07\r\x1b]0;pwned\x07"
    m = parse_manifest({
        "study": evil,
        "datasets": [{"name": evil, "modality": "clinical", "n": 10,
                      "variables": [evil], "notes": evil}],
    })
    for text in (m.study, m.datasets[0].name, m.datasets[0].notes,
                 m.datasets[0].variables[0]):
        assert "\x1b" not in text
        assert "\x07" not in text
        assert "\n" not in text and "\r" not in text
    assert "STUDY" in m.study and "HACKED" in m.study  # content is kept


def test_control_stripping_keeps_ordinary_korean_and_symbols():
    m = parse_manifest({
        "study": "수면 MoA 코호트 (예시) — α=0.05 · 90%",
        "datasets": [{"name": "EEG·호흡", "modality": "eeg", "n": 10,
                      "variables": ["delta_power", "θ/α ratio"]}],
    })
    assert m.study == "수면 MoA 코호트 (예시) — α=0.05 · 90%"
    assert m.datasets[0].variables == ["delta_power", "θ/α ratio"]


def test_output_collision_is_detected_through_a_symlink(tmp_path, capsys):
    """abspath let two flags share one file: both wrote, both reported success,
    and only the last survived."""
    real = tmp_path / "real.html"
    real.write_text("", encoding="utf-8")
    alias = tmp_path / "alias.html"
    os.symlink(real, alias)
    code = main([MANIFEST_JSON, "--html", str(real), "--out", str(alias)])
    assert code == 2
    assert "같은 경로" in capsys.readouterr().err
    assert real.read_text(encoding="utf-8") == ""


def test_distinct_output_paths_are_still_allowed(tmp_path):
    code = main([MANIFEST_JSON, "--top", "1",
                 "--html", str(tmp_path / "a.html"),
                 "--out", str(tmp_path / "b.md"),
                 "--csv", str(tmp_path / "c.csv"),
                 "--json", str(tmp_path / "d.json")])
    assert code == 0
    assert all((tmp_path / n).stat().st_size > 0
               for n in ("a.html", "b.md", "c.csv", "d.json"))


# --- regression: edge-case review round 3 -----------------------------------

def test_sniffing_never_reinterprets_a_file_that_already_parsed_as_csv():
    """A comma header whose COLUMN NAME contains ';' used to sniff to ';' and
    silently yield different modalities, different N, different verdicts."""
    text = "modality,비고;type;n\neeg,x;clinical;40\nwatch,y;lab;99\n"
    assert _sniff_delimiter(text) == ","
    old = parse_csv_manifest(text, delimiter=",")
    new = parse_csv_manifest(text, delimiter=None)
    assert [(d.modality, d.n) for d in old.datasets] == \
           [(d.modality, d.n) for d in new.datasets] == \
           [("eeg", None), ("watch", None)]


def test_comma_wins_outright_whenever_it_finds_a_modality_column():
    for text in ("modality,n|hz\nclinical,40|1\n",
                 'modality,"n;표본수"\nclinical,40\n',
                 "MODALITY,N\nclinical,40\n"):
        assert _sniff_delimiter(text) == ","


def test_unterminated_quote_is_named_as_such():
    """csv swallows every following line into one field; the rows just vanish."""
    text = 'name,modality,n\nA,eeg,40\n"B,watch,50\nC,clinical,60\n'
    m = parse_csv_manifest(text, delimiter=None)
    assert [d.name for d in m.datasets] == ["A"]
    assert any("따옴표" in w for w in m.warnings)


def test_unterminated_quote_that_eats_everything_still_explains_itself():
    """When nothing usable survives, the error must name the cause too — the
    bare "유효한 데이터셋 행이 없습니다" misdiagnoses a quoting problem."""
    with pytest.raises(ManifestError) as exc:
        parse_csv_manifest('name,modality\n"A,eeg\nB,watch\n', delimiter=None)
    assert "따옴표" in str(exc.value)


def test_clean_csv_does_not_claim_a_quoting_problem():
    m = parse_csv_manifest('modality,n,notes\nclinical,40,"a, b"\n', delimiter=None)
    assert not any("따옴표" in w for w in m.warnings)


@pytest.mark.parametrize("enc", ["utf-16-le", "utf-16-be"])
def test_bom_less_utf16_is_decoded_not_misdiagnosed(enc, tmp_path):
    """It used to fall through to CP949 and blame a missing modality column."""
    path = tmp_path / "excel.csv"
    path.write_bytes("name,modality,n\n뇌파,eeg,40\n".encode(enc))
    m = load_manifest(str(path))
    assert [(d.modality, d.n) for d in m.datasets] == [("eeg", 40)]
    assert any("BOM 없는" in w for w in m.warnings)


def test_utf8_is_never_shadowed_by_the_utf16_heuristic(tmp_path):
    path = tmp_path / "plain.csv"
    path.write_text("name,modality,n\n뇌파,eeg,40\n", encoding="utf-8")
    m = load_manifest(str(path))
    assert [(d.modality, d.n) for d in m.datasets] == [("eeg", 40)]
    assert not any("UTF-16" in w or "BOM 없는" in w for w in m.warnings)


@pytest.mark.parametrize("header", ['"#id",modality,n', "#name,modality,n"])
def test_a_header_whose_first_column_starts_with_hash_is_not_a_comment(header):
    m = parse_csv_manifest(f"{header}\n1,eeg,40\n", delimiter=None)
    assert [(d.modality, d.n) for d in m.datasets] == [("eeg", 40)]


def test_a_real_leading_comment_is_still_skipped():
    m = parse_csv_manifest("# 2026 인벤토리\n\nmodality,n\neeg,40\n", delimiter=None)
    assert [(d.modality, d.n) for d in m.datasets] == [("eeg", 40)]


def test_hardlinked_output_paths_are_detected(tmp_path, capsys):
    """realpath does not see a hard link, where two names ARE one file."""
    a = tmp_path / "x.md"
    a.write_text("", encoding="utf-8")
    b = tmp_path / "y.html"
    os.link(a, b)
    code = main([MANIFEST_JSON, "--out", str(a), "--html", str(b)])
    assert code == 2
    assert "같은 경로" in capsys.readouterr().err
    assert a.read_text(encoding="utf-8") == ""
