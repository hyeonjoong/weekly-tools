"""Regressions for the 2026-07-31 adversarial edge-case review.

Every test names the exact old failure (traceback, hang, or silently wrong
number) so a regression is unambiguous.
"""
import json
import time

import pytest

from paperforge import power
from paperforge.cli import main
from paperforge.manifest import ManifestError, _clean_count, parse_csv_manifest


def _write(tmp_path, data, name="m.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


_TWO = {"datasets": [{"modality": "eeg", "n": 90, "variables": ["a"]},
                     {"modality": "respiration", "n": 90, "variables": ["b"]}]}


# --- 1. effect sizes that underflow to a zero denominator -------------------

def test_microscopic_effect_scale_is_a_clean_error_not_zerodivision(
        tmp_path, capsys):
    """Old: ZeroDivisionError traceback from n_for_correlation."""
    path = _write(tmp_path, _TWO)
    assert main([path, "--effect-scale", "1e-16"]) == 2
    err = capsys.readouterr().err
    assert "분석 오류" in err
    assert "Traceback" not in err


def test_microscopic_template_effect_is_a_clean_error(tmp_path, capsys):
    pack = tmp_path / "p.json"
    pack.write_text(json.dumps({"templates": [{
        "id": "tiny", "title": "t", "required": ["eeg"], "optional": [],
        "hypothesis": "h", "predictors": ["p"], "outcomes": ["o"],
        "analysis": "a", "design": "d", "journal": "j", "novelty": "n",
        "effect": {"type": "correlation", "r": 1e-300},
    }]}), encoding="utf-8")
    path = _write(tmp_path, _TWO)
    assert main([path, "--templates", str(pack), "--no-builtin"]) == 2
    assert "분석 오류" in capsys.readouterr().err


@pytest.mark.parametrize("fn,args", [
    (power.n_for_correlation, (1e-17,)),
    (power.n_for_correlation, (1e-300,)),
    (power.n_for_paired, (1e-300,)),
    (power.n_per_group_two_means, (1e-300,)),
    (power.n_total_two_group, (1e-300,)),
])
def test_underflowing_effect_raises_valueerror(fn, args):
    with pytest.raises(ValueError):
        fn(*args)


def test_required_n_has_a_ceiling():
    with pytest.raises(ValueError, match="넘습니다|exceeds"):
        power.n_for_correlation(1e-8)


# --- 2. deeply nested JSON -> RecursionError --------------------------------

def test_deeply_nested_json_manifest_exits_2(tmp_path, capsys):
    p = tmp_path / "deep.json"
    p.write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
    assert main([str(p)]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_deeply_nested_json_template_pack_exits_2(tmp_path, capsys):
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
    path = _write(tmp_path, _TWO)
    assert main([path, "--templates", str(deep)]) == 2
    assert "Traceback" not in capsys.readouterr().err


# --- 3/4/5. huge repeats / non-centrality: no overflow, no hang -------------

def test_absurd_repeats_is_a_usage_error(tmp_path, capsys):
    """Old: OverflowError from design_effect (int too large to convert)."""
    path = _write(tmp_path, _TWO)
    with pytest.raises(SystemExit) as exc:
        main([path, "--repeats", str(10 ** 400)])
    assert exc.value.code == 2
    assert "--repeats" in capsys.readouterr().err


def test_design_effect_rejects_absurd_repeats():
    with pytest.raises(ValueError):
        power.design_effect(10 ** 400, 0.3)


def test_huge_noncentrality_returns_fast_instead_of_hanging():
    """Old: the Poisson mixture walked ~sqrt(lam) terms — 7.6 s at n=1e12, and
    math.lgamma overflowed outright beyond that."""
    start = time.monotonic()
    assert power.power_for_regression(0.15, 10 ** 12, 3) == 1.0
    assert power._ncf_cdf(2.0, 3, 40, 1e300) == 0.0
    assert time.monotonic() - start < 2.0


def test_enormous_effect_scale_terminates_cleanly(tmp_path, capsys):
    """Old: d**2 overflowed (OverflowError) or fed a lam so large the Poisson
    mixture ran for minutes. Now it is a fast, clean exit 2."""
    path = _write(tmp_path, _TWO)
    start = time.monotonic()
    assert main([path, "--effect-scale", "1e200", "--top", "2"]) == 2
    err = capsys.readouterr().err
    assert "분석 오류" in err and "Traceback" not in err
    assert time.monotonic() - start < 15.0


@pytest.mark.parametrize("fn,args", [
    (power.n_for_paired, (1e200,)),
    (power.n_per_group_two_means, (1e200,)),
    (power.n_total_two_group, (1e200,)),
    (power.n_for_regression, (1e300, 1)),
    (power.n_for_regression_change, (1e300, 1, 1)),
])
def test_absurdly_large_effect_raises_valueerror(fn, args):
    with pytest.raises(ValueError):
        fn(*args)


# --- 6. comma stripping turned "40,50" into 4050 ---------------------------

@pytest.mark.parametrize("raw,expected", [("1,234", 1234),
                                          ("12,345,678", 12345678)])
def test_well_formed_thousands_separators_still_parse(raw, expected):
    assert _clean_count(raw) == expected


@pytest.mark.parametrize("raw", ["40,50", "1,5", "1,2,3", "1,23", "12,3456"])
def test_ambiguous_commas_are_rejected_not_concatenated(raw):
    """Old: '40,50' -> 4050 silently, flipping the verdict to 충분 가능."""
    with pytest.raises(ValueError):
        _clean_count(raw)


def test_ambiguous_comma_in_csv_warns_and_drops_the_value(tmp_path, capsys):
    p = tmp_path / "c.csv"
    p.write_text('modality,n\neeg,"40,50"\n', encoding="utf-8")
    man = parse_csv_manifest(p.read_text(encoding="utf-8"))
    assert man.datasets[0].n is None
    assert any("positive integer" in w for w in man.warnings)


def test_sample_size_ceiling_keeps_absurd_counts_out_of_the_table():
    """Old: n=1e308 became a real 309-digit int and wrecked the report."""
    from paperforge.manifest import parse_manifest
    man = parse_manifest({"datasets": [{"modality": "eeg", "n": 1e308}]})
    assert man.datasets[0].n is None
    assert man.warnings


# --- 7. a footnote marker deleted an entire dataset ------------------------

@pytest.mark.parametrize("cell", ["EEG*", "respiration+", "eeg&", "워치×"])
def test_footnote_markers_do_not_become_linkage_rows(cell):
    """Old: '*'/'+' matched the linkage regex, so the row vanished from the
    manifest and the user got a warning about a feature they never used."""
    man = parse_csv_manifest(f"modality,n\n{cell},40\nwatch,50\n")
    assert len(man.datasets) == 2
    assert man.linked_n == {}


def test_real_linkage_rows_still_work():
    man = parse_csv_manifest("modality,n\neeg,90\nwatch,90\neeg+watch,42\n")
    assert len(man.datasets) == 2
    assert man.linked_n == {frozenset({"eeg", "watch"}): 42}


# --- 9. colliding output paths silently destroyed the earlier file ---------

@pytest.mark.parametrize("a,b", [("--out", "--csv"), ("--out", "--json"),
                                 ("--csv", "--json")])
def test_colliding_output_paths_are_refused(tmp_path, capsys, a, b):
    path = _write(tmp_path, _TWO)
    target = str(tmp_path / "same.txt")
    assert main([path, a, target, b, target]) == 2
    assert "같은 경로" in capsys.readouterr().err


def test_distinct_output_paths_all_written(tmp_path):
    path = _write(tmp_path, _TWO)
    md, csv_p, js = (str(tmp_path / n) for n in ("a.md", "b.csv", "c.json"))
    assert main([path, "--out", md, "--csv", csv_p, "--json", js]) == 0
    for f in (md, csv_p, js):
        assert open(f, encoding="utf-8").read().strip()


# --- 10/11. ragged rows and unterminated quotes ---------------------------

def test_ragged_rows_are_reported():
    man = parse_csv_manifest("modality,n\neeg,40,EXTRA,MORE\nwatch,50\n")
    assert any("열 수가 다른 행" in w for w in man.warnings)


def test_well_formed_csv_has_no_ragged_warning():
    man = parse_csv_manifest("modality,n\neeg,40\nwatch,50\n")
    assert not any("열 수가 다른 행" in w for w in man.warnings)


def test_warning_text_never_contains_raw_newlines(tmp_path, capsys):
    """Old: an unterminated quote swallowed the file into one field and the
    warning dumped it back, breaking the Markdown blockquote."""
    p = tmp_path / "bq.csv"
    p.write_text('modality,n\n"eeg,40\nwatch,50\n', encoding="utf-8")
    man = parse_csv_manifest(p.read_text(encoding="utf-8"))
    for w in man.warnings:
        assert "\n" not in w and "\r" not in w
        assert len(w) < 300


# --- 12. --repeats without --icc silently assumed independence ------------

def test_repeats_without_icc_warns(tmp_path, capsys):
    path = _write(tmp_path, _TWO)
    assert main([path, "--repeats", "3", "--top", "1"]) == 0
    assert "--icc 가 없어" in capsys.readouterr().out


def test_repeats_with_explicit_icc_does_not_warn(tmp_path, capsys):
    path = _write(tmp_path, _TWO)
    assert main([path, "--repeats", "3", "--icc", "0.0", "--top", "1"]) == 0
    assert "--icc 가 없어" not in capsys.readouterr().out


# --- 13. giant inventories produced an unreadable report ------------------

def test_large_inventory_report_is_bounded(tmp_path, capsys):
    rows = ["modality,n,variables"]
    for i in range(4000):
        rows.append(f"unknownmode,{i + 1},v{i}")
    for i in range(300):
        rows.append(f"eeg,{500 + i},var_{i}")
    p = tmp_path / "big.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    start = time.monotonic()
    assert main([str(p), "--top", "2"]) == 0
    out = capsys.readouterr().out
    assert time.monotonic() - start < 30.0
    assert max(len(line) for line in out.splitlines()) < 5000
    assert "동일 경고" in out or out.count("unrecognized") <= 20


# --- 16. unbounded reads ---------------------------------------------------

def test_oversized_manifest_is_refused(tmp_path, capsys):
    p = tmp_path / "huge.csv"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("modality,n\n")
        fh.write("eeg,1\n" * 6_000_000)
    assert p.stat().st_size > 32 * 1024 * 1024
    assert main([str(p)]) == 2
    assert "너무 큽니다" in capsys.readouterr().err
