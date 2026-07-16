"""CLI end-to-end 테스트 (오프라인, 번들 예시 사용)."""
import json
import os

from surveyscan.cli import run

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
CSV = os.path.join(ROOT, "examples", "sleep_survey.csv")
CFG = os.path.join(ROOT, "examples", "sleep_config.json")


def test_cli_text_output(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "설문 응답 분석 리포트" in out
    assert "Cronbach" in out
    assert "불면증상(ISI)" in out


def test_cli_json_output(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["n_respondents"] == 40
    assert len(data["subscales"]) == 2
    # scores 리스트는 JSON에서 제거되어야 함
    assert "scores" not in data["subscales"][0]


def test_cli_missing_file(capsys):
    rc = run(["/no/such/file.csv"])
    assert rc == 2


def test_cli_auto_config(capsys):
    # config 없이 실행: ID 제외하면 숫자 컬럼 전체가 '전체' 척도
    rc = run([CSV, "--id-col", "ID"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "전체" in out


def test_cli_markdown_output(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--format", "md"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("# 설문 응답 분석 리포트")
    assert "| 문항 |" in out
    assert "Cronbach α" in out


def test_cli_score_method_sum(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--score-method", "sum", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["score_method"] == "sum"
    # ISI 7문항, 0~4 척도 -> 가능한 합 0~28
    isi = [s for s in data["subscales"] if s["name"].startswith("불면")][0]
    assert isi["possible_max"] == 28


def test_cli_scores_out(tmp_path, capsys):
    out_csv = str(tmp_path / "scores.csv")
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--scores-out", out_csv])
    assert rc == 0
    text = (tmp_path / "scores.csv").read_text(encoding="utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].startswith("ID,")
    assert "불면증상(ISI)" in lines[0]
    # 응답자 40명 + 헤더
    assert len(lines) == 41
    assert lines[1].startswith("S001,")


def test_cli_ci_level_validation(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--ci-level", "1.5"])
    assert rc == 2


def test_cli_alpha_ci_in_json(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--json"])
    data = json.loads(capsys.readouterr().out)
    isi = [s for s in data["subscales"] if s["name"].startswith("불면")][0]
    assert isi["alpha_ci"] is not None
    assert len(isi["alpha_ci"]) == 2
    assert isi["sem"] is not None
    assert isi["mdc95"] is not None
    assert isi["alpha_std"] is not None


def test_cli_item_freq(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--item-freq"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "문항별 응답 선택지 빈도" in out


def test_cli_item_freq_json_valid(capsys):
    rc = run([CSV, "--config", CFG, "--id-col", "ID", "--item-freq", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)  # 유효 JSON
    fr = data["item_freq"]
    assert fr["levels"] == [0, 1, 2, 3, 4]
    it0 = fr["items"][0]
    assert "counts" in it0 and "other" in it0 and "n" in it0


def test_cli_scores_out_id_name_injection(tmp_path):
    # R2-F2: --id-col 로 지정한 위험한 컬럼 '이름'도 헤더에서 이스케이프
    csv_txt = "=cmd,Q1,Q2\nS1,1,2\nS2,3,4\n"
    p = tmp_path / "d.csv"
    p.write_text(csv_txt, encoding="utf-8")
    out_csv = str(tmp_path / "s.csv")
    rc = run([str(p), "--id-col", "=cmd", "--scores-out", out_csv])
    assert rc == 0
    header = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.startswith("'=cmd")


def test_cli_scores_out_formula_injection(tmp_path):
    # ID 값과 하위척도 이름 모두 수식 인젝션 가드 대상
    csv_txt = "ID,Q1,Q2\n=cmd(),1,2\n+5,3,4\n@x,5,5\n"
    p = tmp_path / "inj.csv"
    p.write_text(csv_txt, encoding="utf-8")
    cfg = tmp_path / "c.json"
    cfg.write_text('{"subscales":{"=EVIL()":["Q1","Q2"]}}', encoding="utf-8")
    out_csv = str(tmp_path / "s.csv")
    rc = run([str(p), "-c", str(cfg), "--id-col", "ID", "--scores-out", out_csv])
    assert rc == 0
    text = (tmp_path / "s.csv").read_text(encoding="utf-8-sig")
    lines = text.strip().splitlines()
    # 헤더의 위험한 하위척도 이름이 이스케이프됨
    assert lines[0] == "ID,'=EVIL()"
    # 위험한 ID 값이 모두 작은따옴표 접두
    assert lines[1].startswith("'=cmd()")
    assert lines[2].startswith("'+5")
    assert lines[3].startswith("'@x")


def test_cli_markdown_pipe_escaping(tmp_path, capsys):
    # 문항명에 파이프가 있어도 마크다운 표가 깨지지 않게 이스케이프
    csv_txt = "A|B,C\n1,2\n3,4\n5,5\n"
    p = tmp_path / "pipe.csv"
    p.write_text(csv_txt, encoding="utf-8")
    rc = run([str(p), "--format", "md"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "A\\|B" in out


def test_cli_config_encoding_error(tmp_path):
    # cp949로 저장된 config는 친절한 오류 + 종료코드 2
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("Q1,Q2\n1,2\n3,4\n", encoding="utf-8")
    cfg = tmp_path / "c.json"
    cfg.write_bytes('{"subscales":{"가나다":["Q1","Q2"]}}'.encode("cp949"))
    rc = run([str(csv_path), "-c", str(cfg)])
    assert rc == 2
