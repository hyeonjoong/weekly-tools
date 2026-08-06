"""metapool 명령행 인터페이스."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .analysis import LOO_MAX_K, run_analysis
from .effects import MEASURES, EffectError
from .io_csv import TableError, canonical_columns, detect_measure, read_table, validate_measure
from .meta import TAU2_METHODS, MetaError
from .report import render_csv, render_markdown, render_text

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_EPILOG = """
입력 CSV 형식 (다섯 중 하나)
  1) 이미 계산된 효과크기 : study, effect, se            (또는 ci_low, ci_high)
  2) 연속형 2군 원자료     : study, n1, mean1, sd1, n2, mean2, sd2
  3) 이분형 2군 원자료     : study, events1, n1, events2, n2   (a,b,c,d 형식도 인식)
  4) 상관계수              : study, r, n
  5) 단일군 비율(유병률 등) : study, events, n
  · 선택 열: subgroup (하위군 분석)
  · 열 이름이 다르면 --map 원본열=표준열 (예: --map 실험군수=n1)
  · 1군 = 처치/실험군, 2군 = 대조군. 효과 방향은 항상 "1군 - 2군" 또는 "1군 / 2군".

예시
  metapool studies.csv                            # 지표 자동 판별 + 전체 리포트
  metapool smd.csv --measure smd --subgroup 연령대
  metapool binary.csv --measure or --tau2 PM
  metapool effects.csv --json > result.json
  metapool binary.csv --measure or --baseline-risk 0.2   # NNT를 기저위험 20% 기준으로
  metapool studies.csv --tau2 REML --csv -o 표.csv       # 원고 표로 쓸 수 있는 CSV
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="metapool",
        description="메타분석 한 방에: 효과크기 합성(고정·변량) + 이질성 + 하위군 + 민감도 + 출판편향 + 논문 문장",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="연구 목록 CSV 경로")
    p.add_argument("--measure", "-m", choices=MEASURES, default=None,
                   help="효과크기 지표 (기본: 열 이름으로 자동 판별)")
    p.add_argument("--model", choices=("random", "fixed"), default="random",
                   help="주 모형 — 문장/숲그림에 쓸 모형 (기본: random). 두 모형 모두 항상 함께 보고합니다.")
    p.add_argument("--tau2", choices=TAU2_METHODS + tuple(m.lower() for m in TAU2_METHODS),
                   default="DL",
                   help="변량효과 tau² 추정법 (기본: DL. PM=Paule–Mandel, "
                        "REML=제한최대가능도(metafor 기본), SJ=Sidik–Jonkman)")
    p.add_argument("--no-hksj", action="store_true",
                   help="Hartung–Knapp 보정을 끄고 고전적 z 기반 신뢰구간 사용")
    p.add_argument("--subgroup", metavar="열이름", default=None,
                   help="하위군 분석에 쓸 열 (지정하지 않아도 subgroup 열이 있으면 자동 사용)")
    p.add_argument("--label", metavar="열이름", default=None, help="연구명으로 쓸 열")
    p.add_argument("--conf", type=float, default=0.95, metavar="0.95",
                   help="출력 신뢰수준 (기본 0.95, 최대 0.999)")
    p.add_argument("--input-conf", type=float, default=0.95, metavar="0.95",
                   help="입력 파일의 ci_low/ci_high 가 몇 %% 구간인지 (기본 0.95). "
                        "--conf 와 별개입니다 — 논문에서 옮겨 적은 구간은 보통 95%%입니다.")
    p.add_argument("--outcome", metavar="결과변수명", default=None,
                   help="논문 문장에 넣을 결과변수 이름 (예: 'ISI 총점')")
    p.add_argument("--cc", type=float, default=0.5, metavar="0.5",
                   help="이분형에서 0인 칸에 더할 연속성 보정값 (기본 0.5, 0이면 보정 안 함)")
    p.add_argument("--log-input", action="store_true",
                   help="--measure generic 에서 effect 열이 이미 OR/RR(비율) 값일 때 로그를 취해 읽기")
    p.add_argument("--map", action="append", default=[], metavar="원본열=표준열",
                   help="열 이름 수동 매핑 (반복 가능). 표준열: " + ", ".join(canonical_columns()))
    p.add_argument("--sort", choices=("none", "effect", "label", "weight"), default="none",
                   help="연구 정렬 순서 (기본: 파일 순서)")
    p.add_argument("--no-forest", action="store_true", help="숲그림 생략")
    p.add_argument("--no-sensitivity", action="store_true", help="leave-one-out 민감도 분석 생략")
    p.add_argument("--sensitivity-max", type=int, default=LOO_MAX_K, metavar=str(LOO_MAX_K),
                   help="이 연구 수를 넘으면 민감도 분석을 자동 생략 (계산량이 k²에 비례)")
    p.add_argument("--no-bias", action="store_true",
                   help="출판편향 진단(Egger·Begg·trim-and-fill·깔때기그림) 생략")
    p.add_argument("--no-funnel", action="store_true", help="텍스트 깔때기그림 생략")
    p.add_argument("--no-trimfill", action="store_true", help="trim-and-fill 보정 생략")
    p.add_argument("--trimfill-estimator", choices=("L0", "R0"), default="L0",
                   help="trim-and-fill 누락 연구 수 추정량 (기본 L0)")
    p.add_argument("--baseline-risk", type=float, default=None, metavar="0.20",
                   help="NNT·절대위험차를 계산할 때 가정할 대조군 위험 (0~1). "
                        "생략하면 포함 연구의 대조군 사건률을 씁니다. OR/RR/RD 에서만 의미가 있습니다.")
    p.add_argument("--json", action="store_true", help="사람용 리포트 대신 JSON 출력")
    p.add_argument("--md", "--markdown", dest="markdown", action="store_true",
                   help="마크다운 리포트 출력")
    p.add_argument("--csv", dest="csv_out", action="store_true",
                   help="연구별·통합·민감도 결과를 tidy CSV 로 출력 (엑셀/R 로 넘길 때)")
    p.add_argument("--out", "-o", metavar="파일", default=None,
                   help="결과를 파일로 저장 (확장자가 .json/.md/.csv 면 형식 자동 선택)")
    p.add_argument("--version", action="version", version="metapool %s" % __version__)
    return p


def _parse_map(items: List[str]) -> dict:
    mapping = {}
    for item in items:
        if "=" not in item:
            raise TableError("--map 형식은 원본열=표준열 입니다 (받은 값: %r)" % item)
        src, dst = item.split("=", 1)
        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            raise TableError("--map 형식은 원본열=표준열 입니다 (받은 값: %r)" % item)
        mapping[src] = dst
    return mapping


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not (0.5 < args.conf <= 0.999):
        parser.error("--conf 는 0.5 초과 0.999 이하여야 합니다 (받은 값: %r)" % args.conf)
    if not (0.5 < args.input_conf <= 0.999):
        parser.error("--input-conf 는 0.5 초과 0.999 이하여야 합니다 (받은 값: %r)" % args.input_conf)
    if args.cc < 0:
        parser.error("--cc 는 0 이상이어야 합니다 (받은 값: %r)" % args.cc)
    chosen = [n for n, v in (("--json", args.json), ("--md", args.markdown),
                             ("--csv", args.csv_out)) if v]
    if len(chosen) > 1:
        parser.error("%s 는 함께 쓸 수 없습니다 — 하나만 고르세요." % ", ".join(chosen))
    if args.baseline_risk is not None and not (0.0 < args.baseline_risk < 1.0):
        parser.error("--baseline-risk 는 0 초과 1 미만이어야 합니다 (받은 값: %r)" % args.baseline_risk)

    if args.out:
        try:
            same = os.path.exists(args.out) and os.path.samefile(args.out, args.csv)
        except OSError:
            same = False
        if same:
            sys.stderr.write("오류: --out 이 입력 파일과 같습니다 — 원자료를 덮어쓸 뻔했습니다. "
                             "다른 파일 이름을 지정하세요.\n")
            return EXIT_ERROR
        # lexists: 끊어진 심볼릭 링크도 "이미 있는 경로"다 (exists 는 False 를 준다).
        if os.path.lexists(args.out):
            sys.stderr.write("경고: 기존 파일을 덮어씁니다 — %s\n" % args.out)
        ext_out = os.path.splitext(args.out)[1].lower()
        if ext_out in (".tsv", ".xlsx", ".xls") or (ext_out == ".csv" and (args.json or args.markdown)):
            sys.stderr.write(
                "경고: --out 확장자가 표 형식이지만 저장되는 내용은 표가 아닙니다 "
                "(표로 받으려면 --csv 를 쓰세요).\n"
            )

    try:
        mapping = _parse_map(args.map)
        records, header, warns = read_table(
            args.csv, mapping=mapping, label_column=args.label, subgroup_column=args.subgroup
        )
        measure = args.measure or detect_measure(records, header)
        validate_measure(records, measure)
        if args.log_input and measure != "generic":
            raise TableError(
                "--log-input 은 --measure generic 에서만 쓸 수 있습니다 (지금 지표: %s). "
                "%s 는 원자료에서 직접 계산하므로 로그 변환이 이미 반영되어 있습니다."
                % (measure, measure)
            )
        analysis = run_analysis(
            records,
            measure=measure,
            conf=args.conf,
            tau2_method=args.tau2.upper(),
            knapp_hartung=not args.no_hksj,
            do_loo=not args.no_sensitivity,
            do_egger=not args.no_bias,
            cc=args.cc,
            log_input=args.log_input,
            input_conf=args.input_conf,
            source=os.path.basename(args.csv),
            primary_model=args.model,
            sort=args.sort,
            outcome=args.outcome,
            loo_max_k=max(0, args.sensitivity_max),
            do_trimfill=not (args.no_trimfill or args.no_bias),
            trimfill_estimator=args.trimfill_estimator,
            baseline_risk=args.baseline_risk,
        )
    except (TableError, EffectError, MetaError) as exc:
        sys.stderr.write("오류: %s\n" % exc)
        return EXIT_ERROR
    except (ArithmeticError, ValueError) as exc:
        sys.stderr.write(
            "오류: 계산 중 값이 표현 범위를 벗어났습니다 — 입력 숫자의 자릿수를 확인하세요 (%s)\n" % exc
        )
        return EXIT_ERROR
    except OSError as exc:
        sys.stderr.write("오류: 파일을 읽을 수 없습니다 — %s\n" % exc)
        return EXIT_ERROR

    analysis.warnings = list(warns) + list(analysis.warnings)

    fmt_choice = ("json" if args.json else "md" if args.markdown
                  else "csv" if args.csv_out else "text")
    if args.out and not chosen:
        ext = os.path.splitext(args.out)[1].lower()
        if ext == ".json":
            fmt_choice = "json"
        elif ext in (".md", ".markdown"):
            fmt_choice = "md"
        elif ext == ".csv":
            fmt_choice = "csv"

    try:
        if fmt_choice == "json":
            # to_dict()가 inf/nan을 이미 null로 바꾸므로 allow_nan=False가 안전망이 된다.
            text = json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
        elif fmt_choice == "md":
            text = render_markdown(analysis)
        elif fmt_choice == "csv":
            text = render_csv(analysis)
        else:
            text = render_text(analysis, show_forest=not args.no_forest,
                               show_funnel=not args.no_funnel)
    except (ArithmeticError, ValueError) as exc:
        sys.stderr.write("오류: 결과를 표시하는 중 문제가 발생했습니다 (%s)\n" % exc)
        return EXIT_ERROR

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError as exc:
            sys.stderr.write("오류: 결과를 저장할 수 없습니다 — %s\n" % exc)
            return EXIT_ERROR
        sys.stderr.write("저장했습니다: %s\n" % args.out)
    else:
        sys.stdout.write(text + "\n")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
