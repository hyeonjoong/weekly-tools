"""surveyscan 명령줄 인터페이스.

사용 예:
  surveyscan responses.csv --config scale.json
  surveyscan responses.csv --id-col ID --json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Dict, List, Optional

from . import __version__
from .analyze import analyze
from .config import SCORE_METHODS, ConfigError, auto_config, load_config
from .dataio import DataError, SurveyData, load_csv
from .report import render, render_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="surveyscan",
        description="설문 응답 CSV 분석: 문항 기술통계 · Cronbach α · 하위척도 점수 · 역문항 처리 · 결측 요약",
    )
    p.add_argument("csv", help="설문 응답 CSV 경로 (행=응답자, 열=문항)")
    p.add_argument(
        "-c", "--config", help="하위척도/역문항 설정 JSON 경로 (없으면 숫자 컬럼 전체를 한 척도로 분석)"
    )
    p.add_argument(
        "--id-col",
        action="append",
        default=[],
        metavar="이름",
        help="분석에서 제외할 ID 컬럼(여러 번 지정 가능)",
    )
    p.add_argument(
        "--na-number",
        action="append",
        default=[],
        type=float,
        metavar="값",
        help="결측 코드로 쓰인 숫자(예: --na-number 999). 여러 번 지정 가능",
    )
    p.add_argument("--delimiter", default=",", help="CSV 구분자 (기본: 콤마). 'tab'/'\\t' 는 탭")
    p.add_argument(
        "--score-method",
        choices=SCORE_METHODS,
        default=None,
        help="하위척도 점수 산출: mean(가용문항 평균) 또는 sum(비례배분 총합). "
        "config 값을 덮어씀. ISI·PHQ-9 등은 보통 sum",
    )
    p.add_argument(
        "--ci-level",
        type=float,
        default=0.95,
        metavar="0.95",
        help="신뢰구간 신뢰수준 (0<값<1, 기본 0.95)",
    )
    p.add_argument(
        "--format",
        choices=["text", "json", "md", "markdown"],
        default=None,
        help="출력 형식: text(기본) / json / md",
    )
    p.add_argument(
        "--item-freq",
        action="store_true",
        help="문항별 응답 선택지 빈도표를 추가 출력(척도 범위가 정수일 때)",
    )
    p.add_argument("--json", action="store_true", help="--format json 과 동일(하위호환)")
    p.add_argument(
        "--scores-out",
        metavar="파일.csv",
        help="응답자별 하위척도 점수를 CSV로 저장(원자료에 다시 붙여 분석용)",
    )
    p.add_argument("-o", "--output", help="결과를 파일로 저장(미지정 시 표준출력)")
    p.add_argument("--version", action="version", version=f"surveyscan {__version__}")
    return p


def _write_scores_csv(path: str, data: SurveyData, result: Dict[str, object]) -> None:
    """응답자별 하위척도 점수를 CSV로 저장. ID 컬럼이 있으면 앞에 붙인다.

    수식 인젝션 방지: '=', '+', '-', '@' 등으로 시작하는 ID 값은 앞에 작은따옴표를 붙여
    스프레드시트에서 수식으로 실행되지 않게 한다.
    """
    id_cols = data.id_columns
    subs = result["subscales"]
    header = [_csv_safe(c) for c in id_cols] if id_cols else ["행"]
    header += [_csv_safe(str(s["name"])) for s in subs]
    n = data.n_respondents
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in range(n):
            if id_cols:
                row = [_csv_safe(data.id_values[r].get(c, "")) for c in id_cols]
            else:
                row = [r + 1]
            for s in subs:
                val = s["scores"][r]
                row.append("" if val is None else f"{val:.6g}")
            w.writerow(row)


def _csv_safe(s: str) -> str:
    """스프레드시트 수식 인젝션 방지용 프리픽스 처리."""
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not (0.0 < args.ci_level < 1.0):
        print("오류: --ci-level 은 0과 1 사이여야 합니다.", file=sys.stderr)
        return 2

    delimiter = "\t" if args.delimiter in ("tab", "\\t", "\t") else args.delimiter
    if len(delimiter) != 1:
        print("오류: --delimiter 는 한 글자여야 합니다(탭은 'tab').", file=sys.stderr)
        return 2

    try:
        data = load_csv(
            args.csv,
            id_columns=args.id_col,
            na_numbers=args.na_number,
            delimiter=delimiter,
        )
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print(f"오류: 폴더가 아니라 CSV 파일 경로를 지정하세요: {args.csv}", file=sys.stderr)
        return 2
    except PermissionError:
        print(f"오류: 파일을 읽을 권한이 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        print(
            "데이터 오류: CSV를 UTF-8로 읽을 수 없습니다. "
            "엑셀이면 'CSV UTF-8'로 다시 저장하세요.",
            file=sys.stderr,
        )
        return 2
    except DataError as e:
        print(f"데이터 오류: {e}", file=sys.stderr)
        return 2

    # ID 컬럼 오타 경고(P4): 지정했지만 헤더에 없던 이름
    if data.unknown_id_columns:
        print(
            "경고: --id-col 로 지정한 컬럼이 헤더에 없습니다(오타?): "
            + ", ".join(data.unknown_id_columns),
            file=sys.stderr,
        )

    try:
        if args.config:
            try:
                cfg = load_config(args.config)
            except IsADirectoryError:
                print(f"오류: 폴더가 아니라 config JSON 파일을 지정하세요: {args.config}", file=sys.stderr)
                return 2
            except PermissionError:
                print(f"오류: config 파일을 읽을 권한이 없습니다: {args.config}", file=sys.stderr)
                return 2
            except UnicodeDecodeError:
                print(
                    "설정 오류: config JSON을 UTF-8로 읽을 수 없습니다. UTF-8로 저장하세요.",
                    file=sys.stderr,
                )
                return 2
        else:
            # 자동설정: 숫자 컬럼만 사용. 제외되는 컬럼(전부 결측/텍스트)을 알린다(P3).
            dropped = data.nonnumeric_columns()
            if dropped:
                print(
                    "참고: 숫자값이 없어 분석에서 제외된 컬럼: "
                    + ", ".join(dropped)
                    + " (의도와 다르면 --config 로 명시하세요)",
                    file=sys.stderr,
                )
            cfg = auto_config(data.numeric_columns())
    except FileNotFoundError:
        print(f"오류: config 파일을 찾을 수 없습니다: {args.config}", file=sys.stderr)
        return 2
    except (ConfigError, json.JSONDecodeError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    # 점수 방식 CLI 덮어쓰기(config 값보다 우선).
    if args.score_method is not None:
        cfg.score_method = args.score_method

    try:
        result = analyze(data, cfg, conf=args.ci_level, item_freq=args.item_freq)
    except ValueError as e:
        print(f"분석 오류: {e}", file=sys.stderr)
        return 2
    except OverflowError:
        print(
            "분석 오류: 값이 너무 커서 계산할 수 없습니다(입력에 비정상적으로 큰 수가 있는지 "
            "확인하세요; 척도 범위를 설정하면 범위 이탈로 표시됩니다).",
            file=sys.stderr,
        )
        return 2

    # 점수 CSV 내보내기(선택).
    if args.scores_out:
        try:
            _write_scores_csv(args.scores_out, data, result)
        except OSError as e:
            print(f"오류: 점수 CSV를 저장할 수 없습니다: {e}", file=sys.stderr)
            return 2
        print(f"점수 저장됨: {args.scores_out}")

    fmt = args.format
    if fmt is None:
        fmt = "json" if args.json else "text"
    elif fmt == "markdown":
        fmt = "md"

    if fmt == "json":
        # scores 리스트는 길어서 JSON 출력에선 생략(요약 통계만 남김).
        slim = {k: v for k, v in result.items()}
        slim["subscales"] = [
            {k: v for k, v in s.items() if k != "scores"} for s in result["subscales"]
        ]
        # allow_nan=False: 비유한값이 있으면 조용히 깨진 JSON을 내보내지 않고 막는다.
        try:
            text = json.dumps(slim, ensure_ascii=False, indent=2, allow_nan=False)
        except ValueError:
            print(
                "분석 오류: 결과에 유한하지 않은 값이 있어 JSON으로 출력할 수 없습니다.",
                file=sys.stderr,
            )
            return 2
    elif fmt == "md":
        text = render_markdown(result)
    else:
        text = render(result)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError as e:
            print(f"오류: 결과를 저장할 수 없습니다: {e}", file=sys.stderr)
            return 2
        print(f"저장됨: {args.output}")
    else:
        print(text)
    return 0


def main() -> None:  # console-script 진입점
    raise SystemExit(run())


if __name__ == "__main__":
    main()
