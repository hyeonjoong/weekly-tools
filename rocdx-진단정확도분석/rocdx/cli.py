"""Command-line entry point for rocdx."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from typing import List, Optional, Sequence

from . import __version__
from .analyze import add_comparison, analyze, finalize_comparisons, load_dataset
from .jsonout import analysis_to_json
from .loader import LoadError, read_table
from .report import format_report, markdown_report, points_csv_rows
from .svgplot import roc_svg

__all__ = ["main", "build_parser"]

_EPILOG = """\
예시 / examples:
  rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis
  rocdx data.csv --score crp --truth sepsis --min-spec 0.90 --bootstrap 2000
  rocdx data.csv --score crp --truth 진단 --positive-label 양성 --cutoff 10 --cutoff 20
  rocdx data.csv --score cognition --truth dementia --direction lower
  rocdx data.csv --score new_marker --truth sepsis --compare crp_mg_L
  rocdx data.csv --score crp --truth sepsis --pauc-min-spec 0.90 --bootstrap 2000
  rocdx data.csv --score new --truth sepsis --compare crp --ni-margin 0.05
  rocdx data.csv --score crp --truth sepsis --cluster-col patient_id --cluster --bootstrap 2000
  rocdx data.csv --score crp --truth sepsis --plot-svg roc.svg --json out.json
  rocdx data.csv --list-columns
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rocdx",
        description="진단정확도(ROC) 분석 — AUC·민감도·특이도·PPV/NPV·우도비를 "
                    "신뢰구간과 함께 계산합니다. 외부 라이브러리 없이 동작합니다.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV/TSV 파일")
    p.add_argument("--score", "-s", help="검사값(연속형 지표) 열 이름 또는 #번호")
    p.add_argument("--truth", "-t", help="기준 진단(정답) 열 이름 또는 #번호")
    p.add_argument("--positive-label", help="기준 진단 열에서 '질환 있음'을 뜻하는 값 (이 값이 아닌 나머지는 "
                        "모두 비질환군이 됩니다 — 판정보류를 빼려면 --negative-label 도 지정)")
    p.add_argument("--negative-label", help="'질환 없음'을 뜻하는 값 (--positive-label 과 함께 지정해야 하며, "
                        "두 값 중 어느 쪽도 아닌 행은 분석에서 제외)")
    p.add_argument("--direction", choices=["auto", "higher", "lower"], default="auto",
                   help="검사값이 높을수록(higher)/낮을수록(lower) 질환. 기본 auto")
    p.add_argument("--min-spec", type=float, metavar="P",
                   help="특이도 하한 (예: 0.90) — 이를 만족하는 가장 민감한 절단점")
    p.add_argument("--min-sens", type=float, metavar="P",
                   help="민감도 하한 (예: 0.95) — 이를 만족하는 가장 특이한 절단점")
    p.add_argument("--cutoff", type=float, action="append", default=[], metavar="X",
                   help="미리 정해진 절단점 (여러 번 지정 가능)")
    p.add_argument("--prevalence", type=float, metavar="P",
                   help="대상 인구집단의 유병률 — PPV/NPV를 이 값 기준으로 재계산")
    p.add_argument("--compare", action="append", default=[], metavar="COL",
                   help="같은 대상에서 측정한 다른 검사 열 — DeLong 짝지은 AUC 비교 "
                        "(2개 이상이면 Holm 다중비교 보정 p도 함께 출력)")
    p.add_argument("--ni-margin", type=float, metavar="M",
                   help="비열등성 한계 (AUC 차이, 예: 0.05) — --compare 와 함께 사용. "
                        "한계는 임상적 근거로 사전에 정해야 합니다")
    p.add_argument("--pauc-min-spec", type=float, metavar="P",
                   help="부분 AUC를 계산할 구간의 특이도 하한 (예: 0.90) — "
                        "선별·확진 등 실제로 쓰는 구간만 적분")
    p.add_argument("--pauc-max-spec", type=float, default=1.0, metavar="P",
                   help="부분 AUC 구간의 특이도 상한 (기본 1.0)")
    p.add_argument("--cluster-col", metavar="COL",
                   help="독립 단위 식별자 열 (환자ID·기관ID). 중복이 있으면 경고하고, "
                        "--cluster 를 함께 주면 군집 부트스트랩으로 보정")
    p.add_argument("--cluster", action="store_true",
                   help="--cluster-col 기준으로 군집 부트스트랩 신뢰구간 계산 "
                        "(--bootstrap 반복 수 필요)")
    p.add_argument("--bootstrap", type=int, default=0, metavar="N",
                   help="절단점 선택까지 포함한 부트스트랩 반복 수 (예: 2000). 기본 0=생략")
    p.add_argument("--seed", type=int, default=20260806, help="부트스트랩 난수 seed")
    p.add_argument("--alpha", type=float, default=0.05, help="유의수준 (기본 0.05 = 95%% CI)")
    p.add_argument("--ci-method", choices=["logit", "wald"], default="logit",
                   help="AUC 신뢰구간 계산 방식 (기본 logit)")
    p.add_argument("--sep", help="구분자 강제 지정 (기본: 자동 판별)")
    p.add_argument("--encoding", help="인코딩 강제 지정 (기본: 자동 판별)")
    p.add_argument("--markdown", action="store_true", help="마크다운 표로 출력")
    p.add_argument("--no-curve", action="store_true", help="ASCII ROC 곡선 생략")
    p.add_argument("--points-csv", metavar="FILE",
                   help="모든 절단점(TP/FP/민감도/특이도)을 CSV로 저장")
    p.add_argument("--json", metavar="FILE",
                   help="전체 결과를 JSON으로 저장 ('-' 이면 표준출력으로만 내보내고 "
                        "사람용 보고서는 생략)")
    p.add_argument("--plot-svg", metavar="FILE",
                   help="ROC 곡선을 SVG 그림 파일로 저장 (비교 검사·부분 AUC 구간 포함)")
    p.add_argument("--version", action="version", version=f"rocdx {__version__}")
    p.add_argument("--list-columns", action="store_true", help="열 이름만 출력하고 종료")
    p.add_argument("--show-samples", action="store_true",
                   help="--list-columns 에서 값 미리보기도 표시 (8자까지만)")
    return p


def _list_columns(table, show_samples: bool = False) -> int:
    """Print the column names (and, only if asked, a short masked sample).

    Sample cells are withheld by default: this command is what a confused user
    runs first, and a clinical export's first rows are whole patient records.
    """
    print(f"파일: {table.path}")
    print(f"인코딩: {table.encoding}   구분자: {table.delimiter!r}   행: {len(table.rows)}")
    print("열 목록:")
    for i, h in enumerate(table.headers, 1):
        if not show_samples:
            print(f"  #{i:<3} {h}")
            continue
        sample = [r[i - 1] for r in table.rows[:2] if len(r) >= i and r[i - 1].strip()]
        masked = [c if len(c) <= 8 else c[:8] + "…" for c in sample]
        print(f"  #{i:<3} {h}   예: {', '.join(masked) or '(비어 있음)'}")
    if not show_samples:
        print("  (값 미리보기는 --show-samples 를 붙이면 8자까지만 표시합니다 — "
              "환자정보가 화면·로그에 남지 않도록 기본은 숨김입니다)")
    return 0


def _make_output_safe() -> None:
    """Never die on a terminal whose encoding cannot show — / ∞ / Korean.

    A hospital workstation with ``LC_ALL=C`` would otherwise raise
    UnicodeEncodeError halfway through printing the report.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream already closed
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _make_output_safe()
    try:
        table = read_table(args.csv, encoding=args.encoding, delimiter=args.sep)
        if args.list_columns:
            return _list_columns(table, show_samples=args.show_samples)
        if not args.score or not args.truth:
            print("오류: --score 와 --truth 를 모두 지정해야 합니다.\n"
                  "      열 이름을 모르면 --list-columns 를 먼저 실행하세요.", file=sys.stderr)
            return 2
        for name, val in (("--min-spec", args.min_spec), ("--min-sens", args.min_sens)):
            if val is not None and not 0.0 <= val <= 1.0:
                print(f"오류: {name} 은(는) 0과 1 사이의 비율이어야 합니다 (예: 0.90).",
                      file=sys.stderr)
                return 2
        if args.bootstrap < 0:
            print("오류: --bootstrap 은 0 이상이어야 합니다.", file=sys.stderr)
            return 2
        for c in args.cutoff:
            if not math.isfinite(c):
                print("오류: --cutoff 는 유한한 숫자여야 합니다 (nan/inf 불가).",
                      file=sys.stderr)
                return 2
        if args.ni_margin is not None:
            if not (math.isfinite(args.ni_margin) and 0.0 < args.ni_margin < 1.0):
                print("오류: --ni-margin 은 0과 1 사이의 AUC 차이여야 합니다 (예: 0.05).",
                      file=sys.stderr)
                return 2
            if not args.compare:
                print("오류: --ni-margin 은 --compare 와 함께 지정해야 합니다 "
                      "(비교 대상이 있어야 비열등성을 말할 수 있습니다).", file=sys.stderr)
                return 2
        if args.pauc_min_spec is None and args.pauc_max_spec != 1.0:
            print("오류: --pauc-max-spec 는 --pauc-min-spec 과 함께 지정해야 합니다 "
                  "(구간의 양쪽 끝이 있어야 부분 AUC를 계산합니다).", file=sys.stderr)
            return 2
        if args.pauc_min_spec is not None and not math.isfinite(args.pauc_min_spec):
            print("오류: --pauc-min-spec 는 유한한 숫자여야 합니다.", file=sys.stderr)
            return 2
        if not math.isfinite(args.pauc_max_spec):
            print("오류: --pauc-max-spec 는 유한한 숫자여야 합니다.", file=sys.stderr)
            return 2
        if args.cluster and not args.cluster_col:
            print("오류: --cluster 는 --cluster-col 로 단위 식별자 열을 함께 지정해야 합니다.",
                  file=sys.stderr)
            return 2
        if args.cluster and args.bootstrap <= 0:
            print("오류: --cluster 는 --bootstrap N (예: 2000)이 필요합니다 "
                  "— 군집 보정 구간은 재표본으로만 계산됩니다.", file=sys.stderr)
            return 2

        dataset = load_dataset(
            table, args.score, args.truth,
            positive_label=args.positive_label, negative_label=args.negative_label,
            compare_cols=args.compare, cluster_col=args.cluster_col,
        )
        analysis = analyze(
            dataset, direction=args.direction, alpha=args.alpha,
            prevalence=args.prevalence, min_spec=args.min_spec, min_sens=args.min_sens,
            cutoffs=args.cutoff, n_boot=args.bootstrap, seed=args.seed,
            ci_method=args.ci_method, pauc_min_spec=args.pauc_min_spec,
            pauc_max_spec=args.pauc_max_spec, cluster=args.cluster,
        )
        for comp in dataset.extra:
            add_comparison(analysis, comp, direction=args.direction, alpha=args.alpha)
        finalize_comparisons(analysis, ni_margin=args.ni_margin)
    except LoadError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    json_to_stdout = args.json == "-"
    if json_to_stdout:
        # Machine-readable only: a report on stdout would corrupt the JSON stream.
        # And the stream must be UTF-8 whatever the terminal's locale is —
        # _make_output_safe() sets errors="replace" for the human report, which
        # under LC_ALL=ISO8859-1 would quietly turn Korean column names into "???"
        # while still exiting 0. Bytes go straight to the buffer instead.
        payload = analysis_to_json(analysis, __version__)
        buf = getattr(sys.stdout, "buffer", None)
        if buf is None:      # a captured/text-only stream (e.g. under pytest)
            sys.stdout.write(payload)
        else:
            sys.stdout.flush()
            buf.write(payload.encode("utf-8"))
            buf.flush()
    elif args.markdown:
        print(markdown_report(analysis))
    else:
        print(format_report(analysis, show_curve=not args.no_curve))

    for path, payload, what in (
        (args.json if not json_to_stdout else None,
         lambda: analysis_to_json(analysis, __version__), "전체 결과 JSON"),
        (args.plot_svg, lambda: roc_svg(analysis), "ROC 곡선 SVG"),
    ):
        if not path:
            continue
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload())
        except OSError as exc:
            print(f"오류: {what}를 쓸 수 없습니다: {exc}", file=sys.stderr)
            return 2
        if not json_to_stdout:
            print(f"[저장] {what} → {path}")

    if args.points_csv:
        try:
            with open(args.points_csv, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(points_csv_rows(analysis))
        except OSError as exc:
            print(f"오류: 절단점 CSV를 쓸 수 없습니다: {exc}", file=sys.stderr)
            return 2
        if not json_to_stdout:
            print(f"[저장] 모든 절단점 → {args.points_csv}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
