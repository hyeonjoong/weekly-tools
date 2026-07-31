"""Command-line interface for agreestat.

Examples
--------
Two measurement columns (auto-detected if only two numeric columns exist)::

    agreestat data.csv --method-a sensor --method-b band

Percentage Bland-Altman + subject id for repeated measures::

    agreestat data.csv -a watch -b ecg --subject id --percent

Machine-readable output::

    agreestat data.csv -a sensor -b band --json

Categorical agreement (kappa family) instead of Bland-Altman/ICC::

    agreestat stages.csv --categorical -a psg_stage -b device_stage \
        --categories "W,N1,N2,N3,REM"

Ordinal scale (weighted kappa) with a pre-specified acceptance threshold::

    agreestat grades.csv --categorical --ordinal --min-kappa 0.6

Repeated units per subject (sleep epochs) -> cluster-robust CI::

    agreestat epochs.csv --categorical -a psg -b device -s subject

Three or more raters (full ICC family, SEM/MDC95, pairwise LoA)::

    agreestat sizes.csv --raters "reader1,reader2,reader3"

Three or more raters, categorical (Fleiss kappa / AC1 / Krippendorff alpha)::

    agreestat grades.csv --raters "r1,r2,r3" --categorical --ordinal

Long/tidy input (one row per measurement); 2 levels -> the A-vs-B report,
3+ levels -> the multi-rater report::

    agreestat long.csv --long --id-col subject --method-col reader \
        --value-col size_mm
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Optional, Sequence

from . import __version__
from .analyze import analyze
from .catanalyze import analyze_categorical
from .catreport import render_cat_json, render_cat_markdown, render_cat_text
from .dataio import load_categorical_pairs, load_pairs
from .report import (
    render_json,
    render_markdown,
    render_plot_data,
    render_svg,
    render_text,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agreestat",
        description="측정 방법 일치도(agreement) 분석기 — 두 방법/평가자(A vs B)의 "
                    "짝지은 측정값으로 일치도를 계산하고 논문용 문장까지 출력합니다. "
                    "[연속형] Bland–Altman, ICC(2,1)/ICC(3,1), Lin's CCC, 반복성, "
                    "Deming·Passing–Bablok 회귀, 상관/차이 검정. "
                    "[범주형 --categorical] Cohen's kappa·가중 kappa, Gwet's AC1/AC2, "
                    "Krippendorff's alpha, 범주별 일치도(PPA/NPA), kappa 역설 진단, "
                    "주변 동질성 검정, 군집(피험자) 보정 CI. "
                    "[평가자 3명 이상 --raters] ICC(1,1)~(3,k)·평가자 간 계통차이 "
                    "검정·SEM/MDC95·쌍별 LoA, 범주형이면 Fleiss' kappa·Gwet AC1·"
                    "Krippendorff alpha. [--long] 긴(tidy) 형식 CSV 입력.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV 파일 경로")
    p.add_argument("-a", "--method-a", dest="method_a",
                   help="방법 A(측정1) 열 이름 (미지정 시 자동 탐지)")
    p.add_argument("-b", "--method-b", dest="method_b",
                   help="방법 B(측정2/기준) 열 이름 (미지정 시 자동 탐지)")
    p.add_argument("-s", "--subject", dest="subject",
                   help="(선택) 피험자 ID 열 이름 — 반복측정 지표 계산에 사용")
    p.add_argument("--name-a", dest="name_a",
                   help="리포트에 표시할 방법 A 이름 (기본: 열 이름)")
    p.add_argument("--name-b", dest="name_b",
                   help="리포트에 표시할 방법 B 이름 (기본: 열 이름)")
    p.add_argument("--percent", action="store_true",
                   help="백분율 Bland–Altman (diff%% = 100·(A−B)/mean) — "
                        "비례오차가 있는 데이터에 적합")
    p.add_argument("--alpha", type=float, default=0.05,
                   help="유의수준 / 신뢰구간 폭 (기본 0.05 → 95%% CI)")
    p.add_argument("--accept", type=float, metavar="DELTA",
                   help="사전 설정한 임상 허용한계 ±DELTA. 95%% LoA가 "
                        "[−DELTA, +DELTA] 안이면 '교환가능' 판정을 출력")
    p.add_argument("--accept-lower", dest="accept_lower", type=float,
                   help="비대칭 허용한계의 하한 (--accept-upper와 함께 사용)")
    p.add_argument("--accept-upper", dest="accept_upper", type=float,
                   help="비대칭 허용한계의 상한 (--accept-lower와 함께 사용)")
    p.add_argument("--encoding",
                   help="입력 CSV 인코딩 (기본: 자동 감지 utf-8/utf-16/cp949/euc-kr)")
    p.add_argument("--target-loa-hw", dest="target_loa_hw", type=float,
                   metavar="H",
                   help="목표 LoA CI 반너비 H. 그 정밀도 달성에 필요한 표본수 n을 계산")
    p.add_argument("--deming-lambda", dest="deming_lambda", type=float,
                   default=1.0, metavar="L",
                   help="Deming 회귀의 오차분산비 λ=Var(err_기준B)/Var(err_검증A) "
                        "(기본 1.0 = 직교회귀). 값이 클수록 기준(B)의 오차가 크다고 가정")
    p.add_argument("--at", dest="decision_point", type=float, metavar="XC",
                   help="의학적 결정수준 XC(기준법 값)에서의 예측 계통편향 "
                        "bias(XC)=절편+(기울기−1)·XC 를 회귀로 추정 (CLSI EP09). "
                        "--accept와 함께 쓰면 그 지점 편향이 허용한계 안인지 판정")
    g = p.add_argument_group(
        "범주형 일치도 / categorical agreement",
        "두 평가자·기기가 '숫자'가 아니라 '범주'(수면단계, 등급, 양성/음성)를 "
        "매길 때 사용합니다. Cohen's kappa·가중 kappa·Gwet's AC1/AC2·"
        "Krippendorff's alpha·범주별 일치도·주변동질성 검정을 계산합니다.")
    g.add_argument("--categorical", action="store_true",
                   help="범주형 일치도(kappa) 분석 수행 "
                        "(Bland–Altman/ICC 대신)")
    g.add_argument("--ordinal", action="store_true",
                   help="범주가 순서형(예: 경증<중등도<중증)임을 표시 — "
                        "가중 kappa(기본 quadratic)를 계산")
    g.add_argument("--weights", choices=("unweighted", "linear", "quadratic"),
                   help="가중 kappa의 가중치 방식 (지정 시 --ordinal 자동 적용; "
                        "--ordinal 기본값은 quadratic)")
    g.add_argument("--categories", metavar="C1,C2,...",
                   help="범주의 순서를 직접 지정 (순서형에서 중요; "
                        "예: --categories \"W,N1,N2,N3,REM\")")
    g.add_argument("--min-kappa", dest="min_kappa", type=float, metavar="K",
                   help="사전 설정한 최소 허용 kappa. 신뢰구간 하한이 K 이상이면 "
                        "'기준 충족' 판정 (점추정이 아니라 CI 하한으로 판정)")
    g.add_argument("--na", dest="na", metavar="L1,L2,...",
                   help="결측으로 처리할 라벨 목록 (기본: 빈 칸과 #N/A만 결측). "
                        "범주형에서는 'None'·'-'·'.'·'NA' 가 실제 범주일 수 있어 "
                        "임의로 버리지 않습니다 — 결측이면 여기에 지정하세요")
    g.add_argument("--bootstrap", dest="bootstrap", type=int, default=2000,
                   metavar="B",
                   help="부트스트랩 재표본 수 (기본 2000). 2명 분석에서는 "
                        "-s/--subject 를 준 경우의 군집 보정 CI에, 평가자 3명 "
                        "이상(--raters)에서는 Fleiss kappa·AC1·alpha 의 CI에 "
                        "쓰입니다")
    g.add_argument("--seed", dest="seed", type=int, default=20260716,
                   metavar="S",
                   help="부트스트랩 난수 시드 (기본 20260716) — 같은 시드는 "
                        "항상 같은 CI를 주므로 논문 결과가 재현됩니다")
    m = p.add_argument_group(
        "다중 평가자 / 긴 형식 입력  (3명 이상, long format)",
        "평가자·방법이 3개 이상이거나, 자료가 '한 행 = 한 측정'인 긴(long/tidy) "
        "형식일 때 사용합니다. 연속형이면 ICC 전체 계열(1,1)~(3,k)·SEM·MDC95·"
        "쌍별 LoA, 범주형이면 Fleiss' kappa·Gwet AC1·Krippendorff alpha를 "
        "계산합니다.")
    m.add_argument("--raters", metavar="C1,C2,C3,...",
                   help="평가자/방법 열 이름을 쉼표로 나열 (3개 이상 → 다중 "
                        "평가자 분석). --long 과 함께 쓰면 분석에 포함할 "
                        "방법 이름(순서)을 뜻합니다")
    m.add_argument("--long", action="store_true",
                   help="입력이 긴(long/tidy) 형식임 — --id-col/--method-col/"
                        "--value-col 로 열을 지정하세요")
    m.add_argument("--id-col", dest="id_col", metavar="COL",
                   help="(--long) 대상 ID 열 이름")
    m.add_argument("--method-col", dest="method_col", metavar="COL",
                   help="(--long) 방법/평가자 이름이 들어 있는 열")
    m.add_argument("--value-col", dest="value_col", metavar="COL",
                   help="(--long) 측정값/판정 라벨이 들어 있는 열")
    p.add_argument("--json", action="store_true", help="JSON으로 출력")
    p.add_argument("--markdown", nargs="?", const="-", metavar="PATH",
                   help="결과를 마크다운 표로 출력(경로 생략 시 표준출력)")
    p.add_argument("--plot-data", dest="plot_data", metavar="PATH",
                   help="Bland–Altman 플롯 데이터(mean,diff)를 CSV로 저장")
    p.add_argument("--svg", metavar="PATH",
                   help="Bland–Altman 플롯을 SVG 파일로 저장")
    p.add_argument("--version", action="version",
                   version=f"agreestat {__version__}")
    return p


def _resolve_accept(args):
    """Turn --accept / --accept-lower / --accept-upper into (lo, hi) or None.

    Returns the string ``"error"`` on an invalid/incomplete specification
    (mixing forms, non-positive/non-finite delta, incomplete or lo>=hi pair).
    """
    lo, hi = args.accept_lower, args.accept_upper
    if args.accept is not None:
        if lo is not None or hi is not None:
            return "error"  # can't mix symmetric and asymmetric forms
        d = abs(args.accept)
        if d == 0.0 or not math.isfinite(d):
            return "error"
        return (-d, d)
    if lo is None and hi is None:
        return None
    if lo is None or hi is None:
        return "error"
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
        return "error"
    return (lo, hi)


_CAT_ONLY = (("--ordinal", "ordinal"), ("--weights", "weights"),
             ("--categories", "categories"), ("--min-kappa", "min_kappa"),
             ("--na", "na"))

_CONT_ONLY = (("--percent", "percent"), ("--accept", "accept"),
              ("--accept-lower", "accept_lower"),
              ("--accept-upper", "accept_upper"),
              ("--target-loa-hw", "target_loa_hw"), ("--at", "decision_point"),
              ("--plot-data", "plot_data"), ("--svg", "svg"))


def _check_mode_flags(args) -> Optional[str]:
    """Reject flags that belong to the other analysis mode."""
    if args.categorical:
        bad = [flag for flag, dest in _CONT_ONLY if getattr(args, dest)]
        if bad:
            return (f"{', '.join(bad)} 는 연속형(Bland–Altman/ICC) 분석 전용이라 "
                    "--categorical 과 함께 쓸 수 없습니다.")
        if args.deming_lambda != 1.0:
            return ("--deming-lambda 는 연속형 분석 전용이라 --categorical 과 "
                    "함께 쓸 수 없습니다.")
        return None
    bad = [flag for flag, dest in _CAT_ONLY if getattr(args, dest) not in (None, False)]
    if bad:
        return (f"{', '.join(bad)} 는 범주형 분석 전용입니다 — "
                "--categorical 을 함께 지정하세요.")
    return None


def _run_categorical(args) -> int:
    if _validate_cat_common(args) == "error":
        return 2
    na = _parse_na(args)
    if na == "error":
        return 2
    cats: Optional[Sequence[str]] = _parse_categories(args)
    if cats == "error":
        return 2

    try:
        data = load_categorical_pairs(args.csv, args.method_a, args.method_b,
                                      encoding=args.encoding,
                                      subject_col=args.subject, na_labels=na)
    except (ValueError, OSError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    return _report_categorical(args, data, cats)


def _report_categorical(args, data, cats) -> int:
    """Shared tail of every categorical path: analyze + render."""
    if data.n < 2:
        print(f"오류: 분석에 최소 2쌍이 필요합니다 (발견: {data.n}쌍).",
              file=sys.stderr)
        return 2

    try:
        res = analyze_categorical(
            data.a, data.b,
            name_a=args.name_a or data.name_a,
            name_b=args.name_b or data.name_b,
            alpha=args.alpha,
            categories=cats,
            ordinal=args.ordinal or args.weights not in (None, "unweighted"),
            weights=args.weights,
            dropped=data.dropped,
            min_kappa=args.min_kappa,
            extra_warnings=data.notes,
            subjects=data.subjects,
            bootstrap=args.bootstrap,
            seed=args.seed,
        )
    except (ValueError, OverflowError) as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        md = render_cat_markdown(res)
        if args.markdown == "-":
            print(md)
        else:
            try:
                with open(args.markdown, "w", encoding="utf-8") as fh:
                    fh.write(md)
                print(f"마크다운 표를 저장했습니다: {args.markdown}", file=sys.stderr)
            except OSError as exc:
                print(f"출력 오류: {exc}", file=sys.stderr)
                return 2
    elif args.json:
        print(render_cat_json(res))
    else:
        print(render_cat_text(res))
    return 0


_LONG_COLS = (("--id-col", "id_col"), ("--method-col", "method_col"),
              ("--value-col", "value_col"))


def _check_table_flags(args) -> Optional[str]:
    """Validate the --raters / --long flag combination."""
    if args.long:
        missing = [flag for flag, dest in _LONG_COLS if not getattr(args, dest)]
        if missing:
            return (f"--long 에는 {', '.join(missing)} 이(가) 필요합니다 "
                    "(예: --long --id-col subject --method-col rater "
                    "--value-col score).")
    else:
        given = [flag for flag, dest in _LONG_COLS if getattr(args, dest)]
        if given:
            return f"{', '.join(given)} 는 --long 과 함께 써야 합니다."
    if args.method_a or args.method_b:
        return ("-a/-b 는 두 열을 직접 고르는 옵션이라 --raters/--long 과 함께 "
                "쓸 수 없습니다. 분석할 방법은 --raters 로 지정하세요.")
    if args.subject:
        return ("-s/--subject 는 --raters/--long 과 함께 쓸 수 없습니다 "
                "(--long 에서는 --id-col 이 대상 ID 역할을 합니다).")
    return None


_MULTI_UNSUPPORTED = (("--percent", "percent"), ("--accept", "accept"),
                      ("--accept-lower", "accept_lower"),
                      ("--accept-upper", "accept_upper"),
                      ("--target-loa-hw", "target_loa_hw"),
                      ("--at", "decision_point"),
                      ("--plot-data", "plot_data"), ("--svg", "svg"))


def _emit(text: str, path: Optional[str]) -> int:
    """Print *text*, or write it to *path* ('-' means stdout)."""
    if path in (None, "-"):
        print(text)
        return 0
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        print(f"출력 오류: {exc}", file=sys.stderr)
        return 2
    print(f"마크다운 표를 저장했습니다: {path}", file=sys.stderr)
    return 0


def _run_rater_table(args) -> int:
    """Handle --raters / --long input (2 methods -> pairwise; 3+ -> multi)."""
    from .dataio import load_rater_table

    cols: Optional[Sequence[str]] = None
    if args.raters is not None:
        cols = [c.strip() for c in args.raters.split(",") if c.strip()]
        if len(cols) < 2:
            print("입력 오류: --raters 에는 쉼표로 구분된 이름이 2개 이상 "
                  "필요합니다 (예: --raters \"reader1,reader2,reader3\").",
                  file=sys.stderr)
            return 2
        if len(set(cols)) != len(cols):
            print("입력 오류: --raters 에 중복된 이름이 있습니다.",
                  file=sys.stderr)
            return 2

    try:
        table = load_rater_table(args.csv, cols, encoding=args.encoding,
                                 long_format=args.long, id_col=args.id_col,
                                 method_col=args.method_col,
                                 value_col=args.value_col)
    except (ValueError, OSError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    if table.k == 2:
        return _run_two_from_table(args, table)

    bad = [flag for flag, dest in _MULTI_UNSUPPORTED if getattr(args, dest)]
    if bad:
        print(f"입력 오류: {', '.join(bad)} 는 두 방법(A vs B) 전용이라 "
              f"평가자 {table.k}명 분석에는 쓸 수 없습니다. 두 방법만 "
              "비교하려면 --raters 로 2개만 지정하세요.", file=sys.stderr)
        return 2

    if args.categorical:
        return _run_multi_categorical(args, table)
    return _run_multi_continuous(args, table)


def _run_two_from_table(args, table) -> int:
    """Exactly two methods selected via --raters/--long: normal A-vs-B report."""
    from .dataio import (
        CategoricalData,
        PairedData,
        label_rater_rows,
        numeric_rater_rows,
    )

    if args.categorical:
        if _validate_cat_common(args) == "error":
            return 2
        na = _parse_na(args)
        if na == "error":
            return 2
        cats = _parse_categories(args)
        if cats == "error":
            return 2
        try:
            rows, dropped, notes = label_rater_rows(table, na)
        except ValueError as exc:
            print(f"입력 오류: {exc}", file=sys.stderr)
            return 2
        pairs = [(r[0], r[1]) for r in rows if r[0] != "" and r[1] != ""]
        dropped += len(rows) - len(pairs)
        data = CategoricalData([p[0] for p in pairs], [p[1] for p in pairs],
                               table.names[0], table.names[1], dropped,
                               table.notes + notes, None)
        return _report_categorical(args, data, cats)

    try:
        rows, _ids, dropped, nonfinite = numeric_rater_rows(table)
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    data = PairedData([r[0] for r in rows], [r[1] for r in rows], None,
                      table.names[0], table.names[1], dropped, nonfinite,
                      list(table.notes))
    return _report_continuous(args, data)


def _run_multi_continuous(args, table) -> int:
    from .dataio import numeric_rater_rows
    from .multirater import multi_continuous
    from .multireport import (
        render_multi_json,
        render_multi_markdown,
        render_multi_text,
    )

    try:
        rows, _ids, dropped, nonfinite = numeric_rater_rows(table)
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    notes = list(table.notes)
    if dropped:
        notes.append(f"모든 평가자의 값이 갖춰지지 않은 {dropped}건을 "
                     "제외했습니다(완전자료 분석) — ICC는 균형 자료를 "
                     "요구합니다.")
    if nonfinite:
        notes.append(f"무한대(inf)·비정상 수치가 있는 {nonfinite}건을 "
                     "제외했습니다.")
    if len(rows) < 2:
        print(f"오류: 모든 평가자가 측정한 대상이 {len(rows)}건뿐입니다 "
              "(최소 2건 필요).", file=sys.stderr)
        return 2
    try:
        res = multi_continuous(table.names, rows, alpha=args.alpha,
                               dropped=dropped, extra_warnings=notes)
    except (ValueError, OverflowError) as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        return _emit(render_multi_markdown(res), args.markdown)
    print(render_multi_json(res) if args.json else render_multi_text(res))
    return 0


def _validate_cat_common(args):
    """--min-kappa / --bootstrap bounds shared by every categorical path."""
    if args.min_kappa is not None and not (
            math.isfinite(args.min_kappa) and -1.0 <= args.min_kappa <= 1.0):
        print("입력 오류: --min-kappa 는 -1과 1 사이의 유한한 값이어야 합니다.",
              file=sys.stderr)
        return "error"
    if args.bootstrap < 100 or args.bootstrap > 200_000:
        print("입력 오류: --bootstrap 은 100 이상 200000 이하여야 합니다 "
              "(백분위 CI에는 최소 수백 회가 필요합니다).", file=sys.stderr)
        return "error"
    return None


def _parse_categories(args):
    """--categories into a list, or None; the string 'error' on bad input."""
    if not args.categories:
        return None
    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    if len(cats) < 2:
        print("입력 오류: --categories 에는 쉼표로 구분된 범주가 2개 이상 "
              "필요합니다 (예: --categories \"W,N1,N2,N3,REM\").",
              file=sys.stderr)
        return "error"
    if len(set(cats)) != len(cats):
        print("입력 오류: --categories 에 중복된 범주가 있습니다.",
              file=sys.stderr)
        return "error"
    return cats


def _parse_na(args):
    """--na into a list of labels, or None; the string 'error' on bad input."""
    if args.na is None:
        return None
    na = [s.strip() for s in args.na.split(",") if s.strip()]
    if not na:
        print("입력 오류: --na 에는 결측으로 처리할 라벨을 쉼표로 구분해 "
              "하나 이상 지정하세요 (예: --na \"NA,모름\").", file=sys.stderr)
        return "error"
    return na


def _run_multi_categorical(args, table) -> int:
    from .categorical import order_categories
    from .dataio import label_rater_rows
    from .multirater import multi_categorical
    from .multireport import (
        render_multicat_json,
        render_multicat_markdown,
        render_multicat_text,
    )

    if _validate_cat_common(args) == "error":
        return 2
    na = _parse_na(args)
    if na == "error":
        return 2
    explicit = _parse_categories(args)
    if explicit == "error":
        return 2

    try:
        rows, dropped, notes = label_rater_rows(table, na)
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    observed = [v for row in rows for v in row if v != ""]
    ordinal = bool(args.ordinal or args.weights not in (None, "unweighted"))
    try:
        cats, cat_notes = order_categories(observed,
                                           explicit if explicit else None)
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    if not ordinal:
        cat_notes = [n for n in cat_notes if "알파벳순" not in n]
    weights = args.weights or ("quadratic" if args.ordinal else "unweighted")
    if not ordinal:
        weights = "unweighted"

    all_notes = list(table.notes) + notes + cat_notes
    if dropped:
        all_notes.append(f"평가가 1개 이하인 {dropped}건은 제외했습니다.")
    try:
        res = multi_categorical(table.names, rows, cats, alpha=args.alpha,
                                ordinal=ordinal, weights=weights,
                                dropped=dropped, bootstrap=args.bootstrap,
                                seed=args.seed, min_kappa=args.min_kappa,
                                extra_warnings=all_notes)
    except (ValueError, OverflowError) as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        return _emit(render_multicat_markdown(res), args.markdown)
    print(render_multicat_json(res) if args.json else render_multicat_text(res))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if not 0.0 < args.alpha < 1.0:
        print("입력 오류: --alpha 는 0과 1 사이여야 합니다.", file=sys.stderr)
        return 2

    mode_err = _check_mode_flags(args)
    if mode_err:
        print(f"입력 오류: {mode_err}", file=sys.stderr)
        return 2

    # `args.raters is not None`, not truthiness: --raters "" must be rejected,
    # not silently fall back to two-column auto-detection.
    if (args.long or args.raters is not None
            or any(getattr(args, d) for _f, d in _LONG_COLS)):
        table_err = _check_table_flags(args)
        if table_err:
            print(f"입력 오류: {table_err}", file=sys.stderr)
            return 2
        return _run_rater_table(args)

    if args.categorical:
        return _run_categorical(args)

    if _validate_continuous(args) == "error":
        return 2

    try:
        data = load_pairs(args.csv, args.method_a, args.method_b, args.subject,
                          encoding=args.encoding)
    except (ValueError, OSError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    return _report_continuous(args, data)


def _validate_continuous(args):
    """Check every continuous-only numeric flag. Returns accept or ``"error"``."""
    if args.target_loa_hw is not None and not (
            math.isfinite(args.target_loa_hw) and args.target_loa_hw > 0.0):
        print("입력 오류: --target-loa-hw 는 0보다 큰 유한한 값이어야 합니다.",
              file=sys.stderr)
        return "error"
    if not (math.isfinite(args.deming_lambda) and args.deming_lambda > 0.0):
        print("입력 오류: --deming-lambda 는 0보다 큰 유한한 값이어야 합니다.",
              file=sys.stderr)
        return "error"
    if args.decision_point is not None and not math.isfinite(args.decision_point):
        print("입력 오류: --at 는 유한한 값이어야 합니다.", file=sys.stderr)
        return "error"
    accept = _resolve_accept(args)
    if accept == "error":
        print("입력 오류: 허용한계는 --accept DELTA 또는 "
              "--accept-lower/--accept-upper 쌍으로 지정하세요 (하한 < 상한).",
              file=sys.stderr)
        return "error"
    return accept


def _report_continuous(args, data) -> int:
    """Shared tail of every continuous A-vs-B path: analyze + render."""
    accept = _validate_continuous(args)
    if accept == "error":
        return 2
    if data.n < 2:
        print(f"오류: 분석에 최소 2쌍이 필요합니다 (발견: {data.n}쌍).",
              file=sys.stderr)
        return 2

    try:
        result = analyze(
            data.a, data.b, subjects=data.subjects,
            name_a=args.name_a or data.name_a,
            name_b=args.name_b or data.name_b,
            alpha=args.alpha,
            mode="percent" if args.percent else "absolute",
            dropped=data.dropped,
            accept=accept,
            nonfinite=data.nonfinite,
            extra_warnings=data.notes,
            target_loa_hw=args.target_loa_hw,
            deming_lambda=args.deming_lambda,
            decision_point=args.decision_point,
        )
    except (ValueError, OverflowError) as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    # Side-car file outputs (plot data / SVG); these don't replace the report.
    try:
        if args.plot_data:
            with open(args.plot_data, "w", encoding="utf-8") as fh:
                fh.write(render_plot_data(result))
            print(f"플롯 데이터를 저장했습니다: {args.plot_data}", file=sys.stderr)
        if args.svg:
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(render_svg(result))
            print(f"SVG 플롯을 저장했습니다: {args.svg}", file=sys.stderr)
    except OSError as exc:
        print(f"출력 오류: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        md = render_markdown(result)
        if args.markdown == "-":
            print(md)
        else:
            try:
                with open(args.markdown, "w", encoding="utf-8") as fh:
                    fh.write(md)
                print(f"마크다운 표를 저장했습니다: {args.markdown}", file=sys.stderr)
            except OSError as exc:
                print(f"출력 오류: {exc}", file=sys.stderr)
                return 2
    elif args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
