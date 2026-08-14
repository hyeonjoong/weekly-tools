"""CLI: 종료코드, 산출 파일, 커버리지 자백, 옵션 방어."""

from __future__ import annotations

import csv

import pytest

from docx_fixture import p_tracked, simple_docx, write_docx
from revcheck.report import ADDED_REFS_CSV, CHANGES_CSV, ISSUES_CSV, REPORT_MD

CLEAN_RESP = """Comment 1-1: How was allocation concealed?
Response: We have added a sentence to the Methods section describing concealment:
"Allocation was concealed in sequentially numbered opaque envelopes."

Comment 1-2: Please confirm the adherence figures.
Response: The adherence figures are unchanged and are reported in the Results section
exactly as in the original submission, with no reanalysis performed.

Comment 1-3: The discussion is adequate.
Response: We thank the reviewer for this positive assessment and have left the Discussion
section unchanged in this revision.
"""


def test_exit_zero_on_a_clean_revision(trio, run_cli):
    old, new, resp = trio(resp=CLEAN_RESP)
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    assert code == 0, out
    assert "종료코드 0 (정상)" in out
    assert "[커버리지 자백]" in out


def test_exit_one_on_a_critical(trio, run_cli):
    from conftest import NEW_MD, OLD_MD

    broken = NEW_MD.replace("5.2 points (SD 3.1)", "5.9 points (SD 3.6)")
    old, new, resp = trio(old=OLD_MD, new=broken, resp=CLEAN_RESP)
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    assert code == 1
    # 무엇이 잡혔는지까지 확인한다 — "[치명" 만 보면 어떤 실행에서도 통과한다.
    assert "[치명 1건]" in out
    assert "숫자가 다른 값으로 바뀌었습니다" in out
    assert "5.2" in out and "5.9" in out


def test_exit_two_on_a_warning_only(trio, run_cli):
    from conftest import NEW_MD, OLD_MD

    resp = CLEAN_RESP.replace(
        '"Allocation was concealed in sequentially numbered opaque envelopes."',
        '"Allocation was concealed in sequentially numbered sealed envelopes."',
    )
    old, new, resp_path = trio(old=OLD_MD, new=NEW_MD, resp=resp)
    code, out = run_cli("--old", old, "--new", new, "--response", resp_path)
    assert code == 2, out
    assert "표현이 다릅니다" in out


def test_exit_three_when_the_numbering_cannot_be_parsed(trio, run_cli):
    old, new, resp = trio(resp="Dear Editor,\n\nWe addressed all comments. Thank you.\n")
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    assert code == 3
    assert "판정불가" in out
    assert "--comments" in out
    assert "[커버리지 자백]" in out  # 판정불가에도 자백은 나온다


def test_manual_comment_ids_recover_from_an_odd_format(trio, run_cli):
    odd = (
        "Point R1.1 — allocation concealment.\n"
        "We added a sentence to the Methods describing concealment.\n"
        "Point R1.2 — adherence.\n"
        "The adherence figures are unchanged.\n"
        "Point R1.3 — discussion.\n"
        "No change was needed here.\n"
    )
    old, new, resp = trio(resp=odd)
    code, out = run_cli(
        "--old", old, "--new", new, "--response", resp, "--comments", "1-1,1-2,1-3"
    )
    assert "판정불가" not in out
    assert "코멘트누락" not in out and "찾지 못했습니다" not in out
    assert "리뷰어 코멘트 식별: 3건 모두 확인했습니다" in out


def test_all_four_output_files_are_written(trio, run_cli, tmp_path):
    old, new, resp = trio(resp=CLEAN_RESP)
    out_dir = tmp_path / "결과"
    code, out = run_cli("--old", old, "--new", new, "--response", resp, "--out-dir", out_dir)
    assert code == 0
    for name in (REPORT_MD, ISSUES_CSV, CHANGES_CSV, ADDED_REFS_CSV):
        assert (out_dir / name).exists(), name
    assert "저장" in out
    report = (out_dir / REPORT_MD).read_text(encoding="utf-8")
    assert "커버리지 자백" in report


def test_changes_csv_has_the_documented_columns(trio, run_cli, tmp_path):
    old, new, resp = trio(resp=CLEAN_RESP)
    out_dir = tmp_path / "결과"
    run_cli("--old", old, "--new", new, "--response", resp, "--out-dir", out_dir)
    with open(out_dir / CHANGES_CSV, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == [
        "제출본문단", "개정본문단", "줄범위", "절", "유형", "숫자변경", "신고여부",
        "신고근거", "제출본텍스트", "개정본텍스트",
    ]
    assert any(row[4] == "변경" for row in rows[1:])


def test_tracked_changes_banner_is_the_first_thing_reported(tmp_path, run_cli):
    """변경내용 추적을 조용히 읽고 넘어가면 실패."""
    old = simple_docx(tmp_path / "old.docx", ["## Methods", "The ISI fell by 5.2 points."])
    new = write_docx(
        tmp_path / "new.docx",
        [
            p_tracked(before="The ISI fell by ", deleted="5.2", inserted="5.2"),
            p_tracked(after=" points. Allocation was concealed in opaque envelopes."),
        ],
    )
    resp = simple_docx(
        tmp_path / "resp.docx",
        [
            "Comment 1-1: How was allocation concealed?",
            'Response: We added: "Allocation was concealed in opaque envelopes."',
            "Comment 1-2: Second point.",
            "Response: We have clarified this in the Discussion section as requested.",
            "Comment 1-3: Third point.",
            "Response: We have clarified this in the Methods section as requested too.",
        ],
    )
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    head = out.split("\n\n")[0]
    assert "변경내용 추적" in head
    assert "수락" in head


def test_tracked_reject_mode_is_reported_as_such(tmp_path, run_cli):
    old = simple_docx(tmp_path / "old.docx", ["## Methods", "The ISI fell by 5.2 points."])
    new = write_docx(
        tmp_path / "new.docx",
        [p_tracked(before="The ISI fell by ", deleted="5.2", inserted="5.8", after=" points.")],
    )
    resp = simple_docx(
        tmp_path / "resp.docx",
        [
            "Comment 1-1: Recheck the primary outcome.",
            "Response: We have revised the primary outcome value after reanalysis.",
            "Comment 1-2: Second point.",
            "Response: We have clarified this in the Discussion section as requested.",
            "Comment 1-3: Third point.",
            "Response: We have clarified this in the Methods section as requested too.",
        ],
    )
    _code, out = run_cli(
        "--old", old, "--new", new, "--response", resp, "--tracked", "reject"
    )
    assert "원본" in out.split("\n\n")[0]


def test_quiet_mode_prints_a_short_summary_with_coverage(trio, run_cli, tmp_path):
    """요약 모드에서도 커버리지 자백은 빠지지 않는다 — 그게 이 툴의 원칙이다."""
    old, new, resp = trio(resp=CLEAN_RESP)
    code, out = run_cli(
        "--old", old, "--new", new, "--response", resp, "--out-dir", tmp_path / "r", "--quiet"
    )
    assert code == 0
    assert len(out.strip().splitlines()) <= 14
    assert "치명 0건" in out
    assert "리뷰어 코멘트 식별" in out and "인용 문구 대조" in out


def test_bad_ratio_is_rejected(trio, run_cli):
    old, new, resp = trio()
    code, out = run_cli("--old", old, "--new", new, "--response", resp, "--ratio", "0.1")
    assert code == 3
    assert "--ratio" in out


def test_bad_comment_ids_are_rejected(trio, run_cli):
    old, new, resp = trio()
    code, out = run_cli("--old", old, "--new", new, "--response", resp, "--comments", "??")
    assert code == 3


def test_missing_file_exits_three(trio, run_cli, tmp_path):
    old, new, _resp = trio()
    code, out = run_cli(
        "--old", old, "--new", new, "--response", str(tmp_path / "nope.md")
    )
    assert code == 3
    assert "오류" in out


def test_version_flag():
    from revcheck.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_unexpected_crash_exits_three_not_one(trio, run_cli, monkeypatch):
    """파이썬이 그냥 죽으면 종료코드 1 = '치명 있음' 이 된다. 그러면 안 된다."""
    from revcheck import cli

    def boom(*_args, **_kwargs):
        raise RuntimeError("내부 오류")

    monkeypatch.setattr(cli, "run_check", boom)
    old, new, resp = trio(resp=CLEAN_RESP)
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    assert code == 3
    assert "이상 없음" in out


def test_truncated_input_never_reports_normal(trio, run_cli, monkeypatch):
    from revcheck import docio

    monkeypatch.setattr(docio, "MAX_PARA_CHARS", 40)
    old, new, resp = trio(resp=CLEAN_RESP)
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    assert code != 0, "일부만 읽고 '정상'이라고 말하면 안 된다"
    assert "일부만 읽었습니다" in out


def test_control_characters_never_reach_stdout_or_files(tmp_path, run_cli):
    """원고 안의 ESC·양방향 제어문자가 화면과 산출 파일에 나오면 안 된다."""
    # 정상 원고에 섞여 들어간 소량의 제어문자(변환 사고·복사 붙여넣기)를 가정한다.
    nasty = "\x1b[2J\x1b]0;PWNED\x07\u202e\u2066\u2069"
    filler = " The trial enrolled adults with chronic insomnia at two urban sleep clinics." * 6
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    resp = tmp_path / "resp.md"
    old.write_text(
        f"# T\n\n## Results\n\nISI fell by 5.2 points.{filler}\n", encoding="utf-8"
    )
    new.write_text(
        f"# T\n\n## Results\n\nISI fell by {nasty}5.8 points.{filler}\n", encoding="utf-8"
    )
    resp.write_text(CLEAN_RESP, encoding="utf-8")
    out_dir = tmp_path / "결과"
    _code, out = run_cli("--old", old, "--new", new, "--response", resp, "--out-dir", out_dir)
    written = [p.read_text(encoding="utf-8-sig") for p in out_dir.iterdir()]
    for blob in [out] + written:
        for ch in ("\x1b", "\x07", "\u202e", "\u2066", "\u2069"):
            assert ch not in blob, (ch, blob[:200])


def test_quiet_mode_says_undecidable_instead_of_zero_findings(trio, run_cli):
    """--quiet 이 판정불가 실행을 '치명 0건'으로 요약하면 거짓말이 된다."""
    old, new, resp = trio(resp="Dear Editor,\n\nWe addressed all comments. Thank you.\n")
    code, out = run_cli("--old", old, "--new", new, "--response", resp, "--quiet")
    assert code == 3
    assert "[판정불가]" in out
    assert "종료코드 3" in out
    assert "치명 0건" not in out


def test_utf16_text_manuscript_is_read(tmp_path, run_cli):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    resp = tmp_path / "resp.md"
    old.write_bytes("Results\n\nThe mean ISI decreased by 5.2 points.\n".encode("utf-16"))
    new.write_bytes("Results\n\nThe mean ISI decreased by 5.8 points.\n".encode("utf-16"))
    resp.write_text(CLEAN_RESP, encoding="utf-8")
    code, out = run_cli("--old", old, "--new", new, "--response", resp)
    assert code == 1
    assert "5.8" in out
