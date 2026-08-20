"""번들 수집 — 6개 형식, 좌표 보존, 그리고 '읽지 못한 파일' 자백."""

import os
import zipfile

import pytest
from conftest import make_bundle, make_xlsx, write

from tracecheck.bundle import collect, require_outputs
from tracecheck.safety import InputError


def cells_of(bundle):
    return {(c.rel, c.row, c.col, str(c.value)) for c in bundle.cells}


def test_csv_keeps_row_and_header_column(tmp_path):
    root = make_bundle(tmp_path / "out",
                       {"a.csv": "group,mean,sd\nA,12.44,4.08\n"})
    bundle = collect([root], "현재")
    assert ("a.csv", 2, "mean", "12.44") in cells_of(bundle)
    assert ("a.csv", 2, "sd", "4.08") in cells_of(bundle)
    assert bundle.file_count == 1


def test_tsv_and_semicolon_csv(tmp_path):
    root = make_bundle(tmp_path / "out", {
        "a.tsv": "g\tmean\nA\t1.5\n",
        "b.csv": "g;mean\nA;2.5\n"})
    bundle = collect([root], "현재")
    values = {str(c.value) for c in bundle.cells}
    assert {"1.5", "2.5"} <= values


def test_numeric_header_row_gets_positional_labels(tmp_path):
    """헤더가 없는 CSV 에서 첫 행을 열 이름으로 쓰면 좌표가 거짓말이 됩니다."""
    root = make_bundle(tmp_path / "out", {"a.csv": "1.5,2.5\n3.5,4.5\n"})
    bundle = collect([root], "현재")
    assert ("a.csv", 1, "열1", "1.5") in cells_of(bundle)


def test_duplicate_headers_are_disambiguated(tmp_path):
    root = make_bundle(tmp_path / "out", {"a.csv": "mean,mean\n1.5,2.5\n"})
    bundle = collect([root], "현재")
    cols = {c.col for c in bundle.cells}
    assert cols == {"mean", "mean#2"}


def test_json_key_path_is_the_coordinate(tmp_path):
    root = make_bundle(tmp_path / "out",
                       {"r.json": '{"isi": {"mean": 12.44}, "arms": [{"n": 42}]}'})
    bundle = collect([root], "현재")
    coords = {(c.col, str(c.value)) for c in bundle.cells}
    assert ("isi.mean", "12.44") in coords
    assert ("arms[0].n", "42") in coords


def test_json_strings_with_numbers_are_indexed(tmp_path):
    root = make_bundle(tmp_path / "out", {"r.json": '{"note": "mean was 12.44"}'})
    bundle = collect([root], "현재")
    assert "12.44" in {str(c.value) for c in bundle.cells}


def test_json_booleans_are_not_numbers(tmp_path):
    root = make_bundle(tmp_path / "out", {"r.json": '{"ok": true, "no": false}'})
    assert collect([root], "현재").cells == []


def test_xlsx_sheet_row_column(tmp_path):
    path = str(tmp_path / "out" / "book.xlsx")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    make_xlsx(path, {"결과": [["metric", "mean"], ["ISI", 12.44]]})
    bundle = collect([str(tmp_path / "out")], "현재")
    cell = [c for c in bundle.cells if str(c.value) == "12.44"][0]
    assert (cell.sheet, cell.row, cell.col) == ("결과", 2, "B")
    assert "결과·2행·B열" == cell.loc


def test_md_table_and_plain_text(tmp_path):
    root = make_bundle(tmp_path / "out", {
        "t.md": "| a | b |\n|---|---|\n| 1.5 | 2.5 |\n",
        "log.txt": "mean = 3.5\n"})
    bundle = collect([root], "현재")
    values = {str(c.value) for c in bundle.cells}
    assert {"1.5", "2.5", "3.5"} <= values


def test_recursive_collection_and_relative_paths(tmp_path):
    root = make_bundle(tmp_path / "out", {
        os.path.join("sub", "deep.csv"): "m\n7.7\n"})
    bundle = collect([root], "현재")
    assert bundle.cells[0].rel == os.path.join("sub", "deep.csv")
    assert bundle.cells[0].file.endswith(os.path.join("out", "sub", "deep.csv"))


def test_multiple_roots(tmp_path):
    a = make_bundle(tmp_path / "a", {"x.csv": "m\n1.5\n"})
    b = make_bundle(tmp_path / "b", {"y.csv": "m\n2.5\n"})
    bundle = collect([a, b], "현재")
    assert bundle.file_count == 2


# --------------------------------------------------------------------------- #
# 읽지 못한 파일은 반드시 자백
# --------------------------------------------------------------------------- #

def test_known_unreadable_formats_are_confessed(tmp_path):
    root = make_bundle(tmp_path / "out", {"old.xls": "x", "scan.pdf": "%PDF"})
    write(os.path.join(root, "good.csv"), "m\n1.5\n")
    bundle = collect([root], "현재")
    reasons = dict((os.path.basename(f), why) for f, why in bundle.unread)
    assert "old.xls" in reasons and ".xls" in reasons["old.xls"]
    assert "scan.pdf" in reasons
    assert bundle.file_count == 1


def test_corrupt_xlsx_is_confessed_not_crashed(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    write(os.path.join(root, "broken.xlsx"), "not a zip at all")
    write(os.path.join(root, "ok.csv"), "m\n1.5\n")
    bundle = collect([root], "현재")
    assert any("broken.xlsx" in f for f, _ in bundle.unread)
    assert bundle.cell_count == 1


def test_encrypted_xlsx_is_confessed(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    path = os.path.join(root, "locked.xlsx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("EncryptedPackage", b"\x00\x01\x02")
    bundle = collect([root], "현재")
    assert bundle.unread and "locked.xlsx" in bundle.unread[0][0]


def test_xlsx_with_dtd_is_refused(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    path = os.path.join(root, "bomb.xlsx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml",
                    '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY b "b">]><workbook/>')
    bundle = collect([root], "현재")
    assert any("DTD" in why for _f, why in bundle.unread)


def test_zip_path_traversal_is_refused(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    path = os.path.join(root, "evil.xlsx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("../../escape.xml", "<a/>")
        zf.writestr("xl/workbook.xml", "<workbook/>")
    bundle = collect([root], "현재")
    assert any("경로 탈출" in why for _f, why in bundle.unread)


def test_symlinked_file_is_skipped_and_confessed(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    real = write(str(tmp_path / "secret.csv"), "m\n9.9\n")
    os.symlink(real, os.path.join(root, "link.csv"))
    bundle = collect([root], "현재")
    assert bundle.cells == []
    assert any("심볼릭" in why for _f, why in bundle.unread)


def test_max_files_limit_is_confessed(tmp_path):
    root = make_bundle(tmp_path / "out",
                       {"a%d.csv" % i: "m\n%d.5\n" % i for i in range(5)})
    bundle = collect([root], "현재", max_files=2)
    assert bundle.file_count == 2 and bundle.truncated
    assert any("--max-files" in why for _f, why in bundle.unread)


def test_max_bytes_limit_is_confessed(tmp_path):
    root = make_bundle(tmp_path / "out", {"big.csv": "m\n" + "1.5\n" * 500})
    bundle = collect([root], "현재", max_bytes=10)
    assert bundle.truncated and bundle.cell_count == 0


def test_max_cells_limit_is_confessed(tmp_path):
    root = make_bundle(tmp_path / "out",
                       {"a.csv": "m\n" + "".join("%d.5\n" % i for i in range(50))})
    bundle = collect([root], "현재", max_cells=10)
    assert bundle.cell_count == 10 and bundle.truncated


def test_binary_file_with_csv_extension_is_confessed(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    with open(os.path.join(root, "weird.csv"), "wb") as handle:
        handle.write(b"\x00\x01\x02binary")
    bundle = collect([root], "현재")
    assert bundle.unread and bundle.cell_count == 0


def test_cp949_csv_is_read(tmp_path):
    root = str(tmp_path / "out")
    os.makedirs(root)
    with open(os.path.join(root, "kr.csv"), "wb") as handle:
        handle.write("지표,평균\n불면증,12.44\n".encode("cp949"))
    bundle = collect([root], "현재")
    assert "12.44" in {str(c.value) for c in bundle.cells}


def test_hidden_files_and_office_lockfiles_are_ignored(tmp_path):
    root = make_bundle(tmp_path / "out", {".hidden.csv": "m\n1.5\n",
                                          "~$draft.xlsx": "junk",
                                          "real.csv": "m\n2.5\n"})
    bundle = collect([root], "현재")
    assert bundle.file_count == 1 and bundle.cell_count == 1


def test_empty_bundle_folder(tmp_path):
    root = str(tmp_path / "empty")
    os.makedirs(root)
    bundle = collect([root], "현재")
    assert bundle.cell_count == 0 and bundle.file_count == 0


def test_single_file_as_bundle(tmp_path):
    path = write(str(tmp_path / "only.csv"), "m\n1.5\n")
    bundle = collect([path], "현재")
    assert bundle.cell_count == 1


def test_require_outputs_is_the_boundary_guard():
    with pytest.raises(InputError) as exc:
        require_outputs(None)
    assert "numcheck" in str(exc.value)
    require_outputs(["x"])          # 예외 없음
