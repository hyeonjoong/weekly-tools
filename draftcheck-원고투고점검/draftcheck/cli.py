"""draftcheck 명령줄 인터페이스.

    draftcheck 원고.docx --limits journals/sleepmed.json --out-dir 점검_20260806

종료 코드
    0  문제 없음(또는 ``--strict`` 없이 실행)
    1  ``--strict`` 이고 치명/경고가 있음
    2  사용법·입출력 오류
    3  ``--strict`` 이고 '점검 불가'(인용 0개 / 참고문헌 목록 없음)
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from .checks import LIMIT_FIELDS, run_checks
from .docio import ManuscriptError, _decode, detect_sections, read_manuscript
from .report import console_report, summary_line, write_outputs

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2
EXIT_UNVERIFIABLE = 3

_LIMIT_KEYS = {key for key, _, _ in LIMIT_FIELDS} | {"journal"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="draftcheck",
        description=(
            "투고 직전 원고(.docx/.md/.tex/.txt)의 자기 정합성을 기계적으로 대조합니다 — "
            "인용↔참고문헌, 그림/표 번호, 표본수, 통계 보고, 약어, 분량. "
            "네트워크를 쓰지 않고 원본 파일을 수정하지 않습니다."
        ),
        epilog=(
            "예) draftcheck 원고.docx --limits journals/sleepmed.json --out-dir 점검_20260806\n"
            "    draftcheck 원고.md --strict        # 문제가 있으면 종료 코드 1"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("manuscript", help="원고 파일 (.docx / .md / .tex / .txt)")
    parser.add_argument(
        "--limits",
        metavar="JSON",
        help="저널 한도 JSON (title_chars_max, abstract_words_max, body_words_max, "
        "references_max, figures_tables_max)",
    )
    parser.add_argument(
        "--citation-style",
        choices=["auto", "numeric", "author-year", "cite-key"],
        default="auto",
        help="인용 스타일 (기본 auto: 본문을 세어 자동 판별하고 결과를 출력에 밝힘)",
    )
    parser.add_argument(
        "--out-dir",
        metavar="DIR",
        help="점검결과.md / 문제목록.csv / references.csv 를 쓸 폴더 "
        "(지정하지 않으면 화면 출력만 하고 파일을 만들지 않습니다)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="문제나 '점검 불가'가 있으면 0이 아닌 종료 코드 (CI·스크립트용)",
    )
    parser.add_argument(
        "--abbrev-ok",
        metavar="ABC,DEF",
        default="",
        help="정의 없이 써도 되는 약어 목록(쉼표 구분)",
    )
    parser.add_argument(
        "--dump-text",
        action="store_true",
        help="추출된 본문 텍스트를 줄번호와 함께 그대로 출력 (인식 문제 진단용)",
    )
    parser.add_argument("--quiet", action="store_true", help="요약 한 줄만 출력")
    parser.add_argument("--version", action="version", version=f"draftcheck {__version__}")
    return parser


def _reject_constant(name: str):  # pragma: no cover - 아래 예외로 즉시 올라간다
    raise ValueError(f"허용되지 않는 값 {name}")


def load_limits(path: Optional[str]) -> Dict[str, object]:
    if not path:
        return {}
    file_path = Path(path).expanduser()
    try:
        raw_bytes = file_path.read_bytes()
    except OSError as exc:
        raise ManuscriptError(f"저널 한도 파일을 읽을 수 없습니다: {file_path} ({exc})") from exc
    # 한도 파일도 원고와 같은 사람이 메모장에서 만든다 — CP949로 저장돼 오는 일이 흔하다.
    raw, _ = _decode(raw_bytes)
    try:
        # json 은 표준이 아닌 Infinity/NaN 리터럴을 받아 준다. int() 에서 터지므로 여기서 막는다.
        data = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ManuscriptError(
            f"저널 한도 파일이 올바른 JSON이 아닙니다: {file_path} ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise ManuscriptError("저널 한도 파일은 JSON 객체({...})여야 합니다.")
    cleaned: Dict[str, object] = {}
    unknown: List[str] = []
    for key, value in data.items():
        if key == "journal":
            cleaned[key] = str(value)
            continue
        if key not in _LIMIT_KEYS:
            unknown.append(key)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ManuscriptError(f"한도 '{key}' 는 양의 정수여야 합니다 (받은 값: {value!r}).")
        if not math.isfinite(value) or value <= 0 or value > 10_000_000:
            raise ManuscriptError(
                f"한도 '{key}' 는 0보다 크고 현실적인 정수여야 합니다 (받은 값: {value!r})."
            )
        cleaned[key] = int(value)
    if unknown:
        print(
            f"[알림] 한도 파일에서 모르는 항목은 무시했습니다: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )
    return cleaned


def _dump_text(ms, sec) -> None:
    print(f"# {ms.path.name} — {ms.fmt} / {len(ms.lines)}{ms.line_label}")
    for note in ms.notes:
        print(f"# 메모: {note}")
    print(f"# 섹션: {sec.headings}")
    for line in ms.lines:
        print(f"{line.no:>5} [{line.kind[:4]:<4}] {line.text}")


def _console_width() -> int:
    """터미널 폭에 맞춘다(80칸 터미널에서 한글이 잘리지 않도록). 범위는 60~100."""
    try:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:  # pragma: no cover - 아주 이상한 환경
        columns = 80
    return max(60, min(100, columns - 2))


def _run(args) -> int:
    try:
        limits = load_limits(args.limits)
        ms = read_manuscript(args.manuscript)
        sec = detect_sections(ms)
    except ManuscriptError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except RecursionError:
        # 재귀 파서가 한도에 부딪혀도 사용자에게는 깔끔한 메시지와 '오류' 코드여야 한다.
        # 트레이스백 + 종료 코드 1은 '치명 결함 발견'과 구별되지 않아 위험하다.
        print(
            "오류: 파일 구조가 너무 깊게 중첩되어 해석할 수 없습니다. "
            "정상적인 원고 파일이 맞는지 확인하세요.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    if args.dump_text:
        _dump_text(ms, sec)
        return EXIT_OK

    extra_known = {
        token.strip().upper() for token in args.abbrev_ok.split(",") if token.strip()
    }
    result = run_checks(
        ms, sec, limits=limits, style=args.citation_style, extra_known=extra_known
    )

    written: List[Path] = []
    if args.out_dir is not None:
        if not args.out_dir.strip():
            print("오류: --out-dir 에 폴더 이름이 비어 있습니다.", file=sys.stderr)
            return EXIT_ERROR
        try:
            written = write_outputs(
                result, args.out_dir, generated=date.today().isoformat()
            )
        except ManuscriptError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return EXIT_ERROR
        except OSError as exc:
            print(f"오류: 결과를 쓸 수 없습니다 ({exc}).", file=sys.stderr)
            return EXIT_ERROR

    if args.quiet:
        print(f"{ms.path.name}: {summary_line(result)}")
    else:
        print(console_report(result, width=_console_width()))
        if written:
            print(f"→ {written[0].parent}")
            for path in written:
                print(f"   · {path.name}")
            print()

    if args.strict:
        if result.unverifiable:
            return EXIT_UNVERIFIABLE
        if result.n_critical or result.n_warning:
            return EXIT_FINDINGS
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except BrokenPipeError:
        # README가 안내하는 `--dump-text | less` 에서 less 를 먼저 닫으면 발생한다.
        # 파이프가 끊긴 것은 오류가 아니므로 조용히 끝낸다.
        try:
            sys.stdout.close()
        except Exception:  # pragma: no cover - 이미 끊긴 스트림
            pass
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
