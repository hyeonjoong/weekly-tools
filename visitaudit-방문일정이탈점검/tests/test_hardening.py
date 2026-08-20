"""적대적 패널 라운드 1 회귀 테스트 (2026-08-18).

각 테스트의 이름/주석에 HARDENING.md 라운드 1 의 항목 번호(C1~C4, B1~B13, D5)가
붙어 있다. 이 파일의 테스트가 깨지면, 패널이 실제로 재현했던 결함이 되살아난
것이다.
"""

import csv
import json
import os

import pytest

from tests.conftest import (EXAMPLES, PROTOCOL_JSON, crit, d, mini_protocol,
                            rec, run_cli, subj, write_csv)
from visitaudit.consort import build_consort, build_pp
from visitaudit.criteria import recheck
from visitaudit.enroll import build_enrollment
from visitaudit.judge import V_MISSING, V_NA_DROPOUT, V_PENDING, judge
from visitaudit.protocol import PPRules, ProtocolError, load_protocol
from visitaudit.report import guard_cell, render_drafts
from visitaudit.tables import InputError, load_visits_long, load_visits_wide

FAR = d(2026, 12, 31)
ASOF = d(2026, 8, 14)


def _write_proto(tmp_path, obj):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ══ C1 — 프로토콜 오타가 조용히 규칙을 끄면 안 된다 ══════════════════
def test_c1_pp_rule_typo_rejected_with_suggestion(tmp_path):
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["PP제외규칙"] = {"창이탈일수초괴": 7}          # 패널의 재현 그대로 (1글자 오타)
    with pytest.raises(ProtocolError) as e:
        load_protocol(_write_proto(tmp_path, obj))
    msg = str(e.value)
    assert "창이탈일수초괴" in msg                    # 무엇이 틀렸는지
    assert "창이탈일수초과" in msg                    # 가장 비슷한 유효 키 제안


def test_c1_top_level_unknown_key(tmp_path):
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["기준방문일"] = "Baseline"                    # '기준방문' 의 그럴듯한 오타
    with pytest.raises(ProtocolError, match="기준방문일"):
        load_protocol(_write_proto(tmp_path, obj))


def test_c1_visit_entry_unknown_key(tmp_path):
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["방문"][2]["필소"] = True
    with pytest.raises(ProtocolError, match="필소"):
        load_protocol(_write_proto(tmp_path, obj))


def test_c1_criterion_unknown_key(tmp_path):
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["선정기준"][0]["연신"] = ">="
    with pytest.raises(ProtocolError, match="연신"):
        load_protocol(_write_proto(tmp_path, obj))


# ══ C2 — as-of 이후의 탈락일·등록일은 이 기준시점의 사실이 아니다 ════
def test_c2_future_dropout_missing_visit_is_missing_not_na():
    """패널 재현: 탈락일 2026-09-01, as-of 2026-08-14 → 이 시점엔 미탈락."""
    subjects = [subj("S01", dropout="2026-09-01")]
    # 기준 2026-03-02, V1 창 03-27~04-02 는 as-of 에 이미 마감, 기록 없음
    res = judge([rec("S01", "Baseline", "2026-03-02")], subjects, mini_protocol(), ASOF)
    v1 = [s for s in res.slots if s.visit.name == "V1"][0]
    assert v1.verdict == V_MISSING                    # 해당없음이 아니라 결측
    assert any("as-of 이후 탈락일" in n for n in res.notes)


def test_c2_same_dropout_later_asof_is_na():
    """같은 데이터라도 as-of 를 탈락일 뒤로 옮기면 해당없음 — 재현성의 핵심."""
    subjects = [subj("S01", dropout="2026-09-01")]
    res = judge([rec("S01", "Baseline", "2026-08-20")], subjects, mini_protocol(),
                d(2026, 10, 31))
    v1 = [s for s in res.slots if s.visit.name == "V1"][0]
    assert v1.verdict == V_NA_DROPOUT


def test_c2_consort_future_dropout_not_dropped():
    subjects = [subj("S01", arm="A", dropout="2026-09-01", dropout_reason="이상반응")]
    judged = judge([rec("S01", "Baseline", "2026-03-02")], subjects, mini_protocol(), ASOF)
    c = build_consort(subjects, judged, mini_protocol(), ASOF)
    assert c.arm_dropout["A"] == 0                    # 리포트 자기모순 해소
    assert c.arm_dropout_reasons["A"] == []
    assert any("as-of 이후 탈락일" in n for n in c.notes)


def test_c2_pp_future_dropout_not_excluded():
    subjects = [subj("S01", dropout="2026-09-01")]
    proto = mini_protocol(pp=PPRules(missing_required=False, dropout=True,
                                     eligibility_violation=False))
    judged = judge([rec("S01", "Baseline", "2026-08-10"),
                    rec("S01", "V1", "2026-08-12")], subjects, proto, d(2026, 8, 20))
    pp = build_pp(judged, recheck(subjects, proto), subjects, proto, d(2026, 8, 20))
    assert pp.entries["S01"].status == "후보"          # '탈락' 사유가 붙지 않는다
    # 같은 데이터, as-of 를 탈락일 뒤로 → 제외
    pp2 = build_pp(judged, recheck(subjects, proto), subjects, proto, d(2026, 9, 30))
    assert pp2.entries["S01"].reasons == ["탈락"]


def test_c2_future_enroll_separate_bucket():
    subjects = [subj("S01", enroll="2026-03-05"), subj("S02", enroll="2026-09-05")]
    e = build_enrollment(subjects, None, ASOF)
    assert e.n_future_dates == 1
    assert e.n_missing_dates == 0
    # 월별 합 + 미래 + 미기재 = 전체 행 (정확성 감사관의 '사라지는 등록일' 도 해소)
    assert sum(n for _, n in e.monthly) + e.n_future_dates + e.n_missing_dates == e.n_total_rows
    # 목표까지 남은 인원을 셀 때 쓰는 '등록 인원'은 as-of 기준이라 미래분을 뺀다
    assert e.n_enrolled == e.n_total_rows - e.n_future_dates == 1


def test_c2_future_enroll_skips_preenroll_check():
    # 등록일이 as-of 이후면 '등록일 이전 방문' 검사에 쓰지 않는다
    subjects = [subj("S01", enroll="2026-09-01")]
    res = judge([rec("S01", "Baseline", "2026-03-02"),
                 rec("S01", "V1", "2026-03-30")], subjects, mini_protocol(), ASOF)
    assert res.data_errors == []                      # 전 방문이 오류로 뜨면 안 된다


# ══ C3 — 탈락일이 등록일보다 앞서면 모순 데이터 ══════════════════════
def test_c3_dropout_before_enroll_unjudgeable():
    """패널 재현: 등록일 2026-05-01, 탈락일 2026-04-01 → 실제 방문이 조용히
    '해당없음'이 되어 exit 0 이 나오던 결함."""
    subjects = [subj("S01", enroll="2026-05-01", dropout="2026-04-01")]
    res = judge([rec("S01", "Baseline", "2026-05-01")], subjects, mini_protocol(), FAR)
    assert "S01" in res.subject_unjudgeable
    assert "모순" in res.subject_unjudgeable["S01"]
    assert res.count(V_NA_DROPOUT) == 0               # 조용한 해당없음 없음


def test_c3_dropout_before_anchor_when_no_enroll():
    subjects = [subj("S01", dropout="2026-02-01")]    # 등록일 없음 → 기준방문일과 비교
    res = judge([rec("S01", "Baseline", "2026-03-02")], subjects, mini_protocol(), FAR)
    assert "모순" in res.subject_unjudgeable["S01"]


def test_c3_dropout_on_enroll_day_is_fine():
    subjects = [subj("S01", enroll="2026-03-02", dropout="2026-03-02")]
    res = judge([rec("S01", "Baseline", "2026-03-02")], subjects, mini_protocol(), FAR)
    assert "S01" not in res.subject_unjudgeable


# ══ C4 — csv.Error 가 날 트레이스백으로 나가면 안 된다 ═══════════════
def test_c4_giant_field_is_input_error(tmp_path):
    """패널 재현: 한 필드 131,072자 초과 → csv.Error → exit 1 트레이스백이던 결함."""
    big = '"' + "x" * 140_000 + '"'
    p = write_csv(tmp_path, "big.csv", f"피험자ID,방문명,방문일\nS01,V1,{big}\n")
    with pytest.raises(InputError, match="CSV 해석 실패"):
        load_visits_long(p)


def test_c4_cli_giant_field_exit2(tmp_path, protocol_file):
    big = '"' + "y" * 140_000 + '"'
    p = write_csv(tmp_path, "big.csv", f"피험자ID,방문명,방문일\nS01,V1,{big}\n")
    assert run_cli([p, "--protocol", protocol_file, "--no-files"]) == 2


# ══ B1 — PP 문장 초안의 산술 정합 ════════════════════════════════════
def test_b1_pp_draft_includes_unjudgeable():
    proto = mini_protocol(pp=PPRules(missing_required=True, max_days_out=7))
    # S01 후보, S02 판정불가(기준방문 없음), S03 제외(결측)
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30"),
               rec("S03", "Baseline", "2026-03-02")]
    subjects = [subj("S01"), subj("S02"), subj("S03")]
    judged = judge(records, subjects, proto, FAR)
    cr = recheck(subjects, proto)
    c = build_consort(subjects, judged, proto, FAR)
    pp = build_pp(judged, cr, subjects, proto, FAR)
    # 산술: ITT = 후보 + 제외 + 판정불가
    assert c.n_itt == pp.n_candidates + pp.n_excluded + pp.n_unjudgeable == 3
    text = render_drafts(proto, judged, cr, c, pp, ASOF)
    assert "판정불가 1명" in text                      # KR 문장에 포함
    assert "1 unjudgeable participant" in text        # EN 문장에 포함 (단수형)


# ══ B3 — 헤더만 있는 표는 exit 2 ═════════════════════════════════════
def test_b3_header_only_long(tmp_path):
    p = write_csv(tmp_path, "empty.csv", "피험자ID,방문명,방문일\n")
    with pytest.raises(InputError, match="0건"):
        load_visits_long(p)


def test_b3_header_only_wide(tmp_path):
    p = write_csv(tmp_path, "empty.csv", "피험자ID,Baseline,V1\n")
    with pytest.raises(InputError, match="0건"):
        load_visits_wide(p, ["Baseline", "V1"])


def test_b3_cli_header_only_exit2(tmp_path, protocol_file, capsys):
    p = write_csv(tmp_path, "empty.csv", "피험자ID,방문명,방문일\n")
    assert run_cli([p, "--protocol", protocol_file, "--no-files"]) == 2
    # 예전엔 '이상 없음 exit 0' + 거짓 자백("전부 미도래")이었다
    assert "이탈 없음" not in capsys.readouterr().out


# ══ B4 — --min-coverage nan/inf ══════════════════════════════════════
@pytest.mark.parametrize("value", ["nan", "inf"])
def test_b4_nonfinite_min_coverage_exit2(tmp_path, protocol_file, value):
    p = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n")
    assert run_cli([p, "--protocol", protocol_file, "--as-of", "2026-08-14",
                    "--min-coverage", value, "--no-files"]) == 2


def test_b4_negative_inf_rejected(tmp_path, protocol_file):
    # "-inf" 는 argparse 가 옵션으로 오인해 스스로 SystemExit(2) — 프로세스 관점에선 동일한 exit 2
    p = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n")
    with pytest.raises(SystemExit) as e:
        run_cli([p, "--protocol", protocol_file, "--min-coverage", "-inf", "--no-files"])
    assert e.value.code == 2


# ══ B2 — --as-of 는 CLI 에서도 YYYY-MM-DD 만 ═════════════════════════
@pytest.mark.parametrize("value", ["44927", "2026.8.14", "20260814"])
def test_b2_cli_asof_strict(tmp_path, protocol_file, value):
    p = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n")
    assert run_cli([p, "--protocol", protocol_file, "--as-of", value, "--no-files"]) == 2


# ══ B5 — 빈 ID/방문명 행은 세고 자백한다 ═════════════════════════════
def test_b5_blank_rows_counted(tmp_path):
    p = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n,,2026-03-09\n")
    records, _, n_blank, _ = load_visits_long(p)
    assert len(records) == 1 and n_blank == 1


def test_b5_cli_confesses_blank_rows(tmp_path, protocol_file, capsys):
    p = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n,,2026-03-09\n")
    run_cli([p, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    assert "빈 행 1건" in capsys.readouterr().out


# ══ B6 — 등록일 해석 불가는 자백한다 ═════════════════════════════════
def test_b6_enroll_parse_failure_confessed():
    subjects = [subj("S01", enroll="언젠가")]
    res = judge([rec("S01", "Baseline", "2026-03-02")], subjects, mini_protocol(), FAR)
    assert any("등록일 해석 불가" in n for n in res.notes)
    assert "S01" not in res.subject_unjudgeable        # 강등은 아니고 자백만


# ══ B7 — 기준 판정불가 후보 표시 ═════════════════════════════════════
def test_b7_criteria_unjudgeable_candidate_marked():
    proto = mini_protocol(pp=PPRules(), incl=[crit("ISI_baseline", ">=", 15)])
    subjects = [subj("S01", ISI_baseline="")]          # 값 없음 → 기준 판정불가
    judged = judge([rec("S01", "Baseline", "2026-03-02"),
                    rec("S01", "V1", "2026-03-30")], subjects, proto, FAR)
    cr = recheck(subjects, proto)
    pp = build_pp(judged, cr, subjects, proto, FAR)
    assert pp.entries["S01"].status == "후보"
    assert pp.entries["S01"].caveat == "기준판정불가"
    assert pp.n_caveat_candidates == 1


# ══ B8 — 캐시가 재스캔과 같은 결과 ═══════════════════════════════════
def test_b8_slot_cache_matches_filter():
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30"),
               rec("S02", "Baseline", "2026-03-09")]
    res = judge(records, None, mini_protocol(), FAR)
    cache = res.slots_by_subject()
    for sid in ("S01", "S02"):
        assert cache[sid] == [s for s in res.slots if s.subject == sid]


# ══ B9 — 심볼릭 링크로 원본을 파괴하지 않는다 ════════════════════════
def test_b9_symlink_in_outdir_refused(tmp_path, protocol_file):
    victim = tmp_path / "소중한원본.csv"
    victim.write_text("피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n", encoding="utf-8")
    out = tmp_path / "결과"
    out.mkdir()
    os.symlink(str(victim), str(out / "이탈목록.csv"))  # 미리 심어 둔 링크
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\nS01,V1,2026-03-30\n"
                  "S01,V2,2026-04-27\nS01,Screening,2026-02-16\nS01,EOT,2026-05-25\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14",
                    "--out-dir", str(out)])
    assert code == 2
    # 링크 대상(원본)이 1바이트도 안 바뀌었다
    assert victim.read_text(encoding="utf-8").startswith("피험자ID,방문명,방문일")
    assert "S01,Baseline" in victim.read_text(encoding="utf-8")


# ══ B10 — 공유용 md 에 절대경로를 박지 않는다 ════════════════════════
def test_b10_md_uses_protocol_basename(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\nS01,V1,2026-03-30\n"
                  "S01,V2,2026-04-27\nS01,Screening,2026-02-16\nS01,EOT,2026-05-25\n")
    out = tmp_path / "결과"
    run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14",
             "--out-dir", str(out)])
    md = (out / "진행점검.md").read_text(encoding="utf-8")
    assert "프로토콜: 프로토콜.json" in md              # basename 만
    assert str(tmp_path) not in md                     # 절대경로(사용자명 포함) 없음


# ══ B11 — 가드의 숫자 판정은 평범한 숫자만 ═══════════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("-inf", "'-inf"), ("-nan", "'-nan"), ("+inf", "'+inf"),
    ("-1_0", "'-1_0"),                                 # float() 은 통과시키지만 가드해야 함
    ("-1e5", "-1e5"), ("-2", "-2"), ("+7", "+7"),      # 평범한 숫자는 그대로
])
def test_b11_guard_numeric_strictness(raw, expected):
    assert guard_cell(raw) == expected


# ══ B12 — EN 초안에 한국어 어휘를 남기지 않는다 ══════════════════════
def test_b12_en_labels_translated():
    proto = mini_protocol(pp=PPRules(missing_required=True, max_days_out=7))
    records = [rec("S01", "Baseline", "2026-03-02"),                 # V1 결측
               rec("S02", "Baseline", "2026-03-02"), rec("S02", "V1", "2026-04-15"),  # +13일
               rec("S03", "Baseline", "2026-03-02"), rec("S03", "V1", "2026-03-30")]
    subjects = [subj("S01"), subj("S02"),
                subj("S03", dropout="2026-05-01", dropout_reason="이상반응"),
                subj("S04", arm="", screenfail="기준미달"),
                subj("S05", arm="", screenfail="동의철회")]
    judged = judge(records, subjects, proto, FAR)
    cr = recheck(subjects, proto)
    c = build_consort(subjects, judged, proto, FAR)
    pp = build_pp(judged, cr, subjects, proto, FAR)
    text = render_drafts(proto, judged, cr, c, pp, ASOF)
    # EN 문장만 골라서 본다 (KR 문장에는 한국어 어휘가 당연히 있다)
    en_lines = [ln for ln in text.splitlines()
                if ln.startswith(("> Of", "> The candidate", "> As of"))]
    en = "\n".join(en_lines)
    assert "1 did not meet eligibility criteria" in en
    assert "1 consent withdrawal" in en
    assert "missed mandatory visit" in en
    assert "out-of-window >7 days" in en
    assert "1 withdrawal" in en
    # 데이터 유래 라벨(군 이름 'A')은 원문 + 번역 필요 표시
    assert "A [needs translation]" in en
    for korean in ("기준미달", "동의철회", "필수방문결측", "창이탈", "탈락"):
        assert korean not in en                        # EN 문장에 한국어 어휘 없음


# ══ D5 — 미도래 경계(창 종료일 == as-of)의 독립적 2차 방어 ═══════════
def test_d5_pending_boundary_via_cli(tmp_path, protocol_file, capsys):
    """단위 테스트와 별도로 CLI 경로에서도 창 종료일 == as-of → 미도래를 고정.
    judge 의 `>=` 가 `>` 로 뮤테이션되면 이 테스트가 결측(exit 1)으로 잡아낸다."""
    # 기준 2026-06-15 → V1 예정 07-13, 창 07-10~07-16. as-of = 07-16 (창 종료일 당일).
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "S01,Screening,2026-06-01\nS01,Baseline,2026-06-15\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-07-16", "--no-files"])
    out = capsys.readouterr().out
    assert code == 0                                   # 이탈 0건이어야 한다
    assert "미도래(창 미마감): 3건" in out              # V1·V2·EOT 전부 미도래
    assert "결측" not in out.split("[이탈]")[1].split("[")[0]
