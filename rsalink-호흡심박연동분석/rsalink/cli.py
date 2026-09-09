"""rsalink.cli — 명령줄 진입점.

    rsalink 호흡.csv RR.csv --baseline 0:00-5:00 --stimulus 5:00-15:00 --pace 6 --out-dir 결과
    rsalink 호흡.csv RR.csv --segments 조건.csv --resp-offset 1.5 --out-dir 결과
    rsalink 호흡.csv RR.csv --inspect

exit 0 정상 / 2 입력·인자 오류(파일 없음, 형식, --out-dir 이 파일, 권한, 심볼릭/하드링크 타깃)
/ 3 신뢰 불가(유효 호흡 <60% 또는 사용 가능 구간 <2분 또는 RR 제외 >20%) — 리포트는 그래도 출력.
네트워크 0, 입력은 읽기만, 산출물은 --out-dir 안에만.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import RsalinkError, __version__
from .breath import detect_breaths
from .parse import RespEvents, RespWaveform, parse_resp, parse_rr
from .report import Coverage, build_report, prepare_out_dir, write_artifacts
from .rsa import rsa_per_breath, summarize
from .segments import analyze_segments, build_segments, compare_all
from .spectral import suggest_offset

EXIT_OK, EXIT_INPUT, EXIT_UNRELIABLE = 0, 2, 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rsalink",
        description="호흡 신호 + RR 간격 연동 분석: RSA peak-valley, 고정 HF vs 호흡중심 대역, 결맞음, "
                    "페이싱 동조, 조건 비교. 인과를 주장하지 않는다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit 0 정상 / 2 입력·인자 오류 / 3 신뢰 불가(유효 호흡 <60%%, 사용 구간 <2분, RR 제외 >20%%)")
    p.add_argument("resp", help="호흡 CSV: timestamp,value 파형 또는 timestamp 한 열(흡기 시작 이벤트)")
    p.add_argument("rr", help="RR/IBI CSV (hrvkit 규격: ms/s/bpm 자동, 값 열 하나)")
    p.add_argument("--baseline", metavar="M:SS-M:SS", help="기준선 구간")
    p.add_argument("--stimulus", metavar="M:SS-M:SS", help="자극 구간")
    p.add_argument("--segments", metavar="CSV", help="구간 CSV (start,end,label) — --baseline/--stimulus 와 배타")
    p.add_argument("--pace", type=float, metavar="N", help="목표 호흡수(회/분) — 동조율 계산")
    p.add_argument("--out-dir", metavar="DIR", help="산출물 폴더(연동리포트.md, 호흡별.csv, 조건비교.csv). 없으면 터미널만")
    p.add_argument("--rr-col", metavar="NAME|IDX", help="RR 값 열 지정")
    p.add_argument("--resp-offset", type=float, default=0.0, metavar="SEC",
                   help="호흡 시각에 더할 초(호흡 시계가 RR 시계보다 앞서면 양수). 자동 정렬 없음 — 제안값은 표시만")
    p.add_argument("--min-breath-s", type=float, default=1.5, help="최소 호흡 주기(초, 기본 1.5)")
    p.add_argument("--max-breath-s", type=float, default=15.0, help="최대 호흡 주기(초, 기본 15)")
    p.add_argument("--k-mad", type=float, default=1.0, help="골 돌출 임계 = k × MAD (기본 1.0)")
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
        L.append(f"  절대 시각 차(호흡 시작 − RR 시작): {d:+.2f} s — 자동 보정하지 않음(--resp-offset 참고)")
    return "\n".join(L)


def run(argv: Optional[List[str]] = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if a.pace is not None and a.pace <= 0:
        raise RsalinkError("--pace 는 양수여야 합니다")
    if a.min_breath_s <= 0 or a.max_breath_s <= a.min_breath_s:
        raise RsalinkError("--min-breath-s > 0, --max-breath-s > --min-breath-s 이어야 합니다")
    if a.nperseg is not None and (a.nperseg < 64 or a.nperseg & (a.nperseg - 1)):
        raise RsalinkError("--nperseg 는 64 이상 2의 거듭제곱이어야 합니다")
    # out-dir 은 계산 전에 검사(길게 계산하고 나서 실패하지 않도록)
    out_dir = prepare_out_dir(a.out_dir) if a.out_dir else None

    resp = parse_resp(a.resp)
    rr = parse_rr(a.rr, a.rr_col)
    if a.inspect:
        print(_inspect(resp, rr, a.resp, a.rr))
        return EXIT_OK

    br = detect_breaths(resp, a.min_breath_s, a.max_breath_s, a.k_mad, a.invert, a.smooth_s)
    rows = rsa_per_breath(br, rr, a.resp_offset)
    resp_t = br.t if isinstance(resp, RespWaveform) else None
    resp_v = br.detrended if isinstance(resp, RespWaveform) else None

    # 사용 가능 구간 = 두 신호가 겹치는 시간(RR 시계 기준)
    r0, r1 = br.t[0] + a.resp_offset, br.t[-1] + a.resp_offset
    usable = max(0.0, min(r1, rr.duration_s) - max(r0, 0.0))
    total = min(r1, rr.duration_s)
    segs = build_segments(a.baseline, a.stimulus, a.segments, total)
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
    cov = Coverage(
        resp_source=br.source, resp_file=os.path.basename(a.resp), rr_file=os.path.basename(a.rr),
        fs_est=getattr(resp, "fs_est", float("nan")), irregular_frac=getattr(resp, "irregular_frac", 0.0),
        n_gaps=getattr(resp, "n_gaps", 0), gap_total_s=getattr(resp, "gap_total_s", 0.0), dup_resp=resp.dup_ts,
        resp_duration_s=resp.duration_s, rr_duration_s=rr.duration_s, usable_s=usable, rr_unit=rr.unit_detected,
        rr_col=rr.col_used, rr_total=rr.n_total, rr_excluded=rr.n_excluded, rr_excluded_frac=rr.excluded_frac,
        n_breaths=br.n, n_valid=len(br.valid_breaths), artifact_frac=br.artifact_frac, flags=flags,
        beats_per_breath=tot["beats_per_breath"], n_undetermined=tot["n_undetermined"],
        resp_offset=a.resp_offset, offset_suggest=suggest, peak_assumed=br.peak_assumed,
        time_kinds=(resp.time.kind, rr.time.kind),
        short_segments=[s.seg.label for s in stats if s.short_flag])
    gate = cov.gate_reasons()
    md = build_report(cov, br, rows, stats, comps, a.pace, a.resp_band_halfwidth, gate)
    if not a.quiet:
        print(md)
    if out_dir:
        written = write_artifacts(out_dir, md, rows, stats, comps, a.pace, a.subject)
        print("\n산출물: " + ", ".join(os.path.basename(w) for w in written) + f"  (폴더: {os.path.basename(out_dir)})")
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
