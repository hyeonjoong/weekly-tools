"""circadia CLI — 워치 CSV(심박·걸음·수면구간) → 일주기리듬 리포트.

exit code: 0 정상 / 2 입력·인자 오류 / 3 데이터 불충분(착용률 임계 미만).
지표별 최소일수 미달은 exit 0 이되 값 대신 '데이터 부족'을 출력한다 —
분석 전체가 무효인 것(착용률 미달)과 일부 지표만 못 내는 것을 구분한다.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import List, Optional

from . import __version__
from .actogram import render_actogram
from .cosinor import fit_cosinor
from .hrmark import hr_markers
from .nonparam import coverage, hourly_bin, is_iv, l5m10
from .parse import (CircadiaError, Series, SleepData, check_cross_file_offsets,
                    read_series, read_sleep)
from .report import (AnalysisContext, build_daily_rows, build_metrics_rows,
                     build_report)

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_INSUFFICIENT = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="circadia",
        description=("워치에서 내보낸 연속 시계열 CSV(심박·걸음·수면구간, 최소 1개)로 "
                     "일주기리듬을 정량화합니다 — cosinor, IS/IV/RA/L5/M10, SRI·사회적 "
                     "시차, 심박 야간 강하, 48h 텍스트 액토그램. 진단하지 않습니다."),
        epilog=("예: circadia 심박.csv --steps 걸음.csv --sleep 수면.csv --out-dir 리듬결과"))
    p.add_argument("hr_csv", nargs="?", default=None,
                   help="심박 CSV (timestamp,hr — Apple/삼성/Fitbit식 열 이름 자동 인식)")
    p.add_argument("--steps", metavar="CSV", help="걸음/활동 CSV (timestamp,steps)")
    p.add_argument("--sleep", metavar="CSV", help="수면구간 CSV (start,end[,단계])")
    p.add_argument("--out-dir", metavar="DIR",
                   help="리듬리포트.md·지표.csv·액토그램.txt 를 쓸 폴더 "
                        "(없으면 터미널 출력만)")
    p.add_argument("--hr-col", help="심박 값 열 이름 지정")
    p.add_argument("--hr-time-col", help="심박 시각 열 이름 지정")
    p.add_argument("--steps-col", help="걸음 값 열 이름 지정")
    p.add_argument("--steps-time-col", help="걸음 시각 열 이름 지정")
    p.add_argument("--sleep-start-col", help="수면 시작 열 이름 지정")
    p.add_argument("--sleep-end-col", help="수면 종료 열 이름 지정")
    p.add_argument("--min-days", type=int, default=5,
                   help="IS/IV·SRI 최소 유효일수 (기본 5, 권장 7)")
    p.add_argument("--min-wear", type=float, default=0.5,
                   help="주 시계열 최소 착용률(0–1, 기본 0.5) — 미만이면 exit 3")
    p.add_argument("--inspect", action="store_true",
                   help="열 인식·인코딩·행 수만 보여주고 종료 (분석 안 함)")
    p.add_argument("--version", action="version", version=f"circadia {__version__}")
    return p


def _inspect(metas) -> None:
    print("── --inspect: 열 인식 결과 " + "─" * 30)
    for m in metas:
        print(f"[{m.kind}] {m.path}")
        print(f"    인코딩 {m.encoding} · 구분자 {m.delimiter!r}")
        for role, pick in m.columns.items():
            print(f"    {role} 열 ← {pick.raw_name!r} ({pick.how})")
        if m.kind == "수면":
            print(f"    원본 데이터 행 {m.n_rows}행 → 수면구간 {m.n_used}개"
                  + (f", 제외 {dict(m.excluded)}" if m.excluded else ""))
        else:
            print(f"    데이터 행 {m.n_rows}개 중 {m.n_used}개 사용"
                  + (f", 제외 {dict(m.excluded)}" if m.excluded else ""))
        if m.first_ts:
            print(f"    기간 {m.first_ts} ~ {m.last_ts}")
        if m.tz_note:
            print(f"    시간대 {m.tz_note}")
        for note in m.notes:
            print(f"    주: {note}")


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.hr_csv is None and not args.steps and not args.sleep:
        print("오류: 입력이 없습니다 — 심박 CSV(위치 인자), --steps, --sleep 중 "
              "최소 1개가 필요합니다", file=sys.stderr)
        return EXIT_INPUT
    if not (0.0 <= args.min_wear <= 1.0):
        print("오류: --min-wear 는 0~1 사이여야 합니다", file=sys.stderr)
        return EXIT_INPUT
    if args.min_days < 2:
        print("오류: --min-days 는 2 이상이어야 합니다", file=sys.stderr)
        return EXIT_INPUT

    hr: Optional[Series] = None
    steps: Optional[Series] = None
    sleep: Optional[SleepData] = None
    try:
        if args.hr_csv:
            hr = read_series(args.hr_csv, "심박",
                             time_col=args.hr_time_col, value_col=args.hr_col)
        if args.steps:
            steps = read_series(args.steps, "걸음",
                                time_col=args.steps_time_col, value_col=args.steps_col)
        if args.sleep:
            sleep = read_sleep(args.sleep, start_col=args.sleep_start_col,
                               end_col=args.sleep_end_col)
        metas = [x.meta for x in (hr, steps, sleep) if x is not None]
        check_cross_file_offsets(metas)
    except CircadiaError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return EXIT_INPUT

    if args.inspect:
        _inspect(metas)
        return EXIT_OK

    ctx = AnalysisContext(metas=metas, min_days=args.min_days,
                          min_wear=args.min_wear)

    # 주 시계열(비모수 지표·착용률 게이트의 기준): 걸음 > 심박
    primary = steps or hr
    if primary is not None:
        ctx.primary_kind = "걸음" if primary is steps else "심박"
        cov = coverage(primary.samples)
        ctx.primary_coverage = cov
        ctx.coverages.append((ctx.primary_kind, cov))
        if hr is not None and primary is not hr:
            ctx.coverages.append(("심박", coverage(hr.samples)))
        if cov.wear_rate < args.min_wear:
            print(f"오류: {ctx.primary_kind} 착용률 {cov.wear_rate * 100:.1f}%가 "
                  f"임계({args.min_wear * 100:.0f}%) 미만입니다 — 이 커버리지로 낸 "
                  "일주기 지표는 신뢰할 수 없어 분석을 중단합니다. 갭이 적은 "
                  "구간을 잘라 다시 넣거나 --min-wear 를 낮추세요(권장하지 않음)",
                  file=sys.stderr)
            return EXIT_INSUFFICIENT

    # 코사이너 — 하루 중 시각(시간)을 t 로 사용(τ=24h 이므로 위상=시계 시각)
    def _cosinor_of(series: Series):
        """(적합 결과 또는 None, 실패 사유) — 사유를 구분해 보고(M7)."""
        span_h = (series.samples[-1][0] - series.samples[0][0]).total_seconds() / 3600
        if span_h < 24.0:
            return None, f"기록 기간이 {span_h:.1f}시간(<24h)이라 24h 성분을 적합할 수 없음"
        ts = [s.hour + s.minute / 60.0 + s.second / 3600.0
              for s, _ in series.samples]
        fit = fit_cosinor(ts, [v for _, v in series.samples])
        if fit is None:
            return None, "표본이 8개 미만이거나 시각이 몰려 있어(특이 설계) 적합 불가"
        return fit, ""

    if hr is not None:
        ctx.hr_cosinor, ctx.hr_cosinor_note = _cosinor_of(hr)
    if steps is not None:
        # 심박뿐이면 활동 줄을 중복 표기하지 않음
        ctx.act_cosinor, ctx.act_cosinor_note = _cosinor_of(steps)

    # 비모수 지표
    if primary is not None:
        binned = hourly_bin(primary.samples,
                            agg="sum" if ctx.primary_kind == "걸음" else "mean")
        ctx.binned = binned
        ctx.isiv = is_iv(binned, min_days=args.min_days)
        ctx.l5m10 = l5m10(binned)

    # 수면 규칙성
    if sleep is not None:
        from .sleepreg import analyze_sleep
        ctx.sleep = analyze_sleep(sleep.intervals, min_nights=args.min_days)

    # 심박 마커
    if hr is not None:
        nights = ctx.sleep.nights if ctx.sleep else None
        ctx.hrmark = hr_markers(hr.samples,
                                sleep.intervals if sleep else None, nights)

    # 액토그램
    act_src = steps or hr
    ctx.actogram_text = render_actogram(
        act_src.samples if act_src else None,
        sleep.intervals if sleep else None,
        activity_kind="걸음" if act_src is steps else "심박")

    ctx.daily_rows = build_daily_rows(ctx,
                                      hr.samples if hr else None,
                                      steps.samples if steps else None)

    report_text = build_report(ctx)
    print(report_text)
    if ctx.actogram_text:
        print()
        print(ctx.actogram_text)

    if args.out_dir:
        # '~' 확장(M15). 산출물 쓰기 실패는 traceback 없이 한국어 오류 + exit 2,
        # 오류 요약이 마지막 줄이 되게 stderr 로(C5).
        out = os.path.expanduser(args.out_dir)
        try:
            os.makedirs(out, exist_ok=True)
            targets = [os.path.join(out, "리듬리포트.md"),
                       os.path.join(out, "지표.csv"),
                       os.path.join(out, "액토그램.txt")]
            for t in targets:
                # 심볼릭링크 추종 쓰기 금지(C3) — out-dir 안에 미리 심어 둔
                # 링크를 따라가 입력 건강데이터를 덮어쓰는 경로를 차단한다.
                if os.path.islink(t):
                    raise CircadiaError(
                        f"{t} 이(가) 심볼릭링크입니다 — 링크를 따라가 다른 파일을 "
                        "덮어쓰지 않도록 거부합니다. 링크를 지우거나 비어 있는 "
                        "--out-dir 을 쓰세요")
                # 하드링크도 같은 이유로 거부(라운드 2) — islink 는 하드링크에
                # False 라서, nlink>1 인 기존 파일에 쓰면 다른 이름으로 연결된
                # 원본(입력 건강데이터일 수 있음)까지 함께 덮어써진다.
                if os.path.exists(t) and not os.path.isdir(t) and os.stat(t).st_nlink > 1:
                    raise CircadiaError(
                        f"{t} 이(가) 하드링크(연결 수 {os.stat(t).st_nlink})입니다 — "
                        "다른 이름으로 연결된 파일을 함께 덮어쓰지 않도록 거부합니다. "
                        "파일을 지우거나 비어 있는 --out-dir 을 쓰세요")
            # 저장 파일에는 입력 경로를 basename 으로만(M14 — 사용자명 유출 방지)
            report_text_md = build_report(ctx, short_paths=True)
            with open(targets[0], "w", encoding="utf-8") as fh:
                fh.write("# circadia 리듬 리포트\n\n```\n" + report_text_md + "\n```\n")
                if ctx.actogram_text:
                    fh.write("\n```\n" + ctx.actogram_text + "\n```\n")
            with open(targets[1], "w", encoding="utf-8-sig", newline="") as fh:
                csv.writer(fh).writerows(build_metrics_rows(ctx))
            if ctx.actogram_text:
                with open(targets[2], "w", encoding="utf-8") as fh:
                    fh.write(ctx.actogram_text + "\n")
        except CircadiaError as exc:
            print(f"\n오류: {exc}", file=sys.stderr)
            return EXIT_INPUT
        except OSError as exc:
            print(f"\n오류: 산출물을 쓸 수 없습니다({out}): {exc} — --out-dir 이 "
                  "파일이 아닌 쓰기 가능한 폴더인지 확인하세요", file=sys.stderr)
            return EXIT_INPUT
        print(f"\n산출물 저장: {out}/리듬리포트.md, {out}/지표.csv"
              + (f", {out}/액토그램.txt" if ctx.actogram_text else ""))
    return EXIT_OK


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
