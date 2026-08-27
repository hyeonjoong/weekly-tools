"""감사 파이프라인 — 스캔 범위와 지적 유형."""

from __future__ import annotations

from deidaudit.audit import HIPAA_AGE_LIMIT, run_audit, suggest_quasi
from deidaudit.findings import CRITICAL, WARNING

from .conftest import write_csv_file


def _kinds(result):
    return [f.kind for f in result.findings]


def test_structured_scan_covers_every_column_not_just_free_text(tmp_path):
    """전화번호가 '점수' 열에 들어 있어도 잡아야 합니다."""
    src = write_csv_file(tmp_path / "a.csv", ["subject_id", "점수"], [["S01", "010-1234-5678"]])
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert "휴대전화" in _kinds(result)


def test_name_detection_is_limited_to_name_like_and_free_text_columns(tmp_path):
    """일반 범주 열의 한글 값은 이름으로 보지 않습니다(오탐 억제)."""
    src = write_csv_file(tmp_path / "b.csv", ["subject_id", "기관"], [["S01", "강남"], ["S02", "송파"]])
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert "성명" not in _kinds(result)


def test_age_over_89_is_a_warning(tmp_path):
    src = write_csv_file(tmp_path / "c.csv", ["subject_id", "age"], [["S01", "88"], ["S02", "91"]])
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    hits = [f for f in result.findings if f.kind == "89세 초과 연령"]
    # 물리 행 번호: 1행이 헤더이므로 두 번째 데이터 행은 파일의 3번째 줄입니다.
    assert len(hits) == 1 and hits[0].row == 3 and hits[0].severity == WARNING
    assert HIPAA_AGE_LIMIT == 89


def test_row_numbers_point_at_physical_file_lines(tmp_path):
    """엑셀이 내보낸 CSV 에는 빈 줄이 섞입니다 — 그걸 무시하고 세면 행 번호가 어긋납니다."""
    path = tmp_path / "blank.csv"
    path.write_text("subject_id,phone\n\n\nS02,010-1234-5678\n", encoding="utf-8")
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    hits = [f for f in result.findings if f.kind == "휴대전화"]
    assert len(hits) == 1
    assert hits[0].row == 4  # 실제 파일의 4번째 줄


def test_overflow_cells_are_scanned_not_discarded(tmp_path):
    """자유기술 칸의 이스케이프 안 된 쉼표 하나로 전화번호가 열 밖으로 밀립니다."""
    path = tmp_path / "ragged.csv"
    path.write_text(
        "subject_id,sex,score\nS01,M,10\nS02,F,11,010-1234-5678,김현중\n", encoding="utf-8"
    )
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    kinds = [f.kind for f in result.findings]
    assert "휴대전화" in kinds
    overflow = [f for f in result.findings if f.column == "(열 밖으로 밀린 값)"]
    assert overflow


def test_headerless_file_is_reported_and_column_names_are_masked(tmp_path):
    """헤더 없이 저장된 CSV 는 첫 행이 열 이름이 됩니다 — 리포트에 원문이 남으면 안 됩니다."""
    path = tmp_path / "headerless.csv"
    path.write_text("김현중,010-1234-5678,hong@snu.ac.kr\n이서연,010-2222-3333,a@b.kr\n", encoding="utf-8")
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    assert "헤더 행이 데이터" in {f.kind for f in result.findings}
    for finding in result.findings:
        assert "010-1234-5678" not in finding.column
        assert "hong@snu.ac.kr" not in finding.column
        assert "김현중" not in finding.column


def test_birth_column_is_critical_and_not_double_reported_as_date(tmp_path):
    src = write_csv_file(tmp_path / "d.csv", ["subject_id", "birth"], [["S01", "1988-04-02"]])
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    kinds = _kinds(result)
    assert kinds.count("생년월일") == 1
    assert "정확 날짜 열" not in kinds


def test_exact_date_column_is_a_single_column_level_warning(tmp_path):
    rows = [["S01", f"2026-03-{d:02d}"] for d in range(1, 21)]
    src = write_csv_file(tmp_path / "e.csv", ["subject_id", "visit_date"], rows)
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    hits = [f for f in result.findings if f.kind == "정확 날짜 열"]
    assert len(hits) == 1 and hits[0].row is None


def test_address_header_is_warned_once(tmp_path):
    src = write_csv_file(tmp_path / "f.csv", ["subject_id", "주소"], [["S01", "서울시"], ["S02", "부산시"]])
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    hits = [f for f in result.findings if f.kind.startswith("주소 열")]
    assert len(hits) == 1 and hits[0].severity == WARNING


def test_suggest_quasi_lists_candidates_without_computing(tmp_path):
    src = write_csv_file(
        tmp_path / "g.csv", ["subject_id", "sex", "age", "visit_date", "TST_min"],
        [["S01", "M", "45", "2026-03-14", "410"]],
    )
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert result.k_results == []
    assert set(result.quasi_suggested) >= {"sex", "age", "visit_date"}
    assert "TST_min" not in result.quasi_suggested


def test_hidden_sheet_findings_are_annotated(tmp_path):
    from .xlsx_builder import Sheet, build_xlsx

    path = build_xlsx(
        tmp_path / "h.xlsx",
        [Sheet(name="응답", rows=[["subject_id"], ["S01"]]),
         Sheet(name="명단", rows=[["이름"], ["김현중"]], hidden=True)],
    )
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    name_hits = [f for f in result.findings if f.kind == "성명"]
    assert name_hits and "숨김 시트" in name_hits[0].note


def test_ambiguous_date_format_is_confessed_not_guessed(tmp_path):
    rows = [["S01", "03/14/2026"], ["S02", "04/01/2026"], ["S03", "05/02/2026"]]
    src = write_csv_file(tmp_path / "i.csv", ["subject_id", "visit_date"], rows)
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    skipped = " ".join(f"{a} {b}" for a, b in result.coverage.skipped)
    assert "일/월 순서" in skipped
    assert "정확 날짜 열" not in _kinds(result)


def test_single_row_file_does_not_crash(tmp_path):
    src = write_csv_file(tmp_path / "j.csv", ["subject_id", "name"], [["S01", "김현중"]])
    result = run_audit([src], ["sex"], ["subject_id"], 10**9, 5)
    assert "성명" in _kinds(result)


def test_coverage_counts_cells(tmp_path):
    src = write_csv_file(tmp_path / "k.csv", ["a", "b"], [["1", "2"], ["3", "4"], ["5", "6"]])
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert result.coverage.columns == 2
    assert result.coverage.cells == 6
    assert result.coverage.sheets == 1


def test_common_korean_headers_are_not_mistaken_for_data(tmp_path):
    """`이름`·`연락처`·`주소`·`연령대` 는 전부 첫 음절이 한국 성씨입니다."""
    from deidaudit.audit import header_looks_like_data

    assert not header_looks_like_data(["이름", "주민등록번호", "연락처"])
    assert not header_looks_like_data(["성명", "성별", "연령대", "주소"])
    assert not header_looks_like_data(["subject_id", "담당자", "비고"])
    # 진짜 데이터 행이면 잡아야 합니다.
    assert header_looks_like_data(["김현중", "이서연", "박준호"])
    assert header_looks_like_data(["김현중", "010-1234-5678", "hong@a.kr"])


def test_landline_and_intl_numbers_are_detected(tmp_path):
    src = write_csv_file(
        tmp_path / "tel.csv", ["subject_id", "연락처2"],
        [["S01", "02-3456-7890"], ["S02", "031-123-4567"], ["S03", "070-8888-1234"],
         ["S04", "+82-10-1234-5678"]],
    )
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    kinds = _kinds(result)
    assert kinds.count("유선/인터넷 전화") == 3
    assert "국제표기 전화(+82)" in kinds


def test_measurement_numbers_are_not_mistaken_for_landlines(tmp_path):
    """구분자가 없는 숫자는 전화번호로 보지 않습니다."""
    src = write_csv_file(
        tmp_path / "m.csv", ["subject_id", "RR_ms", "code"],
        [["S01", "0234567890", "031"], ["S02", "412", "02"], ["S03", "1052", "070"]],
    )
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert "유선/인터넷 전화" not in _kinds(result)


def test_role_modifiers_before_a_title_are_not_names(tmp_path):
    """'연구간호사 확인함' 은 임상 메모에 가장 흔한 문장입니다 — 매 행마다 울면 안 됩니다."""
    phrases = [
        "연구간호사 확인함", "담당 연구 간호사에게 전달", "지도 교수 검토 완료",
        "심리 상담사 면담 병행", "방문 담당자 교체됨", "임상 담당자 확인",
        "조사 담당자 서명", "방문 간호사 왕진", "안전성 담당자 보고",
        "주간 담당자 변경", "전화 상담사 연결", "수면 상담사 안내",
        "기기 담당자 방문", "야간 간호사 라운딩",
    ]
    rows = [[f"S{i:02d}", text] for i, text in enumerate(phrases)]
    src = write_csv_file(tmp_path / "notes.csv", ["subject_id", "비고"], rows)
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert [f for f in result.findings if f.kind == "자유텍스트 내 인명"] == []


def test_real_person_mentions_still_caught(tmp_path):
    rows = [["S01", "새벽에 깨서 ○○○ 간호사한테 얘기함"], ["S02", "김철수 씨가 대신 기록해 줌"],
            ["S03", "박서연 간호사가 확인함"]]
    src = write_csv_file(tmp_path / "notes2.csv", ["subject_id", "비고"], rows)
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert len([f for f in result.findings if f.kind == "자유텍스트 내 인명"]) == 3


def test_normal_sheet_names_stay_readable_in_reports(tmp_path):
    """`원본명단` 을 `****` 로 가리면 어느 시트가 문제인지 알 수 없어 리포트가 쓸모없어집니다."""
    from deidaudit.audit import safe_sheet_label, sheet_name_has_person

    for name in ["원본명단", "응답", "대상자", "1차방문", "Sheet1", "명단"]:
        assert safe_sheet_label(name) == name
        assert not sheet_name_has_person(name)


def test_sheet_name_with_a_person_is_warned_and_structured_ids_are_masked():
    from deidaudit.audit import safe_sheet_label, sheet_name_has_person

    assert sheet_name_has_person("숨김_정하늘_명단")
    assert sheet_name_has_person("김철수 자료")
    assert safe_sheet_label("김철수 010-9876-5432") != "김철수 010-9876-5432"
    assert "9876" not in safe_sheet_label("김철수 010-9876-5432")


def test_person_in_sheet_name_produces_a_warning(tmp_path):
    from .xlsx_builder import Sheet, build_xlsx

    path = build_xlsx(
        tmp_path / "b.xlsx", [Sheet(name="김철수 자료", rows=[["subject_id"], ["S01"]])]
    )
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    assert "시트 이름에 인명" in _kinds(result)


# --- 라운드 1 수정이 서로를 깨뜨린 자리들 (전부 "조용한 전원 통과"였습니다) ---


def test_ragged_row_does_not_steal_the_header(tmp_path):
    """자유기술 칸의 쉼표 하나로 진짜 헤더가 '제목 행'으로 분류돼 버려지던 자리."""
    path = tmp_path / "ov2.csv"
    path.write_text(
        "피험자ID,비고\n"
        "S01,어제 밤 잠 못 잠, 보호자 김철수, 연락처 010-1234-5678\n"
        "S02,수면제 복용\nS03,특이사항 없음\n",
        encoding="utf-8",
    )
    from deidaudit.tabular import load_csv

    table = load_csv(path).tables[0]
    assert table.columns == ["피험자ID", "비고"]
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    assert "휴대전화" in _kinds(result)


def test_title_line_does_not_defeat_delimiter_sniffing(tmp_path):
    """제목 줄 하나로 탭 구분 표가 1열로 뭉개져 이름 4건이 미검사이던 자리."""
    path = tmp_path / "t3b.csv"
    path.write_text(
        "2026년 수면연구 참여자 명단\n"
        "피험자ID\t이름\t성별\t나이\t점수\n"
        "S01\t김철수\t남\t34\t12\nS02\t이영희\t여\t41\t15\n"
        "S03\t박민수\t남\t29\t9\nS04\t최지훈\t남\t52\t11\n",
        encoding="utf-8",
    )
    from deidaudit.tabular import load_csv

    table = load_csv(path).tables[0]
    assert table.columns == ["피험자ID", "이름", "성별", "나이", "점수"]
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    assert len([f for f in result.findings if f.kind == "성명"]) == 4


def test_title_row_above_the_header_is_still_scanned(tmp_path):
    """헤더를 찾느라 건너뛴 제목 줄에 담당자 이름·번호가 들어 있는 일이 흔합니다."""
    path = tmp_path / "t2.csv"
    path.write_text(
        "2026년 수면연구 참여자 명단 (담당 김철수 010-1234-5678),,,,\n"
        "피험자ID,성별,나이,점수,비고\nS01,남,34,12,ok\nS02,여,41,15,ok\n",
        encoding="utf-8",
    )
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    assert "휴대전화" in _kinds(result)
    assert any(f.column == "(헤더 위 제목 행)" for f in result.findings)


def test_header_row_email_containing_a_header_word_is_still_caught(tmp_path):
    """`davidkim@hosp.kr` 은 `id`·`mail` 을 포함해 헤더 사전 검사에 먼저 걸리던 자리."""
    path = tmp_path / "hdr3.csv"
    path.write_text("davidkim@hosp.kr,30대,12\n익명,40대,15\n무명,50대,9\n", encoding="utf-8")
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    assert "헤더 행이 데이터" in _kinds(result)


def test_overflow_cells_are_scanned_for_names_too(tmp_path):
    path = tmp_path / "ov3.csv"
    path.write_text("피험자ID,점수\nS01,12,김철수\nS02,15,이영희\nS03,9,박민수\n", encoding="utf-8")
    result = run_audit([path], [], ["subject_id"], 10**9, 5)
    assert len([f for f in result.findings if f.kind == "성명"]) == 3


def test_an_id_column_full_of_names_is_critical(tmp_path):
    """`대상자` 열이 이름으로 차 있다면 그 ID 가 곧 사람이라는 뜻입니다."""
    src = write_csv_file(
        tmp_path / "nm1.csv", ["대상자", "성별", "점수"],
        [["김철수", "남", "12"], ["이영희", "여", "15"], ["박민수", "남", "9"], ["최지훈", "남", "11"]],
    )
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert len([f for f in result.findings if f.kind == "성명"]) == 4


def test_repeating_district_column_is_not_a_name_column(tmp_path):
    """`강남·서초·노원…` 은 전부 첫 음절이 성씨입니다 — 값이 반복되면 이름 열이 아닙니다."""
    src = write_csv_file(
        tmp_path / "fp1.csv", ["피험자ID", "거주지역", "점수"],
        [["S01", "강남", "12"], ["S02", "서초", "15"], ["S03", "노원", "9"],
         ["S04", "마포", "11"], ["S05", "성북", "8"], ["S06", "양천", "10"],
         ["S07", "강남", "12"], ["S08", "서초", "15"]],
    )
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert "성명" not in _kinds(result)


def test_unparseable_sheet_is_not_reported_as_empty(tmp_path):
    """읽지 못한 시트와 비어 있는 시트는 완전히 다릅니다."""
    import zipfile

    from .xlsx_builder import Sheet, build_xlsx

    src = build_xlsx(
        tmp_path / "twosheet.xlsx",
        [Sheet(name="응답", rows=[["subject_id"], ["S01"]]), Sheet(name="명단", rows=[["이름"], ["김철수"]])],
    )
    with zipfile.ZipFile(src) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    parts["xl/worksheets/sheet2.xml"] = b"<worksheet><sheetData><row"
    with zipfile.ZipFile(src, "w") as zf:
        for n, data in parts.items():
            zf.writestr(n, data)
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert result.coverage.unreadable_sheets == 1
    assert result.coverage.undetermined
    assert any("파싱 실패" in reason for _, reason in result.coverage.skipped)


def test_stray_value_far_down_a_wide_sheet_stays_cheap(tmp_path):
    """값 하나로 100만 행 격자를 만들면 6KB 파일이 2GB 를 먹습니다."""
    import zipfile

    from .xlsx_builder import Sheet, build_xlsx

    src = build_xlsx(
        tmp_path / "bloat.xlsx",
        [Sheet(name="S", rows=[[f"c{i}" for i in range(200)]] + [[str(i)] * 200 for i in range(8)])],
    )
    with zipfile.ZipFile(src) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    sheet = parts["xl/worksheets/sheet1.xml"].decode("utf-8")
    parts["xl/worksheets/sheet1.xml"] = sheet.replace(
        "</sheetData>", '<row r="1048000"><c r="A1048000" t="str"><v>x</v></c></row></sheetData>'
    ).encode("utf-8")
    with zipfile.ZipFile(src, "w") as zf:
        for n, data in parts.items():
            zf.writestr(n, data)
    from deidaudit.xlsx import load_xlsx

    table = load_xlsx(src).tables[0]
    assert table.n_rows == 9          # 데이터 8행 + 맨 아래 값 1행 (100만 행이 아님)
    assert table.source_rows[-1] == 1048000
