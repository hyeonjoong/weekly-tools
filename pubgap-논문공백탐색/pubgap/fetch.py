"""PubMed E-utilities 클라이언트 (esearch → efetch).

network 접근은 이 모듈에만 있다. HTTP 호출을 `http_get` 인자로 주입할 수 있어
테스트에서는 실제 네트워크 없이 캔드(canned) 응답으로 검증한다.

NCBI 예절: tool/email 파라미터를 붙이고, 요청 사이에 간격을 둔다
(api_key 없으면 3req/s 이하). 참고: https://www.ncbi.nlm.nih.gov/books/NBK25497/
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, List, Optional

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

HttpGet = Callable[[str], bytes]


def _default_http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "pubgap/0.1 (research tool)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - eutils https only
        return resp.read()


def _build_url(endpoint: str, params: dict) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return f"{_EUTILS}/{endpoint}?{urllib.parse.urlencode(clean)}"


def esearch_pmids(
    query: str,
    years: int = 10,
    retmax: int = 300,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    reldate_days: Optional[int] = None,
) -> List[str]:
    """검색어로 최근 `years`년 논문의 PMID 목록을 얻는다.

    reldate_days 를 주면 years 대신 '최근 N일'로 필터한다(테스트/특수용).
    """
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retmode": "xml",
        "sort": "pub_date",
        "tool": "pubgap",
        "email": email,
        "api_key": api_key,
    }
    # years<=0 이면 날짜 필터 없이 전체 기간을 검색한다.
    if reldate_days is not None:
        params["reldate"] = str(reldate_days)
        params["datetype"] = "pdat"
    elif years and years > 0:
        params["reldate"] = str(int(years) * 365 + 1)
        params["datetype"] = "pdat"

    url = _build_url("esearch", params)
    raw = http_get(url)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        # 429 등에서 NCBI 가 HTML 오류 페이지를 돌려주면 XML 파싱이 깨진다.
        raise RuntimeError(
            "PubMed 응답을 해석하지 못했습니다(속도제한/오류 페이지일 수 있음). "
            "--email/--api-key 를 지정하거나 잠시 후 다시 시도하세요."
        ) from exc
    # eSearch 가 에러를 본문에 담아 200 으로 주는 경우를 잡는다.
    err = root.find(".//ERROR")
    if err is not None and (err.text or "").strip():
        raise RuntimeError(f"PubMed 오류: {err.text.strip()}")
    if root.tag != "eSearchResult":
        raise RuntimeError("PubMed esearch 응답 형식이 예상과 다릅니다(속도제한/오류일 수 있음).")
    return [el.text for el in root.findall(".//IdList/Id") if el.text]


def efetch_xml(
    pmids: List[str],
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    batch_size: int = 200,
    sleep: float = 0.34,
) -> str:
    """PMID 목록의 상세(MeSH 포함) XML을 이어붙여 하나의 문자열로 반환.

    여러 배치를 각각 <PubmedArticleSet>...</PubmedArticleSet> 로 받아,
    parse_efetch_xml 이 iter("PubmedArticle") 로 훑을 수 있도록 하나의
    루트로 감싼다.
    """
    if not pmids:
        return "<PubmedArticleSet></PubmedArticleSet>"

    chunks: List[str] = []
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "retmode": "xml",
            "rettype": "abstract",
            "tool": "pubgap",
            "email": email,
            "api_key": api_key,
        }
        url = _build_url("efetch", params)
        raw = http_get(url)
        # 배치별 XML 에서 <PubmedArticle>...</PubmedArticle> 알맹이만 추출.
        root = ET.fromstring(raw)
        for art in root.iter("PubmedArticle"):
            chunks.append(ET.tostring(art, encoding="unicode"))
        if i + batch_size < len(pmids) and sleep:
            time.sleep(sleep)

    return "<PubmedArticleSet>" + "".join(chunks) + "</PubmedArticleSet>"


def fetch_articles_xml(
    query: str,
    years: int = 10,
    retmax: int = 300,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
) -> str:
    """esearch + efetch 를 한 번에 수행해 통합 efetch XML 문자열을 반환."""
    pmids = esearch_pmids(
        query, years=years, retmax=retmax, email=email, api_key=api_key, http_get=http_get
    )
    return efetch_xml(pmids, email=email, api_key=api_key, http_get=http_get)
