"""명령줄 진입점.

종료 코드
  0  대조 대상 전부 출처 확인됨
  1  치명 발견(출처 없음 / 구버전 잔존)
  2  경고만 있음 · 또는 입력·인자 오류(`--outputs` 미지정 포함)
  3  판정불가 — 대조 가능 숫자 부족, 미매칭율 임계 초과, 원고·번들 처리 불가

3 은 1 보다 우선합니다. 대조율이 낮은 상태에서 낸 치명은 신뢰할 수 없습니다.
"""

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .analyze import (EXIT_UNDECIDABLE, EXIT_WARN, SectionError, analyze,
                      finalize_exit, parse_sections)
from .bundle import collect, require_outputs
from .manuscript import read_manuscript
from .numbers import extract_numbers
from .report import (ReportIntegrityError, render_console, write_outputs)
from .safety import (InputError, check_input_dir, check_input_file,
                     prepare_out_dir, resolve)

DEFAULT_OUT_DIR = "출처대조결과"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracecheck",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "원고에 적힌 숫자가 실제 분석 산출물에서 왔는지 전수 대조합니다.\n"
            "Abstract·Results·표·캡션의 숫자를 출력 번들(CSV/JSON/XLSX/MD/TXT)과\n"
            "값으로만 대조하고, 어느 출력에도 없는 숫자와 옛 번들에만 있는 숫자를 찾습니다."),
        epilog=(
            "예)\n"
            "  tracecheck 원고.docx --outputs 분석출력_2026-08-18/ \\\n"
            "             --previous 분석출력_2026-08-03/ --out-dir 출처대조결과\n"
            "  tracecheck 원고.md --outputs 출력/ --sections abstract,results,tables\n"
            "  tracecheck 원고.docx --dump-text     # 무엇을 대조 대상으로 뽑는지 먼저 보기\n"))
    parser.add_argument("manuscript", help="원고 파일 (.docx/.md/.tex/.txt)")
    parser.add_argument("--outputs", action="append", metavar="폴더",
                        help="분석 출력 번들 폴더(또는 파일). 여러 번 지정 가능")
    parser.add_argument("--previous", action="append", metavar="폴더",
                        help="재분석 이전 출력 번들 — 옛 값이 원고에 남아 있는지 검사")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, metavar="폴더",
                        help="산출물 폴더 (기본 %s)" % DEFAULT_OUT_DIR)
    parser.add_argument("--sections", default="", metavar="목록",
                        help="대조할 절 (기본 abstract,results,tables,captions)")
    parser.add_argument("--max-unmatched", type=float, default=30.0, metavar="퍼센트",
                        help="미매칭율이 이 값을 넘으면 판정불가(exit 3). 기본 30")
    parser.add_argument("--min-comparable", type=int, default=5, metavar="N",
                        help="대조 가능 숫자가 이보다 적으면 판정불가. 기본 5")
    parser.add_argument("--chance-matches", type=int, default=12, metavar="N",
                        help="같은 값이 번들 N곳 이상에서 나오고 자릿수가 낮으면 경고. 기본 12")
    parser.add_argument("--max-files", type=int, default=500, metavar="N",
                        help="번들에서 읽을 최대 파일 수. 기본 500")
    parser.add_argument("--max-bytes", type=int, default=20_000_000, metavar="N",
                        help="파일 하나의 최대 크기(바이트). 기본 20,000,000")
    parser.add_argument("--max-cells", type=int, default=200_000, metavar="N",
                        help="번들 하나에서 색인할 최대 수치 셀 수. 기본 200,000")
    parser.add_argument("--dump-text", action="store_true",
                        help="대조 대상으로 뽑힌 숫자 목록만 출력하고 끝냅니다(매칭 안 함)")
    parser.add_argument("--no-files", action="store_true",
                        help="산출물 파일을 만들지 않고 화면에만 출력")
    parser.add_argument("--quiet", action="store_true", help="콘솔 리포트를 줄입니다")
    parser.add_argument("--version", action="version",
                        version="tracecheck %s" % __version__)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except InputError as exc:
        sys.stderr.write("오류: %s\n" % exc)
        return EXIT_WARN
    except SectionError as exc:
        sys.stderr.write("오류: %s\n" % exc)
        return EXIT_WARN
    except ReportIntegrityError as exc:
        sys.stderr.write("리포트 무결성 오류: %s\n" % exc)
        return EXIT_UNDECIDABLE
    except KeyboardInterrupt:
        sys.stderr.write("\n중단되었습니다.\n")
        return EXIT_WARN


def _run(args) -> int:
    manuscript_path = check_input_file(args.manuscript, what="원고")
    sections = parse_sections(args.sections)

    if args.dump_text:
        return _dump_text(manuscript_path, sections)

    require_outputs(args.outputs)
    for name in ("max_files", "max_bytes", "max_cells", "min_comparable",
                 "chance_matches"):
        if getattr(args, name) <= 0:
            raise InputError("--%s 는 1 이상이어야 합니다." % name.replace("_", "-"))
    if not 0 <= args.max_unmatched <= 100:
        raise InputError("--max-unmatched 는 0~100 사이여야 합니다.")

    current_roots = [check_input_dir(p, what="출력 번들") for p in args.outputs]
    previous_roots = [check_input_dir(p, what="이전 번들")
                      for p in (args.previous or [])]
    if manuscript_path in _files_under(current_roots + previous_roots):
        raise InputError("원고가 출력 번들 폴더 안에 있습니다 — 원고 자신과 대조하게 됩니다.")

    manuscript = read_manuscript(manuscript_path)
    current = collect(current_roots, "현재", max_files=args.max_files,
                      max_bytes=args.max_bytes, max_cells=args.max_cells)
    previous = (collect(previous_roots, "이전", max_files=args.max_files,
                        max_bytes=args.max_bytes, max_cells=args.max_cells)
                if previous_roots else None)

    analysis = analyze(manuscript, current, previous, sections=sections,
                       max_unmatched=args.max_unmatched,
                       min_comparable=args.min_comparable,
                       chance_matches=args.chance_matches)

    # 산출물 경로 검사를 **리포트 출력 전에** 합니다. 뒤에 하면 화면에는
    # "종료 코드 0" 이 찍히고 프로세스는 2 로 죽는 모순이 생깁니다.
    protected: List[str] = []
    out_dir = None
    if not args.no_files:
        protected = [manuscript_path] + sorted(
            _files_under(current_roots + previous_roots))
        out_dir = prepare_out_dir(args.out_dir,
                                  [r for r in current_roots + previous_roots
                                   if os.path.isdir(r)])

    console = render_console(analysis)
    if not args.quiet:
        sys.stdout.write(console + "\n")
    else:
        sys.stdout.write(console.splitlines()[-1] + "\n")

    if out_dir is not None:
        written = write_outputs(analysis, out_dir, protected)
        if not args.quiet:
            sys.stdout.write("\n산출물 (%s):\n" % args.out_dir)
            for path in written:
                sys.stdout.write("  · %s\n" % os.path.basename(path))
    return finalize_exit(analysis)


def _files_under(roots: List[str]) -> set:
    found = set()
    for root in roots:
        real = resolve(root)
        if os.path.isfile(real):
            found.add(real)
            continue
        for dirpath, _dirnames, filenames in os.walk(real, followlinks=False):
            for name in filenames:
                found.add(resolve(os.path.join(dirpath, name)))
    return found


def _dump_text(path: str, sections: List[str]) -> int:
    """매칭 없이 '무엇을 대조 대상으로 뽑았는지'만 봅니다.

    실무 원고에서 절 분류와 표 셀 복원이 실제로 되는지 30분 안에 확인하기 위한
    진단 모드입니다. 여기서 인용값·용량이 섞여 나오면 건너뜀 규칙을 좁혀야 합니다.
    """
    manuscript = read_manuscript(path)
    sys.stdout.write("tracecheck %s — 대상 추출 점검(--dump-text)\n" % __version__)
    sys.stdout.write("원고: %s (%s, 블록 %d개, 표 %d개)\n\n"
                     % (os.path.basename(path), manuscript.line_kind,
                        len(manuscript.blocks), manuscript.table_count))
    total = compared = off_section = 0
    skipped_counts = {}
    for block in manuscript.blocks:
        if block.kind == "heading":
            sys.stdout.write("\n[%s] %s\n" % (block.section, block.text[:70]))
            continue
        numbers = extract_numbers(block)
        if not numbers:
            continue
        targeted = block.target_key in sections
        if not targeted:
            off_section += len(numbers)
            continue
        total += len(numbers)
        for number in numbers:
            if number.skip:
                skipped_counts[number.skip] = skipped_counts.get(number.skip, 0) + 1
                sys.stdout.write("  - %-6s %-14s %-10s [건너뜀: %s]\n"
                                 % (block.line, block.loc[:14], number.text,
                                    number.skip))
            else:
                compared += 1
                sys.stdout.write("  ● %-6s %-14s %-10s %s\n"
                                 % (block.line, block.loc[:14], number.text,
                                    number.context[:60]))
    sys.stdout.write("\n추출 %d개 · 대조 대상 %d개 · 건너뜀 %d개 "
                     "(대조 제외 절의 숫자 %d개는 세지 않음)\n"
                     % (total, compared, sum(skipped_counts.values()), off_section))
    for reason, count in sorted(skipped_counts.items(), key=lambda kv: -kv[1]):
        sys.stdout.write("    %-20s %d\n" % (reason, count))
    sys.stdout.write("\n※ 이건 진단 모드입니다 — 대조는 하지 않았습니다. "
                     "판정을 받으려면 `--outputs` 를 주고 다시 실행하세요.\n")
    return 0


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
