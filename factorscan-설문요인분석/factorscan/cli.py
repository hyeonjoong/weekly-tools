"""factorscan 명령줄 인터페이스.

예:
  factorscan responses.csv --id-col ID
  factorscan responses.csv --config scale.json --n-factors 2
  factorscan responses.csv --items Q1,Q2,Q3,Q4 --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import List, Optional

from . import __version__
from .analyze import analyze
from .dataio import (DataError, Dataset, apply_reverse, listwise, load_csv,
                     reverse_range_violations, select_items)
from .report import render


def _split_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="factorscan",
        description="설문 척도 요인분석·타당도 진단: KMO · Bartlett · 고유값/평행분석 · "
                    "요인적재량(Varimax) · 공통성 · 문항-총점 상관",
    )
    p.add_argument("csv", help="설문 응답 CSV 경로 (행=응답자, 열=문항)")
    p.add_argument("-c", "--config", help="설정 JSON (items/reverse/scale_range/id_cols)")
    p.add_argument("--items", help="분석할 문항 열, 쉼표구분 (미지정 시 숫자열 자동선택)")
    p.add_argument("--id-col", action="append", default=[], metavar="이름",
                   help="분석에서 제외할 ID 열(여러 번 지정 가능)")
    p.add_argument("--reverse", help="역문항 열, 쉼표구분 (--scale-min/max 필요)")
    p.add_argument("--scale-min", type=float, help="리커트 최솟값(역문항 재점수화용)")
    p.add_argument("--scale-max", type=float, help="리커트 최댓값(역문항 재점수화용)")
    p.add_argument("--na", action="append", default=[], metavar="값",
                   help="결측으로 처리할 추가 문자열(여러 번 지정 가능)")
    p.add_argument("-k", "--n-factors", type=int,
                   help="유지할 요인 수(미지정 시 평행분석 기준, 평행분석 끄면 Kaiser)")
    p.add_argument("--rotation", choices=["varimax", "none"], default="varimax",
                   help="회전 방식(기본 varimax)")
    p.add_argument("--parallel-iter", type=int, default=100,
                   help="평행분석 반복수(0이면 생략, 기본 100)")
    p.add_argument("--seed", type=int, default=42, help="평행분석 난수 시드(재현용)")
    p.add_argument("--min-loading", type=float, default=0.40,
                   help="주요 적재/교차적재 판정 임계값(기본 0.40)")
    p.add_argument("--json", action="store_true", help="사람용 보고서 대신 JSON 출력")
    p.add_argument("-V", "--version", action="version", version=f"factorscan {__version__}")
    return p


def _sanitize(obj):
    """JSON으로 낼 수 없는 NaN/Inf(예: 상수열의 문항-총점 상관)를 null로 치환."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    items = _split_list(args.items)
    id_cols = list(args.id_col)
    reverse = _split_list(args.reverse)
    scale_min, scale_max = args.scale_min, args.scale_max

    if args.config:
        try:
            cfg = _load_config(args.config)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"설정 파일 오류: {exc}", file=sys.stderr)
            return 2
        items = items or cfg.get("items", [])
        id_cols = id_cols or cfg.get("id_cols", [])
        reverse = reverse or cfg.get("reverse", [])
        if scale_min is None and "scale_range" in cfg:
            scale_min, scale_max = cfg["scale_range"]

    try:
        columns = load_csv(args.csv, na_values=args.na)
        ds: Dataset = select_items(columns, items=items or None,
                                   id_cols=id_cols, na_values=args.na)
        if reverse:
            if scale_min is None or scale_max is None:
                print("역문항 재점수화에는 --scale-min/--scale-max (또는 config의 scale_range)가 필요합니다.",
                      file=sys.stderr)
                return 2
            violations = reverse_range_violations(ds, reverse, scale_min, scale_max)
            if violations:
                detail = ", ".join(f"{k}({v}개)" for k, v in violations.items())
                print(f"⚠ 경고: 선언한 척도범위 [{scale_min:g}, {scale_max:g}]를 벗어난 값이 "
                      f"있어 역점수가 왜곡될 수 있습니다: {detail}. --scale-min/max를 확인하세요.",
                      file=sys.stderr)
            ds = apply_reverse(ds, reverse, scale_min, scale_max)
        prep = listwise(ds)
        result = analyze(
            prep,
            n_factors=args.n_factors,
            rotation=args.rotation,
            parallel_iter=args.parallel_iter,
            seed=args.seed,
            min_loading=args.min_loading,
        )
    except (DataError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 1

    if args.json:
        # numpy 배열은 이미 tolist()로 변환됨; 상관행렬만 별도 처리
        out = dict(result)
        cm = out.pop("correlation_matrix", None)
        if cm is not None:
            out["correlation_matrix"] = cm.tolist()
        print(json.dumps(_sanitize(out), ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(render(result))
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
