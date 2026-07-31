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
from .report import (group_compare, group_compare_to_csv, metrics_to_csv,
                     paired_group, paired_group_to_csv, render_batch_table,
                     render_comparison, render_group_compare,
                     render_paired_group, render_text, render_windows,
                     windows_to_csv)
from .window import DEFAULT_WINDOW_SEC, MIN_WINDOW_BEATS, analyze_windows


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hrvkit",
        description="심박변이도(HRV) 분석기 — RR/IBI(ms) 또는 순간 HR(bpm) CSV로부터 "
                    "이상박동 보정 후 시간영역·주파수영역(Welch/FFT)·비선형(Poincaré/"
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

    if not (0.0 < args.alpha < 1.0):
        print(f"입력 오류: --alpha 는 0과 1 사이여야 합니다 (받은 값: {args.alpha:g}).",
              file=sys.stderr)
        return 2

    # 분석 모드는 서로 배타적입니다. 조용히 하나를 무시하면 사용자는 자기가 요청한
    # 분석 대신 다른 분석 결과를 보고 있다는 것을 알아채지 못합니다.
    modes = [name for name, on in (("--paired", bool(args.paired)),
                                   ("--groups", bool(args.groups)),
                                   ("--compare", bool(args.compare)),
                                   ("--window", args.window is not None)) if on]
    if len(modes) > 1:
        print(f"입력 오류: {', '.join(modes)} 는 함께 쓸 수 없습니다 "
              f"(한 번에 한 가지 분석 모드).", file=sys.stderr)
        return 2

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
        if fmt == "json":
            g = group_compare(a_res, b_res, alpha=args.alpha)
            _print_json({"mode": "groups", "group_a": a_label,
                         "group_b": b_label, **g})
        elif fmt == "csv":
            print(group_compare_to_csv(a_res, b_res, alpha=args.alpha), end="")
        else:
            print(render_group_compare(a_res, b_res, a_label=a_label,
                                       b_label=b_label, alpha=args.alpha))
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
        if fmt == "json":
            g = paired_group(result_pairs, alpha=args.alpha)
            _print_json({"mode": "paired", **g})
        elif fmt == "csv":
            print(paired_group_to_csv(result_pairs, alpha=args.alpha), end="")
        else:
            print(render_paired_group(result_pairs, alpha=args.alpha))
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
                nperseg=args.nperseg, do_sampen=not args.no_sampen)
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
