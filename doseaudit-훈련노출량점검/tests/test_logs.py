"""워크북 묶음 파싱 — 규격이 다르면 **틀린 판정 대신 거절**."""

import os
import zipfile

import pytest

from doseaudit.errors import ReadError, RefuseError
from doseaudit.logs import (SPEC_HEADER, load_bundle, subject_code_from_filename)
from doseaudit.xlsxread import read_workbook
from xlsxwrite import MODULES, module_sheet, write_xlsx


@pytest.mark.parametrize("filename,expected", [
    ("3D609A5O_mystart_81.xlsx", "3D609A5O"),
    ("OBPOZZXNJX_Sieun63_155.xlsx", "OBPOZZXNJX"),      # 10자 — 길이로 검증하면 안 된다
    ("AB12_x_1.xlsx", "AB12"),
    ("/some/dir/CD99EF00GH_hotel_24.xlsx", "CD99EF00GH"),
    ("NOUNDERSCORE.xlsx", "NOUNDERSCORE"),
    ("ab12cd34_lower_1.xlsm", "ab12cd34"),
])
def test_파일명_첫_토큰만_피험자_코드다(filename, expected):
    assert subject_code_from_filename(filename) == expected


@pytest.mark.parametrize("filename", [
    "_login_1.xlsx", ".xlsx", "한글코드_x_1.xlsx", "A_x_1.xlsx", "!!_x_1.xlsx",
    ("X" * 40) + "_x_1.xlsx",
])
def test_코드를_못_뽑으면_읽지_못한_파일이다(filename):
    with pytest.raises(ReadError):
        subject_code_from_filename(filename)


def test_LoginID_는_코드에_섞이지_않는다():
    """`LoginID` 는 읽되 출력하지 않는다 — 여기서 아예 버린다."""
    assert "mystart" not in subject_code_from_filename("3D609A5O_mystart_81.xlsx")


def test_기본_묶음을_읽는다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    assert bundle.n_workbooks == 1
    assert bundle.n_sheets == len(MODULES)
    assert bundle.n_data_rows == 3
    assert bundle.subjects[0].code == "AB12CD34"


def test_헤더는_엑셀_행_번호로_기록된다(make_workbook, basic_rows, tmp_path):
    """오프셋 상수가 아니라 **찾은 값**이다 — `[요약]` 3행 + 빈 행 다음이 5행."""
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    assert bundle.subjects[0].modules["음소쌍"].header_row == 5


def test_요약블록_위치가_바뀌어도_헤더를_찾는다(tmp_path):
    rows = [["[요약]"], ["메모", "추가된 줄"], ["평균 소요시간", "5초"],
            ["평균 반복횟수", "2회"], None, None,
            list(SPEC_HEADER), ["2026.03.02", "1", "REGULAR", "2회", "5초"]]
    folder = tmp_path / "logs"
    folder.mkdir()
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"), [(m, rows) for m in MODULES])
    bundle = load_bundle(str(folder))
    assert bundle.n_data_rows == len(MODULES)
    assert bundle.subjects[0].modules["음소쌍"].header_row == 7


@pytest.mark.parametrize("bad_header", [
    ["날짜", "레벨", "훈련유형", "반복횟수", "소요시간"],
    ["날짜", "레벨", "훈련유형", "소요시간(초)", "반복횟수"],
    ["날짜", "레벨", "훈련 유형", "반복횟수", "소요시간(초)"],
    ["날짜", "레벨", "훈련유형", "반복횟수"],
    ["날짜"],
    ["날짜", "Level", "Type", "Reps", "Seconds"],
])
def test_헤더가_5열_규격과_다르면_판정하지_않는다(make_workbook, basic_rows, tmp_path, bad_header):
    """형식이 바뀌었는데 틀린 판정을 내놓는 것보다, 판정하지 않는 편이 낫다."""
    make_workbook("AB12CD34", {"음소쌍": basic_rows}, header=bad_header)
    with pytest.raises(RefuseError) as info:
        load_bundle(str(tmp_path / "logs"))
    assert "5열 규격" in str(info.value) or "헤더 행을 찾지" in str(info.value)


def test_헤더가_아예_없으면_거절(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"),
               [(m, [["[요약]"], ["평균 소요시간", "0초"]]) for m in MODULES])
    with pytest.raises(RefuseError):
        load_bundle(str(folder))


def test_데이터_0행_모듈은_읽히되_비어_있다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    assert bundle.subjects[0].modules["발성훈련"].is_empty is True
    assert bundle.subjects[0].modules["발성훈련"].n_rows == 0


def test_해석하지_못한_값을_조용히_버리지_않는다(tmp_path):
    rows = [["[요약]"], ["평균 소요시간", "0초"], ["평균 반복횟수", "0회"], None,
            list(SPEC_HEADER),
            ["2026.03.02", "1", "REGULAR", "2회", "--초"],
            ["뭐라고?", "1", "REGULAR", "2회", "5초"]]
    folder = tmp_path / "logs"
    folder.mkdir()
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"), [(MODULES[0], rows)]
               + [(m, [["[요약]"], None, list(SPEC_HEADER)]) for m in MODULES[1:]])
    bundle = load_bundle(str(folder))
    assert bundle.n_data_rows == 2
    assert len(bundle.parse_fail_rows) == 2
    reasons = " ".join(r[3] for r in bundle.parse_fail_rows)
    assert "소요시간" in reasons and "날짜" in reasons


def test_날짜를_못_읽은_행은_활동일에_쓰이지_않는다(tmp_path):
    rows = [["[요약]"], None, list(SPEC_HEADER),
            ["언제였더라", "1", "REGULAR", "2회", "5초"]]
    folder = tmp_path / "logs"
    folder.mkdir()
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"), [(m, rows) for m in MODULES])
    bundle = load_bundle(str(folder))
    assert all(not row.usable for subject in bundle.subjects for row in subject.rows())
    assert bundle.date_span() == (None, None)


def test_같은_코드의_워크북이_둘이면_합치지_않고_거절(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows}, login="one", seq=1)
    make_workbook("AB12CD34", {"음소쌍": basic_rows}, login="two", seq=2)
    with pytest.raises(RefuseError) as info:
        load_bundle(str(tmp_path / "logs"))
    assert "둘입니다" in str(info.value)


def test_못_읽는_파일은_죽지_않고_자백에_쌓인다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "ZZ99ZZ99_x_2.xlsx").write_bytes(b"not a workbook")
    bundle = load_bundle(str(tmp_path / "logs"))
    assert bundle.n_workbooks == 1
    assert len(bundle.unreadable) == 1
    assert "ZZ99ZZ99" in bundle.unreadable[0][0]


def test_CSV만_있으면_logflow_로_안내한다(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "events.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(RefuseError) as info:
        load_bundle(str(folder))
    assert "logflow" in str(info.value)


def test_읽을_워크북이_없으면_거절(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    with pytest.raises(RefuseError):
        load_bundle(str(folder))


def test_없는_경로는_거절(tmp_path):
    with pytest.raises(RefuseError):
        load_bundle(str(tmp_path / "nope"))


def test_엑셀_임시파일과_숨김파일은_건너뛴다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "~$AB12CD34_x_1.xlsx").write_bytes(b"lock")
    (tmp_path / "logs" / ".DS_Store").write_bytes(b"junk")
    bundle = load_bundle(str(tmp_path / "logs"))
    assert bundle.n_workbooks == 1
    assert bundle.unreadable == []


def test_하위_폴더는_한_겹만_읽는다고_자백한다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "더보기").mkdir()
    bundle = load_bundle(str(tmp_path / "logs"))
    assert any("하위 폴더" in reason for _n, reason in bundle.skipped_files)


def test_워크북_하나를_직접_줄_수도_있다(make_workbook, basic_rows):
    path = make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(path)
    assert bundle.n_workbooks == 1


def test_구형_xls_는_안내하고_건너뛴다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    (tmp_path / "logs" / "OLD1_x_9.xls").write_bytes(b"\xd0\xcf\x11\xe0")
    bundle = load_bundle(str(tmp_path / "logs"))
    assert any(".xlsx 로 저장" in reason for _n, reason in bundle.skipped_files)


def test_sharedStrings_를_쓰는_워크북도_읽는다(tmp_path):
    """벤더가 도구를 바꾸면 inline 대신 공유문자열로 올 수 있다."""
    folder = tmp_path / "logs"
    folder.mkdir()
    path = str(folder / "AB12CD34_x_1.xlsx")
    write_xlsx(path, [(m, module_sheet([["2026.03.02", "1", "REGULAR", "2회", "5초"]], 5, 2))
                      for m in MODULES])
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    shared = ["2026.03.02", "REGULAR", "2회", "5초", "날짜", "레벨", "훈련유형",
              "반복횟수", "소요시간(초)", "[요약]", "평균 소요시간", "평균 반복횟수"]
    xml = ('<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/'
           'spreadsheetml/2006/main" count="%d" uniqueCount="%d">%s</sst>'
           % (len(shared), len(shared), "".join("<si><t>%s</t></si>" % s for s in shared)))
    sheet = members["xl/worksheets/sheet1.xml"].decode()
    for idx, value in enumerate(shared):
        sheet = sheet.replace('t="str"><v>%s</v>' % value, 't="s"><v>%d</v>' % idx)
    members["xl/worksheets/sheet1.xml"] = sheet.encode()
    members["xl/sharedStrings.xml"] = xml.encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    bundle = load_bundle(path)
    assert bundle.subjects[0].modules["소리그림감상"].n_rows == 1


def test_시트_순서는_워크북이_정한다(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    order = list(reversed(MODULES))
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"),
               [(m, module_sheet([], 0, 0)) for m in order])
    bundle = load_bundle(str(folder))
    assert bundle.module_order == list(order)


def test_빈_행은_데이터로_세지_않는다(tmp_path):
    rows = [["[요약]"], None, list(SPEC_HEADER), None,
            ["2026.03.02", "1", "REGULAR", "2회", "5초"], ["", "", "", "", ""], None]
    folder = tmp_path / "logs"
    folder.mkdir()
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"), [(m, rows) for m in MODULES])
    bundle = load_bundle(str(folder))
    assert bundle.n_data_rows == len(MODULES)


def test_열이_5개보다_적은_데이터행도_읽는다(tmp_path):
    rows = [["[요약]"], None, list(SPEC_HEADER), ["2026.03.02", "1", "REGULAR"]]
    folder = tmp_path / "logs"
    folder.mkdir()
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"), [(m, rows) for m in MODULES])
    bundle = load_bundle(str(folder))
    row = bundle.subjects[0].rows()[0]
    assert row.usable and row.seconds is None and row.reps is None


def test_읽기_전용이다_입력_파일이_바뀌지_않는다(make_workbook, basic_rows, tmp_path):
    path = make_workbook("AB12CD34", {"음소쌍": basic_rows})
    before = (os.path.getmtime(path), os.path.getsize(path), open(path, "rb").read())
    load_bundle(str(tmp_path / "logs"))
    after = (os.path.getmtime(path), os.path.getsize(path), open(path, "rb").read())
    assert before == after


def test_망가진_XML_은_못_읽은_파일이다(make_workbook, basic_rows, tmp_path):
    path = make_workbook("AB12CD34", {"음소쌍": basic_rows})
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members["xl/worksheets/sheet1.xml"] = b"<worksheet><broken>"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    with pytest.raises(ReadError):
        read_workbook(path)
