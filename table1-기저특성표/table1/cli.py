"""Command-line interface for table1.

Examples
--------
Auto-detect every variable, group by treatment arm:
    table1 baseline.csv --group arm

Pick specific variables and force a coded column to be categorical:
    table1 baseline.csv --group arm --vars age,sex,isi,bmi --categorical sex

Write a CSV you can paste into a manuscript:
    table1 baseline.csv --group arm --format csv -o table1.csv
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from . import __version__
from .build import Options, build_table1
from .dataio import load_frame
from .render import render


def _split(arg: Optional[str]) -> List[str]:
    if not arg:
        return []
    return [x.strip() for x in arg.split(",") if x.strip()]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="table1",
        description="임상 CSV → 출판용 '표 1'(기저 특성표). 변수별로 연속형/범주형을 "
                    "자동 판별해 알맞은 요약과 검정을 고르고, 표준화 평균차(SMD)와 "
                    "결측까지 정리해 Markdown/CSV/TSV/JSON으로 출력합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV 파일 경로")
    p.add_argument("--group", "-g", default=None,
                   help="그룹(군) 라벨이 담긴 열 이름 (예: arm, treatment). "
                        "생략하면 전체 코호트를 한 열로 요약하는 기술통계표"
                        "(검정·SMD·차이·보정 없음)를 만듭니다")
    p.add_argument("--vars",
                   help="요약할 변수 열들(쉼표 구분). 미지정 시 그룹 열을 뺀 전체")
    p.add_argument("--continuous",
                   help="연속형으로 강제할 열들(쉼표 구분)")
    p.add_argument("--categorical",
                   help="범주형으로 강제할 열들(쉼표 구분)")
    p.add_argument("--cat-max-levels", type=int, default=2,
                   help="수치형이라도 서로 다른 값이 이 개수 이하이면 범주형 취급 "
                        "(기본 2 = 0/1 같은 이진 플래그)")
    p.add_argument("--max-levels", type=int, default=20,
                   help="자동 판별된 범주형의 고유값이 이보다 많으면 ID/자유텍스트로 "
                        "보고 건너뜀 (기본 20)")
    p.add_argument("--display", choices=["auto", "mean", "median", "both"],
                   default="auto",
                   help="연속형 표기: auto(정규성 따라)·mean·median·both (기본 auto)")
    p.add_argument("--test-cont", choices=["auto", "welch", "student", "nonparam"],
                   default="auto",
                   help="연속형 검정 선택: auto(정규성/등분산 사전검정, 기본)·"
                        "welch(항상 Welch t)·student(항상 Student t)·"
                        "nonparam(항상 Mann-Whitney/Kruskal). 사전검정을 피하려면 "
                        "welch 권장(Delacre 2017)")
    p.add_argument("--pct", choices=["col", "row"], default="col",
                   help="범주형 %% 기준: col(그룹 내, 기본)·row(수준 내)")
    p.add_argument("--pct-decimals", type=int, default=1,
                   help="범주형 %% 소수 자릿수 (기본 1, 0~10)")
    p.add_argument("--binary-single", action="store_true",
                   help="2수준(이진) 범주형을 한 줄로 축약 표시 (예: 'sex = M — n(%%)')")
    p.add_argument("--ref", nargs="*", metavar="COL=수준", default=None,
                   help="이진 축약 시 기준(참조) 수준 지정. 표에는 반대 수준을 표시 "
                        "(예: --ref sex=F 이면 M 행을 표시)")
    p.add_argument("--alpha-norm", type=float, default=0.05,
                   help="정규성/등분산 판정 유의수준 (기본 0.05)")
    p.add_argument("--fisher", action="store_true",
                   help="2x2 범주형에 항상 Fisher exact 사용")
    p.add_argument("--missing-as-level", action="store_true",
                   help="범주형 결측을 '(결측)' 수준으로 표에 표시(검정에서는 제외)")
    p.add_argument("--no-overall", action="store_true",
                   help="'전체' 열을 표시하지 않음")
    p.add_argument("--no-pvalue", action="store_true",
                   help="p값 열 숨김 (무작위배정 임상시험은 CONSORT 권고상 기저 "
                        "p값 대신 SMD로 균형을 보고)")
    p.add_argument("--range", dest="range", action="store_true",
                   help="연속형 셀에 (최소–최대) 범위 추가")
    p.add_argument("--effect", action="store_true",
                   help="두 군 차이(95%% CI) 열 추가: 연속형은 평균차(모수)/"
                        "Hodges-Lehmann 중앙값차(비모수), 이진형은 위험차(%%p, "
                        "Newcombe). 2군 비교에만 적용")
    p.add_argument("--padjust", choices=["none", "bonferroni", "holm",
                                         "bh", "by"], default="none",
                   help="변수별 p값에 다중비교 보정 열 추가: none(기본)·bonferroni·"
                        "holm·bh(Benjamini-Hochberg)·by(Benjamini-Yekutieli). "
                        "무작위배정 시험의 기저 p값 보정은 비권장(비교/관찰 연구용)")
    p.add_argument("--lang", choices=["ko", "en"], default="ko",
                   help="표 라벨 언어: ko(기본)·en(영문 저널 제출용)")
    p.add_argument("--labels", nargs="*", metavar="COL=이름", default=None,
                   help="변수 표시 이름/단위 지정 (예: --labels rmssd_ms='RMSSD (ms)' "
                        "ahi='AHI (events/h)')")
    p.add_argument("--decimals", type=int, default=1,
                   help="연속형 통계 소수 자릿수 (기본 1, 음수 불가)")
    p.add_argument("--format", "-f", choices=["md", "csv", "tsv", "json", "html"],
                   default="md", help="출력 형식 (기본 md; html = Word/저널 붙여넣기용)")
    p.add_argument("--delimiter",
                   help="입력 CSV 구분자(미지정 시 자동 감지)")
    p.add_argument("-o", "--out", help="출력 파일 경로(미지정 시 화면 출력)")
    p.add_argument("--version", action="version",
                   version=f"table1 {__version__}")
    return p


def _parse_labels(items: Optional[Sequence[str]], flag: str = "--labels") -> dict:
    """Parse ``COL=value`` pairs into a mapping."""
    out: dict = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{flag} 항목은 'COL=값' 형식이어야 합니다: {item!r}")
        key, val = item.split("=", 1)
        key = key.strip()
        if key:
            out[key] = val.strip()
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    # Cap decimals: a huge value would zero-pad every number to millions of digits
    # (unbounded output/memory) with no analytic benefit past ~15 significant figs.
    if not 0 <= args.decimals <= 15:
        print("입력 오류: --decimals 는 0~15 사이여야 합니다.", file=sys.stderr)
        return 2
    if not 0 <= args.pct_decimals <= 10:
        print("입력 오류: --pct-decimals 는 0~10 사이여야 합니다.", file=sys.stderr)
        return 2
    if not 0.0 < args.alpha_norm < 1.0:
        print("입력 오류: --alpha-norm 은 0과 1 사이여야 합니다 (예: 0.05).",
              file=sys.stderr)
        return 2
    try:
        labels = _parse_labels(args.labels)
        ref = _parse_labels(args.ref, "--ref")
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    # Friendly delimiter aliases: a user typing --delimiter "\t" from a shell
    # passes the two literal characters backslash+t, and "tab" is a natural way
    # to ask for a tab. Map these before the strict one-character check.
    delimiter = args.delimiter
    if delimiter is not None:
        delimiter = {"\\t": "\t", "tab": "\t", "TAB": "\t",
                     "\\|": "|"}.get(delimiter, delimiter)
    try:
        frame = load_frame(args.csv, delimiter=delimiter)
    except FileNotFoundError:
        print(f"입력 오류: 파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print(f"입력 오류: '{args.csv}' 은(는) 폴더입니다. CSV 파일을 지정하세요.",
              file=sys.stderr)
        return 2
    except PermissionError:
        print(f"입력 오류: 파일을 읽을 권한이 없습니다: {args.csv} "
              "(엑셀 등에서 열려 있지 않은지 확인하세요).", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # Any other filesystem/IO error (bad descriptor, I/O error, ...) — never
        # let a raw traceback reach the researcher.
        print(f"입력 오류: 파일을 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 2

    opt = Options(
        group_col=args.group,   # None -> single-group descriptive table
        var_cols=_split(args.vars) or None,
        continuous=_split(args.continuous),
        categorical=_split(args.categorical),
        cat_max_levels=args.cat_max_levels,
        max_display_levels=args.max_levels,
        display=args.display,
        test_cont=args.test_cont,
        alpha_norm=args.alpha_norm,
        force_fisher=args.fisher,
        pct=args.pct,
        pct_decimals=args.pct_decimals,
        missing_as_level=args.missing_as_level,
        binary_single=args.binary_single,
        ref=ref,
        overall=not args.no_overall,
        decimals=args.decimals,
        show_pvalue=not args.no_pvalue,
        show_range=args.range,
        effect=args.effect,
        padjust=args.padjust,
        lang=args.lang,
        labels=labels,
    )
    try:
        table = build_table1(frame, opt)
    except ValueError as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2
    except OverflowError:
        # Astronomically-large magnitudes (~1e308) overflow a sum-of-squares.
        # Never let the traceback reach the researcher; ask them to rescale.
        print("분석 오류: 값의 크기가 너무 커서 계산할 수 없습니다"
              "(예: 1e308 규모). 단위를 바꿔 값을 축소한 뒤 다시 실행하세요.",
              file=sys.stderr)
        return 2

    text = render(table, opt, fmt=args.format)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + ("\n" if not text.endswith("\n") else ""))
        except OSError as exc:
            print(f"출력 오류: {exc}", file=sys.stderr)
            return 2
        print(f"저장했습니다: {args.out}  ({len(table.rows)}개 변수, "
              f"{len(table.groups)}개 군)", file=sys.stderr)
    else:
        print(text)

    if table.warnings and not args.out:
        pass  # warnings already embedded in md/json; keep stdout clean for csv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
