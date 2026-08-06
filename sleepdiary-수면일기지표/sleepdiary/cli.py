"""명령줄 진입점.

    python3 -m sleepdiary.cli 일기.csv --list-columns
    python3 -m sleepdiary.cli 일기.csv
    python3 -m sleepdiary.cli 일기.csv --compare-periods baseline followup
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Optional, Sequence

from . import __version__
from .aggregate import (
    ALL_KEYS,
    LINEAR_KEYS,
    METRIC_LABEL,
    compare_periods,
    period_levels,
    summarize_by_subject,
    summarize_group,
)
from .dataio import DataError, read_csv, resolve_columns, sanitize_cell
from .nightly import build_night
from .report import render_markdown, render_report

COLUMN_OPTS = ("subject", "date", "period", "bedtime", "lights_off", "sol", "waso",
               "awakenings", "final_awake", "out_of_bed")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sleepdiary",
        description="수면일기 CSV → 표준 수면지표(TST/SE/SOL/WASO/중앙수면시각) "
                    "+ 대상자별·시기별 요약 + 대응표본 비교. 외부 의존성 없음.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  python3 -m sleepdiary.cli examples/sleep_diary_trial.csv --list-columns
  python3 -m sleepdiary.cli examples/sleep_diary_trial.csv
  python3 -m sleepdiary.cli examples/sleep_diary_trial.csv \\
      --compare-periods baseline followup --markdown
  python3 -m sleepdiary.cli 내일기.csv --lights-off 소등 --final-awake 기상 \\
      --per-night-csv nights.csv --json out.json
""")
    p.add_argument("csv_path", nargs="?", help="수면일기 CSV 파일")
    p.add_argument("--version", action="version", version=f"sleepdiary {__version__}")
    p.add_argument("--list-columns", action="store_true",
                   help="열 이름과 자동인식 결과만 보여주고 끝낸다")

    g = p.add_argument_group("열 지정 (자동인식이 틀렸을 때만 쓰세요)")
    for field in COLUMN_OPTS:
        g.add_argument(f"--{field.replace('_', '-')}", dest=field, default=None,
                       metavar="열이름")

    o = p.add_argument_group("분석 옵션")
    o.add_argument("--date-means", choices=("morning", "evening"), default="morning",
                   help="일기의 날짜가 '기상한 아침'(기본)인지 '잠자리에 든 저녁'인지")
    o.add_argument("--conf", type=float, default=0.95, metavar="0.95",
                   help="신뢰수준 (기본 0.95)")
    o.add_argument("--min-nights", type=int, default=1, metavar="N",
                   help="집단 통계에 포함할 최소 유효 밤 수 (기본 1). "
                        "임상시험은 보통 5~7박을 요구합니다.")
    o.add_argument("--compare-periods", nargs=2, metavar=("먼저", "나중"),
                   help="두 시기를 대응표본으로 비교 (차이 = 나중 − 먼저)")
    o.add_argument("--ignore-period", action="store_true",
                   help="시기 열이 있어도 무시하고 한 덩어리로 집계")

    x = p.add_argument_group("출력")
    x.add_argument("--markdown", action="store_true", help="마크다운 표로 출력")
    x.add_argument("--json", metavar="파일", help="기계판독용 JSON 저장")
    x.add_argument("--per-night-csv", metavar="파일", help="밤별 계산 결과 CSV 저장")
    x.add_argument("--per-subject-csv", metavar="파일", help="대상자별 요약 CSV 저장")
    x.add_argument("--quiet", action="store_true", help="표준출력 보고서를 생략")
    return p


def _list_columns(path: str) -> int:
    rows, fieldnames, encoding = read_csv(path)
    print(f"파일 : {path}")
    print(f"인코딩: {encoding}   행 수: {len(rows)}")
    print("\n열 목록:")
    for name in fieldnames:
        sample = next((r.get(name) for r in rows if str(r.get(name) or "").strip()), "")
        print(f"  - {name}    (예: {str(sample)[:30]})")
    print("\n자동인식 결과:")
    try:
        cols = resolve_columns(fieldnames, {})
    except DataError as exc:
        print(f"  ! {exc}")
        return 2
    for field in COLUMN_OPTS:
        print(f"  {field:<14}→ {cols.get(field) or '—'}")
    return 0


def _open_out(path: str):
    """산출물 파일 열기 — 못 열면 DataError로 바꿔 친절한 메시지를 낸다."""
    try:
        return open(path, "w", newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise DataError(f"'{path}' 에 쓸 수 없습니다: {exc}") from exc


def _write_per_night_csv(path: str, nights: Sequence) -> None:
    fields = ["row", "subject", "date", "period", "bedtime_min", "lights_off_min",
              "sol_min", "waso_min", "awakenings", "final_awake_min", "out_of_bed_min",
              "tib_min", "spt_min", "tst_min", "twak_min", "se_pct", "onset_min",
              "midsleep_min", "imputed", "valid", "errors", "warnings"]
    with _open_out(path) as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        for night in nights:
            d = night.as_dict()
            d["errors"] = " | ".join(d["errors"])
            d["warnings"] = " | ".join(d["warnings"])
            d["imputed"] = " | ".join(d["imputed"])
            writer.writerow([sanitize_cell(d.get(f)) for f in fields])


def _write_per_subject_csv(path: str, summaries: Sequence) -> None:
    rows = [s.as_dict() for s in summaries]
    fields = ["subject", "period", "n_nights", "n_excluded", "n_warned",
              "date_first", "date_last", "regularity_min"]
    fields += ["sol_imputed_nights", "waso_imputed_nights"]
    fields += [k for k in ALL_KEYS]
    with _open_out(path) as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([sanitize_cell(row.get(f)) for f in fields])


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.csv_path:
        build_parser().print_help()
        return 2

    if args.list_columns:
        return _list_columns(args.csv_path)

    if not 0.5 <= args.conf < 1.0:
        print("오류: --conf 는 0.5 이상 1.0 미만이어야 합니다.", file=sys.stderr)
        return 2

    rows, fieldnames, encoding = read_csv(args.csv_path)
    overrides = {f: getattr(args, f) for f in COLUMN_OPTS}
    cols = resolve_columns(fieldnames, overrides)

    nights = [build_night(row, cols, i + 2, date_means=args.date_means)
              for i, row in enumerate(rows)]

    use_period = bool(cols.get("period")) and not args.ignore_period
    summaries = summarize_by_subject(nights, by_period=use_period)

    eligible = [s for s in summaries if s.n_nights >= args.min_nights]
    dropped = [s for s in summaries if s.n_nights < args.min_nights]

    if use_period:
        levels = period_levels(nights)
        groups = [summarize_group([s for s in eligible if s.period == lv], lv, args.conf)
                  for lv in levels]
        groups = [g for g in groups if g.n_subjects]
    else:
        groups = [summarize_group(eligible, None, args.conf)]

    comps = []
    if args.compare_periods:
        a, b = args.compare_periods
        if not use_period:
            print("오류: 시기(period) 열이 없거나 --ignore-period 가 켜져 있어 "
                  "--compare-periods 를 쓸 수 없습니다.", file=sys.stderr)
            return 2
        levels = period_levels(nights)
        unknown = [lv for lv in (a, b) if lv not in levels]
        if unknown:
            print(f"오류: 시기 값 {', '.join(unknown)} 을(를) 찾지 못했습니다. "
                  f"있는 값: {', '.join(levels)}", file=sys.stderr)
            return 2
        comps = compare_periods(eligible, a, b, ALL_KEYS, args.conf)

    meta = {"path": args.csv_path, "encoding": encoding, "n_rows": len(rows),
            "cols": cols, "date_means": args.date_means, "conf": args.conf,
            "min_nights": args.min_nights}

    if not args.quiet:
        if args.markdown:
            print(render_markdown(meta, nights, eligible, groups, comps, args.conf))
        else:
            print(render_report(meta, nights, eligible, groups, comps, args.conf,
                                show_period=use_period))
        if dropped:
            names = ", ".join(f"{s.subject}({s.n_nights}박)" for s in dropped[:10])
            print(f"\n  ※ --min-nights {args.min_nights} 미만이라 집단 통계에서 제외된 "
                  f"대상자 {len(dropped)}명: {names}")

    if args.per_night_csv:
        _write_per_night_csv(args.per_night_csv, nights)
        if not args.quiet:
            print(f"  → 밤별 결과 저장: {args.per_night_csv}")
    if args.per_subject_csv:
        _write_per_subject_csv(args.per_subject_csv, summaries)
        if not args.quiet:
            print(f"  → 대상자별 요약 저장: {args.per_subject_csv}")
    if args.json:
        payload = {
            "tool": "sleepdiary", "version": __version__,
            # 파일 이름만 넣는다 — 임상 파일명에는 환자 이름이 들어 있는 일이
            # 흔한데, JSON은 협력자에게 그대로 전달되는 산출물이다.
            "input": {"file": os.path.basename(meta["path"]),
                      **{k: meta[k] for k in ("encoding", "n_rows", "date_means",
                                              "min_nights", "conf")}},
            "columns": cols,
            "counts": {
                "nights_total": len(nights),
                "nights_valid": sum(1 for n in nights if n.valid),
                "nights_excluded": sum(1 for n in nights if not n.valid),
                "subjects_analyzed": len(eligible),
                "subjects_dropped_min_nights": len(dropped),
            },
            "nights": [n.as_dict() for n in nights],
            "subjects": [s.as_dict() for s in summaries],
            "groups": [g.as_dict() for g in groups],
            "comparisons": [c.as_dict() for c in comps],
            "metric_labels": {k: METRIC_LABEL[k] for k in ALL_KEYS},
            "notes": [
                "분석 단위는 대상자입니다 (밤별 값을 사람마다 평균한 뒤 사람 사이에서 요약).",
                "시각형 지표는 원형(circular) 평균/차이를 사용했습니다.",
                "다중비교 보정은 적용하지 않았습니다.",
                "결측 SOL/WASO는 0으로 계산했습니다 (TST·SE가 그만큼 높아집니다). "
                "해당 밤은 nights[].imputed 에 표시되어 있고 그 지표의 요약에서는 뺐습니다.",
                "낮잠은 포함되지 않습니다 — 야간 수면만 계산합니다.",
                "두 시기 모두 기록한 대상자만 비교했습니다 (완전자료 분석, ITT 아님).",
                "자기보고 자료이며 PSG/액티그래피와 체계적으로 다를 수 있습니다.",
            ],
        }
        try:
            with open(args.json, "w", encoding="utf-8") as fh:
                # allow_nan=False: NaN/Infinity는 표준 JSON이 아니라 R jsonlite 등이
                # 파일 전체를 거부한다. 여기까지 새어 나오면 버그이므로 터뜨린다.
                json.dump(payload, fh, ensure_ascii=False, indent=2,
                          default=str, allow_nan=False)
        except OSError as exc:
            raise DataError(f"'{args.json}' 에 쓸 수 없습니다: {exc}") from exc
        if not args.quiet:
            print(f"  → JSON 저장: {args.json}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return run(argv)
    except DataError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        print("\n중단되었습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
