"""사람이 읽는 리포트 · 텍스트 숲그림(forest plot) · 논문용 문장 생성."""

from __future__ import annotations

import math
import unicodedata
from typing import List, Optional, Sequence

from .analysis import Analysis
from .clinical import format_absolute
from .effects import measure_label

__all__ = ["render_text", "render_markdown", "render_csv", "forest_plot",
           "funnel_plot", "sentences"]

_PLOT_WIDTH = 41

#: 출판편향 관련 수치를 "논문에 붙일 문장"에 실어도 되는 최소 연구 수.
#: Cochrane 권고(10편)를 Egger·Begg·trim-and-fill 에 똑같이 적용한다 — 셋 다
#: 이보다 적으면 신뢰할 수 없는데, 한쪽만 빼면 정책이 어긋나 보인다.
_BIAS_MIN_K = 10


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


def _conf_pct(conf: float) -> str:
    """신뢰수준을 % 문자열로. 99.9% 를 '100%' 로 반올림하지 않는다.

    '100% CI' 는 (-inf, inf) 를 뜻하므로, 그대로 원고에 붙으면 명백한 오류가 된다.
    """
    value = 100.0 * conf
    for digits in (0, 1, 2, 3):
        text = "%.*f" % (digits, value)
        if float(text) < 100.0:
            return text
    return "%.4f" % value  # pragma: no cover - conf <= 0.999 이라 도달하지 않음


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

    null_pos = pos(0.0) if (a.has_null_line and lo <= 0.0 <= hi) else None

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

    legend = "숲그림 (%s■ 연구, ◆ 통합, ◇ 예측구간)" % (
        "│ 무효과선, " if null_pos is not None else "")
    header = "%s %s %s %s" % (
        _pad("연구", label_w),
        _pad("효과 [%s%% CI]" % _conf_pct(a.conf), 24),
        _pad("가중치", 7),
        legend,
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
                _pad("%s%% 예측구간" % _conf_pct(a.conf), label_w),
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
    note = {
        "log": "(가로축은 로그 척도, 눈금은 되돌린 값)",
        "fisherz": "(가로축은 Fisher z 척도, 눈금은 상관계수 r)",
        "logit": "(가로축은 logit 척도, 눈금은 비율)",
    }.get(a.scale)
    if note:
        rows.append(" " * (label_w + 34) + note)
    return rows


# --------------------------------------------------------------------------
# 깔때기그림 (funnel plot)
# --------------------------------------------------------------------------


def funnel_plot(analysis: Analysis, width: int = 57, height: int = 13) -> List[str]:
    r"""텍스트 깔때기그림.

    가로축 = 효과크기(분석 척도), 세로축 = 표준오차(위쪽이 정밀, 아래쪽이 부정밀).
    통합 추정치를 꼭짓점으로 하는 유사 95% 구간(mu ± 1.96·SE)을 ``\``/``/`` 로
    함께 그려, 점들이 좌우 대칭인지 눈으로 볼 수 있게 한다.
    비대칭이 곧 출판편향은 아니다 — 이질성·연구 질로도 생긴다.
    """
    a = analysis
    if not a.studies:  # pragma: no cover - run_analysis 가 이미 막는다
        return []
    ses = [s.sei for s in a.studies]
    se_max = max(ses)
    if not math.isfinite(se_max) or se_max <= 0:
        return []
    mu = a.primary.estimate
    z95 = 1.959963984540054
    half = z95 * se_max
    lo = min(min(s.yi for s in a.studies), mu - half)
    hi = max(max(s.yi for s in a.studies), mu + half)
    if not math.isfinite(hi - lo) or hi - lo <= 0:
        lo, hi = mu - 1.0, mu + 1.0
    pad = 0.05 * (hi - lo)
    lo, hi = lo - pad, hi + pad

    def col(x: float) -> int:
        return max(0, min(width - 1, int(round((x - lo) / (hi - lo) * (width - 1)))))

    grid = [[" "] * width for _ in range(height)]
    # 유사 95% 신뢰 경계선
    for r in range(height):
        # 점(위)·축 라벨과 같은 정의를 써야 경계선이 반 칸 어긋나지 않는다.
        se_r = se_max * r / max(1, height - 1)
        for edge in (mu - z95 * se_r, mu + z95 * se_r):
            c = col(edge)
            if grid[r][c] == " ":
                grid[r][c] = "\\" if edge < mu else "/"
    c_mu = col(mu)
    for r in range(height):
        if grid[r][c_mu] == " ":
            grid[r][c_mu] = "┆"
    # 연구 점 — 겹치면 숫자로 표시
    counts = {}
    for s in a.studies:
        r = min(height - 1, int(s.sei / se_max * (height - 1) + 0.5)) if se_max > 0 else 0
        c = col(s.yi)
        counts[(r, c)] = counts.get((r, c), 0) + 1
    for (r, c), n in counts.items():
        grid[r][c] = "o" if n == 1 else (str(n) if n < 10 else "*")

    pad_left = 9
    rows = ["  깔때기그림 (o 연구, ┆ 통합 추정치, \\ / 유사 95% 구간)"]
    for r, line in enumerate(grid):
        se_label = se_max * r / max(1, height - 1)
        rows.append("%s│%s" % (("SE %.3f" % se_label).rjust(pad_left - 1), "".join(line)))
    rows.append(" " * (pad_left - 1) + "└" + "─" * width)
    left, right = fmt(a.back(lo), 2), fmt(a.back(hi), 2)
    rows.append(" " * pad_left + _pad(left, width // 2) + right.rjust(width - width // 2))
    return rows


# --------------------------------------------------------------------------
# 논문용 문장
# --------------------------------------------------------------------------



def _plural(n: int, one: str, many: str) -> str:
    """영어 문장이 'study(ies)' 처럼 보이지 않도록 단·복수를 고른다."""
    return one if n == 1 else many


def _josa(word: str, with_final: str, without_final: str) -> str:
    """한국어 조사 선택 — 앞 글자의 받침 유무로 은/는, 이/가 를 고른다.

    "비율는" 같은 어색한 문장이 그대로 원고에 붙는 것을 막는다.
    한글이 아닌 글자로 끝나면(숫자·영문) 받침 없음으로 본다.
    """
    if not word:
        return word
    ch = word[-1]
    has_final = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0
    return word + (with_final if has_final else without_final)


def _scale_ko(a: Analysis) -> str:
    return {"log": "로그", "fisherz": "Fisher z", "logit": "logit"}.get(a.scale, "원")


def _scale_en(a: Analysis) -> str:
    return {"log": "log", "fisherz": "Fisher z", "logit": "logit"}.get(a.scale, "raw")


def _i2_ci_text(a: Analysis) -> str:
    """I² 신뢰구간을 문장에 끼워 넣을 조각 (없으면 빈 문자열)."""
    if not a.het.i2_ci:
        return ""
    return " (%s%% CI %s ~ %s)" % (
        _conf_pct(a.het.ci_conf), _pct(a.het.i2_ci[0]), _pct(a.het.i2_ci[1]))


def sentences(a: Analysis, escape=None) -> "tuple[str, str]":
    """한국어·영어 결과 문장 (그대로 원고에 붙여 쓰고 숫자만 확인하면 되도록).

    ``escape`` 를 주면 문장에 끼워 넣는 **사용자 입력(연구명·하위군명)** 에만
    적용한다. 마크다운으로 내보낼 때 ``[클릭](javascript:...)`` 같은 연구명이
    링크로 렌더링되지 않게 하려는 것이며, 숫자와 고정 문구는 건드리지 않는다.

    하위군 차이와 민감도 분석처럼 **심사자가 반드시 묻는 내용**을 함께 넣고,
    도구 스스로 "믿지 말라"고 경고한 검정(연구 10편 미만의 Egger)은 결과 문장에
    수치로 싣지 않는다 — 그대로 붙여 넣으면 심사에서 지적받기 때문이다.
    """
    esc = escape or (lambda s: s)
    p = a.primary
    if p.k < 3:
        msg_ko = "유효한 연구가 %d편뿐이라 메타분석 결과 문장을 만들지 않습니다 (통합 추정이 의미를 갖기 어렵습니다)." % p.k
        msg_en = (
            "Only %d %s available — no pooled results paragraph is generated, "
            "because pooling is not meaningful at this size." % (p.k, _plural(p.k, "study", "studies"))
        )
        return msg_ko, msg_en
    name_en, name_ko = {
        "smd": ("Hedges' g", "표준화 평균차"),
        "md": ("mean difference", "평균차"),
        "or": ("odds ratio", "오즈비"),
        "rr": ("risk ratio", "위험비"),
        "rd": ("risk difference", "위험차"),
        "cor": ("correlation", "상관계수"),
        "prop": ("proportion", "비율"),
        "generic": ("effect size", "통합 효과크기"),
    }[a.measure]
    est = a.back(p.estimate)
    cl, ch = a.back(p.ci_low), a.back(p.ci_high)
    model_ko = "변량효과" if p.model == "random" else "고정효과"
    model_en = "random-effects" if p.model == "random" else "fixed-effect"
    n_txt = "" if a.total_n is None else "(총 %s명)" % format(int(a.total_n), ",")
    stat_name = "t(%g)" % p.df if p.ci_method == "HK" else "z"
    if a.has_null_line:
        sig_ko = "로 %s" % ("통계적으로 유의하였다" if p.p < 0.05 else "통계적으로 유의하지 않았다")
        sig_en = "was %s" % ("statistically significant" if p.p < 0.05
                             else "not statistically significant")
        stat_ko = ", %s = %s, p %s" % (stat_name, fmt(p.stat, 2), fmt_p(p.p))
        stat_en = ", %s = %s, p %s" % (stat_name, fmt(p.stat, 2), fmt_p(p.p))
    else:
        # 단일군 비율에는 "효과 없음"이 없다 (logit 0 = 50%) — 유의성 검정을 싣지 않는다.
        sig_ko = "로 추정되었다 (단일군 비율이므로 무효과 검정은 보고하지 않는다)"
        sig_en = "is a single-arm proportion, for which no null-hypothesis test applies"
        stat_ko = stat_en = ""
    ci_note_ko = " Hartung–Knapp 보정을 적용하였다." if p.ci_method == "HK" else ""
    ci_note_en = " Hartung–Knapp adjustment was applied." if p.ci_method == "HK" else ""

    ko = (
        "%s 모형으로 %d편의 연구%s를 통합한 결과, %s %s (%s%% CI %s ~ %s%s)%s. "
        "연구 간 이질성은 Q(%d) = %s, p %s, I² = %s%s, τ² = %s이었다.%s"
        % (
            model_ko, p.k, n_txt, _josa(name_ko, "은", "는"), fmt(est),
            _conf_pct(a.conf), fmt(cl), fmt(ch), stat_ko, sig_ko,
            a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p), _pct(a.het.i2), _i2_ci_text(a),
            fmt(a.het.tau2), ci_note_ko,
        )
    )
    if a.pred:
        ko += " %s%% 예측구간은 %s ~ %s이었다." % (
            _conf_pct(a.conf), fmt(a.back(a.pred[0])), fmt(a.back(a.pred[1])))
    if a.subgroup_test:
        t = a.subgroup_test
        named = ", ".join(
            "%s %s" % (esc(r.name), fmt(a.back(r.pooled.estimate)))
            for r in a.subgroups if r.name in t["groups"]
        )
        ko += " 하위군 분석에서 %s로, 하위군 간 차이는 %s(Q_between(%d) = %s, p %s)." % (
            named,
            "유의하였다" if t["p"] < 0.05 else "유의하지 않았다",
            t["df"], fmt(t["q_between"], 2), fmt_p(t["p"]),
        )
    if a.loo:
        flipped = ([r.omitted for r in a.loo if (r.p < 0.05) != (p.p < 0.05)]
                   if a.has_null_line else [])
        ko += (
            " 연구를 하나씩 제외한 민감도 분석에서 통합 추정치는 %s ~ %s 범위였으며, %s"
            % (
                fmt(a.back(min(r.estimate for r in a.loo))),
                fmt(a.back(max(r.estimate for r in a.loo))),
                ("'%s'을(를) 제외하면 유의성 결론이 달라졌다." % ", ".join(esc(f) for f in flipped[:3]))
                if flipped else "어느 한 편을 제외해도 결론은 유지되었다.",
            )
        )
    if a.egger and a.egger.k >= 10:
        ko += " Egger 회귀 비대칭 검정에서 절편은 %s (p %s)이었다." % (
            fmt(a.egger.intercept, 2), fmt_p(a.egger.p))
    elif a.egger:
        ko += " 연구가 %d편(<10)이어서 깔때기그림 비대칭은 형식적으로 평가하지 않았다." % a.egger.k
    if a.absolute:
        ab = a.absolute
        ko += (
            " 가정 대조군 위험 %.1f%%를 적용하면 절대 위험차는 %+.1f%%p(1000명당 %+.0f명)이며, %s는 약 %s이다."
            % (100.0 * ab.baseline_risk, 100.0 * ab.risk_diff, ab.per_1000,
               "NNH" if ab.is_harm else "NNT",
               "정의되지 않음" if ab.nnt is None else "%.0f" % ab.nnt)
        )
    if a.trimfill and a.trimfill.k0 > 0 and p.k >= _BIAS_MIN_K:
        ko += (
            " Duval–Tweedie trim-and-fill 로 %s쪽에 %d편을 채워 넣으면 통합 추정치는 %s(%s%% CI %s ~ %s)로 이동하였다."
            % ("왼" if a.trimfill.side == "left" else "오른", a.trimfill.k0,
               fmt(a.back(a.trimfill.adjusted.estimate)), _conf_pct(a.conf),
               fmt(a.back(a.trimfill.adjusted.ci_low)), fmt(a.back(a.trimfill.adjusted.ci_high)))
        )
    if a.is_transformed:
        ko += " (합성은 %s 척도에서 수행하였고, τ²는 %s 척도 값이다.)" % (_scale_ko(a), _scale_ko(a))
    if a.outcome:
        ko = ko.replace("통합한 결과,", "통합한 결과, %s에 대한" % a.outcome, 1)
    else:
        ko += " [결과변수명·비교대상을 문장에 채워 넣으세요]"

    en = (
        "A %s meta-analysis of %d %s%s yielded a pooled %s of %s (%s%% CI %s to %s%s), "
        "which %s. Between-study heterogeneity was Q(%d) = %s, p %s, I² = %s%s, τ² = %s.%s"
        % (
            model_en, p.k, _plural(p.k, "study", "studies"),
            "" if a.total_n is None else " (N = %s)" % format(int(a.total_n), ","),
            name_en, fmt(est), _conf_pct(a.conf), fmt(cl), fmt(ch), stat_en, sig_en,
            a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p), _pct(a.het.i2), _i2_ci_text(a),
            fmt(a.het.tau2), ci_note_en,
        )
    )
    if a.pred:
        en += " The %s%% prediction interval ranged from %s to %s." % (
            _conf_pct(a.conf), fmt(a.back(a.pred[0])), fmt(a.back(a.pred[1]))
        )
    if a.subgroup_test:
        t = a.subgroup_test
        named = ", ".join(
            "%s %s" % (esc(r.name), fmt(a.back(r.pooled.estimate)))
            for r in a.subgroups if r.name in t["groups"]
        )
        en += " In subgroup analysis (%s), the between-subgroup difference was %s (Q_between(%d) = %s, p %s)." % (
            named,
            "significant" if t["p"] < 0.05 else "not significant",
            t["df"], fmt(t["q_between"], 2), fmt_p(t["p"]),
        )
    if a.loo:
        flipped = ([r.omitted for r in a.loo if (r.p < 0.05) != (p.p < 0.05)]
                   if a.has_null_line else [])
        en += " Leave-one-out sensitivity analysis gave pooled estimates ranging from %s to %s, and %s" % (
            fmt(a.back(min(r.estimate for r in a.loo))),
            fmt(a.back(max(r.estimate for r in a.loo))),
            ("omitting %s changed the significance of the conclusion."
             % ", ".join(esc(f) for f in flipped[:3])) if flipped
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
    if a.absolute:
        ab = a.absolute
        en += (
            " Assuming a control risk of %.1f%%, the absolute risk difference was %+.1f%% "
            "(%+.0f per 1000), corresponding to an %s of about %s."
            % (100.0 * ab.baseline_risk, 100.0 * ab.risk_diff, ab.per_1000,
               "NNH" if ab.is_harm else "NNT",
               "undefined" if ab.nnt is None else "%.0f" % ab.nnt)
        )
    if a.trimfill and a.trimfill.k0 > 0 and p.k >= _BIAS_MIN_K:
        en += (
            " Duval–Tweedie trim-and-fill imputed %d %s on the %s, shifting the pooled "
            "estimate to %s (%s%% CI %s to %s)."
            % (a.trimfill.k0, _plural(a.trimfill.k0, "study", "studies"), a.trimfill.side,
               fmt(a.back(a.trimfill.adjusted.estimate)),
               _conf_pct(a.conf), fmt(a.back(a.trimfill.adjusted.ci_low)),
               fmt(a.back(a.trimfill.adjusted.ci_high)))
        )
    if a.is_transformed:
        en += " (Pooling was performed on the %s scale; τ² is reported on that scale.)" % _scale_en(a)
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


def render_text(a: Analysis, show_forest: bool = True, show_funnel: bool = True) -> str:
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
        # 단일군 비율에는 무효과값이 없다 — logit = 0 (=50%) 검정은 의미가 없으므로
        # 가장 눈에 띄는 자리에서 아예 뺀다 (그대로 베껴 실리는 사고를 막는다).
        stat_txt = ("   %s = %s, p %s" % (stat_name, fmt(p.stat, 2), fmt_p(p.p))
                    if a.has_null_line else "")
        add("  %s : %s  %s%% CI %s%s" % (
            _pad(name, 18), fmt(a.back(p.estimate)), _conf_pct(a.conf),
            _ci(a.back(p.ci_low), a.back(p.ci_high)), stat_txt))
    if not a.has_null_line:
        add("  (단일군 비율에는 '효과 없음' 값이 없어 검정통계량·p값을 보고하지 않습니다 — "
            "logit 0 은 50%일 뿐입니다.)")
    if a.pred:
        add("  %s : %s   (다음 연구 1편의 참효과가 놓일 범위)" % (
            _pad("%s%% 예측구간" % _conf_pct(a.conf), 18),
            _ci(a.back(a.pred[0]), a.back(a.pred[1]))))
    add("")

    if a.absolute:
        add("── 절대효과 · NNT " + "─" * 45)
        for line in format_absolute(a.absolute):
            add("  " + line)
        add("  (가정 대조군 위험은 --baseline-risk 로 바꿀 수 있습니다.)")
        add("")

    add("── 이질성 " + "─" * 53)
    add("  Q(%d) = %s, p %s" % (a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p)))
    add("  I² = %s (%s)   H² = %s   τ² = %s (τ = %s, %s)" % (
        _pct(a.het.i2), _i2_verdict(a.het.i2), fmt(a.het.h2, 2),
        fmt(a.het.tau2), fmt(a.het.tau), a.het.tau2_method))
    if a.het.i2_ci and a.het.tau2_ci:
        add("  %s%% CI (Q-profile): I² %s ~ %s,  τ² %s ~ %s" % (
            _conf_pct(a.het.ci_conf), _pct(a.het.i2_ci[0]), _pct(a.het.i2_ci[1]),
            fmt(a.het.tau2_ci[0]), fmt(a.het.tau2_ci[1])))
    if a.is_transformed:
        add("  (τ²·τ는 %s 척도 값입니다)" % _scale_ko(a))
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
        flip = ([r for r in a.loo if (r.p < 0.05) != (a.random.p < 0.05)]
                if a.has_null_line else [])
        for r in a.loo:
            marks = []
            if r in flip:
                marks.append("← 결론이 바뀜")
            if r.std_resid is not None and abs(r.std_resid) > 2.0:
                marks.append("← 이상치 후보 (표준화 잔차 %s)" % fmt(r.std_resid, 2))
            add("  %s 제외 → %s %s, p %s%s%s" % (
                _pad(_truncate(r.omitted, 22), 22), fmt(a.back(r.estimate)),
                _ci(a.back(r.ci_low), a.back(r.ci_high)), fmt_p(r.p),
                "   I² %s" % _pct(r.i2) if r.i2 is not None else "",
                "   " + " ".join(marks) if marks else ""))
        outliers = [r for r in a.loo if r.std_resid is not None and abs(r.std_resid) > 2.0]
        if outliers:
            add("  ⚠ 표준화 잔차 |값| > 2 인 연구가 있습니다 — 나머지 연구와 잘 맞지 않는다는 뜻입니다: %s"
                % ", ".join(_truncate(r.omitted, 22) for r in outliers[:5]))
        if flip:
            add("  ⚠ 한 편을 빼는 것만으로 유의성 결론이 바뀝니다 — 결과가 특정 연구에 의존합니다.")
        add("")

    if a.egger or a.begg or a.trimfill:
        add("── 출판편향 / 소규모연구 효과 " + "─" * 34)
        if a.egger:
            add("  Egger 회귀 절편 = %s (SE %s), t(%d) = %s, p %s" % (
                fmt(a.egger.intercept, 2), fmt(a.egger.se, 2), a.egger.df,
                fmt(a.egger.t, 2), fmt_p(a.egger.p)))
        else:
            add("  Egger 회귀: 계산할 수 없습니다 (모든 연구의 정밀도가 같거나 잔차가 0이라 "
                "절편을 식별할 수 없습니다).")
        if a.egger and a.measure == "prop":
            add("  → 이 지표에서는 비대칭 여부를 해석하지 않습니다 (아래 사유).")
        elif a.egger:
            add("  → %s" % ("깔때기그림 비대칭의 근거가 있습니다 (p < .05)." if a.egger.p < 0.05
                            else "비대칭의 뚜렷한 근거는 없습니다."))
        if a.begg:
            add("  Begg 순위상관   τ = %s, z = %s, p %s  (%s)" % (
                fmt(a.begg.tau, 2), fmt(a.begg.z, 2), fmt_p(a.begg.p),
                "순열 정확검정" if a.begg.method == "exact"
                else "정규근사 — 동점이 있어 p가 실제보다 작을 수 있음"))
        k_bias = len(a.studies)
        if k_bias < _BIAS_MIN_K:
            add("  ⚠ 연구가 %d편(<%d)이라 이 검정들의 검정력은 낮습니다. Cochrane은 10편 미만에서 "
                "시행을 권하지 않습니다 — 논문 문장에도 수치를 싣지 않습니다." % (k_bias, _BIAS_MIN_K))
        if a.measure == "prop":
            add("  ⚠ 단일군 비율은 분산이 효과크기의 함수라 깔때기그림이 구조적으로 비대칭해집니다 "
                "— 아래 수치를 출판편향의 근거로 쓰지 마세요.")
        elif a.measure in ("or", "rr"):
            add("  ⚠ 이분형 지표에서는 효과크기와 표준오차의 구조적 상관 때문에 Egger 위양성이 잦습니다.")
        if a.trimfill:
            tf = a.trimfill
            if tf.k0 > 0:
                add("  trim-and-fill(%s): %s쪽에 %d편을 채우면 통합값 %s → %s %s"
                    % (tf.estimator, "왼" if tf.side == "left" else "오른", tf.k0,
                       fmt(a.back(a.random.estimate)), fmt(a.back(tf.adjusted.estimate)),
                       _ci(a.back(tf.adjusted.ci_low), a.back(tf.adjusted.ci_high))))
                add("    → 보정 결과는 \"출판편향이 있었다면 이 정도\"라는 민감도 분석입니다. "
                    "이질성이 크면 k0을 과대추정합니다.")
            else:
                add("  trim-and-fill(%s): 채워 넣을 연구 없음 (k0 = 0) — 대칭에서 벗어난 근거 없음"
                    % tf.estimator)
        if show_funnel:
            add("")
            lines.extend(funnel_plot(a))
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


_MD_SPECIAL = str.maketrans({c: "\\" + c for c in "|[]()*_`<>!#"})


def _md_escape(text: str) -> str:
    """마크다운에 실리는 사용자 입력(연구명·하위군명·경고)을 무해화한다.

    연구명은 CSV 에서 온 값이라 ``[클릭](javascript:...)`` 나 ``![](x)`` 가
    그대로 링크·이미지로 렌더링될 수 있고, ``|`` 는 표를 깨뜨린다.
    보이는 글자는 그대로 두고 마크다운 의미만 없앤다.
    """
    return str(text).translate(_MD_SPECIAL)


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
    add("| 연구 | 효과 | %s%% CI | 가중치(%%) |" % _conf_pct(a.conf))
    add("|---|---:|---:|---:|")
    for s, w in zip(a.studies, a.primary.weight_percent):
        cl, ch = s.ci(a._z)
        add("| %s | %s | %s | %s |" % (
            _md_escape(s.label), fmt(a.back(s.yi)), _ci(a.back(cl), a.back(ch)), "%.1f" % w))
    add("")
    add("## 통합 효과")
    add("")
    add("| 모형 | 추정치 | %s%% CI | 검정통계량 | p |" % _conf_pct(a.conf))
    add("|---|---:|---:|---:|---:|")
    for p, name in ((a.fixed, "고정효과"), (a.random, "변량효과")):
        stat_name = "t(%g)" % p.df if p.ci_method == "HK" else "z"
        stat_cell, p_cell = ((("%s = %s" % (stat_name, fmt(p.stat, 2))), fmt_p(p.p))
                             if a.has_null_line else ("—", "—"))
        add("| %s | %s | %s | %s | %s |" % (
            name, fmt(a.back(p.estimate)), _ci(a.back(p.ci_low), a.back(p.ci_high)),
            stat_cell, p_cell))
    if a.pred:
        add("| %s%% 예측구간 | — | %s | — | — |" % (
            _conf_pct(a.conf), _ci(a.back(a.pred[0]), a.back(a.pred[1]))))
    if not a.has_null_line:
        add("")
        add("> 단일군 비율에는 '효과 없음' 값이 없어 검정통계량·p값을 보고하지 않습니다 "
            "(logit 0 = 50%).")
    add("")
    add("## 이질성")
    add("")
    add("Q(%d) = %s, p %s, I² = %s%s, H² = %s, τ² = %s%s (%s)" % (
        a.het.df, fmt(a.het.q, 2), fmt_p(a.het.p), _pct(a.het.i2), _i2_ci_text(a),
        fmt(a.het.h2, 2), fmt(a.het.tau2),
        " [%s, %s]" % (fmt(a.het.tau2_ci[0]), fmt(a.het.tau2_ci[1])) if a.het.tau2_ci else "",
        a.het.tau2_method))
    add("")
    if a.absolute:
        add("## 절대효과 · NNT")
        add("")
        for line in format_absolute(a.absolute):
            add("- " + line)
        add("")
    if a.subgroups:
        add("## 하위군")
        add("")
        add("| 하위군 | k | 추정치 | %s%% CI | I² |" % _conf_pct(a.conf))
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
    if a.egger or a.begg or a.trimfill:
        add("## 출판편향")
        add("")
        if a.egger:
            add("- Egger 절편 = %s, t(%d) = %s, p %s%s" % (
                fmt(a.egger.intercept, 2), a.egger.df, fmt(a.egger.t, 2), fmt_p(a.egger.p),
                " — 연구 10편 미만이라 검정력 낮음" if a.egger.k < _BIAS_MIN_K else ""))
        else:
            add("- Egger 절편: 계산 불가 (연구들의 정밀도가 모두 같거나 잔차가 0)")
        if a.begg:
            add("- Begg 순위상관 τ = %s, z = %s, p %s (%s)" % (
                fmt(a.begg.tau, 2), fmt(a.begg.z, 2), fmt_p(a.begg.p),
                "정확검정" if a.begg.method == "exact" else "정규근사"))
        if a.trimfill:
            tf = a.trimfill
            if tf.k0 > 0:
                add("- trim-and-fill(%s): %s쪽 %d편 보정 → %s %s (민감도 분석일 뿐, 참값 추정이 아님)" % (
                    tf.estimator, "왼" if tf.side == "left" else "오른", tf.k0,
                    fmt(a.back(tf.adjusted.estimate)),
                    _ci(a.back(tf.adjusted.ci_low), a.back(tf.adjusted.ci_high))))
            else:
                add("- trim-and-fill(%s): 채워 넣을 연구 없음 (k0 = 0)" % tf.estimator)
        add("")
    ko, en = sentences(a, escape=_md_escape)
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
            add("- " + _md_escape(w))
        add("")
    add("```")
    out.extend(forest_plot(a))
    if a.egger or a.begg or a.trimfill:
        out.append("")
        out.extend(funnel_plot(a))
    add("```")
    add("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# CSV 내보내기 (원고 표·다른 통계 도구로 넘길 때)
# --------------------------------------------------------------------------


def render_csv(a: Analysis) -> str:
    """연구별 행 + 통합/하위군/민감도 요약 행을 한 장의 tidy CSV 로 만든다.

    ``row_type`` 열로 행의 종류를 구분한다 (study / pooled / subgroup /
    leave_one_out / prediction / trim_and_fill). 값은 **보고 척도**(OR/RR이면
    지수변환한 값)로 적고, 분석 척도 값은 ``effect_analysis_scale`` 에 함께 넣는다.

    열의 의미가 행 종류마다 달라지지 않도록 한다:
    ``statistic``/``p_value`` 는 언제나 그 행의 통합값에 대한 z(또는 t)와 p 이고,
    leave-one-out 의 표준화 잔차는 별도의 ``std_residual`` 열에 들어간다.
    무효과값이 없는 지표(단일군 비율)에서는 ``statistic``/``p_value`` 를 비운다.
    """
    import csv as _csv
    import io as _io

    def cell(value):
        """엑셀이 수식으로 실행하는 선두 문자를 무력화한다.

        연구명·하위군명은 CSV 에서 그대로 들어오므로, ``=cmd|' /C calc'!A0`` 같은
        값이 그대로 나가면 이 표를 엑셀에서 여는 사람이 공격을 받는다.
        (OWASP CSV injection). 값 자체는 지우지 않고 앞에 아포스트로피만 붙인다.
        """
        text = "" if value is None else str(value)
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text

    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    conf_pct = _conf_pct(a.conf)
    w.writerow([
        "row_type", "label", "subgroup", "k", "effect", "ci_low", "ci_high",
        "effect_analysis_scale", "se_analysis_scale", "weight_fixed_pct",
        "weight_random_pct", "statistic", "p_value", "tau2", "I2_percent",
        "std_residual", "n_total",
    ])

    def num(v):
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return ""
        return repr(float(v))

    z = a._z
    for s, wf, wr in zip(a.studies, a.fixed.weight_percent, a.random.weight_percent):
        cl, ch = s.ci(z)
        w.writerow([
            "study", cell(s.label), cell(s.subgroup or ""), 1,
            num(a.back(s.yi)), num(a.back(cl)), num(a.back(ch)),
            num(s.yi), num(s.sei), num(wf), num(wr), "", "", "", "", "", num(s.n_total),
        ])
    for pooled, name in ((a.fixed, "fixed_effect"), (a.random, "random_effects")):
        w.writerow([
            "pooled", cell(name), "", pooled.k,
            num(a.back(pooled.estimate)), num(a.back(pooled.ci_low)), num(a.back(pooled.ci_high)),
            num(pooled.estimate), num(pooled.se), "", "",
            num(pooled.stat) if a.has_null_line else "",
            num(pooled.p) if a.has_null_line else "",
            num(pooled.tau2), num(a.het.i2), "", num(a.total_n),
        ])
    if a.pred:
        w.writerow([
            "prediction", "%s%% prediction interval" % conf_pct, "", a.random.k,
            "", num(a.back(a.pred[0])), num(a.back(a.pred[1])),
            "", "", "", "", "", "", "", "", "", "",
        ])
    for r in a.subgroups:
        w.writerow([
            "subgroup", cell(r.name), cell(r.name), r.k,
            num(a.back(r.pooled.estimate)), num(a.back(r.pooled.ci_low)),
            num(a.back(r.pooled.ci_high)), num(r.pooled.estimate), num(r.pooled.se),
            "", "",
            num(r.pooled.stat) if a.has_null_line else "",
            num(r.pooled.p) if a.has_null_line else "",
            num(r.pooled.tau2), num(r.het.i2) if r.het else "", "", "",
        ])
    for r in a.loo:
        w.writerow([
            "leave_one_out", cell(r.omitted), "", a.random.k - 1,
            num(a.back(r.estimate)), num(a.back(r.ci_low)), num(a.back(r.ci_high)),
            num(r.estimate), "", "", "", "",
            num(r.p) if a.has_null_line else "",
            num(r.tau2), num(r.i2), num(r.std_resid), "",
        ])
    if a.trimfill and a.trimfill.k0 > 0:
        tf = a.trimfill
        w.writerow([
            "trim_and_fill", "adjusted (%s, k0=%d, %s)" % (tf.estimator, tf.k0, tf.side), "",
            tf.adjusted.k, num(a.back(tf.adjusted.estimate)), num(a.back(tf.adjusted.ci_low)),
            num(a.back(tf.adjusted.ci_high)), num(tf.adjusted.estimate), num(tf.adjusted.se),
            "", "",
            num(tf.adjusted.stat) if a.has_null_line else "",
            num(tf.adjusted.p) if a.has_null_line else "",
            num(tf.adjusted.tau2), "", "", "",
        ])
    return buf.getvalue().rstrip("\n")
