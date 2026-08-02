"""CSV 읽기 · 열 이름 표준화 · 지표 자동 판별의 견고성."""

import pytest

from metapool.io_csv import TableError, detect_measure, read_table, validate_measure


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


def test_reads_basic_csv(tmp_path):
    p = write(tmp_path, "a.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\n")
    records, header, warns = read_table(p)
    assert header == ["study", "effect", "se"]
    assert len(records) == 2
    assert records[0]["study"] == "A" and records[0]["__row__"] == "2"
    assert warns == []


def test_strips_utf8_bom(tmp_path):
    p = write(tmp_path, "bom.csv", "study,effect,se\nA,0.5,0.1\n", encoding="utf-8-sig")
    records, header, _ = read_table(p)
    assert header[0] == "study"
    assert records[0]["study"] == "A"


def test_reads_cp949_korean_headers(tmp_path):
    p = write(tmp_path, "kr.csv", "연구,효과크기,표준오차\n김2021,0.5,0.1\n", encoding="cp949")
    records, _, _ = read_table(p)
    assert records[0]["study"] == "김2021"
    assert records[0]["effect"] == "0.5"
    assert records[0]["se"] == "0.1"


def test_detects_semicolon_and_tab_delimiters(tmp_path):
    p1 = write(tmp_path, "semi.csv", "study;effect;se\nA;0.5;0.1\n")
    assert read_table(p1)[0][0]["effect"] == "0.5"
    p2 = write(tmp_path, "tab.tsv", "study\teffect\tse\nA\t0.5\t0.1\n")
    assert read_table(p2)[0][0]["effect"] == "0.5"


def test_column_aliases_are_recognised(tmp_path):
    p = write(tmp_path, "alias.csv", "Author,Ne,Mean.e,SD.e,Nc,Mean.c,SD.c\nKim,20,10,2,20,8,2\n")
    records, header, _ = read_table(p)
    assert detect_measure(records, header) == "smd"
    assert records[0]["n1"] == "20" and records[0]["mean2"] == "8"


def test_explicit_map_overrides_detection(tmp_path):
    p = write(tmp_path, "map.csv", "이름,실험군수,실험군평균,실험군편차,대조군수,대조군평균,대조군편차\n"
                                    "A,20,10,2,20,8,2\n")
    records, header, _ = read_table(
        p,
        mapping={
            "이름": "study",
            "실험군수": "n1",
            "실험군평균": "mean1",
            "실험군편차": "sd1",
            "대조군수": "n2",
            "대조군평균": "mean2",
            "대조군편차": "sd2",
        },
    )
    assert detect_measure(records, header) == "smd"
    assert records[0]["study"] == "A"


def test_map_rejects_unknown_canonical_name(tmp_path):
    p = write(tmp_path, "m.csv", "x,effect,se\nA,0.5,0.1\n")
    with pytest.raises(TableError):
        read_table(p, mapping={"x": "무슨열"})


def test_abcd_two_by_two_form(tmp_path):
    p = write(tmp_path, "abcd.csv", "study,a,b,c,d\nT1,10,90,20,80\n")
    records, header, _ = read_table(p)
    assert detect_measure(records, header) == "or"
    assert float(records[0]["events1"]) == 10
    assert float(records[0]["n1"]) == 100
    assert float(records[0]["events2"]) == 20
    assert float(records[0]["n2"]) == 100


def test_binary_form_beats_continuous_in_detection(tmp_path):
    p = write(tmp_path, "b.csv", "study,events1,n1,events2,n2\nA,10,100,20,100\n")
    records, header, _ = read_table(p)
    assert detect_measure(records, header) == "or"


def test_generic_detection_from_ci(tmp_path):
    p = write(tmp_path, "g.csv", "study,effect,ci_low,ci_high\nA,0.5,0.3,0.7\n")
    records, header, _ = read_table(p)
    assert detect_measure(records, header) == "generic"


def test_undetectable_file_gives_actionable_error(tmp_path):
    p = write(tmp_path, "x.csv", "제목,비고\nA,메모\n")
    records, header, _ = read_table(p)
    with pytest.raises(TableError) as exc:
        detect_measure(records, header)
    assert "--map" in str(exc.value)


def test_duplicate_canonical_columns_warn_and_use_first(tmp_path):
    p = write(tmp_path, "dup.csv", "study,effect,es,se\nA,0.5,0.9,0.1\n")
    records, _, warns = read_table(p)
    assert records[0]["effect"] == "0.5"
    assert any("effect" in w for w in warns)


def test_ragged_rows_are_padded_and_trimmed(tmp_path):
    p = write(tmp_path, "r.csv", "study,effect,se\nA,0.5\nB,0.3,0.2,extra\n")
    records, _, warns = read_table(p)
    assert records[0]["se"] == ""          # 짧은 행 → 빈 값
    assert records[1]["se"] == "0.2"       # 긴 행 → 잘라냄
    assert any("잘라냈습니다" in w for w in warns)


def test_blank_lines_are_ignored(tmp_path):
    p = write(tmp_path, "blank.csv", "study,effect,se\n\nA,0.5,0.1\n\n\nB,0.3,0.2\n")
    records, _, _ = read_table(p)
    assert len(records) == 2


def test_missing_file_raises(tmp_path):
    with pytest.raises(TableError):
        read_table(str(tmp_path / "nope.csv"))


def test_directory_path_raises(tmp_path):
    with pytest.raises(TableError):
        read_table(str(tmp_path))


def test_empty_file_raises(tmp_path):
    p = write(tmp_path, "e.csv", "   \n")
    with pytest.raises(TableError):
        read_table(p)


def test_header_only_file_raises(tmp_path):
    p = write(tmp_path, "h.csv", "study,effect,se\n")
    with pytest.raises(TableError):
        read_table(p)


def test_non_utf8_bytes_do_not_crash(tmp_path):
    path = tmp_path / "bin.csv"
    path.write_bytes(b"study,effect,se\n\xff\xfe\x00A,0.5,0.1\n")
    records, _, _ = read_table(str(path))
    assert records[0]["effect"] == "0.5"


def test_label_and_subgroup_column_selection(tmp_path):
    p = write(tmp_path, "s.csv", "code,title,effect,se,site\n1,A연구,0.5,0.1,서울\n")
    records, _, _ = read_table(p, label_column="title", subgroup_column="site")
    assert records[0]["study"] == "A연구"
    assert records[0]["subgroup"] == "서울"


def test_unknown_label_column_raises(tmp_path):
    p = write(tmp_path, "s.csv", "study,effect,se\nA,0.5,0.1\n")
    with pytest.raises(TableError):
        read_table(p, label_column="없는열")


def test_validate_measure_reports_missing_columns(tmp_path):
    p = write(tmp_path, "v.csv", "study,n1,mean1,sd1\nA,20,10,2\n")
    records, _, _ = read_table(p)
    with pytest.raises(TableError) as exc:
        validate_measure(records, "smd")
    assert "n2" in str(exc.value)


def test_validate_measure_generic_needs_se_or_ci(tmp_path):
    p = write(tmp_path, "v2.csv", "study,effect\nA,0.5\n")
    records, _, _ = read_table(p)
    with pytest.raises(TableError):
        validate_measure(records, "generic")


def test_large_file_is_handled(tmp_path):
    rows = "\n".join("S%d,%f,0.1" % (i, 0.5 + (i % 7) * 0.01) for i in range(5000))
    p = write(tmp_path, "big.csv", "study,effect,se\n" + rows + "\n")
    records, header, _ = read_table(p)
    assert len(records) == 5000
    assert detect_measure(records, header) == "generic"
