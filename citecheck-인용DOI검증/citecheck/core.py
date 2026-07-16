"""Crossref lookups and claim-vs-record comparison."""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from .parsers import Reference

CROSSREF_API = "https://api.crossref.org/works/"
DOI_RESOLVER = "https://doi.org/"
DEFAULT_UA = "citecheck/0.1 (https://github.com/hyeonjoong/citecheck; mailto:citecheck@example.com)"

# Severity levels for findings.
OK = "ok"
WARNING = "warning"
ERROR = "error"


@dataclass
class Finding:
    severity: str
    message: str


@dataclass
class CheckResult:
    reference: Reference
    findings: list[Finding] = field(default_factory=list)
    resolved_doi: Optional[str] = None
    crossref: Optional[dict] = None

    @property
    def status(self) -> str:
        if any(f.severity == ERROR for f in self.findings):
            return ERROR
        if any(f.severity == WARNING for f in self.findings):
            return WARNING
        return OK

    def add(self, severity: str, message: str) -> None:
        self.findings.append(Finding(severity, message))


class CrossrefClient:
    """Tiny Crossref client over the standard library (no third-party deps)."""

    def __init__(
        self,
        mailto: Optional[str] = None,
        timeout: float = 15.0,
        retries: int = 2,
        sleep: float = 1.0,
        _fetch=None,
        _resolve=None,
    ):
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.user_agent = (
            f"citecheck/0.1 (https://github.com/hyeonjoong/citecheck; mailto:{mailto})"
            if mailto
            else DEFAULT_UA
        )
        # _fetch/_resolve let tests inject fake transports:
        #   _fetch:   doi -> message dict | None
        #   _resolve: doi -> bool (does the DOI resolve at doi.org?)
        self._fetch = _fetch
        self._resolve = _resolve
        # In-run memoisation so a manuscript that cites the same DOI twice (or a
        # re-run over the same file) hits the network only once per DOI.
        self._fetch_cache: dict[str, Optional[dict]] = {}
        self._resolve_cache: dict[str, bool] = {}

    def fetch(self, doi: str) -> Optional[dict]:
        """Return the Crossref `message` for *doi*, or None if not found."""
        if doi in self._fetch_cache:
            return self._fetch_cache[doi]
        result = self._fetch(doi) if self._fetch is not None else self._fetch_network(doi)
        self._fetch_cache[doi] = result
        return result

    def _fetch_network(self, doi: str) -> Optional[dict]:
        url = CROSSREF_API + urllib.parse.quote(doi, safe="")
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("message")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                last_err = e
            except (
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as e:
                last_err = e
            if attempt < self.retries:
                time.sleep(self.sleep * (attempt + 1))
        if last_err:
            raise last_err
        return None

    def resolve(self, doi: str) -> bool:
        """Return True if *doi* resolves at doi.org (independent of Crossref).

        Used to distinguish a genuinely broken DOI from one that resolves but is
        simply not in Crossref's ``works`` index (e.g. a DataCite/dataset DOI).
        """
        if doi in self._resolve_cache:
            return self._resolve_cache[doi]
        result = self._resolve(doi) if self._resolve is not None else self._resolve_network(doi)
        self._resolve_cache[doi] = result
        return result

    def _resolve_network(self, doi: str) -> bool:
        url = DOI_RESOLVER + urllib.parse.quote(doi, safe="/()")
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 400
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False  # doi.org 404 => the DOI is genuinely not registered
            raise  # 5xx/429/etc. are transient — let the caller treat as inconclusive
        # URLError/TimeoutError propagate: a transient failure must NOT be
        # reported as "does not resolve" (that would be a false hard error).


def _fold(s: str) -> str:
    """Strip diacritics so "Müller" and "Muller" compare equal."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return " ".join(_fold(s).lower().split())


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _as_list(value) -> list:
    """Coerce a Crossref field that should be a list into one, defensively."""
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _crossref_title_candidates(message: dict) -> list[str]:
    """Return candidate titles: the main title, and main+subtitle joined.

    Crossref stores subtitles separately, so a citation that (correctly)
    includes the subtitle must be compared against the concatenation too, or it
    falsely reads as a title mismatch.
    """
    titles = [t for t in _as_list(message.get("title")) if isinstance(t, str) and t.strip()]
    if not titles:
        return []
    main = titles[0]
    candidates = [main]
    subs = [s for s in _as_list(message.get("subtitle")) if isinstance(s, str) and s.strip()]
    if subs:
        candidates.append(f"{main}: {subs[0]}")
    return candidates


def _crossref_title(message: dict) -> Optional[str]:
    cands = _crossref_title_candidates(message)
    return cands[0] if cands else None


def _crossref_years(message: dict) -> set[int]:
    """All plausible publication years across Crossref's date fields.

    A paper published online in year N and in print in N+1 legitimately carries
    both; the citation matches if it equals *any* of them.
    """
    years: set[int] = set()
    for key in ("published-print", "published-online", "published", "issued", "created"):
        field_val = message.get(key)
        if not isinstance(field_val, dict):
            continue
        parts = field_val.get("date-parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, list) and part and isinstance(part[0], int):
                years.add(part[0])
            elif isinstance(part, list) and part:
                try:
                    years.add(int(part[0]))
                except (TypeError, ValueError):
                    pass
    return years


def _crossref_first_author(message: dict) -> Optional[str]:
    for a in _as_list(message.get("author")):
        if isinstance(a, dict) and a.get("family"):
            return a["family"]
    return None


def _crossref_all_author_families(message: dict) -> list[str]:
    return [
        a["family"]
        for a in _as_list(message.get("author"))
        if isinstance(a, dict) and a.get("family")
    ]


def _retraction_notice(message: dict) -> Optional[str]:
    """Return the DOI of a retraction notice for *message*, if exposed.

    Falls through to whichever field actually carries a DOI (``update-by`` may
    match on type but omit the DOI while ``update-to`` carries it).
    """
    for f in ("update-by", "update-to"):
        for upd in _as_list(message.get(f)):
            if isinstance(upd, dict) and "retract" in str(upd.get("type", "")).lower():
                doi = upd.get("DOI") or upd.get("doi")
                if doi:
                    return doi
    return None


def _is_retracted(message: dict) -> bool:
    """Best-effort retraction detection against a Crossref *article* record.

    Crossref marks a retracted article via ``update-by`` (the Crossmark model);
    the retraction *notice* carries ``update-to`` pointing back. We check both,
    plus an ``is-retracted-by`` relation (the retracted work pointing to its
    notice). We deliberately do NOT match ``is-retraction-of`` — that identifies
    the notice itself, not a retracted paper. Note: Crossref coverage of
    retractions is incomplete, so a clean result is not a guarantee.
    """
    if str(message.get("type", "")).lower() == "retraction":
        return True
    for f in ("update-by", "update-to"):
        for upd in _as_list(message.get(f)):
            if isinstance(upd, dict) and "retract" in str(upd.get("type", "")).lower():
                return True
    relation = message.get("relation")
    if isinstance(relation, dict):
        for key in relation:
            if isinstance(key, str) and "retracted-by" in key.lower():
                return True
    return False


def check_reference(
    ref: Reference,
    client: CrossrefClient,
    title_threshold: float = 0.80,
    author_threshold: float = 0.85,
    resolve_unknown: bool = True,
) -> CheckResult:
    """Verify a single reference against Crossref and return findings.

    Never raises on a malformed Crossref record or lookup failure — such
    problems become WARNINGs so one bad record cannot abort a whole batch.
    """
    result = CheckResult(reference=ref)

    if not ref.doi:
        result.add(WARNING, "No DOI found — cannot verify against Crossref.")
        return result

    try:
        message = client.fetch(ref.doi)
    except Exception as e:  # network/transport failure, not a citation problem
        result.add(WARNING, f"Lookup failed ({type(e).__name__}): {e}")
        return result

    if message is None:
        # Not in Crossref's works index. Distinguish "broken DOI" from "resolves
        # but not a Crossref journal article" (e.g. a DataCite/dataset DOI) — and
        # never turn a transient doi.org failure into a false hard error.
        if resolve_unknown:
            try:
                resolved = client.resolve(ref.doi)
            except Exception as e:
                result.add(
                    WARNING,
                    f"Lookup failed while confirming the DOI resolves "
                    f"({type(e).__name__}) — could not verify: {ref.doi}",
                )
                return result
        else:
            resolved = False
        if resolved:
            result.add(
                WARNING,
                f"DOI resolves but is not in Crossref — metadata not verified: {ref.doi}. "
                f"It may be a dataset/preprint/DataCite DOI.",
            )
        else:
            result.add(
                ERROR,
                f"DOI does not resolve anywhere (Crossref or doi.org) — "
                f"check for a typo: {ref.doi}",
            )
        return result

    if not isinstance(message, dict):
        result.add(WARNING, "Crossref returned an unexpected record shape — could not verify.")
        return result

    result.resolved_doi = message.get("DOI", ref.doi)
    result.crossref = message

    try:
        _compare(ref, message, result, title_threshold, author_threshold)
    except Exception as e:  # never let one weird record abort the batch
        result.add(WARNING, f"Could not fully compare metadata ({type(e).__name__}).")

    if not result.findings:
        cr_author = _crossref_first_author(message)
        cr_year = sorted(_crossref_years(message))
        cr_title = _crossref_title(message)
        year_disp = cr_year[0] if cr_year else "?"
        result.add(OK, f"Verified: {cr_author or '?'} ({year_disp}) — {cr_title or ref.doi}")
    return result


def _compare(
    ref: Reference,
    message: dict,
    result: CheckResult,
    title_threshold: float,
    author_threshold: float,
) -> None:
    if _is_retracted(message):
        notice = _retraction_notice(message)
        extra = f" (retraction notice: {notice})" if notice else ""
        result.add(ERROR, f"Reference appears to be RETRACTED according to Crossref.{extra}")

    # Title comparison — compare against the best-matching Crossref candidate
    # (main title, or main+subtitle) so a cited subtitle is not a false mismatch.
    cr_titles = _crossref_title_candidates(message)
    if ref.title and cr_titles:
        best = max(_similar(ref.title, t) for t in cr_titles)
        if best < title_threshold:
            result.add(
                WARNING,
                f"Title mismatch ({best:.0%} similar):\n"
                f"    cited:    {ref.title}\n"
                f"    crossref: {cr_titles[0]}",
            )

    # Year and first-author were guessed from free text when heuristic_fields is
    # set (e.g. "2000 patients … 2019" or a title-first line); those guesses are
    # unreliable, so skip their mismatch checks to avoid false alarms. DOI and
    # retraction checks still apply, and — since a free-text reference usually
    # quotes the paper's title verbatim — we can still catch a swapped DOI by
    # checking the cited text actually mentions the Crossref title.
    if ref.heuristic_fields:
        _check_text_mentions_title(ref, cr_titles, result)
        return

    # Year comparison — match against *any* of Crossref's dates.
    cr_years = _crossref_years(message)
    if ref.year and cr_years and ref.year not in cr_years:
        shown = ", ".join(str(y) for y in sorted(cr_years))
        result.add(WARNING, f"Year mismatch: cited {ref.year}, Crossref says {shown}.")

    # First-author comparison — tolerate diacritics; also accept a match against
    # any listed author (co-author ordering / equal-contribution differences).
    if ref.author:
        families = _crossref_all_author_families(message)
        if families:
            best = max(_similar(ref.author, fam) for fam in families)
            if best < author_threshold:
                result.add(
                    WARNING,
                    f"First-author mismatch: cited '{ref.author}', "
                    f"Crossref says '{families[0]}'.",
                )


def _alpha_tokens(text: str) -> list[str]:
    """Diacritic-folded alphabetic sub-words of length >= 3.

    Splits on any non-letter so "COVID-19" -> ["covid"] and "neural-networks" ->
    ["neural", "networks"], rather than dropping such tokens wholesale (which
    made overlap sensitive to punctuation differences between the citation and
    the Crossref record).
    """
    return [t for t in re.findall(r"[a-z]+", _norm(text)) if len(t) >= 3]


def _check_text_mentions_title(
    ref: Reference, cr_titles: list[str], result: CheckResult
) -> None:
    """For a free-text reference, warn if it doesn't mention the Crossref title.

    A correct free-text citation quotes the paper's title, so a low word overlap
    is a strong signal the DOI points to a *different* paper (a swapped DOI).
    Conservative on purpose: skipped entirely for bare-DOI lines (no prose to
    match), and only fires when overlap is well below half.
    """
    if not cr_titles:
        return
    block_tokens = set(_alpha_tokens(ref.raw))
    if len(block_tokens) < 4:
        return  # bare DOI / too little prose to judge — don't guess
    best_overlap = 0.0
    for title in cr_titles:
        title_tokens = set(_alpha_tokens(title))
        if not title_tokens:
            continue
        overlap = len(title_tokens & block_tokens) / len(title_tokens)
        best_overlap = max(best_overlap, overlap)
    if best_overlap < 0.5:
        result.add(
            WARNING,
            f"Cited text does not mention the Crossref title — possibly the wrong DOI:\n"
            f"    crossref: {cr_titles[0]}",
        )
