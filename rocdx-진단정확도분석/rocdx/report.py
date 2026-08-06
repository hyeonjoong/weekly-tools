"""Human-readable output: text report, markdown tables, ASCII ROC curve.

The report is written for someone who has to defend the numbers in a manuscript
or a submission, so every number carries its interval and every choice made on
the user's behalf (direction, dropped rows, data-chosen cut-offs) is stated.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .analyze import Analysis, SelectedPoint
from .roc import Metrics, Point, curve_xy

__all__ = ["format_report", "markdown_report", "ascii_curve", "points_csv_rows",
           "paper_sentence", "auc_grade", "conf_level", "cutoff_text"]

_NA = "—"


def _f(x: Optional[float], nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return _NA
    if math.isinf(x):
        return "∞" if x > 0 else "-∞"
    return f"{x:.{nd}f}"


def _pct(x: Optional[float], nd: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return _NA
    return f"{x * 100:.{nd}f}%"


def _ci(ci: Optional[Tuple[float, float]], nd: int = 3, pct: bool = False) -> str:
    if ci is None:
        return _NA
    lo, hi = ci
    if pct:
        return f"[{_pct(lo, nd)}, {_pct(hi, nd)}]"
    return f"[{_f(lo, nd)}, {_f(hi, nd)}]"


def _p(p: Optional[float]) -> str:
    if p is None or math.isnan(p):
        return _NA
    if p < 1e-4:
        return "< 0.0001"
    return f"{p:.4f}"


def _g(x: float, nd: int = 12) -> str:
    """Compact number for cut-offs, which are in the user's own units.

    Printed with enough significant digits that applying the printed rule to the
    data reproduces the printed 2x2 — rounding a platelet cut-off of 192821 to
    "1.928e+05" changed the sensitivity it was quoted with.
    """
    if math.isnan(x):
        return _NA
    if math.isinf(x):
        return "+∞" if x > 0 else "-∞"
    return f"{x:.{nd}g}"


def conf_level(alpha: float) -> str:
    """Confidence level as text: 0.05 -> "95%", 0.001 -> "99.9%"."""
    txt = f"{(1.0 - alpha) * 100.0:.4f}".rstrip("0").rstrip(".")
    return f"{txt}%"


_GRADE_NOTE = ("(Hosmer–Lemeshow 관례적 구간일 뿐 임상적 유용성 기준이 아닙니다 — "
               "점추정치가 아니라 신뢰구간으로 판단하세요)")


def auc_grade(auc: float, ci: Optional[Tuple[float, float]] = None) -> str:
    """Conventional descriptive band for an AUC (Hosmer & Lemeshow style).

    Two honesty guards: an AUC below 0.5 means the marker is *reversed*, not
    useless, and no band is given while the interval still covers 0.5.
    """
    if math.isnan(auc):
        return "계산 불가"
    if auc < 0.5 - 1e-12:
        return ("방향이 반대입니다 — 낮은 값이 질환 쪽입니다. --direction 을 확인하세요 "
                "(AUC < 0.5: the marker is inverted, not useless)")
    if ci is not None and ci[0] <= 0.5 <= ci[1]:
        return ("판별력을 입증하지 못했습니다 — 신뢰구간이 0.5(우연)를 포함합니다 "
                "(CI includes chance)")
    if auc < 0.6:
        band = "판별력 거의 없음 (no useful discrimination)"
    elif auc < 0.7:
        band = "낮음 (poor)"
    elif auc < 0.8:
        band = "보통 (acceptable)"
    elif auc < 0.9:
        band = "우수 (excellent)"
    else:
        band = "매우 우수 (outstanding)"
    return f"{band} {_GRADE_NOTE}"


def cutoff_text(an: Analysis, point: Point) -> str:
    """Cut-off rule in the user's original units, e.g. ``CRP >= 12.4``."""
    value, op = an.cutoff_in_original_units(point.threshold)
    if math.isinf(point.threshold):
        return ("모두 음성으로 판정 (nobody called positive)" if point.threshold > 0
                else "모두 양성으로 판정 (everybody called positive)")
    return f"{an.dataset.score_name} {op} {_g(value)}"


def _proven(an: Analysis) -> bool:
    """Did this sample actually demonstrate discrimination better than chance?"""
    a = an.auc
    if a.ci is not None and a.ci[0] <= 0.5 <= a.ci[1]:
        return False
    if a.p_value is not None and a.p_value >= an.alpha:
        return False
    if a.ci is None and a.p_value is None:
        return False
    return True


# --- ASCII curve --------------------------------------------------------------

def ascii_curve(points: Sequence[Point], width: int = 46, height: int = 19,
                marks: Sequence[Tuple[Point, str]] = ()) -> str:
    """A small ROC plot for the terminal. Rough by design — for eyeballing shape."""
    grid = [[" "] * (width + 1) for _ in range(height + 1)]
    xy = [(x, y) for x, y in curve_xy(points) if not (math.isnan(x) or math.isnan(y))]
    if not xy:
        return "(곡선을 그릴 수 없습니다)"
    xy.sort()

    def cell(x: float, y: float) -> Tuple[int, int]:
        col = int(round(min(max(x, 0.0), 1.0) * width))
        row = height - int(round(min(max(y, 0.0), 1.0) * height))
        return row, col

    for i in range(width + 1):  # chance diagonal
        r, c = cell(i / width, i / width)
        grid[r][c] = "."
    prev: Optional[Tuple[int, int]] = None
    for x, y in xy:
        r, c = cell(x, y)
        if prev is not None:
            pr, pc = prev
            for cc in range(min(pc, c), max(pc, c) + 1):  # horizontal run
                if grid[pr][cc] in (" ", "."):
                    grid[pr][cc] = "*"
            for rr in range(min(pr, r), max(pr, r) + 1):  # vertical run
                if grid[rr][c] in (" ", "."):
                    grid[rr][c] = "*"
        grid[r][c] = "*"
        prev = (r, c)
    for pt, ch in marks:
        if math.isnan(pt.sens) or math.isnan(pt.spec):
            continue
        r, c = cell(1.0 - pt.spec, pt.sens)
        grid[r][c] = ch

    lines = []
    for i, row in enumerate(grid):
        tick = "1.0" if i == 0 else ("0.0" if i == height else
                                     ("0.5" if i == height // 2 else "   "))
        lines.append(f" {tick} |" + "".join(row))
    lines.append("     +" + "-" * (width + 1))
    axis = [" "] * (width + 3)
    for pos, lab in ((0, "0.0"), (width // 2 - 1, "0.5"), (width - 2, "1.0")):
        for k, ch in enumerate(lab):
            axis[min(max(pos + k, 0), width + 2)] = ch
    lines.append("      " + "".join(axis))
    lines.append("      1 - 특이도 (false positive rate)   ↑ 세로축 = 민감도")
    return "\n".join(lines)


# --- metric block -------------------------------------------------------------

def _metric_lines(an: Analysis, sp: SelectedPoint) -> List[str]:
    m: Metrics = sp.metrics
    pt = m.point
    lvl = conf_level(an.alpha)
    at_prev = m.prevalence_source == "user"
    prev_tag = f" (유병률 {_g(m.prevalence, 6)} 가정)" if at_prev else ""
    lr_tag = " [0.5 보정]" if m.lr_ci_corrected else ""
    out = [
        f"      절단점 (cut-off) : {cutoff_text(an, pt)}",
        f"      2x2              : TP {pt.tp}  FP {pt.fp}  FN {pt.fn}  TN {pt.tn}",
        f"      민감도 Sens      : {_pct(m.sens)}  {lvl} CI {_ci(m.sens_ci, 1, pct=True)}"
        f"   ({pt.tp}/{pt.tp + pt.fn})",
        f"      특이도 Spec      : {_pct(m.spec)}  {lvl} CI {_ci(m.spec_ci, 1, pct=True)}"
        f"   ({pt.tn}/{pt.tn + pt.fp})",
        f"      Youden J         : {_f(pt.youden)}",
        f"      PPV              : {_pct(m.ppv)}  {lvl} CI {_ci(m.ppv_ci, 1, pct=True)}"
        f"{prev_tag}",
        f"      NPV              : {_pct(m.npv)}  {lvl} CI {_ci(m.npv_ci, 1, pct=True)}"
        f"{prev_tag}",
        f"      정확도 Accuracy  : {_pct(m.accuracy)}  {lvl} CI "
        f"{_ci(m.accuracy_ci, 1, pct=True)}{prev_tag}"
        f"   / 균형정확도 {_pct(m.balanced_accuracy)}",
        f"      LR+ / LR-        : {_f(m.plr, 2)} {_ci(m.plr_ci, 2)}  /  "
        f"{_f(m.nlr, 2)} {_ci(m.nlr_ci, 2)}{lr_tag}",
        f"      진단오즈비 DOR   : {_f(m.dor, 2)} {_ci(m.dor_ci, 2)}",
        f"      유병률 기준      : {_g(m.prevalence, 6)} "
        f"({'표본 그대로' if not at_prev else '사용자 지정 — 민감도·특이도가 그 집단에서도 같다는 가정'})",
    ]
    if m.lr_ci_corrected:
        out.append("      * 셀이 0이라 우도비 신뢰구간은 0.5를 더한 표에서 계산했습니다 "
                   "(점추정치 0/∞는 표본이 작다는 뜻이지 완벽하다는 뜻이 아닙니다)")
    b = sp.bootstrap
    if b is not None:
        out.append(f"      ── 절단점 선택까지 포함한 부트스트랩 "
                   f"({b.n_effective}/{b.n_boot}회, seed {b.seed})")
        if b.cutoff_ci:
            lo, hi = b.cutoff_ci
            if an.flipped:
                lo, hi = -hi, -lo
            out.append(f"         절단점 {lvl} 구간 : [{_g(lo)}, {_g(hi)}]"
                       f"   ({b.n_cutoff_draws}회 기준)")
        out.append(f"         민감도 {lvl} 구간 : {_ci(b.sens_ci, 1, pct=True)}"
                   f"   (재선택한 절단점을 원자료에 적용한 분포)")
        out.append(f"         특이도 {lvl} 구간 : {_ci(b.spec_ci, 1, pct=True)}")
        if b.youden_corrected is not None:
            out.append(
                f"         낙관 보정 J     : {_f(b.youden_corrected)} "
                f"(관측 {_f(pt.youden)} − 낙관 {_f(b.optimism_youden)})"
            )
            out.append(f"         낙관 보정 민감도/특이도 : {_pct(b.sens_corrected)} / "
                       f"{_pct(b.spec_corrected)}")
            out.append("         → 부풀림의 크기를 추정한 값일 뿐 제거한 것이 아닙니다. "
                       "내부검증이며 독립 검증 표본을 대신하지 못합니다.")
    return out


# --- full report --------------------------------------------------------------

def format_report(an: Analysis, show_curve: bool = True) -> str:
    ds = an.dataset
    lvl = conf_level(an.alpha)
    L: List[str] = []
    L.append("=" * 74)
    L.append("  rocdx — 진단정확도 분석 (ROC / sensitivity / specificity)")
    L.append("=" * 74)
    L.append(f"  검사값 (index test)  : {ds.score_name}")
    L.append(f"  기준 진단 (reference): {ds.truth_name} "
             f"[양성 = '{ds.positive_label}', 음성 = '{ds.negative_label}']")
    dir_txt = ("값이 낮을수록 질환 (lower = diseased)" if an.flipped
               else "값이 높을수록 질환 (higher = diseased)")
    src = "사용자 지정" if an.direction_source == "user" else "데이터에서 자동 판정"
    L.append(f"  방향                 : {dir_txt}  [{src}]")
    if ds.encoding or ds.delimiter:
        L.append(f"  입력 파일 해석       : 인코딩 {ds.encoding}, 구분자 {ds.delimiter!r}")
    L.append(f"  분석 대상            : 입력 {ds.n_rows_in}행 중 {len(ds.scores)}명 "
             f"(질환군 {ds.n_pos}, 비질환군 {ds.n_neg}, 표본 유병률 "
             f"{ds.n_pos / max(1, len(ds.scores)) * 100:.1f}%)")
    if ds.n_dropped:
        L.append(f"  제외된 행            : {ds.n_dropped} / {ds.n_rows_in}")
        for reason, cnt in sorted(ds.drop_reasons.items(), key=lambda kv: -kv[1]):
            L.append(f"      - {reason}: {cnt}")
    for note in ds.notes:
        L.append(f"  * {note}")

    L.append("")
    L.append("─" * 74)
    L.append("  1. 곡선아래면적 (AUC / c-statistic)")
    L.append("─" * 74)
    a = an.auc
    ci_name = "logit 변환" if a.ci_method == "logit" else "Wald"
    L.append(f"  AUC = {_f(a.auc)}   {lvl} CI {_ci(a.ci)}  "
             f"({ci_name}, DeLong SE = {_f(a.se, 4)})")
    L.append(f"  판별력 : {auc_grade(a.auc, a.ci)}")
    L.append(f"  H0: AUC = 0.5 (동전 던지기와 같음) → p {_p(a.p_value)}  "
             f"[Mann-Whitney U, 동점 보정]")
    side = "낮을" if an.flipped else "높을"
    L.append(f"  해석: 무작위로 고른 질환자 1명의 검사값이 비질환자 1명보다 {side} 확률이 "
             f"{_pct(a.auc)}입니다 (동점은 절반으로 셈).")

    for cmp_ in an.comparisons:
        L.append("")
        L.append(f"  ▸ 두 검사 비교 (DeLong, {'짝지은 동일 대상' if cmp_.paired else '독립 표본'})")
        flip_b = an.comparison_flipped.get(cmp_.label_b)
        dir_b = "" if flip_b is None else (" [낮을수록 질환]" if flip_b else " [높을수록 질환]")
        L.append(f"      {cmp_.label_a}: AUC {_f(cmp_.auc_a)}   "
                 f"{cmp_.label_b}: AUC {_f(cmp_.auc_b)}{dir_b}")
        L.append(f"      차이 = {_f(cmp_.diff)}  {lvl} CI {_ci(cmp_.ci)}  "
                 f"z = {_f(cmp_.z, 3)}  p {_p(cmp_.p_value)}")
        if cmp_.p_value is None:
            L.append("      → 분산을 추정할 수 없어 검정이 정의되지 않습니다 "
                     "(예: 비교 검사값이 모두 동일). 차이의 크기만 보고하세요.")
        elif cmp_.p_value < an.alpha:
            L.append("      → 차이가 통계적으로 유의합니다")
        else:
            L.append("      → 차이가 유의하지 않습니다 (같다는 증명은 아닙니다)")

    L.append("")
    L.append("─" * 74)
    L.append("  2. 절단점별 진단 성능")
    L.append("─" * 74)
    for sp in an.selected:
        L.append("")
        tag = " [데이터에서 선택]" if sp.data_chosen else " [사전 지정]"
        L.append(f"  ● {sp.label}{tag}")
        if not sp.feasible:
            L.append(f"      {sp.note or '조건을 만족하는 절단점이 없습니다.'}")
            continue
        L.extend(_metric_lines(an, sp))

    if any(sp.data_chosen for sp in an.selected):
        L.append("")
        L.append("  ※ '데이터에서 선택'한 절단점의 민감도·특이도는 같은 데이터에서 고른 만큼")
        L.append("     낙관적으로 부풀려집니다. 보고할 때는 이 사실을 밝히고, 가능하면 별도의")
        L.append("     검증 표본에서 확인하세요 (--bootstrap 으로 부풀림 정도를 추정할 수 있습니다).")

    if show_curve:
        L.append("")
        L.append("─" * 74)
        L.append("  3. ROC 곡선 (* 곡선, . 우연선, Y = Youden 절단점)")
        L.append("─" * 74)
        marks = [(sp.metrics.point, "Y") for sp in an.selected
                 if sp.key == "youden" and sp.feasible]
        L.append(ascii_curve(an.points, marks=marks))

    if an.warnings:
        L.append("")
        L.append("─" * 74)
        L.append("  경고 / caveats")
        L.append("─" * 74)
        for w in an.warnings:
            L.append(f"  ! {w}")

    L.append("")
    L.append("─" * 74)
    L.append("  4. 논문용 문장 초안 (숫자를 확인하고, 주의 문장은 지우지 마세요)")
    L.append("─" * 74)
    L.append(paper_sentence(an))
    L.append("")
    return "\n".join(L)


def paper_sentence(an: Analysis) -> str:
    """A ready-to-adapt results paragraph, Korean + English.

    When this sample did not actually demonstrate discrimination — the AUC
    interval covers 0.5, or a group is tiny — the operating-point sentence is
    replaced by a refusal rather than a publishable-looking performance claim.
    """
    ds = an.dataset
    a = an.auc
    lvl = conf_level(an.alpha)
    ci = f"{_f(a.ci[0])}–{_f(a.ci[1])}" if a.ci else _NA
    best = next((sp for sp in an.selected if sp.key == "youden" and sp.feasible), None)
    tiny = min(ds.n_pos, ds.n_neg) < 10
    lines = [
        f"  총 {ds.n_rows_in}명 중 분석 가능한 {len(ds.scores)}명"
        f"(질환군 {ds.n_pos}명, 비질환군 {ds.n_neg}명)을 대상으로 "
        f"{ds.score_name}의 진단 성능을 평가하였다. AUC는 {_f(a.auc)}"
        f"({lvl} CI {ci}, p {_p(a.p_value)})였다.",
    ]
    if not _proven(an):
        lines.append(
            "  AUC의 신뢰구간이 0.5를 포함하므로 본 자료만으로는 이 검사의 판별력을 "
            "입증하지 못하였다. 아래 절단점의 민감도·특이도는 참고용이며 진단 성능의 "
            "근거로 인용해서는 안 된다."
        )
    elif tiny:
        lines.append(
            f"  다만 한쪽 군의 표본이 매우 작아(질환군 {ds.n_pos}, 비질환군 {ds.n_neg}) "
            f"신뢰구간이 넓고 정규근사에 기반한 p값은 신뢰하기 어렵다."
        )
    if best is not None and _proven(an) and not tiny:
        m = best.metrics
        lr = ""
        if math.isfinite(m.plr) and math.isfinite(m.nlr):
            lr = f", 양성우도비 {_f(m.plr, 2)}, 음성우도비 {_f(m.nlr, 2)}"
        lines.append(
            f"  Youden 지수를 최대화하는 절단점({cutoff_text(an, m.point)})에서 민감도는 "
            f"{_pct(m.sens)}({_ci(m.sens_ci, 1, pct=True)}), 특이도는 "
            f"{_pct(m.spec)}({_ci(m.spec_ci, 1, pct=True)})였다{lr}."
        )
        lines.append(
            "  절단점은 본 자료에서 선택되었으므로 외부 자료에서의 성능은 이보다 낮을 수 "
            "있다 (이 문장은 지우지 마세요)."
        )
    if an.flipped and an.direction_source == "auto":
        lines.append("  (검사 방향은 데이터에서 자동으로 정해졌다 — 임상 근거로 방향을 "
                     "정한 뒤 --direction 으로 명시해 다시 확인하기를 권한다.)")
    if an.prevalence_user is not None:
        lines.append(f"  PPV/NPV는 유병률 {_g(an.prevalence_user, 6)}을 가정하여 베이즈 "
                     f"정리로 산출하였다.")
    for cmp_ in an.comparisons:
        lines.append(
            f"  {cmp_.label_a}의 AUC({_f(cmp_.auc_a)})는 {cmp_.label_b}"
            f"({_f(cmp_.auc_b)})와 비교하여 차이 {_f(cmp_.diff)}"
            f"({lvl} CI {_ci(cmp_.ci)}, DeLong p {_p(cmp_.p_value)})였다."
        )
    en = (f"  EN: Of {ds.n_rows_in} records, {len(ds.scores)} were analysable "
          f"({ds.n_pos} cases, {ds.n_neg} controls). The AUC of {ds.score_name} was "
          f"{_f(a.auc)} ({lvl} CI {ci}, p {_p(a.p_value)}).")
    if best is not None and _proven(an) and not tiny:
        en += (f" At the Youden-optimal cut-off ({cutoff_text(an, best.metrics.point)}), "
               f"sensitivity was {_pct(best.metrics.sens)} and specificity "
               f"{_pct(best.metrics.spec)}; because the cut-off was chosen on these "
               f"data, external performance may be lower.")
    else:
        en += (" The interval includes 0.5, so discrimination was not demonstrated in "
               "this sample; operating-point figures are exploratory only.")
    lines.append(en)
    return "\n".join(lines)


# --- markdown / csv -----------------------------------------------------------

def markdown_report(an: Analysis) -> str:
    ds = an.dataset
    a = an.auc
    lvl = conf_level(an.alpha)
    prev_tag = (f" (유병률 {_g(an.prevalence_user, 6)} 가정)"
                if an.prevalence_user is not None else "")
    lines = [
        f"### 진단정확도 — {ds.score_name} (기준: {ds.truth_name})",
        "",
        f"- 입력 {ds.n_rows_in}행 중 분석 {len(ds.scores)}명 "
        f"(질환군 {ds.n_pos} / 비질환군 {ds.n_neg}), "
        f"방향: {'낮을수록 질환' if an.flipped else '높을수록 질환'}",
        f"- **AUC {_f(a.auc)}** ({lvl} CI {_ci(a.ci)}), p {_p(a.p_value)} (H0: AUC=0.5)",
        f"- 판별력: {auc_grade(a.auc, a.ci)}",
        "",
        f"| 절단점 기준 | Cut-off | 민감도 ({lvl} CI) | 특이도 ({lvl} CI) | "
        f"PPV{prev_tag} | NPV{prev_tag} | LR+ | LR- | Youden J |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for sp in an.selected:
        if not sp.feasible:
            continue
        m = sp.metrics
        tag = " *[데이터에서 선택]*" if sp.data_chosen else " *[사전 지정]*"
        lines.append(
            f"| {sp.label}{tag} | {cutoff_text(an, m.point)} | "
            f"{_pct(m.sens)} {_ci(m.sens_ci, 1, pct=True)} | "
            f"{_pct(m.spec)} {_ci(m.spec_ci, 1, pct=True)} | {_pct(m.ppv)} | {_pct(m.npv)} | "
            f"{_f(m.plr, 2)} | {_f(m.nlr, 2)} | {_f(m.point.youden)} |"
        )
    for cmp_ in an.comparisons:
        lines.append("")
        lines.append(f"- DeLong 비교: {cmp_.label_a} {_f(cmp_.auc_a)} vs "
                     f"{cmp_.label_b} {_f(cmp_.auc_b)}, 차이 {_f(cmp_.diff)} "
                     f"{_ci(cmp_.ci)}, p {_p(cmp_.p_value)}")
    lines.append("")
    if any(sp.data_chosen for sp in an.selected):
        lines.append("> **주의**: `[데이터에서 선택]` 절단점의 민감도·특이도는 같은 자료에서 "
                     "고른 만큼 낙관적으로 부풀려져 있습니다. 독립 검증 표본에서 확인하세요.")
    if an.prevalence_user is not None:
        lines.append(f"> **주의**: PPV/NPV는 유병률 {_g(an.prevalence_user, 6)} 가정에서 "
                     f"베이즈 정리로 계산했으며 신뢰구간을 제공하지 않습니다.")
    for w in an.warnings:
        lines.append(f"> **주의**: {w}")
    return "\n".join(lines)


def points_csv_rows(an: Analysis) -> List[List[str]]:
    """Every operating point of the curve, for plotting elsewhere.

    The two trivial endpoints have no finite cut-off; their cut-off cell is left
    empty so a spreadsheet does not choke on ``inf``.
    """
    rows = [["cutoff", "rule", "tp", "fp", "fn", "tn", "sensitivity",
             "specificity", "one_minus_specificity", "youden_j"]]
    op = "<=" if an.flipped else ">="
    for p in an.points:
        value, _ = an.cutoff_in_original_units(p.threshold)
        rows.append([
            "" if math.isinf(value) else f"{value:.12g}",
            f"{an.dataset.score_name} {op} cutoff",
            str(p.tp), str(p.fp), str(p.fn), str(p.tn),
            f"{p.sens:.10g}", f"{p.spec:.10g}", f"{1.0 - p.spec:.10g}",
            f"{p.youden:.10g}",
        ])
    return rows
