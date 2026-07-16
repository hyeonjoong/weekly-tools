"""logflow 명령행 인터페이스."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date
from typing import List, Optional, Sequence

from .analyze import analyze, to_csv_tables, to_dict
from .dataio import load_events
from .report import render_text


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
    p.add_argument("--confidence", type=float, default=0.95,
                   help="리텐션·퍼널 전환율 신뢰구간 수준 (0~1, 기본: 0.95)")
    p.add_argument("--retention-mode", choices=("exact", "rolling"), default="exact",
                   help="리텐션 정의: exact=정확히 day-N, rolling=day-N 이후 (기본: exact)")
    p.add_argument("--delimiter", default=None,
                   help="CSV 구분자(1글자). 미지정 시 자동감지(콤마/세미콜론/탭/파이프)")
    p.add_argument("--csv-dir", default=None,
                   help="DAU·리텐션·퍼널 등 표를 이 폴더에 CSV 로 저장 (원고·엑셀용)")
    p.add_argument("--dedup", action="store_true",
                   help="(user, event, timestamp) 가 완전히 같은 중복 행 제거")
    p.add_argument("--from", dest="date_from", default=None,
                   help="이 날짜(YYYY-MM-DD) 이후만 분석 (tz 보정 후 기준, 포함)")
    p.add_argument("--to", dest="date_to", default=None,
                   help="이 날짜(YYYY-MM-DD) 이전만 분석 (tz 보정 후 기준, 포함)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="텍스트 대신 JSON 결과를 출력 (다운스트림 분석·논문용)")
    p.add_argument("--out", default=None,
                   help="리포트를 이 파일에 저장 (지정 안 하면 표준출력)")
    return p.parse_args(argv)


def _same_file(a: str, b: str) -> bool:
    """두 경로가 같은 파일을 가리키는지 — 심볼릭/하드 링크까지 고려.

    realpath 로 심볼릭 링크를 풀고, 둘 다 존재하면 samefile(inode 동일)로 하드링크도 잡는다.
    """
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return False


def _write_csv_tables(result, csv_dir: str) -> List[str]:
    """분석 표들을 csv_dir 에 <name>.csv 로 저장. 저장 실패는 ValueError 로 변환."""
    tables = to_csv_tables(result)
    try:
        os.makedirs(csv_dir, exist_ok=True)
        written = []
        for name, text in tables.items():
            path = os.path.join(csv_dir, f"{name}.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(text)
            written.append(path)
    except OSError as exc:
        raise ValueError(f"CSV 표 저장 실패: {exc}") from exc
    return written


def _parse_date(raw: Optional[str], label: str) -> Optional[date]:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError(f"{label} 날짜 형식이 잘못됨 (YYYY-MM-DD 필요): {raw!r}")


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
    if not (math.isfinite(args.confidence) and 0.0 < args.confidence < 1.0):
        print("오류: --confidence 는 0 과 1 사이의 유한한 수여야 합니다", file=sys.stderr)
        return 1
    if not math.isfinite(args.gap_min) or args.gap_min < 0:
        print("오류: --gap-min 은 0 이상의 유한한 수여야 합니다", file=sys.stderr)
        return 1
    if not math.isfinite(args.tz_offset):
        print("오류: --tz-offset 은 유한한 수여야 합니다", file=sys.stderr)
        return 1
    if args.delimiter is not None and len(args.delimiter) != 1:
        print("오류: --delimiter 는 정확히 1글자여야 합니다", file=sys.stderr)
        return 1
    if args.out and _same_file(args.out, args.csv):
        print("오류: --out 경로가 입력 CSV 와 같습니다 (입력 파일 덮어쓰기 방지)", file=sys.stderr)
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
            delimiter=args.delimiter,
            dedup=args.dedup,
            date_from=_parse_date(args.date_from, "--from"),
            date_to=_parse_date(args.date_to, "--to"),
        )
        result = analyze(
            events,
            gap_seconds=args.gap_min * 60.0,
            retention_days=_int_list(args.retention),
            funnel_steps=_str_list(args.funnel),
            confidence=args.confidence,
            retention_mode=args.retention_mode,
        )
        report = (
            json.dumps(to_dict(result), ensure_ascii=False, indent=2)
            if args.as_json
            else render_text(result, top=args.top)
        )
        csv_written = _write_csv_tables(result, args.csv_dir) if args.csv_dir else []
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except (ValueError, LookupError, OverflowError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(report + "\n")
        except OSError as exc:
            print(f"오류: 출력 파일 저장 실패: {exc}", file=sys.stderr)
            return 1
        print(f"리포트를 저장했습니다: {args.out}", file=sys.stderr)
    else:
        print(report)

    if csv_written:
        print(f"CSV 표 {len(csv_written)}개를 저장했습니다: {args.csv_dir}", file=sys.stderr)

    skipped = counters.get("skipped_missing", 0) + counters.get("skipped_bad", 0)
    notes = []
    if counters.get("skipped_missing"):
        notes.append(f"결측 {counters['skipped_missing']}행")
    if counters.get("skipped_bad"):
        notes.append(f"파싱불가 {counters['skipped_bad']}행")
    if counters.get("deduped"):
        notes.append(f"중복 {counters['deduped']}행")
    if counters.get("filtered"):
        notes.append(f"기간밖 {counters['filtered']}행")
    if notes:
        print(f"(참고: {', '.join(notes)}을 건너뛰었습니다.)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
