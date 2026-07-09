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
from .records import Article, parse_efetch_xml
from .report import build_report, render_markdown


def _load_articles(args: argparse.Namespace) -> List[Article]:
    if args.from_file:
        xml_text = Path(args.from_file).read_text(encoding="utf-8")
        return parse_efetch_xml(xml_text)
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
    return parse_efetch_xml(xml_text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pubgap",
        description="PubMed 최근 동향 요약 + 덜 연구된 각도(연구공백) 제안",
    )
    p.add_argument("query", nargs="?", default=None, help="PubMed 검색어 (예: 'slow breathing sleep')")
    p.add_argument("--years", type=int, default=10, help="최근 몇 년 (기본 10)")
    p.add_argument("--max-records", type=int, default=300, help="가져올 최대 논문 수 (기본 300)")
    p.add_argument("--from-file", help="네트워크 대신 미리 받아둔 efetch XML 파일을 분석")
    p.add_argument("--save-xml", help="네트워크 조회 결과 XML 을 이 경로에 저장")
    p.add_argument("--email", help="NCBI 예절용 이메일(권장)")
    p.add_argument("--api-key", help="NCBI API key(있으면 rate limit 완화)")
    p.add_argument("--top-mesh", type=int, default=15, help="주요 주제 표시 개수")
    p.add_argument("--top-journals", type=int, default=8, help="주요 저널 표시 개수")
    p.add_argument("--gap-top-k", type=int, default=12, help="공백 탐색에 쓸 빈출 주제 상위 K")
    p.add_argument("--gap-min-expected", type=float, default=2.0, help="공백 기준: 최소 기대 동시등장 수")
    p.add_argument("--gap-max-lift", type=float, default=0.5, help="공백 기준: 최대 lift(관측/기대)")
    p.add_argument("--json", action="store_true", help="Markdown 대신 JSON 출력")
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
    rep = build_report(
        articles,
        query_label,
        top_mesh_n=args.top_mesh,
        top_journals_n=args.top_journals,
        gap_top_k=args.gap_top_k,
        gap_min_expected=args.gap_min_expected,
        gap_max_lift=args.gap_max_lift,
    )

    output = json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render_markdown(rep)

    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
        print(f"저장 완료: {args.out}  (논문 {rep['n_articles']}편 분석)")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
