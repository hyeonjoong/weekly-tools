"""적대적 리뷰에서 발견된 결함에 대한 회귀 테스트."""
import json

import pytest

from surveyscan.analyze import analyze
from surveyscan.config import SurveyConfig
from surveyscan.dataio import SurveyData, load_csv


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _raise_on_nonfinite(x):
    raise ValueError(f"non-finite in JSON: {x}")


def test_all_zero_column_not_dropped(tmp_path):
    # P1: 전부 0인 문항(예: ISI 전원 '0=문제없음')이 제외되면 안 됨
    path = write(tmp_path, "d.csv", "Q1,Q2\n0,1\n0,2\n0,3\n")
    data = load_csv(path)
    assert data.numeric_columns() == ["Q1", "Q2"]
    assert data.nonnumeric_columns() == []


def test_all_missing_column_is_nonnumeric(tmp_path):
    # P3: 전부 결측인 컬럼은 nonnumeric으로 분류되어 자동설정에서 경고 대상
    path = write(tmp_path, "d.csv", "Q1,Q2\nNA,1\n,2\nNA,3\n")
    data = load_csv(path)
    assert data.numeric_columns() == ["Q2"]
    assert data.nonnumeric_columns() == ["Q1"]


def test_unknown_id_column_tracked(tmp_path):
    # P4: 헤더에 없는 --id-col 은 unknown_id_columns 로 기록
    path = write(tmp_path, "d.csv", "Q1,Q2\n1,2\n3,4\n")
    data = load_csv(path, id_columns=["NOPE"])
    assert data.unknown_id_columns == ["NOPE"]
    data2 = load_csv(path, id_columns=["Q1"])
    assert data2.unknown_id_columns == []


def test_out_of_range_detected():
    # P2: 선언된 척도 범위를 벗어난 값 탐지
    cols = ["Q1", "Q2"]
    rows = [
        {"Q1": 9, "Q2": 3},  # Q1=9 는 1~5 범위 밖
        {"Q1": 2, "Q2": 3},
        {"Q1": -1, "Q2": 4},  # Q1=-1 도 밖
    ]
    data = SurveyData(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    oor = {o["item"]: o for o in res["out_of_range"]}
    assert "Q1" in oor
    assert oor["Q1"]["count"] == 2
    assert "Q2" not in oor


def test_no_out_of_range_when_in_bounds():
    cols = ["Q1"]
    data = SurveyData(cols, [{"Q1": 1}, {"Q1": 5}, {"Q1": 3}])
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    assert res["out_of_range"] == []


def test_out_of_range_examples_are_int_when_integer():
    # P3(round2): 텍스트/JSON 일관성 — 정수값은 int로 저장
    cols = ["Q1"]
    data = SurveyData(cols, [{"Q1": 9}, {"Q1": 2}])
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    assert res["out_of_range"][0]["examples"] == [9]
    assert all(isinstance(v, int) for v in res["out_of_range"][0]["examples"])


def test_fully_missing_item_flagged(tmp_path):
    # P2(round2): 한 문항이 전부 결측이면 items_no_data 로 표시되고 리포트에 경고
    from surveyscan.report import render
    cols = ["A", "B"]
    rows = [{"A": 1, "B": None}, {"A": 5, "B": None}, {"A": 3, "B": None}]
    data = SurveyData(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["items_no_data"] == ["B"]
    assert "전부 결측인 문항" in render(res)


def test_nonfinite_cells_treated_as_missing(tmp_path):
    # R1(round?): 'inf'/'1e400'/'+nan' 은 통계를 오염시키므로 결측 처리
    path = write(tmp_path, "d.csv", "Q1,Q2\ninf,2\n1e400,3\n1,4\n2,5\n")
    data = load_csv(path)
    # inf, 1e400 -> 결측, 실제 값은 1,2 만 남음
    assert data.present_values("Q1") == [1.0, 2.0]


def test_json_output_is_strict_valid(tmp_path):
    # R1: 비유한값이 있어도 JSON은 항상 엄격 유효(NaN/Infinity 금지)
    from surveyscan.cli import run
    path = write(tmp_path, "d.csv", "Q1,Q2\ninf,2\n1,3\n5,4\n")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run([path, "--json"])
    assert rc == 0
    out = buf.getvalue()
    assert "Infinity" not in out and "NaN" not in out
    # parse_constant 로 비유한 토큰이 있으면 예외
    json.loads(out, parse_constant=_raise_on_nonfinite)


def test_config_directory_friendly_error(tmp_path, capsys):
    from surveyscan.cli import run
    csv_path = write(tmp_path, "d.csv", "Q1,Q2\n1,2\n3,4\n")
    d = tmp_path / "cfgdir"
    d.mkdir()
    rc = run([csv_path, "--config", str(d)])
    assert rc == 2  # raw traceback 대신 친절한 종료코드


def test_output_to_directory_friendly_error(tmp_path):
    from surveyscan.cli import run
    csv_path = write(tmp_path, "d.csv", "Q1,Q2\n1,2\n3,4\n")
    rc = run([csv_path, "-o", str(tmp_path)])  # 폴더로 저장 시도
    assert rc == 2


def test_unknown_config_key_rejected():
    # config 키 오타('reverse_item')는 역문항 재코딩을 통째로 건너뛰어 α를 틀리게
    # 만들지만 사용자는 알 방법이 없었다 -> 조용히 무시하지 말고 오류.
    from surveyscan.config import ConfigError, _from_dict
    with pytest.raises(ConfigError) as e:
        _from_dict({
            "subscales": {"S": ["A", "B"]},
            "reverse_item": ["B"],
            "scale_min": 0,
            "scale_max": 4,
        })
    msg = str(e.value)
    assert "reverse_item" in msg
    assert "reverse_items" in msg  # 근접 키 힌트


def test_underscore_config_keys_allowed_as_comments():
    from surveyscan.config import _from_dict
    cfg = _from_dict({
        "_메모": "2026-07 ISI 설정",
        "_출처": "Bastien 2001",
        "subscales": {"S": ["A", "B"]},
    })
    assert cfg.subscales == {"S": ["A", "B"]}


def test_all_known_config_keys_accepted():
    # KNOWN_KEYS 에 있는 키는 전부 실제로 파싱되어야 한다(목록과 파서가 어긋나면 실패).
    from surveyscan.config import KNOWN_KEYS, _from_dict
    cfg = _from_dict({
        "subscales": {"S": ["A", "B"]},
        "reverse_items": ["B"],
        "scale_min": 0,
        "scale_max": 4,
        "min_valid_ratio": 0.75,
        "score_method": "sum",
        "severity_bands": {"S": [[0, 3, "낮음"], [4, 8, "높음"]]},
    })
    assert set(KNOWN_KEYS) == {
        "subscales", "reverse_items", "scale_min", "scale_max",
        "min_valid_ratio", "score_method", "severity_bands",
    }
    assert cfg.reverse_items == ["B"]
    assert cfg.scale_min == 0.0 and cfg.scale_max == 4.0
    assert cfg.min_valid_ratio == 0.75
    assert cfg.score_method == "sum"
    assert cfg.severity_bands == {"S": [(0.0, 3.0, "낮음"), (4.0, 8.0, "높음")]}


def test_blank_row_does_not_shift_scores_out_rows(tmp_path):
    """엑셀이 남긴 중간 빈 줄 때문에 점수가 다른 응답자에게 밀려 붙는 사고 방지.

    빈 줄을 건너뛰면 '몇 번째 응답자'와 '파일의 몇 번째 줄'이 어긋난다. 점수
    CSV의 첫 열은 원본 CSV 줄 번호여야 원자료에 정확히 병합된다.
    """
    from surveyscan.cli import run
    # 3행이 빈 줄 -> 응답자는 2,4,5,6행
    path = write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2,3\n,,\n4,5,4\n3,3,3\n2,2,2\n")
    data = load_csv(path)
    assert data.n_respondents == 4
    assert data.source_lines == [2, 4, 5, 6]
    assert data.skipped_blank_lines == [3]

    out_csv = str(tmp_path / "s.csv")
    assert run([path, "--scores-out", out_csv, "-o", str(tmp_path / "r.txt")]) == 0
    lines = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    assert lines[0].startswith("원본CSV행,")
    assert [ln.split(",")[0] for ln in lines[1:]] == ["2", "4", "5", "6"]


def test_ragged_row_error_points_at_real_line(tmp_path):
    """셀 안에 개행이 있으면 줄 번호가 밀렸다 — 실제 파일 줄을 가리켜야 한다."""
    from surveyscan.dataio import DataError
    # 2행의 Q2 값에 개행이 들어 있어 실제로는 파일 3번째 줄까지 차지한다.
    # 그 다음(파일 4번째 줄)이 열 개수가 모자란 행.
    text = 'Q1,Q2,Q3\n1,"여러 줄\n메모",3\n9,9\n'
    path = write(tmp_path, "d.csv", text)
    with pytest.raises(DataError) as e:
        load_csv(path)
    assert "4행" in str(e.value)


def test_unreadable_cells_reported_separately_from_blanks(tmp_path):
    """값이 있는데 못 읽은 셀은 '빈칸(무응답)'과 구분해서 알려야 한다.

    조용히 결측으로 묻으면 N·결측률·α가 전부 틀어지고 사용자는 알 방법이 없다.
    """
    from surveyscan.report import render, render_markdown
    from surveyscan.config import SurveyConfig
    path = write(
        tmp_path, "d.csv",
        "Q1,Q2,Q3\n3,4,2\n2,3,매우그렇다\n1,\"3,5\",2\n4,4,3\n0,,1\n",
    )
    data = load_csv(path)
    assert data.unreadable["Q3"]["count"] == 1
    assert data.unreadable["Q2"]["examples"] == ["3,5"]
    assert "Q1" not in data.unreadable  # Q1은 전부 정상
    # Q2의 빈칸은 unreadable 이 아니라 blank
    assert data.unreadable["Q2"]["count"] == 1

    res = analyze(data, SurveyConfig(subscales={"S": ["Q1", "Q2", "Q3"]}))
    items = {u["item"]: u for u in res["unreadable"]}
    assert set(items) == {"Q2", "Q3"}
    for out in (render(res), render_markdown(res)):
        assert "읽지 못해" in out
        assert "3,5" in out and "매우그렇다" in out


def test_na_text_and_blank_are_not_unreadable(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\nNA,1\n,2\nN/A,3\n.,4\n")
    data = load_csv(path)
    assert data.unreadable == {}


def test_na_number_is_not_unreadable(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\n999,1\n2,3\n")
    data = load_csv(path, na_numbers=[999])
    assert data.unreadable == {}
    assert data.present_values("Q1") == [2.0]


def test_metadata_header_row_flagged_as_empty_row(tmp_path):
    """Qualtrics/구글폼이 남기는 문항문구 행이 '응답자'로 잡히면 N이 부풀려진다."""
    from surveyscan.report import render, render_markdown
    from surveyscan.config import SurveyConfig
    path = write(
        tmp_path, "d.csv",
        "Q1,Q2\n잠들기 어려움,자다가 깸\n3,4\n2,3\n1,2\n",
    )
    data = load_csv(path)
    res = analyze(data, SurveyConfig(subscales={"S": ["Q1", "Q2"]}))
    # 메타데이터 행은 원본 CSV 2번째 줄
    assert res["empty_rows"] == [2]
    for out in (render(res), render_markdown(res)):
        assert "모든 문항이 무응답인 행" in out


def test_no_empty_row_warning_for_clean_file(tmp_path):
    from surveyscan.report import render
    from surveyscan.config import SurveyConfig
    path = write(tmp_path, "d.csv", "Q1,Q2\n3,4\n2,3\n1,2\n")
    res = analyze(load_csv(path), SurveyConfig(subscales={"S": ["Q1", "Q2"]}))
    assert res["empty_rows"] == []
    assert res["unreadable"] == []
    assert "모든 문항이 무응답인 행" not in render(res)
    assert "읽지 못해" not in render(res)


def test_sem_none_for_negative_alpha():
    """α<0 이면 SEM=SD·√(1-α) 가 SD를 넘는 불가능한 값이 된다 -> 산출불가."""
    from surveyscan.stats import sem_from_alpha
    assert sem_from_alpha(0.4071, -2.263) is None
    assert sem_from_alpha(1.0, -0.001) is None
    assert sem_from_alpha(1.0, 1.5) is None
    # 정상 범위는 그대로, 그리고 항상 SEM <= SD
    assert sem_from_alpha(2.0, 0.75) == pytest.approx(1.0)
    assert sem_from_alpha(2.0, 0.0) == pytest.approx(2.0)
    assert sem_from_alpha(2.0, 1.0) == pytest.approx(0.0)


def test_negative_alpha_reported_with_warning_and_no_sem():
    """역코딩 누락으로 α가 음수면 경고를 띄우고 SEM·MDC를 내지 않는다."""
    from surveyscan.report import render
    from surveyscan.config import SurveyConfig
    cols = ["A", "B", "C"]
    # C 를 A·B 와 반대로 움직이게 만들어 α를 음수로
    rows = [
        {"A": 1.0, "B": 1.0, "C": 5.0},
        {"A": 2.0, "B": 2.0, "C": 4.0},
        {"A": 4.0, "B": 4.0, "C": 2.0},
        {"A": 5.0, "B": 5.0, "C": 1.0},
    ]
    res = analyze(SurveyData(cols, rows), SurveyConfig(subscales={"S": cols}))
    sub = res["subscales"][0]
    assert sub["alpha"] < 0
    assert sub["sem"] is None and sub["mdc95"] is None
    assert "α가 음수" in render(res)
