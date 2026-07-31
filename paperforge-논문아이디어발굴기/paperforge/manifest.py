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
import re
from dataclasses import dataclass, field
from typing import Optional

# Separators allowed between modalities in a linkage key ("eeg+watch", "뇌파×호흡").
# 'x'/'and' only as standalone words, so an alias that merely *contains* them
# (e.g. "user_test") is never split apart.
_LINK_SPLIT_RE = re.compile(r"[+&×*]|\bx\b|\band\b", re.IGNORECASE)

# A number written with thousands separators, e.g. "1,234" or "12,345,678".
_THOUSANDS_RE = re.compile(r"\d{1,3}(,\d{3})+")

# Refuse counts no cohort could have; also keeps absurd values out of the
# sample-size formulas (a 309-digit N used to be printed into the table).
_MAX_COUNT = 10 ** 9

# A dataset *inventory* is a small file. This also stops an unbounded stream
# (e.g. /dev/zero) from being read until memory runs out.
_MAX_FILE_BYTES = 32 * 1024 * 1024

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
    "행동": "behavior", "행동로그": "behavior",
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
    # Declared subject overlap: frozenset of canonical modality keys -> number of
    # subjects that have ALL of them. Optional, but it is the only honest way to
    # size a cross-modal analysis (see :func:`parse_linked_n`).
    linked_n: dict = field(default_factory=dict)

    def modalities(self) -> set:
        return {d.modality for d in self.datasets if d.modality}


def normalize_modality(value: str):
    """Return (canonical_key_or_None, was_recognized)."""
    key = str(value).strip().lower().replace(" ", "").replace("-", "")
    canon = MODALITY_ALIASES.get(key)
    return canon, canon is not None


# Cells that a spreadsheet uses to mean "no value" rather than a number.
_NULLISH = {"", "-", "--", "n/a", "na", "n.a.", "none", "null", "?", "미상",
            "없음", "unknown", "unk", "tbd", "미정"}
# Trailing units a human types after a count ("40명", "40 subjects").
_COUNT_SUFFIXES = ("명", "인", "subjects", "subject", "ppl", "people", "cases",
                   "case", "pts", "n")


def _clean_count(value):
    """Normalise a messy sample-size cell to an int, ``None``, or raise.

    Handles what real inventories contain: ``"1,234"``, ``"40명"``, ``" 40 "``,
    ``"n/a"``, ``"미상"``. Returns ``None`` for an explicit "not provided"
    marker (silently — that is not a data error) and raises ``ValueError`` for
    anything genuinely unparseable so the caller can warn.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; not a sample size
        raise ValueError("boolean is not a sample size")
    if isinstance(value, (int, float)):
        text = repr(value)
    else:
        text = str(value)
    # Normalise U+00A0 (non-breaking space) to a plain space — Excel and web
    # copy-paste produce it constantly and it is invisible in a diff.
    text = text.strip().replace("\u00a0", " ")
    if text.lower() in _NULLISH:
        return None
    # Thousands separators and inner spaces: "1,234" / "1 234" -> "1234".
    # ONLY a well-formed grouping is collapsed. Stripping every comma turned
    # "40,50" (two cohort sizes typed into one cell) into 4050 and the European
    # decimal "1,5" into 15 — silently, and in the direction that flips a
    # verdict to "충분 가능". Anything else keeps its commas and fails below.
    compact = text.replace(" ", "")
    if "," in compact:
        if _THOUSANDS_RE.fullmatch(compact):
            compact = compact.replace(",", "")
        else:
            raise ValueError(
                f"ambiguous comma in sample size {text!r} — use a plain integer"
            )
    lowered = compact.lower()
    for suffix in _COUNT_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            compact = compact[: len(compact) - len(suffix)]
            break
    fn = float(compact)  # raises ValueError on non-numeric text
    if not fn.is_integer():
        raise ValueError("sample size must be a whole number")
    n = int(fn)
    if n <= 0:
        raise ValueError("sample size must be positive")
    if n > _MAX_COUNT:
        raise ValueError(f"sample size {n} exceeds the {_MAX_COUNT:,} ceiling")
    return n


def _looks_like_linkage(cell: str) -> bool:
    """True only when a CSV modality cell names >= 2 *recognized* modalities.

    A bare ``_LINK_SPLIT_RE`` search was too eager: a footnote marker or a
    trailing separator ("EEG*", "respiration+") matched, so the row was
    reclassified as a linkage declaration and the dataset silently vanished from
    the manifest — with a warning about a `linked_n` feature the user never
    used. Requiring two resolvable modalities makes the split deliberate.
    """
    if not _LINK_SPLIT_RE.search(cell):
        return False
    tokens = [t for t in _LINK_SPLIT_RE.split(cell) if t and t.strip()]
    recognized = {normalize_modality(t)[0] for t in tokens
                  if normalize_modality(t)[1]}
    return len(recognized) >= 2


def parse_linked_n(raw, warnings: list) -> dict:
    """Parse a declared subject-overlap map into ``{frozenset(keys): n}``.

    Accepts ``{"eeg+watch": 30, "뇌파 + 호흡": 28}`` — keys are modality lists
    joined by ``+`` (``&``/``x``/``×`` also work), values are subject counts.
    Single-modality keys are rejected: an overlap needs at least two modalities,
    and a one-modality count already belongs in that dataset's ``n``.
    """
    out: dict = {}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise ManifestError("'linked_n' must be an object mapping 'a+b' -> count.")
    for key, value in raw.items():
        tokens = [t for t in _LINK_SPLIT_RE.split(str(key)) if t.strip()]
        canon = []
        bad = False
        for tok in tokens:
            c, ok = normalize_modality(tok)
            if not ok:
                warnings.append(
                    f"linked_n '{key}': 모달리티 '{tok.strip()}'를 인식할 수 없어 "
                    "이 연결 표본수를 무시합니다."
                )
                bad = True
                break
            canon.append(c)
        if bad:
            continue
        combo = frozenset(canon)
        if len(combo) < 2:
            warnings.append(
                f"linked_n '{key}': 모달리티가 2개 미만이라 무시합니다 "
                "(연결 표본수는 두 모달리티 이상에만 의미가 있습니다)."
            )
            continue
        try:
            n = _clean_count(value)
        except (TypeError, ValueError):
            n = None
        if n is None:
            warnings.append(
                f"linked_n '{key}': 값 {value!r}이 양의 정수가 아니라 무시합니다."
            )
            continue
        # Repeated key (e.g. "eeg+watch" and "watch+eeg"): keep the smaller,
        # conservative count rather than letting dict order decide.
        out[combo] = n if combo not in out else min(out[combo], n)
    return out


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
                f"datasets[{i}] modality '{_safe_snippet(raw_mod)}' is "
                "unrecognized; it will not match any idea template."
            )
            canon = ""
        name = raw.get("name") or (canon or raw_mod) or f"dataset_{i}"

        n = raw.get("n")
        if n is not None:
            try:
                # Tolerates JSON numbers, CSV strings, thousands separators and
                # Korean count suffixes; None for explicit "not provided".
                n = _clean_count(n)
            except (TypeError, ValueError, OverflowError):
                warnings.append(
                    f"{name}: 'n'={raw.get('n')!r} is not a positive integer; "
                    "treating sample size as unknown."
                )
                n = None

        variables = raw.get("variables") or []
        if not isinstance(variables, list):
            raise ManifestError(f"{name}: 'variables' must be an array.")
        # Strip, drop blanks, and de-duplicate case-insensitively while keeping
        # first-seen order — exported inventories routinely repeat columns.
        variables = _dedupe_variables(str(v) for v in variables)

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
    raw_links = data.get("linked_n")
    if raw_links is None:
        raw_links = data.get("linked") or data.get("연결표본수")
    linked = parse_linked_n(raw_links, warnings)
    declared = {m for combo in linked for m in combo}
    present = {d.modality for d in datasets if d.modality}
    missing = sorted(declared - present)
    if missing:
        warnings.append(
            "linked_n에 선언된 모달리티 중 데이터셋에 없는 항목: "
            + ", ".join(missing)
        )
    return Manifest(
        study=study, datasets=datasets, warnings=warnings, linked_n=linked
    )


def _dedupe_variables(values) -> list:
    """Strip/blank-filter/case-insensitively de-duplicate a variable list."""
    seen: set = set()
    out: list = []
    for v in values:
        v = v.strip()
        if not v:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


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


def _safe_snippet(value, limit: int = 60) -> str:
    """One-line, length-capped rendering of a user value for a warning.

    An unterminated CSV quote swallows the rest of the file into a single field;
    echoing it raw put literal newlines inside a Markdown blockquote and dumped
    the file back at the user.
    """
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


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
    links: dict = {}
    ragged: list = []
    for row_no, r in enumerate(body, 1):
        if len(r) != len(header):
            # Silently zip-truncating a long row (or leaving a short one full of
            # blanks) hides the single most common spreadsheet-export defect.
            ragged.append((row_no, len(r)))
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

        # A modality cell naming several modalities ("뇌파+워치") is not a
        # dataset but a declaration of how many subjects have all of them —
        # the spreadsheet-friendly spelling of JSON's `linked_n`.
        mod_cell = record["modality"]
        if _looks_like_linkage(mod_cell):
            links[mod_cell] = record.get("n", "")
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
    if ragged:
        shown = ", ".join(f"{no}행({cols}열)" for no, cols in ragged[:5])
        more = f" 외 {len(ragged) - 5}건" if len(ragged) > 5 else ""
        warn_ragged = (
            f"CSV 헤더는 {len(header)}열인데 열 수가 다른 행이 있습니다: "
            f"{shown}{more}. 남는 칸은 무시되고 모자란 칸은 빈 값으로 처리됩니다."
        )
    else:
        warn_ragged = None
    manifest = parse_manifest(
        {"study": resolved_study, "datasets": datasets, "linked_n": links}
    )
    if warn_ragged:
        manifest.warnings.insert(0, warn_ragged)
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
        raw = fh.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise ManifestError(
            f"매니페스트가 너무 큽니다(> {_MAX_FILE_BYTES // (1024 * 1024)}MB). "
            "데이터 인벤토리 목록만 넣으세요 — 원본 데이터 파일이 아닙니다."
        )
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
