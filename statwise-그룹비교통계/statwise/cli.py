"""Command-line interface for statwise.

Examples
--------
Long format (a value column and a group column):
    statwise data.csv --value score --group arm

Wide format (each column is a group):
    statwise data.csv --wide
    statwise data.csv --wide --columns control,treatment

Paired / repeated-measures (pre vs post on the same subjects):
    statwise data.csv --paired --value score --group time --id subject --baseline pre
    statwise wide_pairs.csv --paired --wide --columns post,pre

    The difference is always (first condition - second), so listing the baseline
    *second* -- "post,pre" -- gives the usual "change from baseline" sign.

Binary (yes/no) endpoint:
    statwise data.csv --binary --value responder --group arm --reference placebo

Equivalence / non-inferiority:
    statwise data.csv --value score --group arm --equivalence-margin 1.5
    statwise data.csv --value score --group arm --ni-margin 3 \
            --ni-direction higher_is_better

Several endpoints at once, corrected across the family:
    statwise data.csv --values isi,psqi,hrv --group arm

Covariate-adjusted comparison (ANCOVA) -- the usual primary analysis in an RCT:
    statwise data.csv --value isi_post --group arm --covariate isi_base \
            --reference placebo
    statwise data.csv --value isi_post --group arm --covariate isi_base,age \
            --adjust-factor site --reference placebo

Machine-readable output:
    statwise data.csv --wide --format json
    statwise data.csv --wide --format csv -o results.csv
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from .analyze import EquivalenceSpec, analyze, analyze_paired
from .ancova import AncovaRecord, run_ancova
from .binary import compare_binary
from .dataio import (load_ancova_long, load_binary_counts,
                     load_binary_long, load_binary_wide, load_long,
                     load_multi_long, load_paired_long, load_paired_wide,
                     load_wide, screen_group_labels, screen_values,
                     summarize_values)
from .endpoints import run_endpoints
from .equivalence import parse_margin
from .report import (render_ancova_json, render_ancova_text,
                     render_binary_json, render_binary_text, render_csv,
                     render_json, render_multi_csv, render_multi_json,
                     render_multi_text, render_text)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="statwise",
        description="그룹 비교 통계 자동 선택기 — 정규성/등분산 점검 후 t-검정·"
                    "Welch·Mann-Whitney·ANOVA·Welch-ANOVA·Kruskal-Wallis를 골라 "
                    "실행하고(+대응표본 paired), 효과크기와 논문용 문장까지 "
                    "출력합니다. 이진(yes/no) 결과(--binary: RD·RR·OR·NNT + "
                    "χ²/Fisher), 등가성·비열등성(--equivalence-margin/--ni-margin: "
                    "TOST), 여러 엔드포인트 동시 분석(--values), 공변량 "
                    "보정(--covariate/--adjust-factor: ANCOVA 보정평균·보정된 "
                    "군간 차이)도 같은 명령으로 처리합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="입력 CSV 파일 경로")
    p.add_argument("--value", help="(long 형식) 수치가 담긴 열 이름")
    p.add_argument("--group", help="(long 형식) 그룹 라벨이 담긴 열 이름")
    p.add_argument("--values", metavar="COL1,COL2,...",
                   help="(long 형식) 결과(endpoint) 열 여러 개를 한 번에 분석. "
                        "--group 과 함께 쓰며, 엔드포인트 간 다중비교 보정 후 "
                        "요약표를 출력합니다.")
    p.add_argument("--endpoint-correction", choices=["holm", "bh", "none"],
                   default="holm",
                   help="(--values) 엔드포인트 간 다중비교 보정 "
                        "(기본 holm, bh=FDR, none=보정 없음)")
    p.add_argument("--brief", action="store_true",
                   help="(--values) 요약표만 출력하고 엔드포인트별 상세 리포트는 생략")
    p.add_argument("--covariate", metavar="COL[,COL2,...]",
                   help="(long 형식) 공변량 보정 비교(ANCOVA). 기저값 등 수치형 "
                        "공변량 열을 지정하면 보정평균(LS mean)·보정된 평균차·"
                        "기울기 동질성 검정까지 계산합니다. 무작위배정 **전에** "
                        "측정된 변수만 넣으세요.")
    p.add_argument("--adjust-factor", metavar="COL[,COL2,...]",
                   help="(--covariate와 같은 ANCOVA 경로) 범주형 보정인자 열 "
                        "(예: 기관 site, 층화인자 stratum). 단독으로도 쓸 수 "
                        "있습니다.")
    p.add_argument("--wide", action="store_true",
                   help="wide 형식(각 열이 하나의 그룹)")
    p.add_argument("--columns",
                   help="(wide 형식) 사용할 열들, 쉼표로 구분 (미지정 시 전체 열)")
    p.add_argument("--paired", action="store_true",
                   help="대응 표본(paired) 분석: 같은 대상의 두 조건(예: 전/후) 비교. "
                        "long이면 --id 필요, wide면 2개 열(--columns)")
    p.add_argument("--id",
                   help="(paired long) 대상 식별자(subject id) 열 이름")
    p.add_argument("--reference", metavar="GROUP",
                   help="(독립 비교) 기준(대조) 그룹 이름. 이 그룹이 빼지는 쪽이 되어 "
                        "차이 = (다른 그룹 − 기준)으로 부호가 고정됩니다 "
                        "(이진 결과면 RD/RR/OR도 기준 대비로 계산).")
    p.add_argument("--baseline",
                   help="(paired) 기준(reference) 조건 이름. 차이 = (다른 조건 − 기준). "
                        "지정하면 CSV 행 순서와 무관하게 부호가 고정됩니다.")
    p.add_argument("--alpha", type=float, default=0.05,
                   help="유의수준 (기본 0.05)")
    p.add_argument("--alpha-norm", type=float, default=0.05,
                   help="정규성/등분산 판정용 유의수준 (기본 0.05)")
    p.add_argument("--correction", choices=["holm", "bh"], default="holm",
                   help="사후검정 다중비교 보정: holm(기본) 또는 bh(Benjamini-Hochberg FDR)")
    p.add_argument("--no-posthoc", action="store_true",
                   help="3그룹 이상에서 사후검정(post-hoc)을 하지 않음")
    p.add_argument("--binary", action="store_true",
                   help="이진(yes/no) 결과 분석: 반응률·위험차(RD)·위험비(RR)·"
                        "오즈비(OR)·NNT + 카이제곱/Fisher 정확검정")
    p.add_argument("--event-value", metavar="VALUE",
                   help="(--binary) '사건(event)'으로 셀 값. 미지정 시 1/0, Y/N, "
                        "yes/no, 유/무 등을 자동 인식")
    p.add_argument("--events-col", metavar="COL",
                   help="(--binary) 이미 집계된 표를 쓸 때 사건 수 열 이름 "
                        "(--n-col, --group 과 함께)")
    p.add_argument("--n-col", metavar="COL",
                   help="(--binary) 집계 표의 표본 수(n) 열 이름")
    p.add_argument("--event-is", choices=["benefit", "harm", "unspecified"],
                   default="unspecified",
                   help="(--binary) 세는 사건이 이로운 것(반응·완치)인지 해로운 "
                        "것(이상반응)인지. 지정해야 NNT/NNH를 올바른 이름으로 "
                        "표시합니다 (기본: 구분하지 않음)")
    p.add_argument("--test",
                   choices=["auto", "student", "welch", "mannwhitney"],
                   default="auto",
                   help="(연속형 2그룹) 검정을 사전 지정. auto(기본)는 정규성·"
                        "등분산을 먼저 검정해 고르지만, 그 자체가 자료 의존적 "
                        "선택이라 SAP에 사전 지정하려면 welch 등을 직접 고르세요.")
    p.add_argument("--binary-test",
                   choices=["auto", "chisq", "chisq-yates", "fisher"],
                   default="auto",
                   help="(--binary) 검정 선택: auto(기본, 기대빈도<5면 Fisher), "
                        "chisq, chisq-yates, fisher")
    p.add_argument("--equivalence-margin", metavar="Δ|low,high",
                   help="등가성(TOST) 검정 마진. '1.5'면 ±1.5, '-1.0,2.0'이면 "
                        "비대칭 구간. 평균차(A−B) 단위이며 임상적으로 정해야 합니다.")
    p.add_argument("--ni-margin", type=float, metavar="Δ",
                   help="비열등성(non-inferiority) 마진(양수). 허용 가능한 최대 열등 폭.")
    p.add_argument("--ni-direction",
                   choices=["higher_is_better", "lower_is_better"],
                   default=None,
                   help="(--ni-margin과 필수 동반) 결과값이 높을수록 좋은지 "
                        "낮을수록 좋은지. 방향을 틀리면 반대쪽 꼬리를 검정하게 "
                        "되므로 기본값을 두지 않았습니다.")
    p.add_argument("--format", choices=["text", "json", "csv"], default="text",
                   help="출력 형식: text(기본), json, 또는 csv "
                        "(csv = 비교 1건당 1행인 정돈된 결과표)")
    p.add_argument("--output", "-o", metavar="PATH",
                   help="결과를 화면 대신 파일로 저장 (UTF-8, 권한 0600; csv는 "
                        "엑셀이 한글을 바로 열도록 BOM 포함). 기존 파일은 "
                        "덮어쓰지 않습니다 — 필요하면 --overwrite")
    p.add_argument("--overwrite", action="store_true",
                   help="(--output) 기존 파일 덮어쓰기를 허용")
    p.add_argument("--delimiter",
                   help="CSV 구분자 강제 지정 (예: ';' 또는 '\\t'). 미지정 시 자동 감지")
    p.add_argument("--version", action="version",
                   version=f"statwise {__version__}")
    return p


def _resolve_delimiter(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    delim = {"\\t": "\t", "tab": "\t", "\\\\t": "\t"}.get(raw, raw)
    if len(delim) != 1:
        raise ValueError(
            f"--delimiter 는 문자 하나여야 합니다 (받은 값: {raw!r}). "
            f"탭은 '\\t' 또는 tab 으로 지정하세요.")
    return delim


def _split_columns(spec: Optional[str], flag: str = "--columns"
                   ) -> Optional[List[str]]:
    if not spec:
        return None
    cols = [c.strip() for c in spec.split(",") if c.strip()]
    if not cols:
        raise ValueError(
            f"{flag} {spec!r} 에서 사용할 수 있는 열 이름을 찾지 못했습니다 "
            f"(쉼표만 있는 값). 열 이름을 쉼표로 구분해 지정하세요.")
    dupes = sorted({c for c in cols if cols.count(c) > 1})
    if dupes:
        # Comparing a column with itself yields a guaranteed null result and a
        # publication-ready sentence naming the same group twice.
        raise ValueError(
            f"{flag} 에 같은 열이 여러 번 있습니다: " + ", ".join(dupes)
            + ". 같은 열을 두 번 분석할 수는 없습니다.")
    return cols


def _equivalence_spec(args: argparse.Namespace) -> Optional[EquivalenceSpec]:
    """Build an EquivalenceSpec from the CLI flags (None when not requested)."""
    for flag, val in (("--equivalence-margin", args.equivalence_margin),
                      ("--delimiter", args.delimiter),
                      ("--reference", args.reference),
                      ("--baseline", args.baseline),
                      ("--event-value", args.event_value),
                      ("--values", args.values),
                      ("--columns", args.columns),
                      ("--covariate", args.covariate),
                      ("--adjust-factor", args.adjust_factor),
                      ("--output", args.output)):
        if val is not None and str(val).strip() == "":
            raise ValueError(
                f"{flag} 에 빈 값이 들어왔습니다. 값을 지정하거나 옵션을 빼세요 "
                f"(빈 값을 조용히 무시하면 요청한 분석이 사라집니다).")
    if args.equivalence_margin and args.ni_margin is not None:
        raise ValueError("--equivalence-margin 과 --ni-margin 은 동시에 쓸 수 "
                         "없습니다 (등가 검정과 비열등성 검정 중 하나만 선택).")
    if args.equivalence_margin:
        return EquivalenceSpec(margin=parse_margin(args.equivalence_margin))
    if args.ni_margin is not None:
        if not (args.ni_margin > 0) or not math.isfinite(args.ni_margin):
            raise ValueError(
                f"--ni-margin 은 0보다 큰 유한한 값이어야 합니다 (받은 값: {args.ni_margin}).")
        if args.ni_direction is None:
            raise ValueError(
                "--ni-margin 과 함께 --ni-direction 을 반드시 지정하세요 "
                "(higher_is_better = 값이 높을수록 좋음, lower_is_better = "
                "낮을수록 좋음). 방향이 틀리면 반대쪽 꼬리를 검정하게 됩니다.")
        return EquivalenceSpec(ni_margin=args.ni_margin,
                               ni_direction=args.ni_direction)
    return None


def _apply_reference(named, reference: Optional[str]):
    """Move the reference group last so every contrast is (other − reference)."""
    if not reference:
        return named
    labels = [g for g, _ in named]
    if reference not in labels:
        raise ValueError(
            f"--reference '{reference}' 은(는) 데이터에 있는 그룹이 아닙니다. "
            f"그룹 {len(labels)}개 중 일부: {summarize_values(labels)}")
    return ([item for item in named if item[0] != reference] +
            [item for item in named if item[0] == reference])


def _run_multi(args: argparse.Namespace, delim: Optional[str],
               notes: List[str]) -> int:
    """Several endpoint columns at once, with across-endpoint multiplicity."""
    if args.paired:
        raise ValueError("--values 는 --paired 와 함께 쓸 수 없습니다. "
                         "대응 설계는 엔드포인트별로 따로 실행하세요.")
    if not args.group:
        raise ValueError("--values 를 쓰려면 --group 으로 그룹 열을 지정해야 합니다.")
    if args.eq_spec is not None:
        raise ValueError(
            "--values 와 등가/비열등성 마진은 함께 쓸 수 없습니다. 마진은 "
            "엔드포인트마다 단위와 임상적 의미가 달라(예: ISI 2점 vs RMSSD 2ms) "
            "하나의 값을 모든 결과에 적용하면 의미가 없습니다 — 엔드포인트별로 "
            "따로 실행하세요.")
    cols = _split_columns(args.values, "--values") or []
    if len(cols) < 2:
        raise ValueError(
            "--values 에는 결과 열을 2개 이상 지정하세요 (1개면 --value 를 쓰면 됩니다).")
    missing: Dict[str, Dict[str, int]] = {}
    datasets = load_multi_long(args.csv, cols, args.group, delim, notes,
                               missing_out=missing, binary=args.binary,
                               event_value=args.event_value)
    datasets = [(name, _apply_reference(named, args.reference))
                for name, named in datasets]
    if datasets:
        screen_group_labels([g for g, _ in datasets[0][1]], notes)
    if not args.binary:
        for name, named in datasets:
            for label, values in named:
                screen_values(f"{name}/{label}", values, notes)
    multi = run_endpoints(
        datasets, alpha=args.alpha, alpha_norm=args.alpha_norm,
        correction=args.endpoint_correction,
        posthoc_correction=args.correction, posthoc=not args.no_posthoc,
        binary=args.binary, binary_test=args.binary_test,
        equivalence=args.eq_spec, missing=missing, test=args.test,
        event_is=args.event_is)
    multi.warnings.extend(notes)
    if not multi.analysed:
        raise ValueError(
            "어떤 엔드포인트도 분석하지 못했습니다: "
            + "; ".join(f"{r.name}: {r.error}" for r in multi.failed))
    if args.format == "json":
        _write(render_multi_json(multi), args)
    elif args.format == "csv":
        _write(render_multi_csv(multi), args)
    else:
        _write(render_multi_text(multi, detail=not args.brief), args)
    return 0


def _run_binary(args: argparse.Namespace, delim: Optional[str],
                notes: List[str]) -> int:
    """Binary-endpoint pipeline: load counts, compare rates, print."""
    if args.paired:
        raise ValueError(
            "--binary 와 --paired 는 아직 함께 쓸 수 없습니다. 대응 이진 자료는 "
            "McNemar 검정이 필요하며 현재 지원하지 않습니다.")
    if args.equivalence_margin or args.ni_margin is not None:
        raise ValueError(
            "--binary 는 등가/비열등성 마진 옵션(--equivalence-margin, "
            "--ni-margin)을 아직 지원하지 않습니다 (비율 차이에 대한 TOST는 "
            "미구현).")
    missing: Dict[str, int] = {}
    if args.events_col or args.n_col:
        if not (args.events_col and args.n_col and args.group):
            raise ValueError("집계 표 입력에는 --events-col, --n-col, --group 을 "
                             "모두 지정해야 합니다.")
        named = load_binary_counts(args.csv, args.events_col, args.n_col,
                                   args.group, delim, notes)
    elif args.wide or (not args.value and not args.group):
        named = load_binary_wide(args.csv, _split_columns(args.columns),
                                 args.event_value, delim, notes,
                                 missing_out=missing)
    else:
        if not args.value or not args.group:
            raise ValueError("이진 long 형식에는 --value 와 --group 을 모두 "
                             "지정해야 합니다. (또는 --wide / --events-col)")
        named = load_binary_long(args.csv, args.value, args.group,
                                 args.event_value, delim, notes,
                                 missing_out=missing)

    named = _apply_reference(named, args.reference)
    usable = [(g, c) for g, c in named if c[1] >= 1]
    dropped = [g for g, c in named if c[1] < 1]
    if len(usable) < 2:
        raise ValueError(
            "분석 가능한 그룹이 2개 미만입니다 (각 그룹에 최소 1개 관측치 필요). "
            f"발견된 그룹 {len(named)}개 중 일부: "
            f"{summarize_values([g for g, _ in named])}")

    screen_group_labels([g for g, _ in named], notes)
    result = compare_binary(usable, alpha=args.alpha,
                            correction=args.correction,
                            posthoc=not args.no_posthoc,
                            test=args.binary_test, missing=missing,
                            event_is=args.event_is)
    if dropped:
        result.warnings.append(
            "관측치가 없어 제외된 그룹: " + ", ".join(dropped))
    result.warnings.extend(notes)
    if args.format == "json":
        _write(render_binary_json(result), args)
    elif args.format == "csv":
        _write(render_csv(result), args)
    else:
        _write(render_binary_text(result), args)
    return 0


def _run_paired(args: argparse.Namespace, delim: Optional[str],
                notes: List[str]) -> int:
    missing: Dict[str, int] = {}
    if args.wide or (not args.value and not args.group):
        cols = _split_columns(args.columns)
        cond_a, cond_b = load_paired_wide(args.csv, cols, delim, notes,
                                          baseline=args.baseline,
                                          missing_out=missing)
    else:
        if not args.value or not args.group or not args.id:
            raise ValueError("paired long 형식에는 --value, --group, --id 를 모두 "
                             "지정해야 합니다. (또는 --paired --wide --columns a,b)")
        cond_a, cond_b = load_paired_long(
            args.csv, args.value, args.group, args.id, delim, notes,
            baseline=args.baseline, missing_out=missing)

    screen_group_labels([cond_a[0], cond_b[0]], notes)
    screen_values(cond_a[0], cond_a[1], notes)
    screen_values(cond_b[0], cond_b[1], notes)
    screen_values(f"{cond_a[0]}−{cond_b[0]} 차이",
                  [x - y for x, y in zip(cond_a[1], cond_b[1])], notes)
    result = analyze_paired(cond_a, cond_b, alpha=args.alpha,
                            alpha_norm=args.alpha_norm,
                            equivalence=args.eq_spec, missing=missing)
    for n in notes:
        result.warnings.append(n)
    _emit(result, args)
    return 0


def _load(args: argparse.Namespace, delim: Optional[str], notes: List[str],
          missing: Dict[str, int]) -> List[Tuple[str, List[float]]]:
    if args.wide or (not args.value and not args.group):
        return load_wide(args.csv, _split_columns(args.columns), delim, notes,
                         missing_out=missing)
    if not args.value or not args.group:
        raise ValueError("long 형식에는 --value 와 --group 을 모두 지정해야 합니다. "
                         "(또는 --wide 사용)")
    return load_long(args.csv, args.value, args.group, delim, notes,
                     missing_out=missing)


def _write(text: str, args: argparse.Namespace) -> None:
    """Print to stdout, or save to --output (BOM for csv so Excel reads 한글)."""
    if not args.output:
        print(text)
        return
    try:
        if os.path.exists(args.output) and os.path.samefile(args.output, args.csv):
            raise ValueError(
                f"출력 파일이 입력 CSV와 같은 파일입니다 ('{args.output}'). "
                f"--overwrite 를 줬더라도 원본 데이터는 덮어쓰지 않습니다.")
    except OSError:
        pass
    encoding = "utf-8-sig" if args.format == "csv" else "utf-8"
    # Reports carry group labels that are often site/subject codes, and `-o`
    # aimed at the input file used to destroy it silently. Refuse to overwrite
    # unless asked, never follow a symlink, and create at 0600.
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if args.overwrite else os.O_EXCL
    try:
        fd = os.open(args.output, flags, 0o600)
    except FileExistsError:
        raise ValueError(
            f"'{args.output}' 파일이 이미 있습니다. 덮어쓰려면 --overwrite 를 "
            f"함께 쓰세요 (실수로 원본 데이터를 지우는 것을 막기 위한 안전장치입니다).")
    except OSError as exc:
        raise ValueError(f"결과 파일을 쓸 수 없습니다: {exc}")
    try:
        # The mode passed to os.open only applies when the file is created, so
        # an --overwrite onto an existing 0666 file kept 0666.
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
    except OSError as exc:
        raise ValueError(f"결과 파일을 쓸 수 없습니다: {exc}")
    print(f"결과를 '{args.output}' 에 저장했습니다.", file=sys.stderr)


def _emit(result, args: argparse.Namespace) -> None:
    if args.format == "json":
        _write(render_json(result), args)
    elif args.format == "csv":
        _write(render_csv(result), args)
    else:
        _write(render_text(result), args)


def _validate_alphas(args: argparse.Namespace) -> None:
    for flag, value in (("--alpha", args.alpha), ("--alpha-norm", args.alpha_norm)):
        if not (0.0 < value < 0.5) or value != value:
            raise ValueError(
                f"{flag} 는 0과 0.5 사이여야 합니다 (받은 값: {value}).")


def _reject_inapplicable_flags(args: argparse.Namespace) -> None:
    """Refuse flags that the chosen mode would silently ignore.

    Accepting ``--reference`` under ``--paired`` (or ``--baseline`` without it)
    and quietly doing nothing is worse than an error: the user believes they
    pinned the direction of the effect, and the sign they get is whatever the
    CSV row order happened to give.
    """
    if args.paired and args.reference:
        raise ValueError(
            "--reference 는 독립 그룹 비교용입니다. 대응(paired) 분석에서 기준 "
            "조건을 고정하려면 --baseline 을 쓰세요.")
    if args.baseline and not args.paired:
        raise ValueError(
            "--baseline 은 --paired 분석에서만 쓸 수 있습니다. 독립 그룹 비교에서 "
            "기준(대조)군을 고정하려면 --reference 를 쓰세요.")
    if args.values and args.value:
        raise ValueError(
            "--value 와 --values 를 동시에 쓸 수 없습니다 "
            "(하나면 --value, 여러 개면 --values).")
    if not args.binary:
        for flag, val in (("--event-value", args.event_value),
                          ("--events-col", args.events_col),
                          ("--n-col", args.n_col)):
            if val:
                raise ValueError(f"{flag} 은(는) --binary 와 함께만 쓸 수 있습니다.")
    if args.brief and not args.values:
        raise ValueError("--brief 는 --values 와 함께만 의미가 있습니다.")
    if args.id and not args.paired:
        # forgetting --paired while keeping --id analyses repeated measures as
        # if the arms were independent
        raise ValueError(
            "--id 는 --paired 분석에서만 쓰입니다. 같은 대상을 두 번 측정한 "
            "자료라면 --paired 를 함께 지정하세요 (빼면 독립표본으로 잘못 "
            "분석됩니다).")
    if args.binary_test != "auto" and not args.binary:
        raise ValueError("--binary-test 는 --binary 와 함께만 쓸 수 있습니다.")
    if args.endpoint_correction != "holm" and not args.values:
        raise ValueError(
            "--endpoint-correction 은 --values 와 함께만 의미가 있습니다.")
    if args.values and (args.wide or args.columns):
        raise ValueError(
            "--values 는 long 형식 전용입니다 (--wide/--columns 와 함께 쓸 수 "
            "없습니다).")
    if args.columns and args.paired and not args.wide and args.value:
        raise ValueError(
            "--columns 는 wide 형식 전용입니다 (--paired long 에서는 --value/"
            "--group/--id 를 씁니다).")
    if args.ni_direction is not None and args.ni_margin is None:
        raise ValueError("--ni-direction 은 --ni-margin 과 함께만 쓸 수 있습니다.")
    if args.event_is != "unspecified" and not args.binary:
        raise ValueError("--event-is 은(는) --binary 와 함께만 쓸 수 있습니다.")
    if args.test != "auto" and (args.binary or args.paired):
        raise ValueError(
            "--test 는 독립 2그룹 연속형 비교에만 적용됩니다 "
            "(이진 결과는 --binary-test 를 쓰세요).")
    if args.overwrite and not args.output:
        raise ValueError("--overwrite 는 --output 과 함께만 의미가 있습니다.")
    if args.covariate or args.adjust_factor:
        which = "--covariate" if args.covariate else "--adjust-factor"
        for flag, bad in (("--wide", args.wide), ("--paired", args.paired),
                          ("--binary", args.binary),
                          ("--columns", bool(args.columns)),
                          ("--values", bool(args.values))):
            if bad:
                raise ValueError(
                    f"{which} 는 {flag} 와 함께 쓸 수 없습니다. 공변량 보정은 "
                    f"한 행이 한 대상인 long 형식(--value/--group)에서만 "
                    f"정의됩니다.")
        if not args.value or not args.group:
            raise ValueError(
                f"{which} 를 쓰려면 --value(결과 열)와 --group(그룹 열)을 모두 "
                f"지정해야 합니다.")
        if args.test != "auto":
            raise ValueError(
                "--test 는 공변량 보정(ANCOVA)에 적용되지 않습니다 — 보정 모형은 "
                "항상 최소제곱 선형모형입니다.")
        if args.no_posthoc:
            raise ValueError(
                "--no-posthoc 는 공변량 보정(ANCOVA)에 적용되지 않습니다. "
                "보정된 쌍별 차이는 결과의 본체라 생략할 수 없습니다.")
    # An asymmetric equivalence margin is stated relative to a specific
    # direction; without a pinned reference the direction is whatever the CSV
    # row order gave, so the same margin can flip from "equivalent" to "not".
    asymmetric = bool(args.equivalence_margin
                      and "," in args.equivalence_margin)
    if (asymmetric or args.ni_margin is not None) and not (args.reference
                                                           or args.baseline):
        what = ("비대칭 등가 마진(low,high)" if asymmetric
                else "비열등성 마진(--ni-margin)")
        raise ValueError(
            f"{what}은 비교 방향이 고정되어야 의미가 있습니다 — CSV 행 순서가 "
            f"바뀌면 결론이 뒤집힙니다. --reference 기준군(독립 비교) 또는 "
            f"--baseline 기준조건(--paired)을 함께 지정하세요.")


def _run_continuous(args: argparse.Namespace, delim: Optional[str],
                    notes: List[str]) -> int:
    """The default pipeline: one continuous outcome across independent groups."""
    missing: Dict[str, int] = {}
    named = _apply_reference(_load(args, delim, notes, missing), args.reference)
    screen_group_labels([g for g, _ in named], notes)
    for label, values in named:
        screen_values(label, values, notes)

    # drop groups too small to analyze, but warn
    usable = [(g, v) for g, v in named if len(v) >= 2]
    dropped = [g for g, v in named if len(v) < 2]
    if len(usable) < 2:
        hint = ""
        if not args.wide and not args.value and not args.group:
            hint = ("\n힌트: 값 열과 그룹 열이 따로 있는 long 형식이라면 "
                    "'--value 값열 --group 그룹열' 을 지정하세요. "
                    "지금은 wide(각 열=그룹)로 해석했습니다.")
        raise ValueError(
            f"분석 가능한 그룹이 2개 미만입니다 (각 그룹 최소 2개 관측치 필요). "
            f"발견된 그룹 {len(named)}개 중 일부: "
            f"{summarize_values([g for g, _ in named])}{hint}")

    result = analyze(usable, alpha=args.alpha, alpha_norm=args.alpha_norm,
                     posthoc=not args.no_posthoc, correction=args.correction,
                     equivalence=args.eq_spec, missing=missing,
                     test=args.test)
    if dropped:
        result.warnings.append(
            "관측치 2개 미만으로 제외된 그룹: " + ", ".join(dropped))
        if args.reference and args.reference in dropped:
            raise ValueError(
                f"--reference '{args.reference}' 그룹은 관측치가 2개 미만이라 "
                f"분석에서 제외되었습니다 — 지정한 기준 대비 비교를 할 수 "
                f"없습니다. 기준군을 다시 정하거나 자료를 확인하세요.")
    result.warnings.extend(notes)
    _emit(result, args)
    return 0


def _run_ancova(args: argparse.Namespace, delim: Optional[str],
                notes: List[str]) -> int:
    """Covariate-adjusted comparison (ANCOVA) of one continuous endpoint."""
    covs = _split_columns(args.covariate, "--covariate") or []
    facs = _split_columns(args.adjust_factor, "--adjust-factor") or []
    missing: Dict[str, int] = {}
    records, dropped = load_ancova_long(args.csv, args.value, args.group, covs,
                                        facs, delim, notes,
                                        missing_out=missing)
    screen_group_labels([r[0] for r in records], notes)
    labels = []
    for r in records:
        if r[0] not in labels:
            labels.append(r[0])
    for lab in labels:
        screen_values(f"{args.value}/{lab}",
                      [r[1] for r in records if r[0] == lab], notes)
    for j, name in enumerate(covs):
        screen_values(name, [r[2][j] for r in records], notes)
    for j, name in enumerate(facs):
        screen_group_labels([r[3][j] for r in records], notes,
                            what=f"보정인자 '{name}'의 수준")
    if args.reference and args.reference not in labels:
        raise ValueError(
            f"--reference '{args.reference}' 은(는) 데이터에 있는 그룹이 "
            f"아닙니다. 그룹 {len(labels)}개 중 일부: {summarize_values(labels)}")
    result = run_ancova(
        [AncovaRecord(g, y, c, f) for g, y, c, f in records],
        covariate_names=covs, factor_names=facs, outcome=args.value,
        alpha=args.alpha, alpha_norm=args.alpha_norm,
        correction=args.correction, reference=args.reference,
        equivalence=args.eq_spec, missing=missing, n_dropped=dropped)
    result.warnings.extend(notes)
    if args.format == "json":
        _write(render_ancova_json(result), args)
    elif args.format == "csv":
        _write(render_csv(result), args)
    else:
        _write(render_ancova_text(result), args)
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    """Pick the pipeline for the requested mode. Raises ValueError on bad input."""
    _validate_alphas(args)
    args.eq_spec = _equivalence_spec(args)   # validates the margin values
    _reject_inapplicable_flags(args)         # then the mode/direction rules
    delim = _resolve_delimiter(args.delimiter)
    notes: List[str] = []
    if args.covariate or args.adjust_factor:
        return _run_ancova(args, delim, notes)
    if args.values:
        return _run_multi(args, delim, notes)
    if args.binary:
        return _run_binary(args, delim, notes)
    if args.paired:
        return _run_paired(args, delim, notes)
    return _run_continuous(args, delim, notes)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    # One error boundary for every mode: bad flags, unreadable files, degenerate
    # data and unwritable output all leave through here as "입력 오류" + exit 2,
    # never as a traceback.
    try:
        return _dispatch(args)
    except (ValueError, FileNotFoundError, IsADirectoryError,
            PermissionError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    except ArithmeticError as exc:
        # OverflowError / ZeroDivisionError from values around 1e300 or 1e-300
        detail = exc.args[-1] if exc.args else exc
        print(f"입력 오류: 값의 크기가 극단적이라 계산할 수 없습니다 ({detail}). "
              f"단위를 바꾸거나(예: ng -> mg) 자료 오류가 없는지 확인하세요.",
              file=sys.stderr)
        return 2
    except BrokenPipeError:            # `statwise ... | head`
        return 0
    except KeyboardInterrupt:
        print("중단되었습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
