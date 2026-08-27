"""리뷰 라운드(2026-08-27)에서 지적된 취약점의 회귀 테스트.

- 시점별 α가 '짝지어진 행'으로 계산되는지, pre/post 에 올바로 붙는지
- 자료에서 온 시점 라벨의 렌더링 안전성(ANSI·마크다운/HTML 주입)과 라벨 정규화
- 점수 CSV의 시점 열 수식 인젝션 방지
- 시점 라벨(=방문일자일 수 있음)이 사유 문구에 대량으로 실리지 않는지
- 실패한 실행이 응답자 단위 점수 CSV만 남기지 않는지
"""
import csv
import json
import os

import pytest

from surveyscan import nonparam, paired
from surveyscan.cli import run
from surveyscan.dataio import load_csv, normalize_label


def _long_csv(tmp_path, rows, header="ID,시점,A,B,C,D"):
    p = tmp_path / "long.csv"
    p.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(p)


# ── 시점별 α ────────────────────────────────────────────────────────────────
def test_alpha_pre_post_attached_to_correct_timepoint(tmp_path, capsys):
    """기저는 문항이 잘 맞물리고(α 높음), 12주는 뒤죽박죽(α 낮음)인 자료."""
    rows = []
    consistent = [[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3], [4, 4, 4, 4],
                  [0, 0, 0, 0], [2, 2, 2, 2], [3, 3, 3, 3], [1, 1, 1, 1]]
    noisy = [[1, 4, 0, 3], [4, 0, 3, 1], [0, 3, 4, 0], [3, 1, 1, 4],
             [2, 0, 4, 1], [4, 2, 0, 3], [0, 4, 2, 0], [3, 0, 3, 2]]
    for i, (c, nz) in enumerate(zip(consistent, noisy), start=1):
        rows.append(f"P{i},기저," + ",".join(str(v) for v in c))
        rows.append(f"P{i},12주," + ",".join(str(v) for v in nz))
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점", "--format", "json"])
    assert rc == 0
    row = json.loads(capsys.readouterr().out)["prepost"]["subscales"][0]
    assert row["alpha_pre"] > 0.9          # 기저: 문항이 완전히 일치
    assert row["alpha_post"] < 0.5         # 12주: 뒤죽박죽
    assert row["alpha_pre"] != row["alpha_post"]


def test_alpha_uses_only_paired_rows(tmp_path, capsys):
    """짝짓기에서 뺀 행(중복 입력·한 시점만 있는 ID)이 α에 섞이면 안 된다.

    표에 적힌 N 과 다른 표본의 α가 나란히 찍히기 때문이다.
    """
    rows = []
    for i in range(1, 7):
        rows.append(f"P{i},기저,{i % 5},{i % 5},{i % 5},{i % 5}")
        rows.append(f"P{i},12주,{(i + 1) % 5},{(i + 1) % 5},{(i + 1) % 5},{(i + 1) % 5}")
    path_clean = _long_csv(tmp_path, rows)
    rc = run([path_clean, "--id-col", "ID", "--time-col", "시점", "--format", "json"])
    assert rc == 0
    clean = json.loads(capsys.readouterr().out)["prepost"]["subscales"][0]

    # 짝을 못 짓는 행(기저만 있는 ID)을, 그것도 α를 흔들 값으로 추가한다.
    rows_noise = rows + ["P99,기저,0,4,0,4"]
    path_noise = _long_csv(tmp_path / "x" if False else tmp_path, rows_noise)
    rc2 = run([path_noise, "--id-col", "ID", "--time-col", "시점", "--format", "json"])
    assert rc2 == 0
    noisy = json.loads(capsys.readouterr().out)["prepost"]["subscales"][0]
    assert noisy["alpha_pre"] == pytest.approx(clean["alpha_pre"])
    assert noisy["n_pairs"] == clean["n_pairs"] == 6


# ── 라벨 렌더링 안전성 ──────────────────────────────────────────────────────
ANSI = "\x1b[2K\x1b[1;31m붉은글씨"
MD_INJECT = "<img src=x onerror=alert(1)>|파이프"


def _two_timepoint_rows(t1, t2):
    return [
        f'P1,"{t1}",3,4,3,4', f'P1,"{t2}",1,2,1,2',
        f'P2,"{t1}",4,4,4,4', f'P2,"{t2}",2,1,2,1',
        f'P3,"{t1}",3,3,3,3', f'P3,"{t2}",1,1,1,1',
    ]


def test_timepoint_label_ansi_stripped_from_text_report(tmp_path, capsys):
    path = _long_csv(tmp_path, _two_timepoint_rows(ANSI, "12주"))
    rc = run([path, "--id-col", "ID", "--time-col", "시점"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "\x1b" not in out          # 터미널 리포트를 덮어쓰는 이스케이프 제거
    assert "붉은글씨" in out


def test_timepoint_label_escaped_in_markdown(tmp_path, capsys):
    path = _long_csv(tmp_path, _two_timepoint_rows(MD_INJECT, "12주"))
    rc = run([path, "--id-col", "ID", "--time-col", "시점", "--format", "md"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "<img" not in out          # 원시 HTML 로 렌더링되면 안 된다
    assert "\\|파이프" in out          # 표 칸을 쪼개는 파이프는 이스케이프되어야 한다


def test_unusable_reason_is_sanitized(tmp_path, capsys):
    """짝이 없어 실패하는 경로의 '사유' 문구에도 자료에서 온 라벨이 들어간다."""
    rows = [f'P1,"{MD_INJECT}",3,4,3,4', 'P2,"정상",1,2,1,2']
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점", "--format", "md"])
    out = capsys.readouterr().out
    assert rc == 0 and "<img" not in out
    rc2 = run([path, "--id-col", "ID", "--time-col", "시점"])
    assert rc2 == 0 and "\x1b" not in capsys.readouterr().out


def test_control_characters_stripped_before_json_and_csv(tmp_path, capsys):
    path = _long_csv(tmp_path, _two_timepoint_rows(ANSI, "12주"))
    scores = tmp_path / "s.csv"
    rc = run([path, "--id-col", "ID", "--time-col", "시점",
              "--format", "json", "--scores-out", str(scores)])
    out = capsys.readouterr().out
    assert rc == 0
    # `--format json | jq` 나 `cat 점수.csv` 는 렌더러를 거치지 않는다.
    assert "\x1b" not in out
    assert "\x1b" not in scores.read_text(encoding="utf-8-sig")


def test_timepoint_column_is_formula_escaped_in_scores_csv(tmp_path, capsys):
    rows = _two_timepoint_rows("=cmd|'/c calc'!A1", "12주")
    path = _long_csv(tmp_path, rows)
    scores = tmp_path / "s.csv"
    rc = run([path, "--id-col", "ID", "--time-col", "시점", "--scores-out", str(scores)])
    assert rc == 0
    with open(scores, encoding="utf-8-sig") as fh:
        body = list(csv.reader(fh))
    cells = [c for row in body for c in row if "cmd" in c]
    assert cells and all(c.startswith("'") for c in cells)


def test_invisible_characters_do_not_split_a_timepoint(tmp_path, capsys):
    """제로폭 공백 하나로 '기저'가 두 시점이 되면 짝짓기가 통째로 무너진다."""
    rows = [
        "P1,기저,3,4,3,4", "P1,12주,1,2,1,2",
        "P2,기저​,4,4,4,4", "P2,12주,2,1,2,1",
        "P3, 기저 ,3,3,3,3", "P3,12주,1,1,1,1",
    ]
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "짝지은 응답자 3명" in out
    assert "시점이 3개" not in out


def test_time_pre_label_is_normalized_like_the_data(tmp_path, capsys):
    """엑셀에서 복사한 --time-pre 값에 NBSP 가 섞여도 매칭되어야 한다."""
    rows = ["P1,기저,3,4,3,4", "P1,4주,2,3,2,3", "P1,12주,1,2,1,2",
            "P2,기저,4,4,4,4", "P2,4주,3,3,3,3", "P2,12주,2,1,2,1",
            "P3,기저,3,3,3,3", "P3,4주,2,2,2,2", "P3,12주,1,1,1,1"]
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점",
              "--time-pre", " 기저", "--time-post", "12주​"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "사전 '기저' → 사후 '12주'" in out


def test_normalize_label_strips_controls():
    assert normalize_label("\x1b[31m기저\x07") == "[31m기저"
    assert normalize_label(" 기저​ ") == "기저"


# ── 시점 컬럼이 숫자여도 문항에서 제외되는지 ───────────────────────────────
def test_numeric_time_column_not_treated_as_item(tmp_path, capsys):
    rows = ["P1,0,3,4,3,4", "P1,12,1,2,1,2", "P2,0,4,4,4,4", "P2,12,2,1,2,1"]
    path = _long_csv(tmp_path, rows, header="ID,주차,A,B,C,D")
    rc = run([path, "--id-col", "ID", "--time-col", "주차"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "문항 수   : 4" in out      # 주차가 문항으로 섞이면 5가 된다


# ── 기타 리뷰 지적 ─────────────────────────────────────────────────────────
def test_wilcoxon_z_sign_matches_effect_direction():
    """JSON 의 z 부호를 방향 판단에 쓰는 소비자를 위해 부호 일관성을 고정한다."""
    up = nonparam.wilcoxon_signed_rank([1.0, 2.0, 2.0, 3.0, 4.0, 5.0, 6.0] * 5)
    down = nonparam.wilcoxon_signed_rank([-1.0, -2.0, -2.0, -3.0, -4.0, -5.0, -6.0] * 5)
    assert up["z"] > 0 and up["rank_biserial"] > 0
    assert down["z"] < 0 and down["rank_biserial"] < 0


def test_rank_effect_label_boundaries():
    assert nonparam.rank_effect_label(0.5) == "큼"
    assert nonparam.rank_effect_label(0.49999) == "중간"
    assert nonparam.rank_effect_label(0.3) == "중간"
    assert nonparam.rank_effect_label(0.29999) == "작음"
    assert nonparam.rank_effect_label(0.1) == "작음"
    assert nonparam.rank_effect_label(0.09999) == "매우 작음"


def test_scores_csv_not_written_when_report_output_fails(tmp_path, capsys):
    """실패한 실행이 응답자 단위 임상 점수 파일만 남기면 안 된다."""
    rows = ["P1,기저,3,4,3,4", "P1,12주,1,2,1,2", "P2,기저,4,4,4,4", "P2,12주,2,1,2,1"]
    path = _long_csv(tmp_path, rows)
    scores = tmp_path / "s.csv"
    bad_out = tmp_path / "없는폴더" / "r.txt"
    rc = run([path, "--id-col", "ID", "--time-col", "시점",
              "--scores-out", str(scores), "-o", str(bad_out)])
    capsys.readouterr()
    assert rc == 2
    assert not os.path.exists(scores)


def test_json_output_has_no_control_characters_in_labels(tmp_path, capsys):
    path = _long_csv(tmp_path, _two_timepoint_rows(ANSI, "12주"))
    rc = run([path, "--id-col", "ID", "--time-col", "시점", "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    labels = data["prepost"]["labels"] + [data["prepost"]["pre"], data["prepost"]["post"]]
    assert all("\x1b" not in str(x) for x in labels)


# ── 엣지케이스 리뷰(2026-08-27) 회귀 ────────────────────────────────────────
def test_duplicate_pair_key_reports_the_real_cause(tmp_path, capsys):
    """다기관 자료에서 환자번호가 겹치면 전원이 빠진다 — 사유가 원인을 짚어야 한다."""
    rows = []
    for site in ("H1", "H2"):
        for pid in ("001", "002"):
            rows.append(f"V{site}{pid}a,{site},{pid},기저,1,2,3,4")
            rows.append(f"V{site}{pid}b,{site},{pid},12주,2,3,4,4")
    path = _long_csv(tmp_path, rows, header="방문ID,기관,환자번호,시점,A,B,C,D")
    rc = run([path, "--id-col", "방문ID", "--id-col", "기관", "--id-col", "환자번호",
              "--time-col", "시점", "--pair-id", "환자번호"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--pair-id" in out                      # 원인(짝짓기 키 중복)을 짚는다
    # 환자번호 2개가 각각 두 기관에서 중복 → 두 키 모두 제외됨을 숫자로 남긴다.
    assert "짝짓기 제외" in out and "제외 2명" in out
    assert "ID 표기가 시점마다" not in out          # 엉뚱한 안내를 하지 않는다


def test_unusable_prepost_still_shows_exclusions_in_markdown(tmp_path, capsys):
    rows = ["P1,기저,3,4,3,4", "P2,12주,1,2,1,2"]
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점", "--format", "md"])
    out = capsys.readouterr().out
    assert rc == 0 and "짝짓기 제외" in out and "한 시점에만 있어 제외 2명" in out


def test_single_pair_gets_no_wilcoxon_effect_size(tmp_path, capsys):
    """쌍 1개에서 r=±1.00 '큼' 이 찍히면 '검정 불가' 경고와 정면으로 어긋난다."""
    rows = ["P1,기저,1,2,3,4", "P1,12주,2,3,4,4"]
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점", "--nonparam"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Wilcoxon" not in out
    assert "1명뿐이라 검정할 수 없습니다" in out


def test_appearance_order_is_flagged_as_a_guess(tmp_path, capsys):
    rows = ["P1,기저,3,4,3,4", "P1,12주,1,2,1,2", "P2,기저,4,4,4,4", "P2,12주,2,1,2,1"]
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점"])
    out = capsys.readouterr().out
    assert rc == 0 and "추정" in out and "--time-pre" in out
    # 명시하면 경고가 사라진다.
    rc2 = run([path, "--id-col", "ID", "--time-col", "시점",
               "--time-pre", "기저", "--time-post", "12주"])
    out2 = capsys.readouterr().out
    assert rc2 == 0 and "추정한 것입니다" not in out2


def test_row_order_change_does_not_silently_flip_direction(tmp_path, capsys):
    """시점 기준으로 정렬된 같은 자료 — 사전/사후가 뒤집히면 경고가 반드시 있어야 한다."""
    a = ["P1,기저,3,4,3,4", "P1,12주,1,2,1,2", "P2,기저,4,4,4,4", "P2,12주,2,1,2,1"]
    b = ["P1,12주,1,2,1,2", "P2,12주,2,1,2,1", "P1,기저,3,4,3,4", "P2,기저,4,4,4,4"]
    out_a = _long_csv(tmp_path, a)
    run([out_a, "--id-col", "ID", "--time-col", "시점"])
    text_a = capsys.readouterr().out
    path_b = tmp_path / "b.csv"
    path_b.write_text("ID,시점,A,B,C,D\n" + "\n".join(b) + "\n", encoding="utf-8")
    run([str(path_b), "--id-col", "ID", "--time-col", "시점"])
    text_b = capsys.readouterr().out
    assert "사전 '기저' → 사후 '12주'" in text_a
    assert "사전 '12주' → 사후 '기저'" in text_b
    assert "추정" in text_a and "추정" in text_b


def test_huge_values_do_not_break_table_layout(tmp_path, capsys):
    # 1e308 은 합계가 overflow 해 별도 오류 경로로 잡힌다 — 여기서는 '크지만 유한한'
    # 오입력(1e30)이 표를 깨뜨리지 않는지 본다.
    rows = ["P1,기저,1e30,4,3,4", "P1,12주,1,2,1,2",
            "P2,기저,3,4,3,4", "P2,12주,2,1,2,1"]
    path = _long_csv(tmp_path, rows)
    rc = run([path, "--id-col", "ID", "--time-col", "시점"])
    out = capsys.readouterr().out
    assert rc == 0
    # 309자리 숫자가 그대로 찍히면 표가 통째로 어긋난다 → 지수표기로 줄인다.
    assert "e+" in out                       # 지수표기로 줄여 찍는다
    assert max(len(line) for line in out.splitlines()) < 200


def test_bom_header_matches_id_col_even_with_explicit_encoding(tmp_path, capsys):
    p = tmp_path / "bom.csv"
    p.write_text("ID,시점,A,B\nP1,기저,3,4\nP1,12주,1,2\nP2,기저,4,4\nP2,12주,2,1\n",
                 encoding="utf-8-sig")
    rc = run([str(p), "--encoding", "utf-8", "--id-col", "ID", "--time-col", "시점"])
    cap = capsys.readouterr()
    assert rc == 0
    assert "헤더에 없습니다" not in cap.err     # BOM 때문에 ID 컬럼을 놓치면 안 된다
    assert "짝지은 응답자 2명" in cap.out
