"""numcheck 명령줄 진입점.

    numcheck 원고.docx --out-dir ./검토 --scale ISI=0:28:7

원고는 **읽기만** 하고, 출력은 ``--out-dir`` 안에만 만든다. 네트워크는 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .docio import ManuscriptError, read_manuscript
from .engine import analyze_manuscript
from .options import Options
from .report import (
    OUTPUT_FILES,
    OutputRefused,
    one_line,
    render_console,
    write_csvs,
)
from .scales import ScaleError, ScaleRegistry, load_scale_config, parse_scale_arg
from .sections import assign_sections

__all__ = ["main", "build_parser"]

_EPILOG = """\
예시
  numcheck 원고.docx
  numcheck 원고.docx --out-dir 검토_20260813
  numcheck 원고.md --scale ISI=0:28:7 --lang en
  numcheck 원고.docx --scale-config scales.json --alpha 0.01
  numcheck 원고.docx --dump-text | less        # 무엇을 읽었는지 직접 확인

종료코드
  0  문제 없음        1  치명 있음        2  경고만 있음
  3  입력 처리 불가 — 파싱 실패, 재계산 가능 claim 부족, 인자 오류, 저장 거부
     (2 는 '경고만'을 뜻하므로 인자 오류에 쓰지 않습니다)
"""


class _Parser(argparse.ArgumentParser):
    """사용법 오류도 종료코드 3 으로 낸다.

    argparse 의 기본값은 2 인데, numcheck 에서 2 는 "경고만 있음"이다. CI 스크립트가
    2 를 '경고니까 통과'로 처리하면, **아예 실행되지 않은 run 을 통과로 삼는다.**
    """

    def error(self, message: str):  # pragma: no cover - argparse 내부 경로
        self.print_usage(sys.stderr)
        print(f"{self.prog}: 인자 오류: {message}", file=sys.stderr)
        raise SystemExit(3)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="numcheck",
        description="원고에 적힌 숫자를 전수 재계산해 대조합니다 (오프라인, 읽기 전용).",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("manuscript", help="원고 파일 (.docx / .md / .tex / .txt)")
    parser.add_argument("--out-dir", metavar="폴더",
                        help=f"결과 저장 폴더. {', '.join(OUTPUT_FILES)} 를 만듭니다.")
    parser.add_argument("--scale", action="append", default=[], metavar="이름=최소:최대:문항수",
                        help="GRIM 용 정수 척도 추가 (예: ISI=0:28:7). 여러 번 쓸 수 있습니다.")
    parser.add_argument("--percent-of-count", action="append", default=[], metavar="이름",
                        help="해당 척도를 '고정 목록 대비 정답 백분율'로 취급합니다.")
    parser.add_argument("--scale-config", metavar="scales.json",
                        help="척도 정의 JSON 파일")
    parser.add_argument("--lang", choices=("ko", "en"), default="ko",
                        help="리포트 언어 (기본 ko)")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="유의성 문구 검사에 쓸 기준 (기본 0.05)")
    parser.add_argument("--tolerance", type=float, default=1.0, metavar="배수",
                        help="반올림 허용 배수. 1.0 = 반올림/버림/올림 모두 허용(기본). "
                             "0.5 로 낮추면 반올림만 허용하며 지적과 오탐이 함께 늘어납니다.")
    parser.add_argument("--min-checked", type=int, default=5, metavar="개수",
                        help="이 개수 미만이면 '원고를 못 읽었다'로 보고 종료코드 3 (기본 5)")
    parser.add_argument("--no-quote", action="store_true",
                        help="리포트에 원문 발췌를 넣지 않습니다(원고를 남에게 보낼 때).")
    parser.add_argument("--no-grimmer", action="store_true",
                        help="SD 기반 GRIMMER 검사를 끕니다.")
    parser.add_argument("--force", action="store_true",
                        help="출력 폴더에 같은 이름의 남의 파일이 있어도 덮어씁니다.")
    parser.add_argument("--quiet", action="store_true", help="콘솔 출력을 줄입니다.")
    parser.add_argument("--dump-text", action="store_true",
                        help="원고를 어떻게 읽었는지(줄번호·절) 출력하고 끝냅니다.")
    parser.add_argument("--version", action="version", version=f"numcheck {__version__}")
    return parser


def _build_registry(args) -> ScaleRegistry:
    registry = ScaleRegistry()
    if args.scale_config:
        registry.add_many(load_scale_config(args.scale_config))
    percent = {name.strip().lower() for name in args.percent_of_count}
    registry.add_many([
        parse_scale_arg(spec, percent_of_count=spec.split("=", 1)[0].strip().lower() in percent)
        for spec in args.scale
    ])
    unknown = percent - {s.name.strip().lower() for s in registry.scales}
    if unknown:
        raise ScaleError(
            "--percent-of-count 로 지정한 척도가 정의되지 않았습니다: "
            + ", ".join(sorted(unknown))
            + "\n  --scale 이름=최소:최대:문항수 로 함께 정의하세요."
        )
    return registry


def _resolve_out_dir(raw: str, manuscript: Path) -> Path:
    """출력 폴더를 안전하게 확정한다(원고 파일을 절대 덮어쓰지 않는다)."""
    if not raw.strip():
        raise ManuscriptError("--out-dir 가 비어 있습니다. 폴더 이름을 지정하세요.")
    try:
        out = Path(raw).expanduser()
        resolved = out.resolve()
    except (OSError, ValueError) as exc:
        # ValueError: 경로에 NUL 이 들어간 경우 (Path.resolve 가 던진다)
        raise ManuscriptError(f"출력 폴더 경로가 올바르지 않습니다 ({exc}).") from exc
    if resolved.exists() and not resolved.is_dir():
        raise ManuscriptError(f"출력 위치에 이미 파일이 있습니다(폴더가 아님): {resolved}")
    # macOS 는 한글 파일명을 NFD 로 저장한다. 정규화하지 않고 비교하면 이 가드가
    # 한국어 파일명에서만 조용히 무력해진다. (최종 방어선은 check_targets 의
    # samefile 비교이고, 이건 더 나은 오류 메시지를 위한 앞단 검사다.)
    normalized = unicodedata.normalize("NFC", manuscript.name)
    if resolved == manuscript.resolve().parent and normalized in OUTPUT_FILES:
        raise ManuscriptError("출력 파일 이름이 원고 파일과 같아 덮어쓸 위험이 있습니다.")
    return resolved


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not (0.0 < args.alpha < 1.0):
        parser.error("--alpha 는 0 과 1 사이여야 합니다.")
    if not (0.0 < args.tolerance <= 5.0):
        parser.error("--tolerance 는 0 초과 5 이하여야 합니다.")
    if args.min_checked < 0:
        parser.error("--min-checked 는 0 이상이어야 합니다.")

    try:
        registry = _build_registry(args)
    except ScaleError as exc:
        print(f"[척도 설정 오류] {exc}", file=sys.stderr)
        return 3

    manuscript_path = Path(args.manuscript).expanduser()
    try:
        ms = read_manuscript(manuscript_path)
    except ManuscriptError as exc:
        print(f"[원고를 읽을 수 없습니다] {exc}", file=sys.stderr)
        return 3

    if args.dump_text:
        assign_sections(ms)
        for ln in ms.lines:
            if not ln.stripped:
                continue
            print(f"{ln.no:>5}  [{ln.section:<12}{ln.kind:<9}] {ln.text[:200]}")
        return 0

    opts = Options(
        registry=registry,
        k=args.tolerance,
        alpha=args.alpha,
        lang=args.lang,
        quote=not args.no_quote,
        min_checked=args.min_checked,
        strict_grimmer=not args.no_grimmer,
    )
    report = analyze_manuscript(ms, opts)

    out_dir: Optional[Path] = None
    if args.out_dir is not None:   # 빈 문자열도 '지정했다'로 보고 오류를 낸다
        try:
            out_dir = _resolve_out_dir(args.out_dir, manuscript_path)
        except ManuscriptError as exc:
            print(f"[출력 폴더 오류] {exc}", file=sys.stderr)
            return 3

    text = render_console(report, args.lang, out_dir, args.min_checked)
    if out_dir is not None:
        try:
            write_csvs(report, out_dir, args.lang, text, force=args.force,
                       manuscript=args.manuscript)
        except OutputRefused as exc:
            print(f"[결과를 저장하지 않았습니다] {exc}", file=sys.stderr)
            return 3
        except OSError as exc:
            print(f"[결과를 저장하지 못했습니다] {exc}", file=sys.stderr)
            return 3
    if not args.quiet:
        print(text)
    else:
        print(one_line(report, args.min_checked, args.lang))
    return report.exit_code(args.min_checked)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
