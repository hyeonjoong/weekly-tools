"""CSV/TSV manifest ingestion tests (messy real-world clinical data)."""
import pytest

from paperforge import manifest as M
from paperforge.engine import evaluate


def test_basic_csv_parses_with_korean_modalities():
    text = (
        "name,modality,n,variables,notes\n"
        "MoA EEG,뇌파,92,delta_power;theta_power,rest\n"
        "Watch,워치,90,rmssd|sdnn,\n"
    )
    man = M.parse_csv_manifest(text, study="s")
    assert man.study == "s"
    assert man.modalities() == {"eeg", "watch"}
    assert man.datasets[0].variables == ["delta_power", "theta_power"]
    assert man.datasets[1].variables == ["rmssd", "sdnn"]


def test_study_column_overrides_argument():
    text = "study,modality,n\nMy Cohort,eeg,40\n"
    man = M.parse_csv_manifest(text, study="fallback")
    assert man.study == "My Cohort"


def test_empty_n_cell_is_absent_not_warning():
    text = "modality,n,variables\neeg,,alpha_power\n"
    man = M.parse_csv_manifest(text)
    assert man.datasets[0].n is None
    assert man.warnings == []  # blank cell != invalid value


def test_missing_modality_column_rejected():
    with pytest.raises(M.ManifestError):
        M.parse_csv_manifest("name,n\nfoo,10\n")


def test_blank_and_comment_lines_skipped():
    text = (
        "# my data inventory\n"
        "\n"
        "modality,n\n"
        "eeg,40\n"
        "\n"
        "watch,50\n"
    )
    man = M.parse_csv_manifest(text)
    assert man.modalities() == {"eeg", "watch"}


def test_rows_without_modality_are_skipped():
    text = "modality,n\neeg,40\n,\nwatch,50\n"
    man = M.parse_csv_manifest(text)
    assert len(man.datasets) == 2


def test_all_rows_blank_rejected():
    with pytest.raises(M.ManifestError):
        M.parse_csv_manifest("modality,n\n,\n,\n")


def test_english_and_korean_headers_both_work():
    text = "종류,표본수,변수\neeg,40,a;b\n"
    man = M.parse_csv_manifest(text)
    assert man.datasets[0].modality == "eeg"
    assert man.datasets[0].n == 40
    assert man.datasets[0].variables == ["a", "b"]


def test_load_csv_file_by_extension(tmp_path):
    p = tmp_path / "inv.csv"
    p.write_text("modality,n,variables\neeg,40,alpha\nrespiration,40,resp_rate\n",
                 encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.study == "inv"  # filename stem
    ids = {r.idea_id for r in evaluate(man)}
    assert "eeg_resp_coupling" in ids


def test_load_tsv_file(tmp_path):
    p = tmp_path / "inv.tsv"
    p.write_text("modality\tn\tvariables\neeg\t40\talpha;theta\n", encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.datasets[0].modality == "eeg"
    assert man.datasets[0].variables == ["alpha", "theta"]


def test_load_dispatches_json_by_extension(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"study":"J","datasets":[{"modality":"eeg","n":30}]}',
                 encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.study == "J"


def test_bom_prefixed_csv(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes("﻿modality,n\neeg,40\n".encode("utf-8"))
    man = M.load_manifest(str(p))
    assert man.datasets[0].modality == "eeg"


def test_cp949_korean_csv_decoded_with_warning(tmp_path):
    p = tmp_path / "kr.csv"
    p.write_bytes("modality,notes\n뇌파,안정상태\n".encode("cp949"))
    man = M.load_manifest(str(p))
    assert man.datasets[0].modality == "eeg"
    assert any("UTF-8" in w for w in man.warnings)


def test_unknown_extension_sniffs_json(tmp_path):
    p = tmp_path / "m.dat"
    p.write_text('{"datasets":[{"modality":"eeg","n":30}]}', encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.datasets[0].modality == "eeg"


def test_unknown_extension_sniffs_csv(tmp_path):
    p = tmp_path / "m.dat"
    p.write_text("modality,n\neeg,30\n", encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.datasets[0].modality == "eeg"


def test_bad_n_in_csv_still_warns():
    text = "modality,n\neeg,lots\n"
    man = M.parse_csv_manifest(text)
    assert man.datasets[0].n is None
    assert any("positive integer" in w for w in man.warnings)


def test_variables_with_spaces_trimmed():
    text = "modality,variables\neeg, alpha ; theta \n"
    man = M.parse_csv_manifest(text)
    assert man.datasets[0].variables == ["alpha", "theta"]


# --- Regression tests for round-1 hardening findings ------------------------

def test_oversized_field_raises_manifest_error_not_csv_error():
    # A quoted cell beyond csv's 128 KB field limit must degrade to a clean
    # ManifestError (exit 2), not a raw csv.Error traceback.
    big = 'modality,variables\neeg,"' + "a" * 200_000 + '"\n'
    with pytest.raises(M.ManifestError):
        M.parse_csv_manifest(big)


def test_binary_blob_does_not_crash_uglily(tmp_path):
    p = tmp_path / "blob.dat"
    p.write_bytes(b"\x41" * 200_000)  # no delimiter/newline, > field limit
    with pytest.raises(M.ManifestError):
        M.load_manifest(str(p))


def test_hash_leading_data_row_not_dropped():
    # '#' comment stripping applies only BEFORE the header; a legitimate first
    # column value like "#3 EEG" in a data row must survive.
    text = "name,modality,n\n#3 EEG,eeg,40\nWatch,watch,50\n"
    man = M.parse_csv_manifest(text)
    assert [d.name for d in man.datasets] == ["#3 EEG", "Watch"]


def test_leading_comment_before_header_still_skipped():
    text = "# inventory v2\nmodality,n\neeg,40\n"
    man = M.parse_csv_manifest(text)
    assert man.datasets[0].modality == "eeg"


def test_duplicate_header_warns():
    man = M.parse_csv_manifest("modality,variables,variables\neeg,a;b,c;d\n")
    assert any("중복" in w for w in man.warnings)
    assert man.datasets[0].variables == ["c", "d"]  # last column wins


def test_csv_integer_float_string_n_accepted():
    # "40.0" in a CSV cell must behave like JSON 40.0 (accepted as 40).
    man = M.parse_csv_manifest("modality,n\neeg,40.0\n")
    assert man.datasets[0].n == 40
    assert man.warnings == []


def test_json_string_integer_float_n_accepted():
    man = M.parse_manifest({"datasets": [{"modality": "eeg", "n": "40.0"}]})
    assert man.datasets[0].n == 40


def test_csv_noninteger_float_string_n_rejected():
    man = M.parse_csv_manifest("modality,n\neeg,40.7\n")
    assert man.datasets[0].n is None
    assert any("positive integer" in w for w in man.warnings)


def test_json_content_in_csv_extension_retries(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text('{"datasets":[{"modality":"eeg","n":30}]}', encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.datasets[0].modality == "eeg"


def test_csv_content_in_json_extension_retries(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("modality,n\neeg,30\n", encoding="utf-8")
    man = M.load_manifest(str(p))
    assert man.datasets[0].modality == "eeg"


def test_broken_json_keeps_json_error(tmp_path):
    # A genuinely broken .json (not CSV either) keeps the JSON parse error.
    p = tmp_path / "bad.json"
    p.write_text("{not valid at all", encoding="utf-8")
    with pytest.raises(M.ManifestError) as exc:
        M.load_manifest(str(p))
    assert "JSON" in str(exc.value)
