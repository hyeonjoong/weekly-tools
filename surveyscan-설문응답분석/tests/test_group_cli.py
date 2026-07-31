"""--group-col (집단 비교) CLI end-to-end 테스트."""
import json
import os

from surveyscan.cli import run

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
CSV = os.path.join(ROOT, "examples", "sleep_survey.csv")
CFG = os.path.join(ROOT, "examples", "sleep_config.json")


def test_group_compare_text(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--group-col", "군"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[ 집단 비교 (기준 컬럼: 군) ]" in out
    assert "Welch t(" in out
    assert "Hedges g" in out
    assert "탐색적" in out  # 과잉해석 방지 문구
    # 평균차·효과크기의 부호 기준을 리포트에 명시(반대로 읽는 것을 막는다)
    assert "평균차(대조군 − 치료군)" in out
    assert "Hedges g(대조군 − 치료군)" in out


def test_group_compare_json(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--group-col", "군", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)  # allow_nan=False 통과(유한값만)
    gc = data["group_compare"]
    assert data["group_column"] == "군"
    assert gc["usable"] is True
    assert sorted(gc["labels"]) == ["대조군", "치료군"]
    row = gc["subscales"][0]
    assert row["test"]["test"] == "welch_t"
    assert 0.0 <= row["p"] <= 1.0
    assert row["p_holm"] >= row["p"] - 1e-12
    # 집단별 α 도 함께 나온다
    assert all("alpha" in g for g in row["groups"])


def test_group_column_is_not_analyzed_as_item(capsys):
    # 집단 컬럼은 문항에서 빠져야 한다(텍스트 컬럼이 '전부 결측 문항'으로 잡히면 안 됨).
    rc = run([CSV, "--id-col", "ID", "--group-col", "군", "--json"])
    cap = capsys.readouterr()
    assert rc == 0
    data = json.loads(cap.out)
    assert data["n_items"] == 10
    assert "군" not in [d["item"] for d in data["descriptives"]]
    assert "군" not in cap.err  # 숫자 아님 경고 대상도 아님


def test_group_col_missing_column_is_error(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--group-col", "없는컬럼"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "없는컬럼" in err


def test_group_col_single_group_reports_reason(tmp_path, capsys):
    p = tmp_path / "one.csv"
    p.write_text("ARM,Q1,Q2\nA,1,2\nA,3,4\nA,2,2\n", encoding="utf-8")
    rc = run([str(p), "--group-col", "ARM"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2개 이상" in out


def test_group_col_three_groups_uses_anova(tmp_path, capsys):
    rows = ["ARM,Q1,Q2"]
    for i in range(9):
        arm = "ABC"[i % 3]
        rows.append(f"{arm},{i % 4},{(i + 1) % 4}")
    p = tmp_path / "three.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rc = run([str(p), "--group-col", "ARM"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Welch ANOVA" in out or "분산이 0" in out


def test_scores_out_includes_group_and_band_columns(tmp_path):
    out_csv = str(tmp_path / "s.csv")
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--group-col", "군",
              "--scores-out", out_csv])
    assert rc == 0
    lines = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    header = lines[0].split(",")
    assert header[:3] == ["원본CSV행", "ID", "군"]
    assert "불면증상(ISI)_심각도" in header
    # 첫 응답자: 2행, S001, 치료군
    first = lines[1].split(",")
    assert first[:3] == ["2", "S001", "치료군"]
    # 심각도 라벨이 실제로 채워져 있다
    band_idx = header.index("불면증상(ISI)_심각도")
    assert any(l.split(",")[band_idx] for l in lines[1:])


def test_group_col_also_id_col_not_duplicated(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("ARM,Q1,Q2\nA,1,2\nA,3,4\nB,2,2\nB,4,4\n", encoding="utf-8")
    out_csv = str(tmp_path / "s.csv")
    rc = run([str(p), "--id-col", "ARM", "--group-col", "ARM", "--scores-out", out_csv])
    assert rc == 0
    header = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(",").count("ARM") == 1


def test_group_values_are_escaped_in_scores_csv(tmp_path):
    p = tmp_path / "inj.csv"
    p.write_text("ARM,Q1,Q2\n=cmd(),1,2\n=cmd(),3,4\n@x,2,2\n@x,4,4\n", encoding="utf-8")
    out_csv = str(tmp_path / "s.csv")
    rc = run([str(p), "--group-col", "ARM", "--scores-out", out_csv])
    assert rc == 0
    lines = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    assert lines[1].split(",")[1] == "'=cmd()"


def test_group_compare_markdown(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--group-col", "군",
              "--format", "md"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## 집단 비교 (기준 컬럼: 군)" in out
    assert "| 집단 | N | 평균 | SD | 중앙 | α |" in out
    assert "## 임상 심각도 구간 분포" in out


def test_blank_group_values_excluded(tmp_path, capsys):
    p = tmp_path / "blank.csv"
    p.write_text(
        "ARM,Q1,Q2\nA,1,2\nA,3,4\n,2,2\nB,4,4\nB,1,1\n", encoding="utf-8"
    )
    rc = run([str(p), "--group-col", "ARM", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    gc = data["group_compare"]
    assert gc["labels"] == ["A", "B"]
    assert gc["n_no_label"] == 1
    total = sum(g["n"] for g in gc["subscales"][0]["groups"])
    assert total == 4  # 라벨 없는 1명 제외
