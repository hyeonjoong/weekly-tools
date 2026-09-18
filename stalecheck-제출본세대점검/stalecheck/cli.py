"""명령줄 진입점 — `python3 -m stalecheck`.

종료코드: 0 치명없음 · 1 치명있음 · 2 판정거절 · 3 판정불가(3이 1보다 우선).
"""

import argparse
import math
import os
import sys

from . import engine, report, scanning, verdicts, writer
from .errors import (EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE,
                     StalecheckError, UndecidableError)

DESCRIPTION = """\
제출 봉투 안의 파일이 작업 폴더 원본과 같은 세대인지 내용 해시로 대조합니다.
고치지 않고, 복사하지 않고, 원고 내용도 저널 규정도 보지 않습니다 —
봉투가 며칠 전 세계를 담고 있는지만 말하고 끝납니다."""

EPILOG = """\
예)
  python3 -m stalecheck --work "논문폴더" --inspect
  python3 -m stalecheck --work "논문폴더" --package "논문폴더/submission" \\
      --out-dir ~/Desktop/세대점검

종료코드: 0 치명 0건 · 1 봉투가 구세대인 쌍 있음 · 2 판정 없이 거절 · 3 판정 불가"""


class _SafeStream(object):
    """출력이 막혀도 판정을 망치지 않는 래퍼.

    fd 가 닫혀 있으면 `sys.stdout` 은 `None` 이고(그대로 쓰면 AttributeError),
    `PYTHONIOENCODING=ascii` 환경에서는 한국어 한 줄이 `UnicodeEncodeError` 로
    터진다. 둘 다 **종료코드를 1로 바꿔** 깨끗한 트리를 "낡았다"로 만든다.
    이 툴의 답은 종료코드에 있으므로, 출력이 실패해도 답은 살아남아야 한다.
    """

    def __init__(self, stream):
        self._stream = stream
        self.failed = False

    def write(self, text):
        if self._stream is None:
            self.failed = True
            return
        try:
            self._stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, "encoding", None) or "ascii"
            try:
                self._stream.write(
                    text.encode(encoding, "backslashreplace").decode(encoding))
            except (OSError, ValueError, UnicodeError):
                self.failed = True
        except (OSError, ValueError):
            self.failed = True

    def flush(self):
        try:
            if self._stream is not None:
                self._stream.flush()
        except (OSError, ValueError):
            self.failed = True


def build_parser():
    """argparse 파서. 테스트가 epilog 의 종료코드 설명을 확인한다."""
    parser = argparse.ArgumentParser(
        prog="stalecheck", description=DESCRIPTION, epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--work", required=True,
                        help="작업 폴더(원본이 있는 논문 폴더)")
    parser.add_argument("--package", action="append", default=[],
                        help="봉투 폴더(작업 폴더 안쪽). 여러 번 줄 수 있습니다.")
    parser.add_argument("--out-dir", default=None,
                        help="리포트/CSV를 쓸 폴더. 없으면 콘솔에만 출력합니다.")
    parser.add_argument("--inspect", action="store_true",
                        help="판정 전에 무엇을 읽었는지만 인쇄하고 끝냅니다.")
    parser.add_argument("--ext", action="append", default=None,
                        help="스캔 확장자(기본: %s)" % " ".join(scanning.DEFAULT_EXTS))
    parser.add_argument("--max-bytes", type=int, default=scanning.DEFAULT_MAX_BYTES,
                        help="이보다 큰 파일은 건너뛰고 자백합니다 (기본 60MB)")
    parser.add_argument("--mtime-tolerance", type=float,
                        default=verdicts.MTIME_TOLERANCE_SEC,
                        help="이 초 안쪽의 mtime 차이는 방향 근거로 쓰지 않습니다 (기본 2초)")
    parser.add_argument("--min-coverage", type=float, default=0.0,
                        help="봉투 파일 중 짝지어진 비율이 이보다 낮으면 exit 3 "
                             "(기본 0 = 사용 안 함. 봉투에만 있는 파일은 정상이므로 "
                             "기본값을 올리지 않았습니다)")
    parser.add_argument("--no-siblings", action="store_true",
                        help="md↔docx · png↔pdf 형제 점검을 끕니다")
    parser.add_argument("--include-archives", action="store_true",
                        help="_이전/·_superseded/ 같은 아카이브 폴더도 작업본으로 셉니다")
    parser.add_argument("--quiet", action="store_true",
                        help="콘솔 리포트를 생략합니다(종료코드와 산출물만 씁니다)")
    return parser


def _print_candidates(work_root, exclude_pattern, stream):
    candidates = scanning.find_package_candidates(work_root, exclude_pattern)
    stream.write("[봉투 후보] %s\n" % work_root)
    if not candidates:
        stream.write("  후보를 찾지 못했습니다. --package 로 직접 지정하세요.\n")
        return candidates
    for rel, why in candidates:
        stream.write("  %s      (%s)\n" % (rel, why))
    stream.write("\n  이 중 무엇이 봉투인지 추론하지 않습니다. --package 로 지정하세요:\n")
    stream.write('    python3 -m stalecheck --work "%s" --package "%s"\n'
                 % (work_root, os.path.join(work_root, candidates[0][0])))
    return candidates


def _redacted_command(argv):
    """리포트에 실을 실행 줄 — **경로는 마지막 한 칸만** 남긴다.

    `세대점검.md` 는 공저자에게 그대로 보내는 파일이다. `--work
    /Users/<이름>/Downloads/...` 를 그대로 실으면 홈 디렉터리와 계정 이름이 샌다.
    """
    parts = ["python3 -m stalecheck"]
    for arg in argv:
        if os.sep in arg or arg.startswith("~"):
            arg = ".../" + os.path.basename(os.path.normpath(arg))
        if " " in arg:
            arg = '"%s"' % arg
        parts.append(arg)
    return " ".join(parts)


def _ext_summary(counter_items):
    return " · ".join("%s %d" % (e.lstrip("."), n) for e, n in counter_items) or "(없음)"


def _inspect_without_package(work_root, exts, max_bytes, exclude, stream):
    """--package 없이 --inspect — 작업 폴더만 읽어 인쇄하고 봉투 후보를 보여 준다."""
    from collections import Counter
    scan = scanning.scan_tree(work_root, exts=exts, max_bytes=max_bytes,
                              exclude_pattern=exclude, package_mode=False)
    stream.write("[읽은 것] %s\n" % work_root)
    stream.write("  작업 폴더 파일 %d개  %s\n"
                 % (scan.read_count,
                    _ext_summary(sorted(Counter(r.ext for r in scan.files).items()))))
    if scan.skipped_ext:
        stream.write("  확장자가 대상 밖이라 보지 않은 파일 %d개  %s\n"
                     % (sum(scan.skipped_ext.values()),
                        _ext_summary(scan.skipped_ext.most_common(6))))
    if scan.excluded_dirs:
        stream.write("  제외한 폴더(아카이브/구버전): %s\n"
                     % ", ".join(scan.excluded_dirs[:10]))
    if scan.package_dirs:
        stream.write("  봉투로 보여 작업본에서 뺀 폴더: %s\n"
                     % ", ".join(scan.package_dirs[:10]))
    if scan.skipped_large:
        stream.write("  크기초과로 건너뜀: %d개\n" % len(scan.skipped_large))
    if scan.unreadable:
        stream.write("  못 읽음: %d개\n" % len(scan.unreadable))
    stream.write("\n")
    _print_candidates(work_root, exclude, stream)
    stream.write("\n  (판정하지 않았습니다 — --package 를 주면 짝지음까지 인쇄합니다)\n")


def _inspect_report(analysis, stream):
    from collections import Counter
    stream.write("[읽은 것] %s\n" % analysis.work_root)
    counts = Counter(rec.ext for rec in analysis.work_scan.files)
    stream.write("  작업 폴더 파일 %d개  %s\n"
                 % (analysis.work_scan.read_count,
                    _ext_summary(sorted(counts.items()))))
    for pkg in analysis.packages:
        pcounts = Counter(rec.ext for rec in pkg.scan.files)
        stream.write("  봉투 %s 파일 %d개  %s\n"
                     % (pkg.label, pkg.scan.read_count,
                        _ext_summary(sorted(pcounts.items()))))
        stream.write("    짝지음 %d쌍 · 봉투에만 %d개 · 이름중복 %d개\n"
                     % (len(pkg.pairing.pairs), len(pkg.pairing.pkg_only),
                        len(pkg.pairing.ambiguous)))
    work_ext, pkg_ext = analysis.skipped_ext
    if work_ext or pkg_ext:
        stream.write("  확장자가 대상 밖이라 보지 않은 파일 %d개  %s\n"
                     % (sum(work_ext.values()) + sum(pkg_ext.values()),
                        _ext_summary((work_ext + pkg_ext).most_common(6))))
    if analysis.excluded_dirs:
        stream.write("  제외한 폴더: %s\n" % ", ".join(analysis.excluded_dirs[:10]))
    if analysis.work_scan.package_dirs:
        stream.write("  작업본에서 뺀 다른 봉투: %s\n"
                     % ", ".join(analysis.work_scan.package_dirs[:10]))
    if analysis.skipped_large:
        stream.write("  크기초과로 건너뜀: %d개\n" % len(analysis.skipped_large))
    if analysis.unreadable:
        stream.write("  못 읽음: %d개\n" % len(analysis.unreadable))
    stream.write("  (판정하지 않았습니다 — --inspect 를 빼면 판정합니다)\n")


ARTIFACT_NAMES = ("세대점검.md", "세대불일치.csv", "짝없음.csv",
                  "일치목록.csv", "형제점검.csv")


def _write_artifacts(analysis, out_dir, min_coverage, command):
    """다섯 자리를 **모두 먼저** 검사한 뒤에 쓴다 — 네 개만 남은 리포트를 만들지 않는다."""
    writer.check_artifact_targets(out_dir, ARTIFACT_NAMES)
    written = []
    written.append(writer.write_text(
        out_dir, "세대점검.md",
        report.render_markdown(analysis, min_coverage=min_coverage, command=command)))
    written.append(writer.write_csv(
        out_dir, "세대불일치.csv", report.MISMATCH_HEADER, report.mismatch_rows(analysis)))
    written.append(writer.write_csv(
        out_dir, "짝없음.csv", report.UNPAIRED_HEADER, report.unpaired_rows(analysis)))
    written.append(writer.write_csv(
        out_dir, "일치목록.csv", report.MATCH_HEADER, report.match_rows(analysis)))
    written.append(writer.write_csv(
        out_dir, "형제점검.csv", report.SIBLING_HEADER, report.sibling_rows(analysis)))
    return written


def main(argv=None, stdout=None, stderr=None):
    """진입점. 예외를 밖으로 내보내지 않고 종료코드로 바꾼다."""
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = _SafeStream(stdout if stdout is not None else sys.stdout)
    stderr = _SafeStream(stderr if stderr is not None else sys.stderr)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_REFUSED if exc.code else EXIT_OK

    if not math.isfinite(args.mtime_tolerance) or args.mtime_tolerance < 0:
        stderr.write("--mtime-tolerance 는 0 이상이어야 합니다 "
                     "(음수면 시각이 같아도 방향을 말하고, inf 면 모든 판정이 "
                     "사라집니다): %r\n"
                     % args.mtime_tolerance)
        return EXIT_REFUSED
    if not math.isfinite(args.min_coverage) or not (0.0 <= args.min_coverage <= 1.0):
        stderr.write("--min-coverage 는 0 과 1 사이여야 합니다: %r\n" % args.min_coverage)
        return EXIT_REFUSED
    if args.max_bytes < 0:
        stderr.write("--max-bytes 는 0 이상이어야 합니다: %r\n" % args.max_bytes)
        return EXIT_REFUSED

    exclude = None if args.include_archives else scanning.DEFAULT_EXCLUDE_PATTERN
    exts = tuple(args.ext) if args.ext else scanning.DEFAULT_EXTS
    exts = tuple(e if e.startswith(".") else "." + e for e in exts)

    work_root = os.path.abspath(os.path.expanduser(args.work))
    if not os.path.isdir(work_root):
        stderr.write("--work 폴더가 없습니다: %s\n" % work_root)
        return EXIT_REFUSED

    if not args.package:
        if args.inspect:
            # --inspect 는 판정이 아니라 "무엇을 읽었는지"를 묻는 것이다.
            # 봉투가 없어도 작업 폴더를 읽어 인쇄하고 후보를 보여 준다.
            # 판정은 하지 않았으므로 2(판정 없이 거절)로 끝낸다.
            _inspect_without_package(work_root, exts, args.max_bytes, exclude, stdout)
            stderr.write("--inspect 는 판정하지 않습니다 (exit 2).\n")
            return EXIT_REFUSED
        _print_candidates(work_root, exclude, stdout)
        stderr.write("\n봉투 폴더를 특정하지 못해 판정하지 않았습니다 (--package 필요).\n")
        return EXIT_REFUSED

    try:
        analysis = engine.analyze(
            work_root, args.package, exts=exts, max_bytes=args.max_bytes,
            exclude_pattern=exclude, tolerance=args.mtime_tolerance,
            check_siblings=not args.no_siblings,
        )
    except StalecheckError as exc:
        stderr.write(exc.message + "\n")
        return exc.exit_code

    if args.inspect:
        _inspect_report(analysis, stdout)
        # 판정하지 않았으므로 0(=치명 없음)으로 끝내지 않는다.
        # `stalecheck --inspect && 제출` 이 통과해 버리는 길을 막는다.
        stderr.write("--inspect 는 판정하지 않습니다 (exit 2).\n")
        return EXIT_REFUSED

    out_dir = None
    if args.out_dir:
        try:
            out_dir = writer.prepare_out_dir(
                args.out_dir, forbidden_roots=[analysis.work_root])
        except StalecheckError as exc:
            stderr.write(exc.message + "\n")
            return exc.exit_code

    command = _redacted_command(argv)
    try:
        text = report.render_console(analysis, min_coverage=args.min_coverage)
    except StalecheckError as exc:
        stderr.write(exc.message + "\n")
        return exc.exit_code

    if not args.quiet:
        stdout.write(text + "\n")

    if out_dir is not None:
        try:
            written = _write_artifacts(analysis, out_dir, args.min_coverage, command)
        except StalecheckError as exc:
            stderr.write(exc.message + "\n")
            return exc.exit_code
        if not args.quiet:
            stdout.write("\n[산출물] %s\n" % out_dir)
            for path in written:
                stdout.write("  %s\n" % os.path.basename(path))

    # 종료코드 — 3이 1보다 우선한다.
    coverage = analysis.coverage
    if analysis.unreadable:
        stderr.write("읽지 못한 파일이 %d개 있어 판정을 신뢰할 수 없습니다 (exit 3).\n"
                     % len(analysis.unreadable))
        return EXIT_UNDECIDABLE
    if analysis.pair_count == 0 and analysis.package_had_files:
        # 봉투에 파일은 있는데 한 쌍도 비교하지 못했다 — 할 말이 없다는 뜻이고,
        # 0(=치명 없음)으로 끝내면 "확인했다"로 읽힌다. 판정 불가로 떨어뜨린다.
        work_ext, pkg_ext = analysis.skipped_ext
        hint = ""
        if pkg_ext:
            hint = (" 봉투 파일 %d개가 대상 확장자 밖이었습니다 (%s). "
                    "포함하려면 %s"
                    % (sum(pkg_ext.values()),
                       " · ".join("%s %d" % (e.lstrip("."), n)
                                  for e, n in pkg_ext.most_common(4)),
                       " ".join("--ext %s" % e.lstrip(".")
                                for e, _ in pkg_ext.most_common(3) if e.startswith("."))))
        stderr.write("비교한 쌍이 하나도 없어 판정하지 않았습니다 (exit 3).%s\n" % hint)
        return EXIT_UNDECIDABLE
    if args.min_coverage > 0 and (coverage is None or coverage < args.min_coverage):
        stderr.write("짝지음 비율이 기준(%.0f%%) 미만이라 판정을 신뢰할 수 없습니다 (exit 3).\n"
                     % (args.min_coverage * 100))
        return EXIT_UNDECIDABLE
    if analysis.ambiguous_count:
        # 이름이 겹쳐 판정하지 않은 봉투 파일이 있다. 0 으로 끝내면
        # "치명 0건" 이 되는데, 사실은 **보지 않은 것**이다.
        stderr.write(
            "이름이 겹쳐 판정하지 못한 봉투 파일이 %d개 있습니다 — "
            "판정을 신뢰할 수 없습니다 (exit 3).\n" % analysis.ambiguous_count)
        return EXIT_UNDECIDABLE
    if analysis.undecidable_rows:
        # 내용이 다른데 방향을 말할 수 없는 쌍이다. 0 으로 끝내면
        # `cp -p`·Dropbox·rsync --times 가 mtime 을 보존한 트리에서
        # **정확히 이 툴이 잡아야 할 상황**이 깨끗하다는 신호로 나간다.
        stderr.write(
            "내용이 다른데 방향을 말할 수 없는 쌍이 %d개 있습니다 — "
            "판정을 신뢰할 수 없습니다 (exit 3).\n" % len(analysis.undecidable_rows))
        return EXIT_UNDECIDABLE
    if analysis.critical_rows:
        return EXIT_CRITICAL
    return EXIT_OK


def exit_with(code):
    """종료코드를 **그대로** 프로세스 상태로 만든다.

    파이프가 끊겼거나 fd 가 닫혀 있으면, 인터프리터 종료 시 stdout 플러시가 실패하며
    CPython 이 상태를 **120** 으로 덮어쓴다. 120 은 이 툴의 종료코드가 아니고,
    `| head -20`·`| less` 로 조기 종료한 것만으로 **깨끗한 트리가 실패로 보고된다.**
    이 툴의 답은 종료코드에 있으므로, 출력 실패가 답을 바꾸게 두지 않는다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except (OSError, ValueError):
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, stream.fileno())
                os.close(devnull)
            except (OSError, ValueError, AttributeError):
                pass
    sys.stdout = sys.stderr = None
    os._exit(code)


def run():  # pragma: no cover - 콘솔 스크립트 래퍼
    exit_with(main())
