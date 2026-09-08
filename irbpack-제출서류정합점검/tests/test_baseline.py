"""--baseline 개정 축 — 프로토콜에서 바뀐 값만, 부속문서 미반영은 치명."""
from irbpack.model import CRITICAL
from tests.conftest import by_sev, md_crf, md_icf, md_protocol, run, write_packet


def _v11(tmp_path):
    return write_packet(tmp_path, {"연구계획서_v1.1.md": md_protocol(visits="2", ver="1.1", date="2026-08-11"),
                                   "ICF_v1.1.md": md_icf(ver="1.1", date="2026-08-11"), "CRF_v1.1.md": md_crf(ver="1.1", date="2026-08-11")}, sub="v11")


def test_stale_attachments_are_critical(tmp_path):
    base = _v11(tmp_path)
    cur = write_packet(tmp_path, {"연구계획서_v1.2.md": md_protocol(visits="3"), "ICF_v1.2.md": md_icf(), "CRF_v1.2.md": md_crf()}, sub="v12")
    res, fatal = run(cur, baseline=base)
    assert fatal == []
    stale = [i for i in by_sev(res, CRITICAL) if "개정 미반영" in i.title]
    assert {i.evidence[1].label for i in stale} == {"동의서(성인)", "CRF"}
    assert "방문횟수" in stale[0].title
    assert "바뀐 항목" in res.coverage.baseline_note and "옛 값이 남은 부속문서 2건" in res.coverage.baseline_note


def test_unchanged_items_are_not_examined(tmp_path):
    base = _v11(tmp_path)
    cur = write_packet(tmp_path, {"연구계획서_v1.2.md": md_protocol(visits="3"), "ICF_v1.2.md": md_icf(visits="총 3회 방문"),
                                  "CRF_v1.2.md": md_crf(visits=3)}, sub="v12")
    res, _ = run(cur, baseline=base)
    assert [i for i in res.issues if "개정 미반영" in i.title] == []
    assert "방문횟수" in res.coverage.baseline_note


def test_attachment_with_new_value_is_fine(tmp_path):
    base = _v11(tmp_path)
    cur = write_packet(tmp_path, {"연구계획서_v1.2.md": md_protocol(comp="회당 3만원 지급"), "ICF_v1.2.md": md_icf(comp="회당 3만원"),
                                  "CRF_v1.2.md": md_crf()}, sub="v12")
    res, _ = run(cur, baseline=base)
    assert [i for i in res.issues if "개정 미반영" in i.title] == []
    assert by_sev(res, CRITICAL) == []


def test_baseline_without_protocol_is_confessed(tmp_path):
    base = write_packet(tmp_path, {"ICF_v1.1.md": md_icf(ver="1.1"), "CRF_v1.1.md": md_crf(ver="1.1")}, sub="v11")
    cur = write_packet(tmp_path, {"연구계획서_v1.2.md": md_protocol(), "ICF_v1.2.md": md_icf()}, sub="v12")
    res, fatal = run(cur, baseline=base)
    assert fatal == [] and "프로토콜 없음" in res.coverage.baseline_note


def test_baseline_only_changed_items_listed(tmp_path):
    base = _v11(tmp_path)
    cur = write_packet(tmp_path, {"연구계획서_v1.2.md": md_protocol(keep="5년"), "ICF_v1.2.md": md_icf(keep="5년"), "CRF_v1.2.md": md_crf()}, sub="v12")
    res, _ = run(cur, baseline=base)
    note = res.coverage.baseline_note
    assert "보관기간" in note and "방문" not in note.split("(")[1].split(")")[0]


def test_stale_defect_reported_once_not_twice(tmp_path):
    """같은 결함(동의서가 옛 방문횟수)이 '값 충돌'과 '개정 미반영'으로 두 번 세이지 않는다."""
    base = _v11(tmp_path)
    cur = write_packet(tmp_path, {"연구계획서_v1.2.md": md_protocol(visits="3"), "ICF_v1.2.md": md_icf(), "CRF_v1.2.md": md_crf()}, sub="v12")
    res, _ = run(cur, baseline=base)
    visits = [i for i in by_sev(res, CRITICAL) if i.item == "visits"]
    assert all("개정 미반영" in i.title for i in visits) and len(visits) == 2


def test_plain_conflict_kept_when_a_third_doc_differs_for_another_reason(tmp_path):
    base = _v11(tmp_path)
    cur = write_packet(tmp_path, {"연구계획서_v1.2.md": md_protocol(visits="3"), "ICF_v1.2.md": md_icf(),
                                  "CRF_v1.2.md": md_crf(visits=5)}, sub="v12")
    res, _ = run(cur, baseline=base)
    visits = [i for i in by_sev(res, CRITICAL) if i.item == "visits"]
    assert any("개정 미반영" not in i.title for i in visits)  # CRF 5회는 옛 값도 새 값도 아니라 일반 충돌로 남는다
