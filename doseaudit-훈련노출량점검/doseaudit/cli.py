"""명령줄 진입점.

종료코드는 `errors.py` 에 적힌 규약을 따른다. 특히 **파이프 뒤에 `| head` 를
붙여도 종료코드가 뒤집히지 않는다** — `BrokenPipeError` 를 삼키고 원래 코드로
끝낸다. (`doseaudit ... | head` 가 0을 돌려주면 CI 가 치명을 놓친다.)
"""

import argparse
import math
import os
import sys

from doseaudit import __version__
from doseaudit.audit import Config, run_audit
from doseaudit.artifacts import write_all
from doseaudit.errors import (EXIT_CLEAN, EXIT_REFUSE, DoseAuditError, RefuseError,
                              ReportIntegrityError)
from doseaudit.inspection import render_inspect
from doseaudit.logs import load_bundle
from doseaudit.paths import prepare_out_dir, resolved_note
from doseaudit.report import render_console
from doseaudit.sanitize import safe_text
from doseaudit.rules import ActiveDayRule, Window
from doseaudit.tracker import load_tracker

DESCRIPTION = """\
doseaudit — 훈련 노출량 점검

피험자별 앱 훈련 로그 워크북 묶음을 읽어, "이 피험자가 처방된 훈련을 실제로
얼마나 받았는가"를 **선언받은 정의로** 다시 셉니다. 순응/비순응을 판정하지 않고,
노출량과 결과의 관계도 보지 않습니다 — 분모가 무엇이었는지를 드러내고 끝납니다.
"""

EPILOG = """\
예)
  # ① 먼저 어떻게 읽었는지 본다 (판정 없음)
  doseaudit --inspect --logs wowfit_project17_excels/

  # ② 정의를 선언하고 센다
  doseaudit --logs wowfit_project17_excels/ \\
      --active-day any-row --window enroll-to-cut --cut 2026-06-23 \\
      --target-per-week 3 --out-dir 결과/

  # ③ 손으로 관리한 트래커와 대조한다
  doseaudit --logs wowfit_project17_excels/ --tracker 참여현황.xlsx \\
      --active-day any-row --window enroll-to-cut --cut 2026-06-23 \\
      --target-per-week 3 --out-dir 결과/

종료코드: 0 불일치 없음 · 1 치명 발견 · 2 판정 없이 거절 · 3 판정 불가(못 읽은 파일)
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="doseaudit", description=DESCRIPTION, epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", required=True, metavar="경로",
                        help="피험자별 로그 워크북(.xlsx)이 든 폴더, 또는 워크북 하나")
    parser.add_argument("--tracker", metavar="파일",
                        help="손으로 관리한 참여현황 표(.xlsx/.csv). Access Code 로만 조인합니다")
    parser.add_argument("--inspect", action="store_true",
                        help="판정하지 않고 '어떻게 읽었는지'와 선언 가능한 규칙들만 인쇄")
    parser.add_argument("--active-day", metavar="규칙",
                        help="활동일의 정의(필수): any-row | min-rows=N | min-modules=K | min-seconds=S")
    parser.add_argument("--window", metavar="창",
                        help="분모의 창(필수): enroll-to-cut | enroll-to-last | fixed-weeks=N "
                             "(N×7 이 정수 일수여야 합니다 — 최소 단위가 날짜입니다)")
    parser.add_argument("--cut", metavar="YYYY-MM-DD", help="`--window enroll-to-cut` 의 데이터컷 날짜")
    parser.add_argument("--enroll", choices=("tracker", "first-activity"),
                        help="관찰 시작일의 출처. 기본은 트래커가 있으면 tracker, 없으면 first-activity "
                             "(어느 쪽이든 리포트에 인쇄됩니다)")
    parser.add_argument("--target-per-week", type=float, metavar="일수",
                        help="처방된 주당 훈련일수. 주면 노출률(%%)과 정의 민감도 표가 나옵니다")
    parser.add_argument("--cap", type=float, metavar="퍼센트",
                        help="노출률 상한 절단(예: 100). **줄 때만** 절단하고, 절단된 인원수를 인쇄합니다")
    parser.add_argument("--criterion", type=float, metavar="퍼센트",
                        help="프로토콜이 정한 기준(예: 80). 줄 때만 충족/미달을 표시합니다 — "
                             "이 툴이 정한 값이 아닙니다")
    parser.add_argument("--pool-types", action="store_true",
                        help="REGULAR·INDIVIDUAL 을 합산합니다(기본은 분리). 합산하면 리포트에 인쇄됩니다")
    parser.add_argument("--out-dir", metavar="폴더", help="산출물 4종을 쓸 폴더")
    parser.add_argument("--no-files", action="store_true", help="파일을 쓰지 않고 콘솔만")
    parser.add_argument("--version", action="version", version="doseaudit %s" % __version__)
    return parser


def _harden_streams():
    """콘솔이 UTF-8 이 아니어도 죽지 않게 한다.

    리포트에는 `—`·`·`·`←`·`…` 가 쓰인다. cp949 콘솔(`PYTHONIOENCODING=cp949`,
    윈도우 기본, 일부 CI)에서는 이 글자들이 `UnicodeEncodeError` 를 내고, 그러면
    **깨끗한 자료가 종료코드 1(치명 발견)로 보고된다.** 글자 몇 개가 `?` 로 보이는
    편이 종료코드가 거짓말하는 것보다 낫다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


def _emit(lines, stream=None):
    stream = stream if stream is not None else sys.stdout
    text = "\n".join(lines) + "\n"
    if stream is None:          # stdout 이 닫힌 채 실행됨 (`>&-`, launchd, cron)
        return
    try:
        stream.write(text)
    except (BrokenPipeError, ValueError, OSError):
        pass
    except UnicodeEncodeError:  # 위 재설정이 실패한 콘솔
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, "backslashreplace").decode(encoding, "replace"))


def _finite(value, flag):
    """`nan`·`inf` 를 숫자로 받아들이지 않는다.

    `--criterion nan` 은 모든 비교를 False 로 만들어 **전원을 '미달'로 찍는다** —
    이 툴이 스스로는 절대 내리지 않겠다고 한 판정을, 인자 하나로 내리게 된다.
    """
    if value is None:
        return None
    if not math.isfinite(value):
        raise RefuseError("`%s` 에 유한한 숫자가 필요합니다 (받은 값: %s)" % (flag, value))
    return value


def main(argv=None):
    """실행 본체. 종료코드를 **돌려준다** (`sys.exit` 하지 않는다 — 테스트가 쉬워진다)."""
    args = build_parser().parse_args(argv)
    _harden_streams()

    _finite(args.cap, "--cap")
    _finite(args.target_per_week, "--target-per-week")
    _finite(args.criterion, "--criterion")

    if args.inspect:
        bundle = load_bundle(args.logs)
        tracker = load_tracker(args.tracker) if args.tracker else None
        _emit(render_inspect(bundle, tracker, logs_arg=args.logs,
                             tracker_arg=args.tracker))
        return EXIT_CLEAN

    if args.criterion is not None and args.target_per_week is None:
        raise RefuseError(
            "`--criterion` 은 `--target-per-week` 없이는 뜻이 없습니다.\n"
            "       기준을 노출률(%)과 견주려면 처방된 주당 훈련일수를 먼저 선언하세요."
        )
    if args.cap is not None and args.cap <= 0:
        raise RefuseError("`--cap` 은 0보다 커야 합니다")
    if args.target_per_week is not None and args.target_per_week <= 0:
        raise RefuseError("`--target-per-week` 는 0보다 커야 합니다")

    rule = ActiveDayRule.parse(args.active_day)
    window = Window.parse(args.window, args.cut)
    if args.cut and args.window != "enroll-to-cut":
        raise RefuseError("`--cut` 은 `--window enroll-to-cut` 과 함께만 쓸 수 있습니다")
    if args.enroll == "tracker" and not args.tracker:
        raise RefuseError("`--enroll tracker` 를 쓰려면 `--tracker` 가 필요합니다")

    if args.out_dir is not None and not args.out_dir.strip():
        raise RefuseError("`--out-dir` 이 빈 문자열입니다 — 쓸 곳을 정하지 못했습니다")

    out_dir = None
    if args.out_dir and not args.no_files:
        out_dir = prepare_out_dir(args.out_dir)

    config = Config(logs=args.logs, tracker_path=args.tracker, rule=rule, window=window,
                    enroll_source=args.enroll, cap=args.cap,
                    target_per_week=args.target_per_week, pool_types=args.pool_types,
                    criterion=args.criterion, out_dir=out_dir)
    result = run_audit(config)
    lines = render_console(result)

    if out_dir:
        written = write_all(result, out_dir, lines)
        lines.append("")
        lines.append("만들어진 파일 (%s):"
                     % safe_text(os.path.basename(os.path.abspath(out_dir)), max_len=60))
        for name in written:
            lines.append("  · %s" % name)
        actual = resolved_note(out_dir)
        if actual:
            # 링크를 거쳐 다른 곳에 쓰였다 — 어디인지 숨기지 않는다.
            lines.append("  (링크를 따라 실제로 쓰인 곳: %s)" % safe_text(actual, max_len=160))
    elif not args.no_files and not args.out_dir:
        lines.append("")
        lines.append("(`--out-dir 폴더` 를 주면 피험자별노출량.csv · 모듈별요약.csv ·")
        lines.append(" 트래커불일치.csv · 노출량점검.md 를 만듭니다.)")

    _emit(lines)
    return result.exit_code


def run(argv=None):
    """콘솔 스크립트 진입점. 예외를 한국어 한 줄로 바꾸고 종료코드를 낸다."""
    try:
        code = main(argv)
    except RefuseError as exc:
        _safe_write(sys.stderr, "[거절] %s\n" % exc)
        _safe_write(sys.stderr, "       판정하지 않았습니다 — 추측해서 세는 것보다 멈추는 편이 낫습니다.\n")
        code = EXIT_REFUSE
    except ReportIntegrityError as exc:
        _safe_write(sys.stderr, "[중단] %s\n" % exc)
        code = EXIT_REFUSE
    except DoseAuditError as exc:
        _safe_write(sys.stderr, "[오류] %s\n" % exc)
        code = exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover
        _safe_write(sys.stderr, "\n[중단] 사용자가 취소했습니다.\n")
        code = 130
    except Exception as exc:
        # 예상 못 한 예외가 새어 나가면 CPython 은 **종료코드 1** 로 끝난다 —
        # 이 툴에서 1은 "치명 발견"이다. 즉 크래시와 진짜 발견이 스크립트·CI 에서
        # 구분되지 않는다. 판정한 적이 없으므로 '판정 없이 거절'(2)로 끝낸다.
        _safe_write(sys.stderr,
                    "[오류] 예상하지 못한 내부 오류입니다: %s: %s\n"
                    % (type(exc).__name__, exc))
        _safe_write(sys.stderr,
                    "       판정하지 않았습니다 — 이 종료코드(2)를 '이상 없음'으로 읽지 마십시오.\n")
        code = EXIT_REFUSE
    _flush_quietly()
    sys.exit(code)


def _safe_write(stream, text):
    if stream is None:          # `2>&-` 로 실행되면 sys.stderr 가 None 이다
        return
    try:
        stream.write(text)
    except (BrokenPipeError, ValueError, OSError, UnicodeEncodeError):
        pass


def _flush_quietly():
    """`| head` 로 파이프가 끊겨도 종료코드를 뒤집지 않는다.

    파이프가 닫힌 뒤 인터프리터 종료 시점에 stdout 을 flush 하면 파이썬이
    `BrokenPipeError` 를 찍고 종료코드 120 을 남긴다 — 치명 1 이 120 으로
    바뀌면 CI 가 무엇을 봐야 할지 알 수 없다. 여기서 미리 삼킨다.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:      # `>&-` / `2>&-` 로 실행되면 None 이다
            continue
        try:
            stream.flush()
        except (BrokenPipeError, ValueError, OSError, UnicodeEncodeError):
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, stream.fileno())
            except (OSError, ValueError, AttributeError):
                pass


if __name__ == "__main__":  # pragma: no cover
    run()
