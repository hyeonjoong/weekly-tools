"""examples/ 합성 패킷 5종 — 실행.command 가 보여 주는 것과 같은 결과여야 한다."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples")


def _run(*args):
    proc = subprocess.run([sys.executable, "-m", "irbpack"] + list(args), cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout


def test_examples_regenerate_identically(tmp_path):
    """_make_examples.py 는 결정론적 — 임시 폴더에 다시 만들면 커밋된 document.xml 과 바이트 단위로 같다."""
    import importlib.util
    import zipfile
    spec = importlib.util.spec_from_file_location("mk", os.path.join(EX, "_make_examples.py"))
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.HERE = str(tmp_path)
    mk.main()
    compared = 0
    for d in os.listdir(tmp_path):
        for f in os.listdir(os.path.join(str(tmp_path), d)):
            if not f.endswith(".docx"):
                continue
            with zipfile.ZipFile(os.path.join(str(tmp_path), d, f)) as a, zipfile.ZipFile(os.path.join(EX, d, f)) as b:
                assert a.read("word/document.xml") == b.read("word/document.xml"), (d, f)
            compared += 1
    assert compared >= 20


def test_clean_packet_exit_0():
    code, out = _run(os.path.join(EX, "정합_패킷"))
    assert code == 0 and "[치명] 0건" in out and "[경고] 0건" in out


def test_mismatch_packet_exit_1_with_expected_items():
    code, out = _run(os.path.join(EX, "불일치_패킷"))
    assert code == 1
    assert "[치명] 5건" in out and "[경고] 3건" in out
    for needle in ("방문횟수", "소요시간", "보상", "문서 버전·날짜", "개인 휴대전화", "전파 누락", "동의 범위 밖"):
        assert needle in out
    assert "010-****-5678" in out and "010-1234-5678" not in out


def test_undecidable_packet_exit_3():
    code, out = _run(os.path.join(EX, "판정불가_패킷"))
    assert code == 3 and "읽지 못한 문서 1" in out and "exit 3" in out


def test_baseline_examples():
    code, out = _run(os.path.join(EX, "개정후_패킷"), "--baseline", os.path.join(EX, "개정전_패킷"))
    assert code == 1 and out.count("개정 미반영") == 2


def test_examples_are_synthetic():
    """실제 환자·실제 IRB 문서 금지 — 합성 표식과 가짜 연락처만."""
    src = open(os.path.join(EX, "_make_examples.py"), encoding="utf-8").read()
    assert "전부 가짜" in src and "010-1234-5678" in src and "02-000-0000" in src
