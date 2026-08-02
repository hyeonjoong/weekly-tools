"""Load and validate *user-supplied* idea-template packs (JSON, stdlib only).

The built-in knowledge base in :mod:`paperforge.knowledge` is hand-curated for
sleep/arousal physiology. A clinical or pharma group working on, say, oncology
biomarkers or PK/PD needs its own angles — and should not have to edit Python to
get them. A template pack is a JSON file holding the same records the built-ins
use::

    {
      "pack": "oncology-biomarker",
      "templates": [
        {
          "id": "pdl1_response",
          "title": "PD-L1 발현과 반응률",
          "required": ["questionnaire"],
          "optional": [],
          "hypothesis": "...",
          "predictors": ["PD-L1 TPS"],
          "outcomes": ["ORR"],
          "analysis": "로지스틱 회귀",
          "design": "two-group comparison",
          "analysis_unit": "subject",
          "effect": {"type": "two_group", "d": 0.5, "allocation": 0.3},
          "journal": "...",
          "novelty": "...",
          "caveats": ["..."]
        }
      ]
    }

A bare JSON array of templates is accepted too. Every field is validated up
front — a typo in ``effect`` must fail loudly at load time, not silently produce
a wrong sample size in a planning meeting. A custom template whose ``id`` equals
a built-in one *replaces* that built-in (so a lab can retune an effect-size
prior without forking the package).
"""
from __future__ import annotations

import json
import os

from .manifest import normalize_modality

# Fields every template must carry, and their expected Python type.
_REQUIRED_STR_FIELDS = ("id", "title", "hypothesis", "analysis", "design",
                        "journal", "novelty")
_REQUIRED_LIST_FIELDS = ("required", "predictors", "outcomes")
_OPTIONAL_LIST_FIELDS = ("optional", "caveats")

# Effect specs: type -> (required numeric keys, optional keys).
_EFFECT_SCHEMA = {
    "correlation": (("r",), ()),
    "two_group": (("d",), ("allocation",)),
    "paired": (("d",), ()),
    "anova": (("f", "k_groups"), ()),
    "ancova": (("d",), ("r_covariate", "k_covariates", "allocation")),
    "regression": (("f2",), ("k",)),
    "regression_change": (("f2", "k_tested", "k_control"), ()),
    "two_proportion": (("p1", "p2"), ("allocation",)),
    "survival": (("hr", "event_rate"), ("allocation",)),
    "exploratory": ((), ()),
}

# Effect families that can be declared as a non-inferiority design, and the
# margin key each one requires. (A correlation or an R² increment has no
# "clinically irrelevant amount worse", so NI is undefined for them.)
_NI_MARGIN_KEY = {
    "two_group": "margin_d",
    "two_proportion": "margin",
    "survival": "margin_hr",
}


# Effect families whose N is a headcount by construction, so declaring them
# observation-level would ask --repeats/--icc to shrink the sample. A log-rank
# test counts events in distinct subjects, a two-proportion test counts subjects
# classified responder/non-responder, and the two parallel-group families below
# randomise each subject to exactly one arm — measuring someone four times
# yields no extra events, no extra responders and no extra arms.
_SUBJECT_ONLY_TYPES = ("survival", "two_proportion", "anova", "ancova")


class TemplateError(ValueError):
    """Raised when a template pack is malformed."""


def _require_number(value, field: str, where: str, *, positive=True,
                    integer=False, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemplateError(f"{where}: effect.{field} must be a number.")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise TemplateError(f"{where}: effect.{field} must be finite.")
    if integer and not value.is_integer():
        raise TemplateError(f"{where}: effect.{field} must be a whole number.")
    if positive and value <= 0:
        raise TemplateError(f"{where}: effect.{field} must be > 0.")
    if upper is not None and value >= upper:
        raise TemplateError(f"{where}: effect.{field} must be < {upper}.")
    return int(value) if integer else value


def validate_effect(effect, where: str) -> dict:
    """Validate an ``effect`` spec, returning a normalised copy."""
    if not isinstance(effect, dict):
        raise TemplateError(f"{where}: 'effect' must be an object.")
    etype = effect.get("type")
    if etype not in _EFFECT_SCHEMA:
        raise TemplateError(
            f"{where}: unknown effect type {etype!r}. "
            f"Supported: {sorted(_EFFECT_SCHEMA)}"
        )
    out = {"type": etype}

    # A non-inferiority design changes what every downstream number MEANS
    # (assumed difference vs margin, one-sided alpha/2, unpooled variance), so
    # it is validated first and relaxes the "the effect must be non-zero" rules
    # below: for NI, "no true difference" is the standard planning assumption.
    ni = False
    if "design" in effect:
        design = effect["design"]
        if design not in ("superiority", "noninferiority"):
            raise TemplateError(
                f"{where}: effect.design must be 'superiority' or "
                f"'noninferiority' (got {design!r})."
            )
        ni = design == "noninferiority"
        if ni:
            if etype not in _NI_MARGIN_KEY:
                raise TemplateError(
                    f"{where}: non-inferiority is only defined for effect types "
                    f"{sorted(_NI_MARGIN_KEY)} (got {etype!r})."
                )
            out["design"] = design

    if etype == "correlation":
        out["r"] = _require_number(effect.get("r"), "r", where, upper=1.0)
    elif etype in ("two_group", "paired"):
        if ni:
            # The assumed TRUE difference; 0 ("the arms are equivalent") is the
            # conventional planning value and a negative value states an assumed
            # disadvantage that eats into the margin.
            out["d"] = _require_number(
                effect.get("d", 0.0), "d", where, positive=False
            )
            margin = _require_number(
                effect.get("margin_d"), "margin_d", where
            )
            if margin + out["d"] <= 0:
                raise TemplateError(
                    f"{where}: effect.margin_d + effect.d must be > 0 — an "
                    "assumed disadvantage at least as large as the margin can "
                    "never be shown non-inferior."
                )
            out["margin_d"] = margin
        else:
            out["d"] = _require_number(effect.get("d"), "d", where)
        if etype == "two_group" and "allocation" in effect:
            alloc = _require_number(
                effect["allocation"], "allocation", where, upper=1.0
            )
            out["allocation"] = alloc
    elif etype == "anova":
        out["f"] = _require_number(effect.get("f"), "f", where)
        k_groups = _require_number(
            effect.get("k_groups"), "k_groups", where, integer=True
        )
        if k_groups < 2:
            raise TemplateError(
                f"{where}: effect.k_groups must be >= 2 (a one-way ANOVA needs "
                "at least two arms; use type 'two_group' for exactly two means)."
            )
        if k_groups > 1000:
            raise TemplateError(f"{where}: effect.k_groups must be <= 1000.")
        out["k_groups"] = k_groups
    elif etype == "ancova":
        out["d"] = _require_number(effect.get("d"), "d", where)
        # rho is a *measurement* property (how well baseline predicts endpoint),
        # so its sign is irrelevant and 0 is legitimate — it just means the
        # covariate buys nothing. |rho| = 1 would imply a zero-variance residual.
        rho = _require_number(
            effect.get("r_covariate", 0.0), "r_covariate", where,
            positive=False,
        )
        if not -1.0 < rho < 1.0:
            raise TemplateError(
                f"{where}: effect.r_covariate must satisfy -1 < rho < 1."
            )
        out["r_covariate"] = rho
        k_cov = _require_number(
            effect.get("k_covariates", 1), "k_covariates", where, integer=True
        )
        if k_cov > 1000:
            raise TemplateError(f"{where}: effect.k_covariates must be <= 1000.")
        out["k_covariates"] = k_cov
        if "allocation" in effect:
            out["allocation"] = _require_number(
                effect["allocation"], "allocation", where, upper=1.0
            )
    elif etype == "regression":
        out["f2"] = _require_number(effect.get("f2"), "f2", where)
        if "k" in effect:
            out["k"] = _require_number(effect["k"], "k", where, integer=True)
    elif etype == "regression_change":
        out["f2"] = _require_number(effect.get("f2"), "f2", where)
        out["k_tested"] = _require_number(
            effect.get("k_tested"), "k_tested", where, integer=True
        )
        out["k_control"] = _require_number(
            effect.get("k_control"), "k_control", where,
            positive=False, integer=True,
        )
        if out["k_control"] < 0:
            raise TemplateError(f"{where}: effect.k_control must be >= 0.")
    elif etype == "two_proportion":
        out["p1"] = _require_number(effect.get("p1"), "p1", where, upper=1.0)
        out["p2"] = _require_number(effect.get("p2"), "p2", where, upper=1.0)
        if ni:
            margin = _require_number(effect.get("margin"), "margin", where,
                                     upper=1.0)
            out["margin"] = margin
            higher = effect.get("higher_is_better", True)
            if not isinstance(higher, bool):
                raise TemplateError(
                    f"{where}: effect.higher_is_better must be true or false."
                )
            out["higher_is_better"] = higher
            sign = 1.0 if higher else -1.0
            if margin + sign * (out["p2"] - out["p1"]) <= 0:
                raise TemplateError(
                    f"{where}: the assumed rates are worse than the margin "
                    "allows; non-inferiority could never be shown."
                )
        elif out["p1"] == out["p2"]:
            # A zero risk difference needs an infinite sample; catching it here
            # beats surfacing "필요 표본수가 1,000,000명을 넘습니다" at run time.
            raise TemplateError(
                f"{where}: effect.p1 and effect.p2 must differ "
                "(a zero risk difference cannot be sized)."
            )
        if "allocation" in effect:
            out["allocation"] = _require_number(
                effect["allocation"], "allocation", where, upper=1.0
            )
    elif etype == "survival":
        out["hr"] = _require_number(
            effect.get("hr", 1.0) if ni else effect.get("hr"), "hr", where
        )
        if ni:
            margin_hr = _require_number(
                effect.get("margin_hr"), "margin_hr", where
            )
            if margin_hr == out["hr"]:
                raise TemplateError(
                    f"{where}: effect.margin_hr must differ from effect.hr "
                    "(a zero-width margin cannot be tested)."
                )
            out["margin_hr"] = margin_hr
        elif out["hr"] == 1.0:
            raise TemplateError(
                f"{where}: effect.hr must differ from 1 (no effect to detect)."
            )
        # An event *probability*; 1.0 (everyone has the event) is legitimate, so
        # the bound is inclusive unlike the other proportions here.
        rate = _require_number(effect.get("event_rate"), "event_rate", where)
        if rate > 1.0:
            raise TemplateError(f"{where}: effect.event_rate must be <= 1.")
        out["event_rate"] = rate
        if "allocation" in effect:
            out["allocation"] = _require_number(
                effect["allocation"], "allocation", where, upper=1.0
            )
    return out


def validate_template(raw, where: str) -> dict:
    """Validate one template record, returning a normalised copy."""
    if not isinstance(raw, dict):
        raise TemplateError(f"{where}: each template must be an object.")
    t: dict = {}
    for field in _REQUIRED_STR_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TemplateError(f"{where}: '{field}' must be a non-empty string.")
        t[field] = value.strip()
    for field in _REQUIRED_LIST_FIELDS:
        value = raw.get(field)
        if not isinstance(value, list) or not value:
            raise TemplateError(f"{where}: '{field}' must be a non-empty array.")
        t[field] = [str(v).strip() for v in value if str(v).strip()]
        if not t[field]:
            raise TemplateError(f"{where}: '{field}' must be a non-empty array.")
    for field in _OPTIONAL_LIST_FIELDS:
        value = raw.get(field, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            raise TemplateError(f"{where}: '{field}' must be an array.")
        t[field] = [str(v).strip() for v in value if str(v).strip()]

    # Modalities must resolve to canonical keys, otherwise the template could
    # never match anything and would sit silently unused.
    for field in ("required", "optional"):
        canon = []
        for token in t[field]:
            key, ok = normalize_modality(token)
            if not ok:
                raise TemplateError(
                    f"{where}: unknown modality {token!r} in '{field}'. "
                    "Use one of the documented aliases (eeg/뇌파, watch/워치, ...)."
                )
            if key not in canon:
                canon.append(key)
        t[field] = canon
    if not t["required"]:
        raise TemplateError(f"{where}: 'required' must list >= 1 modality.")
    overlap = set(t["required"]) & set(t["optional"])
    if overlap:
        raise TemplateError(
            f"{where}: {sorted(overlap)} appears in both 'required' and 'optional'."
        )

    unit = raw.get("analysis_unit")
    if unit is not None:
        if unit not in ("observation", "subject"):
            raise TemplateError(
                f"{where}: 'analysis_unit' must be 'observation' or 'subject' "
                f"(got {unit!r})."
            )
        t["analysis_unit"] = unit

    t["effect"] = validate_effect(raw.get("effect"), where)
    # A binary or time-to-event target counts subjects by construction, so
    # declaring it observation-level would ask --repeats/--icc to shrink the
    # sample. Reject at load rather than silently ignoring the field.
    if (t.get("analysis_unit") == "observation"
            and t["effect"]["type"] in _SUBJECT_ONLY_TYPES):
        raise TemplateError(
            f"{where}: effect type {t['effect']['type']!r} is always measured "
            "per subject; 'analysis_unit' cannot be 'observation'."
        )
    return t


def parse_template_pack(data, source: str = "<pack>") -> list:
    """Validate a parsed pack (object with ``templates``, or a bare array)."""
    if isinstance(data, dict):
        raw_list = data.get("templates")
        if raw_list is None:
            raise TemplateError(
                f"{source}: pack object must contain a 'templates' array."
            )
    else:
        raw_list = data
    if not isinstance(raw_list, list) or not raw_list:
        raise TemplateError(f"{source}: 'templates' must be a non-empty array.")

    out: list = []
    seen: set = set()
    for i, raw in enumerate(raw_list):
        t = validate_template(raw, f"{source}[{i}]")
        if t["id"] in seen:
            raise TemplateError(f"{source}: duplicate template id {t['id']!r}.")
        seen.add(t["id"])
        out.append(t)
    return out


def load_template_pack(path: str) -> list:
    """Read and validate a template pack from ``path``."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TemplateError(
            f"{path}: 템플릿 팩은 UTF-8이어야 합니다 ({exc})."
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TemplateError(f"{path}: JSON을 해석할 수 없습니다 — {exc}") from exc
    return parse_template_pack(data, source=os.path.basename(path))


def merge_templates(builtin: list, packs: list, include_builtin: bool = True):
    """Combine built-in and custom templates.

    Returns ``(templates, warnings)``. Custom templates are appended in load
    order; one whose ``id`` matches an existing template replaces it in place
    (keeping the ranking stable) and produces a warning so the override is
    visible in the report rather than silent.
    """
    warnings: list = []
    merged = list(builtin) if include_builtin else []
    by_id = {t["id"]: i for i, t in enumerate(merged)}
    for pack in packs:
        for t in pack:
            if t["id"] in by_id:
                merged[by_id[t["id"]]] = t
                warnings.append(
                    f"사용자 템플릿 '{t['id']}'가 기존 템플릿을 대체했습니다."
                )
            else:
                by_id[t["id"]] = len(merged)
                merged.append(t)
    if not merged:
        raise TemplateError(
            "사용할 템플릿이 없습니다 (--no-builtin 을 썼다면 --templates 로 "
            "최소 1개 팩을 지정하세요)."
        )
    warnings.extend(_misplaced_ni_warnings(merged))
    return merged, warnings


# A template carries TWO 'design' fields: a free-text one at the top level
# ("randomised parallel-group") and an optional switch inside `effect` that
# actually changes the arithmetic. Putting "noninferiority" in the free-text one
# is the obvious mistake — and it used to be silent, sizing an NI trial as a
# superiority trial and reporting the result as a confident number. Say so.
_NI_TEXT_MARKERS = ("noninferior", "non-inferior", "비열등")


def _misplaced_ni_warnings(templates: list) -> list:
    """Warn when the free-text design says NI but the effect spec doesn't."""
    out = []
    for t in templates:
        text = str(t.get("design", "")).lower()
        if not any(marker in text for marker in _NI_TEXT_MARKERS):
            continue
        if t.get("effect", {}).get("design") == "noninferiority":
            continue
        out.append(
            f"템플릿 '{t['id']}'의 design 문구는 비열등성인데 effect 안에 "
            '"design": "noninferiority" 가 없어 **우월성(superiority) 기준**으로 '
            "표본수를 계산했습니다. 비열등성 마진(margin_d/margin/margin_hr)과 "
            "함께 effect 안에 넣으세요 — 그러지 않으면 필요 표본이 크게 "
            "달라집니다."
        )
    return out
