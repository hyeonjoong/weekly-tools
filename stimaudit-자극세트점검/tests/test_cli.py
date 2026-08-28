"""CLI — 종료코드와 경계 강제. 이 툴은 종료코드로 파이프라인에 말합니다.

  0 치명 0건 · 1 치명 발견 · 2 입력/옵션 오류 · 3 판정불가(못 읽은 파일 있음)
  **3 이 1보다 우선합니다** — 다 못 들었으면 "치명 0건"은 거짓말입니다.
"""
from __future__ import annotations

import json
import os
import unicodedata

import pytest

from stimaudit import cli, wavread
from tests.conftest import fade, sine_rms

FS48 = 48000


def _wav(tmp_path, name, level=-23.0, seconds=1.2, freq=400.0, sub=None, bits=24):
    d = str(tmp_path) if sub is None else os.path.join(str(tmp_path), sub)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    wavread.write_wav(p, [fade(sine_rms(freq, seconds, level, FS48), FS48, ms=150.0)],
                      FS48, bits)
    return p


def _run(args):
    return cli.main(args)


# ------------------------------------------------------------ 종료코드 2

def test_no_arguments_is_usage_error(capsys):
    assert _run([]) == cli.EXIT_USAGE
    assert "2개 이상" in capsys.readouterr().err


def test_single_file_refuses_and_points_elsewhere(tmp_path, capsys):
    """세트가 아니면 이 툴의 질문 자체가 성립하지 않습니다."""
    a = _wav(tmp_path, "a.wav")
    assert _run([a, "--inspect"]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "세트" in err
    assert "bell_acoustic_qc.py" in err and "DEBUSSY" in err


def test_single_file_refuses_even_with_out_dir(tmp_path):
    a = _wav(tmp_path, "a.wav")
    assert _run([a, "--out-dir", os.path.join(str(tmp_path), "o")]) == cli.EXIT_USAGE


def test_absolute_spl_request_refused(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    assert _run([a, b, "--inspect", "--spl-db", "70"]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "dBFS 기준" in err
    assert "WHO" in err


def test_out_dir_required_without_inspect(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    assert _run([a, b]) == cli.EXIT_USAGE
    assert "--inspect" in capsys.readouterr().err


def test_out_dir_pointing_at_a_file_is_a_clean_error(tmp_path, capsys):
    """트레이스백이 아니라 한국어 한 줄 + 종료코드 2."""
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    f = os.path.join(str(tmp_path), "notadir")
    open(f, "w").write("x")
    assert _run([a, b, "--out-dir", f]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "폴더가 아니라 파일" in err
    assert "Traceback" not in err


def test_missing_input_path(tmp_path, capsys):
    a = _wav(tmp_path, "a.wav")
    assert _run([a, os.path.join(str(tmp_path), "ghost.wav"), "--inspect"]) == cli.EXIT_USAGE
    assert "찾을 수 없" in capsys.readouterr().err


def test_duplicate_basenames_across_folders(tmp_path, capsys):
    """설계 JSON 과 리포트가 파일 이름으로 대조하므로 구분할 수 없습니다."""
    a = _wav(tmp_path, "같은이름.wav", sub="v1")
    b = _wav(tmp_path, "같은이름.wav", sub="v2", freq=420.0)
    assert _run([a, b, "--inspect"]) == cli.EXIT_USAGE
    assert "같은 이름" in capsys.readouterr().err


def test_same_file_listed_twice_is_deduplicated(tmp_path):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    assert _run([a, a, b, "--inspect", "--quiet"]) == cli.EXIT_OK


def test_design_pointing_at_missing_file(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    d = os.path.join(str(tmp_path), "d.json")
    json.dump({"conditions": {"x": ["ghost.wav"]}}, open(d, "w"))
    assert _run([a, b, "--inspect", "--design", d]) == cli.EXIT_USAGE
    assert "ghost.wav" in capsys.readouterr().err


def test_bad_thresholds(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    assert _run([a, b, "--inspect", "--lufs-tol", "0"]) == cli.EXIT_USAGE
    assert _run([a, b, "--inspect", "--lufs-crit", "0.5", "--lufs-tol", "1.0"]) == cli.EXIT_USAGE
    assert "커야" in capsys.readouterr().err


def test_missing_manifest(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    assert _run([a, b, "--inspect", "--manifest",
                 os.path.join(str(tmp_path), "no.csv")]) == cli.EXIT_USAGE


def test_missing_baseline_dir(tmp_path):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    out = os.path.join(str(tmp_path), "o")
    assert _run([a, b, "--out-dir", out, "--baseline",
                 os.path.join(str(tmp_path), "ghostdir")]) == cli.EXIT_USAGE


# ------------------------------------------------------------ 종료코드 0 / 1

def test_matched_set_exits_zero(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    d = os.path.join(str(tmp_path), "d.json")
    json.dump({"conditions": {"x": ["a.wav"], "y": ["b.wav"]}}, open(d, "w"))
    out = os.path.join(str(tmp_path), "o")
    assert _run([a, b, "--design", d, "--out-dir", out, "--quiet"]) == cli.EXIT_OK
    assert "치명 0건" in capsys.readouterr().out


def test_level_mismatch_exits_one(tmp_path):
    a = _wav(tmp_path, "a.wav", level=-20.0)
    b = _wav(tmp_path, "b.wav", level=-25.0, freq=420.0)
    d = os.path.join(str(tmp_path), "d.json")
    json.dump({"conditions": {"active": ["a.wav"], "control": ["b.wav"]}}, open(d, "w"))
    out = os.path.join(str(tmp_path), "o")
    assert _run([a, b, "--design", d, "--out-dir", out, "--quiet"]) == cli.EXIT_CRITICAL


def test_inspect_never_reports_critical(tmp_path):
    a = _wav(tmp_path, "a.wav", level=-20.0)
    b = _wav(tmp_path, "b.wav", level=-30.0, freq=420.0)
    assert _run([a, b, "--inspect", "--quiet"]) == cli.EXIT_OK


def test_inspect_writes_no_files(tmp_path):
    """--inspect 는 빈 폴더조차 만들지 않습니다."""
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    out = os.path.join(str(tmp_path), "o")
    assert _run([a, b, "--inspect", "--out-dir", out, "--quiet"]) == cli.EXIT_OK
    assert not os.path.exists(out)


# ------------------------------------------------------------ 종료코드 3

def test_unreadable_file_exits_three(tmp_path):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    bad = os.path.join(str(tmp_path), "bad.wav")
    open(bad, "wb").write(b"RIFF" + b"\x00" * 100)
    assert _run([a, b, bad, "--inspect", "--quiet"]) == cli.EXIT_UNDECIDABLE


def test_undecidable_beats_critical(tmp_path, capsys):
    """3 이 1보다 우선합니다 — 다 못 들었으면 '치명 N건'이 결론이 될 수 없습니다."""
    a = _wav(tmp_path, "a.wav", level=-20.0)
    b = _wav(tmp_path, "b.wav", level=-30.0, freq=420.0)
    bad = os.path.join(str(tmp_path), "bad.wav")
    open(bad, "wb").write(b"RIFF" + b"\x00" * 100)
    d = os.path.join(str(tmp_path), "d.json")
    json.dump({"conditions": {"active": ["a.wav"], "control": ["b.wav"]}}, open(d, "w"))
    out = os.path.join(str(tmp_path), "o")
    assert _run([a, b, bad, "--design", d, "--out-dir", out,
                 "--quiet"]) == cli.EXIT_UNDECIDABLE
    assert "거짓말" in capsys.readouterr().out


def test_unreadable_file_named_in_coverage(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    bad = os.path.join(str(tmp_path), "bad.wav")
    open(bad, "wb").write(b"RIFF" + b"\x00" * 100)
    _run([a, b, bad, "--inspect", "--quiet"])
    out = capsys.readouterr().out
    assert "bad.wav" in out
    assert "못 읽음: 1" in out


# ------------------------------------------------------------ 기능

def test_emit_design_is_valid_json(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    assert _run([a, b, "--emit-design"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert sorted(sum(payload["conditions"].values(), [])) == ["a.wav", "b.wav"]
    assert "claims" in payload


def test_emit_design_with_inspect_keeps_stdout_pure_json(tmp_path, capsys):
    """문서가 안내하는 `--inspect --emit-design > 설계.json` 이 실제로 동작해야 합니다."""
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    assert _run([a, b, "--inspect", "--emit-design", "--quiet"]) == cli.EXIT_OK
    cap = capsys.readouterr()
    payload = json.loads(cap.out)                 # 리포트가 섞였으면 여기서 터집니다
    assert sorted(sum(payload["conditions"].values(), [])) == ["a.wav", "b.wav"]
    assert "커버리지 자백" in cap.err              # 리포트는 표준에러로


def test_emit_design_alone_does_not_analyse(tmp_path, capsys):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    _run([a, b, "--emit-design"])
    cap = capsys.readouterr()
    json.loads(cap.out)
    assert "커버리지 자백" not in cap.out and "커버리지 자백" not in cap.err


def test_directory_input_is_expanded(tmp_path):
    _wav(tmp_path, "a.wav", sub="snd")
    _wav(tmp_path, "b.wav", sub="snd", freq=420.0)
    assert _run([os.path.join(str(tmp_path), "snd"), "--inspect", "--quiet"]) == cli.EXIT_OK


def test_empty_directory_is_an_error(tmp_path, capsys):
    d = os.path.join(str(tmp_path), "empty")
    os.makedirs(d)
    assert _run([d, "--inspect"]) == cli.EXIT_USAGE


def test_all_outputs_written(tmp_path):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    out = os.path.join(str(tmp_path), "o")
    _run([a, b, "--out-dir", out, "--quiet"])
    assert sorted(os.listdir(out)) == sorted(
        ["자극점검.md", "문제목록.csv", "자극기술표.csv", "자극기술표.md",
         "음량행렬.csv", "문장초안.md"])


def test_symlinked_artifact_does_not_destroy_input(tmp_path, capsys):
    """--out-dir 에 산출물 이름의 링크를 심어도 원본이 살아 있어야 합니다."""
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    out = os.path.join(str(tmp_path), "o")
    os.makedirs(out)
    os.symlink(a, os.path.join(out, "자극점검.md"))
    before = open(a, "rb").read()
    assert _run([a, b, "--out-dir", out, "--quiet"]) == cli.EXIT_USAGE
    assert open(a, "rb").read() == before


def test_normalize_name_handles_nfd(tmp_path):
    """macOS 는 파일명을 NFD 로 저장하고 설계 JSON 은 보통 NFC 입니다."""
    nfd = unicodedata.normalize("NFD", "싱잉볼.wav")
    nfc = unicodedata.normalize("NFC", "싱잉볼.wav")
    assert cli.normalize_name("/x/" + nfd) == nfc
    assert cli.normalize_name("/x/" + nfc) == nfc


def test_korean_nfd_filename_matches_nfc_design(tmp_path):
    a = _wav(tmp_path, unicodedata.normalize("NFD", "싱잉볼.wav"))
    b = _wav(tmp_path, "핑크.wav", freq=420.0)
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"x": ["싱잉볼.wav"], "y": ["핑크.wav"]}}, fh,
                  ensure_ascii=False)
    out = os.path.join(str(tmp_path), "o")
    assert _run([a, b, "--design", d, "--out-dir", out, "--quiet"]) == cli.EXIT_OK


def test_original_files_are_never_modified(tmp_path):
    a, b = _wav(tmp_path, "a.wav"), _wav(tmp_path, "b.wav", freq=420.0)
    before = {p: (open(p, "rb").read(), os.stat(p).st_mtime) for p in (a, b)}
    _run([a, b, "--out-dir", os.path.join(str(tmp_path), "o"), "--quiet"])
    for p, (data, mtime) in before.items():
        assert open(p, "rb").read() == data
        assert os.stat(p).st_mtime == mtime


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "stimaudit" in capsys.readouterr().out


def test_help_lists_what_it_does_not_do(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for boundary in ("DEBUSSY", "bell_acoustic_qc.py", "calmbark", "statwise", "dB SPL"):
        assert boundary in out


# ------------------------------------------------------------ 번들 예제 통합

def test_example_matched_set_is_clean(examples_dir, tmp_path, capsys):
    d = os.path.join(examples_dir, "맞은세트")
    out = os.path.join(str(tmp_path), "o")
    code = _run([d, "--design", os.path.join(d, "설계.json"), "--out-dir", out, "--quiet"])
    text = capsys.readouterr().out
    assert code == cli.EXIT_OK, text
    assert "치명 0건 · 경고 0건" in text


def test_example_mismatched_set_is_critical(examples_dir, tmp_path, capsys):
    d = os.path.join(examples_dir, "어긋난세트")
    out = os.path.join(str(tmp_path), "o")
    code = _run([d, "--design", os.path.join(d, "설계.json"), "--out-dir", out, "--quiet"])
    text = capsys.readouterr().out
    assert code == cli.EXIT_CRITICAL
    for kind in ("음량 불일치", "주장 불일치", "클리핑", "좌우 불균형",
                 "DC 오프셋", "시작/끝 클릭 위험"):
        assert kind in text, kind


def test_example_undecidable_set_exits_three(examples_dir, capsys):
    d = os.path.join(examples_dir, "판정불가세트")
    assert _run([d, "--inspect", "--quiet"]) == cli.EXIT_UNDECIDABLE
    assert "C_broken.wav" in capsys.readouterr().out


def test_example_baseline_comparison(examples_dir, tmp_path, capsys):
    cur = os.path.join(examples_dir, "어긋난세트")
    old = os.path.join(examples_dir, "맞은세트")
    out = os.path.join(str(tmp_path), "o")
    _run([cur, "--design", os.path.join(cur, "설계.json"), "--baseline", old,
          "--out-dir", out, "--quiet"])
    text = capsys.readouterr().out
    assert "버전 대조" in text
    assert "음량 +3.0 LU" in text


def test_example_manifest_confound_table(examples_dir, tmp_path, capsys):
    d = os.path.join(examples_dir, "맞은세트")
    out = os.path.join(str(tmp_path), "o")
    _run([d, "--design", os.path.join(d, "설계.json"), "--manifest",
          os.path.join(d, "DEBUSSY지표_예시.csv"), "--out-dir", out, "--quiet"])
    text = capsys.readouterr().out
    assert "교란 후보" in text
    assert "의도한 대조축" in text
    assert "roughness_asper" in text     # 받아 쓴 값 — 계산한 값이 아닙니다
    assert "statwise" in text            # 검정이 필요하면 저쪽으로
