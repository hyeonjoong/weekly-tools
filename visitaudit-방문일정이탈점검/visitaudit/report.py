"""리포트 렌더링과 산출물 4종 쓰기.

커버리지 자백이 항상 맨 위다. 판정 못 한 것을 조용히 '이상 없음'으로
흘려보내는 체커는 없느니만 못하기 때문이다.

CSV 는 전부: (1) 기준시점(as-of) 열 포함, (2) 수식 인젝션 가드(`= + - @` 로
시작하는 비숫자 셀 앞에 `'`), (3) utf-8-sig (엑셀에서 바로 열림).
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re
from typing import List, Optional, Sequence

from . import __version__
from .consort import Consort, PPResult
from .criteria import CriteriaResult
from .enroll import Enrollment
from .judge import (E_DUP, E_FUTURE, E_PARSE, E_PREENROLL, E_SUBJECT,
                    JudgeResult, V_IN, V_MISSING, V_NA_DROPOUT, V_NA_OPTIONAL,
                    V_OUT, V_PENDING, V_UNJUDGEABLE)
from .protocol import Protocol

# 평범한 숫자 표기만 '숫자'로 본다. float() 는 nan/inf/"-1_0" 도 통과시켜
# 가드에 구멍을 낸다 (B11). 기준 재점검(criteria)과 같은 규칙을 쓴다.
from .tables import PLAIN_NUM as _PLAIN_NUM

# 수식으로 해석될 수 있는 시작 문자. 앞에 붙은 공백·탭·CR 은 스프레드시트가
# 무시하고 그 뒤부터 읽으므로, 가드도 같은 눈으로 봐야 한다.
_FORMULA_LEAD = "=+-@"
_STRIPPABLE = " \t\r\n\v\f﻿"


# ── CSV 수식 인젝션 가드 ──────────────────────────────────────────────
def guard_cell(value: object) -> str:
    text = "" if value is None else str(value)
    # 앞의 공백·탭·CR·BOM 을 걷어낸 뒤 첫 글자로 판단한다. 입력 셀은 tables 에서
    # 이미 strip 되지만, 가드가 그 사실에 기대면 다른 모듈의 한 줄이 바뀌는 순간
    # 조용히 구멍이 난다 — 여기서 스스로 방어한다.
    lead = text.lstrip(_STRIPPABLE)
    if lead and lead[0] in _FORMULA_LEAD:
        if _PLAIN_NUM.match(lead) and lead == text:
            return text        # -3, -3.5, +7 같은 숫자는 그대로
        return "'" + text
    return text


_PROTECTED_INPUTS: List[str] = []


def set_protected_inputs(paths: Sequence[str]) -> None:
    """이 실행에서 절대 덮어쓰면 안 되는 입력 파일들을 등록한다."""
    _PROTECTED_INPUTS[:] = [p for p in paths if p]


def _open_out(path: str, **kwargs):
    """산출물 쓰기 전용 open — 입력 파일을 건드릴 여지가 있으면 거부.

    경로 문자열 비교로는 하드링크를 볼 수 없다(폴더도 이름도 다른데 같은 파일).
    쓰기 직전에 `samefile` 로 신원을 확인하는 것이 마지막이자 확실한 방어선이다.
    """
    if os.path.islink(path):
        raise OSError(f"출력 경로가 심볼릭 링크입니다 — 링크 대상을 덮어쓰지 않기 위해 거부합니다: {path}")
    if os.path.exists(path):
        for src in _PROTECTED_INPUTS:
            try:
                if os.path.samefile(path, src):
                    raise OSError(f"출력 경로가 입력 파일과 같은 파일입니다 — 원본 보호를 위해 거부합니다: {path} ≡ {src}")
            except FileNotFoundError:
                continue
    return open(path, "w", **kwargs)


def write_csv(path: str, header: Sequence[str], rows: Sequence[Sequence[object]],
              as_of: dt.date) -> None:
    with _open_out(path, encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(header) + ["기준시점"])
        for row in rows:
            w.writerow([guard_cell(c) for c in row] + [as_of.isoformat()])


# ── 숫자 표기 ────────────────────────────────────────────────────────
def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "-"


def _signed(n: Optional[int]) -> str:
    if n is None:
        return ""
    return f"+{n}" if n > 0 else str(n)   # CSV 는 부호 없는 정수/음수 그대로


# ── 본문 렌더링 ──────────────────────────────────────────────────────
def render_report(protocol: Protocol, judged: JudgeResult, crit: CriteriaResult,
                  consort: Consort, pp: PPResult, enroll: Enrollment,
                  as_of: dt.date, as_of_defaulted: bool, protocol_path: str,
                  min_coverage: float) -> str:
    L: List[str] = []
    add = L.append
    add("visitaudit — 방문일정 이탈 점검")
    add(f"연구: {protocol.study}   기준시점(as-of): {as_of.isoformat()}   프로토콜: {protocol_path}")
    if as_of_defaulted:
        add(f"⚠ --as-of 미지정 — 오늘 날짜({as_of.isoformat()})를 사용했습니다. "
            "다음 달에 같은 파일을 돌리면 결과가 달라집니다. 재현하려면 --as-of 를 명시하세요.")
    add("")

    # ── 1. 커버리지 자백 (항상 최상단) ──────────────────────────────
    add("[커버리지 자백]")
    n_univ = len(judged.universe)
    n_unj_subj = len(judged.subject_unjudgeable)
    add(f"  피험자 {n_univ}명 중 {n_univ - n_unj_subj}명 판정 / {n_unj_subj}명 판정불가")
    if judged.subject_unjudgeable:
        by_reason = {}
        for sid, reason in judged.subject_unjudgeable.items():
            by_reason.setdefault(reason, []).append(sid)
        for reason, sids in by_reason.items():
            shown = ", ".join(sids[:6]) + (" …" if len(sids) > 6 else "")
            add(f"    - {reason}: {len(sids)}명 ({shown}) → 추정하지 않음")
    n_err = judged.n_unjudgeable - judged.error_count(E_SUBJECT)
    add(f"  예상 방문 슬롯 {judged.n_slots}건 중 판정완료 {judged.n_completed}건 / 판정 제외 {judged.n_excluded}건")
    add(f"    - 미도래(창 미마감): {judged.count(V_PENDING)}건 · 탈락 후 해당없음: {judged.count(V_NA_DROPOUT)}건"
        + (f" · 선택방문 미실시: {judged.count(V_NA_OPTIONAL)}건" if judged.count(V_NA_OPTIONAL) else ""))
    err_bits = []
    for kind in (E_DUP, E_PARSE, E_FUTURE, E_PREENROLL):
        n = judged.error_count(kind)
        if n:
            err_bits.append(f"{kind} {n}")
    add(f"    - 데이터 오류: {n_err}건" + (f" ({', '.join(err_bits)})" if err_bits else ""))
    n_subj_slots = judged.error_count(E_SUBJECT)
    if n_subj_slots:
        add(f"    - 피험자 판정불가로 묶인 슬롯: {n_subj_slots}건")
    rate = judged.coverage_rate
    denom = judged.n_completed + judged.n_unjudgeable
    if rate is None:
        add(f"  판정률 계산 불가 — 판정 대상 0건(전부 미도래/해당없음). 임계({min_coverage:.0f}%) 검사 생략.")
    else:
        verdict = "통과" if rate >= min_coverage else "미달 → exit 3"
        add(f"  판정률 {rate:.1f}% = 판정완료 {judged.n_completed} / 판정대상 {denom} "
            f"(미도래·해당없음은 분모 제외, 임계 {min_coverage:.0f}%) → {verdict}")
    add(f"  방문기록 {judged.n_rows_seen}행 읽음"
        + (f" (판정 제외 피험자의 행 {judged.n_nonuniverse_rows}건 포함)" if judged.n_nonuniverse_rows else ""))
    for note in judged.notes:
        add(f"  * {note}")
    for warn in judged.warnings:
        add(f"  ! {warn}")
    add("")

    # ── 2. 이탈 ─────────────────────────────────────────────────────
    devs = judged.deviations
    # 창이탈·결측은 방문 단위, 순서위반은 방문 '쌍' 단위라 분자와 분모의 단위가
    # 다르다. 비율은 방문 단위인 것만으로 내고, 순서위반은 건수로만 적는다.
    n_slot_devs = len(judged.deviations_of("창이탈")) + len(judged.deviations_of("필수방문결측"))
    add(f"[이탈]  총 {len(devs)}건"
        + (f" (방문 단위 {n_slot_devs}건 / 판정완료 {judged.n_completed}건 = "
           f"{_pct(n_slot_devs, judged.n_completed)})" if judged.n_completed else ""))
    if not devs:
        add("  이탈 없음")
    for kind, label in (("창이탈", "창 이탈"), ("필수방문결측", "필수방문 결측"), ("순서위반", "순서 위반")):
        group = judged.deviations_of(kind)
        if not group:
            continue
        add(f"  {label}  {len(group)}건")
        for d in group:
            if kind == "창이탈":
                add(f"    {d.subject}  {d.visit_name:<10} 예정 {d.scheduled.isoformat()} "
                    f"(창 {d.win_start.strftime('%m-%d')}~{d.win_end.strftime('%m-%d')})  "
                    f"실제 {d.actual.strftime('%m-%d')}  → {_signed(d.days_out)}일  [{d.severity}]")
            else:
                add(f"    {d.subject}  {d.visit_name:<10} {d.evidence}")
    add("")

    # ── 3. 데이터 오류 (이탈 아님, 크게) ────────────────────────────
    if judged.data_errors:
        add(f"[데이터 오류]  {len(judged.data_errors)}건 — 이탈로 세지 않음. 원본 확인 필요.")
        for e in judged.data_errors:
            add(f"    {e.subject}  {e.visit_name:<10} {e.kind}: {e.detail}")
        add("")

    # ── 4. 선정/제외기준 재점검 ─────────────────────────────────────
    if crit.skipped:
        add(f"[선정/제외기준 재점검]  생략 — {crit.skipped}")
    else:
        # 열 자체가 없는 기준은 피험자별로 반복하지 않고 열 단위로 한 줄만 내지만,
        # 머리 숫자에서도 빠지면 아래에 '판정불가' 줄이 보이는데 카운터는 0 인 모순이 된다.
        n_unj_cols = len(crit.missing_columns)
        col_txt = f"(+ 항목 열 없음 {n_unj_cols}종)" if n_unj_cols else ""
        add(f"[선정/제외기준 재점검]  위반 {len(crit.violations)}건 / 판정불가 {len(crit.unjudgeable)}건"
            f"{col_txt} (판정 {crit.n_checked}건)")
        for f in crit.violations:
            add(f"    위반     {f.subject}  {f.detail}")
        for f in crit.unjudgeable:
            add(f"    판정불가 {f.subject}  {f.detail}")
        for col in crit.missing_columns:
            add(f"    판정불가 (전원) 항목 열 {col!r} 이 피험자.csv 에 없음 — 위반으로 세지 않음")
    add("")

    # ── 5. CONSORT ──────────────────────────────────────────────────
    add("[CONSORT]")
    if not consort.available:
        add(f"  피험자.csv 없음 — 방문기록 기준 피험자 {consort.fallback_subjects}명, "
            f"마지막 방문({protocol.visits[-1].name}) 기록 {consort.fallback_last_visit_done}명. "
            "스크리닝/제외/군별/탈락 숫자는 계산 불가.")
    else:
        reason_txt = " / ".join(f"{r} {n}" for r, n in consort.excluded_reasons) or "-"
        add(f"  스크리닝 {consort.n_screened} → 제외 {consort.n_excluded} ({reason_txt})")
        arm_txt = "  /  ".join(f"{arm or '(군 미기재)'} {consort.arm_counts[arm]}" for arm in consort.arms)
        add(f"  무작위배정 {consort.n_randomized}  →  {arm_txt}")
        if consort.arms:
            add("  완료 " + " / ".join(str(consort.arm_completed[a]) for a in consort.arms)
                + "    진행중 " + " / ".join(str(consort.arm_ongoing[a]) for a in consort.arms)
                + "    중도탈락 " + " / ".join(str(consort.arm_dropout[a]) for a in consort.arms))
            for arm in consort.arms:
                reasons = consort.arm_dropout_reasons.get(arm) or []
                if reasons:
                    add(f"    ({arm or '(군 미기재)'} 탈락 사유: " + ", ".join(f"{r} {n}" for r, n in reasons) + ")")
    if pp.skipped:
        add(f"  ITT {consort.n_itt}   PP: {pp.skipped}")
    else:
        detail = ", ".join(f"{lbl} {n}" for lbl, n in pp.reason_counts)
        dedup = f" — 중복 {pp.n_dedup_removed}명 제거" if pp.n_dedup_removed > 0 else ""
        unj = f" / 판정불가 {pp.n_unjudgeable}" if pp.n_unjudgeable else ""
        why = f": {detail}{dedup}" if detail else ""
        add(f"  ITT {consort.n_itt}   PP 후보 {pp.n_candidates}  (제외 {pp.n_excluded}{why}{unj})")
        if pp.n_caveat_candidates:
            add(f"  ※ 후보 {pp.n_candidates}명 중 {pp.n_caveat_candidates}명은 판정하지 못한 항목이 있음 "
                "— 피험자별요약.csv 에 '후보(사유)'로 표기")
        add("  ※ PP 는 프로토콜 JSON 규칙에 따른 '후보'입니다 — 최종 확정은 데이터 검토 회의의 몫")
    bad = [desc for desc, ok, _indep in consort.checks if not ok]
    if consort.available:
        if bad:
            for desc in bad:
                add(f"  ✗ 정합성 경고: {desc}")
        else:
            cross = [desc for desc, _ok, indep in consort.checks if indep]
            add("  합계 확인: ✓ 스크리닝 = 제외 + 무작위배정 · ✓ 무작위배정 = 군 합계 "
                "· ✓ 군별 완료+진행중+탈락 = 배정  (같은 목록을 갈라 센 항등식)")
            add("  교차 검증: " + " · ".join("✓ " + desc for desc in cross)
                + (" · ✓ ITT ≥ PP" if pp.skipped is None else "")
                + "  (독립적으로 센 숫자 — 어긋날 수 있는 검사)")
        if pp.skipped is None and consort.n_itt < pp.n_candidates:
            add(f"  ✗ 정합성 경고: ITT({consort.n_itt}) < PP 후보({pp.n_candidates})")
    for note in consort.notes:
        add(f"  ※ {note}")
    add("")

    # ── 6. 등록 진행 ────────────────────────────────────────────────
    add("[등록 진행]")
    if enroll.skipped:
        add(f"  생략 — {enroll.skipped}")
    else:
        add("  " + " / ".join(f"{ym} {n}명" for ym, n in enroll.monthly))
        if enroll.n_missing_dates:
            add(f"  ! 등록일 없는 무작위배정 피험자 {enroll.n_missing_dates}명 — 월별 집계에서 빠짐")
        if enroll.n_future_dates:
            add(f"  ! as-of 이후 등록일 {enroll.n_future_dates}건 — 월별 집계 제외 "
                f"(월별 합 + 미래 {enroll.n_future_dates} + 미기재 {enroll.n_missing_dates} = 전체 {enroll.n_total_rows})")
        if enroll.rate is not None:
            add(f"  최근 {len(enroll.rate_months)}개월({enroll.rate_months[0]}~{enroll.rate_months[-1]}) "
                f"평균 {enroll.rate:.1f}명/월")
        if enroll.target_n is not None and enroll.remaining is not None:
            if enroll.remaining == 0:
                add(f"  목표 {enroll.target_n}명 도달 (등록 {enroll.n_enrolled}명)")
            elif enroll.projected_month:
                add(f"  목표 {enroll.target_n}명까지 {enroll.remaining}명 → 현 속도 유지 시 "
                    f"{enroll.projected_month} 경 (단순 선형 외삽, 신뢰구간 아님)")
            elif enroll.rate is None:
                add(f"  목표 {enroll.target_n}명까지 {enroll.remaining}명 — 완결된 달이 없어 속도를 낼 수 없음"
                    "(도달 시점 예측 불가)")
            else:
                add(f"  목표 {enroll.target_n}명까지 {enroll.remaining}명 — 최근 등록 속도가 0이라 도달 시점 예측 불가")
    return "\n".join(L)


# ── 문장 초안 (KR/EN) — 진행점검.md 전용 ─────────────────────────────
# 툴 내부 어휘는 번역하고, 데이터에서 온 라벨(군 이름·자유 텍스트 사유)은
# 원문 + 번역 필요 표시로 남긴다 (B12).
_EN_LABELS = {  # (단수, 복수)
    "필수방문결측": ("missed mandatory visit", "missed mandatory visits"),
    "선정기준위반": ("eligibility violation", "eligibility violations"),
    "탈락": ("withdrawal", "withdrawals"),
    "기준미달": ("did not meet eligibility criteria", "did not meet eligibility criteria"),
    "동의철회": ("consent withdrawal", "consent withdrawals"),
    "기타": ("other", "other"),
    "사유 미기재": ("reason not recorded", "reason not recorded"),
}
_RE_DAYS_LABEL = re.compile(r"^창이탈 (\d+)일초과$")

# 군 이름은 데이터에서 오지만, 한국 임상시험에서 쓰는 표준 표기는 거의 정해져
# 있다. 이것만 넣어도 EN CONSORT 캡션(가장 많이 붙여넣는 문장)이 그대로 나간다.
# 사전에 없는 이름은 원문 + [needs translation] 로 남겨 사람이 고치게 한다.
_EN_ARMS = {
    "중재군": "the intervention group",
    "시험군": "the treatment group",
    "실험군": "the experimental group",
    "치료군": "the treatment group",
    "대조군": "the control group",
    "위약군": "the placebo group",
    "표준치료군": "standard of care",
}


def _en_arm(arm: str) -> str:
    if not arm:
        return "unspecified arm"
    if arm in _EN_ARMS:
        return _EN_ARMS[arm]
    return f"{arm} [needs translation]"


def _en_label(label: str, n: int = 1) -> str:
    m = _RE_DAYS_LABEL.match(label)
    if m:
        return f"out-of-window >{m.group(1)} days"
    if label in _EN_LABELS:
        return _EN_LABELS[label][0 if n == 1 else 1]
    return f"{label} [needs translation]"


def _plural(n: int, singular: str, plural: str = "") -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def render_drafts(protocol: Protocol, judged: JudgeResult, crit: CriteriaResult,
                  consort: Consort, pp: PPResult, as_of: dt.date) -> str:
    n_dev = len(judged.deviations)
    n_win = len(judged.deviations_of("창이탈"))
    n_miss = len(judged.deviations_of("필수방문결측"))
    n_ord = len(judged.deviations_of("순서위반"))
    denom = judged.n_completed + judged.n_unjudgeable
    rate = judged.coverage_rate
    rate_txt = f"{rate:.1f}%" if rate is not None else "계산 불가"
    L: List[str] = []
    add = L.append
    add("## 문장 초안 (KR/EN)")
    add("")
    add("숫자는 이 실행에서 실제로 센 값입니다. 문장을 쓰기 전에 사유·규칙이 프로토콜")
    add("문서와 맞는지 반드시 사람이 확인하세요.")
    add("")
    add("### Protocol deviations 문단")
    add("")
    # 산술이 닫혀야 한다: 전체 슬롯 = 판정완료 + 미도래 + 해당없음 + 판정불가.
    # 판정불가를 빼먹으면 논문 심사자가 200 − 168 − 19 − 6 = 7 을 계산해 낸다.
    # 나머지(선택방문 해당없음 등)까지 합쳐 '그 밖' 으로 묶어 반드시 닫는다.
    n_pend = judged.count(V_PENDING)
    n_na = judged.count(V_NA_DROPOUT)
    n_unj = judged.n_unjudgeable
    n_rest = judged.n_slots - judged.n_completed - n_pend - n_na - n_unj
    kr_unj = f", 판정불가 {n_unj}건" if n_unj else ""
    kr_rest = f", 그 밖 {n_rest}건" if n_rest else ""
    en_unj = (f", and {_plural(n_unj, 'visit')} could not be adjudicated "
              "owing to duplicate rows or unparseable dates") if n_unj else ""
    en_rest = f", and {n_rest} otherwise not applicable" if n_rest else ""
    add(f"> 기준시점({as_of.isoformat()}) 현재, 무작위배정 피험자 {len(judged.universe)}명의 "
        f"예정 방문 {judged.n_slots}건 중 {judged.n_completed}건을 판정하였다"
        f"(판정률 {rate_txt} = 판정완료 {judged.n_completed} / 판정대상 "
        f"{judged.n_completed + n_unj}; 미도래 {n_pend}건, 중도탈락 후 해당없음 "
        f"{n_na}건{kr_unj}{kr_rest}은 판정 대상에서 제외). 프로토콜 이탈은 총 {n_dev}건으로, "
        f"방문창 이탈 {n_win}건, 필수방문 결측 {n_miss}건, 방문 순서 위반 {n_ord}건이었다.")
    add(">")
    add(f"> As of {as_of.isoformat()}, {judged.n_completed} of {judged.n_slots} scheduled visits "
        f"among {_plural(len(judged.universe), 'randomized participant')} were adjudicated "
        f"(coverage {rate_txt} = {judged.n_completed} adjudicated / "
        f"{judged.n_completed + n_unj} adjudicable; {n_pend} visits were not yet due, "
        f"{n_na} fell after withdrawal{en_unj}{en_rest}). "
        f"A total of {_plural(n_dev, 'protocol deviation')} "
        f"{'was' if n_dev == 1 else 'were'} identified: "
        f"{_plural(n_win, 'out-of-window visit')}, "
        f"{_plural(n_miss, 'missed mandatory visit')}, and "
        f"{_plural(n_ord, 'visit-sequence violation')}.")
    add("")
    if consort.available:
        add("### CONSORT 캡션 숫자")
        add("")
        reason_txt = "; ".join(f"{r} {n}" for r, n in consort.excluded_reasons) or "-"
        arm_txt = ", ".join(f"{arm or '(군 미기재)'} {consort.arm_counts[arm]}" for arm in consort.arms)
        add(f"> 스크리닝 {consort.n_screened}명 중 {consort.n_excluded}명 제외({reason_txt}), "
            f"{consort.n_randomized}명 무작위배정({arm_txt}).")
        add(">")
        en_reason = "; ".join(f"{n} {_en_label(r, n)}" for r, n in consort.excluded_reasons) or "-"
        en_arm = ", ".join(f"{consort.arm_counts[arm]} to {_en_arm(arm)}"
                           for arm in consort.arms)
        add(f"> Of {consort.n_screened} screened, {consort.n_excluded} were excluded ({en_reason}); "
            f"{consort.n_randomized} were randomized ({en_arm}).")
        add("")
    if pp.skipped is None:
        add("### PP 집합 정의 문장")
        add("")
        detail = ", ".join(f"{lbl} {n}명" for lbl, n in pp.reason_counts)
        # 산술 정합: ITT = 제외 + 판정불가 + 후보 — 판정불가를 빼먹으면 문장이 스스로 모순 (B1)
        # 제외가 한 명도 없을 때 사유 목록을 그대로 끼우면 "(중복 제거 후 0명)을 제외한"
        # 처럼 앞말이 없는 괄호가 남는다 — 등록 단계에서는 이쪽이 오히려 정상이다.
        if pp.reason_counts:
            kr_excl = f"{detail}(중복 제거 후 {pp.n_excluded}명)"
        else:
            kr_excl = f"제외 사유에 해당하는 피험자 {pp.n_excluded}명"
        if pp.n_unjudgeable:
            kr_excl += f"과 판정불가 {pp.n_unjudgeable}명"
        add(f"> PP(per-protocol) 집합 후보는 ITT {consort.n_itt}명에서 {kr_excl}을 제외한 "
            f"{pp.n_candidates}명이다. "
            "이는 프로토콜에 정의된 규칙을 기계적으로 적용한 후보이며, 최종 PP 확정은 "
            "눈가림 해제 전 데이터 검토 회의에서 이루어져야 한다.")
        add(">")
        en_detail = ", ".join(f"{n} {_en_label(lbl, n)}" for lbl, n in pp.reason_counts)
        en_why = f" ({en_detail}; counted once per participant)" if pp.reason_counts else ""
        en_unj = (f" and {_plural(pp.n_unjudgeable, 'unjudgeable participant')}"
                  if pp.n_unjudgeable else "")
        add(f"> The candidate per-protocol set comprises {pp.n_candidates} of "
            f"{_plural(consort.n_itt, 'ITT participant')}, after excluding "
            f"{_plural(pp.n_excluded, 'participant')}{en_why}{en_unj}. "
            "This is a rule-based candidate list; the final "
            "per-protocol population must be confirmed at a blinded data-review meeting.")
        add("")
    return "\n".join(L)


# ── 산출물 쓰기 ──────────────────────────────────────────────────────
OUT_FILES = ("진행점검.md", "이탈목록.csv", "피험자별요약.csv", "CONSORT.txt")


def write_outputs(out_dir: str, report_text: str, drafts_text: str, consort_txt: str,
                  judged: JudgeResult, pp: PPResult, subjects_map, as_of: dt.date) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)

    md_path = os.path.join(out_dir, "진행점검.md")
    with _open_out(md_path, encoding="utf-8") as fh:
        fh.write(f"# visitaudit 진행점검 — 기준시점 {as_of.isoformat()}\n\n")
        fh.write(f"(visitaudit v{__version__} 산출물. 판정치이며 확정이 아닙니다.)\n\n")
        fh.write("```\n" + report_text + "\n```\n\n")
        fh.write(drafts_text)

    dev_rows = []
    for d in judged.deviations:
        dev_rows.append([
            d.subject, d.visit_name, d.kind,
            d.scheduled.isoformat() if d.scheduled else "",
            d.win_start.isoformat() if d.win_start else "",
            d.win_end.isoformat() if d.win_end else "",
            d.actual.isoformat() if d.actual else "",
            "" if d.days_out is None else str(d.days_out),
            d.severity, d.evidence,
        ])
    write_csv(os.path.join(out_dir, "이탈목록.csv"),
              ["피험자ID", "방문명", "이탈유형", "예정일", "창시작", "창종료",
               "실제일", "창밖일수", "심각도", "근거"], dev_rows, as_of)

    subj_rows = []
    slots_map = judged.slots_by_subject()          # O(n) 한 번 (B8)
    devs_map = judged.deviations_by_subject()
    for sid in judged.universe:
        slots = slots_map.get(sid, [])
        devs = devs_map.get(sid, [])
        n_done = sum(1 for s in slots if s.verdict in (V_IN, V_OUT))
        outs = [s.days_out for s in slots if s.verdict == V_OUT and s.days_out is not None]
        worst = max(outs, key=abs) if outs else None
        entry = pp.entries.get(sid)
        subj = subjects_map.get(sid) if subjects_map else None
        judgeable = sid not in judged.subject_unjudgeable
        pp_status = ""
        if entry:
            pp_status = entry.status
            if entry.status == "후보" and entry.caveat:
                pp_status = f"후보({entry.caveat})"   # B7: 기준 판정불가 후보 표시
        subj_rows.append([
            sid,
            subj.arm if subj else "",
            str(n_done),
            str(len(devs)),
            "" if worst is None else str(worst),
            pp_status,
            "; ".join(entry.reasons) if entry and entry.status != "후보" else "",
            "가능" if judgeable else f"불가({judged.subject_unjudgeable[sid]})",
        ])
    write_csv(os.path.join(out_dir, "피험자별요약.csv"),
              ["피험자ID", "군", "완료방문수", "이탈건수", "최대창밖일수",
               "PP포함여부", "PP제외사유", "판정가능여부"], subj_rows, as_of)

    with _open_out(os.path.join(out_dir, "CONSORT.txt"), encoding="utf-8") as fh:
        fh.write(consort_txt)

    return list(OUT_FILES)
