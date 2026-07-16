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

import csv
import io
import json
import os
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
                # Coerce via float so JSON numbers *and* CSV strings behave the
                # same: 40, 40.0 and "40.0" all become 40; 40.7/"40.7"/"lots"/
                # inf/nan are rejected. (float() rejects non-numeric strings.)
                fn = float(n)
                if not fn.is_integer():
                    raise ValueError
                n = int(fn)
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


# Column-header aliases (case-insensitive) for the CSV/TSV manifest format.
_CSV_COLUMN_ALIASES = {
    "name": "name", "이름": "name", "dataset": "name", "데이터셋": "name",
    "modality": "modality", "모달리티": "modality", "종류": "modality",
    "type": "modality",
    "n": "n", "표본수": "n", "samples": "n", "sample_size": "n", "표본": "n",
    "variables": "variables", "vars": "variables", "변수": "variables",
    "columns": "variables", "cols": "variables",
    "sampling_hz": "sampling_hz", "hz": "sampling_hz", "sampling": "sampling_hz",
    "샘플링": "sampling_hz", "sampling_rate": "sampling_hz",
    "notes": "notes", "note": "notes", "비고": "notes", "memo": "notes",
    "study": "study", "연구": "study", "연구명": "study",
}

# Variables in a single CSV cell are separated by ';' or '|' (never ',', which
# is the field delimiter). Whitespace around each token is stripped.
_CSV_VAR_SPLIT = (";", "|")


def _decode_bytes(raw: bytes):
    """Decode manifest bytes, tolerating BOM and common Korean-Excel encodings.

    Returns ``(text, warning_or_None)``. Real-world clinical CSVs exported from
    Korean Excel are frequently CP949/EUC-KR, not UTF-8; we fall back rather
    than crashing, and warn so the user can re-save as UTF-8.
    """
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            pass
    for enc in ("cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            return text, (
                f"파일이 UTF-8이 아니어서 {enc}로 해석했습니다 — 가능하면 "
                "UTF-8로 다시 저장하세요."
            )
        except UnicodeDecodeError:
            pass
    # Last resort: latin-1 never fails; flag that bytes may be garbled.
    return raw.decode("latin-1"), (
        "파일 인코딩을 인식할 수 없어 latin-1로 강제 해석했습니다 — 한글이 "
        "깨질 수 있으니 UTF-8로 다시 저장하세요."
    )


def parse_csv_manifest(text: str, study: Optional[str] = None,
                       delimiter: str = ",") -> Manifest:
    """Parse a CSV/TSV data inventory into a :class:`Manifest`.

    One row per dataset. Headers are matched case-insensitively against a set of
    English/Korean aliases; ``modality`` is the only required column. The
    ``variables`` cell may list several columns separated by ``;`` or ``|``.
    A ``study`` column (first non-empty value wins) names the study; otherwise
    the ``study`` argument (typically the filename stem) is used.
    """
    stripped = text.lstrip("﻿")
    try:
        all_rows = list(csv.reader(io.StringIO(stripped), delimiter=delimiter))
    except csv.Error as exc:
        # e.g. a field larger than csv's 128 KB limit, or malformed quoting —
        # surface as a clean ManifestError (exit 2) instead of a raw traceback.
        raise ManifestError(f"CSV를 해석할 수 없습니다: {exc}") from exc

    # Drop leading blank / '#'-comment lines ONLY until the header row, so a
    # hand-kept inventory can start with "# notes" but a legitimate first-column
    # value like "#3 EEG" in a *data* row is never silently discarded.
    header = None
    body: list = []
    for r in all_rows:
        if header is None:
            if not any(cell.strip() for cell in r):
                continue  # blank line before header
            if r and r[0].lstrip().startswith("#"):
                continue  # comment line before header
            header = [_CSV_COLUMN_ALIASES.get(h.strip().lower(), "") for h in r]
            continue
        if any(cell.strip() for cell in r):  # skip fully blank data rows
            body.append(r)

    if header is None:
        raise ManifestError("CSV 매니페스트가 비어 있습니다 (헤더 행 필요).")
    if "modality" not in header:
        raise ManifestError(
            "CSV 헤더에 'modality'(또는 '모달리티'/'종류') 열이 필요합니다."
        )
    dup = {c for c in header if c and header.count(c) > 1}

    study_from_col = None
    datasets: list = []
    for r in body:
        record: dict = {}
        for col, cell in zip(header, r):
            if not col:
                continue
            value = cell.strip()
            record[col] = value
        # Pull the study name out of the row, if present.
        if record.get("study") and study_from_col is None:
            study_from_col = record["study"]
        record.pop("study", None)

        # A row with no modality at all is blank filler — skip it silently.
        if not record.get("modality"):
            continue

        # Empty cells mean "not provided" — drop them so parse_manifest treats
        # sample size / variables as absent instead of warning on "".
        if not record.get("n"):
            record.pop("n", None)
        if not record.get("sampling_hz"):
            record.pop("sampling_hz", None)
        raw_vars = record.pop("variables", "")
        variables = [raw_vars]
        for sep in _CSV_VAR_SPLIT:
            variables = [tok for chunk in variables for tok in chunk.split(sep)]
        record["variables"] = [v.strip() for v in variables if v.strip()]
        datasets.append(record)

    if not datasets:
        raise ManifestError(
            "CSV에 유효한 데이터셋 행이 없습니다 (modality 값이 있는 행 필요)."
        )

    resolved_study = study_from_col or study or "Unnamed study"
    manifest = parse_manifest({"study": resolved_study, "datasets": datasets})
    if dup:
        # Duplicate headers collapse to the last column (data loss); warn rather
        # than fail so a mislabeled export is still usable.
        cols = ", ".join(sorted(dup))
        manifest.warnings.insert(
            0, f"CSV 헤더에 중복된 열({cols})이 있어 마지막 값만 사용합니다."
        )
    return manifest


def load_manifest(path: str) -> Manifest:
    """Load a manifest from a ``.json``, ``.csv`` or ``.tsv`` file.

    Format is chosen by extension; for anything else we sniff the first
    non-whitespace byte (``{``/``[`` → JSON, otherwise CSV). Encoding is decoded
    tolerantly (BOM / CP949 / EUC-KR fallbacks) rather than assuming UTF-8.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    text, enc_warning = _decode_bytes(raw)

    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        fmt = "json"
    elif ext in (".csv", ".tsv"):
        fmt = "csv"
    else:
        head = text.lstrip()
        fmt = "json" if head[:1] in ("{", "[") else "csv"

    stem = os.path.splitext(os.path.basename(path))[0]

    def _as_json():
        try:
            return parse_manifest(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ManifestError(f"Could not parse JSON: {exc}") from exc

    def _as_csv():
        delimiter = "\t" if ext == ".tsv" else ","
        return parse_csv_manifest(text, study=stem, delimiter=delimiter)

    looks_json = text.lstrip()[:1] in ("{", "[")
    if fmt == "json":
        primary, other, other_is_json = _as_json, _as_csv, False
    else:
        primary, other, other_is_json = _as_csv, _as_json, True

    try:
        manifest = primary()
    except ManifestError as primary_exc:
        # Wrong extension for the content? If the bytes clearly look like the
        # other format, retry it before giving up (so a JSON payload saved as
        # .csv still loads). Re-raise the original error if the retry fails too.
        if other_is_json == looks_json:
            try:
                manifest = other()
            except ManifestError:
                raise primary_exc from None
        else:
            raise

    if enc_warning:
        manifest.warnings.insert(0, enc_warning)
    return manifest
