"""powerplan CLI — 설계별 하위 명령으로 표본수/검정력을 계산한다.

    powerplan ttest2 --d 0.5 --power 0.8            # 표본수 구하기
    powerplan ttest2 --d 0.5 --n 40                 # 확보 가능한 n의 검정력
    powerplan pilot data.csv --value isi --group arm --power 0.8

각 하위 명령은 공통 옵션(--alpha/--sides/--power/--n/--dropout/--cluster-*/
--comparisons/--sensitivity/--format)을 공유한다.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .designs import (
    CorrelationTest,
    EquivalenceT,
    NonInferiorityT,
    OneSampleT,
    OneWayAnova,
    PairedT,
    TwoProportions,
    TwoSampleT,
)
from .effects import cohen_d, cohen_f_from_means
from .pilot import effect_from_paired, effect_from_two_group, read_paired, read_two_group
from .precision import icc_plan, loa_plan
from .report import render_json, render_markdown, render_text
from .solve import Adjustments, make_plan, smallest_unit
from .validate import PowerPlanError, as_float, as_int, probability

_EPILOG = """
예시:
  powerplan ttest2 --d 0.5 --power 0.8 --dropout 0.15
  powerplan ttest2 --mean1 8 --mean2 5 --sd 6 --power 0.9 --sensitivity
  powerplan paired --dz 0.45 --power 0.8            # 전후 비교(대응표본)
  powerplan prop2 --p1 0.30 --p2 0.50 --power 0.8
  powerplan anova --k 3 --f 0.25 --power 0.8
  powerplan corr --r 0.35 --power 0.8
  powerplan noninf --margin 3 --sd 8 --power 0.8    # 비열등성
  powerplan equiv --margin 5 --sd 8 --power 0.8     # 동등성(TOST)
  powerplan icc --icc 0.8 --width 0.15 --raters 2   # 신뢰도 연구(정밀도)
  powerplan loa --sd-diff 2.0 --half-width 0.5      # Bland-Altman LoA
  powerplan pilot pilot.csv --value isi --group arm --power 0.8
  powerplan ttest2 --d 0.5 --n 30                   # 검정력만 확인

설계 고르기:
  두 군 평균 비교 → ttest2 | 전후(같은 사람) → paired | 기준값 대비 → onesample
  반응률(예/아니오) → prop2 | 3군 이상 → anova | 두 변수 관계 → corr
  "더 나쁘지 않다" → noninf | "같다" → equiv
  측정 일치도/신뢰도 연구 → icc, loa (검정력이 아니라 정밀도 기준)
"""


def _add_common(parser: argparse.ArgumentParser, sides: bool = True,
                ratio: bool = False) -> None:
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="유의수준 (기본 0.05)")
    if sides:
        parser.add_argument("--sides", type=int, default=2, choices=(1, 2),
                            help="양측(2, 기본) / 단측(1)")
    if ratio:
        parser.add_argument("--ratio", type=float, default=1.0,
                            help="2군/1군 배분비 (기본 1.0 = 1:1)")
    parser.add_argument("--power", type=float, default=None,
                        help="목표 검정력 (예: 0.8). 주면 표본수를 계산")
    parser.add_argument("--n", type=int, default=None,
                        help="확보 가능한 표본수(1군 기준). 주면 검정력을 계산")
    parser.add_argument("--dropout", type=float, default=0.0,
                        help="예상 탈락률 (예: 0.15 = 15%%)")
    parser.add_argument("--cluster-size", type=int, default=None,
                        help="군집 무작위배정: 군집당 인원 m")
    parser.add_argument("--cluster-icc", type=float, default=None,
                        help="군집 무작위배정: 군집내 상관 ICC")
    parser.add_argument("--comparisons", type=int, default=1,
                        help="다중비교 개수 (α 보정)")
    parser.add_argument("--alpha-method", default="bonferroni",
                        choices=("bonferroni", "sidak", "none"),
                        help="α 보정 방법 (기본 bonferroni)")
    parser.add_argument("--sensitivity", action="store_true",
                        help="가정이 틀렸을 때의 표본수/검정력 표를 함께 출력")
    parser.add_argument("--format", default="text", choices=("text", "md", "json"),
                        help="출력 형식 (기본 text)")
    parser.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    parser.add_argument("--force", action="store_true",
                       help="-o 로 지정한 파일이 이미 있어도 덮어쓰기")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="powerplan",
        description="임상연구 표본수·검정력 계산기 (외부 의존성 0 · 평균·ANOVA는 비중심 t/F 정확계산, 비율·상관·ICC·LoA는 근사)",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"powerplan {__version__}")
    sub = parser.add_subparsers(dest="design", metavar="설계")

    p = sub.add_parser("ttest2", help="두 독립군 평균 비교 (독립표본 t)")
    p.add_argument("--d", type=float, default=None, help="Cohen's d")
    p.add_argument("--mean1", type=float, default=None, help="1군 평균 (--d 대신)")
    p.add_argument("--mean2", type=float, default=None, help="2군 평균")
    p.add_argument("--sd", type=float, default=None, help="공통 표준편차")
    p.add_argument("--analysis", default="raw", choices=("raw", "ancova", "change"),
                   help="분석 방법: raw(추적값만) · ancova(기저값 보정) · change(변화량)")
    p.add_argument("--baseline-r", type=float, default=0.0,
                   help="기저값-추적값 상관 r (ancova/change에 필요)")
    _add_common(p, ratio=True)

    p = sub.add_parser("paired", help="대응표본(전후) 평균 비교")
    p.add_argument("--dz", type=float, default=None, help="Cohen's dz (변화량 평균/변화량 SD)")
    p.add_argument("--diff", type=float, default=None, help="예상 변화량 (--dz 대신)")
    p.add_argument("--sd-diff", type=float, default=None, help="변화량의 표준편차")
    _add_common(p)

    p = sub.add_parser("onesample", help="단일표본 평균 비교 (기준값 대비)")
    p.add_argument("--d", type=float, default=None, help="Cohen's d")
    p.add_argument("--mean", type=float, default=None, help="예상 평균")
    p.add_argument("--ref", type=float, default=None, help="기준값")
    p.add_argument("--sd", type=float, default=None, help="표준편차")
    _add_common(p)

    p = sub.add_parser("prop2", help="두 군 비율(반응률) 비교")
    p.add_argument("--p1", type=float, required=True, help="1군(대조) 비율")
    p.add_argument("--p2", type=float, required=True, help="2군(중재) 비율")
    p.add_argument("--continuity", action="store_true", help="연속성 보정 적용(보수적)")
    _add_common(p, ratio=True)

    p = sub.add_parser("anova", help="여러 군(k개) 평균 비교 (일원배치 ANOVA)")
    p.add_argument("--k", type=int, required=True, help="군 수 (≥2)")
    p.add_argument("--f", type=float, default=None, help="Cohen's f")
    p.add_argument("--means", default=None, help="군 평균들 (예: 8,6,5) — --f 대신")
    p.add_argument("--sd", type=float, default=None, help="군내 표준편차 (--means와 함께)")
    _add_common(p, sides=False)

    p = sub.add_parser("corr", help="상관계수 검정 (r ≠ 0)")
    p.add_argument("--r", type=float, required=True, help="예상 상관계수")
    p.add_argument("--bias-correct", action="store_true",
                   help="Fisher z 편향보정 (G*Power 정확법과 근사 일치)")
    _add_common(p)

    p = sub.add_parser("noninf", help="비열등성 검정 (두 군 평균, 단측)")
    p.add_argument("--margin", type=float, required=True, help="비열등성 마진 (원래 단위)")
    p.add_argument("--sd", type=float, required=True, help="표준편차")
    p.add_argument("--diff", type=float, default=0.0,
                   help="가정하는 실제 차이 (중재−대조, 기본 0)")
    p.add_argument("--lower-is-better", action="store_true",
                   help="결과지표가 낮을수록 좋은 경우 (ISI, 통증점수 등)")
    _add_common(p, sides=False, ratio=True)
    p.set_defaults(alpha=0.025)

    p = sub.add_parser("equiv", help="동등성 검정 (TOST, 두 군 평균)")
    p.add_argument("--margin", type=float, required=True, help="동등성 마진 ±(원래 단위)")
    p.add_argument("--sd", type=float, required=True, help="표준편차")
    p.add_argument("--diff", type=float, default=0.0, help="가정하는 실제 차이 (기본 0)")
    _add_common(p, sides=False, ratio=True)

    p = sub.add_parser("icc", help="ICC 신뢰도 연구 (신뢰구간 폭 기준)")
    p.add_argument("--icc", type=float, required=True, help="예상 ICC")
    p.add_argument("--width", type=float, required=True, help="목표 95%% CI 전체 폭")
    p.add_argument("--raters", type=int, default=2, help="대상자당 측정 횟수 k (기본 2)")
    p.add_argument("--alpha", type=float, default=0.05, help="1 − 신뢰수준 (기본 0.05)")
    p.add_argument("--format", default="text", choices=("text", "md", "json"))
    p.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    p.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")

    p = sub.add_parser("loa", help="Bland–Altman 일치한계 정밀도 기준 표본수")
    p.add_argument("--sd-diff", type=float, required=True, help="예상되는 차이의 표준편차")
    p.add_argument("--half-width", type=float, required=True, help="목표 LoA CI 반폭")
    p.add_argument("--alpha", type=float, default=0.05, help="1 − 신뢰수준 (기본 0.05)")
    p.add_argument("--format", default="text", choices=("text", "md", "json"))
    p.add_argument("-o", "--output", default=None, help="결과를 파일로 저장")
    p.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")

    p = sub.add_parser("pilot", help="사전연구 CSV → 효과크기 → 본연구 표본수")
    p.add_argument("csv", help="사전연구 데이터 CSV")
    p.add_argument("--value", default=None, help="결과변수 열 (두 군 비교)")
    p.add_argument("--group", default=None, help="군 열 (두 군 비교)")
    p.add_argument("--groups", default=None, help="비교할 두 군 이름 (예: sham,device)")
    p.add_argument("--pre", default=None, help="사전 측정 열 (전후 비교)")
    p.add_argument("--post", default=None, help="사후 측정 열 (전후 비교)")
    p.add_argument("--baseline", default=None,
                   help="기저값 열 (두 군 비교) — 기저값 상관 r을 추정해 ANCOVA 표본수도 제시")
    p.add_argument("--filter", action="append", default=None, metavar="열=값",
                   help="특정 행만 사용 (예: --filter 군=중재). 여러 번 지정 가능")
    p.add_argument("--plan-on", default="conservative",
                   choices=("conservative", "observed"),
                   help="표본수를 무엇으로 계획할지: conservative(신뢰구간 하한, 기본) "
                        "또는 observed(관측 효과크기 — 과소추정 위험)")
    p.add_argument("--conf", type=float, default=0.95, help="효과크기 신뢰수준 (기본 0.95)")
    p.add_argument("--skip-invalid", action="store_true",
                   help="숫자로 읽을 수 없는 값을 결측으로 처리")
    _add_common(p)
    return parser


def _adjustments(args) -> Adjustments:
    return Adjustments(
        dropout=getattr(args, "dropout", 0.0),
        cluster_size=getattr(args, "cluster_size", None),
        cluster_icc=getattr(args, "cluster_icc", None),
        comparisons=getattr(args, "comparisons", 1),
        alpha_method=getattr(args, "alpha_method", "bonferroni"),
    )


def _require_one_target(args) -> None:
    if args.power is None and args.n is None:
        raise PowerPlanError(
            "목표 검정력(--power 0.8) 또는 확보 가능한 표본수(--n 30) 중 하나를 지정하세요"
        )


#: --n 상한. 이보다 큰 표본수는 계획이 아니라 오타이며, 내부 로그 계산의
#: 상쇄오차 영역(자유도 1e12 이상)에 들어가 결과를 신뢰할 수 없다.
MAX_GIVEN_N = 10_000_000


def _validate_common(args) -> None:
    """설계별 검증 **이전에** 사용자 입력을 먼저 거른다.

    예전에는 α 검증이 설계 생성 시점에만 있어서, 다중비교 보정이 먼저 실행되며
    `--alpha 2 --alpha-method sidak`이 복소수를 만들어 트레이스백으로 죽었다.
    """
    if getattr(args, "alpha", None) is not None:
        probability("--alpha", args.alpha)
        # 다중비교 보정 전의 '전체' α도 0.5 미만이어야 한다 (보정 후만 검사하면
        # --alpha 0.99 --comparisons 100 같은 입력이 조용히 통과한다).
        # icc/loa의 신뢰수준에도 같은 기준을 적용한다 (α 0.9 = 10% 신뢰구간은 무의미).
        if args.alpha >= 0.5:
            raise PowerPlanError(
                f"--alpha: 0.5 이상은 의미가 없습니다 (받은 값: {args.alpha:g}). 보통 0.05를 씁니다"
            )
    if getattr(args, "power", None) is not None:
        probability("--power", args.power)
    if getattr(args, "conf", None) is not None:
        probability("--conf", args.conf)
    if getattr(args, "n", None) is not None:
        as_int("--n", args.n, minimum=1)
        if args.n > MAX_GIVEN_N:
            raise PowerPlanError(
                f"--n: {MAX_GIVEN_N:,}보다 큰 표본수는 지원하지 않습니다 (받은 값: {args.n:,})"
            )
    for name in ("d", "dz", "sd", "sd_diff", "mean", "mean1", "mean2", "ref",
                 "margin", "diff", "r", "p1", "p2", "f", "icc", "width", "half_width",
                 "baseline_r", "ratio"):
        value = getattr(args, name, None)
        if value is not None:
            as_float(f"--{name.replace('_', '-')}", value)


def _parse_filters(raw) -> list[tuple[str, str]]:
    """['군=중재', ...] → [('군', '중재'), ...]."""
    out = []
    for item in raw or ():
        if "=" not in item:
            raise PowerPlanError(
                f"--filter 는 '열=값' 형태여야 합니다 (받은 값: {item!r}). 예: --filter 군=중재"
            )
        column, value = item.split("=", 1)
        if not column.strip():
            raise PowerPlanError(f"--filter 의 열 이름이 비어 있습니다 (받은 값: {item!r})")
        out.append((column.strip(), value.strip()))
    return out


def _write_output(path: str, text: str, force: bool) -> None:
    """결과 저장 — 심볼릭 링크를 따라가지 않고, 기존 파일을 조용히 덮어쓰지 않는다.

    파일 권한은 0600으로 만든다 (환자 유래 통계가 담길 수 있으므로).
    """
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if force else os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise PowerPlanError(
            f"이미 있는 파일입니다: {path} — 덮어쓰려면 --force 를 붙이세요"
        ) from None
    except OSError as exc:
        raise PowerPlanError(
            f"결과를 저장할 수 없습니다: {path} ({exc.strerror})"
        ) from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError as exc:
        raise PowerPlanError(
            f"결과를 저장할 수 없습니다: {path} ({exc.strerror})"
        ) from None


def _make_design(args, alpha: float):
    """하위 명령 인자 → 설계 객체."""
    key = args.design
    if key == "ttest2":
        if args.d is None:
            if None in (args.mean1, args.mean2, args.sd):
                raise PowerPlanError(
                    "--d 를 주거나, --mean1/--mean2/--sd 세 개를 모두 주세요"
                )
            d = cohen_d(args.mean1, args.mean2, args.sd)
        else:
            d = args.d
        return TwoSampleT(d, alpha, args.sides, args.ratio,
                          getattr(args, "baseline_r", 0.0),
                          getattr(args, "analysis", "raw"))
    if key == "paired":
        if args.dz is None:
            if None in (args.diff, args.sd_diff):
                raise PowerPlanError(
                    "--dz 를 주거나, --diff(예상 변화량)와 --sd-diff(변화량 SD)를 함께 주세요"
                )
            if args.sd_diff <= 0:
                raise PowerPlanError("--sd-diff: 0보다 커야 합니다")
            dz = args.diff / args.sd_diff
        else:
            dz = args.dz
        return PairedT(dz, alpha, args.sides)
    if key == "onesample":
        if args.d is None:
            if None in (args.mean, args.ref, args.sd):
                raise PowerPlanError("--d 를 주거나, --mean/--ref/--sd 를 모두 주세요")
            d = cohen_d(args.mean, args.ref, args.sd)
        else:
            d = args.d
        return OneSampleT(d, alpha, args.sides)
    if key == "prop2":
        return TwoProportions(args.p1, args.p2, alpha, args.sides, args.ratio,
                              args.continuity)
    if key == "anova":
        if args.f is None:
            if args.means is None or args.sd is None:
                raise PowerPlanError("--f 를 주거나, --means(예: 8,6,5)와 --sd 를 함께 주세요")
            means = [m for m in (x.strip() for x in args.means.split(",")) if m]
            if len(means) != args.k:
                raise PowerPlanError(
                    f"--means의 값 개수({len(means)})가 --k({args.k})와 다릅니다"
                )
            f = cohen_f_from_means(means, args.sd)
        else:
            f = args.f
        return OneWayAnova(f, args.k, alpha)
    if key == "corr":
        return CorrelationTest(args.r, alpha, args.sides, args.bias_correct)
    if key == "noninf":
        return NonInferiorityT(args.margin, args.sd, args.diff, alpha, args.ratio,
                               args.lower_is_better)
    if key == "equiv":
        return EquivalenceT(args.margin, args.sd, args.diff, alpha, args.ratio)
    raise PowerPlanError(f"알 수 없는 설계: {key}")  # pragma: no cover


def _units_for(design, target_power: float) -> str:
    """이 설계에서 목표 검정력에 필요한 표본수를 짧은 문구로."""
    try:
        alloc = design.allocation(smallest_unit(design, target_power))
    except PowerPlanError:
        return "계산 불가"
    if "n1" in alloc:
        return f"군당 {alloc['n1']:,}명(총 {alloc['total']:,}명)"
    return f"{alloc['n']:,}명"


def _pilot_plan(args) -> tuple[dict, list[str]]:
    """사전연구 CSV → 관측 효과크기 요약 + 표본수 계획."""
    paired_mode = args.pre is not None or args.post is not None
    group_mode = args.value is not None or args.group is not None
    if paired_mode and group_mode:
        raise PowerPlanError(
            "전후 비교(--pre/--post)와 두 군 비교(--value/--group)는 함께 쓸 수 없습니다"
        )
    if not paired_mode and not group_mode:
        raise PowerPlanError(
            "두 군 비교는 --value/--group, 전후 비교는 --pre/--post 를 지정하세요"
        )
    adj = _adjustments(args)
    alpha, alpha_info = adj.adjusted_alpha(args.alpha)
    target_power = args.power if args.power is not None else 0.80
    filters = _parse_filters(args.filter)

    if paired_mode:
        if None in (args.pre, args.post):
            raise PowerPlanError("전후 비교에는 --pre 와 --post 를 모두 주세요")
        if args.baseline:
            raise PowerPlanError("--baseline 은 두 군 비교(--value/--group)에서만 씁니다")
        data = read_paired(args.csv, args.pre, args.post, args.skip_invalid, filters)
        observed = effect_from_paired(data, args.conf)
        effect_value, conservative = observed["dz"], observed["conservative_d"]
        design_at = lambda value: PairedT(value, alpha, args.sides)  # noqa: E731
    else:
        if None in (args.value, args.group):
            raise PowerPlanError("두 군 비교에는 --value 와 --group 을 모두 주세요")
        groups = None
        if args.groups:
            parts = [g.strip() for g in args.groups.split(",") if g.strip()]
            if len(parts) != 2:
                raise PowerPlanError("--groups 는 '군A,군B' 형태로 두 개만 지정하세요")
            if parts[0] == parts[1]:
                raise PowerPlanError(f"--groups 에 같은 군을 두 번 적었습니다: {parts[0]}")
            groups = tuple(parts)
        data = read_two_group(args.csv, args.value, args.group, groups, args.skip_invalid,
                              filters, args.baseline)
        observed = effect_from_two_group(data, args.conf)
        effect_value, conservative = observed["d"], observed["conservative_d"]
        design_at = lambda value: TwoSampleT(value, alpha, args.sides, 1.0)  # noqa: E731

    # 무엇으로 계획할지: 기본은 신뢰구간 하한(보수적). 하한이 0을 포함하면
    # 계획 자체가 불가능하므로 관측값으로 되돌리고 크게 경고한다.
    plan_on = args.plan_on
    ci_crosses_zero = conservative <= 0.0
    if plan_on == "conservative" and ci_crosses_zero:
        plan_on = "observed"
    planning_effect = conservative if plan_on == "conservative" else effect_value

    plan = make_plan(design_at(planning_effect), target_power=target_power,
                     unit=args.n, adjustments=adj, sensitivity=args.sensitivity,
                     alpha_adjustment=alpha_info)
    plan["pilot"] = {"data": data, "observed": observed, "planned_on": plan_on,
                     "planning_effect": planning_effect}

    extra: list[str] = []
    ci = observed["ci"]
    extra.append("")
    extra.append("■ 사전연구에서 관측된 효과크기")
    if data.get("filters"):
        extra.append("  선택 조건: " + ", ".join(
            f"{f['column']}={f['value']}" for f in data["filters"])
            + f" (제외된 행 {data.get('filtered_out', 0)}개)")
    if observed["kind"] == "two_group":
        g1, g2 = data["group1"], data["group2"]
        for g in (g1, g2):
            extra.append(f"  {g['label']:<20} n={g['n']:,}  평균={g['mean']:.4g}  "
                         f"SD={g['sd']:.4g}"
                         + (f"  범위 {g['min']:.4g}~{g['max']:.4g}" if g["n"] else "")
                         + (f"  결측={g['missing']}" if g["missing"] else ""))
        extra.append(f"  평균차 {observed['mean_diff']:.4g} · 합동 SD {observed['sd_pooled']:.4g}")
        extra.append(f"  Cohen's d = {observed['d']:.4f}"
                     + (f"  (Hedges g = {observed['hedges_g']:.4f})"
                        if observed["hedges_g"] is not None else ""))
        drop = g1["missing"] + g2["missing"]
        if drop:
            total_rows = g1["n"] + g2["n"] + drop
            extra.append(f"  결측/탈락 {drop}명 / 전체 {total_rows}명 = "
                         f"{drop / total_rows:.1%} → 본연구 --dropout 참고값")
        if data.get("baseline_r") is not None:
            extra.append(f"  기저값('{data['baseline_column']}')-추적값 군내 상관 r = "
                         f"{data['baseline_r']:.4f}"
                         f"  → ANCOVA 계획: --analysis ancova --baseline-r "
                         f"{data['baseline_r']:.3f}")
    else:
        d = data["diff"]
        extra.append(f"  쌍 n={d['n']:,}  변화량 평균={d['mean']:.4g}  변화량 SD={d['sd']:.4g}"
                     + (f"  (불완전 쌍 {data['incomplete_pairs']}개 제외)"
                        if data["incomplete_pairs"] else ""))
        extra.append(f"  Cohen's dz = {observed['dz']:.4f}"
                     + (f"  (Hedges g = {observed['hedges_g']:.4f})"
                        if observed["hedges_g"] is not None else ""))
        if data.get("pre_post_r") is not None:
            extra.append(f"  사전-사후 상관 r = {data['pre_post_r']:.4f}"
                         f"  → 두 군 비교를 계획한다면 --analysis ancova --baseline-r "
                         f"{data['pre_post_r']:.3f}")
    extra.append(f"  {ci['conf']:.0%} 신뢰구간(비중심 t 정확법): "
                 f"[{ci['low']:.4f}, {ci['high']:.4f}]")

    extra.append("")
    if plan_on == "conservative":
        extra.append(f"■ 계획 기준: **신뢰구간 하한** {conservative:.4f} (보수적, 기본값)")
        extra.append(f"  참고) 관측값 {effect_value:.4f}을 그대로 쓰면 표본수가 "
                     f"{_units_for(design_at(effect_value), target_power)}으로 줄지만, "
                     "사전연구 효과크기는 위로 편향되기 쉬워 위험합니다 (--plan-on observed).")
    elif ci_crosses_zero:
        extra.append("■ 계획 기준: 관측 효과크기 (⚠ 신뢰구간이 0을 포함)")
        extra.append("  사전연구만으로는 효과크기를 확정할 수 없습니다. 아래 표본수는 "
                     "참고용일 뿐이며, 임상적으로 의미있는 최소 차이(MCID)를 직접 정해 "
                     "ttest2/paired 로 다시 계산하세요.")
    else:
        extra.append(f"■ 계획 기준: 관측 효과크기 {effect_value:.4f} (--plan-on observed)")
        extra.append(f"  보수적 기준(신뢰구간 하한 {conservative:.4f})으로는 "
                     f"{_units_for(design_at(conservative), target_power)}이 필요합니다.")

    plan["notes"].insert(0,
        "사전연구의 관측 효과크기는 위로 편향되기 쉽습니다(승자의 저주). 기본값은 "
        "신뢰구간 하한으로 계획하는 것이며, 임상적으로 의미있는 최소 차이(MCID)가 있다면 "
        "그쪽이 더 좋습니다.")
    plan["notes"].append(
        "계획용 하한의 신뢰수준은 --conf 로 조절합니다. 문헌에서는 60~80% 하한을 쓰는 "
        "제안이 많습니다(Browne 1995; Kieser & Wassmer 1996) — 기본 0.95는 가장 보수적입니다.")
    plan["notes"].append(
        f"읽은 파일: {os.path.basename(data['path'])} (인코딩 {data['encoding']}, 구분자 "
        f"{data['delimiter']!r})")
    if data.get("invalid_ignored"):
        plan["notes"].append(
            f"--skip-invalid: 숫자로 읽을 수 없는 값 {data['invalid_ignored']}개를 "
            "결측으로 처리했습니다 — 원자료를 꼭 확인하세요")
    if data.get("other_groups"):
        others = ", ".join(
            f"{g['label']}(n={g['n']})" if "n" in g else f"{g['label']}(행 {g['rows']})"
            for g in data["other_groups"][:5])
        plan["notes"].append(f"비교에 쓰지 않은 군: {others}")
    return plan, extra


def main(argv: list[str] | None = None) -> int:
    # 한글을 표현할 수 없는 인코딩(PYTHONIOENCODING=ascii 등)에서도 죽지 않게
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):  # pragma: no cover - 리다이렉트된 스트림
            pass
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.design:
        parser.print_help()
        return 0
    try:
        _validate_common(args)
        extra: list[str] = []
        if args.design == "icc":
            plan = icc_plan(args.icc, args.width, args.raters, args.alpha)
        elif args.design == "loa":
            plan = loa_plan(args.sd_diff, args.half_width, args.alpha)
        elif args.design == "pilot":
            plan, extra = _pilot_plan(args)
        else:
            _require_one_target(args)
            adj = _adjustments(args)
            alpha, alpha_info = adj.adjusted_alpha(args.alpha)
            design = _make_design(args, alpha)
            plan = make_plan(design, target_power=args.power, unit=args.n,
                             adjustments=adj, sensitivity=args.sensitivity,
                             alpha_adjustment=alpha_info)
        if args.format == "json":
            text = render_json(plan)
        elif args.format == "md":
            text = render_markdown(plan)
        else:
            text = render_text(plan)
            if extra:
                # 사전연구 관측치를 먼저 보여주고, 그 뒤에 계획을 붙인다
                text = "\n".join(extra).lstrip("\n") + "\n\n" + text
        if args.output:
            _write_output(args.output, text, getattr(args, "force", False))
            print(f"저장했습니다: {args.output}")
        else:
            print(text)
        return 0
    except PowerPlanError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        # 분포 계산 계층이 범위를 벗어난 입력을 거절한 경우 (자유도·비중심모수 상한 등)
        print(f"오류(수치 범위): {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover - head/파이프 사용 시
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        print("중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
