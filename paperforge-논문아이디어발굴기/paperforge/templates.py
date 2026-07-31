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
    "regression": (("f2",), ("k",)),
    "regression_change": (("f2", "k_tested", "k_control"), ()),
    "exploratory": ((), ()),
}


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
    if etype == "correlation":
        out["r"] = _require_number(effect.get("r"), "r", where, upper=1.0)
    elif etype in ("two_group", "paired"):
        out["d"] = _require_number(effect.get("d"), "d", where)
        if etype == "two_group" and "allocation" in effect:
            alloc = _require_number(
                effect["allocation"], "allocation", where, upper=1.0
            )
            out["allocation"] = alloc
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
    return merged, warnings
