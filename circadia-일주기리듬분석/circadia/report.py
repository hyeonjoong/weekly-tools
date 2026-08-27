"""한국어 건강정보 리포트 — 참고범위 대조, 비진단 문구 규율.

문구 규율(HARDENING.md·tests/test_report.py 로 강제):
- 모든 해석은 ①내 값 ②문헌 참고범위(출처 명기) ③방향 서술
  ("참고범위 대비 낮음/높음/범위 내") 3요소 구조를 지킨다.
- 진단 단정("~입니다"류 질병 명명)은 어떤 경우에도 쓰지 않는다.
- 참고범위가 활동 데이터 기준인 지표(RA 등)를 심박으로 계산했다면
  참고범위 비교 자체를 하지 않고 그 이유를 적는다.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .cosinor import CosinorFit, hours_to_clock
from .hrmark import HRMarkers
from .nonparam import Coverage, HourlyBinned, ISIVResult, L5M10Result
from .parse import ParseMeta
from .sleepreg import SleepRegularity, hours_to_clock_from_noon

DISCLAIMER = (
    "※ 면책: 이 도구는 의료기기가 아니며, 어떤 값도 의학적 진단이 아닙니다. "
    "여기 나오는 참고범위는 특정 연구 코호트의 기술통계 또는 일반 권고일 뿐 "
    "정상/비정상의 경계가 아닙니다. 수면 문제나 이상 소견이 지속되면 "
    "수면클리닉 등 전문의료기관과 상의하세요.")

# 참고범위 출처(리포트에 그대로 표기).
# IS/IV/RA 의 분포 '범위'는 원 논문이 보고한 값이 아니라 후속 코호트 문헌을
# 종합한 참고치이므로 그렇게 표기한다(라운드 1 M2). SRI 수치는 Windred 2023
# (Sleep 47:zsad253, UK Biobank n≈60,000: 중앙값 81.0, IQR 73.8–86.3)과
# Phillips 2017 (Sci Rep 7:3216): 코호트(n=61) 평균 73±11 (범위 38–86);
# 83±3 은 상위 규칙군(n=12)만의 수치 — 코호트 평균으로 쓰지 말 것(라운드 2 정정)
# (라운드 1 C1 — 이전 표기 '중앙값 ≈60'은 Cribb 2023 eLife 수치의 오귀속).
REF_IS = ("대략 0.4–0.8, 높을수록 일간 패턴 안정 (지표 정의: Van Someren 1999, "
          "Chronobiol Int; 분포 범위는 후속 코호트 문헌 종합 참고치)")
REF_IV = ("대략 0.4–1.0, 높을수록 리듬 단편화 (지표 정의: Van Someren 1999, "
          "Chronobiol Int; 분포 범위는 후속 코호트 문헌 종합 참고치)")
REF_RA = ("활동 기록에서 대략 0.8 이상 (지표 정의: Van Someren 1999, "
          "Chronobiol Int; 분포 범위는 후속 코호트 문헌 종합 참고치)")
REF_SRI = ("UK Biobank 6만 명 중앙값 81(IQR 74–86) (Windred 2023, Sleep); "
           "대학생 코호트 평균 73±11, 상위 규칙군은 83±3 (Phillips 2017, Sci Rep)")
REF_SJL = ("성인 69%가 1시간 이상 (Roenneberg 2012, Curr Biol); "
           "2시간 이상은 대사 지표와의 연관 보고")
REF_DIP = "주야간 10–20% 강하 참고 — 24시간 활동혈압(ABPM) 문헌의 dipping 관례를 심박에 준용한 값으로, 심박 고유의 확립 기준이 아님"
REF_TST = "성인 7–9시간 권장 (Hirshkowitz 2015, Sleep Health — National Sleep Foundation 권고)"
REF_COSINOR = "적합·검정 방법: Cornelissen 2014, Theor Biol Med Model (단일 성분 cosinor, zero-amplitude F 검정)"

# 방향 판정 경계 — 모든 판정 줄에 "(판정 경계 …)"로 공개한다(라운드 1 M10)
BOUND_IS = (0.4, 0.8)
BOUND_IV = (0.4, 1.0)
BOUND_RA = (0.8, 1.0)         # RA ≤ 1 이므로 사실상 하한 판정
BOUND_SRI = (74.0, 100.0)     # 74 = Windred 2023 IQR 하한 (라운드 1 C1)
BOUND_SJL = (0.0, 2.0)        # 2시간 초과 → 큼 (라운드 1 M1)
BOUND_TST = (7.0, 9.0)
BOUND_DIP = (10.0, 20.0)


def direction_phrase(value: float, low: float, high: float) -> str:
    if value < low:
        return "참고범위 대비 낮음"
    if value > high:
        return "참고범위 대비 높음"
    return "참고범위 내"


def fmt(v: Optional[float], nd: int = 2, unit: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}{unit}"


def csv_guard(cell: str) -> str:
    """스프레드시트 수식 인젝션 가드 — '='·'+'·'-'·'@'·탭 시작 셀 무력화.

    단, 순수 숫자(예: -100, -0.5)는 수식이 될 수 없고 통계 소프트웨어가
    숫자로 읽어야 하므로 건드리지 않는다.
    """
    if cell and cell[0] in "=+-@\t":
        try:
            float(cell)
            return cell
        except ValueError:
            return "'" + cell
    return cell


@dataclass
class AnalysisContext:
    """cli 가 채우고 report 가 그리는 분석 결과 묶음."""
    metas: List[ParseMeta] = field(default_factory=list)
    primary_kind: str = ""                 # 비모수 지표의 활동원("걸음"/"심박")
    primary_coverage: Optional[Coverage] = None
    coverages: List[Tuple[str, Coverage]] = field(default_factory=list)
    hr_cosinor: Optional[CosinorFit] = None
    act_cosinor: Optional[CosinorFit] = None
    isiv: Optional[ISIVResult] = None
    l5m10: Optional[L5M10Result] = None
    binned: Optional[HourlyBinned] = None
    sleep: Optional[SleepRegularity] = None
    hrmark: Optional[HRMarkers] = None
    actogram_text: Optional[str] = None
    min_days: int = 5
    min_wear: float = 0.5
    daily_rows: List[dict] = field(default_factory=list)
    # 코사이너를 계산하지 못한 사유 (라운드 1 M7 — 오도성 부재 메시지 방지)
    hr_cosinor_note: str = ""
    act_cosinor_note: str = ""


# ---------------------------------------------------------------------------
# 커버리지 자백 (리포트 최상단)
# ---------------------------------------------------------------------------

def _coverage_section(ctx: AnalysisContext, short_paths: bool = False) -> List[str]:
    out = ["── 데이터 커버리지 (읽고 시작하세요) " + "─" * 24]
    for m in ctx.metas:
        # 저장 파일(리듬리포트.md)에는 절대경로·사용자명을 남기지 않는다(M14)
        shown_path = os.path.basename(m.path) if short_paths else m.path
        span = ""
        if m.first_ts and m.last_ts:
            days = (m.last_ts.date() - m.first_ts.date()).days + 1
            span = f"{m.first_ts:%Y-%m-%d %H:%M} ~ {m.last_ts:%Y-%m-%d %H:%M} ({days}일)"
        if m.kind == "수면":
            # 수면은 '행'과 '구간'이 다르다(단계 행 병합) — 셈을 정확히(M9)
            line = f"[{m.kind}] {shown_path} — 원본 데이터 행 {m.n_rows}행"
            if m.excluded:
                details = ", ".join(f"{k} {v}행" for k, v in m.excluded.items())
                line += f"(제외: {details})"
            line += f" → 수면구간 {m.n_used}개"
        else:
            line = f"[{m.kind}] {shown_path} — 행 {m.n_used}개 사용"
            if m.excluded:
                details = ", ".join(f"{k} {v}행" for k, v in m.excluded.items())
                line += f", 제외: {details}"
        out.append(line)
        if span:
            out.append(f"    기간: {span}")
        if m.tz_note:
            out.append(f"    시간대: {m.tz_note}")
        for note in m.notes:
            out.append(f"    주: {note}")
    for kind, cov in ctx.coverages:
        out.append(f"[{kind}] 착용률(시간 빈 기준): {cov.wear_rate * 100:.1f}% "
                   f"({cov.n_covered}/{cov.n_hour_bins} 시간)")
        if cov.gaps:
            shown = cov.gaps[:10]
            out.append(f"    갭(3시간 이상) {len(cov.gaps)}건:"
                       + ("" if len(cov.gaps) <= 10 else " (앞 10건만)"))
            for a, b in shown:
                hrs = (b - a).total_seconds() / 3600.0
                out.append(f"      {a:%m-%d %H:%M} → {b:%m-%d %H:%M} ({hrs:.1f}h)")
    if ctx.binned is not None:
        out.append(f"[비모수 지표] 유효일(24/24 시간 빈 채워진 날): "
                   f"{len(ctx.binned.valid_days)}일")
        if ctx.binned.dropped_days:
            drops = ", ".join(f"{d:%m-%d}({n}/24)" for d, n in ctx.binned.dropped_days)
            out.append(f"    제외한 날(빈 미달 — 보간하지 않음): {drops}")
    out.append("결측은 보간하지 않았습니다. 갭은 위에 적힌 그대로 계산에서 빠집니다.")
    return out


# ---------------------------------------------------------------------------
# 지표 섹션들
# ---------------------------------------------------------------------------

def _cosinor_section(ctx: AnalysisContext) -> List[str]:
    out = ["", "── 코사이너(24h 리듬 성분) " + "─" * 30]
    any_fit = False
    act_label = "활동(" + (ctx.primary_kind or "걸음") + ")"
    for label, fit, note in (("심박", ctx.hr_cosinor, ctx.hr_cosinor_note),
                             (act_label, ctx.act_cosinor, ctx.act_cosinor_note)):
        if fit is None:
            if note:                       # 이유를 구분해 말한다(M7)
                any_fit = True
                out.append(f"[{label}] 적합하지 않음 — {note}")
            continue
        any_fit = True
        sig = "—"
        if fit.p_value is not None:
            sig = ("유의(p<0.001)" if fit.p_value < 0.001
                   else f"유의(p={fit.p_value:.3f})" if fit.p_value < 0.05
                   else f"비유의(p={fit.p_value:.3f})")
        out.append(f"[{label}] MESOR {fmt(fit.mesor)} · 진폭 {fmt(fit.amplitude)} · "
                   f"정점위상 {fit.acrophase_clock} · R²={fmt(fit.r2, 3)} · "
                   f"zero-amplitude 검정 {sig} (n={fit.n})")
        if fit.p_value is not None and fit.p_value >= 0.05:
            out.append("    → 24시간 주기 성분이 통계적으로 뚜렷하지 않습니다 — "
                       "기록이 짧거나 리듬 진폭이 작을 수 있습니다.")
    if not any_fit:
        out.append("계산할 시계열이 없습니다 (심박 또는 걸음 입력 필요).")
    out.append(REF_COSINOR)
    return out


def _nonparam_section(ctx: AnalysisContext) -> List[str]:
    out = ["", f"── 비모수 리듬 지표 (활동원: {ctx.primary_kind}) " + "─" * 20]
    hr_based = ctx.primary_kind == "심박"
    if ctx.isiv is None:
        out.append("계산할 시계열이 없습니다.")
        return out
    r = ctx.isiv
    if r.insufficient:
        out.append(f"IS(일간 안정성): {r.note} — 계산하지 않습니다 "
                   f"(유효 {ctx.min_days}일 미만이면 값이 불안정해 오해를 만듭니다)")
        out.append(f"IV(일내 변동성): {r.note} — 계산하지 않습니다")
    else:
        if r.is_ is not None:
            line = f"IS(일간 안정성) = {r.is_:.3f} — 참고범위 {REF_IS}"
            if not hr_based:
                line += (f" → {direction_phrase(r.is_, *BOUND_IS)} "
                         f"(판정 경계 {BOUND_IS[0]}–{BOUND_IS[1]})")
            else:
                line += " → 참고범위는 활동 기준이라 심박 IS에는 방향 판정을 하지 않습니다"
            out.append(line)
        if r.iv is not None:
            line = f"IV(일내 변동성) = {r.iv:.3f} — 참고범위 {REF_IV}"
            if not hr_based:
                line += (f" → {direction_phrase(r.iv, *BOUND_IV)} "
                         f"(판정 경계 {BOUND_IV[0]}–{BOUND_IV[1]})")
            else:
                line += " → 참고범위는 활동 기준이라 방향 판정을 하지 않습니다"
            out.append(line)
        if r.note:
            out.append(f"    주: {r.note}")
        if not hr_based and r.iv is not None:
            out.append("    주: IS/IV 참고범위는 손목 가속도계 활동량(count) 문헌 "
                       "기준입니다 — 걸음수는 활동이 계단식이라 IV가 그보다 높게 "
                       "나오는 경향이 있어, IV의 방향 판정은 보수적으로 읽으세요")
    if ctx.l5m10 is not None:
        l = ctx.l5m10
        out.append(f"L5(최저 5h) = {fmt(l.l5)} (중앙 {hours_to_clock(l.l5_mid_hours)}, "
                   f"시작 {l.l5_onset_hour:02d}시) · "
                   f"M10(최고 10h) = {fmt(l.m10)} (중앙 {hours_to_clock(l.m10_mid_hours)}, "
                   f"시작 {l.m10_onset_hour:02d}시) — 유효일 {l.n_days}일 평균 프로파일")
        if l.n_days < 3:
            out.append("    주: 유효일이 3일 미만 — L5/M10은 참고로만 보세요")
        if l.ra is not None:
            line = f"RA(상대진폭) = {l.ra:.3f} — 참고범위 {REF_RA}"
            if not hr_based:
                line += (f" → {direction_phrase(l.ra, *BOUND_RA)} "
                         f"(판정 경계 {BOUND_RA[0]} 미만 → 낮음)")
            else:
                line += (" → 심박은 진폭이 원래 작아 활동 기준 참고범위를 적용할 수 "
                         "없습니다(값만 보고)")
            out.append(line)
    return out


def _sleep_section(ctx: AnalysisContext) -> List[str]:
    out = ["", "── 수면 규칙성 " + "─" * 40]
    s = ctx.sleep
    if s is None:
        out.append("수면구간 입력이 없어 계산하지 않습니다.")
        return out
    out.append(f"밤 수: {len(s.nights)}밤 (주중 {s.n_work}, 주말[금·토] {s.n_free})")
    if s.sri.insufficient:
        out.append(f"SRI(수면 규칙성 지수): {s.sri.note} — 계산하지 않습니다")
    elif s.sri.sri is not None:
        out.append(f"SRI = {s.sri.sri:.1f} — 참고범위 {REF_SRI} → "
                   f"{direction_phrase(s.sri.sri, *BOUND_SRI)} "
                   f"(판정 경계 {BOUND_SRI[0]:.0f} = Windred IQR 하한; "
                   f"상태쌍 {s.sri.n_pairs:,}분)")
    if s.midsleep_mean_h is not None:
        out.append(f"중간수면 시각: 평균 {hours_to_clock_from_noon(s.midsleep_mean_h)}"
                   f" ± {fmt(s.midsleep_sd_h, 2, 'h')} (SD)")
    if s.onset_sd_h is not None and s.wake_sd_h is not None:
        out.append(f"입면 시각 SD {fmt(s.onset_sd_h, 2, 'h')} · "
                   f"기상 시각 SD {fmt(s.wake_sd_h, 2, 'h')} "
                   f"(평균 입면 {hours_to_clock_from_noon(s.onset_mean_h)} · "
                   f"평균 기상 {hours_to_clock_from_noon(s.wake_mean_h)})")
    if s.tst_mean_h is not None:
        line = (f"주 수면 시간: {fmt(s.tst_mean_h, 2)} ± {fmt(s.tst_sd_h, 2)} 시간 — "
                f"참고범위 {REF_TST} → {direction_phrase(s.tst_mean_h, *BOUND_TST)} "
                f"(판정 경계 {BOUND_TST[0]:.0f}–{BOUND_TST[1]:.0f}시간)")
        out.append(line)
    if s.sjl_hours is not None:
        out.append(f"사회적 시차 = {s.sjl_hours:.2f}시간 "
                   f"(주중밤 중간수면 {hours_to_clock_from_noon(s.msw_h)} vs "
                   f"주말밤 {hours_to_clock_from_noon(s.msf_h)}) — 참고범위 {REF_SJL} → "
                   f"{direction_phrase(s.sjl_hours, *BOUND_SJL)} "
                   f"(판정 경계 {BOUND_SJL[1]:.0f}시간 초과 → 높음)")
        out.append("    주: Roenneberg의 수면부채 보정판(MSFsc)이 아닌 원식 |MSF−MSW| 입니다")
    else:
        out.append(f"사회적 시차: 계산하지 않음 — {s.sjl_note}")
    for note in s.notes:
        out.append(f"    주: {note}")
    return out


def _hr_section(ctx: AnalysisContext) -> List[str]:
    out = ["", "── 심박 일주기 마커 " + "─" * 36]
    h = ctx.hrmark
    if h is None:
        out.append("심박 입력이 없어 계산하지 않습니다.")
        return out
    if h.dip_pct is not None:
        out.append(f"야간 심박 강하율 = {h.dip_pct:.1f}% "
                   f"(주간 평균 {fmt(h.day_mean, 1)}bpm → 야간 평균 {fmt(h.night_mean, 1)}bpm, "
                   f"{h.method}) — 참고범위 {REF_DIP} → "
                   f"{direction_phrase(h.dip_pct, *BOUND_DIP)} "
                   f"(판정 경계 {BOUND_DIP[0]:.0f}–{BOUND_DIP[1]:.0f}%)")
    if h.nadir_hour_mid is not None:
        out.append(f"심박 최저점(nadir): {hours_to_clock(h.nadir_hour_mid)} 부근 "
                   f"(시간당 평균 프로파일 최저 {fmt(h.nadir_value, 1)}bpm)")
    if h.prewake_rise is not None:
        word = "관찰됨" if h.prewake_rise else "뚜렷하지 않음"
        out.append(f"기상 전 심박 상승: {word} (밤별 Δ 중앙값 "
                   f"{fmt(h.prewake_delta_bpm, 1)}bpm, {h.n_nights_used}밤 기준, "
                   f"기준 +2bpm 초과)")
    for note in h.notes:
        out.append(f"    주: {note}")
    return out


def _tips_section() -> List[str]:
    return [
        "",
        "── 일반적 리듬 관리 수칙 (비진단·일반 정보) " + "─" * 16,
        "- 기상 시각을 주말 포함 매일 같게 유지하는 것이 리듬 안정의 첫 단추입니다.",
        "- 기상 후 1시간 안에 밝은 빛(가능하면 야외 햇빛)을 쬐면 위상이 앞당겨집니다.",
        "- 취침 2–3시간 전에는 밝은 조명과 카페인을 줄이는 것이 일반적으로 권장됩니다.",
        "- 주말 몰아자기는 사회적 시차를 키웁니다 — 늦잠은 1시간 이내로.",
        "위 수칙은 일반적 수면위생 정보이며 개인 맞춤 의학적 조언이 아닙니다.",
    ]


def build_report(ctx: AnalysisContext, short_paths: bool = False) -> str:
    """short_paths=True 는 저장 파일용 — 경로를 basename 으로(M14)."""
    lines: List[str] = []
    lines.append("=" * 66)
    lines.append("  circadia — 일주기리듬 분석 리포트")
    lines.append("=" * 66)
    lines.extend(_coverage_section(ctx, short_paths=short_paths))
    lines.extend(_cosinor_section(ctx))
    lines.extend(_nonparam_section(ctx))
    lines.extend(_sleep_section(ctx))
    lines.extend(_hr_section(ctx))
    lines.extend(_tips_section())
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 지표 CSV (statwise/longistat 투입용)
# ---------------------------------------------------------------------------

CSV_HEADER = ["구분", "날짜", "지표", "값", "단위", "비고"]


def _row(kind: str, date: str, name: str, value, unit: str, note: str = "") -> List[str]:
    if value is None:
        val = "데이터 부족"
    elif isinstance(value, float):
        val = f"{value:.6g}"
    else:
        val = str(value)
    return [csv_guard(kind), csv_guard(date), csv_guard(name), csv_guard(val),
            csv_guard(unit), csv_guard(note)]


def build_metrics_rows(ctx: AnalysisContext) -> List[List[str]]:
    rows: List[List[str]] = [CSV_HEADER[:]]
    a = rows.append
    if ctx.hr_cosinor:
        f = ctx.hr_cosinor
        a(_row("요약", "", "cosinor_HR_MESOR", f.mesor, "bpm"))
        a(_row("요약", "", "cosinor_HR_진폭", f.amplitude, "bpm"))
        a(_row("요약", "", "cosinor_HR_정점위상", f.acrophase_clock, "시:분"))
        a(_row("요약", "", "cosinor_HR_R2", f.r2, ""))
        a(_row("요약", "", "cosinor_HR_p", f.p_value, ""))
    if ctx.act_cosinor:
        f = ctx.act_cosinor
        a(_row("요약", "", "cosinor_활동_MESOR", f.mesor, ctx.primary_kind))
        a(_row("요약", "", "cosinor_활동_진폭", f.amplitude, ctx.primary_kind))
        a(_row("요약", "", "cosinor_활동_정점위상", f.acrophase_clock, "시:분"))
        a(_row("요약", "", "cosinor_활동_R2", f.r2, ""))
        a(_row("요약", "", "cosinor_활동_p", f.p_value, ""))
    if ctx.isiv:
        # '비연속 run' 주석은 IV 계산에만 해당 — IS 행에 오부착 금지(M3).
        # 부족 사유("데이터 부족…")는 두 지표 공통이므로 양쪽에 남긴다.
        common = ctx.isiv.note if ctx.isiv.insufficient else ""
        iv_note = ctx.isiv.note
        a(_row("요약", "", "IS", ctx.isiv.is_, "", common))
        a(_row("요약", "", "IV", ctx.isiv.iv, "", iv_note))
    if ctx.l5m10:
        l = ctx.l5m10
        a(_row("요약", "", "L5", l.l5, ctx.primary_kind))
        a(_row("요약", "", "L5_중앙시각", hours_to_clock(l.l5_mid_hours), "시:분"))
        a(_row("요약", "", "M10", l.m10, ctx.primary_kind))
        a(_row("요약", "", "M10_중앙시각", hours_to_clock(l.m10_mid_hours), "시:분"))
        a(_row("요약", "", "RA", l.ra, ""))
    if ctx.sleep:
        s = ctx.sleep
        a(_row("요약", "", "SRI", s.sri.sri, "", s.sri.note))
        a(_row("요약", "", "중간수면_평균",
               hours_to_clock_from_noon(s.midsleep_mean_h) if s.midsleep_mean_h is not None else None,
               "시:분"))
        a(_row("요약", "", "중간수면_SD", s.midsleep_sd_h, "시간"))
        a(_row("요약", "", "입면_SD", s.onset_sd_h, "시간"))
        a(_row("요약", "", "기상_SD", s.wake_sd_h, "시간"))
        a(_row("요약", "", "수면시간_평균", s.tst_mean_h, "시간"))
        a(_row("요약", "", "수면시간_SD", s.tst_sd_h, "시간"))
        a(_row("요약", "", "사회적시차", s.sjl_hours, "시간", s.sjl_note))
    if ctx.hrmark:
        h = ctx.hrmark
        a(_row("요약", "", "야간심박강하율", h.dip_pct, "%", h.method))
        a(_row("요약", "", "심박_nadir_시각",
               hours_to_clock(h.nadir_hour_mid) if h.nadir_hour_mid is not None else None,
               "시:분"))
        a(_row("요약", "", "기상전상승_Δ중앙값", h.prewake_delta_bpm, "bpm"))
    for d in ctx.daily_rows:
        date = d.get("날짜", "")
        for key, pair in d.items():
            if key == "날짜":
                continue          # 날짜는 각 행의 열로 들어간다
            val, unit = pair
            a(_row("일별", date, key, val, unit))
    return rows


def build_daily_rows(ctx: AnalysisContext,
                     hr_samples, steps_samples) -> List[dict]:
    """1행=1일 일별 지표 — 지표.csv의 '일별' 구분으로 들어간다."""
    daily: dict = {}

    def bucket(d):
        return daily.setdefault(d, {"날짜": f"{d:%Y-%m-%d}"})

    if hr_samples:
        by_day: dict = {}
        for t, v in hr_samples:
            by_day.setdefault(t.date(), []).append(v)
        for d, vs in sorted(by_day.items()):
            bucket(d)["심박_평균"] = (sum(vs) / len(vs), "bpm")
    if steps_samples:
        by_day = {}
        for t, v in steps_samples:
            by_day.setdefault(t.date(), []).append(v)
        for d, vs in sorted(by_day.items()):
            bucket(d)["걸음_합"] = (sum(vs), "걸음")
    if ctx.sleep:
        for n in ctx.sleep.nights:
            b = bucket(n.date)
            b["수면시간_주수면"] = (n.tst_hours, "시간")
            b["입면"] = (f"{n.onset:%H:%M}", "시:분")
            b["기상"] = (f"{n.wake:%H:%M}", "시:분")
            b["중간수면"] = (f"{n.midsleep:%H:%M}", "시:분")
            b["밤유형"] = ("주말밤" if n.is_weekend else "주중밤", "")
    return [daily[d] for d in sorted(daily)]
