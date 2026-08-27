"""사전-사후 · 비모수 옵션의 CLI end-to-end 테스트 (오프라인, 번들 예시 사용)."""
import csv
import json
import os

from surveyscan.cli import run

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
PP_CSV = os.path.join(ROOT, "examples", "sleep_prepost.csv")
PP_CFG = os.path.join(ROOT, "examples", "sleep_prepost_config.json")
BASE = [PP_CSV, "--config", PP_CFG, "--id-col", "ID", "--time-col", "시점"]


def test_prepost_text_report(capsys):
    rc = run(BASE)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[ 사전-사후 비교 (시점 컬럼: 시점) ]" in out
    assert "사전 '기저' → 사후 '12주'" in out
    assert "대응표본 t(" in out and "Cohen dz" in out
    assert "ICC(2,1)" in out and "반응자(임계값" in out
    # 반복측정 자료라는 사실과 그 함의를 상단에 알린다.
    assert "모든 시점을 합친" in out


def test_prepost_pairing_diagnostics_reported(capsys):
    """짝을 못 지은 사람·중복 입력을 조용히 버리지 않고 숫자로 남긴다."""
    run(BASE)
    out = capsys.readouterr().out
    assert "짝지은 응답자 28명" in out
    assert "한 시점에만 있어 제외 1명" in out
    assert "같은 (ID,시점)이 두 번이라 제외 1명" in out


def test_duplicate_id_check_is_timepoint_aware(capsys):
    """같은 사람이 시점마다 한 줄씩 있는 것은 중복이 아니다(진짜 이중입력만 잡아야 함)."""
    run(BASE)
    out = capsys.readouterr().out
    assert "P002 (시점: 기저)" in out    # 진짜 이중입력 1건
    assert "P001 (시점" not in out       # 정상적인 반복측정은 경고하지 않음


def test_prepost_json_has_structure(capsys):
    rc = run(BASE + ["--group-col", "군", "--nonparam", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    pp = data["prepost"]
    assert pp["usable"] and pp["pre"] == "기저" and pp["post"] == "12주"
    row = pp["subscales"][0]
    for key in ("n_pairs", "change", "test", "effect", "icc", "responders",
                "wilcoxon", "group_change", "alpha_pre", "alpha_post"):
        assert key in row
    assert row["test"]["p"] > 0.0            # p 가 0.0 으로 뭉개지지 않음
    assert row["group_change"]["test"]["test"] == "welch_t"


def test_prepost_markdown(capsys):
    rc = run(BASE + ["--group-col", "군", "--format", "md"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## 사전-사후 비교" in out
    assert "| 시점 | N | 평균 | SD | 중앙 | α |" in out
    assert "집단별 변화량 비교" in out


def test_nonparam_adds_rank_tests(capsys):
    rc = run(BASE + ["--group-col", "군", "--nonparam"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Wilcoxon 부호순위" in out
    assert "Mann-Whitney U" in out


def test_nonparam_absent_by_default(capsys):
    run(BASE + ["--group-col", "군"])
    out = capsys.readouterr().out
    assert "Wilcoxon" not in out and "Mann-Whitney" not in out


def test_scores_out_includes_timepoint(tmp_path, capsys):
    p = tmp_path / "scores.csv"
    rc = run(BASE + ["--group-col", "군", "--scores-out", str(p)])
    assert rc == 0
    with open(p, encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    # 시점 없이 점수만 내보내면 어느 방문의 값인지 알 수 없어 병합이 불가능하다.
    assert rows[0][:4] == ["원본CSV행", "ID", "군", "시점"]
    assert rows[1][3] in ("기저", "12주")


def test_time_pre_post_override(capsys):
    rc = run(BASE + ["--time-pre", "12주", "--time-post", "기저"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "사전 '12주' → 사후 '기저'" in out
    assert "직접 지정" in out


def test_time_pre_without_post_is_error(capsys):
    rc = run(BASE + ["--time-pre", "기저"])
    assert rc == 2
    assert "함께 지정" in capsys.readouterr().err


def test_time_pre_without_time_col_is_error(capsys):
    rc = run([PP_CSV, "--config", PP_CFG, "--id-col", "ID",
              "--time-pre", "기저", "--time-post", "12주"])
    assert rc == 2
    assert "--time-col" in capsys.readouterr().err


def test_unknown_time_column_is_error(capsys):
    rc = run([PP_CSV, "--config", PP_CFG, "--id-col", "ID", "--time-col", "없는컬럼"])
    assert rc == 2
    assert "헤더에 없습니다" in capsys.readouterr().err


def test_pair_id_must_be_id_col(capsys):
    rc = run(BASE + ["--pair-id", "ISI1"])
    assert rc == 2
    assert "--id-col" in capsys.readouterr().err


def test_pair_id_selects_subset(tmp_path, capsys):
    """방문마다 달라지는 접수번호 대신 환자번호로 짝짓기."""
    csv_path = tmp_path / "long.csv"
    csv_path.write_text(
        "접수번호,환자번호,시점,A,B\n"
        "V1,PT1,기저,3,4\nV2,PT1,후,1,2\n"
        "V3,PT2,기저,4,4\nV4,PT2,후,2,1\n",
        encoding="utf-8",
    )
    rc = run([str(csv_path), "--id-col", "접수번호", "--id-col", "환자번호",
              "--time-col", "시점", "--pair-id", "환자번호"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "짝지은 응답자 2명" in out
    # 접수번호로 짝지으면 한 쌍도 안 나온다 — 기본값(전체 조합)에서는 실패해야 정상.
    rc2 = run([str(csv_path), "--id-col", "접수번호", "--id-col", "환자번호",
               "--time-col", "시점"])
    out2 = capsys.readouterr().out
    assert rc2 == 0 and "모두 나온 ID 가 없습니다" in out2


def test_three_timepoints_requires_explicit_pair(tmp_path, capsys):
    csv_path = tmp_path / "three.csv"
    rows = ["ID,시점,A,B"]
    for pid in ("P1", "P2", "P3"):
        for t in ("기저", "4주", "12주"):
            rows.append(f"{pid},{t},3,4")
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rc = run([str(csv_path), "--id-col", "ID", "--time-col", "시점"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "시점이 3개입니다" in out and "--time-pre" in out
    # 지정하면 정상 동작
    rc2 = run([str(csv_path), "--id-col", "ID", "--time-col", "시점",
               "--time-pre", "기저", "--time-post", "12주"])
    out2 = capsys.readouterr().out
    assert rc2 == 0 and "사전 '기저' → 사후 '12주'" in out2


def test_numeric_timepoints_sorted_numerically(tmp_path, capsys):
    """'0','4','12' 주차를 문자열 정렬하면 0→12 가 아니라 0→4 가 되어 결과가 달라진다."""
    csv_path = tmp_path / "weeks.csv"
    rows = ["ID,주차,A,B"]
    for pid in ("P1", "P2", "P3"):
        rows.append(f"{pid},12,1,1")   # 파일에는 12주가 먼저 나온다
        rows.append(f"{pid},0,4,4")
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rc = run([str(csv_path), "--id-col", "ID", "--time-col", "주차"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "사전 '0' → 사후 '12'" in out
    assert "숫자 순" in out


def test_time_column_excluded_from_items(capsys):
    """시점 컬럼이 문항으로 섞이면 문항 수와 α가 통째로 틀어진다."""
    rc = run([PP_CSV, "--id-col", "ID", "--time-col", "시점", "--group-col", "군"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "문항 수   : 10" in out


def test_missing_timepoint_cell_is_not_a_timepoint(tmp_path, capsys):
    csv_path = tmp_path / "na.csv"
    csv_path.write_text(
        "ID,시점,A,B\nP1,기저,3,4\nP1,후,1,2\nP2,기저,4,4\nP2,후,2,1\nP3,NA,3,3\n",
        encoding="utf-8",
    )
    rc = run([str(csv_path), "--id-col", "ID", "--time-col", "시점"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "사전 '기저' → 사후 '후'" in out   # 'NA' 가 세 번째 시점이 되면 안 된다


def test_same_column_for_group_and_time_warns(tmp_path, capsys):
    """시점 컬럼을 집단 컬럼으로도 주면 같은 사람이 두 집단에 들어간다(독립성 위배)."""
    csv_path = tmp_path / "same.csv"
    csv_path.write_text(
        "ID,시점,A,B\nP1,기저,3,4\nP1,후,1,2\nP2,기저,4,4\nP2,후,2,1\n",
        encoding="utf-8",
    )
    rc = run([str(csv_path), "--id-col", "ID", "--time-col", "시점", "--group-col", "시점"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "집단 컬럼과 시점 컬럼이 같습니다" in out
    rc2 = run([str(csv_path), "--id-col", "ID", "--time-col", "시점",
               "--group-col", "시점", "--format", "md"])
    assert rc2 == 0 and "집단 컬럼과 시점 컬럼이 같습니다" in capsys.readouterr().out


def test_other_timepoint_rows_are_counted(tmp_path, capsys):
    """비교하지 않는 시점(4주)의 행은 짝짓기에서 빠지지만 상단 통계에는 들어간다 — 수를 남긴다."""
    csv_path = tmp_path / "three.csv"
    rows = ["ID,시점,A,B"]
    for pid in ("P1", "P2", "P3"):
        for t in ("기저", "4주", "12주"):
            rows.append(f"{pid},{t},3,4")
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rc = run([str(csv_path), "--id-col", "ID", "--time-col", "시점",
              "--time-pre", "기저", "--time-post", "12주"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "비교 대상이 아닌 시점의 행 3개" in out
    assert "응답자 수 : 9" in out          # 상단 통계에는 9행 모두 들어감


def test_encoding_provenance_distinguishes_forced_from_auto(tmp_path, capsys):
    """--encoding 으로 직접 지정한 것을 '자동 판별'이라고 적으면 안 된다."""
    p = tmp_path / "k.csv"
    p.write_bytes("ID,문항1,문항2\nP1,3,4\nP2,2,1\nP3,4,4\n".encode("cp949"))
    run([str(p), "--id-col", "ID", "--encoding", "cp949"])
    out = capsys.readouterr().out
    assert "파일 인코딩: cp949 (--encoding 으로 지정)" in out
    run([str(p), "--id-col", "ID"])
    out2 = capsys.readouterr().out
    assert "자동 재시도" in out2


def test_config_with_utf8_bom_loads(tmp_path, capsys):
    """윈도우 메모장이 'UTF-8'로 저장한 config(BOM 포함)도 읽혀야 한다."""
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"subscales": {"S": ["A", "B"]}}', encoding="utf-8-sig")
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("ID,A,B\nP1,3,4\nP2,2,1\nP3,4,4\n", encoding="utf-8")
    rc = run([str(csv_path), "--config", str(cfg), "--id-col", "ID"])
    assert rc == 0
    assert "설문 응답 분석 리포트" in capsys.readouterr().out
