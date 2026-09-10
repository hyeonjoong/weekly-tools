"""개정 축 (--baseline) — 프로토콜에서 **바뀐 값만** 보고, 전체 diff 는 하지 않는다."""

import os

from conftest import CONSENT_LINES, PROTOCOL_LINES, load_packet, write_md
from irbpack.baseline import compare_baseline, match_document
from irbpack.cli import EXIT_CRITICAL, EXIT_OK, main


def make_packet(directory, protocol_lines, consent_lines, version="1.2", date="2026-09-01"):
    write_md(directory / ("연구계획서_v%s_%s.md" % (version, date)), protocol_lines)
    write_md(directory / ("ICF_성인용_v%s_%s.md" % (version, date)), consent_lines)
    return directory


def run_baseline(tmp_path, new_protocol, new_consent, old_protocol=None, old_consent=None):
    # 이전 패킷은 파일명·버전을 그대로 두고 **내용만** 다르게 한다.
    # (버전 문자열까지 바꾸면 '버전이 바뀐 것'이 섞여 개정 축 판단이 흐려진다.)
    old = make_packet(tmp_path / "v11", old_protocol or PROTOCOL_LINES,
                      old_consent or CONSENT_LINES)
    new = make_packet(tmp_path / "v12", new_protocol, new_consent)
    old_documents, old_mentions, _ = load_packet(old)
    new_documents, new_mentions, _ = load_packet(new)
    return compare_baseline(new_documents, new_mentions, old_documents, old_mentions)


def test_unchanged_protocol_yields_nothing(tmp_path):
    findings, notes = run_baseline(tmp_path, PROTOCOL_LINES, CONSENT_LINES)
    assert findings == []
    assert any("바뀐 값이 없어" in note for note in notes)


def test_protocol_change_not_propagated_is_critical(tmp_path):
    """프로토콜만 3회 → 5회로 바뀌고 동의서가 그대로면 치명."""
    new_protocol = [line.replace("3회 방문", "5회 방문") for line in PROTOCOL_LINES]
    findings, notes = run_baseline(tmp_path, new_protocol, CONSENT_LINES)
    assert [finding.item for finding in findings] == ["visits"]
    assert findings[0].severity == "치명"
    assert "개정미반영" in findings[0].kinds


def test_propagated_change_is_not_flagged(tmp_path):
    new_protocol = [line.replace("3회 방문", "5회 방문") for line in PROTOCOL_LINES]
    new_consent = [line.replace("3회 방문", "5회 방문") for line in CONSENT_LINES]
    findings, _ = run_baseline(tmp_path, new_protocol, new_consent)
    assert findings == []


def test_unchanged_protocol_items_are_ignored_even_if_inconsistent(tmp_path):
    """프로토콜이 안 바뀐 항목은 개정 축에서 보지 않는다(전체 diff 가 되면 툴이 죽는다)."""
    new_protocol = [line.replace("3회 방문", "5회 방문") for line in PROTOCOL_LINES]
    new_consent = [line.replace("3회 방문", "5회 방문").replace("만 20~65세", "만 20~64세")
                   for line in CONSENT_LINES]
    findings, _ = run_baseline(tmp_path, new_protocol, new_consent)
    assert findings == []


def test_finding_shows_before_and_after(tmp_path):
    new_protocol = [line.replace("30,000 원", "50,000 원") for line in PROTOCOL_LINES]
    findings, _ = run_baseline(tmp_path, new_protocol, CONSENT_LINES)
    assert findings
    note = " ".join(findings[0].notes)
    assert "이전 패킷" in note and "현재" in note


def test_rows_include_old_protocol_row(tmp_path):
    new_protocol = [line.replace("30,000 원", "50,000 원") for line in PROTOCOL_LINES]
    findings, _ = run_baseline(tmp_path, new_protocol, CONSENT_LINES)
    roles = [row.role for row in findings[0].rows]
    assert "프로토콜(이전)" in roles


def test_missing_old_protocol_is_confessed(tmp_path):
    old = tmp_path / "old"
    write_md(old / "ICF_성인용_v1.1.md", CONSENT_LINES)
    new = make_packet(tmp_path / "new", PROTOCOL_LINES, CONSENT_LINES)
    old_documents, old_mentions, _ = load_packet(old)
    new_documents, new_mentions, _ = load_packet(new)
    findings, notes = compare_baseline(new_documents, new_mentions, old_documents, old_mentions)
    assert findings == []
    assert any("이전 패킷에서 프로토콜" in note for note in notes)


def test_match_document_by_role_and_name(tmp_path):
    old = make_packet(tmp_path / "old", PROTOCOL_LINES, CONSENT_LINES, version="1.1", date="2026-08-01")
    new = make_packet(tmp_path / "new", PROTOCOL_LINES, CONSENT_LINES)
    old_documents, _, _ = load_packet(old)
    new_documents, _, _ = load_packet(new)
    consent = [document for document in new_documents if document.role == "동의서"][0]
    matched = match_document(consent, old_documents)
    assert matched is not None and matched.role == "동의서"


def test_match_document_returns_none_without_counterpart(tmp_path):
    new = make_packet(tmp_path / "new", PROTOCOL_LINES, CONSENT_LINES)
    new_documents, _, _ = load_packet(new)
    consent = [document for document in new_documents if document.role == "동의서"][0]
    assert match_document(consent, []) is None


def test_cli_with_baseline_flags_unpropagated_change(tmp_path):
    old = make_packet(tmp_path / "old", PROTOCOL_LINES, CONSENT_LINES, version="1.1", date="2026-08-01")
    new_protocol = [line.replace("3회 방문", "5회 방문") for line in PROTOCOL_LINES]
    new = make_packet(tmp_path / "new", new_protocol, CONSENT_LINES)
    code = main([str(new), "--baseline", str(old), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == EXIT_CRITICAL


def test_cli_baseline_note_printed(tmp_path, capsys):
    old = make_packet(tmp_path / "old", PROTOCOL_LINES, CONSENT_LINES, version="1.1", date="2026-08-01")
    new = make_packet(tmp_path / "new", PROTOCOL_LINES, CONSENT_LINES)
    main([str(new), "--baseline", str(old), "--out-dir", str(tmp_path / "out")])
    output = capsys.readouterr().out
    assert "개정 축" in output
    assert "이전 패킷(--baseline)" in output


def test_cli_baseline_missing_folder_is_confessed(tmp_path, capsys):
    new = make_packet(tmp_path / "new", PROTOCOL_LINES, CONSENT_LINES)
    code = main([str(new), "--baseline", str(tmp_path / "없음"),
                 "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_OK
    assert "개정 축" in capsys.readouterr().out


def test_baseline_does_not_write_into_old_packet(tmp_path):
    old = make_packet(tmp_path / "old", PROTOCOL_LINES, CONSENT_LINES, version="1.1", date="2026-08-01")
    new = make_packet(tmp_path / "new", PROTOCOL_LINES, CONSENT_LINES)
    before = sorted(os.listdir(str(old)))
    main([str(new), "--baseline", str(old), "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert sorted(os.listdir(str(old))) == before
