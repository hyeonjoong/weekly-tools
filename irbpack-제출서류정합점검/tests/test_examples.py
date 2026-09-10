"""번들 예제 3종 — 문서에 적어 둔 결과가 실제로 나오는지 (README 가 거짓말하지 않게)."""

import os

import pytest

from conftest import EXAMPLES
from irbpack.cli import EXIT_CRITICAL, EXIT_OK, EXIT_UNDECIDABLE, main


def run(packet, tmp_path, extra=None):
    return main([os.path.join(EXAMPLES, packet), "--out-dir", str(tmp_path / "out"), "--quiet"]
                + (extra or []))


def test_examples_exist():
    for packet in ("정합_패킷", "불일치_패킷", "판정불가_패킷", "개정_이전_패킷", "개정_이후_패킷"):
        assert os.path.isdir(os.path.join(EXAMPLES, packet))


def test_baseline_example_finds_unpropagated_change(tmp_path, capsys):
    """--baseline 을 보여 주는 번들 예제 — 계획서만 5회로 고치고 동의서는 그대로다."""
    code = main([os.path.join(EXAMPLES, "개정_이후_패킷"),
                 "--baseline", os.path.join(EXAMPLES, "개정_이전_패킷"),
                 "--out-dir", str(tmp_path / "out")])
    output = capsys.readouterr().out
    assert code == EXIT_CRITICAL
    assert "개정되었는데" in output
    assert "이전 패킷: 3회 → 현재: 5회" in output


def test_docx_versions_exist():
    for packet in ("정합_패킷", "불일치_패킷", "판정불가_패킷"):
        directory = os.path.join(EXAMPLES, "docx", packet)
        assert os.path.isdir(directory)
        assert any(name.endswith(".docx") for name in os.listdir(directory))


def test_maker_script_is_bundled():
    assert os.path.exists(os.path.join(EXAMPLES, "_make_examples.py"))


def test_clean_packet_exit_zero(tmp_path):
    assert run("정합_패킷", tmp_path) == EXIT_OK


def test_flawed_packet_exit_one(tmp_path):
    assert run("불일치_패킷", tmp_path) == EXIT_CRITICAL


def test_undecidable_packet_exit_three(tmp_path):
    assert run("판정불가_패킷", tmp_path) == EXIT_UNDECIDABLE


def test_clean_packet_docx_matches_markdown(tmp_path):
    assert main([os.path.join(EXAMPLES, "docx", "정합_패킷"),
                 "--out-dir", str(tmp_path / "out"), "--quiet"]) == EXIT_OK


def test_flawed_packet_docx_matches_markdown(tmp_path):
    assert main([os.path.join(EXAMPLES, "docx", "불일치_패킷"),
                 "--out-dir", str(tmp_path / "out"), "--quiet"]) == EXIT_CRITICAL


@pytest.mark.parametrize("source", ["불일치_패킷", os.path.join("docx", "불일치_패킷")])
def test_flawed_packet_finds_the_five_planted_defects(tmp_path, source, capsys):
    main([os.path.join(EXAMPLES, source), "--out-dir", str(tmp_path / "out")])
    output = capsys.readouterr().out
    assert "[치명] 4건" in output
    assert "[경고] 1건" in output
    for item in ("문서 버전·날짜", "연령 범위", "방문·회기 횟수", "보상", "평가·검사 항목"):
        assert item in output


def test_undecidable_packet_confesses_the_hwp(tmp_path, capsys):
    main([os.path.join(EXAMPLES, "판정불가_패킷"), "--out-dir", str(tmp_path / "out")])
    output = capsys.readouterr().out
    assert ".hwp" in output
    assert "판정 불가" in output


def test_examples_contain_no_real_phone_numbers():
    """번들 예제에 실제 연락처가 섞이면 안 된다 (전부 합성)."""
    for root, _, names in os.walk(EXAMPLES):
        for name in names:
            if not name.endswith((".md", ".py")):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                text = handle.read()
            assert "010-9278" not in text
            assert "snubh" not in text.lower()


def test_examples_are_marked_synthetic():
    with open(os.path.join(EXAMPLES, "_make_examples.py"), encoding="utf-8") as handle:
        text = handle.read()
    assert "합성" in text and "실제 환자" in text
