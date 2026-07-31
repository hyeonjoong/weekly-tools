"""Real-world messy-inventory handling: sample-size cells, duplicate columns, junk."""
import pytest

from paperforge.manifest import (
    _clean_count,
    parse_csv_manifest,
    parse_manifest,
)


def _n_of(man, modality="eeg"):
    for d in man.datasets:
        if d.modality == modality:
            return d.n
    raise AssertionError("modality not found")


@pytest.mark.parametrize("raw,expected", [
    (40, 40),
    (40.0, 40),
    ("40", 40),
    ("40.0", 40),
    (" 40 ", 40),
    ("1,234", 1234),
    ("1 234", 1234),
    ("40명", 40),
    ("40 명", 40),
    ("120 subjects", 120),
])
def test_clean_count_parses_real_world_spellings(raw, expected):
    assert _clean_count(raw) == expected


@pytest.mark.parametrize("raw", ["", "-", "n/a", "N/A", "미상", "없음", "TBD",
                                 "unknown", "?", None])
def test_clean_count_treats_placeholders_as_absent(raw):
    assert _clean_count(raw) is None


@pytest.mark.parametrize("raw", ["lots", "40.7", "-5", "0", True, False,
                                 "inf", "nan", "1e400"])
def test_clean_count_rejects_nonsense(raw):
    with pytest.raises((ValueError, OverflowError)):
        _clean_count(raw)


def test_placeholder_n_is_silent_but_garbage_warns():
    quiet = parse_manifest({"datasets": [{"modality": "eeg", "n": "미상"}]})
    assert _n_of(quiet) is None
    assert quiet.warnings == []

    noisy = parse_manifest({"datasets": [{"modality": "eeg", "n": "약 마흔 명"}]})
    assert _n_of(noisy) is None
    assert any("positive integer" in w for w in noisy.warnings)


def test_thousands_separator_survives_csv_quoting():
    # Excel quotes a thousands-separated number, so it stays one CSV field.
    man = parse_csv_manifest('modality,n\neeg,"1,024"\n')
    assert _n_of(man) == 1024


def test_variables_are_stripped_deduped_and_blank_filtered():
    man = parse_csv_manifest(
        "modality,variables\n"
        "eeg,  alpha_power ; Alpha_Power ;; theta_power |  \n"
    )
    assert man.datasets[0].variables == ["alpha_power", "theta_power"]


def test_json_variables_dedupe_too():
    man = parse_manifest({"datasets": [
        {"modality": "eeg", "variables": ["a", "A", " a ", "", "b"]},
    ]})
    assert man.datasets[0].variables == ["a", "b"]


def test_infinite_and_nan_sampling_hz_do_not_crash():
    man = parse_manifest({"datasets": [
        {"modality": "eeg", "n": 30, "sampling_hz": "not-a-number"},
        {"modality": "watch", "n": 30, "sampling_hz": None},
    ]})
    assert man.datasets[0].sampling_hz is None


def test_csv_with_only_linkage_rows_is_an_error_not_a_silent_empty_run():
    from paperforge.manifest import ManifestError
    with pytest.raises(ManifestError):
        parse_csv_manifest("modality,n\neeg+watch,30\n")


def test_linkage_row_without_n_is_ignored_with_a_warning():
    man = parse_csv_manifest(
        "modality,n\neeg,50\nrespiration,50\neeg+respiration,\n"
    )
    assert man.linked_n == {}
    assert any("양의 정수가 아니라" in w for w in man.warnings)
    assert len(man.datasets) == 2


def test_user_test_alias_is_not_split_as_a_linkage_row():
    # 'user_test' contains no standalone 'x'/'and'; it must stay one modality.
    man = parse_csv_manifest("modality,n\nuser_test,20\n")
    assert [d.modality for d in man.datasets] == ["behavior"]
    assert man.linked_n == {}


def test_large_inventory_stays_fast_and_correct():
    rows = ["modality,n,variables"]
    for i in range(2000):
        rows.append(f"eeg,{i + 1},v{i}")
    man = parse_csv_manifest("\n".join(rows))
    assert len(man.datasets) == 2000
    # Engine must take the conservative minimum across the duplicate modality.
    from paperforge.engine import evaluate
    results = evaluate(man)
    assert results and all(r.available_n == 1 for r in results)
    assert any("보수적으로 사용" in w for w in man.warnings)
