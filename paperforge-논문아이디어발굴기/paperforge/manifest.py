"""Load and validate a dataset manifest (JSON, stdlib only).

A manifest describes the datasets a lab already holds. Example::

    {
      "study": "Sleep MoA pilot",
      "datasets": [
        {"name": "MoA EEG", "modality": "eeg", "n": 40,
         "variables": ["alpha_power", "theta_power"], "sampling_hz": 256},
        {"name": "Respiration band", "modality": "respiration", "n": 40,
         "variables": ["resp_rate", "rsa"]}
      ]
    }

Datasets sharing subjects (e.g. collected in the same session) are what make
cross-modal ideas feasible; the report flags when linkage is assumed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

# Canonical modality keys and the aliases (incl. Korean) that map onto them.
MODALITY_ALIASES = {
    "eeg": "eeg", "뇌파": "eeg", "eeg뇌파": "eeg",
    "watch": "watch", "smartwatch": "watch", "워치": "watch",
    "스마트워치": "watch", "hr": "watch", "hrv": "watch",
    "respiration": "respiration", "resp": "respiration",
    "호흡": "respiration", "호흡밴드": "respiration", "breathing": "respiration",
    "questionnaire": "questionnaire", "survey": "questionnaire",
    "설문": "questionnaire", "설문지": "questionnaire", "scale": "questionnaire",
    "psqi": "questionnaire", "self_report": "questionnaire",
    "behavior": "behavior", "usertest": "behavior", "user_test": "behavior",
    "유저테스트": "behavior", "behavioral": "behavior", "log": "behavior",
    "moa": "moa", "mechanism": "moa",
}

MODALITY_LABEL_KO = {
    "eeg": "EEG(뇌파)",
    "watch": "스마트워치(HR/HRV)",
    "respiration": "호흡밴드",
    "questionnaire": "설문/자기보고",
    "behavior": "행동/유저테스트",
    "moa": "MoA 테스트",
}


class ManifestError(ValueError):
    """Raised when a manifest is structurally invalid."""


@dataclass
class Dataset:
    name: str
    modality: str  # canonical key
    raw_modality: str
    n: Optional[int]
    variables: list = field(default_factory=list)
    sampling_hz: Optional[float] = None
    notes: str = ""


@dataclass
class Manifest:
    study: str
    datasets: list  # list[Dataset]
    warnings: list = field(default_factory=list)

    def modalities(self) -> set:
        return {d.modality for d in self.datasets if d.modality}


def normalize_modality(value: str):
    """Return (canonical_key_or_None, was_recognized)."""
    key = str(value).strip().lower().replace(" ", "").replace("-", "")
    canon = MODALITY_ALIASES.get(key)
    return canon, canon is not None


def parse_manifest(data: dict) -> Manifest:
    """Validate a parsed JSON object into a :class:`Manifest`."""
    if not isinstance(data, dict):
        raise ManifestError("Manifest root must be a JSON object.")
    raw_datasets = data.get("datasets")
    if not isinstance(raw_datasets, list) or not raw_datasets:
        raise ManifestError(
            "Manifest must contain a non-empty 'datasets' array."
        )

    warnings: list = []
    datasets: list = []
    for i, raw in enumerate(raw_datasets):
        if not isinstance(raw, dict):
            raise ManifestError(f"datasets[{i}] must be an object.")
        if "modality" not in raw:
            raise ManifestError(f"datasets[{i}] is missing required 'modality'.")
        raw_mod = raw["modality"]
        canon, ok = normalize_modality(raw_mod)
        if not ok:
            warnings.append(
                f"datasets[{i}] modality '{raw_mod}' is unrecognized; it will "
                "not match any idea template."
            )
            canon = ""
        name = raw.get("name") or (canon or raw_mod) or f"dataset_{i}"

        n = raw.get("n")
        if n is not None:
            try:
                # bool is an int subclass — reject true/false as a sample size.
                if isinstance(n, bool):
                    raise ValueError
                # A non-integer count (e.g. 40.7) is almost certainly a mistake.
                if isinstance(n, float) and not n.is_integer():
                    raise ValueError
                n = int(n)
                if n <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                warnings.append(
                    f"{name}: 'n'={raw.get('n')!r} is not a positive integer; "
                    "treating sample size as unknown."
                )
                n = None

        variables = raw.get("variables") or []
        if not isinstance(variables, list):
            raise ManifestError(f"{name}: 'variables' must be an array.")
        variables = [str(v) for v in variables]

        sampling = raw.get("sampling_hz")
        try:
            sampling = float(sampling) if sampling is not None else None
        except (TypeError, ValueError):
            sampling = None

        datasets.append(
            Dataset(
                name=str(name),
                modality=canon,
                raw_modality=str(raw_mod),
                n=n,
                variables=variables,
                sampling_hz=sampling,
                notes=str(raw.get("notes", "")),
            )
        )

    study = str(data.get("study") or "Unnamed study")
    return Manifest(study=study, datasets=datasets, warnings=warnings)


def load_manifest(path: str) -> Manifest:
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"Could not parse JSON: {exc}") from exc
    return parse_manifest(data)
