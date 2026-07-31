"""Command-line interface for paperforge."""
from __future__ import annotations

import argparse
import os
import sys

from .engine import evaluate
from .knowledge import IDEA_TEMPLATES
from .manifest import ManifestError, load_manifest
from .report import render_csv, render_json, render_markdown
from .templates import TemplateError, load_template_pack, merge_templates


def _positive_int(value: str) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"정수가 아닙니다: {value!r}")
    if ivalue < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수여야 합니다.")
    return ivalue


def _probability(value: str) -> float:
    """Strictly between 0 and 1, finite (used for --alpha / --power).

    These were bare ``type=float``, so ``--power 7`` reached the report header
    and ``--power inf`` wrote a literal ``Infinity`` into the --json output,
    which is not valid JSON for any consumer.
    """
    try:
        fvalue = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value!r}")
    if fvalue != fvalue or fvalue in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError(f"유한한 숫자여야 합니다: {value!r}")
    if not 0.0 < fvalue < 1.0:
        raise argparse.ArgumentTypeError(
            f"0과 1 사이여야 합니다 (0, 1 제외): {value!r}"
        )
    return fvalue


def _bounded_int(limit: int):
    """A positive-int argparse type with an upper bound."""
    def parse(value: str) -> int:
        ivalue = _positive_int(value)
        if ivalue > limit:
            raise argparse.ArgumentTypeError(f"{limit:,} 이하여야 합니다.")
        return ivalue
    return parse


def _unit_interval(value: str) -> float:
    """0 <= x <= 1 (used for --icc)."""
    try:
        fvalue = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value!r}")
    if not 0.0 <= fvalue <= 1.0 or fvalue != fvalue:
        raise argparse.ArgumentTypeError("--icc 는 0 이상 1 이하여야 합니다.")
    return fvalue


def _dropout(value: str) -> float:
    try:
        fvalue = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value!r}")
    if not 0.0 <= fvalue < 1.0:
        raise argparse.ArgumentTypeError("--dropout 은 0 이상 1 미만이어야 합니다.")
    return fvalue


def _positive_float(value: str) -> float:
    try:
        fvalue = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value!r}")
    if not fvalue > 0.0 or fvalue != fvalue or fvalue == float("inf"):
        raise argparse.ArgumentTypeError("--effect-scale 은 0보다 큰 유한수여야 합니다.")
    return fvalue


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paperforge",
        description=(
            "보유 데이터 매니페스트(JSON)를 입력받아 멀티모달 논문 아이디어 "
            "매트릭스를 생성합니다 (가설·변수·분석법·저널·실현가능성)."
        ),
    )
    p.add_argument("manifest", nargs="?", help="데이터셋 매니페스트 JSON/CSV/TSV 경로")
    p.add_argument("--out", help="Markdown 리포트 저장 경로 (미지정 시 stdout)")
    p.add_argument("--csv", help="CSV 매트릭스 저장 경로 (선택)")
    p.add_argument("--json", dest="json_out", metavar="PATH",
                   help="구조화 JSON 저장 경로 (선택; 프로그램 연동용)")
    p.add_argument(
        "--alpha", type=_probability, default=0.05,
        help="유의수준 (기본 0.05; 0<alpha<1 의 임의 값)",
    )
    p.add_argument(
        "--power", type=_probability, default=0.80,
        help="목표 검정력 (기본 0.80; 0<power<1 의 임의 값)",
    )
    p.add_argument(
        "--one-sided", dest="one_sided", action="store_true",
        help="단측검정 기준으로 표본수 계산 (상관/평균차에만 적용; ΔR²는 무관)",
    )
    p.add_argument(
        "--n-tests", dest="n_tests", type=_bounded_int(1_000_000), default=1,
        metavar="K",
        help="아이디어별 주요 비교 횟수 K → alpha/K 로 Bonferroni 보정 (기본 1)",
    )
    p.add_argument(
        "--repeats", type=_bounded_int(1_000_000), default=1, metavar="M",
        help="피험자당 반복 관측 수 (기본 1). --icc 와 함께 설계효과 보정",
    )
    p.add_argument(
        "--icc", type=_unit_interval, default=0.0, metavar="RHO",
        help="반복 관측의 급내상관 ICC (0~1, 기본 0). --repeats 와 함께 사용",
    )
    p.add_argument(
        "--templates", action="append", default=None, metavar="PATH",
        help="사용자 아이디어 템플릿 팩(JSON) 추가 (여러 번 지정 가능)",
    )
    p.add_argument(
        "--no-builtin", dest="no_builtin", action="store_true",
        help="내장(수면/각성) 템플릿을 제외하고 --templates 팩만 사용",
    )
    p.add_argument(
        "--list-templates", dest="list_templates", action="store_true",
        help="사용 가능한 아이디어 템플릿 목록만 출력하고 종료",
    )
    p.add_argument(
        "--top", type=_positive_int, default=None,
        help="상위 N개만 출력 (1 이상; 기본: 전체)",
    )
    p.add_argument(
        "--dropout", type=_dropout, default=0.0,
        help="예상 중도탈락 비율 (0~1 미만; 권장 모집 N을 상향 보정)",
    )
    p.add_argument(
        "--max-n", dest="max_n", type=_bounded_int(1_000_000), default=None,
        metavar="N",
        help="현실적으로 모집 가능한 최대 표본수 → 초과하는 아이디어에 경고 표시",
    )
    p.add_argument(
        "--feasible-only", dest="feasible_only", action="store_true",
        help="'충분 가능' 판정 아이디어만 출력 (표본 부족/미상 제외)",
    )
    p.add_argument(
        "--effect-scale", dest="effect_scale", type=_positive_float, default=1.0,
        help="가정 효과크기 배율 (>0, 기본 1.0). <1 이면 더 작은 효과 가정 → 권장 N↑",
    )
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Assemble the template set first: --list-templates works without a manifest,
    # and a bad pack should fail before we read any data.
    try:
        packs = [load_template_pack(p) for p in (args.templates or [])]
        templates, template_warnings = merge_templates(
            IDEA_TEMPLATES, packs, include_builtin=not args.no_builtin
        )
    except FileNotFoundError as exc:
        print(f"오류: 템플릿 팩을 찾을 수 없습니다: {exc.filename}", file=sys.stderr)
        return 2
    except (TemplateError, OSError) as exc:
        print(f"템플릿 오류: {exc}", file=sys.stderr)
        return 2
    except RecursionError:
        print("템플릿 오류: JSON 중첩이 너무 깊습니다.", file=sys.stderr)
        return 2

    if args.list_templates:
        print(f"사용 가능한 아이디어 템플릿: {len(templates)}개")
        for t in templates:
            mods = "+".join(t["required"])
            print(f"  - {t['id']:<28} [{mods}] {t['title']}")
        return 0

    if not args.manifest:
        parser.error("매니페스트 경로가 필요합니다 (또는 --list-templates 사용).")

    try:
        manifest = load_manifest(args.manifest)
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.manifest}", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print(f"오류: 디렉터리입니다(파일 아님): {args.manifest}", file=sys.stderr)
        return 2
    except ManifestError as exc:
        print(f"매니페스트 오류: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"오류: 매니페스트를 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 2
    except RecursionError:
        print("매니페스트 오류: JSON 중첩이 너무 깊습니다.", file=sys.stderr)
        return 2

    manifest.warnings.extend(template_warnings)

    # --icc defaults to 0, i.e. "M repeats are M fully independent observations"
    # — the least conservative assumption available, and false for essentially
    # every repeated-measures design. Everything else here errs toward a larger
    # N, so say it out loud rather than quietly cutting the target by M.
    if args.repeats > 1 and args.icc == 0.0 and "--icc" not in (argv or sys.argv[1:]):
        manifest.warnings.append(
            f"--repeats {args.repeats}를 지정했지만 --icc 가 없어 ICC=0"
            "(반복 관측이 완전히 독립)으로 계산했습니다 — 필요 표본이 최대 "
            f"{args.repeats}배까지 과소평가될 수 있습니다. 파일럿 ICC를 넣으세요."
        )

    try:
        results = evaluate(
            manifest, alpha=args.alpha, power=args.power, dropout=args.dropout,
            effect_scale=args.effect_scale, templates=templates,
            sided=1 if args.one_sided else 2, n_tests=args.n_tests,
            repeats=args.repeats, icc=args.icc, max_n=args.max_n,
        )
    except ValueError as exc:
        print(f"분석 오류: {exc}", file=sys.stderr)
        return 2

    # Filter before --top so "top 5 feasible" means five feasible ideas, not
    # "whatever survives the filter out of the first five".
    if args.feasible_only:
        kept = [r for r in results if r.feasible is True]
        if not kept:
            manifest.warnings.append(
                "--feasible-only 로 걸러진 결과가 없습니다 ('충분 가능' 아이디어 "
                "없음). 표본수를 채우거나 --effect-scale·--max-n 가정을 확인하세요."
            )
        results = kept
    if args.top is not None:
        results = results[: args.top]

    settings = {
        "alpha": args.alpha,
        "power": args.power,
        "dropout": args.dropout,
        "effect_scale": args.effect_scale,
        "sided": 1 if args.one_sided else 2,
        "n_tests": args.n_tests,
        "repeats": args.repeats,
        "icc": args.icc,
        "max_n": args.max_n,
        "feasible_only": args.feasible_only,
    }
    report = render_markdown(
        manifest, results, args.alpha, args.power, dropout=args.dropout,
        settings=settings,
    )

    # Two flags pointing at one path would write, then overwrite, and still
    # report success for both — the same half-success the ordering below guards
    # against.
    chosen = [(flag, path) for flag, path in
              (("--out", args.out), ("--csv", args.csv), ("--json", args.json_out))
              if path]
    seen: dict = {}
    for flag, path in chosen:
        key = os.path.abspath(path)
        if key in seen:
            print(
                f"오류: {seen[key]} 와 {flag} 가 같은 경로를 가리킵니다: {path}",
                file=sys.stderr,
            )
            return 2
        seen[key] = flag

    # Write requested files FIRST so a bad path fails cleanly (exit 2) before we
    # print anything to stdout — avoids the confusing "report printed, then
    # crashed on the CSV write" half-success.
    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(report + "\n")
        if args.csv:
            with open(args.csv, "w", encoding="utf-8", newline="") as fh:
                fh.write(render_csv(results))
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(
                    render_json(
                        manifest, results, args.alpha, args.power, args.dropout,
                        settings=settings,
                    )
                    + "\n"
                )
    except OSError as exc:
        print(f"출력 파일 쓰기 오류: {exc}", file=sys.stderr)
        return 2

    if args.out:
        print(f"리포트 저장: {args.out}")
    else:
        print(report)
    if args.csv:
        print(f"CSV 저장: {args.csv}", file=sys.stderr)
    if args.json_out:
        print(f"JSON 저장: {args.json_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
