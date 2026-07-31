"""pubgap CLI — PubMed 동향/연구공백 리포트 생성.

두 가지 입력 경로:
  1) 네트워크: 검색어로 PubMed(E-utilities)를 직접 조회.
  2) 오프라인: --from-file 로 미리 받아둔 efetch XML 을 분석(데모/재현/테스트).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .analyze import GAP_SORTS, POPULATION_SORTS, is_non_topical
from .records import (
    Article,
    apply_include_keywords,
    apply_major_only,
    dedup_articles,
    dedup_articles_detailed,
    clean_value,
    detect_format,
    read_source,
    filter_years,
    parse_efetch_xml,
    parse_records,
    topics_from_keywords,
)
from .report import CSV_SECTIONS, build_report, json_safe, render_csv, render_markdown


def _load_articles(
    args: argparse.Namespace, state: dict, exclude_terms: Sequence[str] = ()
) -> List[Article]:
    if args.from_file:
        # 파일은 **한 번만** 읽는다. 예전엔 여기서 한 번, 실행정보(sha256)에서 또 한 번
        # 읽어 큰 파일의 I/O 가 두 배였고, FIFO·프로세스치환(`--from-file <(...)`)은
        # 두 번째 읽기에서 영원히 멈췄다.
        #
        # --from-file 은 여러 번 줄 수 있다: PubMed XML + Scopus CSV + WoS 내보내기를
        # 한 코퍼스로 합쳐 분석하는 것이 실제 문헌고찰의 표준 절차이기 때문이다.
        # 합칠 때 중복 제거(PMID→DOI→제목+연도)를 하지 않으면 모든 통계가 부풀려진다.
        merged: List[Article] = []
        sources: List[dict] = []
        for path in args.from_file:
            state["current_file"] = path
            raw, text, hint = read_source(path)
            arts = parse_records(text, hint=hint)
            merged.extend(arts)
            sources.append({
                "path": clean_value(str(Path(path))),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "format": detect_format(text, hint=hint),
                "records": len(arts),
            })
        state["sources"] = sources
        articles, stats = dedup_articles_detailed(
            merged, by_title=not args.no_fuzzy_dedup
        )
        state["dedup"] = {
            "n_input": stats.n_input, "n_unique": stats.n_unique,
            "n_removed": stats.n_removed, "by_pmid": stats.by_pmid,
            "by_doi": stats.by_doi, "by_title": stats.by_title,
            "n_enriched": stats.n_enriched, "n_files": len(sources),
        }
        if args.save_xml:
            print(
                "안내: --save-xml 은 네트워크 조회에만 적용됩니다(--from-file 에서는 무시).",
                file=sys.stderr,
            )
        if args.years != _DEFAULT_YEARS:
            print(
                "안내: --years 는 네트워크 조회에만 적용됩니다 — 파일 분석에서 기간을 "
                "좁히려면 --min-year/--max-year 를 쓰세요.",
                file=sys.stderr,
            )
    else:
        # 네트워크 경로 (여기서만 import — 오프라인 테스트가 fetch 를 안 건드리도록)
        from .fetch import fetch_articles

        result = fetch_articles(
            args.query,
            years=args.years,
            retmax=args.max_records,
            email=args.email,
            api_key=args.api_key,
            sample=args.sample,
            min_year=args.min_year,
            max_year=args.max_year,
        )
        state["total_available"] = result.total_available
        state["n_fetched"] = result.n_fetched
        if args.save_xml:
            _write_text(args.save_xml, result.xml_text, what="--save-xml")
        articles = dedup_articles(parse_efetch_xml(result.xml_text))

    # 후처리(순서 있음): 대표주제 한정 → 키워드 보강/폴백 → 연도 필터.
    if args.major_topics_only:
        # '주제가 비었다'는 판정은 **주제어**로만 한다 — apply_major_only 는 체크 태그
        # (대상집단·근거 축의 신호)를 일부러 남기므로 mesh 가 비지 않는다.
        before = sum(1 for a in articles if any(not is_non_topical(m) for m in a.mesh))
        articles = apply_major_only(articles)
        after = sum(1 for a in articles if any(not is_non_topical(m) for m in a.mesh))
        if not after:
            print(
                "경고: --major-topics-only 를 켰지만 대표(별표) MeSH 주제가 하나도 "
                "없어 주제 분석이 비었습니다. PubMed 색인에 별표가 없거나(합성·구형 "
                "레코드), RIS/CSV 처럼 대표주제 표기를 담지 않는 형식입니다 — "
                "옵션을 빼고 다시 실행해 보세요.",
                file=sys.stderr,
            )
    if args.include_keywords:
        articles = apply_include_keywords(articles)
        state["topic_source"] = "mesh+keywords"
    elif not args.major_topics_only:
        # MeSH 가 한 편도 없는 코퍼스(RIS/CSV 내보내기, 색인 전 최신 논문)라면
        # 조용히 빈 리포트를 내지 말고 저자 키워드를 주제로 승격한다.
        # --major-topics-only 일 때는 하지 않는다: 사용자가 '대표주제만' 이라고
        # 명시했는데 키워드로 슬그머니 채우면 요청과 다른 분석이 된다.
        articles, used = topics_from_keywords(articles)
        if used:
            state["topic_source"] = "keywords"
            print(
                "안내: MeSH 주제어가 없는 입력이라 저자 키워드를 주제로 사용합니다"
                " (MeSH 색인만큼 표준화돼 있지 않으니 동의어 분산에 주의하세요).",
                file=sys.stderr,
            )
    if args.min_year is not None or args.max_year is not None:
        articles = filter_years(articles, args.min_year, args.max_year)
    # 제외 목록을 적용하고도 분석할 주제가 남는가. **분석에 실제로 쓰이는 주제**로
    # 판정해야 한다 — 체크 태그(Humans/Aged/Female…)는 기본적으로 주제 분석에서
    # 빠지므로, 이것만 남았는데도 "주제가 남았다"고 보면 경고가 조용히 사라진다.
    _dropped = {e.strip().lower() for e in exclude_terms}
    _topical = (
        (lambda t: True) if args.include_check_tags
        else (lambda t: not is_non_topical(t))
    )
    if exclude_terms and articles and not any(
        any(t.strip().lower() not in _dropped and _topical(t) for t in a.mesh)
        for a in articles
    ):
        print(
            "경고: --exclude-term/--exclude-terms-file 로 지정한 주제어를 빼고 나니 "
            "분석할 주제가 하나도 남지 않았습니다 — 제외 목록을 줄여 보세요.",
            file=sys.stderr,
        )
    return articles


def _current_input(args: argparse.Namespace, state: dict) -> str:
    """오류 메시지에 쓸 '지금 읽던 파일' — --from-file 이 여러 개일 수 있다."""
    cur = state.get("current_file")
    if cur:
        return str(cur)
    files = args.from_file or []
    return " + ".join(str(f) for f in files) if files else "(입력 없음)"


def _write_text(path: str, text: str, what: str = "--out") -> None:
    """파일 쓰기 — 실패를 원시 트레이스백 대신 한국어 오류로 바꾼다.

    `--out 디렉터리` 는 오타 하나면 나는 실수인데, 예전에는 여기서 분석을 다 끝낸 뒤
    IsADirectoryError 트레이스백을 뱉고 rc 1('결과 없음')로 끝나 원인을 알 수 없었다.
    """
    try:
        Path(path).write_text(text, encoding="utf-8")
    except IsADirectoryError:
        raise OutputError(f"{what}: 파일이 아니라 디렉터리입니다 — {path}")
    except FileExistsError:
        # macOS 에서 `--out /` 는 IsADirectoryError 가 아니라 FileExistsError 를 낸다.
        raise OutputError(f"{what}: 파일이 아니라 디렉터리입니다 — {path}")
    except FileNotFoundError:
        raise OutputError(f"{what}: 상위 폴더가 없습니다 — {path}")
    except PermissionError:
        raise OutputError(f"{what}: 쓸 권한이 없습니다 — {path}")
    except OSError as exc:
        raise OutputError(f"{what}: 파일을 쓰지 못했습니다 — {exc}")


class OutputError(Exception):
    """출력 파일 쓰기 실패(사용자 입력 문제) — rc 2 로 매핑된다."""


def _build_meta(args: argparse.Namespace, state: dict, exclude_terms: List[str]) -> dict:
    """재현용 실행 정보(도구 버전·시각·입력 지문·**모든** 분석 옵션)를 모은다.

    옵션을 일부만 남기면 "Methods 에 그대로 옮겨 적으면 재현된다"는 약속이 거짓이
    된다. 결과에 영향을 주는 옵션은 전부 적는다(출력 형식 등 표시 전용 옵션 포함).
    api_key·email 은 **절대 넣지 않는다** — 리포트는 공유되는 산출물이다.
    """
    src: dict = {}
    if args.from_file:
        sources = state.get("sources") or []
        if sources:
            # 파일이 하나면 예전과 같은 평평한 모양(path/bytes/sha256/format)을 유지해
            # 기존 소비자를 깨지 않고, 여러 개면 `sources` 목록을 함께 싣는다.
            first = sources[0]
            src["path"] = first["path"]
            src["bytes"] = first["bytes"]
            src["sha256"] = first["sha256"]
            src["format"] = first["format"]
            if len(sources) > 1:
                src["sources"] = sources
                src["path"] = " + ".join(s["path"] for s in sources)
        if state.get("dedup"):
            src["dedup"] = state["dedup"]
    else:
        src["path"] = clean_value(f"PubMed:{args.query}")
        src["format"] = "efetch-xml"
        src["years"] = args.years
        src["max_records"] = args.max_records
        if state.get("total_available") is not None:
            src["total_available"] = state["total_available"]
    return {
        "tool": "pubgap",
        "version": __version__,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "input": src,
        "topic_source": state.get("topic_source", "mesh"),
        # 이 실행 경로에서의 기본값 — 렌더러가 '기본과 다른 옵션'만 강조할 때 쓴다.
        "defaults": {"sample": None if args.from_file else "stratified",
                     "verify_gaps": not bool(args.from_file)},
        "params": {
            "gap_top_k": args.gap_top_k,
            "gap_min_expected": args.gap_min_expected,
            "gap_max_lift": args.gap_max_lift,
            "gap_max_q": args.gap_max_q,
            "gap_sort": args.gap_sort,
            "bridges": not args.no_bridges,
            "evidence": not args.no_evidence,
            "top_evidence": args.top_evidence,
            "angles": not args.no_angles,
            "angle_top_k": args.angle_top_k,
            "angle_top_qualifiers": args.angle_top_qualifiers,
            "angle_min_expected": args.angle_min_expected,
            "angle_max_lift": args.angle_max_lift,
            "angle_hide_implausible": args.angle_hide_implausible,
            "population": not args.no_population,
            "population_top_k": args.population_top_k,
            "population_min_articles": args.population_min_articles,
            "population_sort": args.population_sort,
            "fuzzy_dedup": not args.no_fuzzy_dedup,
            "top_mesh": args.top_mesh,
            "top_journals": args.top_journals,
            "major_topics_only": args.major_topics_only,
            "include_keywords": args.include_keywords,
            "include_check_tags": args.include_check_tags,
            "min_year": args.min_year,
            "max_year": args.max_year,
            "sample": None if args.from_file else args.sample,
            "verify_gaps": _should_verify(args),
            "exclude_terms": clean_value(";".join(exclude_terms)) or None,
        },
    }


def _should_verify(args: argparse.Namespace) -> bool:
    """공백쌍을 PubMed 전수로 검증할지.

    네트워크를 이미 쓰는 조회 경로에서는 기본으로 켠다(추가 비용은 esearch 수십 회).
    `--from-file` 은 '오프라인' 이 계약이므로 사용자가 명시할 때만 켠다.
    """
    if args.no_verify_gaps:
        return False
    if args.verify_gaps:
        return True
    return not args.from_file


def _verify_gaps(rep: dict, args: argparse.Namespace) -> None:
    """표시 중인 공백쌍을 PubMed 전수 편수로 재계산해 리포트에 채운다(실패해도 진행)."""
    gaps = rep.get("gaps") or []
    if not gaps:
        return
    from .fetch import verify_pairs_online

    if len(gaps) > MAX_VERIFY_PAIRS:
        print(
            f"안내: 공백 후보가 {len(gaps)}개라 상위 {MAX_VERIFY_PAIRS}개만 전수 검증합니다"
            " (쌍마다 PubMed 조회가 한 번씩 필요합니다).",
            file=sys.stderr,
        )
        gaps = gaps[:MAX_VERIFY_PAIRS]
    pairs = [(g["term_a"], g["term_b"]) for g in gaps]
    try:
        counts = verify_pairs_online(
            pairs, query=args.query, email=args.email, api_key=args.api_key,
            years=args.years, min_year=args.min_year, max_year=args.max_year,
        )
    except Exception as exc:  # 검증은 보너스다 — 실패해도 리포트는 나와야 한다
        print(f"안내: 공백 검증(PubMed 전수 조회)에 실패해 건너뜁니다 — {_scrub(exc, args)}",
              file=sys.stderr)
        return

    total = counts.get("__total__", 0)
    rep["verify_total"] = total
    for g in gaps:
        ca = counts.get(g["term_a"])
        cb = counts.get(g["term_b"])
        cab = counts.get(f"{g['term_a']}||{g['term_b']}")
        if ca is None or cb is None or cab is None:
            continue
        # 일관성 검사: 동시등장은 각 주제 편수를, 각 주제 편수는 전체를 넘을 수 없다.
        # 넘었다면 검색식이 의도대로 해석되지 않은 것(예: 주제어에 남은 따옴표가
        # 최상위 OR 를 만든 경우)이므로, 그 수치로 판정하면 정반대 결론이 나온다.
        if not (0 <= cab <= min(ca, cb) <= total):
            g["verdict"] = "unknown"
            continue
        g["pubmed_count_a"] = ca
        g["pubmed_count_b"] = cb
        g["pubmed_observed"] = cab
        expected = (ca * cb / total) if total else 0.0
        g["pubmed_expected"] = expected
        g["pubmed_lift"] = (cab / expected) if expected > 0 else None
        g["verdict"] = _verdict(g["pubmed_lift"], cab)


# 전수 lift 가 이 값을 넘으면 '표본에서만 공백' — 실제 문헌에는 충분히 엮여 있다.
VERIFY_ARTIFACT_LIFT = 0.5


def _verdict(pubmed_lift, pubmed_observed) -> str:
    """전수 재계산 결과로 이 후보가 진짜 공백인지 판정."""
    if pubmed_lift is None:
        return "unknown"
    if pubmed_lift > VERIFY_ARTIFACT_LIFT:
        # 상하위어(부모×자식)는 전수에서 lift 가 1 이상으로 나와 여기서 걸러진다.
        return "artifact"
    if pubmed_observed == 0:
        return "confirmed_empty"
    return "confirmed"


def _collect_exclude_terms(args: argparse.Namespace) -> List[str]:
    """--exclude-term(반복) + --exclude-terms-file 을 합친다(중복·공백 제거)."""
    terms: List[str] = list(args.exclude_term or [])
    if args.exclude_terms_file:
        text = Path(args.exclude_terms_file).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    out: List[str] = []
    seen = set()
    for t in terms:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t.strip())
    return out


_DEFAULT_YEARS = 10
# 공백 탐색은 상위 K 주제의 모든 쌍(K²/2)을 검정하고, 살아남은 쌍마다 코퍼스를 한 번씩
# 훑는다. K 가 커지면 O(K²·N) 으로 폭발한다(실측: K=200·2만편 = 229초). 실용적 상한을
# 두어, 오타 하나로 몇 시간짜리 실행이 시작되는 일을 막는다.
MAX_GAP_TOP_K = 200
# 전수 검증은 쌍마다 HTTP 요청이 하나씩 필요하다. 임계를 느슨하게 주면 공백이
# 수만 개가 될 수 있어(실측: 2만 쌍 → 약 2시간) 상한을 둔다.
MAX_VERIFY_PAIRS = 30
_SECRET_RE = re.compile(r"((?:api_key|email)=)[^&\s]*", re.IGNORECASE)


def _scrub(exc: BaseException, args: Optional[argparse.Namespace] = None) -> str:
    """예외 메시지에서 자격증명을 지운다.

    E-utilities 는 api_key 를 URL 질의문자열로 받는다. urllib 계열 예외 중에는
    실패한 URL 을 통째로 메시지에 담는 것이 있어, 그대로 출력하면 사용자의 NCBI
    API 키가 터미널·로그·붙여넣은 이슈에 그대로 남는다.

    두 겹으로 막는다:
      1) **값 기반** — 사용자가 준 실제 키/이메일 문자열을 통째로 치환한다. 이게
         가장 확실하다. `api_key: X`(콜론), JSON 본문, 경로 세그먼트, URL 인코딩된
         변형 등 패턴 기반으로는 놓치는 모든 모양을 한 번에 잡는다.
      2) **패턴 기반** — 값을 모르는 경우(라이브러리 코드가 만든 URL 등) 대비.
    """
    text = str(exc)
    if args is not None:
        for secret in (getattr(args, "api_key", None), getattr(args, "email", None)):
            if secret and len(str(secret)) >= 4:
                text = text.replace(str(secret), "<redacted>")
                text = text.replace(urllib.parse.quote(str(secret)), "<redacted>")
    return _SECRET_RE.sub(r"\1<redacted>", text)


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


MAX_YEARS = 100


def _years_opt(value: str) -> int:
    """--years — 층화 표집이 연도마다 요청을 보내므로 상한을 둔다.

    `--years 2020`(‘2020년 이후’ 라는 뜻으로 쓴 오타) 하나로 2,020회 요청이 나가고,
    NCBI 예절 대기까지 더하면 11분 넘게 PubMed 를 두드린다. `--gap-top-k` 와 같은 이유.
    """
    iv = _nonneg_int(value)
    if iv > MAX_YEARS:
        raise argparse.ArgumentTypeError(
            f"{MAX_YEARS} 이하여야 합니다(연도마다 조회하므로 요청이 그만큼 늘어납니다): {iv}. "
            "특정 기간을 보려면 --min-year/--max-year 를 쓰세요."
        )
    return iv


def _year_opt(value: str) -> int:
    """연도 옵션 — 서지 데이터에 있을 수 있는 범위만 받는다."""
    try:
        iv = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"정수 연도가 아닙니다: {value!r}")
    if not 1500 <= iv <= 2200:
        raise argparse.ArgumentTypeError(f"1500~2200 사이 연도여야 합니다: {iv}")
    return iv


def _positive_int(value: str) -> int:
    iv = _nonneg_int(value)
    if iv == 0:
        raise argparse.ArgumentTypeError("1 이상이어야 합니다: 0")
    return iv


def _gap_top_k(value: str) -> int:
    """공백 탐색 상위 K — 0 이상, 그리고 실행시간이 폭발하지 않도록 상한을 둔다."""
    iv = _nonneg_int(value)
    if iv > MAX_GAP_TOP_K:
        raise argparse.ArgumentTypeError(
            f"{MAX_GAP_TOP_K} 이하여야 합니다(검정 수가 K 에 따라 빠르게 늘어 "
            f"실행이 느려지고 q(FDR)도 나빠집니다): {iv}"
        )
    return iv


MAX_ANGLE_QUALIFIERS = 100


def _angle_top_qualifiers(value: str) -> int:
    """--angle-top-qualifiers — 검정 칸이 K×M 로 늘어 실행시간이 폭발하지 않도록 상한."""
    iv = _nonneg_int(value)
    if iv > MAX_ANGLE_QUALIFIERS:
        raise argparse.ArgumentTypeError(
            f"{MAX_ANGLE_QUALIFIERS} 이하여야 합니다(검정 칸이 K×M 로 늘어납니다): {iv}"
        )
    return iv


def _unit_float(value: str) -> float:
    """0 이상 1 이하의 확률값(q 임계 등). NaN/무한대는 비교가 조용히 어긋나 거부한다."""
    try:
        fv = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value!r}")
    if fv != fv or fv in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError(f"유한한 값이어야 합니다: {value!r}")
    if not 0.0 <= fv <= 1.0:
        raise argparse.ArgumentTypeError(f"0 과 1 사이여야 합니다: {fv}")
    return fv


def _nonneg_float(value: str) -> float:
    try:
        fv = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"숫자가 아닙니다: {value!r}")
    if fv != fv or fv in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError(f"유한한 값이어야 합니다: {value!r}")
    if fv < 0:
        raise argparse.ArgumentTypeError(f"0 이상이어야 합니다: {fv}")
    return fv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pubgap",
        description="PubMed 최근 동향 요약 + 덜 연구된 각도(연구공백) 제안",
    )
    p.add_argument("query", nargs="?", default=None, help="PubMed 검색어 (예: 'slow breathing sleep')")
    p.add_argument(
        "--years", type=_years_opt, default=_DEFAULT_YEARS,
        help=f"최근 몇 년 (기본 {_DEFAULT_YEARS}; 0 이면 기간 제한 없음). 네트워크 조회 전용",
    )
    p.add_argument(
        "--max-records", type=_positive_int, default=300,
        help="가져올 최대 논문 수 (기본 300). 검색 결과가 이보다 많으면 표본이 잘리며"
             "(기본은 연도 층화 표집), 그때는 추세 관련 출력이 생략됩니다",
    )
    p.add_argument(
        "--from-file", action="append", metavar="PATH",
        help="네트워크 대신 파일 분석 — efetch XML / MEDLINE·NBIB / RIS / CSV·TSV "
             "(.gz 가능, 자동 판별). **여러 번 지정하면 합쳐서** 분석하고 중복(PMID→DOI→"
             "제목+연도)을 제거합니다",
    )
    p.add_argument(
        "--no-fuzzy-dedup", action="store_true",
        help="중복 제거에서 '제목+연도' 대조를 끔(PMID·DOI 가 같을 때만 중복으로 봄)",
    )
    p.add_argument("--save-xml", help="네트워크 조회 결과 XML 을 이 경로에 저장")
    p.add_argument(
        "--sample", choices=("stratified", "recent"), default="stratified",
        help="네트워크 표집 방식: stratified(기본·연도별 균등 — 표본이 한 해로 붕괴하는 "
             "것을 막음) / recent(최신순 상위 N편)",
    )
    p.add_argument(
        "--count-only", action="store_true",
        help="검색 결과 편수만 조회하고 끝냄(1초) — --max-records 를 얼마로 둘지 결정할 때",
    )
    p.add_argument(
        "--verify-gaps", action="store_true",
        help="표시할 공백쌍을 PubMed 전수 편수로 재계산해 색인 artifact 를 걸러냄 "
             "(조회 경로에서는 기본 켜짐; --from-file 에서 쓰려면 이 옵션 필요)",
    )
    p.add_argument(
        "--no-verify-gaps", action="store_true",
        help="공백쌍 전수 검증을 끔(네트워크 호출 절약)",
    )
    p.add_argument("--email", help="NCBI 예절용 이메일(권장)")
    p.add_argument("--api-key", help="NCBI API key(있으면 rate limit 완화)")
    p.add_argument("--top-mesh", type=_nonneg_int, default=15, help="주요 주제 표시 개수")
    p.add_argument("--top-journals", type=_nonneg_int, default=8, help="주요 저널 표시 개수")
    p.add_argument(
        "--gap-top-k", type=_gap_top_k, default=12,
        help=f"공백 탐색에 쓸 빈출 주제 상위 K (기본 12, 최대 {MAX_GAP_TOP_K}). "
             "K 를 올리면 검정 수가 K² 로 늘어 q(FDR)가 나빠집니다",
    )
    p.add_argument(
        "--gap-min-expected", type=_nonneg_float, default=2.0,
        help="공백 기준: 최소 기대 동시등장 수 (기본 2.0)",
    )
    p.add_argument(
        "--gap-max-lift", type=_nonneg_float, default=0.5,
        help="공백 기준: 최대 lift(관측/기대) (기본 0.5)",
    )
    p.add_argument(
        "--gap-max-q", type=_unit_float, default=None,
        help="공백 기준: 최대 q-value(BH-FDR). 지정 시 이 값 이하만 남김(통계적으로 유의한 공백만)",
    )
    p.add_argument(
        "--gap-sort", choices=GAP_SORTS, default="deficit",
        help=(
            "공백 정렬 기준: deficit(기본·기대−관측 편수) / lift(미개척 정도) "
            "/ q(FDR 견고성) / expected(분야 규모) / npmi(배타성)"
        ),
    )
    p.add_argument(
        "--exclude-term", action="append", metavar="TERM",
        help="이 주제어를 분석에서 제외(대소문자 무시). 여러 번 지정 가능 — 보통 검색어 자체",
    )
    p.add_argument(
        "--exclude-terms-file", metavar="PATH",
        help="제외할 주제어 목록 파일(한 줄에 하나, '#' 로 시작하면 주석)",
    )
    p.add_argument(
        "--top-evidence", type=_nonneg_int, default=12,
        help="근거 공백(개입연구 밀도)을 볼 빈출 주제 상위 K (기본 12)",
    )
    p.add_argument(
        "--no-evidence", action="store_true",
        help="연구 설계(PublicationType) 기반 근거 지형·근거 공백 분석을 끔",
    )
    p.add_argument(
        "--no-angles", action="store_true",
        help="MeSH 부주제어(qualifier) 기반 '연구 각도 공백' 분석을 끔",
    )
    p.add_argument(
        "--no-population", action="store_true",
        help="MeSH 연령·성별 체크 태그 기반 '대상집단 공백' 분석을 끔",
    )
    p.add_argument(
        "--population-top-k", type=_gap_top_k, default=12,
        help="대상집단 공백을 볼 빈출 주제 상위 K (기본 12)",
    )
    p.add_argument(
        "--population-min-articles", type=_positive_int, default=5,
        help="대상집단 검정에 넣을 주제의 최소 논문 수 (기본 5)",
    )
    p.add_argument(
        "--population-sort", choices=POPULATION_SORTS, default="deficit",
        help="대상집단 공백 정렬: deficit(부족 편수) | share(비중) | q | lift (기본 deficit)",
    )
    p.add_argument(
        "--angle-top-k", type=_gap_top_k, default=12,
        help="각도 공백을 볼 빈출 주제 상위 K (기본 12)",
    )
    p.add_argument(
        "--angle-top-qualifiers", type=_angle_top_qualifiers, default=10,
        help=f"각도 공백에 쓸 빈출 부주제어 상위 M (기본 10, 최대 {MAX_ANGLE_QUALIFIERS})",
    )
    p.add_argument(
        "--angle-min-expected", type=_nonneg_float, default=1.0,
        help="각도 공백 기준: 최소 기대 동시색인 수 (기본 1.0)",
    )
    p.add_argument(
        "--angle-max-lift", type=_nonneg_float, default=0.5,
        help="각도 공백 기준: 최대 lift(관측/기대) (기본 0.5)",
    )
    p.add_argument(
        "--angle-hide-implausible", action="store_true",
        help="MeSH 색인 규칙상 불가능해 보이는 주제×각도 칸을 표에서 감춤"
             "(기본은 `⚠ 규칙상 불가?` 로 표시하고 순위만 뒤로 미룸)",
    )
    p.add_argument(
        "--csv-section", choices=CSV_SECTIONS, default="gaps",
        help="--format csv 로 내보낼 표 (기본 gaps)",
    )
    p.add_argument(
        "--no-meta", action="store_true",
        help="리포트 하단의 실행 정보(버전·시각·입력 해시·옵션) 블록을 넣지 않음",
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
    p.add_argument("--min-year", type=_year_opt, default=None, help="이 연도 이상 논문만(연도 미상 제외)")
    p.add_argument("--max-year", type=_year_opt, default=None, help="이 연도 이하 논문만(연도 미상 제외)")
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

    if (
        args.min_year is not None
        and args.max_year is not None
        and args.min_year > args.max_year
    ):
        print(
            f"오류: --min-year({args.min_year}) 가 --max-year({args.max_year}) 보다 "
            "큽니다 — 어떤 논문도 이 범위에 들 수 없습니다.",
            file=sys.stderr,
        )
        return 2
    if args.major_topics_only and args.include_keywords:
        print(
            "오류: --major-topics-only 와 --include-keywords 는 함께 쓸 수 없습니다 "
            "(대표주제만 보겠다는 요청과 키워드로 주제를 넓히겠다는 요청이 반대입니다).",
            file=sys.stderr,
        )
        return 2

    try:
        exclude_terms = _collect_exclude_terms(args)
    except OSError as exc:
        print(f"오류: 제외어 파일을 읽지 못했습니다 — {exc}", file=sys.stderr)
        return 2

    if args.count_only:
        if not args.query:
            print("오류: --count-only 는 검색어가 필요합니다.", file=sys.stderr)
            return 2
        try:
            from .fetch import esearch

            total, _ = esearch(
                args.query, years=args.years, retmax=0,
                email=args.email, api_key=args.api_key,
                min_year=args.min_year, max_year=args.max_year,
            )
        except Exception as exc:
            print(f"오류: 편수를 조회하지 못했습니다 — {_scrub(exc, args)}", file=sys.stderr)
            return 3
        window = f"최근 {args.years}년" if args.years else "전체 기간"
        msg = f"검색어: {args.query}\n기간: {window}\n검색 결과: {total:,}편"
        if total > args.max_records:
            msg += (
                f"\n\n현재 --max-records {args.max_records} 로는 표본이 잘립니다.\n"
                f"전수를 분석하려면: --max-records {total}"
            )
        _print_safely(msg)
        return 0

    state: dict = {}
    try:
        articles = _load_articles(args, state, exclude_terms)
    except OutputError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"오류: 파일을 찾을 수 없습니다 — {exc.filename}", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print(f"오류: 파일이 아니라 디렉터리입니다 — {_current_input(args, state)}",
              file=sys.stderr)
        return 2
    except PermissionError:
        print(f"오류: 파일을 읽을 권한이 없습니다 — {_current_input(args, state)}",
              file=sys.stderr)
        return 2
    except OSError as exc:
        # 심볼릭 링크 루프, 너무 긴 경로, 경로 중간이 파일 등 — 전부 입력 문제다.
        print(
            f"오류: 파일을 읽지 못했습니다 — {_current_input(args, state)} "
            f"({exc.strerror or exc})",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        where = state.get("current_file")
        prefix = f"오류: {where} — " if where and len(args.from_file or []) > 1 else "오류: "
        print(f"{prefix}{_scrub(exc, args)}", file=sys.stderr)
        return 2
    except Exception as exc:  # 네트워크/PubMed 오류 등 — 빈 결과(rc 1)와 구분해 rc 3
        print(f"오류: 데이터를 가져오지 못했습니다 — {_scrub(exc, args)}", file=sys.stderr)
        return 3

    if not articles:
        print("검색 결과가 없습니다. 검색어/기간을 바꿔 보세요.", file=sys.stderr)
        return 1

    query_label = clean_value(
        args.query or (" + ".join(args.from_file) if args.from_file else "(file)")
    )
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
            gap_sort=args.gap_sort,
            drop_check_tags=not args.include_check_tags,
            exclude_terms=exclude_terms,
            bridge_top_n=0 if args.no_bridges else 3,
            evidence=not args.no_evidence,
            top_evidence_n=args.top_evidence,
            angles=not args.no_angles,
            angle_top_k=args.angle_top_k,
            angle_top_qualifiers=args.angle_top_qualifiers,
            angle_min_expected=args.angle_min_expected,
            angle_max_lift=args.angle_max_lift,
            angle_hide_implausible=args.angle_hide_implausible,
            population=not args.no_population,
            population_top_k=args.population_top_k,
            population_min_articles=args.population_min_articles,
            population_sort=args.population_sort,
            meta=None if args.no_meta else _build_meta(args, state, exclude_terms),
            topic_source=state.get("topic_source", "mesh"),
            total_available=state.get("total_available"),
            n_fetched=state.get("n_fetched"),
            dedup=state.get("dedup"),
        )

        # 표시할 공백쌍을 PubMed 전수로 다시 세어 검증(네트워크). 계층/프레이밍
        # 색인 artifact 를 걸러내는 단계라, 실제 조회 경로에서는 기본으로 켠다.
        if _should_verify(args):
            _verify_gaps(rep, args)

        fmt = args.format or ("json" if args.json else "md")
        if fmt == "csv":
            off = {
                "angles": (args.no_angles, "--no-angles"),
                "evidence": (args.no_evidence, "--no-evidence"),
                "topic-evidence": (args.no_evidence, "--no-evidence"),
                "population": (args.no_population, "--no-population"),
                "population-profile": (args.no_population, "--no-population"),
            }.get(args.csv_section)
            if off and off[0]:
                print(
                    f"안내: {off[1]} 로 해당 분석을 껐기 때문에 "
                    f"`--csv-section {args.csv_section}` 은 헤더만 출력됩니다.",
                    file=sys.stderr,
                )
        if fmt == "json":
            # allow_nan=False + 사전 정화로 항상 표준 JSON 을 보장.
            output = json.dumps(json_safe(rep), ensure_ascii=False, indent=2, allow_nan=False)
        elif fmt == "csv":
            output = render_csv(rep, section=args.csv_section)
        else:
            output = render_markdown(rep)
    except Exception as exc:  # 분석/렌더 중 예기치 못한 오류 — 원시 트레이스백 대신 rc 3
        print(f"오류: 리포트 생성 중 문제가 발생했습니다 — {_scrub(exc, args)}", file=sys.stderr)
        return 3

    if args.out is not None and not args.out.strip():
        print("오류: --out 에 빈 경로가 들어왔습니다(변수가 비었을 수 있습니다).",
              file=sys.stderr)
        return 2
    if args.out:
        try:
            _write_text(args.out, output + "\n")
        except OutputError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return 2
        try:
            _print_safely(f"저장 완료: {args.out}  (논문 {rep['n_articles']}편 분석)")
        except EncodingError:
            pass  # 파일은 이미 저장됐다 — 확인 문구를 못 찍는 것뿐이다.
    else:
        try:
            if not _print_safely(output):
                return 0
        except EncodingError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return 3
    return 0


class EncodingError(Exception):
    """터미널 인코딩으로 리포트를 출력할 수 없음 — rc 3 으로 매핑된다."""


def _print_safely(text: str) -> bool:
    """표준출력에 쓰되 파이프가 먼저 닫혀도 조용히 끝낸다.

    `pubgap ... | head` 는 평범한 사용법인데, 예전에는 BrokenPipeError 트레이스백이
    그대로 노출되고 종료코드가 1('결과 없음')이 되어 래퍼 스크립트를 오도했다.

    **인코딩 실패는 여기서 삼키면 안 된다.** `UnicodeEncodeError` 는 `ValueError` 의
    하위 클래스라 예전에는 아래 broad except 에 걸려, 한국어 콘솔(cp949)이나
    `PYTHONIOENCODING=ascii` 환경에서 **rc 0 + 출력 0바이트 + 오류 메시지 0바이트**로
    끝났다(래퍼 스크립트는 성공으로 읽는다). UTF-8 바이트로 직접 쓰는 것을 먼저
    시도하고, 그것도 실패하면 EncodingError 로 올린다.
    """
    try:
        if sys.stdout is None:
            return False
        print(text)
        sys.stdout.flush()
        return True
    except UnicodeEncodeError:
        try:
            buf = getattr(sys.stdout, "buffer", None)
            if buf is None:
                raise EncodingError(
                    "현재 터미널 인코딩으로는 한국어 리포트를 출력할 수 없습니다."
                )
            buf.write(text.encode("utf-8") + b"\n")
            buf.flush()
            return True
        except (UnicodeEncodeError, OSError, ValueError, AttributeError) as exc:
            raise EncodingError(
                "현재 터미널 인코딩으로는 리포트를 출력할 수 없습니다 "
                f"({exc}). `--out 결과.md` 로 파일에 저장하거나 "
                "PYTHONIOENCODING=utf-8 을 설정하세요."
            )
    except (BrokenPipeError, OSError, AttributeError, ValueError):
        # 인터프리터 종료 시 stdout 을 다시 flush 하다 같은 오류가 나지 않도록 교체.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return False


if __name__ == "__main__":
    raise SystemExit(main())
