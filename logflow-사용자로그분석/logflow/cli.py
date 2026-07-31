"""logflow 명령행 인터페이스."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import date
from typing import List, Optional, Sequence, Tuple

from .analyze import analyze, to_csv_tables, to_dict
from .dataio import INPUT_FORMATS, load_events
from .groups import filter_to_groups
from .report import render_text


# 방어적 상한 — 이 값을 넘으면 날짜 연산/표시 변환이 의미를 잃는다 (100년 / ±31일).
_MAX_GAP_MIN = 100 * 365 * 24 * 60
_MAX_TZ_OFFSET = 24 * 31


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
    p.add_argument("--group-col", default=None,
                   help="군(arm) 라벨 열 이름. 지정하면 군 간 비교(리텐션 차이·퍼널 완주·"
                        "참여도·이탈 생존)를 함께 계산")
    p.add_argument("--ref-group", default=None,
                   help="기준군(대조군) 라벨. 비율 차이는 (비교군 − 기준군). 기본: 사전순 첫 군")
    p.add_argument("--only-groups", default=None,
                   help="이 군들만 남겨 분석 (쉼표 구분). 3개 이상인 데이터에서 비교할 "
                        "두 군만 고를 때. 전체 지표도 남은 군만으로 계산됩니다")
    p.add_argument("--max-rows", type=int, default=0,
                   help="읽어들일 최대 이벤트 수 (0=제한 없음). 압축 로그가 예상보다 "
                        "커서 메모리를 소진하는 것을 막는 안전장치")
    p.add_argument("--churn-days", type=int, default=7,
                   help="마지막 활동 후 이 일수 이상 무활동이면 이탈로 간주 (기본: 7)")
    p.add_argument("--format", dest="input_format", choices=INPUT_FORMATS, default="auto",
                   help="입력 형식: auto(확장자 판별)/csv/jsonl. .gz 는 자동 해제 (기본: auto)")
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


def _write_csv_tables(result, csv_dir: str, input_path: str) -> Tuple[List[str], List[str]]:
    """분석 표들을 csv_dir 에 <name>.csv 로 저장. 저장 실패는 ValueError 로 변환.

    표 이름은 고정(`users.csv`, `events.csv` 등)이라 입력 로그와 이름이 겹칠 수 있다.
    입력 파일을 덮어쓰면 원본 데이터가 사라지므로, 쓰기 **전에** 모든 대상 경로를
    입력과 대조해 하나라도 같으면 아무것도 쓰지 않고 오류를 낸다(부분 저장 방지).

    반환: (쓴 경로들, 기존 파일을 덮어쓴 경로들)
    """
    tables = to_csv_tables(result)
    paths = {name: os.path.join(csv_dir, f"{name}.csv") for name in tables}
    clashes = sorted(p for p in paths.values() if _same_file(p, input_path))
    if clashes:
        raise ValueError(
            f"CSV 표 저장 경로가 입력 파일과 같습니다: {clashes} "
            f"(입력 로그 덮어쓰기 방지 — --csv-dir 를 다른 폴더로 지정하세요)"
        )
    overwritten = sorted(p for p in paths.values() if os.path.exists(p))
    try:
        os.makedirs(csv_dir, exist_ok=True)
        written = []
        for name, text in tables.items():
            path = paths[name]
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(text)
            written.append(path)
    except OSError as exc:
        raise ValueError(f"CSV 표 저장 실패: {exc}") from exc
    return written, overwritten


def _parse_date(raw: Optional[str], label: str) -> Optional[date]:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError(f"{label} 날짜 형식이 잘못됨 (YYYY-MM-DD 필요): {raw!r}")


def _int_list(raw: str) -> List[int]:
    """쉼표 목록 → 정수 리스트 (순서 유지, 중복 제거).

    중복을 남기면 같은 가설이 여러 번 검정되어 Holm 보정의 family 크기가 부풀고
    보정된 p 값이 근거 없이 커진다 (`--retention 1,1` 이 `1` 보다 보수적이 되는 문제).
    """
    out: List[int] = []
    seen = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            raise ValueError(f"리텐션 day-N 목록에 숫자가 아닌 값: {part!r}")
        if value not in seen:
            seen.add(value)
            out.append(value)
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
    if not math.isfinite(args.gap_min) or not (0 <= args.gap_min <= _MAX_GAP_MIN):
        print(f"오류: --gap-min 은 0 이상 {_MAX_GAP_MIN:g} 이하의 유한한 수여야 합니다",
              file=sys.stderr)
        return 1
    if not math.isfinite(args.tz_offset) or abs(args.tz_offset) > _MAX_TZ_OFFSET:
        print(f"오류: --tz-offset 은 ±{_MAX_TZ_OFFSET:g}시간 이내의 유한한 수여야 합니다",
              file=sys.stderr)
        return 1
    if args.churn_days < 1:
        print("오류: --churn-days 는 1 이상이어야 합니다", file=sys.stderr)
        return 1
    if args.group_col is not None and not args.group_col.strip():
        print("오류: --group-col 은 빈 값일 수 없습니다", file=sys.stderr)
        return 1
    if args.ref_group is not None and args.group_col is None:
        print("오류: --ref-group 은 --group-col 과 함께 써야 합니다", file=sys.stderr)
        return 1
    if args.only_groups is not None and args.group_col is None:
        print("오류: --only-groups 는 --group-col 과 함께 써야 합니다", file=sys.stderr)
        return 1
    if args.max_rows < 0:
        print("오류: --max-rows 는 0 이상이어야 합니다", file=sys.stderr)
        return 1
    if args.delimiter is not None and len(args.delimiter) != 1:
        print("오류: --delimiter 는 정확히 1글자여야 합니다", file=sys.stderr)
        return 1
    if args.out and _same_file(args.out, args.csv):
        print("오류: --out 경로가 입력 CSV 와 같습니다 (입력 파일 덮어쓰기 방지)", file=sys.stderr)
        return 1
    counters: dict = {}
    dropped_users = 0
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
            group_col=args.group_col,
            input_format=args.input_format,
            max_rows=args.max_rows or None,
        )
        dropped_users = 0
        if args.only_groups:
            wanted = _str_list(args.only_groups)
            if not wanted:
                raise ValueError("--only-groups 목록이 비어 있습니다")
            events, dropped_users = filter_to_groups(events, wanted)
        result = analyze(
            events,
            gap_seconds=args.gap_min * 60.0,
            retention_days=_int_list(args.retention),
            funnel_steps=_str_list(args.funnel),
            confidence=args.confidence,
            retention_mode=args.retention_mode,
            group_col=args.group_col,
            churn_days=args.churn_days,
            reference_group=args.ref_group,
        )
        report = (
            json.dumps(to_dict(result), ensure_ascii=False, indent=2)
            if args.as_json
            else render_text(result, top=args.top)
        )
        csv_written, csv_overwritten = (
            _write_csv_tables(result, args.csv_dir, args.csv)
            if args.csv_dir
            else ([], [])
        )
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print(f"오류: 폴더는 읽을 수 없습니다 (파일 경로를 지정하세요): {args.csv}",
              file=sys.stderr)
        return 2
    except PermissionError:
        print(f"오류: 파일을 읽을 권한이 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except EOFError:
        print(f"오류: 압축 파일이 손상되었거나 중간에 끊겼습니다 (다시 내려받아 보세요): {args.csv}",
              file=sys.stderr)
        return 1
    except csv.Error as exc:
        print(f"오류: CSV 를 읽을 수 없습니다 ({exc}). 따옴표가 닫히지 않았거나 "
              f"한 칸의 내용이 지나치게 길 수 있습니다.", file=sys.stderr)
        return 1
    except (ValueError, LookupError, OverflowError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # gzip.BadGzipFile 등 — 손상된 입력에 파이썬 트레이스백을 노출하지 않는다.
        print(f"오류: 파일을 읽을 수 없습니다 ({exc}): {args.csv}", file=sys.stderr)
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
    if csv_overwritten:
        print(f"(참고: 기존 파일 {len(csv_overwritten)}개를 덮어썼습니다: "
              f"{', '.join(os.path.basename(p) for p in csv_overwritten)})", file=sys.stderr)

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
    if dropped_users:
        print(f"(참고: --only-groups 로 사용자 {dropped_users}명을 제외했습니다.)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
