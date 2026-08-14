"""hrvkit 명령줄 인터페이스.

예시
----
단일 열 RR(ms):
    hrvkit examples/resting.csv

시간+값 형식에서 값 열 지정, JSON 출력:
    hrvkit examples/slow_breathing.csv --col rr_ms --json

순간 HR(bpm) 입력을 직접 지정:
    hrvkit my_hr.csv --unit bpm

기저 대 개입 짝지은 비교(BELL-001 워크플로):
    hrvkit baseline.csv intervention.csv --compare

여러 기록 일괄 요약(장치 검증 파이프라인) — CSV로:
    hrvkit subj*.csv --format csv > summary.csv
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, List, Optional, Sequence

from . import __version__
from .analyze import HRVResult, analyze_rr, flat_metrics
from .dataio import load_group_manifest, load_manifest, load_series
from .power import power_grid
from .report import (group_compare, group_compare_to_csv, metrics_to_csv,
                     paired_group, paired_group_to_csv, power_plan_groups,
                     power_plan_paired, power_plan_to_csv, render_batch_table,
                     render_comparison, render_group_compare,
                     render_paired_group, render_plan, render_power_plan,
                     render_text, render_windows, windows_to_csv)
from .window import DEFAULT_WINDOW_SEC, MIN_WINDOW_BEATS, analyze_windows


# --plan-n 상한. 이보다 크면 df 가 1e7 을 넘어 로그밀도 항이 배정밀도 유효
# 자릿수를 다 먹어 결과에 유의미한 자릿수가 남지 않습니다(1e19 에서는 exp 가
# OverflowError 로 죽었습니다).
_MAX_PLAN_N = 10_000_000

# --power 를 CSV 로 낼 때 두 표(통계 / 표본수 설계)는 열 구성이 달라 하나의
# CSV 로 합칠 수 없습니다. 주 통계표를 **먼저** 내보내(=`head -1` 이 주 헤더)
# 아래 구분선으로 나눕니다. 구분선은 '#' 로 시작해 이 저장소의 CSV 리더와
# 대부분의 도구가 주석으로 건너뜁니다.
_CSV_PLAN_DELIM = "\n# ---- sample-size planning (표본수 설계) ----"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hrvkit",
        description="심박변이도(HRV) 분석기 — RR/IBI(ms) 또는 순간 HR(bpm) CSV로부터 "
                    "이상박동 보정 후 시간영역·주파수영역(Welch/FFT 또는 Lomb–Scargle)·비선형(Poincaré/"
                    "SampEn/DFA) 지표를 계산합니다 (표준 라이브러리만). 여러 파일을 "
                    "주면 일괄 요약, --compare 로 짝지은 비교, --paired 로 짝지은 "
                    "코호트 통계, --groups 로 평행군(독립 2군) 비교, --window 로 "
                    "구간별 추이(추세 검정·SDANN)를 냅니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", nargs="*",
                   help="입력 CSV 파일 경로(1개 이상). --paired 사용 시 생략")
    p.add_argument("--col", default=None,
                   help="값 열 이름(또는 0-based 인덱스). 미지정 시 자동 추정.")
    p.add_argument("--unit", default="auto", choices=["auto", "ms", "s", "bpm"],
                   help="입력 단위 (기본 auto: 값의 중앙값으로 감지)")
    p.add_argument("--timestamps", action="store_true",
                   help="값 열을 누적 박동 발생시각으로 보고 차분하여 RR 계산")
    p.add_argument("--clean", default="interpolate",
                   choices=["interpolate", "remove", "none"],
                   help="이상박동 보정 방법 (기본 interpolate)")
    p.add_argument("--min-rr", type=float, default=300.0,
                   help="생리적 하한 RR(ms) (기본 300)")
    p.add_argument("--max-rr", type=float, default=2000.0,
                   help="생리적 상한 RR(ms) (기본 2000)")
    p.add_argument("--rel-thresh", type=float, default=0.2,
                   help="국소 중앙값 대비 상대 급변 임계값 (기본 0.2 = 20%%)")
    p.add_argument("--fs", type=float, default=4.0,
                   help="주파수영역 리샘플 주파수 Hz (기본 4)")
    p.add_argument("--nperseg", type=int, default=None,
                   help="Welch 구간 길이(표본, 2의 거듭제곱으로 내림). 기본은 기록의 "
                        "약 절반(상한 256 = fs 4 Hz 기준 64초). 구간이 길수록 저주파 "
                        "해상도가 좋아지지만 평균할 구간 수가 줄어 분산이 커집니다. "
                        "VLF(0.003–0.04 Hz)를 신뢰하려면 구간이 333초 이상이어야 "
                        "하므로 긴 기록에서 예: --nperseg 2048 (fs 4 Hz → 512초)")
    p.add_argument("--psd", default="welch", choices=["welch", "lomb"],
                   help="PSD 추정 방법 (기본 welch: 4 Hz 선형보간 후 Welch). "
                        "lomb = Lomb–Scargle 주기도 — **보간하지 않고** 박동 시각 "
                        "위에서 직접 적합합니다. 선형보간은 저역통과로 작용해 HF를 "
                        "과소추정하므로 **절대 파워(ms²·ln HF)를 논문에 실을 때** "
                        "가장 권장되고, --clean remove 로 지운 기록에서는 구멍을 "
                        "건너뜁니다. 기록을 구간으로 쪼개지 않아 긴 기록의 VLF도 "
                        "해상되지만, lomb 은 추세 제거를 하지 않아 느린 드리프트가 "
                        "VLF로 새어 들어갑니다(README 경고 참조). "
                        "--fs/--nperseg 는 lomb 에서 쓰이지 않습니다. "
                        "순수 파이썬이라 welch 보다 30–100배 느립니다"
                        "(20분 0.5초, 1시간 3초, 8시간 20초대)")
    p.add_argument("--ls-oversample", type=float, default=4.0, metavar="K",
                   help="Lomb–Scargle 주파수 격자 과표본 배수 (1 이상 32 이하, "
                        "기본 4). 격자 간격은 1/(K·기록길이) 이며, 과표본은 "
                        "해상도(1/기록길이)를 늘리지 않고 대역 경계 적분 오차만 "
                        "줄입니다. 클수록 느립니다. 격자점 4096개 상한에 걸리면 "
                        "자동으로 낮아지고 리포트에 **실제 적용된** 배수를 찍습니다")
    p.add_argument("--no-sampen", action="store_true",
                   help="표본 엔트로피(SampEn) 계산 생략")
    p.add_argument("--compare", action="store_true",
                   help="정확히 2개 파일을 기저 대 개입으로 짝지어 비교")
    p.add_argument("--paired", metavar="MANIFEST",
                   help="매니페스트 CSV(기저,개입[,라벨] 열)로 여러 피험자 코호트 "
                        "통계(Wilcoxon·효과크기·HL 신뢰구간·다중비교 보정)를 계산")
    p.add_argument("--groups", metavar="MANIFEST",
                   help="평행군(독립 2군) 매니페스트 CSV(파일,군[,피험자] 열)로 "
                        "군간 비교(Mann–Whitney·Hedges g·HL 이동량 CI·보정 p). "
                        "각 피험자가 한 군에만 속하는 설계(약물 대 위약)용. "
                        "**매니페스트에 먼저 나온 군이 기준(대조)** 이 되고 모든 "
                        "차이는 (나중 군 − 먼저 군) 방향입니다")
    p.add_argument("--window", type=float, nargs="?", metavar="SEC",
                   const=DEFAULT_WINDOW_SEC, default=None,
                   help="파일 1개를 SEC초 구간(epoch)으로 나눠 구간별 지표와 "
                        f"단조 추세(Mann–Kendall), SDANN·SDNN index 를 계산 "
                        f"(값 생략 시 {DEFAULT_WINDOW_SEC:.0f}초 = Task Force 표준)")
    p.add_argument("--step", type=float, default=None, metavar="SEC",
                   help="구간 시작 간격(초). 기본은 --window 와 동일(겹치지 않음). "
                        "작게 주면 슬라이딩 창이 되지만 구간이 독립이 아니라 "
                        "추세 p값이 낙관적이 되고 SDANN 은 생략됩니다")
    p.add_argument("--min-window-beats", type=int, default=MIN_WINDOW_BEATS,
                   metavar="N",
                   help=f"구간을 분석하기 위한 최소 박동 수 (기본 {MIN_WINDOW_BEATS})")
    p.add_argument("--alpha", type=float, default=0.05,
                   help="유의수준 — Hodges–Lehmann 신뢰구간은 1-alpha 수준으로, "
                        "유의성 판정도 이 값 기준 (기본 0.05 → 95%% CI)")
    p.add_argument("--power", action="store_true",
                   help="--paired/--groups 결과에 **다음(본)시험 표본수 설계** "
                        "블록을 덧붙입니다. 파일럿의 효과크기와 그 신뢰구간의 "
                        "0 쪽 경계(보수적) 두 가지로 필요 N 을 냅니다. "
                        "사후 검정력(observed power)은 p값의 단조함수라 "
                        "정보가 없으므로 계산하지 않습니다")
    p.add_argument("--target-power", type=float, default=0.80, metavar="P",
                   help="표본수 설계의 목표 검정력 (기본 0.80). --power 와 "
                        "--plan 에서 쓰입니다")
    p.add_argument("--dropout", type=float, default=0.0, metavar="FRAC",
                   help="예상 탈락률(0 이상 1 미만, 기본 0). 완료 인원 N 을 "
                        "⌈N/(1−FRAC)⌉ 로 올려 **모집 인원**을 함께 보고합니다")
    p.add_argument("--plan", action="store_true",
                   help="파일럿 파일 없이 **가정값만으로** 표본수를 설계합니다. "
                        "--delta 와 --sd 를 주면 목표 검정력별 필요 N 표를, "
                        "--plan-n 과 --sd 를 주면 그 N 에서 탐지 가능한 최소 "
                        "차이(MDD) 표를 냅니다 (둘 다 주면 검정력도 계산)")
    p.add_argument("--delta", type=float, default=None, metavar="D",
                   help="--plan: 탐지하려는 차이(원 단위, 예: RMSSD 8 ms)")
    p.add_argument("--sd", type=float, default=None, metavar="S",
                   help="--plan: 가정 표준편차. paired 설계면 **개인 내 "
                        "차이(post−pre)의 SD**, parallel 이면 군 내 합동 SD")
    p.add_argument("--plan-n", type=int, default=None, metavar="N",
                   help="--plan: 확보 가능한 표본수(parallel 이면 군당)")
    p.add_argument("--design", default="paired", choices=["paired", "parallel"],
                   help="--plan 의 설계 (기본 paired). --power 에서는 사용된 "
                        "분석 모드(--paired/--groups)로 자동 결정됩니다")
    p.add_argument("--format", default=None,
                   choices=["text", "json", "csv"],
                   help="출력 형식 (기본 text; --json 은 --format json 과 동일)")
    p.add_argument("--json", action="store_true",
                   help="JSON 출력 (--format json 의 단축)")
    p.add_argument("--version", action="version", version=f"hrvkit {__version__}")
    return p


def _json_safe(obj: Any) -> Any:
    """비유한 float(NaN/inf)을 문자열로 바꿔 표준 준수 JSON을 만듭니다.

    기본 json.dumps 는 NaN/Infinity 토큰을 내보내 엄격한 파서(JS/jq/serde)가
    거부합니다. CSV 출력과 동일하게 'NaN'/'inf'/'-inf' 문자열로 표기합니다.
    """
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _print_json(obj: Any) -> None:
    print(json.dumps(_json_safe(obj), ensure_ascii=False, indent=2,
                     allow_nan=False))


def _analyze_file(args, path: str) -> HRVResult:
    """한 파일을 로드·분석해 HRVResult 반환. 오류는 ValueError로 전파."""
    rr_ms, meta = load_series(path, col=args.col, unit=args.unit,
                              beat_times=args.timestamps)
    res = analyze_rr(
        rr_ms,
        source=path,
        unit=meta["unit"],
        clean_method=args.clean,
        fs=args.fs,
        min_rr=args.min_rr,
        max_rr=args.max_rr,
        rel_thresh=args.rel_thresh,
        nperseg=args.nperseg,
        do_sampen=not args.no_sampen,
        psd_method=args.psd,
        ls_oversample=args.ls_oversample,
    )
    if meta["n_dropped"]:
        res.warnings.append(
            f"{meta['n_dropped']}개 셀이 비수치/빈칸으로 무시되었습니다.")
    if meta.get("unit_note"):
        res.warnings.append(meta["unit_note"])
    if meta.get("column_note"):
        res.warnings.append(meta["column_note"])
    if meta.get("looks_like_timestamps"):
        res.warnings.append(
            "값이 누적 박동 발생시각처럼 보입니다. 간격(RR)이 아니라 발생시각이라면 "
            "--timestamps 를 붙이세요.")
    if meta.get("ragged"):
        res.warnings.append(
            "행마다 열 개수가 달라(ragged) 일부 행이 무시됐을 수 있습니다. "
            "--col 로 값 열을 명시하는 것을 권장합니다.")
    res._input_meta = meta  # type: ignore[attr-defined]
    return res


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    fmt = args.format or ("json" if args.json else "text")
    paths: List[str] = args.csv

    # 하한이 1 인 이유: 과표본은 대역 경계 적분 오차만 줄이는 장치라 1 미만은
    # 쓸 이유가 없고, 격자가 해상도보다 성글어져 대역 파워가 크게 틀어집니다
    # (측정: K=0.2 에서 HF 4배 과대·LF 30배 과소, 경고 없이 exit 0).
    if not (1.0 <= args.ls_oversample <= 32.0):
        print("입력 오류: --ls-oversample 은 1 이상 32 이하여야 합니다 "
              f"(받은 값: {args.ls_oversample:g}). 1 미만은 격자가 주파수 "
              "해상도보다 성글어져 대역 파워가 크게 틀어집니다.", file=sys.stderr)
        return 2

    if not (0.0 < args.alpha < 1.0):
        print(f"입력 오류: --alpha 는 0과 1 사이여야 합니다 (받은 값: {args.alpha:g}).",
              file=sys.stderr)
        return 2

    # 표본수 설계는 t 분위수 t_{1−α/2} 를 씁니다. α 가 배정밀도 해상도보다
    # 작으면 1−α/2 가 정확히 1.0 으로 반올림돼 분위수 계산이 실패합니다
    # (예전에는 --power 경로에서 트레이스백으로 죽었습니다). 정확검정 하한
    # 탐색도 α 가 극단적으로 작으면 비싸집니다.
    if (args.power or args.plan) and args.alpha < 1e-9:
        print(f"입력 오류: --alpha 가 너무 작습니다 (받은 값: {args.alpha:g}). "
              "표본수 설계에는 1e-9 이상이어야 합니다 — 그보다 작은 유의수준은 "
              "부동소수점 정밀도 안에서 t 분위수를 계산할 수 없습니다.",
              file=sys.stderr)
        return 2

    if not (0.0 < args.target_power < 1.0):
        print("입력 오류: --target-power 는 0과 1 사이여야 합니다 "
              f"(받은 값: {args.target_power:g}).", file=sys.stderr)
        return 2

    if not (0.0 <= args.dropout < 1.0):
        print("입력 오류: --dropout 은 0 이상 1 미만이어야 합니다 "
              f"(받은 값: {args.dropout:g}). 1 이면 모집 인원이 무한입니다.",
              file=sys.stderr)
        return 2

    # 분석 모드는 서로 배타적입니다. 조용히 하나를 무시하면 사용자는 자기가 요청한
    # 분석 대신 다른 분석 결과를 보고 있다는 것을 알아채지 못합니다.
    modes = [name for name, on in (("--paired", bool(args.paired)),
                                   ("--groups", bool(args.groups)),
                                   ("--compare", bool(args.compare)),
                                   ("--plan", bool(args.plan)),
                                   ("--window", args.window is not None)) if on]
    if len(modes) > 1:
        print(f"입력 오류: {', '.join(modes)} 는 함께 쓸 수 없습니다 "
              f"(한 번에 한 가지 분석 모드).", file=sys.stderr)
        return 2

    # --power 는 코호트 요약이 있어야 의미가 있습니다. 조용히 무시하면 사용자는
    # 표본수 블록을 기대하고 못 받은 채 결과만 보게 됩니다.
    if args.power and not (args.paired or args.groups):
        print("입력 오류: --power 는 --paired 또는 --groups 와 함께 써야 합니다 "
              "(코호트의 효과크기와 SD 가 있어야 표본수를 낼 수 있습니다). "
              "파일럿 없이 가정값으로 설계하려면 --plan 을 쓰세요.",
              file=sys.stderr)
        return 2

    # --plan 전용 인자를 --plan 없이 주면 조용히 무시하지 않고 알려 줍니다.
    if not args.plan:
        stray = [n for n, v in (("--delta", args.delta), ("--sd", args.sd),
                                ("--plan-n", args.plan_n)) if v is not None]
        if stray:
            print(f"입력 오류: {', '.join(stray)} 는 --plan 에서만 쓰입니다 "
                  "(파일럿 자료로 설계하려면 --paired/--groups 에 --power 를 "
                  "붙이세요). 설계 조건은 파일럿의 효과크기·SD 에서 나오므로 "
                  "여기서는 무시될 뿐입니다.", file=sys.stderr)
            return 2

    # ---- 가정값만으로 표본수 설계 (--plan) ----
    if args.plan:
        if paths:
            print("입력 오류: --plan 은 CSV 파일을 받지 않습니다 (가정값만으로 "
                  "설계합니다). 파일럿 자료로 설계하려면 --paired/--groups 에 "
                  "--power 를 붙이세요.", file=sys.stderr)
            return 2
        if args.sd is None:
            print("입력 오류: --plan 에는 --sd 가 필요합니다 "
                  "(paired 면 개인 내 차이의 SD, parallel 이면 군 내 합동 SD).",
                  file=sys.stderr)
            return 2
        if args.plan_n is not None and not (2 <= args.plan_n <= _MAX_PLAN_N):
            print(f"입력 오류: --plan-n 은 2 이상 {_MAX_PLAN_N:,} 이하여야 합니다 "
                  f"(받은 값: {args.plan_n}). 그보다 크면 자유도가 배정밀도 범위를 "
                  "넘어 계산이 무의미해집니다.", file=sys.stderr)
            return 2
        # NaN/inf 는 조용히 전부 '—' 인 표를 만들어 '설계 불가'처럼 보이게 합니다.
        for name, val in (("--delta", args.delta), ("--sd", args.sd)):
            if val is not None and not math.isfinite(val):
                print(f"입력 오류: {name} 는 유한한 수여야 합니다 (받은 값: {val}).",
                      file=sys.stderr)
                return 2
        try:
            grid = power_grid(delta=args.delta, sd=args.sd, n=args.plan_n,
                              design=args.design, alpha=args.alpha,
                              dropout=args.dropout,
                              target_power=args.target_power)
        except ValueError as exc:
            print(f"입력 오류: {exc}", file=sys.stderr)
            return 2
        if fmt == "json":
            _print_json({"mode": "plan", **grid})
        elif fmt == "csv":
            import csv as _csv
            w = _csv.writer(sys.stdout)
            w.writerow(["design", "alpha", "sd", "delta", "n", "dropout",
                        "target_power", "n_t", "n_nonparam", "n_exact_floor",
                        "n_recommended", "n_enrol", "mdd", "mdd_nonparam",
                        "power_at_n"])
            for r in grid["rows"]:
                w.writerow([grid["design"], args.alpha, args.sd,
                            "" if args.delta is None else args.delta,
                            "" if args.plan_n is None else args.plan_n,
                            args.dropout, r["target_power"],
                            r.get("n_t", ""), r.get("n_nonparam", ""),
                            r.get("n_exact_floor", ""),
                            r.get("n_recommended", ""), r.get("n_enrol", ""),
                            r.get("mdd", ""), r.get("mdd_nonparam", ""),
                            grid.get("power_at_n", "")])
        else:
            print(render_plan(grid))
        return 0

    # ---- 평행군(독립 2군) 비교 (--groups MANIFEST) ----
    if args.groups:
        try:
            rows = load_group_manifest(args.groups)
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(f"입력 오류: {exc}", file=sys.stderr)
            return 2
        order: List[str] = []
        for _, gname, _ in rows:
            if gname not in order:
                order.append(gname)
        a_label, b_label = order
        buckets: dict = {a_label: [], b_label: []}
        for fpath, gname, label in rows:
            try:
                buckets[gname].append(_analyze_file(args, fpath))
            except Exception as exc:  # noqa: BLE001
                tag = label or fpath
                print(f"입력/분석 오류: [{tag}] {exc}", file=sys.stderr)
                return 2
        a_res, b_res = buckets[a_label], buckets[b_label]
        plan = None
        if args.power:
            plan = power_plan_groups(a_res, b_res,
                                     target_power=args.target_power,
                                     alpha=args.alpha, dropout=args.dropout)
        if fmt == "json":
            g = group_compare(a_res, b_res, alpha=args.alpha)
            out = {"mode": "groups", "group_a": a_label,
                   "group_b": b_label, **g}
            if plan is not None:
                out["power_plan"] = plan
            _print_json(out)
        elif fmt == "csv":
            print(group_compare_to_csv(a_res, b_res, alpha=args.alpha), end="")
            if plan is not None:
                print(_CSV_PLAN_DELIM)
                print(power_plan_to_csv(plan), end="")
        else:
            print(render_group_compare(a_res, b_res, a_label=a_label,
                                       b_label=b_label, alpha=args.alpha))
            if plan is not None:
                print(render_power_plan(plan))
        return 0

    # ---- 짝지은 코호트 통계 (--paired MANIFEST) ----
    if args.paired:
        try:
            triples = load_manifest(args.paired)
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(f"입력 오류: {exc}", file=sys.stderr)
            return 2
        result_pairs = []
        for base_p, interv_p, label in triples:
            try:
                b = _analyze_file(args, base_p)
                v = _analyze_file(args, interv_p)
            except Exception as exc:  # noqa: BLE001
                tag = label or f"{base_p}|{interv_p}"
                print(f"입력/분석 오류: [{tag}] {exc}", file=sys.stderr)
                return 2
            result_pairs.append((b, v))
        plan = None
        if args.power:
            plan = power_plan_paired(result_pairs,
                                     target_power=args.target_power,
                                     alpha=args.alpha, dropout=args.dropout)
        if fmt == "json":
            g = paired_group(result_pairs, alpha=args.alpha)
            out = {"mode": "paired", **g}
            if plan is not None:
                out["power_plan"] = plan
            _print_json(out)
        elif fmt == "csv":
            print(paired_group_to_csv(result_pairs, alpha=args.alpha), end="")
            if plan is not None:
                print(_CSV_PLAN_DELIM)
                print(power_plan_to_csv(plan), end="")
        else:
            print(render_paired_group(result_pairs, alpha=args.alpha))
            if plan is not None:
                print(render_power_plan(plan))
        return 0

    if not paths:
        print("입력 오류: CSV 파일을 1개 이상 지정하거나 --paired 를 사용하세요.",
              file=sys.stderr)
        return 2

    # ---- 구간(epoch)별 추이 (--window SEC) ----
    if args.window is not None:
        if len(paths) != 1:
            print("입력 오류: --window 는 파일 1개에만 적용됩니다 "
                  f"({len(paths)}개가 주어졌습니다). 파일마다 따로 실행하세요.",
                  file=sys.stderr)
            return 2
        path = paths[0]
        try:
            rr_ms, meta = load_series(path, col=args.col, unit=args.unit,
                                      beat_times=args.timestamps)
            series = analyze_windows(
                rr_ms, window_sec=args.window, step_sec=args.step,
                min_beats=args.min_window_beats, source=path,
                clean_method=args.clean, fs=args.fs, min_rr=args.min_rr,
                max_rr=args.max_rr, rel_thresh=args.rel_thresh,
                nperseg=args.nperseg, do_sampen=not args.no_sampen,
                psd_method=args.psd, ls_oversample=args.ls_oversample)
        except Exception as exc:  # noqa: BLE001
            print(f"입력/분석 오류: {exc}", file=sys.stderr)
            return 2
        for key in ("unit_note", "column_note"):
            if meta.get(key):
                series.notes.append(meta[key])
        if meta.get("ragged"):
            series.notes.append(
                "행마다 열 개수가 달라(ragged) 일부 행이 무시됐을 수 있습니다. "
                "--col 로 값 열을 명시하는 것을 권장합니다.")
        if meta["n_dropped"]:
            series.notes.append(
                f"{meta['n_dropped']}개 셀이 비수치/빈칸으로 무시되었습니다.")
        if meta.get("looks_like_timestamps"):
            series.notes.append(
                "값이 누적 박동 발생시각처럼 보입니다 — 간격이 아니라면 "
                "--timestamps 를 붙이세요.")
        if fmt == "json":
            out = series.to_dict()
            out["mode"] = "window"
            out["input_meta"] = meta
            _print_json(out)
        elif fmt == "csv":
            print(windows_to_csv(series), end="")
        else:
            print(render_windows(series))
        return 0

    if args.compare and len(paths) != 2:
        print("입력 오류: --compare 는 정확히 2개의 파일이 필요합니다.",
              file=sys.stderr)
        return 2

    # 파일 분석 (하나라도 실패하면 어느 파일인지 표시).
    results: List[HRVResult] = []
    for path in paths:
        try:
            results.append(_analyze_file(args, path))
        except Exception as exc:  # noqa: BLE001 — 어떤 오류든 파일명과 함께 깔끔히 보고
            prefix = f"[{path}] " if len(paths) > 1 else ""
            print(f"입력/분석 오류: {prefix}{exc}", file=sys.stderr)
            return 2

    # ---- 짝지은 비교 ----
    if args.compare:
        base, interv = results
        if fmt == "json":
            out = {
                "mode": "compare",
                "baseline": {**base.to_dict(), "input_meta": base._input_meta},
                "intervention": {**interv.to_dict(),
                                 "input_meta": interv._input_meta},
            }
            _print_json(out)
        elif fmt == "csv":
            print(metrics_to_csv(results), end="")
        else:
            print(render_comparison(base, interv))
        return 0

    # ---- 여러 파일 일괄 요약 ----
    if len(results) > 1:
        if fmt == "json":
            out = {"mode": "batch",
                   "files": [{**r.to_dict(), "input_meta": r._input_meta}
                             for r in results]}
            _print_json(out)
        elif fmt == "csv":
            print(metrics_to_csv(results), end="")
        else:
            print(render_batch_table(results))
        return 0

    # ---- 단일 파일 ----
    res = results[0]
    if fmt == "json":
        out = res.to_dict()
        out["input_meta"] = res._input_meta
        _print_json(out)
    elif fmt == "csv":
        print(metrics_to_csv([res]), end="")
    else:
        print(render_text(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
