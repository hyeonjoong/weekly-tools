"""R3 최종 리뷰에서 나온 회귀 테스트 (--out 링크 우회 데이터 손실 방지)."""

import os

from logflow.cli import main

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "app_events.csv")


def _make_input(tmp_path):
    p = tmp_path / "in.csv"
    p.write_text(
        "user_id,event,timestamp\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u2,a,2026-01-02T09:00:00\n",
        encoding="utf-8",
    )
    return p


def test_out_symlink_to_input_rejected_no_data_loss(tmp_path, capsys):
    src = _make_input(tmp_path)
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    os.symlink(src, link)
    rc = main([str(src), "--out", str(link)])
    assert rc == 1
    assert "덮어쓰기" in capsys.readouterr().err
    assert src.read_bytes() == before  # 입력 파일 보존


def test_out_hardlink_to_input_rejected_no_data_loss(tmp_path, capsys):
    src = _make_input(tmp_path)
    before = src.read_bytes()
    hard = tmp_path / "hard.csv"
    os.link(src, hard)
    rc = main([str(src), "--out", str(hard)])
    assert rc == 1
    assert src.read_bytes() == before


def test_input_as_symlink_out_real_path_rejected(tmp_path, capsys):
    src = _make_input(tmp_path)
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    os.symlink(src, link)
    # 입력을 심볼릭으로 주고 --out 을 실제 경로로 → 같은 파일이므로 거부
    rc = main([str(link), "--out", str(src)])
    assert rc == 1
    assert src.read_bytes() == before


def test_out_distinct_path_still_writes(tmp_path):
    src = _make_input(tmp_path)
    out = tmp_path / "report.txt"
    rc = main([str(src), "--out", str(out)])
    assert rc == 0
    assert out.exists() and "logflow" in out.read_text(encoding="utf-8")


def test_out_relative_vs_absolute_same_file_rejected(tmp_path, capsys, monkeypatch):
    src = _make_input(tmp_path)
    monkeypatch.chdir(tmp_path)
    # 상대경로 입력 + 절대경로 out 이 같은 파일 → 거부
    rc = main(["in.csv", "--out", str(src)])
    assert rc == 1
