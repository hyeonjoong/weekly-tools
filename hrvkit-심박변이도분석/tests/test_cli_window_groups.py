"""--window / --groups CLI 모드의 종단 테스트 (텍스트·JSON·CSV·오류 경로)."""

import csv
import io
import json
import math
import os
import random

import pytest

from hrvkit import cli

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
SESSION = os.path.join(EXAMPLES, "session_20min.csv")
GROUP_MANIFEST = os.path.join(EXAMPLES, "parallel_arm", "manifest.csv")


def write_rr(path, values, header="rr_ms"):
    with open(path, "w", encoding="utf-8") as f:
        if header:
            f.write(header + "\n")
        for v in values:
            f.write(f"{v:.1f}\n")
    return str(path)


def synth(n, mean_rr=820.0, amp=25.0, seed=1):
    rng = random.Random(seed)
    out, t = [], 0.0
    for _ in range(n):
        v = mean_rr + amp * math.sin(2 * math.pi * 0.25 * t) + rng.gauss(0, 5)
        out.append(v)
        t += v / 1000.0
    return out


# --------------------------------------------------------------------------- #
# --window
# --------------------------------------------------------------------------- #
def test_window_text_report(capsys):
    rc = cli.main([SESSION, "--window", "300"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "구간별 추이" in out
    assert "Mann–Kendall" in out
    assert "SDANN" in out
    assert "SDNN index" in out


def test_window_default_is_300_seconds(capsys):
    """--window 를 값 없이 써도 Task Force 표준 300초가 적용된다."""
    rc = cli.main([SESSION, "--window", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["window_sec"] == 300.0
    assert data["step_sec"] == 300.0
    assert data["overlapping"] is False


def test_window_json_has_per_window_metrics_and_trends(capsys):
    rc = cli.main([SESSION, "--window", "200", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "window"
    assert data["n_windows"] == len(data["windows"])
    assert data["n_windows"] >= 5
    w0 = data["windows"][0]
    assert w0["metrics"]["rmssd"] > 0
    assert w0["start_sec"] == 0.0
    # session_20min.csv 는 결정적 픽스처 — 값을 고정합니다(모호한 not-None 금지).
    assert data["trends"]["rmssd"]["tau"] == pytest.approx(1.0)
    assert data["trends"]["rmssd"]["n"] == data["n_windows_ok"]
    # 창이 겹치지 않으므로 SDANN 이 실제 유한 값이어야 한다.
    sdann = data["long_term"]["sdann"]
    assert isinstance(sdann, float) and math.isfinite(sdann)
    assert data["long_term"]["sdnn_index"] == pytest.approx(
        sum(w["metrics"]["sdnn"] for w in data["windows"]) /
        data["n_windows_ok"], rel=1e-6)


def test_window_csv_one_row_per_window(capsys):
    rc = cli.main([SESSION, "--window", "300", "--format", "csv"])
    assert rc == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert len(rows) == 4
    assert rows[0]["window"] == "0"
    assert rows[0]["start_sec"] == "0.0"
    assert float(rows[0]["rmssd"]) > 0
    # 단일 파일 CSV와 같은 지표 스키마를 재사용한다.
    assert "sdnn" in rows[0] and "dfa_alpha1" in rows[0]


def test_window_step_creates_overlapping_windows(capsys):
    rc = cli.main([SESSION, "--window", "300", "--step", "150", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["overlapping"] is True
    assert data["windows"][1]["start_sec"] == 150.0
    # 겹치면 SDANN 은 정의되지 않아 NaN 문자열로 나온다(_json_safe).
    assert data["long_term"]["sdann"] == "NaN"
    assert any("겹칩니다" in n for n in data["notes"])


def test_window_rejects_multiple_files(capsys):
    rc = cli.main([SESSION, SESSION, "--window", "300"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "파일 1개" in err


def test_window_too_long_for_recording_errors(capsys):
    rc = cli.main([os.path.join(EXAMPLES, "resting.csv"), "--window", "3000"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "짧습니다" in err


def test_window_min_beats_option_is_honoured(capsys):
    rc = cli.main([SESSION, "--window", "300", "--min-window-beats", "5000"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "분석 가능한 창이 없습니다" in err


def test_window_conflicting_modes_rejected(capsys):
    rc = cli.main([SESSION, SESSION, "--window", "300", "--compare"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "함께 쓸 수 없습니다" in err


def test_window_json_is_strict_no_nan_token(capsys):
    """NaN/Infinity 토큰이 새면 엄격 파서(jq/serde)가 거부한다."""
    cli.main([SESSION, "--window", "300", "--step", "150", "--json"])
    out = capsys.readouterr().out
    json.loads(out)                            # 표준 파서로 통과
    assert "NaN," not in out.replace('"NaN",', "")
    assert "Infinity" not in out


def test_window_passes_through_input_warnings(tmp_path, capsys):
    """비수치 셀 같은 로딩 경고가 구간 모드에서도 사라지지 않아야 한다."""
    vals = synth(900, seed=5)
    p = tmp_path / "dirty.csv"
    with open(p, "w", encoding="utf-8") as f:
        f.write("rr_ms\n")
        for i, v in enumerate(vals):
            f.write("NA\n" if i % 100 == 0 else f"{v:.1f}\n")
    rc = cli.main([str(p), "--window", "120"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "비수치" in out


# --------------------------------------------------------------------------- #
# --groups
# --------------------------------------------------------------------------- #
def test_groups_text_report(capsys):
    rc = cli.main(["--groups", GROUP_MANIFEST])
    out = capsys.readouterr().out
    assert rc == 0
    assert "독립 2군" in out
    assert "Mann–Whitney" in out
    assert "control" in out and "device" in out
    assert "RMSSD" in out


def test_groups_json_structure(capsys):
    rc = cli.main(["--groups", GROUP_MANIFEST, "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "groups"
    assert data["group_a"] == "control"
    assert data["group_b"] == "device"
    assert data["_meta"]["n_a"] == 5 and data["_meta"]["n_b"] == 5
    rm = data["rmssd"]
    assert rm["hl_shift"] > 0                  # 개입군 RMSSD 가 더 큼
    # 5대5 완전분리 → U=25, 정확 양측 p = 2/C(10,5) = 2/252
    assert rm["u_stat"] == pytest.approx(25.0)
    assert rm["rank_biserial"] == pytest.approx(1.0)
    assert rm["mw_method"] == "exact"
    assert rm["mw_p"] == pytest.approx(2.0 / 252.0)
    assert rm["ci_low"] <= rm["hl_shift"] <= rm["ci_high"]


def test_groups_csv_one_row_per_metric(capsys):
    rc = cli.main(["--groups", GROUP_MANIFEST, "--format", "csv"])
    assert rc == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    keys = [r["metric"] for r in rows]
    assert "rmssd" in keys and "sdnn" in keys and "lf_hf_ratio" in keys
    rm = next(r for r in rows if r["metric"] == "rmssd")
    assert int(rm["n_a"]) == 5 and int(rm["n_b"]) == 5
    assert float(rm["hedges_g"]) > 0


def test_groups_alpha_widens_ci(capsys):
    cli.main(["--groups", GROUP_MANIFEST, "--json", "--alpha", "0.2"])
    wide_a = json.loads(capsys.readouterr().out)["rmssd"]
    cli.main(["--groups", GROUP_MANIFEST, "--json", "--alpha", "0.05"])
    narrow_a = json.loads(capsys.readouterr().out)["rmssd"]
    assert (narrow_a["ci_high"] - narrow_a["ci_low"]) >= \
           (wide_a["ci_high"] - wide_a["ci_low"])


def test_groups_reports_underpowered_sample(capsys):
    """n=5/5 + 11개 지표 보정에서는 어떤 효과도 유의할 수 없음을 밝혀야 한다."""
    cli.main(["--groups", GROUP_MANIFEST])
    out = capsys.readouterr().out
    assert "최소 p" in out


def test_groups_three_arms_rejected(tmp_path, capsys):
    files = []
    for i in range(6):
        p = tmp_path / f"s{i}.csv"
        write_rr(p, synth(120, seed=i))
        files.append(p.name)
    man = tmp_path / "m.csv"
    man.write_text("file,group\n" + "".join(
        f"{f},arm{i % 3}\n" for i, f in enumerate(files)), encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "3개" in err and "Kruskal" in err


def test_groups_duplicate_file_rejected(tmp_path, capsys):
    p1 = tmp_path / "a.csv"
    write_rr(p1, synth(120, seed=1))
    p2 = tmp_path / "b.csv"
    write_rr(p2, synth(120, seed=2))
    man = tmp_path / "m.csv"
    man.write_text("file,group\na.csv,ctl\na.csv,ctl\nb.csv,trt\nb.csv,trt\n",
                   encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "같은 파일이 여러 번" in err


def test_groups_duplicate_subject_label_rejected(tmp_path, capsys):
    for name in ("a", "b", "c", "d"):
        write_rr(tmp_path / f"{name}.csv", synth(120, seed=ord(name)))
    man = tmp_path / "m.csv"
    man.write_text("file,group,subject\na.csv,ctl,S1\nb.csv,ctl,S1\n"
                   "c.csv,trt,S3\nd.csv,trt,S4\n", encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "라벨이 중복" in err


def test_groups_singleton_arm_rejected(tmp_path, capsys):
    for name in ("a", "b", "c"):
        write_rr(tmp_path / f"{name}.csv", synth(120, seed=ord(name)))
    man = tmp_path / "m.csv"
    man.write_text("file,group\na.csv,ctl\nb.csv,ctl\nc.csv,trt\n",
                   encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "최소 2개" in err


def test_groups_headerless_manifest_works(tmp_path, capsys):
    for name in ("a", "b", "c", "d"):
        write_rr(tmp_path / f"{name}.csv", synth(150, seed=ord(name)))
    man = tmp_path / "m.csv"
    man.write_text("a.csv,ctl\nb.csv,ctl\nc.csv,trt\nd.csv,trt\n",
                   encoding="utf-8")
    rc = cli.main(["--groups", str(man), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["_meta"]["n_a"] == 2 and data["_meta"]["n_b"] == 2


def test_groups_missing_member_file_names_the_file(tmp_path, capsys):
    """존재하지 않는 파일은 **분석 전에** 파일명과 함께 거부돼야 한다."""
    write_rr(tmp_path / "a.csv", synth(120, seed=1))
    write_rr(tmp_path / "b.csv", synth(120, seed=2))
    write_rr(tmp_path / "c.csv", synth(120, seed=3))
    man = tmp_path / "m.csv"
    man.write_text("file,group,subject\na.csv,ctl,S1\nb.csv,ctl,S2\n"
                   "c.csv,trt,S3\nmissing.csv,trt,S4\n", encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "missing.csv" in err and "찾을 수 없습니다" in err


def test_groups_headerless_typo_row_is_not_swallowed(tmp_path, capsys):
    """헤더 없는 매니페스트의 **첫 행 경로에 오타**가 있어도 조용히 사라지면 안 된다.

    과거엔 '첫 행에 실제 파일이 하나도 없으면 헤더' 규칙 때문에 그 행이 통째로
    버려져, 사용자가 쓴 3대3 이 2대3 으로 계산되고도 exit 0 이었습니다.
    """
    for name in ("s1", "s2", "s3", "s4", "s5"):
        write_rr(tmp_path / f"{name}.csv", synth(120, seed=hash(name) % 999))
    man = tmp_path / "m.csv"
    man.write_text("s0_TYPO.csv,A\ns1.csv,A\ns2.csv,A\n"
                   "s3.csv,B\ns4.csv,B\ns5.csv,B\n", encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "s0_TYPO.csv" in err


def test_groups_blank_or_ragged_row_rejected_with_line_number(tmp_path, capsys):
    for name in ("s0", "s1", "s2", "s3", "s4"):
        write_rr(tmp_path / f"{name}.csv", synth(120, seed=hash(name) % 999))
    man = tmp_path / "m.csv"
    man.write_text("file,group\ns0.csv,A\ns1.csv\ns2.csv,B\ns3.csv,B\n"
                   "s4.csv,A\n", encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "비어 있거나" in err and "행 3" in err

    man2 = tmp_path / "m2.csv"
    man2.write_text("file,group\ns0.csv,   \ns1.csv,A\ns2.csv,A\ns3.csv,B\n"
                    "s4.csv,B\n", encoding="utf-8")
    rc = cli.main(["--groups", str(man2)])
    assert rc == 2
    assert "행 2" in capsys.readouterr().err


def test_groups_conflicts_with_paired(capsys):
    rc = cli.main(["--groups", GROUP_MANIFEST, "--paired", GROUP_MANIFEST])
    err = capsys.readouterr().err
    assert rc == 2
    assert "함께 쓸 수 없습니다" in err


def test_groups_empty_manifest_rejected(tmp_path, capsys):
    man = tmp_path / "m.csv"
    man.write_text("file,group\n", encoding="utf-8")
    rc = cli.main(["--groups", str(man)])
    assert rc == 2
    assert "오류" in capsys.readouterr().err
