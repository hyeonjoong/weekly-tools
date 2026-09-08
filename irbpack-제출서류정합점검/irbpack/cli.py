"""명령줄 — `irbpack 패킷폴더/ [--baseline 이전패킷/] [--out-dir 폴더] [--role 역할=파일]`

종료코드
  0  치명 0건
  1  치명 발견
  2  프로토콜 없음/못 읽음 · 역할 판별 실패 · 원고 감지 · 문서 1개 · 인자 오류
  3  부속문서 중 못 읽은 것이 있음(.hwp 등) 또는 대조 성립 항목 < 4   ※ 3 이 1 보다 우선
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from typing import List, Optional, Sequence, Tuple

from . import __version__, compare, extract, guards, mask, readers, report, roles, safeio
from .model import Coverage, Doc, ROLE_PROTOCOL, Result

MIN_COMPARED = 4
_SKIP_PREFIX = ("~$", ".")


def _collect(paths: Sequence[str]) -> Tuple[List[str], List[str]]:
    """파일·폴더 인자 → 문서 파일 목록 (폴더는 1단계만, 정렬). 반환: (파일들, 오류들)."""
    files: List[str] = []
    errors: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            names = sorted(os.listdir(p))
            for n in names:
                if n.startswith(_SKIP_PREFIX):
                    continue
                full = os.path.join(p, n)
                ext = os.path.splitext(n)[1].lower()
                if os.path.islink(full):
                    continue  # 링크가 실물을 가리는 사고 방지 — 폴더 안의 링크는 문서로 세지 않습니다
                if os.path.isfile(full) and (ext in readers.READABLE_EXT or ext in readers.UNREADABLE_EXT or ext in readers.MANUSCRIPT_EXT):
                    files.append(full)
        elif os.path.exists(p):
            files.append(p)
        else:
            errors.append("경로가 없습니다: {}".format(p))
    seen = set()
    uniq: List[str] = []
    for f in sorted(files, key=lambda x: (os.path.islink(x), x)):  # 실물이 링크보다 먼저
        key = os.path.realpath(f)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    uniq.sort()
    return uniq, errors


def _stdout_safe() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


def _clean(text: object) -> str:
    """사용자 인자·파일명 등 바깥에서 온 문자열을 화면에 낼 때 — 마스킹 + 개행 새니타이즈."""
    return safeio.sanitize_line(mask.mask(str(text)))


def _print(text: str) -> None:
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(text.encode(enc, errors="replace").decode(enc, errors="replace"))
    sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="irbpack", description="IRB·식약처 제출 서류 묶음의 문서 사이 정합성 점검 (문서 하나의 품질은 보지 않습니다)")
    p.add_argument("paths", nargs="*", help="패킷 폴더 또는 문서 파일들 (.docx .md .txt .pdf)")
    p.add_argument("--role", action="append", default=[], metavar="역할=파일패턴",
                   help="역할 수동 지정 (프로토콜/동의서/동의서(소아)/CRF/모집공고/등록정보). 예: --role 프로토콜=계획서_v1.2.docx")
    p.add_argument("--baseline", metavar="이전패킷", help="개정 전 패킷 폴더 — 프로토콜에서 바뀐 값이 부속문서에도 반영됐는지 봅니다")
    p.add_argument("--out-dir", metavar="폴더", help="정합점검.md · 불일치목록.csv · 항목추출표.csv · 대조불가.csv 를 쓸 폴더 (없으면 화면만)")
    p.add_argument("--version", action="version", version="irbpack {}".format(__version__))
    return p


def _load_packet(files: Sequence[str], forced: List[Tuple[str, str]]) -> Tuple[List[Doc], List[str]]:
    docs = [readers.load(f) for f in files]
    # 같은 파일명이 두 폴더에서 오면 추출값이 한 문서로 뭉쳐 충돌이 사라진다 — 이름을 상위 폴더로 구분
    counts: dict = {}
    for d in docs:
        counts[d.name] = counts.get(d.name, 0) + 1
    for d in docs:
        if counts[d.name] > 1:
            parent = readers.sanitize_name(os.path.basename(os.path.dirname(os.path.abspath(d.path))))
            d.name = "{}/{}".format(parent, d.name)
    errors = roles.assign(docs, forced)
    return docs, errors


def run_packet(files: Sequence[str], forced: List[Tuple[str, str]], baseline_files: Optional[Sequence[str]] = None) -> Tuple[Result, List[str]]:
    """핵심 파이프라인. 반환: (Result, 치명적 오류 메시지 목록 — 비어 있지 않으면 exit 2)."""
    fatal: List[str] = []
    docs, role_errors = _load_packet(files, forced)
    cov = Coverage(n_docs=len(docs))
    cov.n_read = sum(1 for d in docs if d.readable)
    cov.unread = [(d.name, d.unread_reason) for d in docs if not d.readable]
    cov.auto_roles = sum(1 for d in docs if d.role and not d.role_forced)
    cov.forced_roles = sum(1 for d in docs if d.role_forced)
    res = Result(docs=docs, extractions=[], issues=[], coverage=cov)
    if len(docs) < 2:
        fatal.append("문서가 {}개입니다 — irbpack 은 문서 *사이*를 봅니다. 혼자서는 할 말이 없습니다 (문서 하나의 점검은 draftcheck·numcheck)".format(len(docs)))
        return res, fatal
    for d in docs:
        why = guards.manuscript_reason(d)
        if why:
            fatal.append(guards.MANUSCRIPT_MESSAGE.format(name=d.name, why=why))
    if fatal:
        return res, fatal
    for err in role_errors:
        fatal.append("문서 역할 판별 실패 — " + err)
    protos = [d for d in docs if d.role == ROLE_PROTOCOL]
    if not protos:
        fatal.append("프로토콜(연구계획서)을 찾지 못했습니다 — --role 프로토콜=파일명 으로 지정하세요")
    elif not any(d.readable for d in protos):
        fatal.append("프로토콜을 읽지 못했습니다: " + "; ".join("{} ({})".format(d.name, d.unread_reason) for d in protos))
    if fatal:
        return res, fatal
    for d in docs:
        res.extractions.extend(extract.extract_all(d))
    res.issues = compare.compare(res.extractions, docs, cov)
    if baseline_files is not None:
        bdocs, berr = _load_packet(baseline_files, forced)
        if berr or not any(d.role == ROLE_PROTOCOL and d.readable for d in bdocs):
            cov.baseline_note = "기준 패킷 역할 판별 실패 또는 프로토콜 없음 — 개정 축을 보지 않음 ({})".format("; ".join(berr)[:200] if berr else "프로토콜 없음")
        else:
            bext: List = []
            for d in bdocs:
                bext.extend(extract.extract_all(d))
            res.issues.extend(compare.compare_baseline(res.extractions, docs, bext, bdocs, cov))
            res.issues = compare.fold_baseline(res.issues)
    n_crit = sum(1 for i in res.issues if i.severity == "치명")
    if cov.unread:
        res.exit_code, res.exit_reason = 3, "읽지 못한 문서 {}개 — 다 읽지 못했으면 '치명 {}건'은 판정이 아닙니다".format(len(cov.unread), n_crit)
    elif len(cov.items_compared) < MIN_COMPARED:
        res.exit_code, res.exit_reason = 3, "대조 성립 항목 {}개 < {} — 판정 불가".format(len(cov.items_compared), MIN_COMPARED)
    elif n_crit:
        res.exit_code = 1
    else:
        res.exit_code = 0
    return res, fatal


def main(argv: Optional[Sequence[str]] = None) -> int:
    _stdout_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.paths:
        parser.print_help()
        return 2
    try:
        forced = roles.parse_role_args(args.role)
    except ValueError as exc:
        _print("[중단] {}\n".format(exc))
        return 2
    files, errors = _collect(args.paths)
    if errors:
        _print("[중단] " + "\n       ".join(_clean(e) for e in errors) + "\n")
        return 2
    if not files:
        _print("[중단] 읽을 문서가 없습니다 (.docx .md .txt .pdf)\n")
        return 2
    baseline_files: Optional[List[str]] = None
    if args.baseline:
        baseline_files, berr = _collect([args.baseline])
        if berr or not baseline_files:
            _print("[중단] --baseline: " + _clean("; ".join(berr) if berr else "문서가 없습니다") + "\n")
            return 2
    safeio.clear_protected()
    safeio.protect_inputs(list(files) + list(baseline_files or []))
    out_dir = None
    if args.out_dir:
        try:
            out_dir = safeio.prepare_out_dir(args.out_dir)
        except (safeio.OutputError, OSError) as exc:
            _print("[중단] {}\n".format(_clean(exc)))
            return 2
    try:
        res, fatal = run_packet(files, forced, baseline_files)
    except Exception as exc:  # noqa: BLE001 — 내부 오류가 '치명 발견'(exit 1) 로 둔갑하지 않게
        _print("[중단] 내부 오류로 판정하지 않음 → exit 2\n  {}: {}\n".format(exc.__class__.__name__, safeio.sanitize_line(str(exc))[:300]))
        return 2
    label = ", ".join(safeio.relpath_for_display(p) for p in args.paths)
    if fatal:
        _print("irbpack {} — 제출서류 정합점검\n입력: {}  (문서 {}개)\n\n".format(__version__, _clean(label), len(files)))
        for d in res.docs:
            _print("  {}  {}\n".format(_clean(d.label or d.role or "역할 미판별").ljust(12), _clean(d.name)))
        _print("\n[중단] 판정하지 않음 → exit 2\n")
        for f in fatal:
            _print("  " + _clean(f).replace("␤", "\n  ") + "\n")
        return 2
    try:
        console = report.render_console(res, label)
        written: List[str] = []
        if out_dir:
            md_text = report.render_md(res, label, console)
            written = report.write_all(res, out_dir, md_text)   # 화면에 'exit 0' 을 찍기 전에 먼저 쓴다
    except report.ReportIntegrityError as exc:
        _print("[중단] 리포트 무결성 오류 — 커버리지 자백 없이는 리포트를 내지 않습니다: {}\n".format(_clean(exc)))
        return 2
    except (safeio.OutputError, OSError) as exc:
        _print("[중단] 출력 실패 — 리포트를 쓰지 못해 판정을 내지 않습니다: {}\n".format(_clean(exc)))
        return 2
    _print(console)
    if out_dir:
        _print("\n출력: {}\n".format(_clean(out_dir)))
        for w in written:
            _print("  · {}\n".format(_clean(os.path.basename(w))))
    return res.exit_code


def run() -> None:
    sys.exit(main())
