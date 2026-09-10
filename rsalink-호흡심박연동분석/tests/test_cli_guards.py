"""상습 결함 5종 + exit 매트릭스 + 금지어 + basename 테스트."""
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

from rsalink.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "paced_6bpm")
RESP = os.path.join(EX, "호흡.csv")
RR = os.path.join(EX, "RR.csv")


@pytest.fixture(scope="module", autouse=True)
def _examples_exist():
    if not (os.path.exists(RESP) and os.path.exists(RR)):
        subprocess.run([sys.executable, os.path.join(ROOT, "examples", "_generate.py")], check=True)


def _run(args, capsys=None):
    return main(args)


# ---------------------------------------------------------------- exit 0


def test_paced_example_exit0_and_artifacts(tmp_path, capsys):
    out = tmp_path / "결과"
    rc = main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--pace", "6",
               "--out-dir", str(out), "--quiet"])
    assert rc == 0
    for name in ("연동리포트.md", "호흡별.csv", "조건비교.csv"):
        assert (out / name).exists()
    md = (out / "연동리포트.md").read_text(encoding="utf-8")
    assert re.search(r"^- HF 변화가 호흡수 변화와 \*\*동반됨\*\*", md, re.M)
    assert re.search(r"^- 호흡중심 대역 기준 결론 \*\*상이\*\*", md, re.M)
    assert md.index("커버리지 자백") < md.index("구간별 표")
    # D4 호흡당 박동 열, D6 null 95%, D1 구간별 Δf 각주
    assert "| 호흡당 박동 |" in md and "null 95%" in md
    assert re.search(r"기준선: 세그먼트 \d+ 샘플 = \d+ s, Δf [\d.]+ Hz, L = \d+; 자극: 세그먼트", md)
    # 조건비교.csv 에 구간별 nperseg·Δf·null95·CV 플래그 열
    with open(out / "조건비교.csv", encoding="utf-8-sig", newline="") as fh:
        import csv
        rows = list(csv.DictReader(fh))
    assert {"welch_nperseg", "welch_df_hz", "coherence_null95", "flag_rate_cv_gt15"} <= set(rows[0])
    assert all(r["welch_nperseg"] == "256" for r in rows)
    with open(out / "호흡별.csv", encoding="utf-8-sig", newline="") as fh:
        hdr = fh.readline().strip().split(",")
    assert hdr[-2:] == ["RR최소_위상", "RR최대_위상"]


def test_sham_example_cli_not_accompanied(tmp_path):
    out = tmp_path / "sham"
    rc = main([os.path.join(ROOT, "examples", "sham", "호흡.csv"), os.path.join(ROOT, "examples", "sham", "RR.csv"),
               "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--out-dir", str(out), "--quiet"])
    assert rc == 0
    md = (out / "연동리포트.md").read_text(encoding="utf-8")
    assert re.search(r"^- HF 변화가 호흡수 변화와 \*\*비동반\*\*", md, re.M)
    assert re.search(r"^- 호흡중심 대역 기준 결론 \*\*동일\*\*", md, re.M)


def test_short_segment_flag_and_suggest_offset_display_only(tmp_path, capsys):
    # 구간은 붙어 있을 필요가 없다. (2:00–15:00 처럼 프로토콜 전환을 걸치면 구간 호흡수 이봉 → 안정성 게이트 exit 3)
    rc = main([RESP, RR, "--baseline", "0:00-2:00", "--stimulus", "5:00-15:00"])
    assert rc == 0
    out = capsys.readouterr().out
    assert re.search(r"^\| 기준선 \| .*짧음\(<5분\)", out, re.M)
    assert re.search(r"5분 미만 구간\(스펙트럼 '짧음'\) \| 기준선 \|", out)
    # 제안 오프셋은 표시만: 적용값 행은 0.00 그대로
    assert re.search(r"교차상관 제안 오프셋\(표시만, 미적용\) \| [+-]\d+\.\d\d s \(r = [+-]\d\.\d\d\)", out)
    assert "| --resp-offset (적용값) | 0.00 s |" in out
    assert "탐색 범위 ±5 s" in out


def test_option_validation_exit2(capsys):
    for args in (["--smooth-s", "nan"], ["--smooth-s", "-1"], ["--max-breath-s", "inf"], ["--min-breath-s", "0"],
                 ["--resp-band-halfwidth", "0"], ["--resp-band-halfwidth", "nan"], ["--pace", "inf"], ["--pace", "-3"],
                 ["--k-mad", "0"], ["--k-mad", "nan"], ["--resp-offset", "nan"], ["--rsa-lag", "-1"],
                 ["--rsa-lag", "inf"], ["--baseline", "x:yy-5:00"], ["--stimulus", "5:00"]):
        assert main([RESP, RR] + args) == 2, args
        err = capsys.readouterr().err
        assert err.startswith("오류:") and "Traceback" not in err, args


def test_identical_timestamps_exit2(tmp_path, capsys):
    p = tmp_path / "same.csv"
    p.write_text("timestamp,value\n" + "".join(f"1.0,{i % 3}\n" for i in range(50)), encoding="utf-8")
    assert main([str(p), RR]) == 2
    err = capsys.readouterr().err
    assert "타임스탬프" in err and "Traceback" not in err
    ev = tmp_path / "same_ev.csv"
    ev.write_text("timestamp\n" + "5.0\n" * 20, encoding="utf-8")
    assert main([str(ev), RR]) == 2


def test_low_sampling_rate_unit_suspect_exit2(tmp_path, capsys):
    # 10 Hz 파형의 타임스탬프를 ms 로 잘못 적은 것처럼 100 배 → fs 0.1 Hz → 단위 의심
    p = tmp_path / "slow.csv"
    p.write_text("timestamp,value\n" + "".join(f"{i * 10.0:.1f},{i % 7}\n" for i in range(200)), encoding="utf-8")
    assert main([str(p), RR]) == 2
    assert "타임스탬프 단위 의심" in capsys.readouterr().err


def test_waveform_nan_cells_excluded_and_confessed(tmp_path, capsys):
    lines = open(RESP, encoding="utf-8").read().splitlines()
    for i in range(1, len(lines), 50):
        lines[i] = lines[i].split(",")[0] + ",nan"
    p = tmp_path / "nan.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main([str(p), RR]) == 0
    out = capsys.readouterr().out
    assert re.search(r"호흡 값 셀 제외\(빈칸·NA·nan/inf\) \| 180건", out)


def test_spike_clipped_and_confessed(tmp_path, capsys):
    lines = open(RESP, encoding="utf-8").read().splitlines()
    lines[3000] = lines[3000].split(",")[0] + ",500"
    p = tmp_path / "spike.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main([str(p), RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--quiet", "--out-dir", str(tmp_path / "o")]) == 0
    md = (tmp_path / "o" / "연동리포트.md").read_text(encoding="utf-8")
    assert re.search(r"스파이크 클리핑.* \| 1 샘플", md)
    assert re.search(r"^- HF 변화가 호흡수 변화와 \*\*동반됨\*\*", md, re.M)


def test_events_input_ignores_waveform_options_with_warning(tmp_path, capsys):
    ev = _events_csv(tmp_path)
    rc = main([ev, RR, "--invert", "--k-mad", "2", "--smooth-s", "1"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "무시됨" in cap.err and "--invert" in cap.err and "--k-mad" in cap.err and "--smooth-s" in cap.err
    assert "| 무시된 옵션 | --invert, --smooth-s, --k-mad" in cap.out
    assert "흡기 시작은 제공된 이벤트 목록" in cap.out and "결맞음은 계산하지 않았다" in cap.out


def test_tz_one_sided_confessed(tmp_path, capsys):
    # 호흡 ISO(+09:00) vs RR ISO 타임존 없음 → 절대 시각 비교 생략을 자백
    resp_lines = open(RESP, encoding="utf-8").read().splitlines()[1:601]
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    p = tmp_path / "resp_tz.csv"
    p.write_text("timestamp,value\n" + "".join(
        f"{(base + timedelta(seconds=float(l.split(',')[0]))).isoformat()},{l.split(',')[1]}\n" for l in resp_lines),
        encoding="utf-8")
    rr_vals = open(RR, encoding="utf-8").read().split()[1:80]
    q = tmp_path / "rr_notz.csv"
    t = datetime(2026, 1, 1, 9, 0, 0)
    body = []
    for v in rr_vals:
        t += timedelta(milliseconds=float(v))
        body.append(f"{t.isoformat()},{v}")
    q.write_text("timestamp,rr_ms\n" + "\n".join(body) + "\n", encoding="utf-8")
    assert main([str(p), str(q), "--inspect"]) == 0
    assert "한쪽만 타임존" in capsys.readouterr().out
    main([str(p), str(q)])
    out = capsys.readouterr().out
    assert "한쪽만 타임존 있음" in out and "절대 시각 차" not in out


def test_abs_time_diff_row_both_conventions(tmp_path, capsys):
    resp_lines = open(RESP, encoding="utf-8").read().splitlines()[1:]
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1, 9, 0, 2)          # 호흡이 RR 보다 2 s 늦게 시작
    p = tmp_path / "resp_iso.csv"
    p.write_text("timestamp,value\n" + "".join(
        f"{(base + timedelta(seconds=float(l.split(',')[0]))).isoformat()},{l.split(',')[1]}\n" for l in resp_lines),
        encoding="utf-8")
    rr_vals = open(RR, encoding="utf-8").read().split()[1:]
    t = datetime(2026, 1, 1, 9, 0, 0)
    body = []
    for v in rr_vals:
        t += timedelta(milliseconds=float(v))
        body.append(f"{t.isoformat()},{v}")
    q = tmp_path / "rr_iso.csv"
    q.write_text("timestamp,rr_ms\n" + "\n".join(body) + "\n", encoding="utf-8")
    assert main([str(p), str(q), "--inspect"]) == 0
    ins = capsys.readouterr().out
    m = re.search(r"간격의 시작이면 ([+-]\d+\.\d\d) s / 첫 박동 시각\(간격 끝\)이면 ([+-]\d+\.\d\d) s \(첫 RR (\d\.\d+) s\)", ins)
    assert m
    rr1 = float(rr_vals[0]) / 1000.0
    # RR 첫 타임스탬프 = 첫 박동 시각(09:00:00 + rr1) 이므로 d = 2 − rr1, 박동 시각 규약 후보 = d + rr1 = 2.00
    assert float(m.group(1)) == pytest.approx(2.0 - rr1, abs=0.011)
    assert float(m.group(2)) == pytest.approx(2.0, abs=0.011) and float(m.group(3)) == pytest.approx(rr1, abs=1e-3)
    assert main([str(p), str(q), "--quiet", "--out-dir", str(tmp_path / "o")]) == 0
    md = (tmp_path / "o" / "연동리포트.md").read_text(encoding="utf-8")
    assert re.search(r"절대 시각 차\(호흡 첫 샘플 − RR 첫 타임스탬프, 미적용\) \| [+-]\d+\.\d\d s — --resp-offset 후보", md)


def test_breath_csv_segment_label_respects_resp_offset(tmp_path):
    import csv
    out = tmp_path / "o"
    assert main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--resp-offset", "60",
                 "--out-dir", str(out), "--quiet"]) in (0, 3)
    with open(out / "호흡별.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # 구간은 RR 시계 기준 [0,300) → 호흡 시계로는 [−60, 240): onset 250 s 인 호흡은 "자극"
    for r in rows:
        t = float(r["t_onset_s"])
        if 0 <= t < 240:
            assert r["segment"] == "기준선", r
        elif 240 <= t < 840:
            assert r["segment"] == "자극", r


def test_hrvkit_example_rr_parses_readonly():
    cands = [os.path.join(os.path.dirname(ROOT), d, "examples", "session_20min.csv")
             for d in os.listdir(os.path.dirname(ROOT)) if "hrvkit" in d.lower()]
    cands = [c for c in cands if os.path.exists(c)]
    if not cands:
        pytest.skip("hrvkit examples/session_20min.csv 없음")
    from rsalink.parse import parse_rr
    path = cands[0]
    before = os.stat(path).st_mtime_ns
    r = parse_rr(path)
    assert r.n_total > 500 and r.unit_detected in ("ms", "s", "bpm")
    assert r.excluded_frac < 0.2 and 15 * 60 < r.duration_s < 25 * 60
    assert os.stat(path).st_mtime_ns == before


def test_inspect_exit0(capsys):
    assert main([RESP, RR, "--inspect"]) == 0
    out = capsys.readouterr().out
    assert "샘플링레이트" in out and "단위 자동 인식: ms" in out


# ---------------------------------------------------------------- 결함 1: 심볼릭/하드링크


def test_symlink_target_refused(tmp_path, capsys):
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"ORIGINAL BYTES")
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(str(victim), str(out / "연동리포트.md"))
    rc = main([RESP, RR, "--out-dir", str(out), "--quiet"])
    assert rc == 2
    assert victim.read_bytes() == b"ORIGINAL BYTES"
    assert "심볼릭링크" in capsys.readouterr().err


def test_hardlink_target_refused(tmp_path, capsys):
    victim = tmp_path / "victim.csv"
    victim.write_bytes(b"ORIGINAL BYTES")
    out = tmp_path / "out"
    out.mkdir()
    os.link(str(victim), str(out / "호흡별.csv"))
    assert os.stat(str(victim)).st_nlink == 2
    rc = main([RESP, RR, "--out-dir", str(out), "--quiet"])
    assert rc == 2
    assert victim.read_bytes() == b"ORIGINAL BYTES"
    assert "하드링크" in capsys.readouterr().err


# ---------------------------------------------------------------- 결함 2: .gitignore


def test_gitignore_blocks_data_files(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(os.path.join(ROOT, ".gitignore"), repo / ".gitignore")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    def ignored(rel):
        r = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", rel])
        return r.returncode == 0

    for rel in ("a.csv", "data/b.csv", "결과/c.csv", "x.edf", "sub/y.zip", "rsalink/z.csv",
                "결과/연동리포트.md", "rsalink.egg-info/PKG-INFO", "tests/__pycache__/x.pyc",
                "examples/실제.csv", "examples/real_subject/RR.csv", "RR.tsv", "examples/sham/x.tsv"):
        assert ignored(rel), rel
    for rel in ("examples/paced_6bpm/호흡.csv", "examples/sham/RR.csv", "README.md", "rsalink/cli.py"):
        assert not ignored(rel), rel


# ---------------------------------------------------------------- 라운드 1 B1: 쓰기 프로브·가드 순서·경로


def test_probe_name_symlink_and_hardlink_untouched(tmp_path):
    # 라운드 0 프로브 이름(.rsalink_write_probe)이 심볼릭/하드링크여도 희생 파일 바이트 불변(프로브는 mkstemp 난수명)
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"KEEP ME")
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(str(victim), str(out / ".rsalink_write_probe"))
    hard = tmp_path / "victim2.bin"
    hard.write_bytes(b"KEEP ME TOO")
    os.link(str(hard), str(out / ".rsalink_probe_"))
    assert main([RESP, RR, "--out-dir", str(out), "--quiet"]) == 0
    assert victim.read_bytes() == b"KEEP ME" and hard.read_bytes() == b"KEEP ME TOO"
    assert os.path.islink(str(out / ".rsalink_write_probe"))
    assert not [p for p in out.iterdir() if p.name.startswith(".rsalink_tmp_")]


def test_input_equals_artifact_refused(tmp_path, capsys):
    # 입력 폴더 = out-dir 이고 입력 이름이 호흡별.csv → 산출물이 입력을 덮어쓰게 되므로 exit 2, 입력 바이트 불변
    d = tmp_path / "data"
    d.mkdir()
    resp_copy = d / "호흡별.csv"
    shutil.copy(RESP, resp_copy)
    before = resp_copy.read_bytes()
    rc = main([str(resp_copy), RR, "--out-dir", str(d), "--quiet"])
    assert rc == 2
    assert "입력 파일" in capsys.readouterr().err
    assert resp_copy.read_bytes() == before
    assert not (d / "연동리포트.md").exists()      # 셋 다 쓰기 전에 가드 → 아무것도 안 씀


def test_out_dir_not_created_when_input_invalid(tmp_path):
    out = tmp_path / "should_not_exist"
    assert main([str(tmp_path / "없음.csv"), RR, "--out-dir", str(out)]) == 2
    assert not out.exists()


def test_out_dir_tilde_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main([RESP, RR, "--out-dir", "~/x", "--quiet"]) == 0
    assert (tmp_path / "x" / "연동리포트.md").exists()


def test_out_dir_symlink_dir_uses_realpath(tmp_path, capsys):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(str(real), str(link))
    assert main([RESP, RR, "--out-dir", str(link)]) == 0
    out = capsys.readouterr().out
    assert "링크" in out and "실제경로" in out
    assert (real / "연동리포트.md").exists()


# ---------------------------------------------------------------- 라운드 1 B2: CSV 수식 가드 · md 이스케이프


def test_csv_formula_guard_labels_and_subject(tmp_path):
    from rsalink.report import _cell
    assert _cell("\tcmd") == "'\tcmd" and _cell("\rx") == "'\rx" and _cell("@SUM") == "'@SUM"
    assert _cell("-5") == "'-5" and _cell(-5.0) == "-5" and _cell("plain") == "plain"
    seg = tmp_path / "s.csv"
    seg.write_text('start,end,label\n0:00,5:00,"=HYPERLINK(""http://x"",""y"")"\n5:00,15:00,+cmd|pipe\n',
                   encoding="utf-8")
    out = tmp_path / "o"
    rc = main([RESP, RR, "--segments", str(seg), "--subject", "=1+1", "--out-dir", str(out), "--quiet"])
    assert rc == 0
    import csv
    for name in ("호흡별.csv", "조건비교.csv"):
        with open(out / name, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        for r in rows[1:]:
            for c in r:
                assert not c or c[0] not in "=+-@\t\r", (name, c)
        subj = [r[0] for r in rows[1:]]
        assert subj and all(s == "'=1+1" for s in subj)
        labs = {r[1] if name == "조건비교.csv" else r[2] for r in rows[1:]}
        assert "'=HYPERLINK(\"http://x\",\"y\")" in labs and "'+cmd|pipe" in labs
    md = (out / "연동리포트.md").read_text(encoding="utf-8")
    assert "+cmd\\|pipe" in md
    assert not re.search(r"^\| \+cmd\|pipe \|", md, re.M)


# ---------------------------------------------------------------- 결함 3: out-dir


def test_out_dir_is_file_exit2(tmp_path, capsys):
    f = tmp_path / "파일.txt"
    f.write_text("x")
    assert main([RESP, RR, "--out-dir", str(f)]) == 2
    assert "이미 있는 파일" in capsys.readouterr().err


def test_out_dir_permission_denied_exit2(tmp_path, capsys):
    if os.geteuid() == 0:
        pytest.skip("root 는 권한 검사를 우회")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert main([RESP, RR, "--out-dir", str(ro / "sub")]) == 2
        assert "권한" in capsys.readouterr().err
    finally:
        ro.chmod(stat.S_IRWXU)


# ---------------------------------------------------------------- 결함 4: basename


def test_artifacts_basename_only(tmp_path):
    out = tmp_path / "deep" / "결과"
    rc = main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--out-dir", str(out), "--quiet"])
    assert rc == 0
    for name in ("연동리포트.md", "호흡별.csv", "조건비교.csv"):
        text = (out / name).read_text(encoding="utf-8-sig")
        assert str(tmp_path) not in text
        assert ROOT not in text
        assert "/Users/" not in text and "\\Users\\" not in text


# ---------------------------------------------------------------- 결함 5: 금지어


# 라운드 1 C1: 인과·기전·진단 문구 스템. bare "기인" 은 cli.py 의 "호기인" 과, bare "suggest" 는 식별자와 충돌해
# 아래 형태만 쓴다. "확인" 단독은 허용("--invert 를 확인")이라 "확인되었/확인됐" 만 금지.
FORBIDDEN_KR = ["때문", "매개했", "입증", "증명", "원인이",
                "기인한", "기인했", "기인하", "설명한", "설명합", "설명해", "매개", "원인", "유발", "초래", "야기", "인해",
                "반영", "시사", "증거", "뒷받침", "확인되었", "확인됐", "활성", "부교감",
                # 진단 문구
                "진단할 수", "진단에 쓸", "진단 도구로", "질환을 판", "질병을 판", "환자를 선별", "치료 효과를 확인"]
FORBIDDEN_EN = ["due to", "caused", "cause of", "because", "mediat", "explain", "reflect", "indicat", "vagal",
                "parasympath", "attribut", "driven by", "account for", "evidence of", "demonstrat", "confirm",
                "suggests that", "suggesting"]
FORBIDDEN = FORBIDDEN_KR + FORBIDDEN_EN


def _forbidden_hits(text):
    low = text.lower()
    return [w for w in FORBIDDEN if w.lower() in low]


# HARDENING.md 는 금지어 목록 자체를 표로 적으므로 검사 대상에서 뺀다(기획서 범위: 리포트·README·사용법·실행.command).
@pytest.mark.parametrize("rel", ["rsalink/report.py", "rsalink/segments.py", "rsalink/cli.py", "README.md", "사용법.md",
                                 "실행.command"])
def test_forbidden_phrases(rel):
    text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    hits = _forbidden_hits(text)
    assert not hits, f"{rel}: {hits}"


def _events_csv(tmp_path):
    ev = tmp_path / "events.csv"
    lines = ["timestamp"] + [f"{t:.1f}" for t in range(0, 900, 5)]
    ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(ev)


def _report_runs(tmp_path):
    """C1 검사 대상 리포트 4종: paced · sham · 이벤트 입력 · 판정 보류(구간 데이터 부족)."""
    sham_resp = os.path.join(ROOT, "examples", "sham", "호흡.csv")
    sham_rr = os.path.join(ROOT, "examples", "sham", "RR.csv")
    return {
        "paced": [RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--pace", "6"],
        "sham": [sham_resp, sham_rr, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00"],
        "events": [_events_csv(tmp_path), RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00"],
        "hold": [RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "14:00-30:00"],
    }


@pytest.mark.parametrize("case", ["paced", "sham", "events", "hold"])
def test_report_output_no_forbidden(tmp_path, case):
    out = tmp_path / case
    args = _report_runs(tmp_path)[case] + ["--out-dir", str(out), "--quiet"]
    rc = main(args)
    assert rc in (0, 3)
    md = (out / "연동리포트.md").read_text(encoding="utf-8")
    hits = _forbidden_hits(md)
    assert not hits, (case, hits)


@pytest.mark.parametrize("mutant", [
    "HF 상승은 호흡수 감소를 반영한다.",
    "이 결과는 부교감 활성 증가를 시사한다.",
    "The HF increase was driven by slower breathing, suggesting vagal activation.",
    "호흡 변화로 인해 HF 가 변했다고 설명할 수 있다.",
])
def test_forbidden_scanner_kills_euphemism_mutants(mutant):
    # 완곡한 인과 문장 4종을 리포트에 주입했을 때 검사기가 잡는지(뮤턴트가 죽는지) 확인
    assert _forbidden_hits("정상 문장. " + mutant), mutant


# ---------------------------------------------------------------- exit 2 입력 오류


def test_missing_file_exit2(tmp_path, capsys):
    assert main([str(tmp_path / "없음.csv"), RR]) == 2
    assert "입력 파일이 없습니다" in capsys.readouterr().err


def test_segments_conflict_exit2(tmp_path, capsys):
    seg = tmp_path / "s.csv"
    seg.write_text("start,end,label\n0:00,1:00,a\n")
    assert main([RESP, RR, "--segments", str(seg), "--baseline", "0:00-1:00"]) == 2


def test_bad_pace_exit2():
    assert main([RESP, RR, "--pace", "0"]) == 2


# ---------------------------------------------------------------- exit 3 게이트


def _write_rr(path, values):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("rr\n" + "\n".join(f"{v:.1f}" for v in values) + "\n")


def test_gate_rr_excluded_exit3(tmp_path, capsys):
    # 정상 RR 에 25% 를 100 ms(범위 밖) 로
    vals = [float(l) for l in open(RR, encoding="utf-8").read().split()[1:]]
    bad = [100.0 if i % 4 == 0 else v for i, v in enumerate(vals)]
    p = tmp_path / "rr_bad.csv"
    _write_rr(p, bad)
    rc = main([RESP, str(p), "--quiet"])
    assert rc == 3
    assert "RR 제외" in capsys.readouterr().err


def test_gate_usable_short_exit3(tmp_path, capsys):
    vals = [float(l) for l in open(RR, encoding="utf-8").read().split()[1:]]
    p = tmp_path / "rr_short.csv"
    _write_rr(p, vals[:100])   # ≈ 90 s
    rc = main([RESP, str(p), "--quiet"])
    assert rc == 3
    assert "사용 가능 구간" in capsys.readouterr().err


def test_gate_valid_breath_exit3(tmp_path, capsys):
    # 호흡 파형을 1 Hz 구형파 잡음으로 덮어 대부분이 short/low_prom 이 되게 — 대신 이벤트 입력으로 직접 만든다:
    # 이벤트 간격 1.0 s(< min 1.5 → short) 가 70%, 정상 5 s 가 30%
    ev = tmp_path / "ev.csv"
    t, lines = 0.0, ["timestamp"]
    for i in range(200):
        lines.append(f"{t:.2f}")
        t += 1.0 if i % 10 < 7 else 5.0
    ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc = main([str(ev), RR, "--quiet"])
    assert rc == 3
    assert "유효 호흡" in capsys.readouterr().err


# ---------------------------------------------------------------- 라운드 1 A5: 구간 클리핑


def _compare_rows(path):
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_stimulus_beyond_data_is_clipped_and_numbers_match(tmp_path):
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    assert main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-60:00", "--out-dir", str(out_a), "--quiet"]) == 0
    assert main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--out-dir", str(out_b), "--quiet"]) == 0
    md = (out_a / "연동리포트.md").read_text(encoding="utf-8")
    assert re.search(r"\| 자극 \| 5:00\.0–1[45]:\d\d\.\d \(요청 5:00\.0–60:00\.0, 데이터 범위로 8\d% 잘림\)", md)
    ra, rb = _compare_rows(out_a / "조건비교.csv"), _compare_rows(out_b / "조건비교.csv")
    assert len(ra) == len(rb) == 2
    skip = {"t0_requested_s", "t1_requested_s", "clipped_frac", "t1_s", "duration_s"}
    for x, y in zip(ra, rb):
        for k in x:
            if k not in skip:
                assert x[k] == y[k], k
    assert ra[1]["clipped_frac"] != "0" and rb[1]["clipped_frac"] == "0"
    # 잘린 뒤 길이 = 데이터 끝(≈15분) 이라 두 실행의 t1 차이는 RR 마지막 박동 안(<2 s)
    assert abs(float(ra[1]["t1_s"]) - float(rb[1]["t1_s"])) < 2.0


def test_huge_stimulus_finishes_fast(tmp_path):
    import time
    t = time.perf_counter()
    rc = main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-1000000:00", "--quiet"])
    assert rc == 0
    assert time.perf_counter() - t < 1.0


def test_segment_clipped_below_2min_holds(tmp_path, capsys):
    # 데이터 15분: 14:00–30:00 요청 → 잘린 뒤 ≈1분 → 스펙트럼·판정 생략, 판정 보류(구간 데이터 부족)
    rc = main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "14:00-30:00"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "구간 데이터 부족" in out
    assert re.search(r"HF 변화가 호흡수 변화와 \*\*판정 보류\(.*구간 데이터 부족.*\)\*\*", out)


# ---------------------------------------------------------------- 라운드 2 #5·#8·#9


def test_rate_gate_is_per_segment_not_whole_record(tmp_path, capsys):
    # 라운드 1 결정 4 고정: 기준선 30/분(2 s) 2분 = 60 호흡 + 자극 5/분(12 s) 13분 = 65 호흡, 이벤트 입력.
    # 전체 기록으로 계산하면 CV 74%·|중앙값−평균|/중앙값 240% → exit 3 (뮤턴트 "whole-record CV" 는 여기서 죽는다);
    # 구간 안에서는 두 구간 모두 CV 0 → exit 0. 같은 데이터를 구간 없이 주면 "전체" 한 구간이라 exit 3.
    from rsalink.breath import rate_stability
    ev = tmp_path / "ev.csv"
    t, lines = 0.0, ["timestamp"]
    while t < 900.0:
        lines.append(f"{t:.2f}")
        t += 2.0 if t < 120.0 else 12.0
    ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
    flag, reasons = rate_stability([30.0] * 60 + [5.0] * 65)
    assert flag and any("CV" in r for r in reasons) and any("중앙값" in r for r in reasons)
    assert main([str(ev), RR, "--baseline", "0:00-2:00", "--stimulus", "2:00-15:00", "--quiet"]) == 0
    assert "호흡 검출 불안정" not in capsys.readouterr().err
    assert main([str(ev), RR, "--quiet"]) == 3
    err = capsys.readouterr().err
    assert "[전체]" in err and "호흡 검출 불안정" in err


def test_negative_rsa_hint_mentions_rsa_lag_not_resp_offset(capsys):
    # 라운드 1 A1 문구 고정: 음수 RSA 안내는 --rsa-lag·위상 열을 가리키고 --resp-offset 을 권하지 않는다
    assert main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00"]) == 0
    out = capsys.readouterr().out
    sent = [ln for ln in out.splitlines() if "음수면" in ln]
    assert sent and all("--rsa-lag" in ln and "--resp-offset" not in ln for ln in sent), sent
    usage = open(os.path.join(ROOT, "사용법.md"), encoding="utf-8").read()
    bullet = [ln for ln in usage.splitlines() if "RSA_pv 가 음수" in ln]
    assert bullet and "--rsa-lag" in bullet[0] and "--resp-offset" not in bullet[0]


def test_link_note_ignores_macos_private_prefix(capsys):
    import tempfile
    from rsalink.report import path_differs_beyond_private
    assert not path_differs_beyond_private("/tmp/x", "/private/tmp/x")
    assert not path_differs_beyond_private("/private/tmp/x", "/private/tmp/x")
    assert not path_differs_beyond_private("/var/folders/ab/y", "/private/var/folders/ab/y")
    assert path_differs_beyond_private("/a/link", "/a/real")
    assert path_differs_beyond_private("/tmp/link", "/private/tmp/real")
    if os.path.realpath("/tmp") == "/tmp":
        pytest.skip("/tmp 가 /private 로 풀리지 않는 환경")
    d = tempfile.mkdtemp(prefix="rsalink_r2_", dir="/tmp")
    try:
        for _ in range(2):      # 폴더가 없을 때·이미 있을 때(라운드 1 은 이때만 찍었다) 모두 표기 없음
            assert main([RESP, RR, "--out-dir", os.path.join(d, "out"), "--quiet"]) == 0
            out = capsys.readouterr().out
            assert "(폴더: " in out and "링크" not in out and "실제경로" not in out, out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_overlapping_segments_exit2(tmp_path, capsys):
    assert main([RESP, RR, "--baseline", "0:00-6:00", "--stimulus", "5:00-15:00"]) == 2
    assert "구간 겹침" in capsys.readouterr().err
    seg = tmp_path / "ov.csv"
    seg.write_text("start,end,label\n0:00,5:00,a\n4:00,9:00,b\n", encoding="utf-8")
    assert main([RESP, RR, "--segments", str(seg)]) == 2
    err = capsys.readouterr().err
    assert "구간 겹침" in err and "Traceback" not in err
    assert main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--quiet"]) == 0   # 끝 = 시작 허용


def test_negative_or_empty_range_clean_korean_message(capsys):
    for args in (["--stimulus", "-1:00-5:00"], ["--stimulus=-1:00-5:00"], ["--baseline", "5:00-"], ["--baseline", "-5:00"]):
        assert main([RESP, RR] + args) == 2, args
        err = capsys.readouterr().err
        assert err.startswith("오류:") and "''" not in err and "Traceback" not in err, (args, err)
        assert "M:SS-M:SS" in err or "명령줄 인자 오류" in err, (args, err)
    assert main([RESP, RR, "--stimulus=-1:00-5:00"]) == 2
    assert "음수" in capsys.readouterr().err
    # argparse 자체 오류(알 수 없는 옵션·인자 누락)도 한국어 한 줄 + exit 2
    assert main([RESP, RR, "--no-such-flag"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("오류: 명령줄 인자 오류") and "usage:" not in err
    assert main([RESP]) == 2
    assert "명령줄 인자 오류" in capsys.readouterr().err


def test_gate_rate_unstable_exit3(tmp_path, capsys):
    # 2 s / 10 s 가 번갈아(둘 다 short/long 아님) → 호흡수 30·6 → CV ≈ 67% > 60% → exit 3
    ev = tmp_path / "ev.csv"
    t, lines = 0.0, ["timestamp"]
    for i in range(150):
        lines.append(f"{t:.2f}")
        t += 2.0 if i % 2 == 0 else 10.0
    ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc = main([str(ev), RR, "--quiet"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "호흡 검출 불안정" in err and "CV" in err


# ---------------------------------------------------------------- 실행.command


def test_run_command_script_exit0(tmp_path):
    # 저장소 안에 결과/ 를 만들지 않도록 스크립트와 예시를 tmp 로 복사해 실행(패키지는 PYTHONPATH 로)
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(os.path.join(ROOT, "실행.command"), work / "실행.command")
    shutil.copytree(os.path.join(ROOT, "examples"), work / "examples",
                    ignore=shutil.ignore_patterns("__pycache__"))
    env = dict(os.environ, PYTHONPATH=ROOT)
    r = subprocess.run(["bash", str(work / "실행.command")], input=b"\n", capture_output=True, cwd=str(work), env=env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode("utf-8", "replace")
    assert re.search(r"^- HF 변화가 호흡수 변화와 \*\*동반됨\*\*", out, re.M)
    assert (work / "결과" / "paced_6bpm" / "연동리포트.md").exists()
