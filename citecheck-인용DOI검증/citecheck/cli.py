"""Command-line interface for citecheck."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
from collections import Counter
from typing import Optional

from . import __version__
from .core import (
    CODES,
    CheckResult,
    CrossrefClient,
    DiskCache,
    ERROR,
    OK,
    PubMedClient,
    WARNING,
    check_reference,
)
from .parsers import count_malformed_entries, detect_format, parse_references

DEFAULT_CACHE_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "citecheck",
    "lookups.json",
)

# Exit codes.
EXIT_OK = 0
EXIT_PROBLEM = 1  # a hard error (broken DOI / retraction), or --strict warning
EXIT_USAGE = 2  # bad input: unreadable file, no references
EXIT_INCONCLUSIVE = 3  # a lookup failed (e.g. offline) — could not verify

_COLORS = {OK: "\033[32m", WARNING: "\033[33m", ERROR: "\033[31m", "reset": "\033[0m"}
_SYMBOL = {OK: "✓", WARNING: "!", ERROR: "✗"}

# Strip C0/C1 control characters (incl. ESC) from any externally sourced text
# before printing, so a crafted BibTeX title or poisoned Crossref record cannot
# inject ANSI/terminal escape sequences into the terminal.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _sanitize(text: str) -> str:
    return _CONTROL_RE.sub("", text)


def _color(text: str, severity: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLORS[severity]}{text}{_COLORS['reset']}"


def _decode(raw: bytes, override: Optional[str] = None) -> tuple[str, str]:
    """Decode input bytes, tolerating common non-UTF-8 encodings.

    Returns (text, encoding_used). Never raises: an explicit *override* (and the
    latin-1 / ``errors='replace'`` fallbacks) always succeed. Byte-order marks
    are detected first so a UTF-16/UTF-8-BOM file is handled and the BOM stripped
    (rather than surfacing as a phantom ``\\ufeff`` reference).
    """
    if override:
        return raw.decode(override, errors="replace"), override
    if raw[:3] == b"\xef\xbb\xbf":  # UTF-8 BOM — strip it (else a phantom ﻿ ref)
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 LE/BE BOM
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


def _print_result(result: CheckResult, use_color: bool, verbose: bool) -> None:
    status = result.status
    if status == OK and not verbose:
        return
    header = f"{_SYMBOL[status]} {_sanitize(result.reference.label())}"
    print(_color(header, status, use_color))
    for f in result.findings:
        if f.severity == OK and not verbose:
            continue
        lines = f.message.splitlines() or [""]
        for i, line in enumerate(lines):
            indent = "    " if i == 0 else "      "
            print(f"{indent}{_sanitize(line)}")


def _to_json(results: list[CheckResult]) -> str:
    payload = []
    for r in results:
        payload.append(
            {
                # Sanitize the same C0/C1 control chars the text/csv/markdown
                # paths strip, so a poisoned title can't smuggle terminal escapes
                # through the JSON report either (ensure_ascii=False emits them raw).
                "label": _sanitize(r.reference.label()),
                "doi": _sanitize(r.reference.doi) if r.reference.doi else None,
                "pmid": r.reference.pmid,
                "journal": _sanitize(r.reference.journal) if r.reference.journal else None,
                "status": r.status,
                "findings": [
                    # `code` is the stable, machine-readable identity of the
                    # finding — CI should branch on it, never on the prose.
                    {
                        "severity": f.severity,
                        "code": f.code,
                        "message": _sanitize(f.message),
                    }
                    for f in r.findings
                ],
            }
        )
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _csv_safe(value: str) -> str:
    """Neutralise CSV-injection: a leading =/+/-/@ makes a spreadsheet treat the
    cell as a formula, so prefix such a cell with a single quote."""
    value = _sanitize(value)
    if value and value[0] in "=+-@\t\r":
        return "'" + value
    return value


def _to_csv(results: list[CheckResult]) -> str:
    """One row per reference — openable in Excel by a co-author."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["label", "doi", "pmid", "journal", "status", "codes", "findings"])
    for r in results:
        messages = " | ".join(f.message.replace("\n", " ").strip() for f in r.findings)
        # A codes column lets a co-author filter/pivot the sheet without reading
        # every prose message.
        codes = " ".join(dict.fromkeys(f.code for f in r.findings if f.code))
        writer.writerow(
            [
                _csv_safe(r.reference.label()),
                _csv_safe(r.reference.doi or ""),
                _csv_safe(r.reference.pmid or ""),
                _csv_safe(r.reference.journal or ""),
                r.status,
                _csv_safe(codes),
                _csv_safe(messages),
            ]
        )
    return buf.getvalue().rstrip("\r\n")


def _md_cell(text: str) -> str:
    return _sanitize(text).replace("|", "\\|").replace("\n", " ").strip()


def _to_markdown(results: list[CheckResult]) -> str:
    """A Markdown report for pasting into a PR / issue / lab notebook."""
    n_err = sum(r.status == ERROR for r in results)
    n_warn = sum(r.status == WARNING for r in results)
    n_ok = sum(r.status == OK for r in results)
    lines = [
        "# citecheck report",
        "",
        f"Checked **{len(results)}** references: "
        f"**{n_ok}** ok, **{n_warn}** warnings, **{n_err}** errors.",
        "",
        "| Status | Reference | DOI | Findings |",
        "| :----: | --------- | --- | -------- |",
    ]
    symbol = {OK: "✓", WARNING: "!", ERROR: "✗"}
    for r in results:
        findings = "<br>".join(_md_cell(f.message) for f in r.findings) or "—"
        lines.append(
            f"| {symbol[r.status]} | {_md_cell(r.reference.label())} "
            f"| {_md_cell(r.reference.doi or '—')} | {findings} |"
        )
    return "\n".join(lines)


def _flag_duplicate_dois(results: list[CheckResult]) -> None:
    """Warn on DOIs that appear under more than one reference in the manuscript."""
    counts = Counter(r.reference.doi for r in results if r.reference.doi)
    dups = {doi for doi, n in counts.items() if n > 1}
    for r in results:
        if r.reference.doi in dups:
            r.add(
                WARNING,
                f"Duplicate DOI — cited by {counts[r.reference.doi]} references: "
                f"{r.reference.doi}",
                "duplicate-doi",
            )


def _flag_duplicate_pmids(results: list[CheckResult]) -> None:
    """Warn on PMIDs cited more than once — but only where it isn't already
    caught as a duplicate DOI (same paper, two entries), to avoid double noise."""
    counts = Counter(r.reference.pmid for r in results if r.reference.pmid)
    dups = {pmid for pmid, n in counts.items() if n > 1}
    for r in results:
        pmid = r.reference.pmid
        if pmid in dups and not any("Duplicate DOI" in f.message for f in r.findings):
            r.add(
                WARNING, f"Duplicate PMID — cited by {counts[pmid]} references: {pmid}",
                "duplicate-pmid",
            )


# `lookup-failed` is deliberately NOT ignorable. It does not mean "a citation is
# wrong"; it means **we could not check**, and the tool's headline promise is
# that a network outage can never be mistaken for a clean pass:
#
#   $ citecheck refs.bib --ignore lookup-failed     # offline
#     checked 1 references: 1 ok, 0 warnings, 0 errors      <- a lie
#     (0 of 1 compared against a Crossref record)           <- contradicting it
#   exit 0
#
# It is an attractive nuisance precisely because it looks like noise to silence:
# a user who hides it once gets false clean passes forever after. Every other
# code is a judgement call the user is entitled to make; this one is a fact about
# whether the run happened.
NON_IGNORABLE = {"lookup-failed"}


def _parse_ignore(raw: str) -> tuple[set, list, list]:
    """Split an --ignore value into (usable codes, unknown tokens, refused codes).

    Unrecognised codes are returned rather than dropped so the caller can fail
    loudly: a typo'd `--ignore no-doi,retracton` must never quietly fail to
    suppress what the user asked for — nor quietly suppress a retraction.
    """
    wanted = [tok.strip().lower() for tok in raw.replace(" ", ",").split(",") if tok.strip()]
    known = {tok for tok in wanted if tok in CODES}
    unknown = [tok for tok in wanted if tok not in CODES]
    refused = sorted(known & NON_IGNORABLE)
    return known - NON_IGNORABLE, unknown, refused


def _apply_ignores(results: list[CheckResult], ignored: set) -> int:
    """Drop ignored findings in place. Returns how many were removed."""
    if not ignored:
        return 0
    removed = 0
    for r in results:
        keep = [f for f in r.findings if f.code not in ignored]
        removed += len(r.findings) - len(keep)
        r.findings = keep
    return removed


_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, OK: 2}


def _by_severity(results: list[CheckResult]) -> list[CheckResult]:
    """Errors first, then warnings, then verified — input order within each.

    On a 200-reference manuscript the one retraction is otherwise buried among
    60 "No DOI found" lines, with identical visual weight. Python's sort is
    stable, so the reference list's own order survives inside each group.
    """
    return sorted(results, key=lambda r: _SEVERITY_ORDER.get(r.status, 3))


def _print_checks() -> None:
    print("Finding codes (use with --ignore):\n")
    width = max(len(c) for c in CODES)
    for code, meaning in sorted(CODES.items()):
        note = "  [cannot be ignored]" if code in NON_IGNORABLE else ""
        print(f"  {code.ljust(width)}  {meaning}{note}")


def _network_calls(client, pubmed) -> int:
    """Total lookups the clients have sent to their transport so far.

    Used only to tell a cache hit from a real call, so the inter-request delay is
    charged only when we actually went out to Crossref/PubMed.
    """
    return getattr(client, "remote_calls", 0) + getattr(pubmed, "remote_calls", 0)


def _nonneg_float(value: str) -> float:
    f = float(value)
    if not math.isfinite(f) or f < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return f


# Ten years. A TTL beyond this is indistinguishable from "never expire", which
# defeats the whole point of expiry (catching a newly-retracted reference).
# Bounding it here also stops `--cache-ttl 1e308` from overflowing to `inf` once
# multiplied into seconds, which silently made every entry immortal.
MAX_CACHE_TTL_DAYS = 3650.0


def _cache_ttl_days(value: str) -> float:
    f = _nonneg_float(value)
    if f > MAX_CACHE_TTL_DAYS:
        raise argparse.ArgumentTypeError(
            f"must be <= {MAX_CACHE_TTL_DAYS:g} days — a longer cache could hide "
            f"a retraction indefinitely"
        )
    return f


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citecheck",
        description="Verify manuscript citations against Crossref: catch broken DOIs, "
        "metadata mismatches, retractions and expressions of concern, and preprints "
        "that now have a published version. Requires internet access to Crossref.",
    )
    p.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Input file (.bib / .ris / .json / .csv / text). '-' for stdin.",
    )
    p.add_argument(
        "--format",
        choices=["auto", "bibtex", "ris", "csljson", "csv", "text"],
        default="auto",
        help="Input format: bibtex, ris (EndNote/Zotero), csljson (Zotero/pandoc), "
        "csv (Excel/Sheets/Covidence reference table, also TSV), text, or "
        "auto-detect (default).",
    )
    p.add_argument(
        "--report",
        choices=["text", "json", "csv", "markdown"],
        default="text",
        help="Output report format (default: text). csv/markdown are shareable "
        "with co-authors.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Shorthand for --report json.",
    )
    p.add_argument(
        "--encoding",
        help="Force the input file encoding (e.g. latin-1, cp949). "
        "Default: auto-detect (UTF-8/BOM, then cp949, then latin-1).",
    )
    p.add_argument("--mailto", help="Your email — sent to Crossref/PubMed in the request header to "
                   "join the faster 'polite' API pool.")
    p.add_argument(
        "--pubmed",
        action="store_true",
        help="Also cross-check references that carry a PMID against PubMed: "
        "catch retractions Crossref misses and PMID↔DOI mismatches. "
        "Requires internet access to eutils.ncbi.nlm.nih.gov.",
    )
    p.add_argument(
        "--suggest-doi",
        action="store_true",
        help="For references with no DOI, search Crossref by title/author/year "
        "and report the DOI of a confident match, so you can add it.",
    )
    p.add_argument(
        "--cache",
        nargs="?",
        const=DEFAULT_CACHE_PATH,
        metavar="PATH",
        help=f"Cache Crossref/PubMed lookups on disk so re-running over the same "
        f"manuscript is instant. Bare --cache uses {DEFAULT_CACHE_PATH}.",
    )
    p.add_argument(
        "--cache-ttl",
        type=_cache_ttl_days,
        default=7.0,
        metavar="DAYS",
        help="Days a cached lookup stays valid (default: 7). Entries expire so a "
        "newly-retracted reference cannot hide behind a stale cache.",
    )
    p.add_argument(
        "--delay",
        type=_nonneg_float,
        default=0.2,
        help="Seconds to wait between Crossref calls (default: 0.2).",
    )
    p.add_argument(
        "--ignore",
        metavar="CHECK[,CHECK...]",
        default="",
        help="Suppress findings by code, e.g. --ignore no-doi,correction. "
        "Makes --strict usable as a submission gate: a real manuscript cites "
        "books and guidelines that have no DOI, which would otherwise fail it "
        "forever. Use --list-checks to see every code.",
    )
    p.add_argument(
        "--list-checks",
        action="store_true",
        help="Print every finding code (for --ignore) with its meaning, and exit.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on warnings too (not just errors).",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Also show verified references.")
    p.add_argument("--no-color", action="store_true", help="Disable coloured output.")
    p.add_argument("--version", action="version", version=f"citecheck {__version__}")
    return p


def run(
    argv: Optional[list[str]] = None,
    client: Optional[CrossrefClient] = None,
    pubmed: Optional[PubMedClient] = None,
) -> int:
    args = build_parser().parse_args(argv)

    if args.list_checks:
        _print_checks()
        return EXIT_OK

    ignored, unknown_ignores, refused_ignores = _parse_ignore(args.ignore)
    if unknown_ignores:
        print(
            f"citecheck: unknown --ignore code{'' if len(unknown_ignores) == 1 else 's'}: "
            f"{', '.join(unknown_ignores)}. Run --list-checks to see valid codes.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if refused_ignores:
        print(
            f"citecheck: {', '.join(refused_ignores)} cannot be ignored — it means "
            f"a lookup did not happen, not that a citation is wrong. Hiding it "
            f"would let an offline run report a clean pass.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.input == "-":
        raw = sys.stdin.buffer.read()
    else:
        try:
            with open(args.input, "rb") as fh:
                raw = fh.read()
        except OSError as e:
            print(f"citecheck: cannot read {args.input}: {e}", file=sys.stderr)
            return EXIT_USAGE

    try:
        text, encoding = _decode(raw, override=args.encoding)
    except LookupError:
        print(f"citecheck: unknown encoding: {args.encoding}", file=sys.stderr)
        return EXIT_USAGE
    if encoding not in ("utf-8", "utf-8-sig") and not args.encoding:
        print(
            f"citecheck: note: input was not UTF-8; decoded as {encoding}. "
            f"Use --encoding to override if author names look wrong.",
            file=sys.stderr,
        )

    refs = parse_references(text, fmt=args.format)
    if not refs:
        print("citecheck: no references found in input.", file=sys.stderr)
        return EXIT_USAGE

    # Report BibTeX entries that were present but could not be parsed.
    resolved_fmt = args.format
    if resolved_fmt == "auto":
        resolved_fmt = detect_format(text)
    if resolved_fmt == "bibtex":
        malformed = count_malformed_entries(text)
        if malformed:
            print(
                f"citecheck: warning: {malformed} malformed/unterminated BibTeX "
                f"entr{'y was' if malformed == 1 else 'ies were'} skipped; "
                f"references after it may have been missed.",
                file=sys.stderr,
            )

    # The cache is only built for clients we create — an injected client (tests,
    # or a library caller) brings its own caching policy and must not be
    # second-guessed here.
    cache = None
    if args.cache and (client is None or (args.pubmed and pubmed is None)):
        cache = DiskCache(args.cache, ttl_seconds=args.cache_ttl * 24 * 3600)

    if client is None:
        client = CrossrefClient(mailto=args.mailto, cache=cache)
    # A PubMed client is created only when --pubmed is set (or one is injected
    # for tests); otherwise the PMID cross-check is skipped entirely.
    if pubmed is None and args.pubmed:
        pubmed = PubMedClient(mailto=args.mailto, cache=cache)
    use_color = sys.stdout.isatty() and not args.no_color

    # --pubmed only acts on references that carry a PMID. On a file with none it
    # is a silent no-op, and the user — who asked for PubMed-grade retraction
    # coverage — gets byte-identical output and no hint that nothing happened.
    # Say so plainly rather than let the flag imply coverage it did not provide.
    if args.pubmed:
        with_pmid = sum(1 for r in refs if r.pmid)
        if not with_pmid:
            print(
                f"citecheck: warning: --pubmed had no effect — none of the "
                f"{len(refs)} references carry a PMID, so nothing was "
                f"cross-checked against PubMed.",
                file=sys.stderr,
            )
        elif with_pmid < len(refs):
            print(
                f"citecheck: note: --pubmed cross-checked {with_pmid} of "
                f"{len(refs)} references (the rest carry no PMID).",
                file=sys.stderr,
            )

    results: list[CheckResult] = []
    for i, ref in enumerate(refs):
        before = _network_calls(client, pubmed)
        results.append(
            check_reference(ref, client, pubmed=pubmed, suggest_missing=args.suggest_doi)
        )
        hit_network = _network_calls(client, pubmed) > before
        # Only pause when we actually called out — a cache hit costs Crossref
        # nothing, so rate-limiting it would just make a cached run slow for no
        # reason (the whole point of the cache).
        if args.delay and hit_network and i < len(refs) - 1:
            time.sleep(args.delay)

    if cache is not None and not cache.save():
        print(
            f"citecheck: note: could not write the cache at {args.cache} — "
            f"results are unaffected.",
            file=sys.stderr,
        )

    _flag_duplicate_dois(results)
    _flag_duplicate_pmids(results)
    # Applied after every check has run, so --ignore only silences the *report*
    # — it never skips a lookup, and so can never change what else was found.
    n_ignored = _apply_ignores(results, ignored)

    # Errors first: on a long reference list the one retraction must not be
    # buried among the routine warnings.
    results = _by_severity(results)

    report = "json" if args.json else args.report
    if report == "json":
        print(_to_json(results))
    elif report == "csv":
        print(_to_csv(results))
    elif report == "markdown":
        print(_to_markdown(results))
    else:
        for r in results:
            _print_result(r, use_color, args.verbose)
        _print_summary(results, use_color)
        if n_ignored:
            print(
                f"  ({n_ignored} finding{'' if n_ignored == 1 else 's'} hidden by "
                f"--ignore {','.join(sorted(ignored))})"
            )
        if cache is not None and cache.hits:
            print(
                f"  ({cache.hits} lookup{'' if cache.hits == 1 else 's'} served "
                f"from the cache at {args.cache}; entries expire after "
                f"{args.cache_ttl:g} days)"
            )

    has_error = any(r.status == ERROR for r in results)
    has_warning = any(r.status == WARNING for r in results)
    # A lookup failure (typically offline) means we could not actually verify —
    # never let that read as a clean pass in a CI gate.
    #
    # Keyed on the CODE, not on the message text. Matching `startswith("Lookup
    # failed")` made this headline safety property depend on the exact English
    # wording of four separate f-strings: rephrasing any one of them to "Could
    # not reach Crossref" would silently turn every offline run into exit 0.
    # That is the class of bug the codes exist to retire.
    lookup_failed = any(f.code == "lookup-failed" for r in results for f in r.findings)
    if has_error or (args.strict and has_warning):
        return EXIT_PROBLEM
    if lookup_failed:
        return EXIT_INCONCLUSIVE
    return EXIT_OK


def _print_summary(results: list[CheckResult], use_color: bool) -> None:
    n_err = sum(r.status == ERROR for r in results)
    n_warn = sum(r.status == WARNING for r in results)
    n_ok = sum(r.status == OK for r in results)
    # Two disjoint buckets that add up to len(results): either we retrieved a
    # Crossref record and compared against it, or we did not. The previous
    # wording ("N verified, M could not be verified") silently excluded
    # hard-error references from both, so the two numbers did not sum to the
    # total and the reader was left to wonder where the rest went.
    n_checked = sum(1 for r in results if r.crossref is not None)
    n_not_checked = len(results) - n_checked
    print()
    summary = f"checked {len(results)} references: {n_ok} ok, {n_warn} warnings, {n_err} errors"
    sev = ERROR if n_err else (WARNING if n_warn else OK)
    print(_color(summary, sev, use_color))
    print(
        f"  ({n_checked} of {len(results)} compared against a Crossref record; "
        f"{n_not_checked} could not be — no DOI, broken DOI, or not in Crossref)"
    )


def main() -> None:  # console-script entry point
    sys.exit(run())


if __name__ == "__main__":
    main()
