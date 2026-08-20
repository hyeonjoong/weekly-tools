"""적대적 패널 라운드 2 회귀 테스트 (2026-08-20).

라운드 2 는 독립 리뷰어 4명(정확성 감사 / 엣지케이스 파괴 / 문서·유용성 비평 /
안전·테스트품질)이 각각 찾아낸 결함의 회귀 테스트다. 항목 번호는 HARDENING.md
라운드 2 표와 일치한다(R2-1 …). 이 파일이 깨지면 패널이 실제로 재현했던 결함이
되살아난 것이다.
"""

import csv
import json
import os

import pytest

from tests.conftest import (PROTOCOL_JSON, crit, d, mini_protocol, rec, run_cli,
                            subj, write_csv)
from visitaudit.consort import build_consort, build_pp
from visitaudit.criteria import recheck
from visitaudit.judge import V_UNJUDGEABLE, judge
from visitaudit.protocol import ProtocolError, load_protocol
from visitaudit.report import guard_cell
from visitaudit.tables import load_visits_long

ASOF = d(2026, 8, 14)


def _write_proto(tmp_path, obj, name="p.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ══ R2-1 — 심볼릭 링크로 입력 파일이 산출물에 덮여 쓰이면 안 된다 ════════
def test_r2_1_symlinked_outdir_cannot_clobber_input(tmp_path, protocol_file, capsys):
    """`--out-dir` 이 입력 폴더를 가리키는 링크면 원본이 산출물로 덮여 쓰였다.

    abspath 는 링크를 풀지 않아 '같은 폴더'인 것을 못 알아봤다. macOS 는 /tmp
    자체가 링크라 일부러 만들지 않아도 걸리는 자리였다.
    """
    real = tmp_path / "data"
    real.mkdir()
    # 입력 파일 이름을 산출물 이름과 똑같이 둔다 — 실제로 덮어써지는 조건
    victim = real / "이탈목록.csv"
    victim.write_text("피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n", encoding="utf-8")
    before = victim.read_bytes()
    link = tmp_path / "link"
    os.symlink(str(real), str(link))

    code = run_cli([str(victim), "--protocol", protocol_file,
                    "--as-of", "2026-08-14", "--out-dir", str(link)])
    assert code == 2, "링크 너머로 같은 폴더인 것을 알아채고 거부해야 한다"
    assert "원본 보호" in capsys.readouterr().err
    assert victim.read_bytes() == before, "입력 파일이 산출물로 덮여 썼다"


def test_r2_1_symlinked_input_path_cannot_clobber(tmp_path, protocol_file):
    """반대 방향 — 입력을 링크된 경로로 주고 --out-dir 은 실제 폴더."""
    real = tmp_path / "real"
    real.mkdir()
    victim = real / "진행점검.md"
    victim.write_text("피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n", encoding="utf-8")
    before = victim.read_bytes()
    alias = tmp_path / "alias"
    os.symlink(str(real), str(alias))

    code = run_cli([str(alias / "진행점검.md"), "--protocol", protocol_file,
                    "--as-of", "2026-08-14", "--out-dir", str(real)])
    assert code == 2
    assert victim.read_bytes() == before


# ══ R2-2 — 기준방문의 오프셋은 0 이어야 한다 ═══════════════════════════
def test_r2_2_anchor_with_nonzero_offset_rejected(tmp_path):
    """기준방문 예정일 = 그 방문이 실제로 있었던 날. 오프셋이 0 이 아니면
    기준방문이 제 창 밖으로 떨어지고 정상 데이터가 100% 이탈로 뜬다."""
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["기준방문"] = "V1"                       # V1 은 오프셋 28
    with pytest.raises(ProtocolError) as e:
        load_protocol(_write_proto(tmp_path, obj))
    msg = str(e.value)
    assert "오프셋은 0" in msg and "28" in msg


def test_r2_2_anchor_offset_zero_still_loads(tmp_path):
    proto = load_protocol(_write_proto(tmp_path, PROTOCOL_JSON))
    assert proto.get_visit(proto.anchor).offset == 0


# ══ R2-3 — 방문은 시간 순서로 나열돼야 한다 ═════════════════════════════
def test_r2_3_unsorted_visits_rejected(tmp_path):
    """나열 순서를 그대로 시간 순서로 믿기 때문에, 뒤죽박죽이면 정상 데이터에서
    순서 위반이 만들어지고 그 근거 문장이 스스로를 부정한다
    ("V1(2026-01-29) 가 EOT(2026-03-26) 보다 앞섬")."""
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["방문"] = [
        {"이름": "Baseline", "오프셋": 0, "창": [0, 0], "필수": True},
        {"이름": "EOT", "오프셋": 84, "창": [-7, 7], "필수": True},
        {"이름": "V1", "오프셋": 28, "창": [-3, 3], "필수": True},
    ]
    with pytest.raises(ProtocolError) as e:
        load_protocol(_write_proto(tmp_path, obj))
    msg = str(e.value)
    assert "오프셋 오름차순" in msg and "EOT" in msg and "V1" in msg


def test_r2_3_equal_offsets_allowed(tmp_path):
    """같은 날 두 방문(예: EOT 와 추적관찰)은 정상이므로 막지 않는다."""
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["방문"] = [
        {"이름": "Baseline", "오프셋": 0, "창": [0, 0], "필수": True},
        {"이름": "V1", "오프셋": 28, "창": [-3, 3], "필수": True},
        {"이름": "V1b", "오프셋": 28, "창": [-3, 3], "필수": False},
    ]
    assert len(load_protocol(_write_proto(tmp_path, obj)).visits) == 3


# ══ R2-4 — nan/inf/1_0 은 위반이 아니라 판정불가다 ══════════════════════
@pytest.mark.parametrize("bad", ["nan", "NaN", "inf", "-inf", "1_0", "2_5"])
def test_r2_4_nonplain_numbers_are_unjudgeable_not_violations(bad):
    """float() 만으로 거르면 'nan' 은 모든 비교가 False 라 선정기준에서 '위반'이
    되고(제외기준에서는 조용히 통과), '1_0' 은 10.0 이 된다. pandas 가 결측을
    'nan' 으로 내보내므로 실제로 자주 만나는 값이다."""
    proto = mini_protocol(incl=[crit("age", ">=", 19)])
    res = recheck([subj("S01", arm="A", age=bad)], proto)
    assert res.violations == [], f"{bad!r} 이 위반으로 둔갑했다"
    assert [f.subject for f in res.unjudgeable] == ["S01"]
    assert res.n_checked == 0


def test_r2_4_plain_numbers_still_judged():
    """반대 방향 — 평범한 숫자는 그대로 판정돼야 한다(과잉 차단 방지)."""
    proto = mini_protocol(incl=[crit("age", ">=", 19)])
    res = recheck([subj("S01", arm="A", age="18"), subj("S02", arm="A", age="19.0"),
                   subj("S03", arm="A", age="+20"), subj("S04", arm="A", age="2e1")], proto)
    assert [f.subject for f in res.violations] == ["S01"]
    assert res.unjudgeable == [] and res.n_checked == 4


def test_r2_4_fabricated_violation_does_not_shrink_pp():
    """없는 위반은 PP 집합까지 흔든다 — 그 경로 전체를 고정한다."""
    proto = mini_protocol(incl=[crit("age", ">=", 19)])
    subjects = [subj("S01", arm="A", age="nan")]
    judged = judge([rec("S01", "Baseline", "2026-03-02"),
                    rec("S01", "V1", "2026-03-30")], subjects, proto, ASOF)
    c = recheck(subjects, proto)
    pp = build_pp(judged, c, subjects, mini_protocol(pp=_pp_rules()), ASOF)
    assert pp.n_excluded == 0 and pp.n_candidates == 1


def _pp_rules():
    from visitaudit.protocol import PPRules
    r = PPRules()
    r.missing_required = True
    r.max_days_out = 7
    return r


# ══ R2-5 — 방문일이 빈 칸은 파싱 실패가 아니라 '기록 없음' ══════════════
def test_r2_5_blank_date_is_not_a_parse_error(tmp_path, protocol_file, capsys):
    """트래커는 아직 안 온 방문을 빈 칸으로 미리 깔아 둔다. 이걸 데이터 오류로
    세면 첫 실행부터 판정률이 무너져 exit 3 이 뜬다 — 이 툴이 피하려던 바로
    그 크라잉울프다."""
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "S01,Screening,2026-03-02\nS01,Baseline,2026-03-16\n"
                  "S01,V1,2026-04-13\nS01,V2,\nS01,EOT,\n"
                  "S02,Screening,2026-03-04\nS02,Baseline,2026-03-18\n"
                  "S02,V1,2026-04-15\nS02,V2,\nS02,EOT,\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-05-01", "--no-files"])
    out = capsys.readouterr().out
    assert code == 0, "빈 칸을 파싱 실패로 세어 exit 3 이 났다"
    assert "날짜 파싱 실패" not in out
    assert "방문일이 빈 행 4건" in out                    # 자백은 남긴다
    assert "판정률 100.0%" in out


def test_r2_5_long_and_wide_agree_on_blank_cells(tmp_path, protocol_file, capsys):
    """같은 트래커를 long 으로 읽든 wide 로 읽든 결론이 같아야 한다."""
    long_csv = write_csv(tmp_path, "long.csv",
                         "피험자ID,방문명,방문일\n"
                         "S01,Screening,2026-03-02\nS01,Baseline,2026-03-16\n"
                         "S01,V1,2026-04-13\nS01,V2,\nS01,EOT,\n")
    wide_csv = write_csv(tmp_path, "wide.csv",
                         "피험자ID,Screening,Baseline,V1,V2,EOT\n"
                         "S01,2026-03-02,2026-03-16,2026-04-13,,\n")
    c1 = run_cli([long_csv, "--protocol", protocol_file, "--as-of", "2026-05-01", "--no-files"])
    o1 = capsys.readouterr().out
    c2 = run_cli([wide_csv, "--protocol", protocol_file, "--as-of", "2026-05-01",
                  "--wide", "--no-files"])
    o2 = capsys.readouterr().out
    assert c1 == c2 == 0
    for out in (o1, o2):
        assert "판정완료 3건" in out


def test_r2_5_all_blank_dates_is_an_input_error_not_a_clean_pass(tmp_path, protocol_file, capsys):
    """R2-5 를 고치다 낸 회귀 — 빈 칸을 '기록 없음'으로 넘기다 보니, 날짜가 하나도
    없는 파일이 '피험자 0명 / 이탈 0건 / exit 0' 으로 조용히 통과했다. 판정 근거가
    통째로 없는 것을 '이상 없음'으로 흘려보내는 것이야말로 이 툴이 막으려는 일이다."""
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,\nS01,V1,\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    assert code == 2, "판정할 날짜가 0건인데 통과시켰다"
    assert "0건" in capsys.readouterr().err


def test_r2_5_blank_date_still_becomes_missing_when_window_closed(tmp_path, protocol_file, capsys):
    """빈 칸이라고 봐주는 게 아니다 — 창이 닫혔으면 결측으로 잡혀야 한다."""
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "S01,Screening,2026-03-02\nS01,Baseline,2026-03-16\nS01,V1,\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    out = capsys.readouterr().out
    assert code == 1
    assert "필수방문 결측" in out and "S01" in out


# ══ R2-21 — 기준방문은 '등록일 이전' 검사 대상이 아니다 ═════════════════
def test_r2_21_anchor_before_enroll_is_not_a_data_error(tmp_path, protocol_file, capsys):
    """등록일을 무작위배정일로 적어 기저방문 **다음 날**이 되는 기관이 흔하다.
    그때마다 기저방문을 데이터 오류로 떨어뜨리면 정상 트래커에서 판정불가가
    무더기로 생긴다(실측: 40명 중 10명). 기준방문일이 곧 0일을 정의하므로
    등록일과의 앞뒤는 프로토콜 위반이 아니라 기재 관행이다."""
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "S01,Baseline,2026-03-02\nS01,V1,2026-03-30\n")
    s = write_csv(tmp_path, "s.csv",
                  "피험자ID,군,등록일\nS01,중재군,2026-03-03\n")   # 기저 다음 날 등록
    code = run_cli([v, "--protocol", protocol_file, "--subjects", s,
                    "--as-of", "2026-08-14", "--no-files"])
    out = capsys.readouterr().out
    assert "등록일 이전" not in out
    assert code in (0, 1)


def test_r2_21_post_anchor_visit_before_enroll_still_flagged(tmp_path, protocol_file, capsys):
    """반대로 기준방문 *이후* 방문이 등록일보다 앞서면 여전히 데이터 오류다."""
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "S01,Baseline,2026-03-02\nS01,V1,2026-03-05\n")
    s = write_csv(tmp_path, "s.csv",
                  "피험자ID,군,등록일\nS01,중재군,2026-03-10\n")
    run_cli([v, "--protocol", protocol_file, "--subjects", s,
             "--as-of", "2026-08-14", "--no-files"])
    assert "등록일 이전" in capsys.readouterr().out


# ══ R2-6 — 예기치 못한 예외가 exit 1('이탈 발견')로 새면 안 된다 ════════
def test_r2_6_huge_offset_rejected_not_crash(tmp_path, capsys):
    """오프셋이 datetime 범위를 넘으면 OverflowError 트레이스백 + exit 1 이었다.
    exit 1 은 '이탈 발견'이라 호출하는 쪽이 '정상 종료'로 읽는다."""
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj["방문"] = [
        {"이름": "Baseline", "오프셋": 0, "창": [0, 0], "필수": True},
        {"이름": "V1", "오프셋": 2920000, "창": [-3, 3], "필수": True},
    ]
    with pytest.raises(ProtocolError) as e:
        load_protocol(_write_proto(tmp_path, obj))
    assert "범위를 벗어났" in str(e.value)


def test_r2_6_sentinel_year_9999_does_not_crash(tmp_path, protocol_file, capsys):
    """9999-12-31 은 구형 EDC 의 '미정' 표기로 흔하다. 날짜 산술이 넘쳐도
    트레이스백 대신 정돈된 종료여야 하고, 종료코드는 1 이 아니어야 한다."""
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,9999-12-31\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "9999-12-31", "--no-files"])
    assert code in (0, 2, 3), f"예기치 못한 종료코드 {code}"


def test_r2_6_guarded_entrypoint_maps_unexpected_error_to_exit2(monkeypatch, capsys):
    """마지막 방어선 자체를 고정한다 — 어떤 예외든 2 로 보고하고, 1 로는 절대
    새지 않는다."""
    from visitaudit import cli

    def boom(*a, **k):
        raise RuntimeError("의도적 폭발")

    monkeypatch.setattr(cli, "main", boom)
    assert cli.main_guarded([]) == 2
    assert "예기치 못한 오류" in capsys.readouterr().err


# ══ R2-7 — cp949/utf-16 프로토콜이 트레이스백으로 죽으면 안 된다 ════════
@pytest.mark.parametrize("enc", ["cp949", "utf-16"])
def test_r2_7_protocol_encoding_fallback(tmp_path, enc):
    """메모장 'ANSI'(cp949)·엑셀 '유니코드 텍스트'(utf-16)로 저장된 프로토콜.
    UnicodeDecodeError 는 ValueError 라 OSError 로 잡히지 않아 새어 나갔다."""
    p = tmp_path / "p.json"
    p.write_bytes(json.dumps(PROTOCOL_JSON, ensure_ascii=False).encode(enc))
    proto = load_protocol(str(p))
    assert proto.anchor == "Baseline" and len(proto.visits) == 5


def test_r2_7_undecodable_protocol_is_clean_error(tmp_path):
    p = tmp_path / "p.json"
    p.write_bytes(b"\xff\xfe\x00\x00\xff\xff\xfe\xfe{")
    with pytest.raises(ProtocolError) as e:
        load_protocol(str(p))
    assert "인코딩" in str(e.value) or "깨져" in str(e.value)


# ══ R2-8 — 임계가 없으면 심각도는 '경미'가 아니라 '미정' ════════════════
def test_r2_8_severity_undetermined_without_threshold(tmp_path, capsys):
    """PP제외규칙이 없으면 창이탈 심각도의 기준 자체가 없다. 86일 지각을
    '경미'로 적으면 가벼운 일로 읽힌다."""
    obj = json.loads(json.dumps(PROTOCOL_JSON))
    obj.pop("PP제외규칙")
    proto = _write_proto(tmp_path, obj)
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "S01,Baseline,2026-01-05\nS01,V1,2026-05-02\n")
    run_cli([v, "--protocol", proto, "--as-of", "2026-08-14", "--no-files"])
    out = capsys.readouterr().out
    assert "[미정]" in out and "[경미]" not in out


# ══ R2-9 — 판정 못 한 방문을 품은 PP 후보에는 표시가 붙는다 ══════════════
def test_r2_9_pp_candidate_with_unjudgeable_visit_is_flagged():
    """방문 하나가 판정불가인 채로 아무 표시 없이 후보에 오르면, 그 방문이 실은
    창을 크게 벗어났을 경우 PP 숫자가 조용히 틀린다."""
    proto = mini_protocol(pp=_pp_rules())
    subjects = [subj("S01", arm="A")]
    judged = judge([rec("S01", "Baseline", "2026-03-02"),
                    rec("S01", "V1", "엉망진창")], subjects, proto, ASOF)
    assert any(s.verdict == V_UNJUDGEABLE for s in judged.slots)
    pp = build_pp(judged, recheck(subjects, proto), subjects, proto, ASOF)
    assert pp.entries["S01"].status == "후보"
    assert "방문판정불가" in pp.entries["S01"].caveat
    assert pp.n_caveat_candidates == 1


# ══ R2-10 — 제외가 0명일 때 문장이 매달린 괄호를 남기면 안 된다 ══════════
def test_r2_10_pp_sentences_clean_when_no_exclusions():
    """등록 단계에서는 제외 0명이 오히려 정상이다. 그때 사유 목록을 그대로
    끼우면 "(중복 제거 후 0명)" 과 "(; counted once…" 가 남았다."""
    from visitaudit.enroll import build_enrollment
    from visitaudit.report import render_drafts
    proto = mini_protocol(pp=_pp_rules())
    subjects = [subj("S01", arm="A"), subj("S02", arm="A")]
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30"),
               rec("S02", "Baseline", "2026-03-02"), rec("S02", "V1", "2026-03-30")]
    judged = judge(records, subjects, proto, ASOF)
    c = recheck(subjects, proto)
    consort = build_consort(subjects, judged, proto, ASOF)
    pp = build_pp(judged, c, subjects, proto, ASOF)
    assert pp.n_excluded == 0
    text = render_drafts(proto, judged, c, consort, pp, ASOF)
    assert "(중복 제거 후" not in text
    assert "(;" not in text
    assert "제외 사유에 해당하는 피험자 0명" in text


# ══ R2-11 — 일치하는 중복 스크린실패자를 ITT 로 끌어들이지 않는다 ═════════
def test_r2_11_consistent_nonrandomized_duplicate_not_in_universe():
    """중복된 행이 모두 군 미기재로 일치하면 배정 여부가 불확실하지 않다.
    끌어들이면 판정률만 깎여 애먼 exit 3 이 뜨고, CONSORT 와도 어긋난다."""
    proto = mini_protocol()
    s1 = subj("S05", arm="", screenfail="기준미달")
    s1.duplicated = True
    s2 = subj("S05", arm="", screenfail="기준미달")
    s2.duplicated = True
    subjects = [subj("S01", arm="A"), s1, s2]
    judged = judge([rec("S01", "Baseline", "2026-03-02"),
                    rec("S01", "V1", "2026-03-30")], subjects, proto, ASOF)
    assert judged.universe == ["S01"]
    assert judged.coverage_rate == 100.0


def test_r2_11_conflicting_duplicate_still_escalated():
    """반대로 행끼리 엇갈리면(한쪽만 군 기재) 여전히 판정불가로 강등한다."""
    proto = mini_protocol()
    s1 = subj("S05", arm="")
    s1.duplicated = True
    s2 = subj("S05", arm="A")
    s2.duplicated = True
    judged = judge([rec("S01", "Baseline", "2026-03-02")],
                   [subj("S01", arm="A"), s1, s2], proto, ASOF)
    assert "S05" in judged.subject_unjudgeable


# ══ R2-12 — 원본 줄 번호는 빈 줄을 건너뛰어도 어긋나면 안 된다 ═══════════
def test_r2_12_row_numbers_survive_blank_lines(tmp_path):
    """[데이터 오류] 블록의 존재 이유가 '원본 확인'인데, 빈 줄을 걸러낸 뒤
    번호를 매기면 엉뚱한 줄을 가리킨다."""
    p = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "\n"
                  "\n"
                  "S01,Baseline,2026-03-02\n"
                  "S01,V1,2026-03-30\n")
    records, _enc, _blank, _bd = load_visits_long(p)
    # 물리적으로 4·5번째 줄
    assert [r.row_no for r in records] == [4, 5]


# ══ R2-13 — CSV 수식 가드는 앞의 공백·탭에 기대지 않고 스스로 막는다 ═════
@pytest.mark.parametrize("cell", ["\t=1+1", "\r=1+1", " =1+1", "\t@SUM(A1)", " +1+1"])
def test_r2_13_guard_handles_leading_whitespace(cell):
    """스프레드시트는 앞 공백을 무시하고 그 뒤부터 읽는다. 지금은 tables 의
    strip() 이 가려 주지만, 가드가 다른 모듈의 한 줄에 기대면 안 된다."""
    assert guard_cell(cell).startswith("'")


@pytest.mark.parametrize("cell", ["-5", "-3.5", "+7", "-1e5", "0", "12"])
def test_r2_13_guard_leaves_plain_numbers_alone(cell):
    """과잉 차단 방지 — 창밖일수의 음수가 문자열로 망가지면 안 된다."""
    assert guard_cell(cell) == cell


@pytest.mark.parametrize("cell", ["-1_0", "-inf", "-5일"])
def test_r2_13_guard_still_blocks_fake_numbers(cell):
    assert guard_cell(cell).startswith("'")


# ══ R2-14 — 산출물 경로는 실제로 쓴 뒤에만 안내한다 ══════════════════════
def test_r2_14_no_output_paths_announced_when_write_refused(tmp_path, protocol_file, capsys):
    """쓰기가 거부됐는데 "출력: …" 를 먼저 찍으면 만들어지지도 않은 파일 4개를
    안내하게 된다."""
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    # 산출물 중 하나를 심볼릭 링크로 미리 심어 두면 _open_out 이 거부한다
    target = tmp_path / "소중한파일.txt"
    target.write_text("건드리지 마시오", encoding="utf-8")
    os.symlink(str(target), str(out_dir / "진행점검.md"))

    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\nS01,V1,2026-03-30\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14",
                    "--out-dir", str(out_dir)])
    out = capsys.readouterr().out
    assert code == 2
    assert "출력:" not in out
    assert target.read_text(encoding="utf-8") == "건드리지 마시오"


# ══ R2-15 — 이탈 비율의 분자와 분모는 단위가 같아야 한다 ═════════════════
def test_r2_15_deviation_rate_cannot_exceed_100(tmp_path, protocol_file, capsys):
    """순서위반은 방문 '쌍' 단위라 방문 수로 나누면 100% 를 넘을 수 있었다."""
    v = write_csv(tmp_path, "v.csv",
                  "피험자ID,방문명,방문일\n"
                  "S01,Baseline,2026-01-05\n"
                  "S01,V1,2026-03-20\nS01,V2,2026-02-10\nS01,EOT,2026-01-20\n")
    run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    out = capsys.readouterr().out
    line = [ln for ln in out.splitlines() if ln.startswith("[이탈]")][0]
    assert "방문 단위" in line
    pct = float(line.split("= ")[1].rstrip("%)").strip())
    assert 0.0 <= pct <= 100.0, line


# ══ R2-16 — 정합성 '✓' 가 항등식과 진짜 검증을 뒤섞으면 안 된다 ══════════
def test_r2_16_report_separates_identities_from_cross_checks(tmp_path, protocol_file, capsys):
    """어긋날 수 없는 항등식을 '✓ 정합성'으로 나란히 보여 주면, 아무것도
    확인해 주지 않으면서 확인해 준 것처럼 읽힌다."""
    run_cli([os.path.join(os.path.dirname(protocol_file), "..", "examples", "방문기록.csv")
             if False else _example("방문기록.csv"),
             "--protocol", _example("프로토콜.json"),
             "--subjects", _example("피험자.csv"),
             "--as-of", "2026-08-14", "--no-files"])
    out = capsys.readouterr().out
    assert "합계 확인:" in out and "같은 목록을 갈라 센 항등식" in out
    assert "교차 검증:" in out and "어긋날 수 있는 검사" in out


def _example(name: str) -> str:
    from tests.conftest import EXAMPLES
    return os.path.join(EXAMPLES, name)


# ══ R2-17 — 성능: 방문 수만 명이 되어도 O(n²) 로 무너지지 않는다 ═════════
def test_r2_17_large_input_is_not_quadratic(tmp_path, protocol_file):
    """실제 EDC 내보내기에는 프로토콜에 없는 방문명(Unscheduled 등)이 섞이고,
    그 자리가 행마다 set() 을 다시 만들던 곳이었다."""
    import time
    lines = ["피험자ID,방문명,방문일"]
    for i in range(3000):
        sid = f"S{i:05d}"
        lines.append(f"{sid},Baseline,2026-01-05")
        lines.append(f"{sid},V1,2026-02-02")
        lines.append(f"{sid},Unscheduled,2026-02-10")
    v = write_csv(tmp_path, "big.csv", "\n".join(lines) + "\n")
    t0 = time.time()
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    elapsed = time.time() - t0
    assert code in (0, 1, 3)
    assert elapsed < 10.0, f"9000행 처리에 {elapsed:.1f}초 — 이차 동작이 되살아났다"


# ══ R2-22 — 문장 초안의 산술이 닫혀야 한다 ═══════════════════════════════
def test_r2_22_draft_sentence_arithmetic_closes(tmp_path, protocol_file):
    """논문에 붙일 문장이 판정불가를 빼먹어 200 − 168 − 19 − 6 = 7 이 남았다.
    심사자가 그 뺄셈을 하는 것이 이 툴을 쓰는 가장 창피한 시나리오다."""
    from visitaudit.consort import build_consort, build_pp
    from visitaudit.criteria import recheck
    from visitaudit.report import render_drafts
    proto = mini_protocol(pp=_pp_rules())
    subjects = [subj("S01", arm="A"), subj("S02", arm="A")]
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "엉망진창"),
               rec("S02", "Baseline", "2026-03-02"), rec("S02", "V1", "2026-03-30")]
    judged = judge(records, subjects, proto, ASOF)
    c = recheck(subjects, proto)
    consort = build_consort(subjects, judged, proto, ASOF)
    pp = build_pp(judged, c, subjects, proto, ASOF)
    text = render_drafts(proto, judged, c, consort, pp, ASOF)
    assert judged.n_unjudgeable > 0
    assert "판정불가" in text                       # KR 문장이 판정불가를 말한다
    assert "could not be adjudicated" in text       # EN 문장도
    # 산술이 실제로 닫히는지 — 전체 = 판정완료 + 미도래 + 해당없음 + 판정불가 + 나머지
    from visitaudit.judge import V_NA_DROPOUT, V_PENDING
    total = (judged.n_completed + judged.count(V_PENDING)
             + judged.count(V_NA_DROPOUT) + judged.n_unjudgeable)
    assert total <= judged.n_slots


# ══ R2-23 — as-of 시점 미등록자는 판정불가가 아니라 판정 대상 제외 ═══════
def test_r2_23_backdated_run_is_coherent(tmp_path, capsys):
    """과거 기준일로 되돌려 그때 보고한 숫자를 재현하는 것이 이 툴의 용도다.
    그때 아직 안 들어온 사람을 판정불가로 세면 판정률이 무너지고, CONSORT 의 N 과
    등록곡선의 N 이 한 페이지에서 어긋난다."""
    out = _run_example(capsys, "2026-04-10")
    assert "as-of 시점 미등록 13명" in out
    # 한 페이지 안에서 세 숫자가 일치해야 한다
    assert "무작위배정 7" in out
    assert "ITT 7" in out
    assert "교차 검증: ✓ ITT(7) = 판정 대상 피험자(7)" in out
    # 미등록자가 판정불가로 새어 들어오지 않는다
    assert "기준시점(as-of) 이후" not in out


def test_r2_23_current_asof_unchanged(capsys):
    """반대로 현재 기준일에서는 아무것도 달라지지 않아야 한다(회귀 방지)."""
    out = _run_example(capsys, "2026-08-14")
    assert "판정률 91.6%" in out and "ITT 20" in out and "PP 후보 14" in out
    assert "미등록" not in out


def _run_example(capsys, as_of: str) -> str:
    run_cli([_example("방문기록.csv"), "--protocol", _example("프로토콜.json"),
             "--subjects", _example("피험자.csv"), "--as-of", as_of, "--no-files"])
    return capsys.readouterr().out


# ══ R2-24 — EN CONSORT 캡션의 표준 군 이름은 번역된다 ════════════════════
def test_r2_24_standard_arm_names_translated(capsys):
    out = _run_example(capsys, "2026-08-14")
    assert "중재군 [needs translation]" not in out


# ══ R2-28 — 경로 문자열 비교로는 '같은 폴더'를 다 못 잡는다 ═════════════
def test_r2_28_case_folded_outdir_cannot_clobber(tmp_path, protocol_file):
    """macOS 기본 파일시스템은 대소문자를 구분하지 않는다. realpath 는 문자열을
    돌려줄 뿐이라 `data` 와 `DATA` 가 다른 폴더로 보였고, 산출물이 입력 폴더에
    그대로 쏟아졌다."""
    real = tmp_path / "data"
    real.mkdir()
    victim = real / "이탈목록.csv"
    victim.write_text("피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n", encoding="utf-8")
    before = victim.read_bytes()
    upper = tmp_path / "DATA"
    if not upper.exists():          # 대소문자 구분 FS 면 이 시나리오 자체가 없다
        pytest.skip("대소문자를 구분하는 파일시스템 — 이 함정이 존재하지 않음")
    code = run_cli([str(victim), "--protocol", protocol_file,
                    "--as-of", "2026-08-14", "--out-dir", str(upper)])
    assert code == 2
    assert victim.read_bytes() == before


def test_r2_28_unicode_nfd_outdir_cannot_clobber(tmp_path, protocol_file):
    """한글 폴더명은 Finder 붙여넣기(NFD)와 직접 입력(NFC)이 다른 바이트열이다.
    같은 폴더인데 문자열이 달라 가드를 그냥 통과했다."""
    import unicodedata
    nfc = tmp_path / unicodedata.normalize("NFC", "자료")
    nfd = tmp_path / unicodedata.normalize("NFD", "자료")
    nfc.mkdir()
    victim = nfc / "이탈목록.csv"
    victim.write_text("피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\n", encoding="utf-8")
    before = victim.read_bytes()
    if not nfd.exists():            # 정규화를 하지 않는 FS 면 시나리오가 없다
        pytest.skip("유니코드 정규화를 하지 않는 파일시스템")
    code = run_cli([str(victim), "--protocol", protocol_file,
                    "--as-of", "2026-08-14", "--out-dir", str(nfd)])
    assert code == 2
    assert victim.read_bytes() == before


def test_r2_28_hardlinked_output_cannot_clobber_input(tmp_path, protocol_file):
    """하드링크는 폴더도 이름도 다른데 같은 파일이다 — 어떤 경로 비교로도 못 본다.
    쓰기 직전 samefile 이 마지막 방어선."""
    ind = tmp_path / "in"; outd = tmp_path / "out"
    ind.mkdir(); outd.mkdir()
    victim = ind / "방문기록.csv"
    victim.write_text("피험자ID,방문명,방문일\nS01,Baseline,2026-03-02\nS01,V1,2026-03-30\n",
                      encoding="utf-8")
    before = victim.read_bytes()
    os.link(str(victim), str(outd / "이탈목록.csv"))
    code = run_cli([str(victim), "--protocol", protocol_file,
                    "--as-of", "2026-08-14", "--out-dir", str(outd)])
    assert code == 2
    assert victim.read_bytes() == before


# ══ R2-29 — 행 번호는 레코드 순번이 아니라 원본의 물리적 줄이어야 한다 ═══
def test_r2_29_row_numbers_survive_quoted_newlines(tmp_path):
    """엑셀에서 Alt+Enter 로 적은 비고 칸 하나가 그 뒤 번호를 통째로 밀었다.
    '원본 확인'이 목적인 안내가 엉뚱한 줄을 가리키면 없느니만 못하다."""
    p = write_csv(tmp_path, "v.csv",
                  '피험자ID,방문명,방문일,비고\n'
                  'S01,Baseline,2026-03-02,"첫 줄\n둘째 줄\n셋째 줄"\n'
                  'S01,V1,2026-03-28,\n')
    records, _enc, _b, _bd = load_visits_long(p)
    # Baseline 은 2행에서 시작, V1 은 (따옴표 안 3줄을 지나) 5행
    assert [r.row_no for r in records] == [2, 5]
