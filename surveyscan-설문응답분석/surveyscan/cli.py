"""surveyscan 명령줄 인터페이스.

사용 예:
  surveyscan responses.csv --config scale.json
  surveyscan responses.csv --id-col ID --json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .analyze import analyze
from .config import ConfigError, auto_config, load_config
from .dataio import DataError, load_csv
from .report import render


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
    p.add_argument("--delimiter", default=",", help="CSV 구분자 (기본: 콤마)")
    p.add_argument("--json", action="store_true", help="텍스트 대신 JSON으로 결과 출력")
    p.add_argument("-o", "--output", help="결과를 파일로 저장(미지정 시 표준출력)")
    p.add_argument("--version", action="version", version=f"surveyscan {__version__}")
    return p


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        data = load_csv(
            args.csv,
            id_columns=args.id_col,
            na_numbers=args.na_number,
            delimiter=args.delimiter,
        )
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
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
            cfg = load_config(args.config)
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

    try:
        result = analyze(data, cfg)
    except ValueError as e:
        print(f"분석 오류: {e}", file=sys.stderr)
        return 2

    if args.json:
        # scores 리스트는 길어서 JSON 출력에선 생략(요약 통계만 남김).
        slim = json.loads(json.dumps(result))
        for s in slim["subscales"]:
            s.pop("scores", None)
        text = json.dumps(slim, ensure_ascii=False, indent=2)
    else:
        text = render(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"저장됨: {args.output}")
    else:
        print(text)
    return 0


def main() -> None:  # console-script 진입점
    raise SystemExit(run())


if __name__ == "__main__":
    main()
