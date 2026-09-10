"""rsalink.cli — 명령줄 진입점.

    rsalink 호흡.csv RR.csv --baseline 0:00-5:00 --stimulus 5:00-15:00 --pace 6 --out-dir 결과
    rsalink 호흡.csv RR.csv --segments 조건.csv --resp-offset 1.5 --out-dir 결과
    rsalink 호흡.csv RR.csv --inspect

exit 0 정상 / 2 입력·인자 오류(파일 없음, 형식, --out-dir 이 파일, 권한, 심볼릭/하드링크 타깃,
옵션 값 nan/inf/범위 밖)
/ 3 신뢰 불가(유효 호흡 <60% 또는 사용 가능 구간 <2분 또는 RR 제외 >20% 또는 호흡수 CV >60% /
|중앙값−평균|/중앙값 >25%) — 리포트는 그래도 출력.
네트워크 0, 입력은 읽기만, 산출물은 --out-dir 안에만.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from typing import List, Optional

from . import RsalinkError, __version__
from .breath import DEFAULT_K_MAD, detect_breaths, rate_stability
from .parse import RespEvents, RespWaveform, parse_resp, parse_rr
from .report import Coverage, build_report, prepare_out_dir, write_artifacts
from .rsa import DEFAULT_RSA_LAG_S, rsa_per_breath, summarize
from .segments import analyze_segments, build_segments, clip_segments, compare_all
from .spectral import suggest_offset

EXIT_OK, EXIT_INPUT, EXIT_UNRELIABLE = 0, 2, 3


class _Parser(argparse.ArgumentParser):
    """인자 오류를 트레이스백·영문 usage 대신 한국어 한 줄 + exit 2 로(라운드 2 #9). --help/--version 은 그대로."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise RsalinkError(f"명령줄 인자 오류: {message} — `rsalink --help` 로 형식을 확인하세요")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="rsalink",
        description="호흡 신호 + RR 간격 연동 분석: RSA peak-valley, 고정 HF vs 호흡중심 대역, 결맞음, "
                    "페이싱 동조, 조건 비교. 인과를 주장하지 않는다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit 0 정상 / 2 입력·인자 오류 / 3 신뢰 불가(유효 호흡 <60%%, 사용 구간 <2분, RR 제외 >20%%, "
               "호흡수 CV >60%% 또는 |중앙값−평균|/중앙값 >25%%)")
    p.add_argument("resp", help="호흡 CSV: timestamp,value 파형 또는 timestamp 한 열(흡기 시작 이벤트)")
    p.add_argument("rr", help="RR/IBI CSV (hrvkit 규격: ms/s/bpm 자동, 값 열 하나)")
    p.add_argument("--baseline", metavar="M:SS-M:SS", help="기준선 구간")
    p.add_argument("--stimulus", metavar="M:SS-M:SS", help="자극 구간")
    p.add_argument("--segments", metavar="CSV", help="구간 CSV (start,end,label) — --baseline/--stimulus 와 배타")
    p.add_argument("--pace", type=float, metavar="N", help="목표 호흡수(회/분) — 동조율 계산")
    p.add_argument("--out-dir", metavar="DIR", help="산출물 폴더(연동리포트.md, 호흡별.csv, 조건비교.csv). 없으면 터미널만")
    p.add_argument("--rr-col", metavar="NAME|IDX", help="RR 값 열 지정")
    p.add_argument("--resp-offset", type=float, default=0.0, metavar="SEC",
                   help="호흡 시각에 더할 초. 호흡 기록이 RR 기록보다 늦게 시작했으면 양수"
                        "(= 호흡 시작 절대시각 − RR 시작 절대시각). 자동 정렬 없음 — 제안값은 표시만")
    p.add_argument("--rsa-lag", type=float, default=DEFAULT_RSA_LAG_S, metavar="SEC",
                   help="RSA peak-valley 창을 뒤로 미는 초 — 생리적 호흡→RR 위상 지연 허용치(기본 1.0, 0–5). "
                        "20회/분 이상 빠른 호흡에 지연이 거의 없으면 0.5 로")
    p.add_argument("--min-breath-s", type=float, default=1.5, help="최소 호흡 주기(초, 기본 1.5)")
    p.add_argument("--max-breath-s", type=float, default=15.0, help="최대 호흡 주기(초, 기본 15)")
    p.add_argument("--k-mad", type=float, default=DEFAULT_K_MAD,
                   help="골 돌출 임계 = max(k × 잡음 MAD·1.4826, 0.15 × 60 s 창별 (p95−p5)) (k 기본 3.0)")
    p.add_argument("--smooth-s", type=float, default=0.5, help="파형 이동평균 창(초, 기본 0.5)")
    p.add_argument("--invert", action="store_true", help="파형 부호 반전(값이 클수록 호기인 장비)")
    p.add_argument("--resp-band-halfwidth", type=float, default=0.04, metavar="HZ",
                   help="호흡중심 대역 반폭(Hz, 기본 0.04)")
    p.add_argument("--nperseg", type=int, help="Welch 세그먼트 샘플 수(2의 거듭제곱, 기본 256 = 64 s)")
    p.add_argument("--subject", default="", help="조건비교.csv 의 subject 열 값(여러 피험자 병합용)")
    p.add_argument("--inspect", action="store_true", help="입력 인식 결과만 출력하고 종료")
    p.add_argument("--quiet", action="store_true", help="터미널 리포트 생략(산출물만)")
    p.add_argument("--version", action="version", version=f"rsalink {__version__}")
    return p


def _inspect(resp, rr, resp_path: str, rr_path: str) -> str:
    L = [f"rsalink --inspect (v{__version__})", f"호흡 파일: {os.path.basename(resp_path)}"]
    if isinstance(resp, RespWaveform):
        L += [f"  종류: 파형 ({len(resp.t)} 샘플), 추정 샘플링레이트 {resp.fs_est:.3f} Hz, 길이 {resp.duration_s / 60:.2f} 분",
              f"  불균등 샘플링 {100 * resp.irregular_frac:.1f}%, 갭 {resp.n_gaps}건({resp.gap_total_s:.1f} s), 중복 {resp.dup_ts}건",
              f"  타임스탬프 형식: {resp.time.kind}"]
    else:
        L += [f"  종류: 흡기 시작 이벤트 ({len(resp.t)} 개), 길이 {resp.duration_s / 60:.2f} 분, 중복 {resp.dup_ts}건",
              f"  타임스탬프 형식: {resp.time.kind}"]
    L += [f"RR 파일: {os.path.basename(rr_path)}",
          f"  열 `{rr.col_used}`, 단위 자동 인식: {rr.unit_detected}, 총 {rr.n_total} 박동, "
          f"300–2000 ms 밖 제외 {rr.n_excluded} ({100 * rr.excluded_frac:.1f}%)",
          f"  유효 RR 길이 {rr.duration_s / 60:.2f} 분, 타임스탬프 형식: {rr.time.kind}"]
    if resp.time.t0_abs is not None and rr.time.t0_abs is not None:
        d = resp.time.t0_abs - rr.time.t0_abs
        rr1 = rr.rr_ms[0] / 1000.0 if rr.rr_ms else 0.0
        if resp.time.tz_aware != rr.time.tz_aware:
            L.append("  타임존: 한쪽만 타임존이 있어 절대 시각 비교를 생략합니다"
                     f" (호흡 {'있음' if resp.time.tz_aware else '없음'} / RR {'있음' if rr.time.tz_aware else '없음'})")
        else:
            L.append(f"  절대 시각 차(호흡 첫 샘플 − RR 첫 타임스탬프): {d:+.2f} s — 자동 보정하지 않음.")
            L.append(f"  --resp-offset 후보: RR 첫 타임스탬프가 첫 간격의 시작이면 {d:+.2f} s / "
                     f"첫 박동 시각(간격 끝)이면 {d + rr1:+.2f} s (첫 RR {rr1:.3f} s). 장비 규약에 맞는 쪽을 고르세요.")
    return "\n".join(L)


def _validate_options(a) -> None:
    """옵션 값 nan/inf/범위 밖 → 한국어 오류(exit 2). 트레이스백을 내지 않는다(라운드 1 D9)."""
    def fin(name, v):
        if v is not None and not math.isfinite(v):
            raise RsalinkError(f"{name} 는 유한한 수여야 합니다 (받은 값: {v})")

    for name, v in (("--pace", a.pace), ("--min-breath-s", a.min_breath_s), ("--max-breath-s", a.max_breath_s),
                    ("--k-mad", a.k_mad), ("--smooth-s", a.smooth_s), ("--resp-band-halfwidth", a.resp_band_halfwidth),
                    ("--resp-offset", a.resp_offset), ("--rsa-lag", a.rsa_lag)):
        fin(name, v)
    if a.pace is not None and not (0.0 < a.pace <= 60.0):
        raise RsalinkError("--pace 는 0 초과 60 이하(회/분)여야 합니다")
    if a.min_breath_s <= 0 or a.max_breath_s <= a.min_breath_s or a.max_breath_s > 300.0:
        raise RsalinkError("--min-breath-s > 0, --min-breath-s < --max-breath-s ≤ 300 이어야 합니다")
    if a.k_mad <= 0:
        raise RsalinkError("--k-mad 는 양수여야 합니다")
    if not (0.0 <= a.smooth_s <= 30.0):
        raise RsalinkError("--smooth-s 는 0–30 초여야 합니다")
    if not (0.0 < a.resp_band_halfwidth <= 0.5):
        raise RsalinkError("--resp-band-halfwidth 는 0 초과 0.5 Hz 이하여야 합니다")
    if abs(a.resp_offset) > 86400.0:
        raise RsalinkError("--resp-offset 은 ±86400 초 안이어야 합니다")
    if a.nperseg is not None and (a.nperseg < 64 or a.nperseg & (a.nperseg - 1)):
        raise RsalinkError("--nperseg 는 64 이상 2의 거듭제곱이어야 합니다")
    if not (0.0 <= a.rsa_lag <= 5.0):
        raise RsalinkError("--rsa-lag 는 0–5 초 사이 유한한 값이어야 합니다")


def run(argv: Optional[List[str]] = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    _validate_options(a)
    resp = parse_resp(a.resp)
    rr = parse_rr(a.rr, a.rr_col)
    if a.inspect:
        print(_inspect(resp, rr, a.resp, a.rr))
        return EXIT_OK
    ignored_opts = []
    if isinstance(resp, RespEvents):
        if a.invert:
            ignored_opts.append("--invert")
        if a.smooth_s != 0.5:
            ignored_opts.append("--smooth-s")
        if a.k_mad != DEFAULT_K_MAD:
            ignored_opts.append("--k-mad")
        if ignored_opts:
            print(f"경고: 흡기 시작 이벤트 입력에는 {', '.join(ignored_opts)} 가 적용되지 않습니다(무시됨)", file=sys.stderr)
    if isinstance(resp, RespWaveform) and resp.fs_est < 0.2:
        raise RsalinkError(f"호흡 샘플링레이트 {resp.fs_est:.4f} Hz < 0.2 Hz — 타임스탬프 단위 의심"
                           "(밀리초를 초로 읽었는지, 열이 맞는지 --inspect 로 확인)")
    # out-dir 은 입력 검증 뒤·계산 전에 검사(잘못된 입력으로 폴더를 만들지 않고, 길게 계산하고 나서 실패하지도 않도록)
    inputs = [a.resp, a.rr] + ([a.segments] if a.segments else [])
    out_dir, out_link = (prepare_out_dir(a.out_dir, inputs) if a.out_dir else (None, None))

    br = detect_breaths(resp, a.min_breath_s, a.max_breath_s, a.k_mad, a.invert, a.smooth_s)
    rows = rsa_per_breath(br, rr, a.resp_offset, lag=a.rsa_lag)
    resp_t = br.t if isinstance(resp, RespWaveform) else None
    resp_v = br.detrended if isinstance(resp, RespWaveform) else None

    # 사용 가능 구간 = 두 신호가 겹치는 시간(RR 시계 기준); 구간은 이 공통 범위로 잘라 쓴다(A5)
    r0, r1 = br.t[0] + a.resp_offset, br.t[-1] + a.resp_offset
    lo, hi = max(r0, 0.0), min(r1, rr.duration_s)
    usable = max(0.0, hi - lo)
    segs = clip_segments(build_segments(a.baseline, a.stimulus, a.segments, lo, hi), lo, hi)
    stats = analyze_segments(segs, br, rows, rr, resp_t, resp_v, a.resp_offset, a.pace,
                             a.resp_band_halfwidth, a.nperseg)
    comps = compare_all(stats)

    suggest = None
    if resp_t is not None and usable >= 60.0:
        try:
            suggest = suggest_offset(rr.t_beat, rr.rr_ms, resp_t, resp_v, max(r0, 0.0), min(r1, rr.duration_s))
        except (ValueError, ZeroDivisionError):
            suggest = None
    flags = {}
    for b in br.breaths:
        if b.flag:
            flags[b.flag] = flags.get(b.flag, 0) + 1
    tot = summarize(rows)
    # 호흡수 안정성(A2)은 구간 안에서 본다 — 프로토콜의 호흡수 변화(12→6/분)를 검출 불안정으로 오인하지 않도록.
    # 유효 호흡 5개 이상인 구간이 없으면 전체 기록으로.
    rate_unstable, rate_gate = False, []
    seg_rate_lists = [(st.seg.label, [b.rate_bpm for b in br.valid_breaths
                                      if st.seg.t0 - a.resp_offset <= b.t_onset < st.seg.t1 - a.resp_offset])
                      for st in stats]
    seg_rate_lists = [(lab, r) for lab, r in seg_rate_lists if len(r) >= 5]
    if not seg_rate_lists:
        seg_rate_lists = [("전체", br.rates())]
    for lab, rates_ in seg_rate_lists:
        f_, reasons_ = rate_stability(rates_)
        rate_unstable = rate_unstable or f_
        rate_gate += [f"[{lab}] {r}" for r in reasons_]
    abs_diff = None
    tz_note = ""
    if resp.time.t0_abs is not None and rr.time.t0_abs is not None:
        if resp.time.tz_aware != rr.time.tz_aware:
            tz_note = (f"한쪽만 타임존 있음(호흡 {'있음' if resp.time.tz_aware else '없음'} / "
                       f"RR {'있음' if rr.time.tz_aware else '없음'}) — 절대 시각 비교 생략")
        else:
            abs_diff = resp.time.t0_abs - rr.time.t0_abs
    mean_rate_hz = br.mean_freq_hz()
    samples_per_breath = (getattr(resp, "fs_est", float("nan")) / mean_rate_hz
                          if (mean_rate_hz == mean_rate_hz and mean_rate_hz > 0) else float("nan"))
    cov = Coverage(
        resp_source=br.source, resp_file=os.path.basename(a.resp), rr_file=os.path.basename(a.rr),
        fs_est=getattr(resp, "fs_est", float("nan")), irregular_frac=getattr(resp, "irregular_frac", 0.0),
        n_gaps=getattr(resp, "n_gaps", 0), gap_total_s=getattr(resp, "gap_total_s", 0.0), dup_resp=resp.dup_ts,
        resp_duration_s=resp.duration_s, rr_duration_s=rr.duration_s, usable_s=usable, rr_unit=rr.unit_detected,
        rr_col=rr.col_used, rr_total=rr.n_total, rr_excluded=rr.n_excluded, rr_excluded_frac=rr.excluded_frac,
        n_breaths=br.n, n_valid=len(br.valid_breaths), artifact_frac=br.artifact_frac, flags=flags,
        beats_per_breath=tot["beats_per_breath"], n_undetermined=tot["n_undetermined"],
        resp_offset=a.resp_offset, rsa_lag=a.rsa_lag, offset_suggest=suggest, peak_assumed=br.peak_assumed,
        time_kinds=(resp.time.kind, rr.time.kind),
        short_segments=[s.seg.label for s in stats if s.short_flag],
        rate_unstable=rate_unstable, rate_gate_reasons=rate_gate,
        abs_time_diff=abs_diff, rr_first_s=(rr.rr_ms[0] / 1000.0 if rr.rr_ms else 0.0), tz_note=tz_note,
        samples_per_breath=samples_per_breath, n_bad_resp_values=getattr(resp, "n_bad_values", 0),
        n_spikes_clipped=br.n_clipped, ignored_options=ignored_opts, rr_unparsed=rr.n_unparsed,
        mean_breath_period_s=(1.0 / mean_rate_hz if (mean_rate_hz == mean_rate_hz and mean_rate_hz > 0) else float("nan")))
    gate = cov.gate_reasons()
    md = build_report(cov, br, rows, stats, comps, a.pace, a.resp_band_halfwidth, gate)
    if not a.quiet:
        print(md)
    if out_dir:
        written = write_artifacts(out_dir, md, rows, stats, comps, a.pace, a.subject, inputs, a.resp_offset)
        where = out_dir if out_link is None else f"링크 {out_link} → 실제경로 {out_dir}"
        print("\n산출물: " + ", ".join(os.path.basename(w) for w in written) + f"  (폴더: {where})")
    if gate:
        print("\n[exit 3] 신뢰 불가: " + "; ".join(gate), file=sys.stderr)
        return EXIT_UNRELIABLE
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return run(argv)
    except RsalinkError as e:
        print(f"오류: {e}", file=sys.stderr)
        return EXIT_INPUT
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
