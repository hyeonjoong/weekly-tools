"""robustcheck CLI.

종료코드
  0  견고 또는 주의 — 치명 뒤집힘 0건
  1  취약 — 치명 뒤집힘(① 유의→비유의, ③ 부호 반전) 또는 단독 뒤집기 피험자 1명 이상
  2  입력·인자 오류 (주 분석 미지정, 열 없음, 파일 없음, 군이 2개가 아님, ID 중복)
  3  판정 불가 — 유효 N < 6 또는 계산된 시나리오 < 5  ← **1보다 우선한다**

`--design` 과 그 설계가 요구하는 열이 없으면 아무 판정도 하지 않고 exit 2 다.
이 툴은 검정을 골라 주지 않는다.
"""

import argparse
import os
import sys
from typing import List, Optional, Sequence, Tuple

from . import __version__
from .analyze import analyse
from .dataio import InputError, read_table
from .loo import DEFAULT_LOO_BUDGET, DEFAULT_LOO_MAX_N
from .report import (
    OUT_ISSUES,
    OUT_REPORT,
    OUT_SCENARIOS,
    OUT_SUBJECTS,
    ReportIntegrityError,
    render_issues_csv,
    render_markdown,
    render_report,
    render_scenarios_csv,
    render_subjects_csv,
)
from .safety import (
    OutputPathError,
    assert_not_input,
    open_for_write,
    prepare_out_dir,
    safe_join,
)
from .spec import Spec, build_dataset

__all__ = ["main", "build_parser"]

DEFAULT_OUT_DIR = "robustcheck_결과"

_EPILOG = """\
예)
  robustcheck data.csv --design two-group --group arm --value isi_week4 \\
      --covariate-baseline isi_baseline --out-dir 결과/
  robustcheck data.csv --design paired --pre isi_baseline --post isi_week4 --out-dir 결과/
  robustcheck data.csv --design corr --x rmssd_ms --y isi_week4 --out-dir 결과/

이 툴은 '가장 유의한 조합'을 추천하지 않습니다. 정렬 기준은 뒤집힘 여부입니다.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robustcheck",
        description=(
            "이미 정해진 주 분석 하나의 결론이 분석 선택(이상치·결측·검정·변환·"
            "피험자 제외)을 바꿔도 살아남는지를 전수 재계산해서 확인합니다. "
            "새 결론을 만들지 않습니다."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("csv", nargs="?", help="분석용 CSV (1행 = 1피험자)")
    parser.add_argument("--design", choices=("two-group", "paired", "corr"),
                        help="주 분석 설계 (필수)")
    parser.add_argument("--id", dest="id_col", default="subject_id",
                        help="피험자 ID 열 (기본: subject_id)")
    parser.add_argument("--value", help="two-group: 결과변수 열")
    parser.add_argument("--group", help="two-group: 군 열 (정확히 2군)")
    parser.add_argument("--covariate-baseline", dest="covariate",
                        help="two-group: 기저값 보정(ANCOVA) 공변량 열")
    parser.add_argument("--pre", help="paired: 사전 값 열")
    parser.add_argument("--post", help="paired: 사후 값 열")
    parser.add_argument("--x", help="corr: 변수 1")
    parser.add_argument("--y", help="corr: 변수 2")
    parser.add_argument("--timepoint", metavar="열=값",
                        help="시점별 long 포맷에서 한 시점만 고릅니다 "
                             "(예: --timepoint timepoint=week0)")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="유의수준 (기본 0.05)")
    parser.add_argument("--equal-var", action="store_true",
                        help="모수 검정으로 Welch 대신 등분산 Student t 를 씁니다 "
                             "(--covariate-baseline 과 함께 쓰면 무시됩니다)")
    parser.add_argument("--no-log", dest="use_log", action="store_false",
                        help="로그변환 축(E)을 아예 빼고 돌립니다. ISI 처럼 "
                             "로그가 말이 안 되는 지표에서 소음을 없앱니다 "
                             "(뺐다는 사실은 리포트에 인쇄됩니다)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help="산출물 폴더 (기본: %s)" % DEFAULT_OUT_DIR)
    parser.add_argument("--no-files", action="store_true",
                        help="파일을 쓰지 않고 화면에만 출력합니다")
    parser.add_argument("--quiet", action="store_true",
                        help="리포트 본문 대신 마지막 판정 줄만 출력합니다")
    parser.add_argument("--loo-max-n", type=int, default=DEFAULT_LOO_MAX_N,
                        help="이보다 피험자가 많으면 leave-one-out 을 건너뜁니다 "
                             "(기본 %d)" % DEFAULT_LOO_MAX_N)
    parser.add_argument("--loo-budget", type=int, default=DEFAULT_LOO_BUDGET,
                        help="leave-one-out 총 재계산 횟수 상한 (기본 %d)"
                             % DEFAULT_LOO_BUDGET)
    parser.add_argument("--version", action="version",
                        version="robustcheck %s" % __version__)
    return parser


def _parse_timepoint(raw: Optional[str]) -> Optional[Tuple[str, str]]:
    if not raw:
        return None
    if "=" not in raw:
        raise InputError("--timepoint 는 `열=값` 형식이어야 합니다 (받은 값: %r)" % raw)
    col, value = raw.split("=", 1)
    col, value = col.strip(), value.strip()
    if not col or not value:
        raise InputError("--timepoint 의 열 이름과 값이 모두 필요합니다 (받은 값: %r)"
                         % raw)
    return (col, value)


def _write_outputs(analysis, out_dir: str, input_path: str) -> List[str]:
    """산출물 4종을 **전부 아니면 전무**로 쓴다.

    예전에는 한 파일이라도 열리지 않으면 앞의 두 개만 남고, 나머지 둘은 **이전
    실행의 내용**이 그대로 남았다 — 사용자는 두 실행이 섞인 폴더를 보게 된다.
    임시 파일에 모두 쓴 뒤 마지막에 한꺼번에 자리를 바꾼다.
    """
    resolved = prepare_out_dir(out_dir)
    payload = {
        OUT_REPORT: render_markdown(analysis),
        OUT_SCENARIOS: render_scenarios_csv(analysis),
        OUT_SUBJECTS: render_subjects_csv(analysis),
        OUT_ISSUES: render_issues_csv(analysis),
    }
    finals = {name: safe_join(resolved, name) for name in payload}
    temps = {name: safe_join(resolved, ".%s.rc-tmp" % name) for name in payload}
    assert_not_input(list(finals.values()) + list(temps.values()), [input_path])

    written: List[str] = []
    try:
        for name, text in payload.items():
            with open_for_write(temps[name]) as fh:
                fh.write(text)
        for name in payload:
            os.replace(temps[name], finals[name])
            written.append(finals[name])
    except (OutputPathError, OSError):
        for path in temps.values():
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    stream = sys.stdout

    try:
        if not args.csv:
            raise InputError(
                "분석할 CSV 파일이 필요합니다.\n"
                "  이 툴은 주 분석을 스스로 고르지 않습니다 — "
                "`--design` 과 해당 열을 함께 지정해 주세요."
            )
        if not args.design:
            raise InputError(
                "--design 이 필요합니다 (two-group / paired / corr).\n"
                "  robustcheck 는 검정을 골라 주지 않습니다. 이미 정해 둔 주 분석을 "
                "명시하면, 그 결론이 분석 선택을 바꿔도 살아남는지만 확인합니다.\n"
                "  검정 선택이 필요하면 statwise 를 쓰세요."
            )
        spec = Spec(
            design=args.design,
            id_col=args.id_col,
            value=args.value,
            group=args.group,
            pre=args.pre,
            post=args.post,
            x=args.x,
            y=args.y,
            covariate=args.covariate,
            alpha=args.alpha,
            timepoint=_parse_timepoint(args.timepoint),
        )
        table = read_table(args.csv)
        dataset = build_dataset(table, spec)
    except InputError as exc:
        print("robustcheck: 입력·인자 오류\n  %s" % exc, file=sys.stderr)
        return 2

    if args.loo_max_n < 0 or args.loo_budget < 0:
        print("robustcheck: --loo-max-n / --loo-budget 은 0 이상이어야 합니다.",
              file=sys.stderr)
        return 2

    # 산출물 경로는 **리포트를 인쇄하기 전에** 확인한다. 뒤로 미루면
    # "종료코드 1" 이라고 찍어 놓고 실제로는 2 를 돌려주게 된다.
    if not args.no_files:
        try:
            prepare_out_dir(args.out_dir)
        except (OutputPathError, OSError) as exc:
            print("robustcheck: 산출물 폴더를 쓸 수 없습니다\n  %s" % exc,
                  file=sys.stderr)
            return 2

    analysis = analyse(dataset, equal_var=args.equal_var,
                       loo_max_n=args.loo_max_n, loo_budget=args.loo_budget,
                       use_log=args.use_log)
    analysis.writes_files = not args.no_files

    try:
        body = render_report(analysis)
    except ReportIntegrityError as exc:
        print("robustcheck: %s" % exc, file=sys.stderr)
        return 3

    # **파일을 먼저 쓰고 리포트를 나중에 인쇄한다.** 반대로 하면 화면에는
    # "종료코드 1" 이 찍히고 프로세스는 2 로 죽어, stdout 을 읽는 스크립트와
    # $? 를 보는 스크립트가 다른 답을 얻는다.
    written: List[str] = []
    if not args.no_files:
        try:
            written = _write_outputs(analysis, args.out_dir, args.csv)
        except (OutputPathError, OSError) as exc:
            print("robustcheck: 산출물을 쓸 수 없습니다\n  %s" % exc, file=sys.stderr)
            return 2
        except ReportIntegrityError as exc:  # pragma: no cover - 위에서 이미 막힌다
            print("robustcheck: %s" % exc, file=sys.stderr)
            return 3

    if args.quiet:
        print("판정: %s (종료코드 %d)"
              % (analysis.verdict.summary(), analysis.exit_code), file=stream)
    else:
        print(body, file=stream)

    if written:
        print("\n산출물: %s" % os.path.dirname(written[0]), file=stream)
        for path in written:
            print("  · %s" % os.path.basename(path), file=stream)

    return analysis.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
