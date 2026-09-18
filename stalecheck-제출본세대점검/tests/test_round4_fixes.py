# -*- coding: utf-8 -*-
"""2라운드 적대 검토 회귀 테스트 — 앞선 수정이 **완전하지 않았던** 곳들.

공통점: 첫 수정이 한 경로만 막았고, 같은 결함이 옆 경로에 그대로 남아 있었다.
"""

import os
import shutil
import signal
import subprocess
import sys
import zipfile

import pytest

from conftest import T0, T1, write
from stalecheck import engine, normalize, textdiff, writer
from stalecheck.errors import EXIT_REFUSED, RefusedError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Timeout(Exception):
    pass


def _alarm(seconds=5):
    def handler(*_):
        raise _Timeout()
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)


# ============================== 출력이 끊겨도 종료코드가 120 으로 바뀌지 않는다
def _pipe_exit(args, suffix):
    quoted = " ".join("'%s'" % a.replace("'", "'\\''") for a in args)
    proc = subprocess.run(
        "%s -m stalecheck %s 2>/dev/null %s; exit ${PIPESTATUS[0]}"
        % (sys.executable, quoted, suffix),
        shell=True, cwd=ROOT, capture_output=True, executable="/bin/bash")
    return proc.returncode


@pytest.fixture
def clean_tree(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "a.md", "같음", T0)
    write(pkg / "a.md", "같음", T0)
    return str(work), str(pkg)


@pytest.fixture
def stale_tree(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "a.md", "새 내용", T1)
    write(pkg / "a.md", "옛 내용", T0)
    return str(work), str(pkg)


@pytest.mark.parametrize("suffix", ["| true", "| head -1 >/dev/null",
                                    "| head -c1 >/dev/null"])
def test_clean_tree_stays_zero_when_the_pipe_closes(clean_tree, suffix):
    """`| head` 로 조기 종료하면 CPython 이 상태를 120 으로 덮어썼다."""
    work, pkg = clean_tree
    assert _pipe_exit(["--work", work, "--package", pkg], suffix) == 0


@pytest.mark.parametrize("suffix", ["| true", "| head -2 >/dev/null"])
def test_stale_tree_stays_one_when_the_pipe_closes(stale_tree, suffix):
    work, pkg = stale_tree
    assert _pipe_exit(["--work", work, "--package", pkg], suffix) == 1


def test_readonly_stdout_does_not_become_120(clean_tree):
    work, pkg = clean_tree
    proc = subprocess.run(
        "%s -m stalecheck --work '%s' --package '%s' 1</dev/null 2>/dev/null"
        % (sys.executable, work, pkg),
        shell=True, cwd=ROOT, capture_output=True, executable="/bin/bash")
    assert proc.returncode == 0


def test_exit_code_never_leaves_the_documented_set(stale_tree):
    work, pkg = stale_tree
    for suffix in ("", "| true", "| head -1 >/dev/null"):
        code = _pipe_exit(["--work", work, "--package", pkg], suffix or "; true")
        assert code in (0, 1, 2, 3), (suffix, code)


# ============================== FIFO 가 zip·텍스트 경로에서도 멈추지 않는다
def test_fifo_named_docx_does_not_hang(tmp_path):
    """`.docx` 는 이 툴이 가장 많이 해시하는 형식이다 — 여기만 열린 채 남아 있었다."""
    p = str(tmp_path / "doc.docx")
    os.mkfifo(p)
    _alarm(5)
    try:
        with pytest.raises(normalize.UnreadableFile):
            normalize.content_hash(p)
    finally:
        signal.alarm(0)


def test_fifo_named_md_does_not_hang_in_textdiff(tmp_path):
    p = str(tmp_path / "a.md")
    os.mkfifo(p)
    _alarm(5)
    try:
        result = textdiff.line_diff(p, p)
    finally:
        signal.alarm(0)
    assert not result.comparable


def test_fifo_at_artifact_path_does_not_hang(tmp_path):
    """산출물 자리의 FIFO 는 `fstat` 이전에 커널에서 멈춘다 — O_NONBLOCK 이 필요했다."""
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    os.mkfifo(os.path.join(out, "세대점검.md"))
    _alarm(5)
    try:
        with pytest.raises(RefusedError):
            writer.write_text(out, "세대점검.md", "x")
    finally:
        signal.alarm(0)


# ============================== --out-dir 을 검사 이후 바꿔치기해도 못 쓴다
def test_out_dir_swapped_for_symlink_after_prepare_is_refused(tmp_path):
    work = tmp_path / "논문"
    os.makedirs(str(work))
    victim = write(work / "victim.md", "PRECIOUS", T1)
    out = writer.prepare_out_dir(str(tmp_path / "out"), forbidden_roots=[str(work)])
    os.rmdir(out)
    os.symlink(str(work), out)
    with pytest.raises(RefusedError):
        writer.write_text(out, "victim.md", "CLOBBERED")
    assert open(victim, encoding="utf-8").read() == "PRECIOUS"


def test_out_dir_replaced_by_another_directory_is_refused(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    shutil.rmtree(out)
    os.makedirs(out)   # 같은 경로, 다른 폴더(inode 가 다르다)
    with pytest.raises(RefusedError) as exc:
        writer.write_text(out, "세대점검.md", "x")
    assert "바뀌었습니다" in exc.value.message


def test_normal_out_dir_still_works(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    path = writer.write_text(out, "세대점검.md", "정상")
    assert open(path, encoding="utf-8").read() == "정상"


# ============================== 대소문자 접기가 '받아들이는 쪽'에 새지 않는다
def test_is_inside_without_fold_rejects_case_variant(tmp_path):
    assert writer.is_inside("/x/Work/sub", "/x/work") is True
    assert writer.is_inside("/x/Work/sub", "/x/work", fold=False) is False


def test_is_inside_with_fold_still_refuses_case_variant_out_dir(tmp_path):
    assert writer.is_inside("/x/WORK/rep", "/x/work") is True


def test_package_validation_uses_unfolded_comparison():
    """`--package` 는 받아들이는 판단이므로 대소문자를 접으면 안 된다."""
    import inspect
    src = inspect.getsource(engine._validate_roots)
    assert "fold=False" in src
    assert src.count("fold=False") >= 3


# ============================== zip 중복 엔트리의 순서를 잃지 않는다
def test_duplicate_entry_order_changes_the_hash(tmp_path):
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    for path, entries in ((a, [("x.xml", b"1"), ("x.xml", b"2")]),
                          (b, [("x.xml", b"2"), ("x.xml", b"1")])):
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in entries:
                zf.writestr(name, data)
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_entry_order_between_different_names_is_still_ignored(tmp_path):
    """엔트리 저장 순서는 내용이 아니다 — 여전히 무시해야 한다."""
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    entries = [("docProps/core.xml", "<c/>"), ("word/document.xml", "<x/>"),
               ("word/styles.xml", "<s/>")]
    with zipfile.ZipFile(a, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    with zipfile.ZipFile(b, "w") as zf:
        for name, data in reversed(entries):
            zf.writestr(name, data)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


# ============================== /ID 가 본문을 삼키지 않는다
def test_id_pattern_does_not_eat_content_in_a_stream(tmp_path):
    """스트림 안의 우연한 `/ID[` 가 본문을 삼키면 서로 다른 문서가 '일치'가 된다."""
    a = write(tmp_path / "a.pdf",
              b"%PDF-1.4 stream /ID[p = 0.001 significant]  endstream")
    b = write(tmp_path / "b.pdf",
              b"%PDF-1.4 stream /ID[p = 0.999 not signific]  endstream")
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_real_trailer_id_is_still_stripped(tmp_path):
    a = write(tmp_path / "a.pdf", b"%PDF-1.4 trailer << /ID [<AB12> <AB12>] >> /T (K)")
    b = write(tmp_path / "b.pdf", b"%PDF-1.4 trailer << /ID [<9F00> <9F00>] >> /T (K)")
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_id_with_whitespace_and_one_element_stripped(tmp_path):
    a = write(tmp_path / "a.pdf", b"%PDF-1.4 /ID[ <AA> ] /T (K)")
    b = write(tmp_path / "b.pdf", b"%PDF-1.4 /ID[ <BB> ] /T (K)")
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]
