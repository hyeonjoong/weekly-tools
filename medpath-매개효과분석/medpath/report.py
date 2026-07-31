"""Human-readable and Markdown rendering of a mediation result.

Terminal tables are padded by *display* width (Korean/CJK glyphs occupy two
columns), so the output lines up in a real terminal rather than only in a
monospace-ASCII fantasy.
"""

from __future__ import annotations

import math
import unicodedata
from typing import List, Optional, Sequence, Tuple

from .mediation import Effect, MediationResult
from .model import Regression

__all__ = ["render", "fmt", "fmt_p", "display_width"]


def display_width(s: str) -> int:
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _pad(s: str, width: int, align: str = "l") -> str:
    gap = max(0, width - display_width(s))
    if align == "r":
        return " " * gap + s
    if align == "c":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def fmt(v: Optional[float], digits: int = 3) -> str:
    """Format a number for reporting: fixed decimals, or scientific if tiny/huge."""
    if v is None:
        return "—"
    if isinstance(v, float) and math.isnan(v):
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞" if v > 0 else "-∞"
    av = abs(v)
    if av != 0 and (av < 10 ** (-digits) or av >= 1e7):
        return "%.*e" % (max(1, digits - 1), v)
    return "%.*f" % (digits, v)


def fmt_p(p: Optional[float]) -> str:
    """APA-style p-value: leading zero dropped, '< .001' floor."""
    if p is None or (isinstance(p, float) and (math.isnan(p))):
        return "—"
    if p < 0.001:
        return "< .001"
    s = "%.3f" % p
    return "= " + (s[1:] if s.startswith("0") else s)


def _ci(lo: float, hi: float, digits: int = 3) -> str:
    return "[%s, %s]" % (fmt(lo, digits), fmt(hi, digits))


class _Out:
    """Collects lines in either plain-text or Markdown mode."""

    def __init__(self, mode: str = "text"):
        self.mode = mode
        self.lines: List[str] = []

    def title(self, text: str) -> None:
        if self.mode == "md":
            self.lines += ["# %s" % text, ""]
        else:
            bar = "=" * 74
            self.lines += [bar, "  " + text, bar]

    def section(self, text: str) -> None:
        if self.mode == "md":
            self.lines += ["", "## %s" % text, ""]
        else:
            self.lines += ["", "── %s %s" % (text, "─" * max(4, 66 - display_width(text)))]

    def sub(self, text: str) -> None:
        if self.mode == "md":
            self.lines += ["", "### %s" % text, ""]
        else:
            self.lines += ["", "  " + text]

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def bullet(self, text: str) -> None:
        self.lines.append(("- " if self.mode == "md" else "  · ") + text)

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[str]],
              align: Optional[Sequence[str]] = None, indent: str = "  ") -> None:
        if not rows:
            return
        align = list(align or ["l"] * len(headers))
        if self.mode == "md":
            self.lines.append("| " + " | ".join(headers) + " |")
            self.lines.append("|" + "|".join(
                {"l": ":---", "r": "---:", "c": ":---:"}[a] for a in align) + "|")
            for r in rows:
                self.lines.append("| " + " | ".join(str(c) for c in r) + " |")
            return
        widths = [display_width(h) for h in headers]
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = max(widths[i], display_width(str(c)))
        self.lines.append(indent + "  ".join(
            _pad(h, widths[i], "l" if align[i] == "l" else "c") for i, h in enumerate(headers)))
        self.lines.append(indent + "  ".join("-" * w for w in widths))
        for r in rows:
            self.lines.append(indent + "  ".join(
                _pad(str(c), widths[i], align[i]) for i, c in enumerate(r)))

    def text(self) -> str:
        return "\n".join(self.lines)


def _reg_block(out: _Out, reg: Regression, caption: str, digits: int,
               highlight: Sequence[str] = ()) -> None:
    fstat = ("F(%d, %d) = %s, p %s" % (reg.f_df[0], reg.f_df[1], fmt(reg.f, 2), fmt_p(reg.f_p))
             if math.isfinite(reg.f) else "F —")
    out.sub("%s   R² = %s, adj. R² = %s, %s, N = %d"
            % (caption, fmt(reg.r2, 3), fmt(reg.adj_r2, 3), fstat, reg.n))
    rows = []
    for c in reg.coefs:
        mark = " ←" if c.name in highlight else ""
        rows.append([c.name + mark, fmt(c.estimate, digits), fmt(c.se, digits),
                     fmt(c.t, 2), fmt_p(c.p), _ci(c.ci_lo, c.ci_hi, digits)])
    out.table(["항목", "계수", "SE", "t", "p", "95% CI"],
              rows, ["l", "r", "r", "r", "r", "r"], indent="    ")


def _effect_rows(effects: Sequence[Effect], digits: int) -> List[List[str]]:
    rows = []
    for e in effects:
        flag = ""
        if e.kind in ("indirect", "indirect_total"):
            flag = "0 미포함" if e.significant else ("0 포함" if math.isfinite(e.ci_lo) else "—")
        else:
            flag = "p %s" % fmt_p(e.p)
        rows.append([e.label, fmt(e.estimate, digits), fmt(e.se, digits),
                     _ci(e.ci_lo, e.ci_hi, digits), flag])
    return rows


def _apa_sentences(res: MediationResult, digits: int) -> Tuple[str, str]:
    d = res.design
    assert d is not None
    x, y = d.x_name, d.y_name
    inds = res.indirect_effects
    total = res.effect("total")
    direct = res.effect("direct")
    method = {"백분위 부트스트랩": "백분위", "편향보정(BC) 부트스트랩": "편향보정(BC)",
              "BCa 부트스트랩": "BCa"}.get(inds[0].ci_method if inds else "", "부트스트랩")
    method_en = {"백분위": "percentile", "편향보정(BC)": "bias-corrected",
                 "BCa": "bias-corrected and accelerated"}.get(method, "percentile")
    conf_pct = "%g" % (res.conf * 100)
    model_ko = "직렬 다중매개" if res.serial else ("단순매개" if len(inds) == 1 else "병렬 다중매개")
    model_en = ("serial multiple mediation" if res.serial else
                ("simple mediation" if len(inds) == 1 else "parallel multiple mediation"))

    ko = ["%s 모형으로 %s → %s 경로를 검정했다(N = %d, 부트스트랩 %s회, %s%% %s 신뢰구간)."
          % (model_ko, x, y, d.n_used, "{:,}".format(res.boot_ok), conf_pct, method)]
    en = ["A %s model was estimated for %s → %s (N = %d, %s bootstrap resamples, "
          "%s%% %s confidence intervals)."
          % (model_en, x, y, d.n_used, "{:,}".format(res.boot_ok), conf_pct, method_en)]
    for e in inds:
        chain = e.label.replace("간접효과 ", "")
        sig_ko = "유의하였다" if e.significant else "유의하지 않았다"
        sig_en = "significant" if e.significant else "not significant"
        ko.append("%s 경로의 간접효과는 %s(ab = %s, SE = %s, %s%% CI %s)."
                  % (chain, sig_ko, fmt(e.estimate, digits), fmt(e.se, digits),
                     conf_pct, _ci(e.ci_lo, e.ci_hi, digits)))
        en.append("The indirect effect through %s was %s, ab = %s, SE = %s, %s%% CI %s."
                  % (chain.replace(" → ", " -> "), sig_en, fmt(e.estimate, digits),
                     fmt(e.se, digits), conf_pct, _ci(e.ci_lo, e.ci_hi, digits)))
    if total and direct:
        ko.append("총효과는 c = %s (p %s), 직접효과는 c' = %s (p %s)였다."
                  % (fmt(total.estimate, digits), fmt_p(total.p),
                     fmt(direct.estimate, digits), fmt_p(direct.p)))
        en.append("The total effect was c = %s, p %s, and the direct effect was c' = %s, p %s."
                  % (fmt(total.estimate, digits), fmt_p(total.p),
                     fmt(direct.estimate, digits), fmt_p(direct.p)))
    return " ".join(ko), " ".join(en)


_CAUTION_KO = [
    "매개분석은 '상관'을 인과 경로처럼 배열한 회귀 모형입니다. 결과가 인과를 뜻하려면 "
    "X→M→Y의 시간적 선후가 실제로 보장되고, M–Y 사이에 측정되지 않은 교란변수가 없어야 합니다.",
    "같은 시점에 X·M·Y를 한꺼번에 측정한 횡단자료라면 간접효과는 '가설과 일치한다'는 정도로만 "
    "쓰고, 인과 표현은 피하세요.",
    "'완전매개/부분매개'라는 표현은 최근 방법론 문헌에서 권장되지 않습니다. 직접효과의 유의성은 "
    "검정력에 크게 좌우되므로, 간접효과 자체의 크기와 구간을 보고하세요.",
    "간접효과 구간은 부트스트랩이 기준입니다. Sobel z는 곱(ab)의 분포를 정규로 가정하므로 "
    "참고용으로만 병기했습니다.",
]


def render(res: MediationResult, source: str, mode: str = "text",
           digits: int = 3, brief: bool = False) -> str:
    """Render the full report as plain text (``mode='text'``) or Markdown."""
    d = res.design
    assert d is not None
    out = _Out(mode)
    out.title("medpath — 매개효과(간접효과) 분석")

    model_ko = "직렬(serial) 다중매개" if res.serial else (
        "단순매개" if len(d.mediators) == 1 else "병렬(parallel) 다중매개")
    out.line()
    out.bullet("데이터: %s" % source)
    out.bullet("모형: %s" % model_ko)
    out.bullet("X (독립): %s" % d.x_label)
    sep = " → " if res.serial else ", "
    out.bullet("M (매개): %s" % sep.join(nm for nm, _ in d.mediators))
    out.bullet("Y (종속): %s" % d.y_name)
    if d.covariates:
        out.bullet("공변량: %s" % ", ".join(nm for nm, _ in d.covariates))
    for note in d.covariate_notes:
        out.bullet("  " + note)
    out.bullet("표본: 파일 %d행 → 분석 %d행%s"
               % (d.n_total, d.n_used,
                  " (결측 등으로 %d행 제외)" % (d.n_total - d.n_used)
                  if d.n_total != d.n_used else ""))
    out.bullet("설정: %g%% CI · %s %s회(성공 %s) · seed=%d · 표준오차=%s"
               % (res.conf * 100,
                  {"percentile": "백분위 부트스트랩", "bc": "BC 부트스트랩",
                   "bca": "BCa 부트스트랩"}[res.ci_method],
                  "{:,}".format(res.n_boot), "{:,}".format(res.boot_ok), res.seed,
                  "HC3(이분산 강건)" if res.robust == "hc3" else "고전적"))

    if d.missing_by_column and not brief:
        out.section("결측 현황 (분석 전)")
        out.table(["열", "결측/비숫자 행 수"],
                  [[c, str(n)] for c, n in d.missing_by_column], ["l", "r"])

    # --- effects ---------------------------------------------------------
    out.section("효과 요약")
    out.table(["효과", "추정치", "SE", "%g%% CI" % (res.conf * 100), "판정"],
              _effect_rows(res.effects, digits), ["l", "r", "r", "r", "l"])
    out.line()
    if math.isfinite(res.proportion_mediated):
        out.bullet("매개비율(간접/총) = %.1f%%" % (res.proportion_mediated * 100))
    if res.proportion_note:
        out.bullet(res.proportion_note)
    std = [e for e in res.effects if math.isfinite(e.standardized)]
    if std and not brief:
        out.line()
        out.bullet("표준화 효과 — %s. 척도가 다른 연구와 비교할 때 씁니다."
                   % (res.standardized_kind or "표준화"))
        out.table(["효과", "표준화 값"],
                  [[e.label, fmt(e.standardized, digits)] for e in std], ["l", "r"])

    # --- per-path detail -------------------------------------------------
    if not brief:
        out.section("경로별 분해")
        for e in res.indirect_effects:
            parts = " × ".join("%s = %s" % (n, fmt(v, digits)) for n, v, _ in e.components)
            out.sub(e.label)
            out.line("    %s = %s" % (parts, fmt(e.estimate, digits)))
            out.line("    %s %g%% CI %s → %s"
                     % (e.ci_method, res.conf * 100, _ci(e.ci_lo, e.ci_hi, digits),
                        "0을 포함하지 않음(효과 있음)" if e.significant else "0을 포함(효과 근거 부족)"))
            if math.isfinite(e.delta_z):
                out.line("    Sobel/델타법 z = %s, p %s (참고용 — 곱의 분포는 정규가 아님)"
                         % (fmt(e.delta_z, 2), fmt_p(e.delta_p)))
            for wmsg in e.warnings:
                out.line("    ! %s" % wmsg)

    if res.contrasts:
        shown = res.contrasts if len(res.contrasts) <= 10 else [
            c for c in res.contrasts if c.significant]
        out.section("간접효과 간 대비 (어느 경로가 더 큰가)")
        if shown:
            out.table(["대비", "차이", "%g%% CI" % (res.conf * 100), "판정"],
                      [[c.label, fmt(c.estimate, digits), _ci(c.ci_lo, c.ci_hi, digits),
                        "0 미포함" if c.significant else "0 포함"] for c in shown],
                      ["l", "r", "r", "l"])
        if len(res.contrasts) > 10:
            out.line()
            out.bullet("대비 %d개 중 0을 포함하지 않는 것만 표시했습니다(전체는 --json 참고)."
                       % len(res.contrasts))

    # --- regressions -----------------------------------------------------
    if not brief:
        out.section("경로 계수 (회귀 결과)")
        for j, reg in enumerate(res.m_regressions):
            _reg_block(out, reg, "[M%d 모형] %s" % (j + 1, reg.outcome), digits,
                       highlight=[d.x_name])
        if res.y_regression:
            _reg_block(out, res.y_regression, "[Y 모형] %s (매개변수 포함)" % res.y_regression.outcome,
                       digits, highlight=[d.x_name] + [nm for nm, _ in d.mediators])
        if res.total_regression:
            _reg_block(out, res.total_regression,
                       "[총효과 모형] %s (매개변수 제외)" % res.total_regression.outcome,
                       digits, highlight=[d.x_name])

    # --- diagnostics -----------------------------------------------------
    if not brief:
        out.section("진단")
        if res.vif:
            out.table(["Y 모형 예측변수", "VIF"],
                      [[t, fmt(v, 2)] for t, v in res.vif], ["l", "r"])
            out.line()
        if res.bp_test:
            out.bullet("이분산 검정(Breusch–Pagan, studentized): LM = %s, df = %d, p %s"
                       % (fmt(res.bp_test[0], 2), res.bp_test[1], fmt_p(res.bp_test[2])))
        if res.influence:
            out.bullet("영향점(Cook's D > %.3f): %d개%s"
                       % (res.influence[1], res.influence[0],
                          (" — 상위 " + ", ".join("#%d(D=%s)" % (r + 1, fmt(v, 2))
                                                 for r, v in res.influence[2]))
                          if res.influence[2] else ""))
        if res.y_regression:
            out.bullet("Y 모형 조건수 지표(rcond) = %s (0에 가까울수록 공선성이 심함)"
                       % fmt(res.y_regression.rcond, 4))

    # --- warnings --------------------------------------------------------
    all_warn = list(res.warnings)
    if all_warn:
        out.section("경고 / 확인할 점")
        for wmsg in all_warn:
            out.bullet(wmsg)
    if res.notes and not brief:
        out.section("데이터 처리 메모")
        for note in res.notes:
            out.bullet(note)

    # --- APA -------------------------------------------------------------
    ko, en = _apa_sentences(res, digits)
    out.section("논문용 문장 (그대로 붙여 쓰고 숫자만 확인하세요)")
    indent = "    " if mode == "text" else ""
    out.sub("한국어")
    out.line(indent + ko)
    out.sub("English")
    out.line(indent + en)

    out.section("해석 시 주의")
    for c in _CAUTION_KO:
        out.bullet(c)

    return out.text()
