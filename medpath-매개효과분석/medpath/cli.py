"""Command-line interface for medpath."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import List, Optional, Sequence

from . import __version__
from .dataio import DataError, build_design, load_table
from .linalg import SingularDesignError
from .mediation import analyze
from .report import render

EXIT_OK = 0
EXIT_ERROR = 1

_EPILOG = """\
예시:
  # 1) 단순매개: 중재군(arm) → HRV(rmssd_ms) → 서파수면(sws_min), 나이 보정
  medpath sleep.csv --x arm --m rmssd_ms --y sws_min --covariates age

  # 2) 병렬 다중매개: 매개변수 두 개를 동시에
  medpath sleep.csv --x arm --m rmssd_ms,resp_rate --y sws_min

  # 3) 직렬(연쇄) 매개: 호흡 → HRV → 서파수면 → ISI 개선 (순서대로)
  medpath sleep.csv --x arm --m rmssd_ms,sws_min --y isi_change --serial

  # 4) 열 이름이 기억나지 않을 때
  medpath sleep.csv --list-columns
"""


def _split_list(values: Optional[Sequence[str]]) -> List[str]:
    """Accept both repeated flags and comma-separated values."""
    out: List[str] = []
    for v in values or []:
        for part in v.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="medpath",
        description="매개효과(간접효과) 분석 — X → M → Y 경로를 부트스트랩으로 검정합니다. "
                    "병렬/직렬 다중매개, 공변량 보정, 논문용 문장까지. 외부 의존성 없음.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", help="분석할 CSV 파일 경로")
    p.add_argument("-x", "--x", dest="x", help="독립변수(X) 열 이름")
    p.add_argument("-m", "--m", "--mediator", dest="mediators", action="append",
                   help="매개변수(M) 열 이름 — 쉼표로 여러 개 (직렬이면 순서대로)")
    p.add_argument("-y", "--y", dest="y", help="종속변수(Y) 열 이름")
    p.add_argument("-c", "--cov", "--covariates", dest="covariates", action="append",
                   help="공변량 열 이름 — 쉼표로 여러 개 (범주형은 자동 가변수화)")
    p.add_argument("--serial", action="store_true",
                   help="직렬(연쇄) 매개모형: M들을 지정한 순서대로 X→M1→M2→…→Y 로 연결")
    p.add_argument("--reference", help="X가 범주형일 때 기준(0)이 될 수준")
    p.add_argument("--x-levels", help="X 수준이 3개 이상일 때 비교할 두 수준: 기준,비교")
    p.add_argument("--bootstrap", type=int, default=5000, metavar="N",
                   help="부트스트랩 반복 수 (기본 5000, 0이면 생략)")
    p.add_argument("--ci", choices=["percentile", "bc", "bca"], default="percentile",
                   help="신뢰구간 방식 (기본 percentile — PROCESS 기본값과 동일)")
    p.add_argument("--conf", type=float, default=95.0, metavar="LEVEL",
                   help="신뢰수준 (95 또는 0.95 모두 가능, 기본 95)")
    p.add_argument("--seed", type=int, default=20260731,
                   help="부트스트랩 난수 시드 (같은 시드 → 같은 결과)")
    p.add_argument("--robust", choices=["none", "hc3"], default="none",
                   help="경로계수 표준오차: hc3 = 이분산 강건 (부트스트랩 구간은 영향 없음)")
    p.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="부트스트랩 병렬 프로세스 수 (기본 1; 결과는 개수와 무관하게 동일)")
    p.add_argument("--delimiter", help="CSV 구분자 강제 지정 (기본: 자동 인식)")
    p.add_argument("--digits", type=int, default=3, help="소수점 자리수 (기본 3)")
    p.add_argument("--brief", action="store_true", help="핵심 결과만 짧게 출력")
    p.add_argument("--no-diagnostics", action="store_true",
                   help="VIF·이분산·영향점 진단을 건너뜁니다(대용량에서 빠름)")
    p.add_argument("--json", action="store_true", help="사람이 읽는 표 대신 JSON 출력")
    p.add_argument("--markdown", action="store_true", help="Markdown 형식으로 출력")
    p.add_argument("--out", metavar="FILE", help="결과를 파일로 저장 (화면에도 요약 표시)")
    p.add_argument("--list-columns", action="store_true",
                   help="CSV의 열 이름만 보여주고 종료")
    p.add_argument("--version", action="version", version="medpath %s" % __version__)
    return p


def _resolve_conf(raw: float) -> float:
    conf = raw / 100.0 if raw > 1.0 else raw
    if not (0.5 <= conf < 0.99999):
        raise DataError("--conf 는 50~99.99 (또는 0.5~0.9999) 범위여야 합니다: %r" % raw)
    return conf


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        table = load_table(args.csv, args.delimiter)

        if args.list_columns:
            print("CSV: %s" % args.csv)
            print("행 수: %d" % len(table))
            print("열 이름 (%d개):" % len(table.header))
            for h in table.header:
                print("  - %s" % h)
            for note in table.notes:
                print("  ! %s" % note)
            return EXIT_OK

        missing_opts = [flag for flag, val in
                        (("--x", args.x), ("--m", args.mediators), ("--y", args.y))
                        if not val]
        if missing_opts:
            raise DataError(
                "%s 옵션이 필요합니다. 열 이름을 모르면 `medpath %s --list-columns` 로 확인하세요."
                % (", ".join(missing_opts), args.csv))

        mediators = _split_list(args.mediators)
        covariates = _split_list(args.covariates)
        conf = _resolve_conf(args.conf)
        if args.bootstrap < 0:
            raise DataError("--bootstrap 은 0 이상이어야 합니다.")
        if args.bootstrap > 200000:
            raise DataError("--bootstrap 이 너무 큽니다(최대 200000).")
        if args.digits < 0 or args.digits > 10:
            raise DataError("--digits 는 0~10 범위여야 합니다.")
        if args.jobs < 1:
            raise DataError("--jobs 는 1 이상이어야 합니다.")
        if args.ci == "bca" and args.bootstrap == 0:
            raise DataError("--ci bca 는 부트스트랩이 필요합니다(--bootstrap 을 0보다 크게).")
        x_levels = None
        if args.x_levels:
            x_levels = [s.strip() for s in args.x_levels.split(",")]

        design = build_design(table, args.x, mediators, args.y, covariates,
                              reference=args.reference, x_levels=x_levels)
        if args.serial and len(mediators) < 2:
            raise DataError("--serial 은 매개변수를 2개 이상 지정해야 합니다 "
                            "(예: --m 호흡수,rmssd_ms).")
        if args.ci == "bca" and design.n_used > 20000:
            raise DataError(
                "BCa는 잭나이프가 필요해 N=%d에서는 매우 느립니다. "
                "--ci percentile 또는 --ci bc 를 쓰세요." % design.n_used)

        result = analyze(
            design,
            serial=args.serial,
            conf=conf,
            n_boot=args.bootstrap,
            seed=args.seed,
            ci_method=args.ci,
            robust=None if args.robust == "none" else args.robust,
            jobs=args.jobs,
            diagnostics=not args.no_diagnostics,
        )
    except (DataError, SingularDesignError) as exc:
        print("오류: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print("오류: %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        payload = result.to_dict()
        payload["source"] = args.csv
        payload["medpath_version"] = __version__
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=True)
    else:
        text = render(result, args.csv, mode="md" if args.markdown else "text",
                      digits=args.digits, brief=args.brief)

    if args.out:
        try:
            out_dir = os.path.dirname(os.path.abspath(args.out))
            if out_dir and not os.path.isdir(out_dir):
                raise DataError("저장할 폴더가 없습니다: %s" % out_dir)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
                if not text.endswith("\n"):
                    fh.write("\n")
        except OSError as exc:
            print("오류: 결과를 저장하지 못했습니다 (%s)" % exc, file=sys.stderr)
            return EXIT_ERROR
        except DataError as exc:
            print("오류: %s" % exc, file=sys.stderr)
            return EXIT_ERROR
        print("결과를 저장했습니다: %s" % args.out)
        if not args.json:
            inds = result.indirect_effects
            for e in inds:
                print("  %s = %.4g, %g%% CI [%.4g, %.4g] %s"
                      % (e.label, e.estimate, result.conf * 100, e.ci_lo, e.ci_hi,
                         "*" if e.significant else ""))
        return EXIT_OK

    print(text)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
