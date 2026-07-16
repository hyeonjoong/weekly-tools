"""pubgap CLI — PubMed 동향/연구공백 리포트 생성.

두 가지 입력 경로:
  1) 네트워크: 검색어로 PubMed(E-utilities)를 직접 조회.
  2) 오프라인: --from-file 로 미리 받아둔 efetch XML 을 분석(데모/재현/테스트).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .records import (
    Article,
    apply_include_keywords,
    apply_major_only,
    dedup_articles,
    filter_years,
    load_articles,
    parse_efetch_xml,
)
from .report import build_report, json_safe, render_csv, render_markdown


def _load_articles(args: argparse.Namespace) -> List[Article]:
    if args.from_file:
        # gzip/인코딩/XML·NBIB 자동 판별 + PMID 중복 제거까지 한 번에.
        articles = load_articles(args.from_file)
    else:
        # 네트워크 경로 (여기서만 import — 오프라인 테스트가 fetch 를 안 건드리도록)
        from .fetch import fetch_articles_xml

        xml_text = fetch_articles_xml(
            args.query,
            years=args.years,
            retmax=args.max_records,
            email=args.email,
            api_key=args.api_key,
        )
        if args.save_xml:
            Path(args.save_xml).write_text(xml_text, encoding="utf-8")
        articles = dedup_articles(parse_efetch_xml(xml_text))

    # 후처리(순서 있음): 대표주제 한정 → 키워드 보강 → 연도 필터.
    if args.major_topics_only:
        articles = apply_major_only(articles)
    if args.include_keywords:
        articles = apply_include_keywords(articles)
    if args.min_year is not None or args.max_year is not None:
        articles = filter_years(articles, args.min_year, args.max_year)
    return articles


def _nonneg_int(value: str) -> int:
    """0 이상 정수만 허용(top-N/개수 옵션용). 음수는 슬라이스를 잘못 잘라 조용히
    틀린 결과를 내므로 명시적으로 거부한다."""
    try:
        iv = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"정수가 아닙니다: {value!r}")
    if iv < 0:
        raise argparse.ArgumentTypeError(f"0 이상이어야 합니다: {iv}")
    return iv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pubgap",
        description="PubMed 최근 동향 요약 + 덜 연구된 각도(연구공백) 제안",
    )
    p.add_argument("query", nargs="?", default=None, help="PubMed 검색어 (예: 'slow breathing sleep')")
    p.add_argument("--years", type=int, default=10, help="최근 몇 년 (기본 10)")
    p.add_argument("--max-records", type=int, default=300, help="가져올 최대 논문 수 (기본 300)")
    p.add_argument("--from-file", help="네트워크 대신 파일 분석(efetch XML 또는 MEDLINE/NBIB, .gz 가능)")
    p.add_argument("--save-xml", help="네트워크 조회 결과 XML 을 이 경로에 저장")
    p.add_argument("--email", help="NCBI 예절용 이메일(권장)")
    p.add_argument("--api-key", help="NCBI API key(있으면 rate limit 완화)")
    p.add_argument("--top-mesh", type=_nonneg_int, default=15, help="주요 주제 표시 개수")
    p.add_argument("--top-journals", type=_nonneg_int, default=8, help="주요 저널 표시 개수")
    p.add_argument("--gap-top-k", type=_nonneg_int, default=12, help="공백 탐색에 쓸 빈출 주제 상위 K")
    p.add_argument("--gap-min-expected", type=float, default=2.0, help="공백 기준: 최소 기대 동시등장 수")
    p.add_argument("--gap-max-lift", type=float, default=0.5, help="공백 기준: 최대 lift(관측/기대)")
    p.add_argument(
        "--gap-max-q", type=float, default=None,
        help="공백 기준: 최대 q-value(BH-FDR). 지정 시 이 값 이하만 남김(통계적으로 유의한 공백만)",
    )
    p.add_argument(
        "--major-topics-only", action="store_true",
        help="MeSH 대표주제(별표 major)만으로 분석 — 더 정밀하지만 표본이 줄 수 있음",
    )
    p.add_argument(
        "--include-keywords", action="store_true",
        help="저자 키워드(OT/Keyword)도 주제로 포함 — MeSH 미부여 최신 논문 보완",
    )
    p.add_argument(
        "--include-check-tags", action="store_true",
        help="PubMed 체크 태그(Humans/Male/Female/Adult…)도 주제로 포함(기본은 제외)",
    )
    p.add_argument(
        "--no-bridges", action="store_true",
        help="공백쌍의 Swanson ABC 가교 주제 계산을 끔",
    )
    p.add_argument("--min-year", type=int, default=None, help="이 연도 이상 논문만(연도 미상 제외)")
    p.add_argument("--max-year", type=int, default=None, help="이 연도 이하 논문만(연도 미상 제외)")
    p.add_argument(
        "--format", choices=("md", "json", "csv"), default=None,
        help="출력 형식: md(기본)/json/csv(공백 후보 표)",
    )
    p.add_argument("--json", action="store_true", help="(구버전 호환) --format json 과 동일")
    p.add_argument("--out", help="리포트를 이 파일에 저장(미지정 시 표준출력)")
    p.add_argument("--version", action="version", version=f"pubgap {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.from_file and not args.query:
        print("오류: 검색어(query)를 주거나 --from-file 을 지정하세요.", file=sys.stderr)
        return 2

    try:
        articles = _load_articles(args)
    except FileNotFoundError as exc:
        print(f"오류: 파일을 찾을 수 없습니다 — {exc.filename}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # 네트워크/PubMed 오류 등 — 빈 결과(rc 1)와 구분해 rc 3
        print(f"오류: 데이터를 가져오지 못했습니다 — {exc}", file=sys.stderr)
        return 3

    if not articles:
        print("검색 결과가 없습니다. 검색어/기간을 바꿔 보세요.", file=sys.stderr)
        return 1

    query_label = args.query or (args.from_file or "(file)")
    try:
        rep = build_report(
            articles,
            query_label,
            top_mesh_n=args.top_mesh,
            top_journals_n=args.top_journals,
            gap_top_k=args.gap_top_k,
            gap_min_expected=args.gap_min_expected,
            gap_max_lift=args.gap_max_lift,
            gap_max_q=args.gap_max_q,
            drop_check_tags=not args.include_check_tags,
            bridge_top_n=0 if args.no_bridges else 3,
        )

        fmt = args.format or ("json" if args.json else "md")
        if fmt == "json":
            # allow_nan=False + 사전 정화로 항상 표준 JSON 을 보장.
            output = json.dumps(json_safe(rep), ensure_ascii=False, indent=2, allow_nan=False)
        elif fmt == "csv":
            output = render_csv(rep)
        else:
            output = render_markdown(rep)
    except Exception as exc:  # 분석/렌더 중 예기치 못한 오류 — 원시 트레이스백 대신 rc 3
        print(f"오류: 리포트 생성 중 문제가 발생했습니다 — {exc}", file=sys.stderr)
        return 3

    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
        print(f"저장 완료: {args.out}  (논문 {rep['n_articles']}편 분석)")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
