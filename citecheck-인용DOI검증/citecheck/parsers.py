"""Reference parsing: BibTeX, plain DOI lists, and free-text reference lists.

The goal is to extract, for each reference, whatever the author *claimed* —
DOI, title, first-author surname, and year — so the verifier can compare those
claims against Crossref's authoritative record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# A DOI is "10." followed by a registrant code and a suffix. The suffix is
# deliberately permissive (DOIs may contain balanced parentheses, e.g.
# Elsevier's ``10.1016/S0140-6736(97)11096-0``); we only exclude whitespace,
# angle brackets, and quote characters, then trim trailing punctuation with
# bracket balancing in ``_clean_doi``. The registrant code keeps a >=4-digit
# lower bound so clinical dosing text like "10.55/kg" is not misread as a DOI.
_DOI_RE = re.compile(r"10\.\d{4,}/[^\s<>\"']+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")

# Common human-facing prefixes wrapped around a DOI (reference managers export
# DOIs as full URLs or with a "doi:" label).
_DOI_PREFIX_RE = re.compile(
    r"^\s*(?:https?://(?:dx\.)?doi\.org/|https?://|doi:\s*)",
    re.IGNORECASE,
)
_DOI_TRAIL = ".,;:'\"”’`)]}>"


@dataclass
class Reference:
    """A single citation as the author wrote it."""

    raw: str
    doi: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None  # first-author surname, best effort
    year: Optional[int] = None
    key: Optional[str] = None  # BibTeX cite key, if available
    fields: dict = field(default_factory=dict)
    # True when author/year were *guessed* from free text (unstructured), so the
    # verifier should not raise noisy author/year mismatches on them.
    heuristic_fields: bool = False

    def label(self) -> str:
        """A short human-readable identifier for reports."""
        if self.key:
            return self.key
        if self.doi:
            return self.doi
        if self.author and self.year:
            return f"{self.author} ({self.year})"
        snippet = self.raw.strip().replace("\n", " ")
        return (snippet[:50] + "…") if len(snippet) > 50 else snippet


def _clean_doi(doi: str) -> Optional[str]:
    """Normalise a raw DOI string: strip URL/``doi:`` prefix and trailing junk.

    Trailing punctuation is stripped, but a closing bracket that has a matching
    opener inside the DOI is preserved (so ``(97)11096-0`` survives while a
    wrapping ``)`` from "(doi: 10.x)" is removed).
    """
    if not doi:
        return None
    doi = _DOI_PREFIX_RE.sub("", doi.strip()).strip()
    # Drop a URL query string / fragment (e.g. "?utm_source=…" from a
    # browser-copied link); real DOIs do not contain '?' or '#'.
    doi = re.split(r"[?#]", doi, maxsplit=1)[0]
    doi = doi.lower()
    while doi and doi[-1] in _DOI_TRAIL:
        if doi[-1] == ")" and doi.count("(") >= doi.count(")"):
            break
        if doi[-1] == "]" and doi.count("[") >= doi.count("]"):
            break
        doi = doi[:-1]
    return doi or None


def find_doi(text: str) -> Optional[str]:
    """Return the first DOI found in *text*, normalised, or None.

    Handles DOIs embedded in URLs or ``doi:`` labels by matching the ``10.…``
    core directly. Uses a >=4-digit registrant lower bound so clinical dosing
    text like "10.55/kg" is not misread as a DOI.
    """
    m = _DOI_RE.search(text)
    return _clean_doi(m.group(0)) if m else None


# For an *explicit* ``doi={...}`` field there is no free-text ambiguity, so the
# registrant code may have any number of digits.
_DOI_FIELD_RE = re.compile(r"10\.\d+/[^\s<>\"']+", re.IGNORECASE)


def normalize_doi_field(value: str) -> Optional[str]:
    """Normalise the value of an explicit ``doi`` field (BibTeX/CSL).

    Strips a URL/``doi:`` prefix and trailing junk. More lenient than
    ``find_doi`` because the field is known to hold a DOI.
    """
    if not value:
        return None
    stripped = _DOI_PREFIX_RE.sub("", value.strip()).strip()
    m = _DOI_FIELD_RE.search(stripped)
    if m:
        return _clean_doi(m.group(0))
    # No DOI core present — not a usable DOI.
    return None


def find_year(text: str) -> Optional[int]:
    m = _YEAR_RE.search(text)
    return int(m.group(0)) if m else None


# --- BibTeX -----------------------------------------------------------------

# Entry header: ``@type{key,``.  The key is bounded to exclude commas, braces,
# and whitespace so it can never run past its own entry.
_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,{}\s]*)\s*,", re.IGNORECASE)
# BibTeX "entry" types that are not references and must be skipped.
_NON_REFERENCE_TYPES = {"string", "comment", "preamble"}


def _scan_entries(text: str) -> list[tuple[str, str, Optional[str]]]:
    """Scan top-level ``@type{key, …}`` entries in a single left-to-right pass.

    Returns (entry_type, key, body) tuples; ``body`` is None when the entry's
    braces never balanced (unterminated/malformed). Because the pointer only
    ever moves forward and each character is visited once, this is O(n) — no
    quadratic rescanning — and, crucially, a ``@type{…}``-looking string *inside*
    a field value (e.g. a title that discusses BibTeX, or a URL) is consumed as
    part of the enclosing entry's body via brace depth, never mistaken for a new
    top-level entry.
    """
    results: list[tuple[str, str, Optional[str]]] = []
    i, n = 0, len(text)
    while i < n:
        m = _ENTRY_RE.search(text, i)
        if not m:
            break
        entry_type, key = m.group(1), m.group(2).strip()
        start = text.index("{", m.start())
        depth = 0
        end: Optional[int] = None
        for j in range(start, n):
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end is None:
            # Unterminated: the remaining text cannot be reliably delimited.
            results.append((entry_type, key, None))
            break
        results.append((entry_type, key, text[start + 1 : end]))
        i = end + 1
    return results


def _split_bibtex_entries(text: str) -> list[tuple[str, str, str]]:
    """Return (entry_type, key, body) for each well-formed reference entry."""
    return [
        (t, k, body)
        for (t, k, body) in _scan_entries(text)
        if body is not None and t.lower() not in _NON_REFERENCE_TYPES
    ]


def count_malformed_entries(text: str) -> int:
    """Number of reference entries whose braces never balanced (skipped)."""
    return sum(
        1
        for (t, _k, body) in _scan_entries(text)
        if body is None and t.lower() not in _NON_REFERENCE_TYPES
    )


_FIELD_RE = re.compile(r"(\w+)\s*=\s*", re.IGNORECASE)


def _parse_bibtex_fields(body: str) -> dict:
    """Parse `field = {value}` / `field = "value"` / `field = value` pairs."""
    fields: dict = {}
    i = 0
    n = len(body)
    while i < n:
        m = _FIELD_RE.search(body, i)
        if not m:
            break
        name = m.group(1).lower()
        j = m.end()
        if j >= n:
            break
        if body[j] == "{":
            depth = 0
            for k in range(j, n):
                if body[k] == "{":
                    depth += 1
                elif body[k] == "}":
                    depth -= 1
                    if depth == 0:
                        fields[name] = body[j + 1 : k]
                        i = k + 1
                        break
            else:
                break
        elif body[j] == '"':
            for k in range(j + 1, n):
                if body[k] == '"':
                    fields[name] = body[j + 1 : k]
                    i = k + 1
                    break
            else:
                break
        else:
            end = body.find(",", j)
            if end == -1:
                end = n
            fields[name] = body[j:end].strip()
            i = end + 1
    return fields


def _clean_bibtex_value(value: str) -> str:
    value = value.replace("\n", " ")
    value = re.sub(r"[{}]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _first_author_surname(author_field: str) -> Optional[str]:
    """Best-effort first-author surname from a BibTeX `author` field."""
    if not author_field:
        return None
    first = re.split(r"\band\b", author_field, maxsplit=1)[0].strip()
    first = _clean_bibtex_value(first)
    if "," in first:  # "Surname, Given"
        return first.split(",")[0].strip() or None
    parts = first.split()
    return parts[-1] if parts else None


def parse_bibtex(text: str) -> list[Reference]:
    refs = []
    for entry_type, key, body in _split_bibtex_entries(text):
        fields = _parse_bibtex_fields(body)
        title = _clean_bibtex_value(fields.get("title", "")) or None
        author = _first_author_surname(fields.get("author", ""))
        year = None
        if fields.get("year"):
            year = find_year(fields["year"])
        # The `doi` field may be a bare DOI, a full URL, or a `doi:`-prefixed
        # string — extract the DOI core in every case.
        doi = normalize_doi_field(fields["doi"]) if fields.get("doi") else None
        refs.append(
            Reference(
                raw=body.strip(),
                doi=doi,
                title=title,
                author=author,
                year=year,
                key=key or None,
                fields={"type": entry_type, **fields},
            )
        )
    return refs


# --- Plain text / DOI lists -------------------------------------------------


def parse_text(text: str) -> list[Reference]:
    """Parse a newline- or blank-line-separated list of references.

    Each non-empty line (or paragraph) becomes one reference. A bare DOI line
    is treated as a DOI-only reference.
    """
    # Split on blank lines if any are present (allowing whitespace-only blank
    # lines); otherwise split on single newlines. The guard and the split use
    # the same pattern so they can never disagree.
    if re.search(r"\n\s*\n", text):
        blocks = re.split(r"\n\s*\n", text.strip())
    else:
        blocks = text.strip().splitlines()
    refs = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        doi = find_doi(block)
        year = find_year(block)
        author = _guess_text_author(block)
        refs.append(
            Reference(raw=block, doi=doi, year=year, author=author, heuristic_fields=True)
        )
    return refs


def _guess_text_author(block: str) -> Optional[str]:
    """Grab a leading surname from a reference string like 'Kim H, Lee S. ...'."""
    m = re.match(r"\s*\[?\d*\]?\.?\s*([A-Z][A-Za-z'\-]+)", block)
    return m.group(1) if m else None


def parse_references(text: str, fmt: str = "auto") -> list[Reference]:
    """Parse *text* into references.

    fmt: "bibtex", "text", or "auto" (detect from content).
    """
    if fmt == "auto":
        # Require a real ``@type{key,`` entry header — a stray "@" (e.g. an
        # email address) in a plain-text reference must not route to BibTeX.
        fmt = "bibtex" if _ENTRY_RE.search(text) else "text"
    if fmt == "bibtex":
        return parse_bibtex(text)
    return parse_text(text)
