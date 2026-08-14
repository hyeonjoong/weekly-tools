"""revcheck 명령줄 진입점.

    revcheck --old 제출본.docx --new 개정본.docx --response 응답서.docx --out-dir 결과

종료코드
    0 정상 / 1 치명 있음 / 2 경고 있음 / 3 판정불가(코멘트 번호 체계를 못 잡음,
    파일을 못 읽음 등). **판정불가를 0 으로 내리지 않는 것**이 이 툴의 원칙이다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .comments import parse_comment_ids
from .docio import DocumentError, SUPPORTED_SUFFIXES, read_document
from .engine import Options, run_check
from .model import EXIT_UNDECIDABLE
from .normalize import strip_control
from .quotes import DEFAULT_RATIO, MIN_QUOTE_CHARS
from .report import OutputError, render_text, write_outputs

__all__ = ["main", "build_parser"]


class _Parser(argparse.ArgumentParser):
    """사용법 오류도 2(경고)가 아니라 3(판정불가)으로 끝낸다 — 등급과 섞이면 안 된다."""

    def error(self, message: str):  # pragma: no cover - argparse 경로
        self.print_usage(sys.stderr)
        sys.stderr.write(f"\n오류: {message}\n")
        raise SystemExit(EXIT_UNDECIDABLE)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="revcheck",
        description=(
            "리비전 응답 점검 — 응답서에 적은 약속이 개정본에 실제로 반영됐는지 "
            "대조합니다. 네트워크를 쓰지 않고 원본 파일을 수정하지 않습니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예)\n"
            "  revcheck --old 제출본.docx --new 개정본.docx --response 응답서.docx\n"
            "  revcheck --old old.md --new new.md --response resp.md "
            "--comments 1-1,1-2,2-1 --out-dir 결과\n"
        ),
    )
    parser.add_argument("--old", required=True, help="최초 제출본 " + _fmt_help())
    parser.add_argument("--new", required=True, help="개정본 " + _fmt_help())
    parser.add_argument("--response", required=True, help="응답서(point-by-point)")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="리비전점검.md / 문제목록.csv / 변경목록.csv / 추가문헌.csv 를 쓸 폴더 "
        "(생략하면 화면에만 출력)",
    )
    parser.add_argument(
        "--comments",
        default=None,
        help="코멘트 번호를 직접 지정 (예: 1-1,1-2,2-1,E-1). "
        "응답서 형식이 특이해 자동 인식이 안 될 때 씁니다.",
    )
    parser.add_argument(
        "--tracked",
        choices=("accept", "reject"),
        default="accept",
        help="변경내용 추적이 켜진 .docx 를 어느 상태로 읽을지 "
        "(accept=모두 수락한 개정본, reject=원본). 기본 accept",
    )
    parser.add_argument(
        "--ratio",
        type=float,
        default=DEFAULT_RATIO,
        help=f"인용 문구를 '표현만 다름'으로 볼 최소 일치율 (기본 {DEFAULT_RATIO})",
    )
    parser.add_argument(
        "--min-quote-chars",
        type=int,
        default=MIN_QUOTE_CHARS,
        help=f"이보다 짧은 인용은 우연 일치가 많아 검사에서 뺍니다 (기본 {MIN_QUOTE_CHARS})",
    )
    parser.add_argument("--quiet", action="store_true", help="화면 출력을 줄입니다")
    parser.add_argument("--version", action="version", version=f"revcheck {__version__}")
    return parser


def _fmt_help() -> str:
    return "(" + " / ".join(SUPPORTED_SUFFIXES) + ")"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """예상 못 한 예외도 **종료코드 3(판정불가)** 으로 끝낸다.

    파이썬이 그냥 죽으면 종료코드가 1 이 되는데, 이 툴에서 1 은 '치명 있음'이다.
    아무것도 검사하지 못한 실행을 '치명 발견'으로 보고하면 안 된다.
    """
    try:
        return _run(argv)
    except KeyboardInterrupt:  # pragma: no cover - 사용자가 중단
        sys.stderr.write("\n중단했습니다.\n")
        return EXIT_UNDECIDABLE
    except Exception as exc:  # noqa: BLE001 - 마지막 방어선
        sys.stderr.write(
            f"오류: 예상하지 못한 문제로 점검을 마치지 못했습니다 "
            f"({type(exc).__name__}: {strip_control(str(exc))[:200]}).\n"
            "아무것도 '이상 없음'으로 표시하지 않았습니다(종료코드 3).\n"
        )
        return EXIT_UNDECIDABLE


def _run(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not 0.5 <= args.ratio <= 1.0:
        sys.stderr.write("오류: --ratio 는 0.5 ~ 1.0 사이여야 합니다.\n")
        return EXIT_UNDECIDABLE
    if args.min_quote_chars < 5:
        sys.stderr.write("오류: --min-quote-chars 는 5 이상이어야 합니다.\n")
        return EXIT_UNDECIDABLE

    comment_ids = None
    if args.comments:
        try:
            comment_ids = parse_comment_ids(args.comments)
        except ValueError as exc:
            sys.stderr.write(f"오류: {exc}\n")
            return EXIT_UNDECIDABLE

    try:
        old = read_document(args.old, "제출본", args.tracked)
        new = read_document(args.new, "개정본", args.tracked)
        resp = read_document(args.response, "응답서", args.tracked, split_lines=True)
    except DocumentError as exc:
        sys.stderr.write(f"오류: {strip_control(str(exc))}\n")
        return EXIT_UNDECIDABLE

    opts = Options(
        tracked=args.tracked,
        ratio=args.ratio,
        min_quote_chars=args.min_quote_chars,
        comment_ids=comment_ids,
    )
    result = run_check(old, new, resp, opts)
    code = result.exit_code()
    if any(doc.truncated for doc in (old, new, resp)):
        # 일부만 읽었다면 '정상'도 '치명 목록이 전부'도 말할 수 없다.
        # 찾은 것은 그대로 보여 주되 종료코드는 판정불가로 내린다.
        code = EXIT_UNDECIDABLE

    written: List[Path] = []
    if args.out_dir:
        try:
            written = write_outputs(
                result,
                args.out_dir,
                code,
                sources=[old.path, new.path, resp.path],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        except OutputError as exc:
            sys.stderr.write(f"오류: {strip_control(str(exc))}\n")
            return EXIT_UNDECIDABLE

    out_dir = Path(args.out_dir) if written else None
    text = render_text(result, code, out_dir)
    if args.quiet:
        head = text.split("\n\n")[0]
        print(head)
        if result.undecidable:
            print(f"[판정불가] {result.undecidable}")
        else:
            print(f"→ 치명 {len(result.criticals)}건 / 경고 {len(result.warnings)}건")
        # 커버리지 자백 없이는 리포트를 내지 않는다 — 요약 모드에서도 지킨다.
        for item in result.coverage:
            print(item.render())
        if written:
            print(f"→ {written[0].parent} 에 저장했습니다.")
        label = {0: "정상", 1: "치명 있음", 2: "경고 있음", 3: "판정불가"}[code]
        print(f"→ 종료코드 {code} ({label})")
    else:
        print(text)
    return code


if __name__ == "__main__":  # pragma: no cover - 진입점
    raise SystemExit(main())
