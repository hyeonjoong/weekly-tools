"""Match idea templates to a manifest, assess feasibility, and rank."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .knowledge import IDEA_TEMPLATES
from .manifest import MODALITY_LABEL_KO, Manifest
from .power import (
    attained_power,
    design_effect,
    detectable_effect,
    effect_magnitude,
    expected_events,
    is_noninferiority,
    observation_level,
    required_events,
    required_total_n,
    rows_to_subjects,
    scale_effect,
    subjects_to_rows,
)

# How each effect metric renders in the "detectable effect" cell. The margin_*
# metrics belong to non-inferiority designs, where the inverse question is "how
# tight a margin could this N rule out", not "what effect could it detect".
_METRIC_LABEL = {"r": "r", "d": "d", "d_z": "d_z", "f2": "f²", "f": "f",
                 "delta_p": "Δp", "hr": "HR",
                 "margin_d": "마진 d", "margin_p": "마진 Δp",
                 "margin_hr": "마진 HR"}

# Effect-size sensitivity strip: required N is recomputed at these multiples of
# the template's planned effect so the reader sees how the target moves if the
# true effect is smaller (conservative) or larger (optimistic) than assumed.
SENSITIVITY_FACTORS = [("보수적", 2.0 / 3.0), ("계획", 1.0), ("낙관적", 1.5)]

# Effect families whose N is counted in subjects no matter what a template says
# (see :func:`_is_observation_level`).
_SUBJECT_ONLY = frozenset({"survival", "two_proportion", "anova", "ancova"})

# How the sensitivity strip labels its magnitude column, per family. "효과 0.79"
# beside the 보수적 label reads as a *bigger* effect for a hazard ratio, so the
# metric is named.
_SENSITIVITY_METRIC = {"correlation": "r", "two_group": "d", "paired": "d_z",
                       "regression": "f²", "regression_change": "f²",
                       "two_proportion": "Δp", "survival": "HR",
                       "anova": "f", "ancova": "d"}

# Same, for non-inferiority designs — there the strip varies the MARGIN.
_NI_SENSITIVITY_METRIC = {"two_group": "마진 d", "two_proportion": "마진 Δp",
                          "survival": "마진 HR"}


def _sensitivity_metric(effect: dict) -> str:
    """Name of the quantity the sensitivity strip varies for this design."""
    etype = effect.get("type")
    if is_noninferiority(effect):
        return _NI_SENSITIVITY_METRIC.get(etype, "마진")
    return _SENSITIVITY_METRIC.get(etype, "효과")


@dataclass
class IdeaResult:
    idea_id: str
    title: str
    modalities: list  # canonical keys used (required ∩ available)
    hypothesis: str
    predictors: list
    outcomes: list
    analysis: str
    design: str
    journal: str
    novelty: str
    required_n: object  # int, or None when the design has no closed-form target
    available_n: object  # int or None
    feasible: object  # True / False / None (unknown)
    matched_variables: list
    score: float
    notes: list = field(default_factory=list)
    exploratory: bool = False  # design with no power target (e.g. clustering)
    detectable: object = None  # {"metric":..., "value":...} or None
    recruit_n: object = None  # required_n inflated for attrition, or None
    n_sensitivity: list = field(default_factory=list)  # required-N vs effect strip
    attained_power: object = None  # power at the N in hand, or None
    required_rows: object = None  # analysis rows needed (= required_n unless clustered)
    analysis_n: object = None  # effective analysis rows the held N supplies
    planned_effect: object = None  # effect spec actually used for sizing
    linked_declared: bool = False  # available_n came from a declared overlap
    required_events: object = None  # time-to-event designs only
    expected_events: object = None  # events the held N should yield, or None
    justification: str = ""  # protocol-ready sample-size sentence
    within_max_n: object = None  # required_n fits under --max-n (None if unset)

    @property
    def feasibility_label(self) -> str:
        if self.exploratory:
            return "탐색적(표본 판정 비적용)"
        if self.feasible is None:
            return "표본수 미상"
        if self.feasible:
            return "충분 가능"
        return "표본 부족 우려"

    @property
    def detectable_label(self) -> str:
        """Compact minimum-detectable-effect cell, e.g. ``r≥0.29`` or ``—``."""
        if not self.detectable:
            return "—"
        metric = _METRIC_LABEL.get(self.detectable["metric"], self.detectable["metric"])
        # A registry-scale N (n=1,000,000 is well under the tool's own ceiling)
        # drives the MDES below 0.005, where "%.2f" prints "f≥0.00" — which
        # reads as "no effect is too small" directly beside the caption saying
        # smaller effects may go undetected. Switch to significant figures there.
        if metric not in ("HR", "Δp") and 0 < self.detectable["value"] < 0.01:
            return f"{metric}≥{self.detectable['value']:.2g}"
        if metric == "HR":
            # A hazard ratio is symmetric on the log scale, so quoting only the
            # >1 side would read as "harm only" to a clinician looking at a
            # superiority trial. Print both directions.
            return (f"HR≥{self.detectable['value']:.2f} "
                    f"(또는 ≤{self.detectable['hr_protective']:.2f})")
        if metric == "Δp":
            return (f"Δp≥{self.detectable['value']:.2f} "
                    f"({self.detectable['p1']:.0%}→{self.detectable['p2']:.0%})")
        return f"{metric}≥{self.detectable['value']:.2f}"

    @property
    def power_label(self) -> str:
        """Attained-power cell, e.g. ``0.83`` / ``>0.99`` / ``—``."""
        if self.attained_power is None:
            return "—"
        if self.attained_power >= 0.995:
            return ">0.99"
        if self.attained_power < 0.005:
            return "<0.01"
        return f"{self.attained_power:.2f}"


def _fmt_alpha(value: float) -> str:
    """Alpha as a protocol would print it (0.05, 0.0071, ...)."""
    return f"{value:.4g}"


def _pct(value: float) -> str:
    """A proportion as a percentage without lying through rounding.

    ``f"{0.055:.0%}"`` prints "6%", from which a reviewer recomputing
    247/0.06 gets 4117 instead of the 4491 the report shows. Keep up to two
    decimals, drop trailing zeros.
    """
    text = f"{value * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


# Sino-Korean readings of the final digit decide the object particle: a reading
# ending in a consonant takes 을/으로, one ending in a vowel takes 를/로. The
# particles used to be hardcoded, so every generated sentence carried at least
# one wrong one ("HR=0.70를", "N=84을").
_DIGIT_HAS_BATCHIM = {"0": True, "1": True, "2": False, "3": True, "4": False,
                      "5": False, "6": True, "7": True, "8": True, "9": False}


def _josa(number_text: str, with_batchim: str, without: str) -> str:
    """Pick the particle that follows a written number (e.g. '0.70' -> '을')."""
    digits = number_text.rstrip()
    # A percentage is read "…퍼센트", which ends in a vowel regardless of the
    # digits, so "20%" takes 를/로 even though "20" alone would take 을/으로.
    if digits.endswith("%"):
        return without
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    tail = next((c for c in reversed(digits) if c.isdigit()), None)
    if tail is None:
        return with_batchim
    return with_batchim if _DIGIT_HAS_BATCHIM[tail] else without


def _obj(number_text: str) -> str:
    """``'을'``/``'를'`` for a number written as ``number_text``."""
    return _josa(number_text, "을", "를")


def _to(number_text: str) -> str:
    """``'으로'``/``'로'`` for a number written as ``number_text``."""
    return _josa(number_text, "으로", "로")


def _split_arms(total: int, allocation: float):
    """Per-arm sizes for a two-arm design, rounded up so neither arm is short.

    ``total // 2`` understated an arm whenever the ceiled total was odd — which
    happens about half the time for the two-proportion family — and the pair it
    produced (434+434=868 for a target of 869) actually falls below the target
    power. Ceiling each arm can add one subject; that is the safe direction.
    """
    n1 = math.ceil(total * allocation)
    return n1, total - n1


def _arm_clause(total, allocation: float) -> str:
    """``'(시험군 93명 / 대조군 93명)'`` — always stated, balanced or not."""
    if total is None or total < 2:
        return ""
    n1, n2 = _split_arms(total, allocation)
    if n1 == n2:
        return f"(군당 {n1}명)"
    return f"(1군 {n1}명 / 2군 {n2}명)"


def _anova_arm_clause(total, k_groups: int) -> str:
    """``'(군당 53명, 3군)'`` — the per-arm size a k-arm protocol must state."""
    if total is None or k_groups < 2 or total < k_groups:
        return ""
    per = -(-int(total) // int(k_groups))  # ceil, so no arm is short
    return f"(군당 {per}명 × {k_groups}군)"


def _ni_justification(effect: dict, *, alpha_eff: float, power: float,
                      required_n, events) -> str:
    """Protocol sentence for a non-inferiority design.

    Separate from the superiority text because every clause differs: the null is
    the margin (not zero), the test is one-sided at half the nominal level, and
    the margin's clinical justification — not the effect size's — is the thing a
    regulator will ask about, so that is where the blank goes.
    """
    etype = effect.get("type")
    alloc = float(effect.get("allocation", 0.5))
    split = ("1:1 배분" if abs(alloc - 0.5) < 1e-12
             else f"{_pct(alloc)}:{_pct(1 - alloc)} 배분")
    one_sided = f"단측 유의수준 α={_fmt_alpha(alpha_eff / 2)}"
    level = f"{one_sided}(양측 {_fmt_alpha(alpha_eff)} 상당), 목표 검정력 {power:.0%}"

    if etype == "two_group":
        m_txt = f"{effect['margin_d']:.2f}"
        d_txt = f"{float(effect.get('d', 0.0)):.2f}"
        return (
            f"{level}, {split}의 비열등성 설계에서 비열등성 마진 "
            f"d={m_txt}(표준편차 단위), 가정한 실제 군간 차이 d={d_txt} 조건으로 "
            f"산출한 필요 표본은 총 {required_n}명"
            f"{_arm_clause(required_n, alloc)}이다(정규근사)."
        )
    if etype == "two_proportion":
        m_txt = _pct(float(effect["margin"]))
        direction = ("높을수록 좋은 종점"
                     if effect.get("higher_is_better", True)
                     else "낮을수록 좋은 종점")
        return (
            f"{level}, {split}의 비열등성 설계에서 대조군 "
            f"{_pct(float(effect['p1']))} 대비 시험군 "
            f"{_pct(float(effect['p2']))}({direction})을 가정하고 비열등성 마진 "
            f"{m_txt}{_obj(m_txt)} 적용하면, 두 비율 비교(비합동 분산 정규근사, "
            f"연속성 보정 없음) 기준 총 {required_n}명"
            f"{_arm_clause(required_n, alloc)}이 필요하다."
        )
    if etype == "survival":
        m_txt = f"{effect['margin_hr']:.2f}"
        hr_txt = f"{float(effect.get('hr', 1.0)):.2f}"
        er_txt = _pct(float(effect["event_rate"]))
        return (
            f"{level}, {split}의 비열등성 로그순위검정에서 비열등성 마진 "
            f"HR={m_txt}, 가정한 실제 HR={hr_txt} 조건이면 Schoenfeld 공식에 따라 "
            f"총 {events}건의 사건이 필요하며, 관찰기간 내 사건 발생률 "
            f"{er_txt}{_obj(er_txt)} 가정하면 {required_n}명"
            f"{_arm_clause(required_n, alloc)}을 등록해야 한다. 사건 수가 "
            "검정력을 결정하므로 추적기간이 짧아지면 등록 수를 늘려도 검정력은 "
            "회복되지 않는다. Schoenfeld 공식은 비례위험을 가정하며, 비례위험 "
            "가정은 Schoenfeld 잔차로 점검한다."
        )
    return f"비열등성 설계 기준 필요 표본은 총 {required_n}명이다."  # pragma: no cover


def sample_size_justification(
    effect: dict, *, alpha: float, alpha_eff: float, power: float, sided: int,
    required_n, required_rows, events, recruit_n, dropout: float, n_tests: int,
    repeats: int, icc: float, cluster_applies: bool,
) -> str:
    """A protocol-ready Korean sample-size sentence for one idea.

    The numbers in the matrix are only half of what a researcher needs: an IRB
    submission, a grant, or a paper's Methods section wants the *justification* —
    which test, which assumed effect, which alpha, and how the target was
    derived. Writing that by hand from a table is where transcription errors
    (and unstated multiplicity corrections) creep in, so it is generated from
    exactly the numbers that produced the verdict.
    """
    etype = effect.get("type")
    tail = "단측" if sided == 1 else "양측"
    level = f"{tail} 유의수준 α={_fmt_alpha(alpha_eff)}, 목표 검정력 {power:.0%}"
    ftest = f"유의수준 α={_fmt_alpha(alpha_eff)}, 목표 검정력 {power:.0%}"

    if etype == "exploratory":
        return (
            "본 연구는 탐색적(비확증적) 설계이므로 사전 표본수 산출 공식을 "
            "적용하지 않는다. 파일럿 규모로 수행하고 군집 안정성·실루엣 등 "
            "탐색 지표로 평가하며, 확증 연구의 효과크기 추정에 활용한다."
        )
    if required_n is None:
        return ""

    # When repeated measures are in play the closed form sizes analysis
    # observations; the subject count is the converted number, so the core
    # sentence must quote the observation target and let the conversion clause
    # deliver the headcount. Quoting the converted N as if the formula produced
    # it made the two clauses contradict each other ("총 46명" then "85개를 46명
    # 으로 환산").
    converted = cluster_applies and required_rows is not None
    unit_n = required_rows if converted else required_n
    unit_word = "분석 관측" if converted else "명"
    got = (f"필요 분석 관측 수는 {unit_n}개이다" if converted
           else f"필요 표본은 총 {required_n}명이다")

    if is_noninferiority(effect):
        core = _ni_justification(effect, alpha_eff=alpha_eff, power=power,
                                 required_n=required_n, events=events)
    elif etype == "correlation":
        r_txt = f"{effect['r']:.2f}"
        core = (
            f"{level} 조건에서 Pearson 상관 r={r_txt}{_obj(r_txt)} 검출하기 위해 "
            f"Fisher z 변환 근사로 산출한 {got}."
        )
    elif etype == "two_group":
        alloc = float(effect.get("allocation", 0.5))
        split = (
            "1:1 배분" if abs(alloc - 0.5) < 1e-12
            else f"{_pct(alloc)}:{_pct(1 - alloc)} 배분"
        )
        d_txt = f"{effect['d']:.2f}"
        per = "" if converted else _arm_clause(required_n, alloc)
        core = (
            f"{level}, {split}의 독립 2군 평균 비교에서 표준화 평균차 "
            f"d={d_txt}{_obj(d_txt)} 검출하려면 {got}{per}(정규근사)."
        )
    elif etype == "paired":
        d_txt = f"{effect['d']:.2f}"
        core = (
            f"{level} 조건의 대응(피험자 내) 비교에서 d_z={d_txt}{_obj(d_txt)} "
            f"검출하려면 {got}(정규근사)."
        )
    elif etype == "anova":
        k_groups = int(effect["k_groups"])
        f_txt = f"{effect['f']:.2f}"
        per = "" if converted else _anova_arm_clause(required_n, k_groups)
        core = (
            f"{k_groups}개 군의 일원배치 분산분석(one-way ANOVA) 옴니버스 F "
            f"검정에서 Cohen's f={f_txt}{_obj(f_txt)} 검출하려면 {ftest} 기준 "
            f"{got}{per}(비중심 F 분포로 정확 계산). 옴니버스 검정이 유의해도 "
            "어느 군끼리 다른지는 말해주지 않으므로, 사후 쌍별 비교를 계획한다면 "
            "그에 대한 다중비교 보정을 별도로 명시한다."
        )
    elif etype == "ancova":
        alloc = float(effect.get("allocation", 0.5))
        split = ("1:1 배분" if abs(alloc - 0.5) < 1e-12
                 else f"{_pct(alloc)}:{_pct(1 - alloc)} 배분")
        d_txt = f"{effect['d']:.2f}"
        rho = abs(float(effect.get("r_covariate", 0.0)))
        k_cov = int(effect.get("k_covariates", 1))
        per = "" if converted else _arm_clause(required_n, alloc)
        core = (
            f"{level}, {split}의 공분산분석(ANCOVA)에서 공변량 {k_cov}개"
            f"(기저값 등, 종점과의 상관 ρ={rho:.2f})를 보정하면 잔차분산이 "
            f"(1−ρ²)={1 - rho * rho:.2f}배로 줄어, 보정 전 표준화 평균차 "
            f"d={d_txt}{_obj(d_txt)} 검출하는 데 {got}{per}"
            "(Borm 등 2007 근사)."
        )
        # The whole point of adjusting for baseline is the N it saves; quoting
        # the unadjusted target beside it is what makes the choice reviewable.
        try:
            plain = required_total_n(
                {"type": "two_group", "d": effect["d"], "allocation": alloc},
                alpha=alpha_eff, power=power, sided=sided,
            )
        except (ValueError, OverflowError, ZeroDivisionError):
            plain = None
        # ...but only when there IS one. A weak covariate (rho<=~0.1) rounds to
        # the same N, and the sentence is destined for an IRB submission, so
        # asserting a saving of zero subjects is not a cosmetic problem.
        if plain is not None and required_n is not None and plain > required_n:
            core += (
                f" 공변량 보정 없이 단순 2군 비교로 계산하면 {plain}명이 "
                "필요하므로, 기저값 보정은 사전에 분석계획서에 명시해야 이 "
                "표본수 감소가 정당화된다."
            )
        elif plain is not None:
            core += (
                f" 다만 가정한 ρ={rho:.2f}에서는 보정 없는 2군 비교"
                f"({plain}명) 대비 표본 감소가 사실상 없으므로, 공변량은 "
                "표본수가 아니라 검정력·해석의 근거로만 정당화된다."
            )
    elif etype == "regression":
        k = int(effect.get("k", 1))
        f2_txt = f"{effect['f2']:.3g}"
        core = (
            f"예측변수 {k}개 다중회귀의 R²≠0 검정에서 Cohen's f²="
            f"{f2_txt}{_obj(f2_txt)} 검출하려면 {ftest} 기준 {got}"
            "(비중심 F 분포로 정확 계산)."
        )
    elif etype == "regression_change":
        f2_txt = f"{effect['f2']:.3g}"
        core = (
            f"공변량 {int(effect['k_control'])}개를 보정한 뒤 추가 예측변수 "
            f"{int(effect['k_tested'])}개의 증분설명력(ΔR²) 검정에서 f²="
            f"{f2_txt}{_obj(f2_txt)} 검출하려면 {ftest} 기준 {got}"
            "(비중심 F 분포로 정확 계산)."
        )
    elif etype == "two_proportion":
        p1, p2 = float(effect["p1"]), float(effect["p2"])
        alloc = float(effect.get("allocation", 0.5))
        diff_txt = _pct(abs(p2 - p1))
        split = ("1:1 배분" if abs(alloc - 0.5) < 1e-12
                 else f"{_pct(alloc)}:{_pct(1 - alloc)} 배분")
        core = (
            f"{level}, {split} 조건에서 대조군 {_pct(p1)} 대비 시험군 "
            f"{_pct(p2)}(위험차 {diff_txt}){_obj(diff_txt)} 검출하려면 "
            f"두 비율 비교(정규근사, 연속성 보정 없음) 기준 총 {required_n}명"
            f"{_arm_clause(required_n, alloc)}이 필요하다."
        )
    elif etype == "survival":
        er = float(effect["event_rate"])
        hr_txt = f"{effect['hr']:.2f}"
        alloc = float(effect.get("allocation", 0.5))
        split = ("1:1 배분" if abs(alloc - 0.5) < 1e-12
                 else f"{_pct(alloc)}:{_pct(1 - alloc)} 배분")
        er_txt = _pct(er)
        core = (
            f"{level}, {split} 조건의 로그순위검정에서 위험비 HR={hr_txt}"
            f"{_obj(hr_txt)} 검출하려면 Schoenfeld 공식에 따라 총 {events}건의 "
            f"사건이 필요하며, 관찰기간 내 사건 발생률 {er_txt}{_obj(er_txt)} "
            f"가정하면 {required_n}명{_arm_clause(required_n, alloc)}을 "
            "등록해야 한다. 사건 수가 검정력을 결정하므로 추적기간이 짧아지면 "
            "등록 수를 늘려도 검정력은 회복되지 않는다. Schoenfeld 공식은 "
            "비례위험을 가정하며, 비례위험 가정은 Schoenfeld 잔차로 점검한다."
        )
    else:  # pragma: no cover - validated upstream
        core = f"가정 효과크기 기준 {got}."

    extra = []
    if n_tests > 1:
        extra.append(
            f"아이디어당 주요 비교 {n_tests}회에 대한 Bonferroni 보정"
            f"(α={_fmt_alpha(alpha)}/{n_tests}={_fmt_alpha(alpha_eff)})을 "
            "반영했다."
        )
    if converted:
        de_txt = f"{design_effect(repeats, icc):.2f}"
        extra.append(
            f"피험자당 {repeats}회 반복 관측과 급내상관 ICC={icc:g}"
            f"(설계효과 {de_txt}){_obj(de_txt)} 반영해 이를 피험자 "
            f"{required_n}명으로 환산했다."
        )
    if recruit_n is not None and dropout > 0.0:
        drop_txt = _pct(dropout)
        extra.append(
            f"중도탈락 {drop_txt}{_obj(drop_txt)} 고려한 모집 목표는 "
            f"{recruit_n}명이다."
        )
    # The assumed effect size is the tool's prior, not the user's evidence. The
    # sentence is designed to be pasted into a protocol, so it must carry a
    # visible hole where the provenance belongs rather than reading as if the
    # magnitude had already been justified.
    extra.append(
        "가정 효과크기의 근거는 [출처 기재 — 선행연구/자체 파일럿]이다."
    )
    extra.append(
        "본 수치는 계획용 근사치이므로 최종 검정력은 전용 검정력 분석 "
        "소프트웨어(예: G*Power)로 확인한다."
    )
    return " ".join([core] + extra)


def _is_observation_level(template, effect) -> bool:
    """Whether this template's N counts observations rather than subjects.

    ``analysis_unit`` on the template wins when present ("observation" /
    "subject"); otherwise fall back to the effect family. The override matters:
    correlation-family templates whose data are one row per subject (psychometric
    validation, device agreement) must not have their N divided by the number of
    repeated measurements.
    """
    unit = template.get("analysis_unit")
    if unit == "subject":
        return False
    if unit == "observation":
        # ...but the override cannot make a subject-level family observation-
        # level. A log-rank test counts events in distinct subjects, a
        # two-proportion test counts subjects classified responder/non-responder,
        # and a parallel-group ANOVA/ANCOVA randomises each subject to exactly one
        # arm; measuring each subject four times yields no extra events, no extra
        # responders and no extra arms. Honouring the override here divided the
        # survival target by the design effect (412 -> 134 subjects) and flipped
        # the verdict to "충분 가능" while the same record still printed 247
        # required events — and did the same to a 3-arm ANOVA (159 -> 64).
        return effect.get("type") not in _SUBJECT_ONLY
    return observation_level(effect)


def _modality_index(manifest: Manifest):
    """Group datasets by canonical modality.

    Returns ``(index, conflicts)`` where ``index[mod] = {'n':minN, 'vars':set}``.
    When the same modality appears with different sample sizes (e.g. two EEG
    cohorts) we keep the **minimum** — the number of subjects with *linked*
    multimodal data cannot exceed the smaller set, so min is the conservative
    choice for a feasibility check (taking max would overstate feasibility).
    """
    index: dict = {}
    seen_ns: dict = {}
    for d in manifest.datasets:
        if not d.modality:
            continue
        slot = index.setdefault(d.modality, {"n": None, "vars": set()})
        slot["vars"].update(v.lower() for v in d.variables)
        if d.n is not None:
            seen_ns.setdefault(d.modality, set()).add(d.n)
            slot["n"] = d.n if slot["n"] is None else min(slot["n"], d.n)
    conflicts = {m: sorted(ns) for m, ns in seen_ns.items() if len(ns) > 1}
    return index, conflicts


def _available_for(required, index, linked_n):
    """Analysable subject count for a modality combination.

    Returns ``(n_or_None, declared, contradictory)``:

    * ``n`` — the smallest per-modality ``n`` among the required modalities,
      capped by any declared subject overlap (``linked_n``) whose modality set
      is a subset of this combination. A declared "only 28 subjects have EEG
      *and* respiration" caps the combination even when each modality reports 90.
    * ``declared`` — whether a linkage covering this combination was declared at
      all. This is deliberately *not* "was it the binding constraint": the report
      must not tell a user who did declare an overlap that they didn't.
    * ``contradictory`` — the declaration claims MORE subjects than the smallest
      contributing modality has, which cannot be true; the smaller value is kept
      and the report says so.

    Without a declared overlap the min-of-n is an *upper bound*: it assumes the
    smaller cohort is fully contained in the larger. That assumption is flagged
    in the report rather than hidden.
    """
    ns = [index[m]["n"] for m in required if index[m]["n"] is not None]
    all_known = len(ns) == len(required)
    base = min(ns) if all_known and ns else None

    req_set = set(required)
    declared = None
    for combo, value in linked_n.items():
        if combo <= req_set:
            declared = value if declared is None else min(declared, value)

    if declared is None:
        return base, False, False
    if base is None:
        return declared, True, False
    return min(base, declared), True, declared > base


def _optional_shortfalls(used, required, index, linked_n, available_n):
    """Optional modalities whose own N falls short of the combination's N.

    An idea is *offered* on the strength of its required modalities, so the
    headline N is computed from those alone. But a template's hypothesis often
    names an optional modality's variables (e.g. "…predicts the questionnaire
    score"), and if only 5 subjects filled in that questionnaire, any analysis
    touching it is capped at 5 — not the advertised 90. Returning those cases
    lets the report say so instead of quietly overstating feasibility.
    """
    if available_n is None:
        return []
    out = []
    for m in used:
        if m in required:
            continue
        cap = index[m]["n"]
        for combo, value in linked_n.items():
            if combo <= set(used) and m in combo:
                cap = value if cap is None else min(cap, value)
        if cap is not None and cap < available_n:
            out.append((m, cap))
    return sorted(out, key=lambda pair: pair[1])


def evaluate(
    manifest: Manifest,
    alpha: float = 0.05,
    power: float = 0.80,
    templates=IDEA_TEMPLATES,
    dropout: float = 0.0,
    effect_scale: float = 1.0,
    sided: int = 2,
    n_tests: int = 1,
    repeats: int = 1,
    icc: float = 0.0,
    max_n: object = None,
) -> list:
    """Return ranked :class:`IdeaResult` list for ideas whose modalities are met.

    ``dropout`` (0 <= p < 1) inflates each idea's recruitment target to
    ``ceil(required_n / (1 - p))`` so a prospective study still finishes with
    the analyzable N; it does not change the feasibility verdict, which compares
    the *analyzable* required N against the N already in hand.

    ``effect_scale`` (>0) multiplies every template's assumed effect magnitude
    before sizing — set it below 1 to plan against a smaller (more conservative)
    true effect. The per-idea sensitivity strip additionally reports required N
    at a spread of effect magnitudes regardless of this global scale.

    ``sided`` (1 or 2) selects a directional or two-tailed test for the z-based
    designs (correlation / mean differences); F-based ΔR² sizing is unaffected.

    ``n_tests`` (>=1) applies a Bonferroni correction: every sample-size, MDES
    and attained-power figure is computed at ``alpha / n_tests``, which is what
    you want when an idea's analysis plan carries several primary comparisons.

    ``max_n`` (optional, >=1) is the largest sample the group could realistically
    recruit. It never changes a verdict — it annotates ideas whose target (after
    attrition) sits beyond that ceiling, which is the question a planning meeting
    actually asks once the required-N column is full of numbers.

    ``repeats``/``icc`` describe repeated observations per subject (e.g. 3 nights
    with an intraclass correlation of 0.3). For designs sized in *observations*
    (correlation, regression) the subject requirement becomes
    ``ceil(N_rows * (1 + (m-1)*ICC) / m)`` and the held N is converted to
    effective rows the same way; subject-level designs (paired / two-group) are
    left untouched.
    """
    if not 0.0 <= dropout < 1.0:
        raise ValueError("dropout must satisfy 0 <= dropout < 1")
    if effect_scale <= 0.0:
        raise ValueError("effect_scale must be > 0")
    if sided not in (1, 2):
        raise ValueError("sided must be 1 or 2")
    n_tests = int(n_tests)
    if n_tests < 1:
        raise ValueError("n_tests must be >= 1")
    if max_n is not None:
        max_n = int(max_n)
        if max_n < 1:
            raise ValueError("max_n must be >= 1")
    try:
        alpha_eff = float(alpha) / n_tests
    except OverflowError:  # n_tests too large to convert to float
        alpha_eff = 0.0
    if not 0.0 < alpha_eff < 1.0:
        raise ValueError(
            f"alpha / n_tests must fall strictly between 0 and 1 "
            f"(alpha={alpha!r}, n_tests={n_tests})"
        )
    de = design_effect(repeats, icc)  # validates repeats/icc
    clustered = int(repeats) > 1
    index, conflicts = _modality_index(manifest)
    for mod, ns in conflicts.items():
        # An inventory with thousands of distinct n per modality produced a
        # single 30 KB warning line; show a few and count the rest.
        shown = ns if len(ns) <= 6 else ns[:6] + [f"…외 {len(ns) - 6}개"]
        low = f"{min(ns)}"
        msg = (
            f"모달리티 '{modality_label(mod)}'에 서로 다른 n {shown}가 있어 "
            f"연결 가능한 최소값({low}){_obj(low)} 보수적으로 사용합니다."
        )
        if msg not in manifest.warnings:
            manifest.warnings.append(msg)
    available = set(index)
    results: list = []
    unmatched: list = []

    for t in templates:
        required = t["required"]
        if not all(m in available for m in required):
            unmatched.append(t)
            continue

        used = list(required) + [m for m in t.get("optional", []) if m in available]

        # Limiting sample size: min n across required modalities, capped by any
        # declared subject overlap for this combination.
        available_n, linked_declared, linked_contradictory = _available_for(
            required, index, manifest.linked_n
        )
        shortfalls = _optional_shortfalls(
            used, required, index, manifest.linked_n, available_n
        )

        # Apply the global effect-size scale before sizing / feasibility.
        base_effect = t["effect"]
        planned_effect = scale_effect(base_effect, effect_scale)
        # A template may state its unit of analysis explicitly; otherwise it is
        # inferred from the effect family. The explicit form exists because
        # "correlation" alone does not imply observation-level data — a
        # psychometric validation or a device-agreement study yields exactly one
        # value per subject, so repeated measures must NOT shrink its N.
        obs_level = _is_observation_level(t, planned_effect)
        cluster_applies = clustered and obs_level

        def _subjects(rows):
            """Analysis rows -> subjects needed (identity unless clustered)."""
            if rows is None:
                return None
            return rows_to_subjects(rows, repeats, icc) if cluster_applies else rows

        req_rows = required_total_n(
            planned_effect, alpha=alpha_eff, power=power, sided=sided
        )
        req_n = _subjects(req_rows)
        exploratory = req_n is None
        # Time-to-event designs are powered on EVENTS; the subject count is a
        # consequence of the assumed event rate, so both are carried through.
        req_events = required_events(
            planned_effect, alpha=alpha_eff, power=power, sided=sided
        )

        # Effective rows of analysis the held subjects supply.
        analysis_n = None
        if available_n is not None:
            analysis_n = (
                subjects_to_rows(available_n, repeats, icc)
                if cluster_applies else available_n
            )

        if exploratory or available_n is None:
            feasible = None
        else:
            feasible = available_n >= req_n

        # Recruitment target inflated for expected attrition (planning aid only).
        recruit_n = None
        if req_n is not None and dropout > 0.0:
            recruit_n = math.ceil(req_n / (1.0 - dropout))

        # Sensitivity: smallest effect the *available* N could detect, and the
        # power actually attained against the planned effect at that N.
        detectable = detectable_effect(
            planned_effect, analysis_n, alpha=alpha_eff, power=power, sided=sided
        )
        power_now = attained_power(
            planned_effect, analysis_n, alpha=alpha_eff, sided=sided
        )
        exp_events = None
        if planned_effect.get("type") == "survival" and available_n is not None:
            exp_events = expected_events(
                available_n, planned_effect["event_rate"]
            )

        # Required-N-vs-effect strip: how the target moves if the true effect is
        # smaller/larger than the planned prior. Skipped for exploratory designs.
        n_sensitivity = []
        if not exploratory:
            for label, factor in SENSITIVITY_FACTORS:
                eff = scale_effect(planned_effect, factor)
                rows = required_total_n(
                    eff, alpha=alpha_eff, power=power, sided=sided
                )
                n_sensitivity.append({
                    "label": label,
                    "factor": factor,
                    "metric": _sensitivity_metric(planned_effect),
                    "effect_value": round(effect_magnitude(eff), 4),
                    "required_n": _subjects(rows),
                })

        # Variables available in the modalities this idea draws on. (Template
        # predictors/outcomes are free-text concepts, so this is the pool of
        # usable columns, not a token-by-token match — named accordingly.)
        present_vars = set().union(*(index[m]["vars"] for m in used)) if used else set()
        matched = sorted(present_vars)

        # Scoring within a feasibility tier: prefer multimodal, variable-rich
        # ideas. The variable term is CAPPED so a column-rich modality can't
        # outweigh the multimodal bonus (one extra modality = +4).
        score = 0.0
        score += 4.0 * (len(required) - 1)  # multimodal bonus
        score += 0.4 * min(len(matched), 5)  # capped at +2
        # tie-break: more optional modalities available adds a little.
        score += 0.5 * (len(used) - len(required))

        notes = []
        for mod, cap in shortfalls:
            notes.append(
                f"선택 모달리티 '{modality_label(mod)}'의 표본은 {cap}명뿐입니다"
                f"(이 아이디어의 보유 N={available_n}). 가설이 그 변수를 쓰면 "
                f"실제 분석 N은 {cap}{_to(str(cap))} 줄어듭니다 — 보유 N은 필수 모달리티만으로 "
                "계산합니다."
            )
        if linked_contradictory:
            notes.append(
                "선언된 연결 표본수가 개별 모달리티 n의 최소값보다 커서 "
                "(모순) 최소값을 사용했습니다 — 선언값을 확인하세요."
            )
        if len(required) > 1:
            if linked_declared:
                notes.append(
                    f"연결 표본수(linked N)가 매니페스트에 선언돼 있어 "
                    f"보유 N={available_n}{_obj(str(available_n))} 사용했습니다."
                )
            else:
                notes.append(
                    "동일 피험자에서 모달리티가 연결(linked)돼 있어야 함 — "
                    "연결 표본수가 선언되지 않아 각 모달리티 n의 최소값을 "
                    "사용했습니다(실제 겹치는 인원은 더 적을 수 있음). "
                    "매니페스트에 linked_n을 넣으면 정확해집니다."
                )
        if cluster_applies:
            notes.append(
                f"반복측정 보정: 피험자당 {repeats}회 관측, ICC={icc:g} → "
                f"설계효과 {de:.2f}. 필요한 분석 행 {req_rows}개 = 피험자 {req_n}명."
            )
        elif clustered:
            notes.append(
                f"이 설계는 표본 단위가 피험자라서 반복측정({repeats}회) 보정을 "
                "적용하지 않았습니다(반복은 측정오차만 줄임)."
            )
        if n_tests > 1:
            notes.append(
                f"다중비교 보정: alpha {alpha:g}/{n_tests} = {alpha_eff:.5g} "
                "기준으로 표본수·검정력을 계산했습니다(Bonferroni)."
            )
        if sided == 1:
            etype = planned_effect.get("type")
            # The two clinical families ARE affected by --one-sided (186->147,
            # 412->325) and are exactly where a one-sided log-rank or
            # risk-difference test draws reviewer fire, so they need the caveat
            # most.
            if etype in ("correlation", "two_group", "paired", "ancova",
                         "two_proportion", "survival"):
                notes.append("단측검정(one-sided) 기준 — 방향 가설일 때만 사용하세요.")
            elif etype in ("regression", "regression_change", "anova"):
                # The omnibus F belongs here for the same reason ΔR² does: it is
                # one-tailed *on F* while staying direction-free on the means, so
                # --one-sided changes nothing. Saying so beats leaving the user
                # to wonder why the k-arm row alone did not move.
                notes.append(
                    "옴니버스 F/ΔR² 검정은 단측 개념이 없어 --one-sided 가 "
                    "적용되지 않았습니다."
                )
        if feasible is False:
            msg = (
                f"가정한 효과크기 기준 권장 N={req_n}, 보유 N={available_n} "
                "→ 작은 효과는 놓칠 수 있으니 표본 확대 또는 효과크기 재검토."
            )
            if power_now is not None:
                msg += f" (현재 표본의 검정력 ≈ {power_now:.2f})"
            notes.append(msg)
        # Recruitment ceiling: an idea can be "표본 부족" and still be worth
        # running (recruit more), or be permanently out of reach. Say which.
        within_max = None
        if max_n is not None and req_n is not None:
            target = recruit_n if recruit_n is not None else req_n
            within_max = target <= max_n
            if not within_max:
                notes.append(
                    f"모집 상한(--max-n {max_n})으로는 도달 불가: 필요 "
                    f"{'모집 ' if recruit_n is not None else ''}N={target}. "
                    "효과크기 가정·설계(반복측정/1:1 배분)를 재검토하거나 "
                    "다기관·기존 코호트 활용을 고려하세요."
                )
        if req_events is not None:
            msg = f"시간-사건 설계: 필요 사건 수 {req_events}건이 검정력을 결정합니다"
            if exp_events is not None:
                msg += (
                    f" (보유 N={available_n} × 가정 사건발생률 "
                    f"{planned_effect['event_rate']:.0%} ≈ {exp_events}건)"
                )
            notes.append(
                msg + ". 추적기간이 짧아 사건이 덜 발생하면 등록 수를 채워도 "
                "검정력은 부족합니다."
            )
        if exploratory:
            notes.append(
                "탐색적 설계(군집 등)라 단순 표본수 공식이 적용되지 않음 — "
                "실루엣/안정성 등 탐색 지표로 평가하고 예비/파일럿으로 접근."
            )
        elif available_n is None:
            notes.append("매니페스트에 n이 없어 검정력 판단 불가 — n을 채우면 자동 평가됨.")
        # Template-authored honesty caveats (assumptions the sizing makes).
        notes.extend(t.get("caveats", []))

        results.append(
            IdeaResult(
                idea_id=t["id"],
                title=t["title"],
                modalities=used,
                hypothesis=t["hypothesis"],
                predictors=t["predictors"],
                outcomes=t["outcomes"],
                analysis=t["analysis"],
                design=t["design"],
                journal=t["journal"],
                novelty=t["novelty"],
                required_n=req_n,
                available_n=available_n,
                feasible=feasible,
                matched_variables=matched,
                score=round(score, 3),
                notes=notes,
                exploratory=exploratory,
                detectable=detectable,
                recruit_n=recruit_n,
                n_sensitivity=n_sensitivity,
                attained_power=power_now,
                required_rows=req_rows,
                analysis_n=analysis_n,
                planned_effect=dict(planned_effect),
                linked_declared=linked_declared,
                required_events=req_events,
                expected_events=exp_events,
                within_max_n=within_max,
                justification=sample_size_justification(
                    planned_effect, alpha=alpha, alpha_eff=alpha_eff,
                    power=power, sided=sided, required_n=req_n,
                    required_rows=req_rows, events=req_events,
                    recruit_n=recruit_n, dropout=dropout, n_tests=n_tests,
                    repeats=repeats, icc=icc, cluster_applies=cluster_applies,
                ),
            )
        )

    # A template pack that matches nothing used to be indistinguishable from a
    # pack that was never loaded: the run just produced fewer ideas, with no
    # warning. Name what was dropped and why, capped so a 500-template pack does
    # not flood the report.
    if unmatched:
        shown = ", ".join(
            f"{t['id']}({'+'.join(modality_label(m) for m in t['required'])})"
            for t in unmatched[:5]
        )
        more = f" 외 {len(unmatched) - 5}개" if len(unmatched) > 5 else ""
        msg = (
            f"아이디어 템플릿 {len(unmatched)}개가 매니페스트에 없는 모달리티를 "
            f"요구해 제외됐습니다: {shown}{more}. 해당 데이터셋을 매니페스트에 "
            "추가하거나 모달리티 표기를 확인하세요."
        )
        if msg not in manifest.warnings:
            manifest.warnings.append(msg)

    # Feasibility is the PRIMARY sort key so the triage never puts an
    # underpowered idea above a feasible one: feasible (2) > unknown-N (1) >
    # underpowered (0). Score (multimodal/variable richness) orders within a tier.
    feas_rank = {True: 2, None: 1, False: 0}
    results.sort(
        key=lambda r: (feas_rank[r.feasible], r.score, len(r.modalities)),
        reverse=True,
    )
    return results


def modality_label(key: str) -> str:
    return MODALITY_LABEL_KO.get(key, key)
