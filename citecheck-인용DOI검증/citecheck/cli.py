"""Command-line interface for citecheck."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
from collections import Counter
from typing import Optional

from . import __version__
from .core import CheckResult, CrossrefClient, ERROR, OK, PubMedClient, WARNING, check_reference
from .parsers import count_malformed_entries, detect_format, parse_references

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
                    {"severity": f.severity, "message": _sanitize(f.message)}
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
    writer.writerow(["label", "doi", "pmid", "journal", "status", "findings"])
    for r in results:
        messages = " | ".join(f.message.replace("\n", " ").strip() for f in r.findings)
        writer.writerow(
            [
                _csv_safe(r.reference.label()),
                _csv_safe(r.reference.doi or ""),
                _csv_safe(r.reference.pmid or ""),
                _csv_safe(r.reference.journal or ""),
                r.status,
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
            r.add(WARNING, f"Duplicate DOI — cited by {counts[r.reference.doi]} references: {r.reference.doi}")


def _flag_duplicate_pmids(results: list[CheckResult]) -> None:
    """Warn on PMIDs cited more than once — but only where it isn't already
    caught as a duplicate DOI (same paper, two entries), to avoid double noise."""
    counts = Counter(r.reference.pmid for r in results if r.reference.pmid)
    dups = {pmid for pmid, n in counts.items() if n > 1}
    for r in results:
        pmid = r.reference.pmid
        if pmid in dups and not any("Duplicate DOI" in f.message for f in r.findings):
            r.add(WARNING, f"Duplicate PMID — cited by {counts[pmid]} references: {pmid}")


def _nonneg_float(value: str) -> float:
    f = float(value)
    if not math.isfinite(f) or f < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return f


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citecheck",
        description="Verify manuscript citations against Crossref: catch broken DOIs, "
        "metadata mismatches, and retractions. Requires internet access to Crossref.",
    )
    p.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Input file (.bib / .ris / .json / text). '-' for stdin.",
    )
    p.add_argument(
        "--format",
        choices=["auto", "bibtex", "ris", "csljson", "text"],
        default="auto",
        help="Input format: bibtex, ris (EndNote/Zotero), csljson (Zotero/pandoc), "
        "text, or auto-detect (default).",
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
        "--delay",
        type=_nonneg_float,
        default=0.2,
        help="Seconds to wait between Crossref calls (default: 0.2).",
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

    if client is None:
        client = CrossrefClient(mailto=args.mailto)
    # A PubMed client is created only when --pubmed is set (or one is injected
    # for tests); otherwise the PMID cross-check is skipped entirely.
    if pubmed is None and args.pubmed:
        pubmed = PubMedClient(mailto=args.mailto)
    use_color = sys.stdout.isatty() and not args.no_color

    results: list[CheckResult] = []
    for i, ref in enumerate(refs):
        results.append(check_reference(ref, client, pubmed=pubmed))
        if args.delay and i < len(refs) - 1:
            time.sleep(args.delay)

    _flag_duplicate_dois(results)
    _flag_duplicate_pmids(results)

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

    has_error = any(r.status == ERROR for r in results)
    has_warning = any(r.status == WARNING for r in results)
    # A lookup failure (typically offline) means we could not actually verify —
    # never let that read as a clean pass in a CI gate.
    lookup_failed = any(
        f.message.startswith("Lookup failed")
        for r in results
        for f in r.findings
    )
    if has_error or (args.strict and has_warning):
        return EXIT_PROBLEM
    if lookup_failed:
        return EXIT_INCONCLUSIVE
    return EXIT_OK


def _print_summary(results: list[CheckResult], use_color: bool) -> None:
    n_err = sum(r.status == ERROR for r in results)
    n_warn = sum(r.status == WARNING for r in results)
    n_ok = sum(r.status == OK for r in results)
    # "Verified" = we retrieved and compared a Crossref record (even if it then
    # raised a warning). "Unverifiable" = no record to compare against and not a
    # hard error (no DOI, lookup failed, or resolves-but-not-in-Crossref).
    n_verified = sum(1 for r in results if r.crossref is not None)
    n_unverifiable = sum(1 for r in results if r.crossref is None and r.status != ERROR)
    print()
    summary = f"checked {len(results)} references: {n_ok} ok, {n_warn} warnings, {n_err} errors"
    sev = ERROR if n_err else (WARNING if n_warn else OK)
    print(_color(summary, sev, use_color))
    print(f"  ({n_verified} verified against Crossref, {n_unverifiable} could not be verified)")


def main() -> None:  # console-script entry point
    sys.exit(run())


if __name__ == "__main__":
    main()
