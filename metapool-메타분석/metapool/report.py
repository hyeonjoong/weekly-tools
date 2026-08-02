"""사람이 읽는 리포트 · 텍스트 숲그림(forest plot) · 논문용 문장 생성."""

from __future__ import annotations

import math
import unicodedata
from typing import List, Optional, Sequence

from .analysis import Analysis
from .effects import measure_label

__all__ = ["render_text", "render_markdown", "forest_plot", "sentences"]

_PLOT_WIDTH = 41


# --------------------------------------------------------------------------
# 숫자 포맷
# --------------------------------------------------------------------------


def fmt(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "—"
    if value != 0 and abs(value) < 10 ** (-digits):
        return "%.*e" % (digits, value)
    if abs(value) >= 1e7:  # 리포트 열이 무너지지 않도록 큰 값은 지수 표기
        return "%.*e" % (digits, value)
    return "%.*f" % (digits, value)


def fmt_p(p: Optional[float]) -> str:
    """논문 관행에 맞춘 p값 표기."""
    if p is None or math.isnan(p):
        return "—"
    if p < 0.001:
        return "< .001"
    return ("= %.3f" % p).replace("0.", ".", 1)


def _pct(x: float) -> str:
    return "%.1f%%" % x


def _ci(low: float, high: float, digits: int = 3) -> str:
    return "[%s, %s]" % (fmt(low, digits), fmt(high, digits))


def _width(text: str) -> int:
    """한글·한자 등 전각 문자를 2칸으로 세는 터미널 표시폭."""
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _truncate(text: str, width: int) -> str:
    if _width(text) <= width:
        return text
    out = ""
    for ch in text:
        if _width(out + ch) > width - 1:
            return out + "…"
        out += ch
    return out


# --------------------------------------------------------------------------
# 숲그림
# --------------------------------------------------------------------------


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def forest_plot(analysis: Analysis, width: int = _PLOT_WIDTH) -> List[str]:
    """텍스트 숲그림. 로그 지표는 로그 척도에 그리되 눈금은 되돌린 값으로 표기한다."""
    a = analysis
    z = a._z
    lows = sorted(s.ci(z)[0] for s in a.studies)
    highs = sorted(s.ci(z)[1] for s in a.studies)
    # 극단적으로 넓은 CI 하나가 그림을 망치지 않도록 분위수로 축 범위를 잡는다.
    lo = min(_quantile(lows, 0.1), a.primary.ci_low, 0.0)
    hi = max(_quantile(highs, 0.9), a.primary.ci_high, 0.0)
    center = a.primary.estimate if math.isfinite(a.primary.estimate) else 0.0
    if not math.isfinite(lo) or not math.isfinite(hi) or not math.isfinite(hi - lo) or hi - lo < 1e-12:
        span = max(abs(center), 1.0)
        lo, hi = center - span, center + span
    pad = 0.05 * (hi - lo)
    lo, hi = lo - pad, hi + pad

    def pos(x: float) -> int:
        if not math.isfinite(x):
            return width - 1 if x > 0 else 0
        frac = (x - lo) / (hi - lo)
        if not math.isfinite(frac):
            return 0
        return max(0, min(width - 1, int(round(frac * (width - 1)))))

    null_pos = pos(0.0) if lo <= 0.0 <= hi else None

    # 아래 요약 행 이름들도 같은 열에 들어가므로 폭 계산에 함께 넣는다.
    fixed_labels = ("통합(변량효과)", "통합(고정효과)", "95% 예측구간")
    label_w = max(
        12,
        min(24, max(_width(s.label) for s in a.studies)),
        max(_width(t) for t in fixed_labels),
    )
    rows: List[str] = []

    def bar(est: float, cl: float, ch: float, marker: str) -> str:
        cells = [" "] * width
        if null_pos is not None:
            cells[null_pos] = "│"
        left_clip = cl < lo
        right_clip = ch > hi
        p_lo, p_hi = pos(max(cl, lo)), pos(min(ch, hi))
        for i in range(p_lo, p_hi + 1):
            cells[i] = "─"
        # 신뢰구간이 무효과선을 가로지르면 교차점을 남겨 한눈에 보이게 한다.
        if null_pos is not None and p_lo <= null_pos <= p_hi:
            cells[null_pos] = "┼"
        if left_clip:
            cells[0] = "◄"
        if right_clip:
            cells[width - 1] = "►"
        cells[pos(est)] = marker
        return "".join(cells)

    header = "%s %s %s %s" % (
        _pad("연구", label_w),
        _pad("효과 [%d%% CI]" % round(a.conf * 100), 24),
        _pad("가중치", 7),
        "숲그림 (│ 무효과선, ■ 연구, ◆ 통합, ◇ 예측구간)",
    )
    rows.append(header)
    rows.append("-" * (label_w + 34 + width))

    for s, wpct in zip(a.studies, a.primary.weight_percent):
        cl, ch = s.ci(z)
        est_txt = "%s %s" % (fmt(a.back(s.yi)), _ci(a.back(cl), a.back(ch)))
        rows.append(
            "%s %s %s %s"
            % (
                _pad(_truncate(s.label, label_w), label_w),
                _pad(est_txt, 24),
                _pad(_pct(wpct), 7),
                bar(s.yi, cl, ch, "■"),
            )
        )

    rows.append("-" * (label_w + 34 + width))
    for p, name in ((a.random, "통합(변량효과)"), (a.fixed, "통합(고정효과)")):
        est_txt = "%s %s" % (fmt(a.back(p.estimate)), _ci(a.back(p.ci_low), a.back(p.ci_high)))
        rows.append(
            "%s %s %s %s"
            % (
                _pad(name, label_w),
                _pad(est_txt, 24),
                _pad("100%", 7),
                bar(p.estimate, p.ci_low, p.ci_high, "◆"),
            )
        )
    if a.pred:
        rows.append(
            "%s %s %s %s"
            % (
                _pad("%d%% 예측구간" % round(a.conf * 100), label_w),
                _pad(_ci(a.back(a.pred[0]), a.back(a.pred[1])), 24),
                _pad("", 7),
                bar(a.primary.estimate, a.pred[0], a.pred[1], "◇"),
            )
        )

    axis_left, axis_right = a.back(lo), a.back(hi)
    axis = " " * (label_w + 34) + _pad(fmt(axis_left, 2), width // 2) + fmt(axis_right, 2).rjust(
        width - width // 2
    )
    rows.append(axis)
    if a.is_log:
        rows.append(" " * (label_w + 34) + "(가로축은 로그 척도, 눈금은 %s 값)" % ("OR" if a.measure == "or" else "RR"))
    return rows


# --------------------------------------------------------------------------
# 논문용 문장
# --------------------------------------------------------------------------


def sentences(a: Analysis) -> "tuple[str, str]":
    """한국어·영어 결과 문장 (그대로 원고에 붙여 쓰고 숫자만 확인하면 되도록).

    하위군 차이와 민감도 분석처럼 **심사자가 반드시 묻는 내용**을 함께 넣고,
    도구 스스로 "믿지 말라"고 경고한 검정(연구 10편 미만의 Egger)은 결과 문장에
    수치로 싣지 않는다 — 그대로 붙여 넣으면 심사에서 지적받기 때문이다.
    """
    p = a.primary
    if p.k < 3:
        msg_ko = "유효한 연구가 %d편뿐이라 메타분석 결과 문장을 만들지 않습니다 (통합 추정이 의미를 갖기 어렵습니다)." % p.k
        msg_en = (
            "Only %d study(ies) available — no pooled results paragraph is generated, "
            "because pooling is not meaningful at this size." % p.k
        )
        return msg_ko, msg_en
    name_en, name_ko = {
        "smd": ("Hedges' g", "표준화 평균차"),
        "md": ("mean difference", "평균차"),
        "or": ("odds ratio", "오즈비"),
        "rr": ("risk ratio", "위험비"),
        "rd": ("risk difference", "위험차"),
        "generic": ("effect size", "통합 효과크기"),
    }[a.measure]
    est = a.back(p.estimate)
    cl, ch = a.back(p.ci_low), a.back(p.ci_high)
    model_ko = "변량효과" if p.model == "random" else "고정효과"
    model_en = "random-effects" if p.model == "random" else "fixed-effect"
    n_txt = "" if a.total_n is None else "(총 %s명)" % format(int(a.total_n), ",")
    sig_ko = "통계적으로 유의하였다" if p.p < 0.05 else "통계적으로 유의하지 않았다"
    sig_en = "statistically significant" if p.p < 0.05 else "not statistically significant"
    stat_name = "t(%g)" % p.df if p.ci_method == "HK" else "z"
    ci_note_ko = " Hartung–Knapp 보정을 적용하였다." if p.ci_method == "HK" else ""
    ci_note_en = " Hartung–Knapp adjustment was applied." if p.ci_method == "HK" else ""

    ko = (
        "%s 모형으로 %d편의 연구%s를 통합한 결과, %s는 %s (%d%% CI %s ~ %s, %s = %s, p %s)로 %s. "
        "연구 간 이질성은 Q(%d) = %s, p %s, I² = %s, τ² = %s이었다.%s"
        % (
            model_ko, p.k, n_txt, name_ko, fmt(est), round(a.conf * 100), fmt(cl), fmt(ch),
            stat_name, fmt(p.stat, 2), fmt_p(p.p), sig_ko,
            a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p), _pct(a.het.i2), fmt(a.het.tau2),
            ci_note_ko,
        )
    )
    if a.pred:
        ko += " %d%% 예측구간은 %s ~ %s이었다." % (
            round(a.conf * 100), fmt(a.back(a.pred[0])), fmt(a.back(a.pred[1])))
    if a.subgroup_test:
        t = a.subgroup_test
        named = ", ".join(
            "%s %s" % (r.name, fmt(a.back(r.pooled.estimate)))
            for r in a.subgroups if r.name in t["groups"]
        )
        ko += " 하위군 분석에서 %s로, 하위군 간 차이는 %s(Q_between(%d) = %s, p %s)." % (
            named,
            "유의하였다" if t["p"] < 0.05 else "유의하지 않았다",
            t["df"], fmt(t["q_between"], 2), fmt_p(t["p"]),
        )
    if a.loo:
        flipped = [r.omitted for r in a.loo if (r.p < 0.05) != (p.p < 0.05)]
        ko += (
            " 연구를 하나씩 제외한 민감도 분석에서 통합 추정치는 %s ~ %s 범위였으며, %s"
            % (
                fmt(a.back(min(r.estimate for r in a.loo))),
                fmt(a.back(max(r.estimate for r in a.loo))),
                ("'%s'을(를) 제외하면 유의성 결론이 달라졌다." % ", ".join(flipped[:3]))
                if flipped else "어느 한 편을 제외해도 결론은 유지되었다.",
            )
        )
    if a.egger and a.egger.k >= 10:
        ko += " Egger 회귀 비대칭 검정에서 절편은 %s (p %s)이었다." % (
            fmt(a.egger.intercept, 2), fmt_p(a.egger.p))
    elif a.egger:
        ko += " 연구가 %d편(<10)이어서 깔때기그림 비대칭은 형식적으로 평가하지 않았다." % a.egger.k
    if a.is_log:
        ko += " (합성은 로그 척도에서 수행하였고, τ²는 로그 척도 값이다.)"
    if a.outcome:
        ko = ko.replace("통합한 결과,", "통합한 결과, %s에 대한" % a.outcome, 1)
    else:
        ko += " [결과변수명·비교대상을 문장에 채워 넣으세요]"

    en = (
        "A %s meta-analysis of %d studies%s yielded a pooled %s of %s (%d%% CI %s to %s, %s = %s, "
        "p %s), which was %s. Between-study heterogeneity was Q(%d) = %s, p %s, I² = %s, τ² = %s.%s"
        % (
            model_en, p.k, "" if a.total_n is None else " (N = %s)" % format(int(a.total_n), ","),
            name_en, fmt(est), round(a.conf * 100), fmt(cl), fmt(ch),
            stat_name, fmt(p.stat, 2), fmt_p(p.p), sig_en,
            a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p), _pct(a.het.i2), fmt(a.het.tau2),
            ci_note_en,
        )
    )
    if a.pred:
        en += " The %d%% prediction interval ranged from %s to %s." % (
            round(a.conf * 100), fmt(a.back(a.pred[0])), fmt(a.back(a.pred[1]))
        )
    if a.subgroup_test:
        t = a.subgroup_test
        named = ", ".join(
            "%s %s" % (r.name, fmt(a.back(r.pooled.estimate)))
            for r in a.subgroups if r.name in t["groups"]
        )
        en += " In subgroup analysis (%s), the between-subgroup difference was %s (Q_between(%d) = %s, p %s)." % (
            named,
            "significant" if t["p"] < 0.05 else "not significant",
            t["df"], fmt(t["q_between"], 2), fmt_p(t["p"]),
        )
    if a.loo:
        flipped = [r.omitted for r in a.loo if (r.p < 0.05) != (p.p < 0.05)]
        en += " Leave-one-out sensitivity analysis gave pooled estimates ranging from %s to %s, and %s" % (
            fmt(a.back(min(r.estimate for r in a.loo))),
            fmt(a.back(max(r.estimate for r in a.loo))),
            ("omitting %s changed the significance of the conclusion."
             % ", ".join(flipped[:3])) if flipped
            else "the conclusion was unchanged by omitting any single study.",
        )
    if a.egger and a.egger.k >= 10:
        en += " Egger's regression intercept was %s (p %s)." % (
            fmt(a.egger.intercept, 2), fmt_p(a.egger.p))
    elif a.egger:
        en += (
            " With fewer than 10 studies (k = %d), funnel-plot asymmetry was not formally assessed."
            % a.egger.k
        )
    if a.is_log:
        en += " (Pooling was performed on the log scale; τ² is reported on the log scale.)"
    if a.outcome:
        en = en.replace("yielded a pooled", "yielded, for %s, a pooled" % a.outcome, 1)
    else:
        en += " [insert the outcome measure and comparator]"
    return ko, en


def _i2_verdict(i2: float) -> str:
    if i2 < 25:
        return "낮음"
    if i2 < 50:
        return "중간 이하"
    if i2 < 75:
        return "상당함"
    return "매우 큼"


# --------------------------------------------------------------------------
# 텍스트 리포트
# --------------------------------------------------------------------------


def render_text(a: Analysis, show_forest: bool = True) -> str:
    lines: List[str] = []
    add = lines.append
    title = "메타분석 결과 — %s" % measure_label(a.measure)
    add("=" * 78)
    add(title)
    add("=" * 78)
    add("입력 파일   : %s" % (a.source or "(표준입력)"))
    add("연구 수 (k) : %d%s" % (len(a.studies), "" if a.total_n is None else "   총 표본 N = %s" % format(int(a.total_n), ",")))
    add("주 모형     : %s (tau² 추정: %s%s)" % (
        "변량효과 random-effects" if a.primary_model == "random" else "고정효과 fixed-effect",
        a.random.tau2_method,
        ", Hartung–Knapp 보정 CI" if a.random.ci_method == "HK" else "",
    ))
    if a.is_log:
        add("척도        : log(%s)에서 합성하고 %s로 되돌려 보고합니다." % (a.measure.upper(), a.measure.upper()))
    add("")

    if show_forest:
        add("── 개별 연구와 통합 효과 " + "─" * 40)
        lines.extend(forest_plot(a))
        add("")
    else:
        add("── 개별 연구 " + "─" * 50)
        for s, w in zip(a.studies, a.primary.weight_percent):
            cl, ch = s.ci(a._z)
            add("  %s  %s %s  가중치 %s" % (
                _pad(_truncate(s.label, 24), 24), fmt(a.back(s.yi)),
                _ci(a.back(cl), a.back(ch)), _pct(w)))
        add("")

    add("── 통합 효과 " + "─" * 50)
    for p, name in ((a.fixed, "고정효과 (fixed)"), (a.random, "변량효과 (random)")):
        stat_name = "t(%g)" % p.df if p.ci_method == "HK" else "z"
        add("  %s : %s  %d%% CI %s   %s = %s, p %s" % (
            _pad(name, 18), fmt(a.back(p.estimate)), round(a.conf * 100),
            _ci(a.back(p.ci_low), a.back(p.ci_high)), stat_name, fmt(p.stat, 2), fmt_p(p.p)))
    if a.pred:
        add("  %s : %s   (다음 연구 1편의 참효과가 놓일 범위)" % (
            _pad("%d%% 예측구간" % round(a.conf * 100), 18),
            _ci(a.back(a.pred[0]), a.back(a.pred[1]))))
    add("")

    add("── 이질성 " + "─" * 53)
    add("  Q(%d) = %s, p %s" % (a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p)))
    add("  I² = %s (%s)   H² = %s   τ² = %s (τ = %s, %s)" % (
        _pct(a.het.i2), _i2_verdict(a.het.i2), fmt(a.het.h2, 2),
        fmt(a.het.tau2), fmt(a.het.tau), a.het.tau2_method))
    if a.is_log:
        add("  (τ²·τ는 로그 척도 값입니다)")
    add("")

    if a.subgroups:
        add("── 하위군 분석 " + "─" * 48)
        for r in a.subgroups:
            add("  %s (k=%d) : %s  %s%s" % (
                _pad(_truncate(r.name, 20), 20), r.k, fmt(a.back(r.pooled.estimate)),
                _ci(a.back(r.pooled.ci_low), a.back(r.pooled.ci_high)),
                "   I² = %s" % _pct(r.het.i2) if r.het else "",
            ))
        if a.subgroup_test:
            t = a.subgroup_test
            add("  하위군 간 차이: Q_between(%d) = %s, p %s → %s" % (
                t["df"], fmt(t["q_between"], 2), fmt_p(t["p"]),
                "하위군 간 효과 차이가 유의함" if t["p"] < 0.05 else "하위군 간 유의한 차이 없음"))
        else:
            add("  (비교 가능한 하위군이 2개 미만이라 하위군 간 검정은 생략)")
        add("")

    if a.loo:
        add("── 민감도: 하나씩 제외 (leave-one-out, 변량효과) " + "─" * 15)
        ests = [r.estimate for r in a.loo]
        add("  제외했을 때 통합값 범위: %s ~ %s (전체: %s)" % (
            fmt(a.back(min(ests))), fmt(a.back(max(ests))), fmt(a.back(a.random.estimate))))
        flip = [r for r in a.loo if (r.p < 0.05) != (a.random.p < 0.05)]
        for r in a.loo:
            add("  %s 제외 → %s %s, p %s%s" % (
                _pad(_truncate(r.omitted, 22), 22), fmt(a.back(r.estimate)),
                _ci(a.back(r.ci_low), a.back(r.ci_high)), fmt_p(r.p),
                "   ← 결론이 바뀜" if r in flip else ""))
        if flip:
            add("  ⚠ 한 편을 빼는 것만으로 유의성 결론이 바뀝니다 — 결과가 특정 연구에 의존합니다.")
        add("")

    if a.egger:
        add("── 출판편향 / 소규모연구 효과 " + "─" * 34)
        add("  Egger 회귀 절편 = %s (SE %s), t(%d) = %s, p %s" % (
            fmt(a.egger.intercept, 2), fmt(a.egger.se, 2), a.egger.df,
            fmt(a.egger.t, 2), fmt_p(a.egger.p)))
        add("  → %s" % ("깔때기그림 비대칭의 근거가 있습니다 (p < .05)." if a.egger.p < 0.05
                        else "비대칭의 뚜렷한 근거는 없습니다."))
        if a.egger.k < 10:
            add("  ⚠ 연구가 %d편(<10)이라 이 검정의 검정력은 낮습니다. Cochrane은 10편 미만에서 시행을 권하지 않습니다."
                % a.egger.k)
        add("")

    ko, en = sentences(a)
    add("── 논문에 붙일 문장 " + "─" * 44)
    add("  [KO] " + ko)
    add("")
    add("  [EN] " + en)
    add("")

    if a.warnings:
        add("── 경고 " + "─" * 55)
        for w in a.warnings:
            add("  ⚠ " + w)
        add("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 마크다운 리포트
# --------------------------------------------------------------------------


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|")


def render_markdown(a: Analysis) -> str:
    out: List[str] = []
    add = out.append
    add("# 메타분석 결과 — %s" % measure_label(a.measure))
    add("")
    add("- 입력: `%s`" % (a.source or "(표준입력)"))
    add("- 연구 수 k = %d%s" % (len(a.studies), "" if a.total_n is None else ", 총 N = %s" % format(int(a.total_n), ",")))
    add("- 모형: %s, τ² 추정 %s%s" % (
        "변량효과" if a.primary_model == "random" else "고정효과",
        a.random.tau2_method,
        ", Hartung–Knapp CI" if a.random.ci_method == "HK" else ""))
    add("")
    add("## 개별 연구")
    add("")
    add("| 연구 | 효과 | %d%% CI | 가중치(%%) |" % round(a.conf * 100))
    add("|---|---:|---:|---:|")
    for s, w in zip(a.studies, a.primary.weight_percent):
        cl, ch = s.ci(a._z)
        add("| %s | %s | %s | %s |" % (
            _md_escape(s.label), fmt(a.back(s.yi)), _ci(a.back(cl), a.back(ch)), "%.1f" % w))
    add("")
    add("## 통합 효과")
    add("")
    add("| 모형 | 추정치 | %d%% CI | 검정통계량 | p |" % round(a.conf * 100))
    add("|---|---:|---:|---:|---:|")
    for p, name in ((a.fixed, "고정효과"), (a.random, "변량효과")):
        stat_name = "t(%g)" % p.df if p.ci_method == "HK" else "z"
        add("| %s | %s | %s | %s = %s | %s |" % (
            name, fmt(a.back(p.estimate)), _ci(a.back(p.ci_low), a.back(p.ci_high)),
            stat_name, fmt(p.stat, 2), fmt_p(p.p)))
    if a.pred:
        add("| %d%% 예측구간 | — | %s | — | — |" % (
            round(a.conf * 100), _ci(a.back(a.pred[0]), a.back(a.pred[1]))))
    add("")
    add("## 이질성")
    add("")
    add("Q(%d) = %s, p %s, I² = %s, H² = %s, τ² = %s (%s)" % (
        a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p), _pct(a.het.i2),
        fmt(a.het.h2, 2), fmt(a.het.tau2), a.het.tau2_method))
    add("")
    if a.subgroups:
        add("## 하위군")
        add("")
        add("| 하위군 | k | 추정치 | %d%% CI | I² |" % round(a.conf * 100))
        add("|---|---:|---:|---:|---:|")
        for r in a.subgroups:
            add("| %s | %d | %s | %s | %s |" % (
                _md_escape(r.name), r.k, fmt(a.back(r.pooled.estimate)),
                _ci(a.back(r.pooled.ci_low), a.back(r.pooled.ci_high)),
                _pct(r.het.i2) if r.het else "—"))
        if a.subgroup_test:
            add("")
            add("하위군 간 차이: Q_between(%d) = %s, p %s" % (
                a.subgroup_test["df"], fmt(a.subgroup_test["q_between"], 2),
                fmt_p(a.subgroup_test["p"])))
        add("")
    if a.egger:
        add("## 출판편향")
        add("")
        add("Egger 절편 = %s, t(%d) = %s, p %s%s" % (
            fmt(a.egger.intercept, 2), a.egger.df, fmt(a.egger.t, 2), fmt_p(a.egger.p),
            " — 연구 10편 미만이라 검정력 낮음" if a.egger.k < 10 else ""))
        add("")
    ko, en = sentences(a)
    add("## 논문 문장")
    add("")
    add("**KO** " + ko)
    add("")
    add("**EN** " + en)
    add("")
    if a.warnings:
        add("## 경고")
        add("")
        for w in a.warnings:
            add("- " + w)
        add("")
    add("```")
    out.extend(forest_plot(a))
    add("```")
    add("")
    return "\n".join(out)
