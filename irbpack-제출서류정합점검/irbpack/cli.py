"""CLI — 폴더 하나를 받아 서류들 사이의 값을 대조하고 종료코드로 답한다.

종료코드
  0  치명 0건
  1  치명 발견
  2  프로토콜 없음/못 읽음 · 역할 판별 실패 · 원고 감지 · 문서 1개 · 출력 경로 문제
  3  부속문서 중 못 읽은 것이 있음(.hwp 등) 또는 대조 성립 항목 4개 미만

**3이 1보다 우선한다.** 서류를 다 못 읽었으면 "치명 3건"이 아니라 "판정 불가"다.
"""

import argparse
import os
import sys

from . import __version__
from .baseline import compare_baseline
from .compare import compare
from .docread import disambiguate, read_document, scan
from .items import extract_all
from .report import Report
from .roles import (PROTOCOL, RoleError, assign_roles, detect_manuscript, parse_role_option)
from .safeio import OutputError, ReportIntegrityError, prepare_out_dir, safe_name

MIN_COMPARED_ITEMS = 4
DEFAULT_OUT_DIR = "정합점검결과"

EXIT_OK = 0
EXIT_CRITICAL = 1
EXIT_INPUT = 2
EXIT_UNDECIDABLE = 3


def build_parser():
    parser = argparse.ArgumentParser(
        prog="irbpack",
        description="IRB·식약처에 한 봉투로 같이 내는 서류들(연구계획서·동의서·CRF·모집공고 등) "
                    "사이에서 말이 다른 곳만 찾아 줍니다. 문서 하나의 품질은 보지 않습니다.",
        epilog="예) irbpack 제출패킷/ --out-dir 점검_0910   ·   "
               "irbpack 새패킷/ --baseline 이전패킷/ --out-dir 개정점검",
    )
    parser.add_argument("inputs", nargs="*", metavar="패킷폴더|파일",
                        help="점검할 패킷 폴더(또는 문서 파일들)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, metavar="폴더",
                        help="리포트를 저장할 폴더 (기본: %s)" % DEFAULT_OUT_DIR)
    parser.add_argument("--role", action="append", default=[], metavar="역할=파일패턴",
                        help="문서 역할을 직접 지정 (예: --role 프로토콜=연구계획서_v1.2.docx). "
                             "점검에서 빼려면 --role 제외=파일명")
    parser.add_argument("--baseline", metavar="이전패킷폴더",
                        help="이전 판 패킷과 비교해 '프로토콜만 바뀌고 부속문서가 안 따라온 곳'을 찾습니다")
    parser.add_argument("--all-rows", action="store_true",
                        help="소견에서 기준과 값이 같은 문서 줄까지 전부 펼쳐 보여 줍니다")
    parser.add_argument("--quiet", action="store_true", help="콘솔 리포트를 출력하지 않습니다")
    parser.add_argument("--version", action="version", version="irbpack %s" % __version__)
    return parser


def _configure_stdout():
    """콘솔 인코딩 문제로 종료코드가 1로 둔갑하지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):        # 파이프·테스트 더블 등
            pass


def _error(message):
    sys.stderr.write("%s\n" % message)


def _load(paths):
    documents = [read_document(path) for path in paths]
    return disambiguate(documents)


def _manuscript_stop(documents):
    flagged = detect_manuscript(documents)
    if not flagged:
        return None
    lines = ["이건 논문 원고입니다 — irbpack 은 원고를 보지 않습니다."]
    for doc, hits in flagged:
        lines.append("  · %s (원고 표제어: %s)" % (safe_name(doc.name), ", ".join(hits)))
    lines.append("")
    lines.append("원고는 다른 툴을 쓰세요:")
    lines.append("  draftcheck 원고투고점검 · numcheck 원고수치검증 · revcheck 리비전응답점검")
    return "\n".join(lines)


def _run(args):
    missing = [item for item in args.inputs if not os.path.exists(os.path.expanduser(item))]
    if missing:
        _error("입력 경로를 찾지 못했습니다: %s" % ", ".join(safe_name(item) for item in missing))
        return EXIT_INPUT
    if args.out_dir is not None and not str(args.out_dir).strip():
        _error("--out-dir 가 비어 있습니다. 리포트를 저장할 폴더 이름을 적어 주세요.")
        return EXIT_INPUT

    paths, scan_notes = scan(args.inputs)
    if not paths:
        _error("점검할 문서를 찾지 못했습니다. 폴더 안에 .docx/.pdf/.md/.txt 가 있는지 확인해 주세요.")
        return EXIT_INPUT

    documents = _load(paths)

    stop = _manuscript_stop(documents)
    if stop:
        _error(stop)
        return EXIT_INPUT

    if len(documents) < 2:
        _error("문서가 1개뿐입니다 — irbpack 은 서류 '사이'를 보는 툴이라 혼자서는 할 말이 없습니다.\n"
               "  한 문서 안의 정합성은 draftcheck·numcheck 를 쓰세요.")
        return EXIT_INPUT

    try:
        role_options = parse_role_option(args.role)
        documents, excluded = assign_roles(documents, args.role)
    except RoleError as exc:
        _error(str(exc))
        return EXIT_INPUT

    if len(documents) < 2:
        _error("--role 제외 후 남은 문서가 2개 미만입니다 — 대조할 상대가 없습니다.")
        return EXIT_INPUT

    protocols = [doc for doc in documents if doc.role == PROTOCOL]
    if not protocols:
        unread = [doc for doc in documents if not doc.read_ok]
        message = ["프로토콜(연구계획서)을 찾지 못했습니다 — 기준 문서가 없으면 대조가 성립하지 않습니다."]
        if unread:
            message.append("  읽지 못한 문서: %s" % ", ".join(safe_name(doc.name) for doc in unread))
        message.append("  --role 프로토콜=<연구계획서 파일명> 으로 직접 지정해 주세요.")
        _error("\n".join(message))
        return EXIT_INPUT
    if len(protocols) > 1:
        _error("프로토콜로 판별된 문서가 %d개입니다: %s\n"
               "  하나만 기준이 될 수 있습니다 — --role 로 지정하거나 --role 제외=<파일명> 로 빼 주세요."
               % (len(protocols), ", ".join(safe_name(doc.name) for doc in protocols)))
        return EXIT_INPUT

    mentions = []
    for doc in documents:
        mentions.extend(extract_all(doc))
    result = compare(documents, mentions)

    baseline_findings = []
    baseline_notes = []
    baseline_label = ""
    if args.baseline:
        baseline_paths, _ = scan([args.baseline])
        if not baseline_paths:
            baseline_notes.append("--baseline 폴더에서 문서를 찾지 못해 개정 축 점검을 건너뛰었습니다")
        else:
            old_documents = _load(baseline_paths)
            try:
                old_documents, _ = assign_roles(old_documents, args.role)
            except RoleError:
                old_documents = [doc for doc in old_documents if doc.role]
            old_mentions = []
            for doc in old_documents:
                old_mentions.extend(extract_all(doc))
            baseline_findings, baseline_notes = compare_baseline(
                documents, mentions, old_documents, old_mentions)
            baseline_label = args.baseline

    report = Report(
        input_label=args.inputs[0] if len(args.inputs) == 1 else "문서 %d개" % len(paths),
        documents=documents,
        excluded=excluded,
        result=result,
        baseline_findings=baseline_findings,
        baseline_label=baseline_label,
        role_option_count=len(role_options),
        all_rows=args.all_rows,
    )
    report.set_mentions(mentions)
    report.baseline_notes = baseline_notes
    report.scan_notes = scan_notes

    counts = report.counts()
    unreadable_docs = [doc for doc in documents if not doc.read_ok]
    if unreadable_docs:
        exit_code = EXIT_UNDECIDABLE
    elif report.compared_count() < MIN_COMPARED_ITEMS:
        exit_code = EXIT_UNDECIDABLE
    elif counts["치명"]:
        exit_code = EXIT_CRITICAL
    else:
        exit_code = EXIT_OK

    try:
        out_dir = prepare_out_dir(args.out_dir)
        written = report.write_all(out_dir, exit_code)
    except OutputError as exc:
        _error("출력 오류: %s" % exc)
        return EXIT_INPUT
    except ReportIntegrityError as exc:
        _error("리포트 무결성 오류: %s" % exc)
        return EXIT_INPUT

    if not args.quiet:
        lines = [report.console(), "", "저장: %s" % out_dir]
        lines.extend("  · %s" % os.path.basename(path) for path in written)
        lines.append("")
        lines.append(_verdict_line(counts, exit_code, unreadable_docs, report))
        _print_safely("\n".join(lines))
    return exit_code


def _print_safely(text):
    """`irbpack 패킷/ | head` 처럼 읽는 쪽이 먼저 끝나도 종료코드가 뒤집히지 않게 한다.

    파이프가 끊기면 파이썬은 종료 시 stdout flush 에서 다시 터져 exit 120 이 되므로,
    stdout 을 /dev/null 로 바꿔 놓고 조용히 빠져나온다.
    """
    try:
        print(text)
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:                                # pragma: no cover - 방어
            pass


def _verdict_line(counts, exit_code, unreadable_docs, report):
    if exit_code == EXIT_UNDECIDABLE:
        if unreadable_docs:
            return ("판정 불가 — 읽지 못한 문서 %d개가 있어 '치명 %d건'이라고 말할 수 없습니다. → exit 3"
                    % (len(unreadable_docs), counts["치명"]))
        return ("판정 불가 — 대조가 성립한 항목이 %d개뿐입니다(최소 %d개). → exit 3"
                % (report.compared_count(), MIN_COMPARED_ITEMS))
    if exit_code == EXIT_CRITICAL:
        return "치명 %d건. → exit 1" % counts["치명"]
    return "치명 0건 (경고 %d건). → exit 0" % counts["경고"]


def main(argv=None):
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.inputs:
        parser.print_help()
        return EXIT_INPUT
    try:
        return _run(args)
    except KeyboardInterrupt:                      # pragma: no cover
        _error("중단했습니다.")
        return EXIT_INPUT


if __name__ == "__main__":                          # pragma: no cover
    sys.exit(main())
