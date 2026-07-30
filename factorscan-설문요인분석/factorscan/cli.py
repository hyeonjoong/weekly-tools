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
import zipfile
from typing import List, Optional

from . import __version__
from .analyze import analyze
from .dataio import (DataError, Dataset, apply_reverse, listwise, load_table,
                     normalize_name, reverse_range_violations, select_items)
from .report import render


def _split_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    # 파일 헤더와 같은 규칙으로 정규화해야 NFC/NFD 차이로 '보이는데 못 찾는' 오류를 피한다.
    return [normalize_name(s) for s in value.split(",") if normalize_name(s)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="factorscan",
        description="설문 척도 요인분석·타당도 진단: KMO · Bartlett · 고유값/평행분석 · "
                    "요인적재량(Varimax) · 공통성 · 문항-총점 상관",
    )
    p.add_argument("csv", metavar="입력파일",
                   help="설문 응답 파일 (행=응답자, 열=문항). "
                        ".csv · .tsv · 엑셀 .xlsx 를 확장자로 자동 판별")
    p.add_argument("-c", "--config", help="설정 JSON (items/reverse/scale_range/id_cols/structure)")
    p.add_argument("--items", help="분석할 문항 열, 쉼표구분 (미지정 시 숫자열 자동선택)")
    p.add_argument("--id-col", action="append", default=[], metavar="이름",
                   help="분석에서 제외할 ID 열(여러 번 지정 가능)")
    p.add_argument("--reverse", help="역문항 열, 쉼표구분 (--scale-min/max 필요)")
    p.add_argument("--scale-min", type=float, help="리커트 최솟값(역문항 재점수화용)")
    p.add_argument("--scale-max", type=float, help="리커트 최댓값(역문항 재점수화용)")
    p.add_argument("--na", action="append", default=[], metavar="값",
                   help="결측으로 처리할 추가 문자열(여러 번 지정 가능)")
    p.add_argument("--encoding", default="utf-8-sig",
                   help="CSV/TSV 인코딩(기본 utf-8-sig; 한국어 엑셀이 저장한 CSV는 cp949/euc-kr). "
                        ".xlsx 에는 적용되지 않음")
    p.add_argument("--sheet", metavar="이름",
                   help="엑셀(.xlsx) 입력에서 읽을 시트 이름(기본: 첫 번째 시트)")
    p.add_argument("--delimiter", metavar="문자",
                   help="구분자 직접 지정(예: ';'). 기본은 확장자로 판별(.tsv→탭, 그 외→쉼표)")
    p.add_argument("-k", "--n-factors", type=int,
                   help="유지할 요인 수(미지정 시 평행분석 기준, 평행분석 끄면 Kaiser)")
    p.add_argument("--extraction", choices=["pca", "paf", "ml"], default="pca",
                   help="추출 방식: pca(주성분, SPSS 기본) · paf(주축분해, 공통요인 모형) · "
                        "ml(최대우도, χ²/RMSEA/TLI/CFI 적합도지수 제공)")
    p.add_argument("--fit-scan", action="store_true",
                   help="요인 수 k=1..최대까지 ML 적합도지수를 훑어 표로 제시(--extraction ml 필요)")
    p.add_argument("--correlation", choices=["pearson", "polychoric"], default="pearson",
                   help="상관 방식: pearson(기본) · polychoric(순서형 리커트 잠재상관)")
    p.add_argument("--rotation", choices=["varimax", "promax", "none"], default="varimax",
                   help="회전 방식: varimax(직교, 기본) · promax(사교, 요인상관 허용) · none")
    p.add_argument("--parallel-iter", type=int, default=100,
                   help="평행분석 반복수(0이면 생략, 기본 100)")
    p.add_argument("--seed", type=int, default=42, help="평행분석·부트스트랩 난수 시드(재현용)")
    p.add_argument("--bootstrap", type=int, default=0, metavar="N",
                   help="부트스트랩 재표본 N개로 적재량 95%% 신뢰구간과 요인 수 안정성을 추정"
                        "(예: 500. 0이면 생략, 기본 0)")
    p.add_argument("--group-col", metavar="이름",
                   help="집단(사이트·성별·투여군) 열 이름. 집단별로 요인해를 다시 적합해 "
                        "Tucker 일치계수(φ)로 요인구조가 재현되는지 점검")
    p.add_argument("--min-loading", type=float, default=0.40,
                   help="주요 적재/교차적재 판정 임계값(기본 0.40)")
    p.add_argument("--json", action="store_true", help="사람용 보고서 대신 JSON 출력")
    p.add_argument("--csv-out", metavar="경로",
                   help="문항×요인 적재표를 CSV 파일로 저장(논문 부록·엑셀용, utf-8-sig)")
    p.add_argument("--eigen-out", metavar="경로",
                   help="고유값·평행분석 기준선을 CSV로 저장(엑셀에서 스크리 도표를 그릴 표)")
    p.add_argument("--scores-out", metavar="경로",
                   help="응답자별 하위척도(요인) 점수를 CSV로 저장(결측제거 표본 기준)")
    p.add_argument("--score-method", choices=["sum", "mean", "regression"], default="sum",
                   help="하위척도 점수 계산: sum(합산, 기본) · mean(평균) · "
                        "regression(Thurstone 회귀 요인점수, 전 문항 적재 가중·표준화)")
    p.add_argument("--score-missing", choices=["listwise", "prorate"], default="listwise",
                   help="점수 산출 시 결측 처리: listwise(완전응답자만, 기본) · "
                        "prorate(하위척도별 결측이 허용치 이하면 응답한 문항 평균으로 환산 — "
                        "PRO 채점 매뉴얼의 표준 규칙). 요인분석 자체는 언제나 완전응답자 기준")
    p.add_argument("--max-missing-prop", type=float, default=0.2, metavar="비율",
                   help="--score-missing prorate 에서 허용할 하위척도별 결측 비율(기본 0.2)")
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
        cfg = json.load(fh)
    if not isinstance(cfg, dict):
        raise ConfigError("설정 파일 최상위는 JSON 객체({ ... })여야 합니다.")
    return cfg


class ConfigError(Exception):
    """설정 파일 구조 오류."""


def _cfg_list(cfg: dict, key: str) -> List[str]:
    """설정의 리스트 필드를 안전하게 문자열 리스트로 정규화(문자열 하나면 쉼표분리)."""
    v = cfg.get(key)
    if v is None:
        return []
    if isinstance(v, str):
        return _split_list(v)
    if isinstance(v, (list, tuple)):
        return [normalize_name(x) for x in v if normalize_name(x)]
    raise ConfigError(f"설정의 '{key}'는 문자열 목록이어야 합니다.")


def _cfg_structure(cfg: dict):
    """설정의 'structure'(가설 하위척도 → 문항목록)를 검증해 정규화한다.

    형식: {"수면의질": ["Q1","Q2"], "주간기능": ["Q3","Q4"]}
    JSON 객체는 파이썬에서 삽입 순서를 유지하므로 하위척도 순서가 그대로 보존된다.
    """
    if "structure" not in cfg:
        return None
    st = cfg["structure"]
    if not isinstance(st, dict) or not st:
        raise ConfigError(
            "설정의 'structure'는 {\"하위척도이름\": [\"문항1\", \"문항2\"], ...} 형태의 "
            "비어 있지 않은 JSON 객체여야 합니다.")
    out = {}
    for key, val in st.items():
        name = normalize_name(key)
        if not name:
            raise ConfigError("설정의 'structure'에 이름이 빈 하위척도가 있습니다.")
        if isinstance(val, str):
            items = _split_list(val)
        elif isinstance(val, (list, tuple)):
            items = [normalize_name(v) for v in val if normalize_name(v)]
        else:
            raise ConfigError(f"설정의 structure['{name}']는 문항 이름 목록이어야 합니다.")
        if len(items) < 1:
            raise ConfigError(f"설정의 structure['{name}']에 문항이 없습니다.")
        if len(set(items)) != len(items):
            raise ConfigError(f"설정의 structure['{name}']에 중복된 문항이 있습니다.")
        if name in out:
            raise ConfigError(f"설정의 'structure'에 중복된 하위척도 이름이 있습니다: {name}")
        out[name] = items
    return out


def _cfg_scale_range(cfg: dict):
    if "scale_range" not in cfg:
        return None, None
    sr = cfg["scale_range"]
    if (not isinstance(sr, (list, tuple)) or len(sr) != 2
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       and math.isfinite(x) for x in sr)):
        raise ConfigError("설정의 'scale_range'는 [최솟값, 최댓값] 형태의 유한한 숫자 2원소 배열이어야 합니다.")
    return float(sr[0]), float(sr[1])


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.group_col:
        args.group_col = normalize_name(args.group_col)

    items = _split_list(args.items)
    id_cols = [normalize_name(c) for c in args.id_col]
    reverse = _split_list(args.reverse)
    scale_min, scale_max = args.scale_min, args.scale_max
    structure = None

    if args.config:
        try:
            cfg = _load_config(args.config)
            items = items or _cfg_list(cfg, "items")
            id_cols = id_cols or _cfg_list(cfg, "id_cols")
            reverse = reverse or _cfg_list(cfg, "reverse")
            structure = _cfg_structure(cfg)
            if scale_min is None:
                cmin, cmax = _cfg_scale_range(cfg)
                if cmin is not None:
                    scale_min, scale_max = cmin, cmax
        except (OSError, json.JSONDecodeError, ConfigError) as exc:
            print(f"설정 파일 오류: {exc}", file=sys.stderr)
            return 2

    # 인자 값 검증(잘못된 임계값/범위를 조용히 통과시키지 않음)
    if not (0.0 <= args.min_loading <= 1.0):
        print("오류: --min-loading 은 0.0~1.0 사이여야 합니다.", file=sys.stderr)
        return 2
    # 구분자는 정확히 한 글자여야 한다(csv 모듈 제약). 셸이 확장하지 않는 '\t'를
    # 그대로 넘기는 것이 가장 흔한 실수라, 탭으로 해석해 준다.
    if args.delimiter is not None:
        if args.delimiter in ("\\t", "\\\\t", "tab", "TAB"):
            args.delimiter = "\t"
        if len(args.delimiter) != 1:
            print(f"오류: --delimiter 는 한 글자여야 합니다(받은 값: '{args.delimiter}'). "
                  f"탭 구분이면 --delimiter '\\t' 또는 파일 확장자를 .tsv 로 하세요.",
                  file=sys.stderr)
            return 2

    # 적합도지수는 확률모형(ML)에서만 정의된다 — 조용히 무시하지 않고 명확히 거절한다.
    if args.bootstrap < 0:
        print("오류: --bootstrap 은 0 이상이어야 합니다.", file=sys.stderr)
        return 2
    if args.bootstrap == 1:
        # 재표본 1개로는 백분위 구간이 정의되지 않는다(전부 NaN이 된다).
        print("오류: --bootstrap 은 0(생략) 또는 2 이상이어야 합니다 — 재표본 1개로는 "
              "백분위 신뢰구간을 계산할 수 없습니다(500회 이상 권장).", file=sys.stderr)
        return 2
    if 0 < args.bootstrap < 100:
        print(f"⚠ 경고: 부트스트랩 재표본이 적습니다(--bootstrap {args.bootstrap}). "
              f"백분위 신뢰구간은 보통 500회 이상을 권장합니다.", file=sys.stderr)
    if args.bootstrap > 0 and args.correlation == "polychoric":
        # 폴리코릭은 재표본마다 문항쌍 전부를 다시 최적화한다 → 피어슨의 수백 배.
        # 실행 전에 알려 주지 않으면 사용자는 도구가 멈춘 줄 안다.
        print(f"⏳ 알림: 폴리코릭 상관 + 부트스트랩 {args.bootstrap}회는 오래 걸립니다"
              f"(문항 8개·응답자 80명 기준 100회에 약 40초, 문항이 늘면 제곱으로 증가 — "
              f"20문항·300명이면 재표본당 2~3초라 500회에 20분을 넘습니다). "
              f"빠른 확인이 필요하면 --correlation pearson 으로 먼저 돌려 보세요.",
              file=sys.stderr)
    if args.fit_scan and args.extraction != "ml":
        print("오류: --fit-scan 은 --extraction ml 에서만 쓸 수 있습니다"
              "(χ²/RMSEA 등 적합도지수는 최대우도 모형에서만 정의됩니다).", file=sys.stderr)
        return 2
    if not (0.0 <= args.max_missing_prop < 1.0):
        print("오류: --max-missing-prop 은 0 이상 1 미만이어야 합니다.", file=sys.stderr)
        return 2
    if args.score_missing == "prorate" and args.score_method == "regression":
        print("오류: --score-missing prorate 는 --score-method sum/mean 에서만 쓸 수 있습니다 "
              "(회귀 요인점수는 모든 문항의 적재를 가중치로 쓰므로 결측 응답자를 채점할 수 "
              "없습니다).", file=sys.stderr)
        return 2
    if scale_min is not None and scale_max is not None and scale_min >= scale_max:
        print(f"오류: --scale-min({scale_min:g})은 --scale-max({scale_max:g})보다 작아야 합니다.",
              file=sys.stderr)
        return 2

    try:
        columns = load_table(args.csv, na_values=args.na, encoding=args.encoding,
                             sheet=args.sheet, delimiter=args.delimiter)
        # 지정한 ID 열이 실제로 없으면(오타·인코딩 깨짐) 조용히 넘기지 말고 알린다.
        unknown_id = [c for c in id_cols if c not in columns]
        if unknown_id:
            print(f"⚠ 경고: --id-col 로 지정한 열이 CSV에 없습니다: {', '.join(unknown_id)}. "
                  f"열 이름/인코딩(--encoding)을 확인하세요.", file=sys.stderr)
        # 집단 열은 문항이 아니다. 숫자 코드(0/1, 1/2/3)로 들어오는 일이 많아 자동선택에
        # 그대로 걸리면 '문항'인 척 요인분석에 섞여 결과를 조용히 오염시킨다 → 후보에서 제외.
        exclude = list(id_cols)
        if args.group_col:
            if args.group_col not in columns:
                print(f"오류: --group-col 로 지정한 열 '{args.group_col}'이 파일에 없습니다. "
                      f"열 이름/인코딩(--encoding)을 확인하세요.", file=sys.stderr)
                return 2
            if args.group_col not in exclude:
                exclude.append(args.group_col)
        ds: Dataset = select_items(columns, items=items or None,
                                   id_cols=exclude, na_values=args.na)
        if args.group_col and args.group_col in ds.names:
            print(f"⚠ 경고: 집단 열 '{args.group_col}'이 분석 문항에도 들어가 있습니다 "
                  f"(--items 로 명시했기 때문). 집단 변수는 문항이 아니므로 제외하세요.",
                  file=sys.stderr)
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
        # 집단 라벨은 결측제거 후 살아남은 행에만 맞춰 자른다(순번이 어긋나면 엉뚱한
        # 응답자가 엉뚱한 집단으로 들어가 비교 자체가 무의미해진다).
        group_labels = None
        if args.group_col:
            col = columns[args.group_col]
            group_labels = (col[prep.row_mask].tolist()
                            if prep.row_mask is not None else col.tolist())
        result = analyze(
            prep,
            n_factors=args.n_factors,
            rotation=args.rotation,
            parallel_iter=args.parallel_iter,
            seed=args.seed,
            min_loading=args.min_loading,
            extraction=args.extraction,
            correlation=args.correlation,
            fit_scan=args.fit_scan,
            scale_min=scale_min,
            scale_max=scale_max,
            bootstrap=args.bootstrap,
            group_labels=group_labels,
            group_name=args.group_col,
            structure=structure,
        )
    except (DataError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 1
    except zipfile.BadZipFile as exc:
        # 손상된 .xlsx 는 zipfile 예외로 새어 나올 수 있다 — 역추적 대신 한국어로 안내한다.
        print(f"오류: 엑셀 파일이 손상되어 읽을 수 없습니다({exc}). "
              f"엑셀에서 'Excel 통합 문서(.xlsx)'로 다시 저장해 보세요.", file=sys.stderr)
        return 1
    except PermissionError as exc:
        print(f"오류: 파일을 읽을 권한이 없습니다: {exc}", file=sys.stderr)
        return 1
    except IsADirectoryError:
        print(f"오류: '{args.csv}' 는 폴더입니다 — 파일 경로를 지정하세요.", file=sys.stderr)
        return 1

    if args.csv_out:
        from .report import loadings_table_csv
        # 내용을 먼저 만든 뒤 파일을 연다(생성 중 예외로 기존 파일이 0바이트로 잘리는 것 방지).
        text = loadings_table_csv(result)
        try:
            with open(args.csv_out, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"CSV 저장 실패: {exc}", file=sys.stderr)
            return 1
        print(f"✓ 적재표를 저장했습니다: {args.csv_out}", file=sys.stderr)

    if args.eigen_out:
        from .report import eigen_table_csv
        text = eigen_table_csv(result)
        try:
            with open(args.eigen_out, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"고유값 CSV 저장 실패: {exc}", file=sys.stderr)
            return 1
        print(f"✓ 고유값·평행분석 표를 저장했습니다: {args.eigen_out} "
              f"(엑셀에서 꺾은선 그래프 → 스크리 도표)", file=sys.stderr)

    if args.scores_out:
        import numpy as _np
        from .report import scores_table_csv
        mask = prep.row_mask
        prorate = args.score_missing == "prorate"
        # 비례배분이면 **결측 제거 전 전체 응답자**를 채점한다 → ID·행번호도 전체 기준.
        sel = slice(None) if prorate else mask
        id_pairs = []
        for name in id_cols:
            if name in columns and (prorate or mask is not None):
                id_pairs.append((name, [str(v) for v in columns[name][sel]]))
        if prorate:
            row_numbers = list(range(1, prep.n_total + 1))
        else:
            # ID가 없을 때 원본 CSV 행번호로 역추적 가능하게(결측삭제로 순번 어긋남 방지).
            row_numbers = (_np.where(mask)[0] + 1).tolist() if mask is not None else None
        try:
            text = scores_table_csv(result, prep.matrix, id_pairs,
                                    method=args.score_method, row_numbers=row_numbers,
                                    raw_matrix=prep.raw if prorate else None,
                                    max_missing_prop=args.max_missing_prop if prorate else None)
        except ValueError as exc:
            # 오염된 점수를 쓰느니 파일을 만들지 않는다(원인·해결책은 예외 메시지에 담겨 있다).
            print(f"오류: {exc}", file=sys.stderr)
            return 1
        try:
            with open(args.scores_out, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"점수 CSV 저장 실패: {exc}", file=sys.stderr)
            return 1
        if args.score_missing == "prorate":
            print(f"✓ 하위척도 점수를 저장했습니다: {args.scores_out} "
                  f"(전체 {prep.n_total}명, {args.score_method}, 비례배분 "
                  f"결측 허용 {args.max_missing_prop*100:.0f}% — 요인분석 자체는 "
                  f"완전응답 {result['n_used']}명 기준)", file=sys.stderr)
        else:
            print(f"✓ 하위척도 점수를 저장했습니다: {args.scores_out} "
                  f"({result['n_used']}명, {args.score_method})", file=sys.stderr)

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
