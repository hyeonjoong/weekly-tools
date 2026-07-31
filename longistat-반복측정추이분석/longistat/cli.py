"""longistat 명령줄 인터페이스 — 반복측정(전-후, 다시점) 자료 분석.

예시
----
긴 형식(한 행 = 한 대상 × 한 시점), 두 군 비교::

    longistat isi.csv --id 대상 --time 방문 --value ISI --group 군 \\
        --baseline 기저 --mcid 6 --direction lower

넓은 형식(한 행 = 한 대상, 열 = 시점)::

    longistat wide.csv --wide --id 대상 --columns 기저,4주,8주 --group 군
"""

from __future__ import annotations

import argparse
import errno
import math
import os
import sys
from typing import Dict, List, Optional, Sequence

from . import __version__
from .analyze import Options, analyze
from .dataio import DataError, load_long, load_wide
from .report import render_csv, render_json, render_markdown, render_text

__all__ = ["main", "build_parser"]

_EPILOG = """\
자주 쓰는 조합
  longistat isi.csv --id id --time visit --value isi
  longistat isi.csv --id id --time visit --value isi --group arm --mcid 6 --direction lower
  longistat wide.csv --wide --id id --columns base,wk4,wk8 --format json -o out.json

출력 해석
  · [1] 결측     = CONSORT 흐름도에 넣을 군별·시점별 관측 수와 탈락 패턴
  · [4] 주 분석  = 시점/그룹/상호작용의 omnibus 검정 (구형성 보정 자동 적용)
  · [4b] 추세    = 선형·이차 추세 대비 + 개인별 기울기(점/주) — 방문 간격이
                   불규칙하면 --time-values 0,4,12,24 로 알려주세요
  · [5] 변화량   = 기저 대비 변화 + 군간 차이 + 기저값 보정(ANCOVA)
                   + 결측 대체 민감도(LOCF·BOCF)
  · [8] 반응자   = MCID 이상 좋아진 사람의 비율과 군간 차이(RD/RR/OR/NNT)
  · [9] RCI      = 개인 수준에서 '측정오차보다 큰 변화'인지 (Jacobson-Truax)

주의: [4]와 [5]는 모든 시점이 관측된 대상만 쓰는 완전사례 분석입니다 (ITT 아님).
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="longistat",
        description="반복측정(전-후·다시점) 자료를 한 번에 분석합니다 — "
                    "기술통계·정규성/구형성 점검·반복측정/혼합 ANOVA(+GG/HF 보정)·"
                    "Friedman·사후비교·기저대비 변화량·반응자(MCID)·RCI·논문용 문장.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv", help="입력 CSV 파일 경로")

    fmt = p.add_argument_group("자료 형식")
    fmt.add_argument("--wide", action="store_true",
                     help="넓은 형식: 한 행이 한 대상, 시점마다 열이 하나 "
                          "(--columns 필요)")
    fmt.add_argument("--id", help="대상 식별자 열 이름 (긴 형식에서는 필수)")
    fmt.add_argument("--time", help="(긴 형식) 시점 열 이름")
    fmt.add_argument("--value", help="(긴 형식) 측정값 열 이름")
    fmt.add_argument("--columns",
                     help="(넓은 형식) 시점 열들을 시간 순서대로 쉼표로 나열")
    fmt.add_argument("--group", help="그룹(군) 열 이름 — 지정하면 혼합 ANOVA를 수행")
    fmt.add_argument("--time-order",
                     help="(긴 형식) 시점 순서를 직접 지정 (쉼표 구분). "
                          "지정하지 않으면 숫자면 숫자순, 아니면 파일 등장 순서")
    fmt.add_argument("--baseline", help="기준(기저) 시점 이름 (기본: 첫 시점)")
    fmt.add_argument("--delimiter", help="구분자 직접 지정 (기본: 자동 인식)")
    fmt.add_argument("--duplicates", choices=["error", "mean", "first"],
                     default="error",
                     help="같은 (대상, 시점)이 여러 번 있을 때 처리 "
                          "(기본 error: 오류로 알림)")

    stat = p.add_argument_group("통계 옵션")
    stat.add_argument("--alpha", type=float, default=0.05,
                      help="유의수준 (기본 0.05)")
    stat.add_argument("--alpha-norm", type=float, default=0.05,
                      help="정규성·구형성 판정용 유의수준 (기본 0.05)")
    stat.add_argument("--method", choices=["auto", "parametric", "nonparametric"],
                      default="auto",
                      help="보고할 주 분석 (기본 auto: 정규성 점검 결과로 권장안 표시). "
                           "모수·비모수 결과는 항상 함께 계산됩니다")
    stat.add_argument("--sphericity", choices=["auto", "gg", "hf", "none"],
                      default="auto",
                      help="구형성 보정 (기본 auto: Mauchly 기각 시 ε̂_GG ≥ .75면 "
                           "HF, 아니면 GG — Girden 1992)")
    stat.add_argument("--correction", choices=["holm", "bh", "none"],
                      default="holm",
                      help="다중비교 보정 (기본 holm, bh=FDR)")
    stat.add_argument("--equal-var", action="store_true",
                      help="군간 비교에 등분산 가정 Student t 사용 (기본은 Welch)")
    stat.add_argument("--primary-time", metavar="시점",
                      help="계획서에 사전 지정한 주요 시점. 그 시점의 군간 비교는 "
                           "다중비교 보정 없이 보고하고, 나머지 시점끼리만 보정합니다")
    stat.add_argument("--time-values", metavar="0,4,12,24",
                      help="방문의 실제 간격(숫자, 시점 순서대로). 추세 대비와 "
                           "개인 기울기 계산에 씁니다. 지정하지 않으면 시점 "
                           "이름의 숫자를 읽고, 그것도 없으면 등간격 가정")
    stat.add_argument("--time-unit", metavar="주",
                      help="--time-values 의 단위 이름 (기울기 표에 '점/주' 처럼 표시)")
    stat.add_argument("--no-trend", action="store_true",
                      help="시점 추세(직교 다항 대비·개인 기울기) 구획을 생략")
    stat.add_argument("--sensitivity", default="auto",
                      metavar="auto|none|locf,bocf",
                      help="결측 대체 민감도 분석 (기본 auto: 결측이 있으면 "
                           "LOCF·BOCF 를 함께 계산해 결론이 흔들리는지 확인)")
    stat.add_argument("--all-pairs", action="store_true",
                      help="시점이 12개를 넘어도 모든 시점 조합을 비교 "
                           "(기본은 기준시점 대비 + 인접 시점만)")

    clin = p.add_argument_group("임상 해석 옵션")
    clin.add_argument("--mcid", type=float,
                      help="최소임상중요차이(MCID). 지정하면 반응자 분석을 수행")
    clin.add_argument("--mcid-percent", action="store_true",
                      help="--mcid 를 기저 대비 %% 개선으로 해석")
    clin.add_argument("--direction", choices=["lower", "higher"],
                      help="어느 쪽이 '좋아지는' 것인지 "
                           "(lower=점수가 낮아지면 호전, 예 ISI/PHQ). "
                           "--mcid/--reliability 사용 시 필수")
    clin.add_argument("--responder-test", choices=["fisher", "chi2"],
                      default="fisher",
                      help="반응률 군간 비교 검정 (기본 fisher)")
    clin.add_argument("--responder-denominator",
                      choices=["observed", "randomized"], default="observed",
                      help="반응률 분모: observed(해당 시점 관측자) 또는 "
                           "randomized(무응답 대체 NRI — 탈락자를 비반응으로 계산, "
                           "ITT 보고에 필요)")
    clin.add_argument("--reliability", type=float,
                      help="도구의 검사-재검사 신뢰도(0~1). 지정하면 RCI 분석 수행")
    clin.add_argument("--rci-sd", type=float,
                      help="RCI 계산에 쓸 기준시점 SD (기본: 관측된 기저 SD)")
    clin.add_argument("--rci-cutoff", type=float, default=1.96,
                      help="RCI 컷오프 (기본 1.96 = 95%%)")
    clin.add_argument("--recovery-cutoff", type=float,
                      help="'회복' 판정 절단점 (예: ISI ≤ 7). --direction 방향으로 해석")

    out = p.add_argument_group("출력")
    out.add_argument("--format", choices=["text", "md", "json", "csv"],
                     default="text",
                     help="출력 형식 (기본 text; md=논문/Word 붙여넣기용 표)")
    out.add_argument("--labels-en", metavar="한글=English,...",
                     help="영문 문장에 쓸 시점·그룹 이름 "
                          "(예: '기저=Baseline,능동자극=Active')")
    out.add_argument("-o", "--output", help="결과를 파일로 저장")
    out.add_argument("--overwrite", action="store_true",
                     help="--output 파일이 이미 있어도 덮어쓰기")
    out.add_argument("--full", action="store_true",
                     help="[4] 주 분석에서 ANOVA와 Friedman 결과를 모두 표로 출력 "
                          "(사후비교·변화량은 권장 트랙만 표시; 두 트랙 전부는 "
                          "--format json/csv 에 들어 있습니다)")
    out.add_argument("--brief", action="store_true",
                     help="사후비교 표를 생략하고 요약만 출력")
    out.add_argument("--version", action="version",
                     version=f"longistat {__version__}")
    return p


def _finite(value: Optional[float], flag: str) -> None:
    """NaN passes every ``<=`` comparison, so guard it explicitly.

    ``--mcid nan`` used to sail through validation and produce a fully
    formatted report in which nobody was a responder.
    """
    if value is not None and not math.isfinite(value):
        raise DataError(f"{flag} 에 숫자가 아닌 값(nan/inf)은 쓸 수 없습니다.")


def _parse_labels(spec: Optional[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not spec:
        return out
    for part in spec.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise DataError(
                "--labels-en 은 '한글=English' 쌍을 쉼표로 구분해 적습니다.")
        key, _, val = part.partition("=")
        if not key.strip() or not val.strip():
            raise DataError("--labels-en 의 이름이 비어 있습니다.")
        out[key.strip()] = val.strip()
    return out


def _parse_time_values(spec: Optional[str]) -> Optional[List[float]]:
    """``--time-values 0,4,12,24`` → floats, with a readable error on junk."""
    if spec is None:
        return None
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if len(parts) < 2:
        raise DataError("--time-values 에는 시점 개수만큼의 숫자를 쉼표로 "
                        "구분해 적습니다 (예: 0,4,12,24).")
    out: List[float] = []
    for part in parts:
        try:
            value = float(part)
        except ValueError:
            raise DataError(
                f"--time-values 의 '{part}' 은(는) 숫자가 아닙니다.") from None
        if not math.isfinite(value):
            raise DataError("--time-values 에 nan/inf 는 쓸 수 없습니다.")
        out.append(value)
    return out


def _validate(args: argparse.Namespace) -> None:
    for value, flag in ((args.alpha, "--alpha"),
                        (args.alpha_norm, "--alpha-norm"),
                        (args.rci_cutoff, "--rci-cutoff"),
                        (args.mcid, "--mcid"),
                        (args.reliability, "--reliability"),
                        (args.rci_sd, "--rci-sd"),
                        (args.recovery_cutoff, "--recovery-cutoff")):
        _finite(value, flag)
    if not 0.0 < args.alpha < 1.0:
        raise DataError("--alpha 는 0과 1 사이여야 합니다.")
    if args.alpha < 1e-6 or args.alpha_norm < 1e-6:
        raise DataError("--alpha / --alpha-norm 은 1e-6 이상이어야 합니다.")
    if not 0.0 < args.alpha_norm < 1.0:
        raise DataError("--alpha-norm 은 0과 1 사이여야 합니다.")
    if args.rci_cutoff <= 0:
        raise DataError("--rci-cutoff 는 0보다 커야 합니다.")
    if args.rci_sd is not None and args.rci_sd <= 0:
        raise DataError("--rci-sd 는 0보다 커야 합니다.")
    if args.mcid is not None and args.mcid <= 0:
        raise DataError("--mcid 는 0보다 커야 합니다 (개선 폭의 크기).")
    if args.direction and args.mcid is None and args.reliability is None:
        raise DataError(
            "--direction 은 --mcid 또는 --reliability 와 함께 쓸 때만 "
            "의미가 있습니다.")
    if args.recovery_cutoff is not None and args.reliability is None:
        raise DataError("--recovery-cutoff 는 --reliability 와 함께 쓰세요 "
                        "(RCI 분석의 회복 판정 기준입니다).")
    if args.delimiter is not None and args.delimiter not in ("\\t",) \
            and len(args.delimiter) != 1:
        raise DataError("--delimiter 는 한 글자여야 합니다 (탭은 '\\t').")
    if args.mcid_percent and args.mcid is None:
        raise DataError("--mcid-percent 는 --mcid 와 함께 사용하세요.")
    if args.reliability is not None and not 0.0 < args.reliability < 1.0:
        raise DataError("--reliability 는 0과 1 사이여야 합니다.")
    if args.full and args.brief:
        raise DataError("--full 과 --brief 는 함께 쓸 수 없습니다.")
    if args.time_unit and args.time_values is None:
        raise DataError("--time-unit 은 --time-values 와 함께 쓰세요 "
                        "(단위만 붙이면 간격이 여전히 추정값입니다).")
    if args.no_trend and (args.time_values or args.time_unit):
        raise DataError("--no-trend 와 --time-values/--time-unit 은 함께 쓸 수 "
                        "없습니다 (추세를 끄면 간격이 쓰이지 않습니다).")
    if args.wide:
        if not args.columns:
            raise DataError("--wide 형식에는 --columns 로 시점 열을 지정해야 합니다.")
        for opt, name in ((args.time, "--time"), (args.value, "--value"),
                          (args.time_order, "--time-order")):
            if opt:
                raise DataError(f"{name} 은(는) 긴(long) 형식 전용입니다. "
                                "--wide 에서는 --columns 순서가 시점 순서입니다.")
    else:
        missing = [n for n, v in (("--id", args.id), ("--time", args.time),
                                  ("--value", args.value)) if not v]
        if missing:
            raise DataError(
                f"긴(long) 형식에는 {', '.join(missing)} 이(가) 필요합니다. "
                "한 행이 한 대상인 넓은 형식이라면 --wide --columns 를 쓰세요.")
        if args.columns:
            raise DataError("--columns 는 --wide 형식 전용입니다.")


def _write(text: str, path: Optional[str], overwrite: bool, source: str,
           fmt: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    if not path:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except BrokenPipeError:
            # `longistat ... | head` closes the pipe early; that is not an error.
            try:
                sys.stdout.close()
            except BrokenPipeError:
                pass
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return

    if os.path.isdir(path):
        raise DataError(f"'{path}' 은(는) 폴더입니다. 파일 이름을 지정하세요.")
    if _same_file(path, source):
        raise DataError(
            "출력 파일이 입력 CSV 와 같습니다 — 원자료를 덮어쓸 뻔했습니다. "
            "다른 이름을 쓰세요.")
    # lexists, not exists: a *dangling* symlink is invisible to exists() and
    # slipped past the --overwrite guard while still writing through the link.
    if os.path.lexists(path) and not overwrite:
        raise DataError(f"'{path}' 파일이 이미 있습니다. 덮어쓰려면 --overwrite 를 "
                        "붙이세요.")
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        raise DataError(f"저장할 폴더가 없습니다: {parent}")
    # JSON must not carry a BOM: json.load(open(p, encoding="utf-8")) and R's
    # jsonlite both fail on one.  CSV/text keep it so Excel opens Korean text.
    encoding = "utf-8" if fmt == "json" else "utf-8-sig"
    tmp = path + ".longistat-tmp"
    try:
        with open(tmp, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)          # atomic: never truncate on failure
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise DataError(f"파일을 저장할 수 없습니다: {_os_reason(exc)}") from None
    print(f"저장했습니다: {path}", file=sys.stderr)


def _same_file(a: str, b: str) -> bool:
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.realpath(a) == os.path.realpath(b)


def _os_reason(exc: OSError) -> str:
    if exc.errno == errno.EACCES:
        return f"권한이 없습니다 ({exc.filename})"
    if exc.errno == errno.EROFS:
        return f"읽기 전용 위치입니다 ({exc.filename})"
    if exc.errno == errno.ENOSPC:
        return "디스크 공간이 부족합니다"
    return str(exc)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _validate(args)
        notes: List[str] = []
        if args.wide:
            panel = load_wide(args.csv,
                              [c.strip() for c in args.columns.split(",")],
                              id_col=args.id, group_col=args.group,
                              delimiter=args.delimiter,
                              duplicates=args.duplicates, notes=notes)
        else:
            order = ([t.strip() for t in args.time_order.split(",")]
                     if args.time_order else None)
            panel = load_long(args.csv, args.id, args.time, args.value,
                              group_col=args.group, delimiter=args.delimiter,
                              time_order=order, duplicates=args.duplicates,
                              notes=notes)
        opt = Options(
            alpha=args.alpha, alpha_norm=args.alpha_norm,
            correction=args.correction, sphericity=args.sphericity,
            method=args.method, baseline=args.baseline,
            welch=not args.equal_var, mcid=args.mcid,
            mcid_percent=args.mcid_percent, direction=args.direction,
            responder_test=args.responder_test, reliability=args.reliability,
            rci_sd=args.rci_sd, rci_cutoff=args.rci_cutoff,
            recovery_cutoff=args.recovery_cutoff,
            primary_time=args.primary_time, all_pairs=args.all_pairs,
            responder_denominator=args.responder_denominator,
            labels_en=_parse_labels(args.labels_en),
            time_values=_parse_time_values(args.time_values),
            time_unit=(args.time_unit or "").strip(),
            trend=not args.no_trend, sensitivity=args.sensitivity)
        result = analyze(panel, opt)
        if args.format == "json":
            text = render_json(result)
        elif args.format == "csv":
            text = render_csv(result)
        elif args.format == "md":
            text = render_markdown(result, full=args.full, brief=args.brief)
        else:
            text = render_text(result, full=args.full, brief=args.brief)
        _write(text, args.output, args.overwrite, args.csv, args.format)
    except UnicodeEncodeError:
        print("오류: 현재 터미널 인코딩으로는 한글 리포트를 출력할 수 없습니다. "
              "PYTHONIOENCODING=utf-8 을 설정하거나 -o 로 파일에 저장하세요.",
              file=sys.stderr)
        return 1
    except (DataError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"오류: {_os_reason(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:                                  # pragma: no cover
        print("중단되었습니다.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
