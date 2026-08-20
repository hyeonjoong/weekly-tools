"""visitaudit CLI.

종료코드:
  0  이탈 없음 (판정률 임계 통과)
  1  이탈 발견
  2  입력·프로토콜 오류 — 프로토콜 JSON 이 없으면 아무 판정도 하지 않고 여기서 죽는다
  3  판정불가 — 판정률이 임계 미만 (이탈이 있어도 3 이 우선: 그 이탈 개수 자체를 믿을 수 없다)
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from typing import List, Optional

from . import __version__
from .consort import build_consort, build_pp, consort_text
from .criteria import recheck
from .dates import parse_asof
from .enroll import build_enrollment
from .judge import judge
from .protocol import Protocol, ProtocolError, load_protocol
from .report import (OUT_FILES, render_drafts, render_report,
                     set_protected_inputs, write_outputs)
from .tables import InputError, load_subjects, load_visits_long, load_visits_wide

EXIT_OK = 0
EXIT_DEVIATIONS = 1
EXIT_INPUT = 2
EXIT_COVERAGE = 3


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="visitaudit",
        description=("방문일정 이탈 점검 — 방문 날짜 표와 프로토콜(방문창) JSON 을 받아 "
                     "창 이탈·순서 위반·결측·중도탈락 처리를 전수 판정하고, CONSORT 숫자와 "
                     "PP 집합 후보, KR/EN 문장 초안을 냅니다. 완전 오프라인, 원본 무수정."),
        epilog=("종료코드: 0 이탈 없음 / 1 이탈 발견 / 2 입력·프로토콜 오류 / "
                "3 판정률 임계 미만. 프로토콜 JSON 없이는 아무 판정도 하지 않습니다."),
    )
    p.add_argument("visits", help="방문기록 CSV (long: 1행=1피험자-방문. --wide 면 1행=1피험자)")
    p.add_argument("--protocol", metavar="JSON", help="프로토콜 JSON (필수 — 없으면 exit 2)")
    p.add_argument("--subjects", metavar="CSV", help="피험자 CSV (군·등록일·탈락일·기준 항목). 없으면 축소 모드")
    p.add_argument("--as-of", dest="as_of", metavar="YYYY-MM-DD",
                   help="판정 기준일. 미지정 시 오늘 — 리포트에 크게 표시됨")
    p.add_argument("--out-dir", default="결과", metavar="DIR",
                   help="산출물 폴더 (기본: 결과). 모든 산출물은 이 안에만 씁니다")
    p.add_argument("--wide", action="store_true", help="방문기록이 1행=1피험자(방문명이 열 이름) 형태")
    p.add_argument("--min-coverage", type=float, default=70.0, metavar="PCT",
                   help="판정률 임계 %% (기본 70). 미만이면 exit 3")
    p.add_argument("--id-col", metavar="열", help="피험자ID 열 이름 직접 지정")
    p.add_argument("--visit-col", metavar="열", help="방문명 열 이름 직접 지정")
    p.add_argument("--date-col", metavar="열", help="방문일 열 이름 직접 지정")
    p.add_argument("--status-col", metavar="열", help="상태 열 이름 직접 지정")
    p.add_argument("--no-files", action="store_true", help="산출물 파일을 만들지 않고 화면 출력만")
    p.add_argument("--version", action="version", version=f"visitaudit {__version__}")
    return p


def _fail(msg: str) -> int:
    print(f"[!] {msg}", file=sys.stderr)
    print("    (visitaudit --help 로 사용법을 볼 수 있습니다)", file=sys.stderr)
    return EXIT_INPUT


def _dir_id(path: str):
    """폴더의 파일시스템 신원 (st_dev, st_ino). 없으면 None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _check_outdir(out_dir: str, inputs: List[str]) -> Optional[str]:
    """입력 파일이 out-dir 안에 있으면 거부 — 원본 보호.

    경로 **문자열**을 비교하면 안 된다. realpath 는 심볼릭 링크와 `..` 까지는
    풀어 주지만, 같은 폴더가 다른 문자열로 보이는 길이 macOS 에는 더 있다:
      · 대소문자 무시 파일시스템 (`data` vs `DATA`)
      · 유니코드 정규화 (한글 폴더명의 NFC vs NFD — Finder 붙여넣기와 직접 입력이
        서로 다른 바이트열을 만든다)
    그래서 문자열 대신 **파일시스템 신원(st_dev, st_ino)** 으로 비교한다.
    하드링크처럼 폴더가 달라도 같은 파일인 경우는 쓰기 직전에 한 번 더 막는다.
    """
    out_id = _dir_id(out_dir)
    out_real = os.path.realpath(out_dir)
    for path in inputs:
        if not path:
            continue
        # ① 같은 폴더인가 — 신원으로 비교(대소문자·유니코드 정규화·링크 무관)
        parent = os.path.dirname(os.path.abspath(path)) or os.curdir
        if out_id is not None and _dir_id(parent) == out_id:
            return f"입력 파일 {path} 이 --out-dir({out_dir}) 안에 있습니다 — 원본 보호를 위해 거부합니다"
        # ② out-dir 아래 더 깊은 곳에 있는가
        if os.path.realpath(path).startswith(out_real + os.sep):
            return f"입력 파일 {path} 이 --out-dir({out_dir}) 안에 있습니다 — 원본 보호를 위해 거부합니다"
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ── 경계 강제 장치: 프로토콜 없이는 판정하지 않는다 ─────────────
    if not args.protocol:
        print("[!] 프로토콜 JSON(--protocol) 이 없습니다.", file=sys.stderr)
        print("    이 툴의 판정 기준(방문창·순서·결측·PP 규칙)은 전부 프로토콜에서 옵니다.", file=sys.stderr)
        print("    프로토콜 없이 '누가 빠졌나'만 보려면 joinaudit 을 쓰세요 — 그건 그 툴의 일입니다.", file=sys.stderr)
        return EXIT_INPUT

    try:
        protocol: Protocol = load_protocol(args.protocol)
    except ProtocolError as e:
        return _fail(str(e))

    as_of_defaulted = args.as_of is None
    if as_of_defaulted:
        as_of = dt.date.today()
    else:
        try:
            as_of = parse_asof(args.as_of)
        except ValueError as e:
            return _fail(str(e))

    # NaN 은 모든 비교에서 False 라 아래 범위 검사를 그냥 통과한다 — isfinite 로 먼저 거른다 (B4)
    if not math.isfinite(args.min_coverage) or args.min_coverage < 0 or args.min_coverage > 100:
        return _fail(f"--min-coverage 는 0~100 사이의 유한한 수여야 합니다: {args.min_coverage}")

    if not args.no_files:
        inputs = [args.visits, args.subjects or "", args.protocol]
        err = _check_outdir(args.out_dir, inputs)
        if err:
            return _fail(err)
        # 쓰기 직전 신원 확인용(하드링크 방어) — 문자열 비교로는 못 잡는다
        set_protected_inputs(inputs)

    # ── 입력 읽기 (읽기 전용) ───────────────────────────────────────
    try:
        n_blank = 0
        n_blank_date = 0
        if args.wide:
            records, _enc, ignored = load_visits_wide(
                args.visits, protocol.visit_names(), id_col=args.id_col)
        else:
            records, _enc, n_blank, n_blank_date = load_visits_long(
                args.visits, id_col=args.id_col, visit_col=args.visit_col,
                date_col=args.date_col, status_col=args.status_col)
            ignored = []
        subjects = None
        subj_warnings: List[str] = []
        if args.subjects:
            subjects, subj_warnings, _senc = load_subjects(args.subjects, id_col=None)
    except InputError as e:
        return _fail(str(e))

    # ── 판정 ────────────────────────────────────────────────────────
    judged = judge(records, subjects, protocol, as_of)
    judged.warnings = subj_warnings + judged.warnings
    if n_blank:
        judged.notes.append(f"피험자ID/방문명이 빈 행 {n_blank}건 — 건너뜀")
    if n_blank_date:
        judged.notes.append(
            f"방문일이 빈 행 {n_blank_date}건 — 기록 없음으로 간주"
            "(창이 닫혔으면 결측, 아직이면 미도래로 판정)")
    if ignored:
        judged.notes.append(f"--wide: 프로토콜 방문명과 무관한 열 {len(ignored)}개 무시({', '.join(ignored[:6])}"
                            + (" …" if len(ignored) > 6 else "") + ")")
    # 기준 재점검도 이 기준시점에 등록된 사람만 본다 — 아직 안 들어온 사람의
    # 위반을 지금 보고하면 CONSORT·등록곡선과 같은 페이지에서 N 이 어긋난다.
    crit_subjects = subjects
    if subjects is not None and judged.not_yet_enrolled:
        skip = set(judged.not_yet_enrolled)
        crit_subjects = [s for s in subjects if s.sid not in skip]
    crit = recheck(crit_subjects, protocol)
    consort = build_consort(subjects, judged, protocol, as_of)
    pp = build_pp(judged, crit, subjects, protocol, as_of)
    enroll = build_enrollment(subjects, protocol.target_n, as_of)

    # ── 렌더링 + 산출물 ─────────────────────────────────────────────
    report_text = render_report(protocol, judged, crit, consort, pp, enroll,
                                as_of, as_of_defaulted, args.protocol,
                                args.min_coverage)
    print(report_text)

    if not args.no_files:
        # 진행점검.md 는 공유용 산출물 — 절대경로(사용자명 포함)를 박지 않는다 (B10)
        report_text_md = render_report(protocol, judged, crit, consort, pp, enroll,
                                       as_of, as_of_defaulted,
                                       os.path.basename(args.protocol),
                                       args.min_coverage)
        drafts = render_drafts(protocol, judged, crit, consort, pp, as_of)
        ctext = consort_text(consort, pp, protocol, as_of.isoformat())
        subjects_map = {}
        if subjects:
            for s in subjects:
                subjects_map.setdefault(s.sid, s)
        try:
            write_outputs(args.out_dir, report_text_md, drafts, ctext, judged, pp,
                          subjects_map, as_of)
        except OSError as e:
            return _fail(f"산출물을 쓸 수 없습니다({args.out_dir}): {e}")
        # 쓰기가 실제로 끝난 뒤에만 경로를 알린다 — 실패했는데 "출력: …" 를 먼저
        # 찍으면 만들어지지도 않은 파일 4개를 안내하게 된다.
        print("\n출력: " + ", ".join(os.path.join(args.out_dir, f) for f in OUT_FILES))

    # ── 종료코드 ────────────────────────────────────────────────────
    rate = judged.coverage_rate
    if rate is not None and rate < args.min_coverage:
        print(f"exit 3 (판정률 {rate:.1f}% < 임계 {args.min_coverage:.0f}% — 이 결과의 이탈 개수는 신뢰할 수 없습니다)")
        return EXIT_COVERAGE
    if judged.deviations:
        print(f"exit 1 (이탈 {len(judged.deviations)}건 발견)")
        return EXIT_DEVIATIONS
    print("exit 0 (이탈 없음)")
    return EXIT_OK


def main_guarded(argv: Optional[List[str]] = None) -> int:
    """main() 을 감싸 예기치 못한 예외가 exit 1 로 새는 것을 막는다.

    exit 1 은 '이탈 발견'이라는 뜻이라, 파이썬 트레이스백으로 죽으면서 1 을 남기면
    이 툴을 부르는 스크립트가 '점검은 정상적으로 끝났고 이탈이 있었다'로 읽는다.
    버그로 죽는 것은 입력·환경 오류(2)로 보고하고, 트레이스백은 사용자 경로가
    섞이지 않도록 한 줄 요약만 남긴다.
    """
    try:
        return main(argv)
    except KeyboardInterrupt:
        print("\n[!] 사용자가 중단했습니다.", file=sys.stderr)
        return EXIT_INPUT
    except RecursionError:
        return _fail("입력이 너무 깊게 중첩돼 처리할 수 없습니다(프로토콜 JSON 을 확인하세요)")
    except Exception as e:  # noqa: BLE001 — 마지막 방어선
        print(f"[!] 예기치 못한 오류로 중단했습니다: {type(e).__name__}: {e}", file=sys.stderr)
        print("    판정이 완료되지 않았으므로 이 실행 결과는 사용하지 마세요.", file=sys.stderr)
        return EXIT_INPUT


def run() -> None:  # console-script 진입점
    sys.exit(main_guarded())


if __name__ == "__main__":
    run()
