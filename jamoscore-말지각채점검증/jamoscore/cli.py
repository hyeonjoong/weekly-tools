"""jamoscore 명령줄 진입점.

종료코드
    0  치명 0건
    1  치명 N건
    2  판정 없이 거절 — 문항 단위 데이터가 아님 / 열 검출 실패 / --subtest 미지정
    3  판정 불가 — 다 읽지 못함. **3이 1보다 우선한다** (다 못 읽었으면
       "치명 1건"은 거짓말이다)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence

from . import __version__, analyze, features as features_mod, longcsv, methods, report, summary
from .findings import CRITICAL, CSV_HEADER, INFO, WARNING, Finding, count_by_severity
from .hangul import NO_JONG, normalize
from .reader import (LayoutError, Pass, classify_sheets, extract_passes,
                     find_blocks, find_header_row)
from .safeio import (OutputError, base_name, ensure_out_dir, ensure_subdir,
                     mask_identifiers, sanitize_line, strip_paths, write_csv,
                     write_text)
from .spec import SpecError, UNDECIDABLE, parse_all, specs_by_name
from .xlsx import MAX_ROWS, Workbook, XlsxError, column_name

EXIT_OK, EXIT_CRITICAL, EXIT_REFUSED, EXIT_UNDETERMINED = 0, 1, 2, 3

_EPILOG = """\
예시
  jamoscore 본실험_결과.xlsx --inspect \\
      --subtest "자음:목표=2음절초성,문항=18"

  jamoscore 본실험_결과.xlsx \\
      --subtest "자음:목표=2음절초성,문항=18" \\
      --subtest "모음:목표=1음절중성,문항=14" \\
      --subtest "일음절:단위=단어,목표=전체,문항=18" \\
      --subtest "일음절:단위=음소,목표=초중종,문항=18,만점=3" \\
      --subject-pattern "^s[0-9]+$" \\
      --answer-key 언어검사_정답 --summary total-최종 \\
      --out-dir 채점검증_0911

이 툴은 점수를 고치지 않고, 군간·시점간 검정을 하지 않습니다.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jamoscore",
        description="말지각검사 채점 엑셀을 문항 단위에서 다시 계산하고, "
                    "요약표·규칙 채점과 갈리는 곳만 찍어 줍니다.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="채점 엑셀(.xlsx) 또는 문항 단위 long CSV")
    p.add_argument("--subtest", action="append", default=[], metavar="명세",
                   help='예: "자음:목표=2음절초성,문항=18". 같은 검사 이름을 두 번 '
                        '쓰면 세로로 쌓인 두 번째 채점(패스)으로 봅니다.')
    p.add_argument("--timepoints", default="사전,사후",
                   help="시점 이름(쉼표 구분). 기본 '사전,사후'")
    p.add_argument("--subject-pattern", default=None, metavar="정규식",
                   help=r"피험자 시트 이름 정규식 (예: '^s[0-9]+$')")
    p.add_argument("--pii-sheet", action="append", default=[], metavar="이름조각",
                   help="식별정보로 보고 **열지 않을** 시트 이름 조각 (여러 번 지정 가능)")
    p.add_argument("--response-alias", action="append", default=[], metavar="이름",
                   help="응답 열 헤더로 인정할 이름 추가")
    p.add_argument("--score-alias", action="append", default=[], metavar="이름",
                   help="점수 열 헤더로 인정할 이름 추가")
    p.add_argument("--answer-key", default=None, metavar="시트",
                   help="정답(자극 리스트) 시트 이름")
    p.add_argument("--summary", default=None, metavar="시트",
                   help="요약표 시트 이름 (예: total-최종)")
    p.add_argument("--map", dest="map_file", default=None, metavar="매핑.json",
                   help="열 매핑 강제 (요약열·별칭). 형식은 README 참고")
    p.add_argument("--features", default=None, metavar="자질표.csv",
                   help="조음 자질표. 주지 않으면 자질 묶음 집계를 하지 않습니다")
    p.add_argument("--non-response", action="append", default=[], metavar="말",
                   help="전사가 아니라 '반응 없음'으로 볼 응답 표현 추가 "
                        "(기본: 모름·무응답·없음·못들음 등)")
    p.add_argument("--min-count", type=int, default=3, metavar="N",
                   help="혼동 행렬에 남길 최소 관측수 (기본 3)")
    p.add_argument("--alpha", type=float, default=0.05,
                   help="임계차의 유의수준 (기본 0.05 → 95%% 구간)")
    p.add_argument("--min-coverage", type=float, default=80.0, metavar="퍼센트",
                   help="문항 대조율이 이 값 미만이면 판정 불가(exit 3). 기본 80")
    p.add_argument("--out-dir", default=None, metavar="폴더",
                   help="산출물 폴더. 주지 않으면 화면 출력만 합니다")
    p.add_argument("--inspect", action="store_true",
                   help="무엇을 무엇으로 봤는지만 인쇄하고 끝냅니다")
    p.add_argument("--long", action="store_true",
                   help="입력을 문항 단위 long CSV 로 읽습니다(확장자 자동 감지도 됨)")
    for flag, help_text in (
        ("--long-id", "long CSV 의 피험자 ID 열 이름"),
        ("--long-time", "long CSV 의 시점 열 이름"),
        ("--long-test", "long CSV 의 검사 열 이름"),
        ("--long-stim", "long CSV 의 제시 자극 열 이름"),
        ("--long-response", "long CSV 의 응답 열 이름"),
        ("--long-score", "long CSV 의 점수 열 이름"),
    ):
        p.add_argument(flag, default=None, metavar="열이름", help=help_text)
    p.add_argument("--version", action="version",
                   version="jamoscore %s" % __version__)
    return p


# ------------------------------------------------------------------ 유틸
def _refuse(message: str) -> int:
    sys.stderr.write(message.rstrip() + "\n")
    return EXIT_REFUSED


def _echo(lines: Sequence[str]) -> None:
    """--inspect 출력도 리포트와 같은 위생을 거친다.

    사용법.md 가 '먼저 --inspect 를 돌리라'고 하므로, 사용자가 **가장 먼저 보는
    화면**이다. 시트 이름에 심어 둔 개행으로 가짜 `[치명]` 줄을 만들 수 없어야 한다.
    """
    cleaned = [mask_identifiers(sanitize_line(x)) for x in lines]
    sys.stdout.write("\n".join(cleaned) + "\n")


def _load_map(path: Optional[str]) -> dict:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise SpecError("--map 파일을 찾을 수 없습니다: %s" % base_name(path))
    except IsADirectoryError:
        raise SpecError("--map 이 폴더입니다: %s — JSON 파일을 지정하세요."
                        % base_name(path))
    except OSError as exc:
        raise SpecError("--map 파일을 읽을 수 없습니다: %s (%s)"
                        % (base_name(path), exc.strerror or exc))
    except json.JSONDecodeError as exc:
        raise SpecError("--map 파일이 올바른 JSON 이 아닙니다: %s (%s)"
                        % (base_name(path), exc))
    if not isinstance(data, dict):
        raise SpecError("--map 파일의 최상위는 객체여야 합니다: %s" % base_name(path))
    unknown = set(data) - {"요약열"}
    if unknown:
        raise SpecError(
            "--map 에 모르는 키가 있습니다: %s (쓸 수 있는 키: 요약열)\n"
            "  오타라면 조용히 무시되지 않도록 여기서 멈춥니다."
            % ", ".join(sorted(unknown)))
    columns = data.get("요약열")
    if columns is not None:
        if not isinstance(columns, dict):
            raise SpecError("--map 의 '요약열' 은 객체여야 합니다(열이름 → 설정).")
        for name, value in columns.items():
            if not isinstance(value, dict):
                raise SpecError(
                    "--map 의 '요약열'.'%s' 는 객체여야 합니다 — 예: "
                    '{"검사": "일음절", "시점": "사전", "단위": "음소"}' % name)
            bad = set(value) - {"검사", "시점", "단위", "종류"}
            if bad:
                raise SpecError("--map 의 '요약열'.'%s' 에 모르는 키: %s"
                                % (name, ", ".join(sorted(bad))))
            if value.get("단위") not in (None, "단어", "음소"):
                raise SpecError("--map 의 '요약열'.'%s' 단위는 단어·음소 중 하나여야 합니다."
                                % name)
            if value.get("종류") not in (None, "값", "차이"):
                raise SpecError("--map 의 '요약열'.'%s' 종류는 값·차이 중 하나여야 합니다."
                                % name)
    return data


# ------------------------------------------------------------- 입력 읽기
def _read_workbook(args, specs, timepoints):
    """XLSX 에서 passes·roles·workbook 을 얻는다."""
    wb = Workbook(args.input)
    by_name = specs_by_name(specs)
    tests = list(by_name)
    resolved = {}
    for key, (sheet_arg, label) in (
        ("answer_key", (args.answer_key, "--answer-key")),
        ("summary", (args.summary, "--summary")),
    ):
        if not sheet_arg:
            resolved[key] = None
            continue
        match = _resolve_sheet(wb, sheet_arg)
        if match is None:
            raise LayoutError(
                "%s 로 지정한 시트 '%s' 가 워크북에 없습니다.\n  실제 시트: %s"
                % (label, sheet_arg, ", ".join(wb.sheet_names[:20])))
        resolved[key] = match
    roles = classify_sheets(wb, tests, timepoints, resolved["answer_key"],
                            resolved["summary"], args.subject_pattern,
                            pii_extra=args.pii_sheet,
                            response_aliases=args.response_alias,
                            score_aliases=args.score_alias)
    passes: List[Pass] = []
    layout_findings: List[Finding] = []
    missing_pass = defaultdict(list)
    extra_rows = [0]
    for sheet in roles.subjects:
        rows = wb.read_sheet(sheet)
        header_row = find_header_row(rows, tests, timepoints)
        blocks = find_blocks(rows, header_row, tests, timepoints, sheet,
                             extra_response=args.response_alias,
                             extra_score=args.score_alias)
        for block in blocks:
            specs_for_block = by_name[block[1]]
            for p in extract_passes(rows, header_row, block, sheet,
                                    specs_for_block, sheet):
                passes.append(p)
                extra_rows[0] += p.lost_rows
                for kind, note in p.notes:
                    if kind == "결측":
                        missing_pass[(p.test, p.timepoint, p.unit)].append(p.subject)
                        continue
                    layout_findings.append(Finding(
                        CRITICAL, "레이아웃 이상",
                        "%s %s-%s(%s): %s" % (p.subject, p.test, p.timepoint,
                                              p.unit, note),
                        subject=p.subject,
                        location="%s!%s열" % (sheet, column_name(p.col))))
    for key, subjects in sorted(missing_pass.items()):
        layout_findings.append(Finding(
            WARNING, "채점 패스 없음",
            "%s-%s(%s): 선언한 채점 패스가 시트에 없는 피험자 %d명(%s) — 그 채점을 "
            "하지 않은 것으로 보입니다."
            % (key[0], key[1], key[2], len(subjects), "·".join(subjects[:8])),
            subject=subjects[0], location=", ".join(subjects[:8])))
    return wb, roles, passes, layout_findings, extra_rows[0]


def _resolve_sheet(wb: Workbook, name: str):
    """시트 이름을 정규화(NFC)해서 찾는다 — macOS 의 자모 분리형 입력 대응."""
    if wb.has_sheet(name):
        return name
    want = normalize(name)
    for candidate in wb.sheet_names:
        if normalize(candidate) == want:
            return candidate
    return None


def _answer_key_columns(wb: Workbook, sheet: str) -> Dict[str, List[str]]:
    rows = wb.read_sheet(sheet)
    if not rows:
        return {}
    header_row = min(rows)
    header = rows[header_row]
    out: Dict[str, List[str]] = {}
    for col, name in sorted(header.items()):
        clean = normalize(name)
        if not clean:
            continue
        values = []
        for rnum in sorted(rows):
            if rnum <= header_row:
                continue
            values.append(normalize(rows[rnum].get(col, "")))
        while values and not values[-1]:
            values.pop()
        out[clean] = values
    return out


def _group_answer_key(columns: Dict[str, List[str]],
                      tests: Sequence[str]) -> Dict[str, Dict[str, List[str]]]:
    """``{검사: {열이름: [자극...]}}``.

    ``자음1``·``자음2`` 가 각각 어느 시점인지는 **정하지 않는다.** 상쇄균형 설계
    에서는 피험자마다 다르다 — 어느 리스트를 썼는지는 자료가 말하게 둔다.
    """
    grouped: Dict[str, Dict[str, List[str]]] = {}
    for name, values in sorted(columns.items()):
        clean = [v for v in values if v]
        if not clean:
            continue
        for test in sorted(tests, key=len, reverse=True):
            t = normalize(test)
            if t and name.startswith(t):
                grouped.setdefault(test, {})[name] = clean
                break
    return grouped


# -------------------------------------------------------------- inspect
def _inspect(args, specs, timepoints) -> int:
    lines = ["[검사 명세]"]
    for spec in specs:
        lines.append("   %-16s 목표=%-12s 문항=%-6s 만점=%d 단위=%s"
                     % (spec.label, spec.rule.text,
                        spec.n_items if spec.n_items else "(미지정)",
                        spec.max_points, spec.unit))
    lines.append("   시점: %s" % ", ".join(timepoints))
    if _is_csv(args):
        passes, unknown = longcsv.load(
            args.input, specs, timepoints, args.long_id, args.long_time,
            args.long_test, args.long_stim, args.long_response, args.long_score)
        lines.append("")
        lines.append("[입력] long CSV — 피험자 %d명 · 블록 %d개"
                     % (len({p.subject for p in passes}), len(passes)))
        if unknown:
            lines.append("   --subtest 에 없는 검사 이름 %d종은 읽지 않았습니다: %s"
                         % (len(unknown), ", ".join(unknown[:8])))
        _echo(lines + _pass_table(passes))
        return EXIT_OK

    wb, roles, passes, layout_findings, _ = _read_workbook(
        args, specs, timepoints)
    lines.append("")
    lines.append("[입력] 시트 %d개 → 피험자 시트 %d개 인식" % (roles.total,
                                                          len(roles.subjects)))
    lines.append("   피험자 시트: %s%s"
                 % (", ".join(roles.subjects[:12]),
                    " …(+%d)" % (len(roles.subjects) - 12)
                    if len(roles.subjects) > 12 else ""))
    if roles.answer_key:
        lines.append("   정답 시트: %s" % roles.answer_key)
    if roles.summary:
        lines.append("   요약 시트: %s" % roles.summary)
    for name in roles.pii:
        lines.append("   식별정보로 보여 **열지 않은** 시트: %s" % name)
    for name in roles.pii_by_header:
        lines.append("   열 이름이 식별정보라 읽자마자 버린 시트: %s" % name)
    for name, why in roles.incomplete:
        lines.append("   블록 불완전: %s — %s" % (name, why))
    if roles.skipped:
        lines.append("   건너뛴 시트 %d개: %s"
                     % (len(roles.skipped),
                        ", ".join(n for n, _ in roles.skipped[:10])))
    lines.append("")
    _echo(lines + _pass_table(passes) + _layout_notes(layout_findings))
    return EXIT_OK


def _pass_table(passes: Sequence[Pass]) -> List[str]:
    lines = ["[블록별 문항수·분모]"]
    seen: Dict[tuple, List[Pass]] = defaultdict(list)
    for p in passes:
        seen[(p.test, p.timepoint, p.unit)].append(p)
    if not seen:
        lines.append("   (블록을 하나도 찾지 못했습니다)")
        return lines
    for key in sorted(seen):
        group = seen[key]
        counts = Counter(len(p.items) for p in group)
        denoms = Counter(p.denominator for p in group)
        lines.append(
            "   %-8s %-4s %-4s  피험자 %2d명 · 문항수 %s · 분모 %s"
            % (key[0], key[1], key[2], len(group),
               " / ".join("%d(%d명)" % (n, c) for n, c in sorted(counts.items())),
               " / ".join("%d(%d명)" % (n, c) for n, c in sorted(denoms.items()))))
    reported = sum(1 for p in passes if p.reported is not None)
    lines.append("   시트가 스스로 적어 둔 요약 %% 셀: %d개" % reported)
    return lines


def _layout_notes(findings: Sequence[Finding]) -> List[str]:
    if not findings:
        return []
    lines = ["", "[레이아웃 경고] %d건 — 그대로 실행하면 소견으로 보고됩니다"
             % len(findings)]
    for f in findings[:10]:
        lines.append("   " + f.message)
    if len(findings) > 10:
        lines.append("   …(+%d건)" % (len(findings) - 10))
    return lines


def _is_csv(args) -> bool:
    return args.long or os.path.splitext(args.input)[1].lower() in (
        ".csv", ".tsv", ".txt")


# ----------------------------------------------------------------- main
def main(argv: Optional[Sequence[str]] = None) -> int:
    # `jamoscore ... | head` 에서 BrokenPipe 로 종료코드가 뒤집히지 않게.
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        return _refuse("입력 파일을 찾을 수 없습니다: %s" % base_name(args.input))
    if os.path.isdir(args.input):
        return _refuse("입력이 폴더입니다: %s — 채점 엑셀 파일 하나를 지정하세요."
                       % base_name(args.input))
    if not os.path.isfile(args.input):
        # 이름 있는 파이프(FIFO)·장치 파일은 읽기에서 영원히 멈춘다.
        return _refuse("입력이 보통 파일이 아닙니다: %s — 파일 하나를 지정하세요."
                       % base_name(args.input))

    timepoints = [t.strip() for t in args.timepoints.split(",") if t.strip()]
    if len(timepoints) < 1:
        return _refuse("--timepoints 가 비었습니다.")
    if len(set(timepoints)) != len(timepoints):
        return _refuse("--timepoints 에 같은 이름이 두 번 있습니다: %s" % args.timepoints)
    if len(timepoints) > 2:
        return _refuse(
            "--timepoints 는 지금 두 개까지만 지원합니다(%d개 받음: %s).\n"
            "  세 시점 이상은 '차이' 열의 뜻과 임계차의 기준 시점이 자료마다 달라져, "
            "추측하면 조용히 틀린 값을 냅니다.\n"
            "  두 시점씩 나누어 돌리거나(예: --timepoints 사전,사후), 시점 간 추이는 "
            "longistat 를 쓰세요." % (len(timepoints), ", ".join(timepoints)))

    if args.subject_pattern is not None:
        if not args.subject_pattern.strip():
            return _refuse("--subject-pattern 이 비었습니다.")
        try:
            re.compile(args.subject_pattern)
        except re.error as exc:
            return _refuse("--subject-pattern 이 올바른 정규식이 아닙니다: %s\n  (%s)"
                           % (args.subject_pattern, exc))
    if args.out_dir is not None and not str(args.out_dir).strip():
        return _refuse("--out-dir 이 비었습니다. 폴더 이름을 주거나 옵션을 빼세요.")
    if args.answer_key and args.summary and args.answer_key == args.summary:
        return _refuse("--answer-key 와 --summary 에 같은 시트를 줄 수 없습니다: %s"
                       % args.answer_key)
    if not args.subtest:
        return _refuse(
            "--subtest 가 없습니다. 이 툴은 채점 규칙을 추론하지 않습니다 — "
            "추론하면 조용히 거짓말을 하게 됩니다.\n"
            "  예) --subtest \"자음:목표=2음절초성,문항=18\"\n"
            "  먼저 --inspect 로 파일 구조를 확인하세요.")
    try:
        specs = parse_all(args.subtest)
        mapping = _load_map(args.map_file)
    except SpecError as exc:
        return _refuse(str(exc))

    for flag, values in (("--pii-sheet", args.pii_sheet),
                         ("--response-alias", args.response_alias),
                         ("--score-alias", args.score_alias),
                         ("--non-response", args.non_response)):
        for value in values:
            if not str(value).strip():
                return _refuse(
                    "%s 에 빈 값을 줄 수 없습니다 — 빈 값은 모든 열·시트·응답에 "
                    "걸려 조용히 엉뚱한 열을 읽게 됩니다." % flag)
    if args.min_count < 1:
        return _refuse("--min-count 는 1 이상이어야 합니다.")
    if not (0.0 < args.alpha < 1.0):
        return _refuse("--alpha 는 0과 1 사이여야 합니다.")
    if not (0.0 <= args.min_coverage <= 100.0):
        return _refuse("--min-coverage 는 0~100 이어야 합니다.")

    feature_table, feature_names = None, []
    if args.features:
        try:
            feature_table, feature_names = features_mod.load(args.features)
        except (features_mod.FeatureError, OSError) as exc:
            return _refuse("자질표를 읽지 못했습니다: %s" % exc)

    try:
        if args.inspect:
            return _inspect(args, specs, timepoints)
        return _run(args, specs, timepoints, mapping, feature_table, feature_names)
    except (LayoutError, XlsxError) as exc:
        return _refuse(str(exc))
    except OutputError as exc:
        return _refuse(str(exc))
    except PermissionError as exc:
        return _refuse("파일 권한이 없습니다: %s" % base_name(str(getattr(exc, "filename", args.input))))
    except KeyboardInterrupt:                                # pragma: no cover
        sys.stderr.write("\n중단했습니다.\n")
        return EXIT_REFUSED
    except RecursionError:                                   # pragma: no cover
        return _refuse("입력이 너무 깊게 중첩되어 있습니다 — 처리하지 않았습니다.")
    except Exception as exc:
        # 예상 못 한 예외가 트레이스백으로 새어 나가면 종료코드가 1 이 되어
        # "치명 1건"과 구별되지 않는다. 판정하지 않았다는 사실이 더 중요하다.
        return _undetermined_crash(exc)


def _undetermined_crash(exc: BaseException) -> int:
    sys.stderr.write(
        "판정 불가 — 처리 중 예상하지 못한 오류가 났습니다: %s: %s\n"
        "  이 실행 결과는 믿을 수 없습니다. --inspect 로 구조를 먼저 확인하고, "
        "그래도 같은 오류가 나면 입력 파일 구조를 알려 주세요.\n"
        % (type(exc).__name__,
           strip_paths(sanitize_line(str(exc)))[:300]))
    return EXIT_UNDETERMINED


def _run(args, specs, timepoints, mapping, feature_table, feature_names) -> int:
    by_name = specs_by_name(specs)
    tests = list(by_name)
    header_lines: List[str] = []
    findings: List[Finding] = []
    cov = report.Coverage()
    cov.min_count = args.min_count
    wb = None
    roles = None
    unreadable = 0

    if _is_csv(args):
        passes, unknown_tests = longcsv.load(
            args.input, specs, timepoints, args.long_id, args.long_time,
            args.long_test, args.long_stim, args.long_response, args.long_score)
        header_lines.append(
            "[입력] long CSV '%s' — 피험자 %d명 · 블록 %d개"
            % (base_name(args.input), len({p.subject for p in passes}), len(passes)))
        cov.sheets_total = 1
        cov.sheets_subject = 1
        if unknown_tests:
            cov.notes.append(
                "--subtest 에 선언되지 않아 읽지 않은 검사 이름 %d종: %s"
                % (len(unknown_tests), ", ".join(unknown_tests[:8])))
    else:
        wb, roles, passes, layout_findings, extra_rows = _read_workbook(
            args, specs, timepoints)
        findings.extend(layout_findings)
        if extra_rows:
            cov.skip_reasons["블록이 끊겨 읽지 못한 행"] = extra_rows
        unreadable = len(roles.unreadable)
        header_lines.append(
            "[입력] 시트 %d개 → 피험자 시트 %d개 인식%s%s"
            % (roles.total, len(roles.subjects),
               " · 정답 시트 1개" if roles.answer_key else "",
               " · 요약 시트 1개" if roles.summary else ""))
        for name in roles.pii:
            header_lines.append(
                "[경고] `%s` 시트는 식별정보로 보여 **열지 않았습니다**. "
                "내보내기 전 deidaudit 을 돌리세요." % name)
        for name in roles.pii_by_header:
            header_lines.append(
                "[경고] `%s` 시트는 열 이름이 이름·연락처·생년월일이라 읽자마자 "
                "버렸습니다. 내보내기 전 deidaudit 을 돌리세요." % name)
        for name, why in roles.incomplete:
            findings.append(Finding(
                WARNING, "블록 헤더 불완전",
                "시트 '%s' 는 검사 이름이 보이지만 응답·점수 열을 찾지 못해 "
                "대조에서 뺐습니다. 피험자 시트라면 헤더를 고쳐 주세요." % name,
                location=name, evidence=why))
        for name, why in roles.unreadable:
            findings.append(Finding(
                CRITICAL, "시트를 읽지 못함",
                "시트 '%s' 를 읽지 못했습니다: %s" % (name, why), location=name))
        cov.sheets_total = roles.total
        cov.sheets_subject = len(roles.subjects)
        cov.sheets_pii_unopened = list(roles.pii)
        cov.sheets_pii_dropped = list(roles.pii_by_header)
        cov.sheets_skipped = list(roles.skipped)
        cov.sheets_incomplete = list(roles.incomplete)
        cov.sheets_unreadable = list(roles.unreadable)
        cov.answer_key = roles.answer_key
        cov.summary_sheet = roles.summary

    if not passes:
        return _refuse(
            "문항 단위 블록을 하나도 찾지 못했습니다.\n"
            "  --inspect 로 어떤 시트·열이 보이는지 먼저 확인하세요.\n"
            "  이 파일이 요약 %만 담고 있다면 jamoscore 는 할 말이 없습니다 — "
            "군/시점 비교는 statwise · longistat 를 쓰세요.")

    # ---- 채점 ----
    analyze.score_items(passes)
    cov.items_total = sum(len(p.items) for p in passes)

    findings.extend(analyze.duplicate_blocks(passes))
    blank_findings, blank_keys = analyze.blank_passes(passes)
    findings.extend(blank_findings)
    findings.extend(analyze.check_score_cells(passes))
    findings.extend(analyze.check_response_cells(passes, args.non_response))
    rule_findings, rule_rows, rule_stats = analyze.compare_rule(
        passes, args.non_response)
    findings.extend(rule_findings)
    pair_findings, pair_rows = analyze.inconsistent_pairs(passes)
    findings.extend(pair_findings)
    findings.extend(analyze.cross_pass_transcripts(passes))
    sheet_findings, sheet_compared, sheet_mismatched = \
        analyze.check_inhsheet_summary(passes)
    findings.extend(sheet_findings)
    findings.extend(analyze.impossible_percent(passes))
    findings.extend(analyze.floor_ceiling(passes))


    cov.items_total += sum(cov.skip_reasons.get(k, 0)
                           for k in ("블록이 끊겨 읽지 못한 행",))
    cov.items_compared = rule_stats.get("비교", 0)
    for key, value in rule_stats.items():
        if key.startswith("사유:"):
            cov.skip_reasons[key[3:]] = value
    unscored = sum(1 for p in passes for i in p.items if i.human is None)
    if unscored:
        cov.skip_reasons["점수 칸을 숫자로 읽지 못함"] = unscored

    # ---- 정답 시트 ----
    assignment = None
    if wb is not None and roles is not None and roles.answer_key:
        raw_columns = _answer_key_columns(wb, roles.answer_key)
        key_lists = _group_answer_key(raw_columns, tests)
        key_findings, unmatched, assignment = analyze.check_answer_key(
            passes, key_lists, roles.answer_key)
        findings.extend(key_findings)
        cov.answer_key_unmatched = unmatched
    findings.extend(analyze.check_same_list_across_time(
        passes, timepoints, assignment))

    # ---- 요약표 ----
    summary_rows = None
    summary_stats = None
    if wb is not None and roles is not None and roles.summary:
        summary_rows = wb.read_sheet(roles.summary)
        overrides = mapping.get("요약열") or {}
        bad_tests = sorted({v.get("검사") for v in overrides.values()
                            if v.get("검사") and v.get("검사") not in tests})
        if bad_tests:
            return _refuse(
                "--map 의 '요약열' 이 --subtest 에 없는 검사를 가리킵니다: %s\n"
                "  선언된 검사: %s" % (", ".join(bad_tests), ", ".join(tests)))
        header_names = {normalize(v) for v in
                        summary_rows.get(min(summary_rows), {}).values()}
        missing_cols = sorted(set(overrides) - header_names)
        if missing_cols:
            return _refuse(
                "--map 의 '요약열' 에 요약 시트에 없는 열이 있습니다: %s\n"
                "  실제 열: %s" % (", ".join(missing_cols),
                                 ", ".join(sorted(header_names))[:300]))
        sum_findings, sum_stats, unmapped = summary.compare(
            summary_rows, roles.summary, passes, tests, timepoints,
            roles.subjects, overrides)
        findings.extend(sum_findings)
        cov.summary_cells_compared = sum_stats["대조"]
        cov.summary_cells_total = (sum_stats["대조"] + sum_stats["값없음"]
                                   + sum_stats["재계산불가"])
        cov.summary_unmapped = unmapped
        summary_stats = sum_stats
        errors = _count_excel_errors(summary_rows)
        if errors:
            findings.append(Finding(
                WARNING, "요약 시트에 엑셀 오류값",
                "요약 시트 '%s' 에 #DIV/0! 같은 엑셀 오류값이 %d칸 있습니다 — "
                "그 칸은 대조하지 못했습니다." % (roles.summary, errors),
                location=roles.summary))
    cov.notes.append("시트 안에 적혀 있던 요약 %% 셀 %d개도 문항에서 다시 계산해 "
                     "대조했습니다." % sheet_compared)
    dropped_summary = sum(len(p.dropped_summary) for p in passes)
    if dropped_summary:
        cov.notes.append(
            "요약 %%로 보이지만 정수로 반올림돼 확신할 수 없어 대조하지 않은 셀 "
            "%d개(예: %s)." % (dropped_summary,
                              next(x for p in passes if p.dropped_summary
                                   for x in p.dropped_summary)))

    # ---- 혼동 행렬 ----
    raw_conf, gated, dropped, conf_skipped = analyze.confusion(
        passes, args.min_count, args.non_response)
    cov.confusion_dropped = dropped
    if conf_skipped:
        cov.notes.append(
            "음절 수가 제시 자극과 다른 응답 %d건은 혼동 행렬에서 뺐습니다"
            "(자리별 대조가 성립하지 않습니다)." % conf_skipped)
    top = analyze.top_confusions(gated)
    confusion_lines = report.render_confusion(
        top, args.min_count, bool(feature_table), analysed=bool(raw_conf))

    # ---- 임계차 ----
    crit_rows, crit_findings = analyze.critical_differences(
        passes, timepoints, args.alpha)
    findings.extend(crit_findings)

    # ---- 무엇이 '맞았는지'도 말한다 ----
    # 성공을 침묵으로만 알리면, 바로 아래의 "안 나온 항목이 이상 없음이라는 뜻이
    # 아닙니다" 와 정면으로 부딪친다.
    result_lines = []
    if summary_stats is not None:
        result_lines.append(
            "[결과] 요약표 대조: %d셀 중 %d셀 일치 (불일치 %d)"
            % (summary_stats["대조"], summary_stats["대조"] - summary_stats["불일치"],
               summary_stats["불일치"]))
    if sheet_compared:
        result_lines.append(
            "       시트 내부 요약 %d셀 중 %d셀 일치"
            % (sheet_compared, sheet_compared - sheet_mismatched))
    if cov.items_compared:
        rule_bad = len(rule_rows)
        result_lines.append(
            "       규칙 대조: %d문항 중 %d문항 일치 (갈림 %d · %.1f%%)"
            % (cov.items_compared, cov.items_compared - rule_bad, rule_bad,
               100.0 * rule_bad / cov.items_compared))
    if result_lines:
        header_lines.extend([""] + result_lines)

    # ---- 판정 ----
    truncated = sum((wb.truncated if wb is not None else {}).values())
    if truncated:
        findings.append(Finding(
            CRITICAL, "시트를 끝까지 읽지 못함",
            "행 번호가 상한(%d)을 넘어 읽지 못한 행이 %d개 있습니다 — 이 파일은 "
            "끝까지 읽히지 않았습니다." % (MAX_ROWS, truncated),
            location=", ".join(sorted(wb.truncated))))
    if wb is not None and wb.duplicate_sheets:
        findings.append(Finding(
            WARNING, "이름이 겹치는 시트",
            "같은 이름의 시트가 둘 이상 있어 두 번째 이후는 읽지 못했습니다: %s"
            % ", ".join(wb.duplicate_sheets),
            location=", ".join(wb.duplicate_sheets)))
    counts = count_by_severity(findings)   # 모든 소견이 추가된 **뒤에** 센다
    undetermined = (unreadable > 0 or truncated > 0
                    or cov.items_total == 0
                    or cov.item_rate < args.min_coverage)
    if cov.items_total == 0:
        cov.notes.append("문항을 하나도 읽지 못해 판정하지 않았습니다.")
    elif cov.item_rate < args.min_coverage:
        cov.notes.append(
            "문항 대조율 %.1f%% 가 --min-coverage %.1f%% 미만이라 판정하지 "
            "않았습니다." % (cov.item_rate, args.min_coverage))
    verdict_line, exit_code = report.verdict(counts, undetermined)
    text = report.build(header_lines, findings, confusion_lines, cov,
                        verdict_line, exit_code)
    sys.stdout.write(text)

    if args.out_dir:
        out_dir = ensure_out_dir(args.out_dir)
        # 하위 폴더까지 먼저 확보한다 — 파일을 몇 개 쓴 뒤에 거절하면 산출물
        # 폴더에 두 실행의 결과가 섞인 채 남는다.
        ensure_subdir(out_dir, "statwise_longistat_입력")
        _write_artifacts(out_dir, text, findings, passes, rule_rows, pair_rows,
                         gated, crit_rows, specs, args, feature_table,
                         feature_names, len({p.subject for p in passes}),
                         blank_keys)
        sys.stdout.write("\n산출물: %s/ 에 %d개 파일을 썼습니다.\n"
                         % (base_name(out_dir), _ARTIFACT_COUNT[0]))
    return exit_code


_ARTIFACT_COUNT = [0]


def _write_artifacts(out_dir, text, findings, passes, rule_rows, pair_rows,
                     gated, crit_rows, specs, args, feature_table,
                     feature_names, n_subjects, blank_keys=()) -> None:
    written = 0

    def path(name):
        return os.path.join(out_dir, name)

    write_text(path("채점검증.md"), "```\n" + text.replace("```", "'''") + "```\n")
    written += 1

    write_csv(path("문제목록.csv"), CSV_HEADER, [f.as_row() for f in findings])
    written += 1

    score_header = ["ID", "시점", "검사", "하위블록", "정답수", "문항수", "분모",
                    "채점된문항수", "단어%", "음소%", "비고"]
    score_rows = []
    index = {(p.subject, p.test, p.timepoint, p.unit): p for p in passes}
    triples = sorted({(k[0], k[2], k[1]) for k in index})
    for subject, timepoint, test in triples:
        word = index.get((subject, test, timepoint, "단어"))
        phon = index.get((subject, test, timepoint, "음소"))
        units = [u for u, p in (("단어", word), ("음소", phon)) if p and p.items]
        basis = word if (word and word.items) else phon
        if basis is None or not basis.items:
            continue
        note = "응답 공란·0점" if basis.key in blank_keys else ""
        score_rows.append([
            subject, timepoint, test, "+".join(units) or basis.unit,
            _num(basis.earned), len(basis.items), basis.denominator,
            len(basis.scored_items()),
            "" if not word or word.percent is None else "%.1f" % word.percent,
            "" if not phon or phon.percent is None else "%.1f" % phon.percent,
            note,
        ])
    write_csv(path("문항점수.csv"), score_header, score_rows)
    written += 1

    # statwise·longistat 에 바로 넣을 수 있는 검사별 long 파일
    ready_dir = ensure_subdir(out_dir, "statwise_longistat_입력")
    groups = defaultdict(list)
    for key in sorted(index):
        p = index[key]
        if p.percent is None or p.key in blank_keys:
            # 응답이 전부 비어 0점인 블록은 넘기지 않는다 — 하류 툴이 그 0 을
            # 실제 수행으로 분석에 넣는다.
            continue
        groups[(p.test, p.unit)].append(p)
    for (test, unit), plist in sorted(groups.items()):
        name = "%s_%s.csv" % (_slug(test), _slug(unit))
        write_csv(os.path.join(ready_dir, name), ["ID", "시점", "점수"],
                  [[p.subject, p.timepoint, "%.1f" % p.percent] for p in plist])
        written += 1

    write_csv(path("규칙대조.csv"),
              ["피험자", "검사", "시점", "단위", "문항", "위치", "제시", "응답",
               "사람", "규칙", "규칙근거"],
              [[r["피험자"], r["검사"], r["시점"], r["단위"], r["문항"], r["위치"],
                r["제시"], r["응답"], r["사람"], r["규칙"], r["규칙근거"]]
               for r in rule_rows])
    written += 1

    write_csv(path("동일쌍_다른채점.csv"),
              ["검사", "단위", "제시", "응답", "피험자", "시점", "위치", "점수"],
              [[r["검사"], r["단위"], r["제시"], r["응답"], r["피험자"],
                r["시점"], r["위치"], r["점수"]] for r in pair_rows])
    written += 1

    feature_files: List[str] = []
    for pos in ("초성", "중성", "종성"):
        counter = gated.get(pos)
        if not counter:
            continue
        targets = sorted({a for a, _ in counter})
        responses = sorted({b for _, b in counter})
        rows = []
        for a in targets:
            rows.append([a] + [counter.get((a, b), 0) for b in responses])
        write_csv(path("혼동행렬_%s.csv" % pos), ["목표\\응답"] + responses, rows)
        written += 1
        if feature_table:
            for fname in feature_names:
                grouped, unclassified = features_mod.group(counter, feature_table,
                                                           fname)
                if not grouped:
                    # 이 자모 위치에 분류 가능한 것이 하나도 없다(자음 자질표로
                    # 중성을 묶으려는 경우). 빈 표를 만들어 내보내지 않는다.
                    continue
                gt = sorted({a for a, _ in grouped})
                gr = sorted({b for _, b in grouped})
                body = [[a] + [grouped.get((a, b), 0) for b in gr] for a in gt]
                # 미분류는 표 안의 한 칸이 아니라 **표 전체에 대한 값**이다.
                # 첫 행에 끼워 넣으면 그 행의 합이 틀린 표가 된다.
                body.append(["(미분류 합계)", unclassified]
                            + [""] * max(0, len(gr) - 1))
                name = "혼동행렬_%s_%s.csv" % (pos, _slug(fname))
                if name in feature_files:
                    continue      # 자질 이름이 겹쳐 같은 파일을 두 번 쓰지 않는다
                feature_files.append(name)
                write_csv(path(name), ["목표\\응답"] + gr, body)
                written += 1

    write_csv(path("임계차.csv"),
              ["피험자", "검사", "단위", "문항수", "사전정답", "사전%", "사후정답",
               "사후%", "변화%p", "오차범위정답수", "95%임계차%p(하락/상승)", "판정"],
              [[r["피험자"], r["검사"], r["단위"], r["문항수"], r["사전정답"],
                r["사전%"], r["사후정답"], r["사후%"], r["변화%p"],
                r["오차범위정답수"], r["95%임계차%p(하락/상승)"], r["판정"]]
               for r in crit_rows])
    written += 1

    denominators = {}
    for p in passes:
        denominators.setdefault(p.spec.label, p.denominator)
    if feature_files:
        write_text(path("자질묶음_안내.txt"),
                   "자질 묶음 혼동 행렬 %d개를 만들었습니다:\n  %s\n"
                   "분류하지 못한 관측은 각 표의 '(미분류 합계)' 행에 있습니다.\n"
                   % (len(feature_files), "\n  ".join(feature_files)))
        written += 1
    write_text(path("Methods_초안.md"),
               methods.build(specs, denominators, args.alpha, args.min_count,
                             bool(feature_table), n_subjects, __version__))
    written += 1
    _ARTIFACT_COUNT[0] = written


#: 엑셀이 셀에 남기는 오류 문자열.
_EXCEL_ERRORS = ("#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A",
                 "#NULL!", "#SPILL!")


def _count_excel_errors(rows) -> int:
    return sum(1 for row in rows.values() for value in row.values()
               if normalize(value) in _EXCEL_ERRORS)


def _num(value) -> str:
    f = float(value)
    return str(int(f)) if f == int(f) else ("%.1f" % f)


def _slug(text: str) -> str:
    keep = []
    for ch in normalize(text):
        keep.append(ch if (ch.isalnum() or ch in "-_가-힣") else "_")
    return "".join(keep)[:40] or "x"


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
