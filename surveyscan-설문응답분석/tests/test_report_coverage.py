"""리포트가 '계산은 했지만 보여주지 않는' 지표를 남기지 않는지 검증.

analyze() 가 결과 dict에 넣는 지표는 JSON 뿐 아니라 사람이 읽는 text/markdown
리포트에도 나타나야 한다. 과거에 McDonald ω·중복 ID·하위척도 간 상관이
계산만 되고 기본 출력에서 누락되어 사용자가 볼 수 없었다.
"""
from surveyscan.analyze import analyze
from surveyscan.config import SurveyConfig
from surveyscan.dataio import SurveyData
from surveyscan.report import render, render_markdown


def _corr_data(n=30, k=5):
    """단일요인 구조를 가진 합성 응답(문항 간 상관이 뚜렷하도록 결정론적 생성)."""
    cols = [f"Q{i+1}" for i in range(k)]
    rows = []
    for r in range(n):
        latent = r % 5  # 0~4 잠재점수
        row = {}
        for i, c in enumerate(cols):
            # 잠재점수 + 문항별 결정론적 잔차(0/1) -> 0~4 범위로 클립
            noise = (r + i * 7) % 3 - 1
            row[c] = float(min(4, max(0, latent + noise)))
        rows.append(row)
    return SurveyData(cols, rows)


def test_omega_shown_in_text_and_markdown():
    data = _corr_data()
    cfg = SurveyConfig(subscales={"S": data.columns}, scale_min=0, scale_max=4)
    res = analyze(data, cfg)
    sub = res["subscales"][0]
    assert sub["omega"] is not None, "합성 단일요인 데이터에서 ω가 산출되어야 함"
    omega_txt = f"{sub['omega']:.3f}"
    text = render(res)
    md = render_markdown(res)
    assert "ω" in text and omega_txt in text
    assert "ω" in md and omega_txt in md


def test_omega_absent_renders_without_error():
    """문항 2개(ω 식별 불가)여도 리포트가 깨지지 않아야 한다."""
    cols = ["A", "B"]
    rows = [{"A": 1.0, "B": 2.0}, {"A": 3.0, "B": 3.0}, {"A": 5.0, "B": 4.0}]
    data = SurveyData(cols, rows)
    cfg = SurveyConfig(subscales={"S": cols}, scale_min=1, scale_max=5)
    res = analyze(data, cfg)
    assert res["subscales"][0]["omega"] is None
    render(res)
    render_markdown(res)


def test_duplicate_ids_shown_in_text_and_markdown():
    """중복 ID는 이중입력·병합오류 신호 — JSON 뿐 아니라 기본 리포트에도 떠야 한다."""
    cols = ["Q1", "Q2"]
    rows = [{"Q1": 1.0, "Q2": 2.0} for _ in range(4)]
    data = SurveyData(
        cols,
        rows,
        id_columns=["ID"],
        id_values=[{"ID": "P01"}, {"ID": "P02"}, {"ID": "P01"}, {"ID": "P03"}],
    )
    cfg = SurveyConfig(subscales={"S": cols})
    res = analyze(data, cfg)
    assert res["duplicate_ids"] and res["duplicate_ids"][0]["id"] == "P01"
    for out in (render(res), render_markdown(res)):
        assert "중복" in out
        assert "P01" in out
        assert "1" in out and "3" in out  # 중복된 데이터 행 번호


def test_no_duplicate_section_when_ids_unique():
    cols = ["Q1", "Q2"]
    rows = [{"Q1": 1.0, "Q2": 2.0} for _ in range(3)]
    data = SurveyData(
        cols, rows, id_columns=["ID"],
        id_values=[{"ID": "A"}, {"ID": "B"}, {"ID": "C"}],
    )
    res = analyze(data, SurveyConfig(subscales={"S": cols}))
    assert res["duplicate_ids"] == []
    assert "중복된 ID" not in render(res)
    assert "중복된 ID" not in render_markdown(res)


def test_subscale_correlation_shown_in_text_and_markdown():
    """하위척도 간 상관(변별타당도)이 기본 리포트에 표시되어야 한다."""
    cols = ["A1", "A2", "B1", "B2"]
    rows = []
    for r in range(20):
        rows.append({"A1": float(r % 5), "A2": float((r + 1) % 5),
                     "B1": float((r + 2) % 5), "B2": float((r + 3) % 5)})
    data = SurveyData(cols, rows)
    cfg = SurveyConfig(subscales={"A": ["A1", "A2"], "B": ["B1", "B2"]})
    res = analyze(data, cfg)
    sc = res["subscale_corr"]
    assert sc is not None and len(sc["pairs"]) == 1
    for out in (render(res), render_markdown(res)):
        assert "하위척도 간 상관" in out
        assert f"{sc['pairs'][0]['r']:.3f}" in out


def test_markdown_table_column_counts_match_header():
    """마크다운 표의 헤더·구분줄·데이터행 셀 수가 일치해야 한다(열 추가 시 흔한 실수)."""
    data = _corr_data()
    cfg = SurveyConfig(subscales={"S": data.columns}, scale_min=0, scale_max=4)
    md = render_markdown(analyze(data, cfg))
    lines = md.splitlines()
    checked = 0
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        if set(line.replace("|", "").replace(" ", "")) <= set("-:"):
            # 구분줄 — 바로 앞 헤더행과 셀 수가 같아야 함
            assert line.count("|") == lines[i - 1].count("|"), (
                f"{i+1}행: 구분줄 셀 수가 헤더와 다름\n{lines[i-1]}\n{line}"
            )
            checked += 1
    assert checked >= 3, f"검사한 표가 너무 적음({checked}) — 테스트가 공회전하는지 확인"
