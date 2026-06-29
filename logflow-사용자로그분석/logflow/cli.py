"""logflow 명령행 인터페이스."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from .dataio import load_events
from .report import build_report


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="logflow",
        description="사용자 이벤트 로그(CSV) 분석: 세션화 · 집계 · DAU/WAU/MAU · 리텐션 · 퍼널",
    )
    p.add_argument("csv", help="이벤트 로그 CSV 경로")
    p.add_argument("--user-col", default="user_id", help="사용자 ID 열 (기본: user_id)")
    p.add_argument("--event-col", default="event", help="이벤트 이름 열 (기본: event)")
    p.add_argument("--time-col", default="timestamp", help="타임스탬프 열 (기본: timestamp)")
    p.add_argument("--gap-min", type=float, default=30.0,
                   help="세션 분리 비활동 간격(분) (기본: 30)")
    p.add_argument("--retention", default="1,7",
                   help="리텐션 day-N 목록, 쉼표 구분 (기본: 1,7)")
    p.add_argument("--funnel", default=None,
                   help="퍼널 단계 이벤트 이름, 쉼표 구분 (순서대로)")
    p.add_argument("--top", type=int, default=10, help="상위 N개 표시 (기본: 10)")
    p.add_argument("--encoding", default="utf-8-sig", help="CSV 인코딩 (기본: utf-8-sig)")
    p.add_argument("--tz-offset", type=float, default=0.0,
                   help="시각에 더할 시간(시). 날짜를 현지시각 기준으로 끊을 때 사용 (예: 9 = KST)")
    p.add_argument("--skip-bad-rows", action="store_true",
                   help="파싱 불가한 타임스탬프 행을 오류 없이 건너뜀")
    return p.parse_args(argv)


def _int_list(raw: str) -> List[int]:
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise ValueError(f"리텐션 day-N 목록에 숫자가 아닌 값: {part!r}")
    return out


def _str_list(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    out = [p.strip() for p in raw.split(",") if p.strip()]
    return out or None


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.top < 0:
        print("오류: --top 은 0 이상이어야 합니다", file=sys.stderr)
        return 1
    counters: dict = {}
    try:
        events = load_events(
            args.csv,
            user_col=args.user_col,
            event_col=args.event_col,
            time_col=args.time_col,
            encoding=args.encoding,
            tz_offset_hours=args.tz_offset,
            skip_bad_rows=args.skip_bad_rows,
            counters=counters,
        )
        report = build_report(
            events,
            gap_seconds=args.gap_min * 60.0,
            retention_days=_int_list(args.retention),
            funnel_steps=_str_list(args.funnel),
            top=args.top,
        )
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print(report)
    skipped = counters.get("skipped_missing", 0) + counters.get("skipped_bad", 0)
    if skipped:
        print(f"(참고: 결측 {counters['skipped_missing']}행, 파싱불가 "
              f"{counters['skipped_bad']}행을 건너뛰었습니다.)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
