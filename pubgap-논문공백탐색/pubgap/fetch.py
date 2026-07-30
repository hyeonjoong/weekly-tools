"""PubMed E-utilities 클라이언트 (esearch → efetch).

network 접근은 이 모듈에만 있다. HTTP 호출을 `http_get` 인자로 주입할 수 있어
테스트에서는 실제 네트워크 없이 캔드(canned) 응답으로 검증한다.

NCBI 예절: tool/email 파라미터를 붙이고, 요청 사이에 간격을 둔다
(api_key 없으면 3req/s 이하). 참고: https://www.ncbi.nlm.nih.gov/books/NBK25497/
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .records import assert_no_internal_entities, decode_bytes

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_ALLOWED_HOST_SUFFIX = ".nlm.nih.gov"

# 한 번의 HTTP 응답으로 받아들일 최대 크기. 정상 efetch 배치(200편)는 수 MB 를 넘지
# 않는다. 상한이 없으면 오작동하거나 악의적인 응답 하나가 메모리를 고갈시킬 수 있다.
MAX_RESPONSE_BYTES = 256 * 1024 * 1024  # 256 MiB

HttpGet = Callable[[str], bytes]


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 **https + NCBI 호스트** 로만 허용한다.

    urllib 의 기본 핸들러는 http/ftp 로의 리다이렉트도 따라간다. 중간자나 침해된
    응답이 평문 http 로 내려보내면 조회 내용이 그대로 노출된다.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            host == "nlm.nih.gov" or host.endswith(_ALLOWED_HOST_SUFFIX)
        ):
            raise urllib.error.URLError(
                f"허용되지 않은 리다이렉트 대상입니다: {parsed.scheme}://{host}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


def _default_http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "pubgap/0.1 (research tool)"})
    with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 - eutils https only
        data = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise RuntimeError(
            f"PubMed 응답이 너무 큽니다({MAX_RESPONSE_BYTES // (1024 * 1024)}MB 초과) — "
            "--max-records 를 줄여 보세요."
        )
    return data


def _parse_xml_safely(raw: bytes, what: str) -> ET.Element:
    """네트워크로 받은 XML 을 파싱 — 내부 엔티티 폭탄 가드를 파일 경로와 동일하게 적용.

    가드는 **디코드한 텍스트에도** 걸어야 한다. 바이트에만 걸면 UTF-16/32 응답에서
    `<\\x00!\\x00E\\x00…` 가 되어 바이트 정규식이 `<!ENTITY` 를 못 보는데, expat 는
    XML 선언/BOM 을 보고 제대로 디코드해 엔티티를 확장한다 — 즉 가드를 우회한다.
    (`--from-file` 경로는 이미 decode_bytes() 이후에 검사하므로 안전했다. 두 경로가
    같은 보호를 받도록 여기서 바이트와 텍스트를 모두 확인한다.)
    """
    assert_no_internal_entities(raw)
    # NUL 을 걷어낸 사본도 검사한다. BOM 없는 UTF-16BE/LE 응답은 decode_bytes 가
    # 판별하지 못하지만(BOM 이 없으므로), expat 는 XML 규격에 따라 '\x00<' 패턴으로
    # 자동 감지해 파싱한다 — 즉 BOM 없이도 우회가 가능하다. NUL 제거는 인코딩 판별과
    # 무관하게 모든 UTF-16/32 변형을 한 번에 덮는다.
    assert_no_internal_entities(raw.replace(b"\x00", b""))
    assert_no_internal_entities(decode_bytes(raw))
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(
            f"PubMed {what} 응답을 해석하지 못했습니다(속도제한/오류 페이지일 수 있음). "
            "--email/--api-key 를 지정하거나 잠시 후 다시 시도하세요."
        ) from exc


def _assert_esearch_result(root: ET.Element) -> None:
    """esearch 응답이 정상 형식인지 확인.

    NCBI 는 속도제한 시 HTTP 200 으로 HTML 오류 페이지를 준다. 이 가드가 없으면
    그 응답이 조용히 '검색 결과 0편'이 되어, 사용자는 rc 1 과 함께 "검색어를 바꿔
    보세요" 라는 엉뚱한 안내를 받는다(연도별 조회 경로에 이 가드가 빠져 있었다).
    """
    err = root.find(".//ERROR")
    if err is not None and (err.text or "").strip():
        raise RuntimeError(f"PubMed 오류: {err.text.strip()}")
    if root.tag != "eSearchResult":
        raise RuntimeError(
            "PubMed esearch 응답 형식이 예상과 다릅니다(속도제한/오류일 수 있음). "
            "--email/--api-key 를 지정하거나 잠시 후 다시 시도하세요."
        )


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
    """검색어로 최근 `years`년 논문의 PMID 목록을 얻는다(전체 편수는 버림)."""
    return esearch(
        query, years=years, retmax=retmax, email=email, api_key=api_key,
        http_get=http_get, reldate_days=reldate_days,
    )[1]


def esearch(
    query: str,
    years: int = 10,
    retmax: int = 300,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    reldate_days: Optional[int] = None,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
) -> Tuple[int, List[str]]:
    """검색어 → (검색결과 **전체 편수**, 가져온 PMID 목록).

    `<Count>` 를 버리면 안 된다. retmax 로 잘린 표본을 분야 전체로 착각하게 만드는
    가장 큰 원인이 그것이다 — 전체 편수를 알아야 리포트가 "2,431편 중 300편(최신순)"
    이라고 밝히고, 추세 관련 출력을 억제할 수 있다.

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
    # 연도 범위가 주어지면 **질의 자체**를 그 창으로 제한한다. 그래야 "기간을 좁혀
    # 전수를 받으세요" 라는 안내가 실제로 성립한다(예전엔 받아온 뒤 걸러내기만 해서,
    # 잘린 표본을 더 줄일 뿐 절대 늘릴 수 없었다).
    if min_year is not None or max_year is not None:
        params["mindate"] = f"{min_year if min_year is not None else 1500}/01/01"
        params["maxdate"] = f"{max_year if max_year is not None else 3000}/12/31"
        params["datetype"] = "pdat"
    elif reldate_days is not None:
        params["reldate"] = str(reldate_days)
        params["datetype"] = "pdat"
    elif years and years > 0:
        params["reldate"] = str(int(years) * 365 + 1)
        params["datetype"] = "pdat"

    url = _build_url("esearch", params)
    # 429 등에서 NCBI 가 HTML 오류 페이지를 돌려주면 XML 파싱이 깨진다.
    root = _parse_xml_safely(http_get(url), "esearch")
    _assert_esearch_result(root)
    pmids = [el.text for el in root.findall(".//IdList/Id") if el.text]
    count_node = root.find("Count")
    total = len(pmids)
    if count_node is not None and (count_node.text or "").strip().isdigit():
        total = int(count_node.text.strip())
    return total, pmids


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
        # 배치별 XML 에서 레코드 알맹이만 추출. 책/챕터(PubmedBookArticle)도 포함해야
        # 오프라인 경로(--from-file)와 같은 코퍼스가 된다.
        root = _parse_xml_safely(http_get(url), "efetch")
        for art in root.iter():
            if art.tag in ("PubmedArticle", "PubmedBookArticle"):
                chunks.append(ET.tostring(art, encoding="unicode"))
        if i + batch_size < len(pmids) and sleep:
            time.sleep(sleep)

    return "<PubmedArticleSet>" + "".join(chunks) + "</PubmedArticleSet>"


def esearch_count(
    term: str,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    reldate_days: Optional[int] = None,
) -> int:
    """검색식 하나의 **전체 결과 편수**만 받는다(retmax=0 — PMID 는 안 받음).

    응답이 `<Count>` 한 줄뿐이라 매우 싸다. 이것으로 표본이 아니라 **PubMed 전체**에
    대해 공백 통계를 다시 계산할 수 있다.
    """
    params = {
        "db": "pubmed", "term": term, "retmax": "0", "retmode": "xml",
        "tool": "pubgap", "email": email, "api_key": api_key,
    }
    if reldate_days is not None:
        # 코퍼스와 **같은 기간 창**을 써야 한다. 창을 안 걸면 전체 역사에 대한 편수가
        # 나와, 1990년대에 활발했지만 최근 10년엔 비어 있는 조합이 'artifact' 로
        # 잘못 판정된다(리포트가 말하는 기간에 대해서는 진짜 공백인데도).
        params["reldate"] = str(reldate_days)
        params["datetype"] = "pdat"
    root = _parse_xml_safely(http_get(_build_url("esearch", params)), "esearch")
    _assert_esearch_result(root)
    node = root.find("Count")
    if node is None or not (node.text or "").strip().isdigit():
        raise RuntimeError("PubMed esearch 응답에 Count 가 없습니다.")
    return int(node.text.strip())


_QUERY_UNSAFE_RE = re.compile(r'["\r\n\t\x0b\x0c]+')


def _mesh_clause(term: str) -> str:
    """주제어를 MeSH 절로 감싼다.

    따옴표를 반드시 제거한다. 주제어에 `"` 가 남아 있으면 인용이 조기 종료돼
    **최상위 OR** 가 만들어지고(`... AND ("x") OR ("Humans"[MeSH Terms])`),
    동시등장 편수가 개별 편수를 넘는 말이 안 되는 값이 나온다. 저자 키워드를 주제로
    쓰는 경로(RIS/CSV, `--include-keywords`)에서 실제로 도달 가능하다.
    """
    return f'"{_QUERY_UNSAFE_RE.sub(" ", str(term)).strip()}"[MeSH Terms]'


def verify_pairs_online(
    pairs: Sequence[Tuple[str, str]],
    query: Optional[str] = None,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    sleep: float = 0.34,
    years: Optional[int] = None,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
) -> Dict[str, int]:
    """표시할 공백쌍을 **PubMed 전수**로 다시 세어 검증한다.

    왜 필요한가: 우리가 가진 코퍼스는 최대 `--max-records` 편짜리 표본이고, MeSH 는
    상하위어를 자동 확장(explode)한다. 그래서 표본에서 '동시등장 0편' 인 쌍이 실제
    문헌에는 수백~수만 편 있을 수 있다(실측: 표본 0편 ↔ 전수 563편, 표본 13편 ↔
    전수 21,673편). 그런 후보를 1순위로 추천하면 도구가 신뢰를 잃는다.

    방법: 코퍼스를 만든 것과 **같은 검색 제한** 안에서 세 종류의 편수를 받는다.
        C_total = count(query)
        C_A     = count(query AND "A"[MeSH])
        C_AB    = count(query AND "A"[MeSH] AND "B"[MeSH])
    그러면 표본이 아니라 전수에 대해 기대·lift 를 계산할 수 있다. 상하위어 쌍은
    C_AB ≈ C_child 라 lift 가 1 이상으로 나와 **자동으로 걸러진다**(MeSH 트리 파일이
    필요 없다 — PubMed 가 이미 계층을 알고 있으므로).

    반환: {"__total__": C_total, term: C_term…, "A||B": C_AB…}
    호출 수 = 1 + (고유 주제 수) + (쌍 수). 12쌍이면 대략 23회.
    """
    base = (query or "").strip()
    # 코퍼스와 같은 기간 제한을 검색식/파라미터로 재현한다.
    if min_year is not None or max_year is not None:
        lo = min_year if min_year is not None else 1500
        hi = max_year if max_year is not None else 3000
        date_clause = f'("{lo}"[dp] : "{hi}"[dp])'
        base = f"({base}) AND {date_clause}" if base else date_clause
        reldate = None
    else:
        reldate = (int(years) * 365 + 1) if years and years > 0 else None

    def _combine(*clauses: str) -> str:
        parts = [c for c in ((base,) + clauses) if c]
        return " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else (parts[0] if parts else "")

    counts: Dict[str, int] = {}
    terms: List[str] = []
    for a, b in pairs:
        for t in (a, b):
            if t not in terms:
                terms.append(t)

    requests: List[Tuple[str, str]] = [("__total__", _combine())]
    requests += [(t, _combine(_mesh_clause(t))) for t in terms]
    requests += [
        (f"{a}||{b}", _combine(_mesh_clause(a), _mesh_clause(b))) for a, b in pairs
    ]

    for i, (key, term) in enumerate(requests):
        if not term:
            # --from-file 처럼 질의가 없으면 전체 PubMed 를 모집단으로 쓴다.
            term = "all[sb]"
        counts[key] = esearch_count(
            term, email=email, api_key=api_key, http_get=http_get,
            reldate_days=reldate,
        )
        if sleep and i + 1 < len(requests):
            time.sleep(sleep)
    return counts


@dataclass
class FetchResult:
    """조회 결과 + 표본이 잘렸는지 판단할 메타데이터."""

    xml_text: str
    total_available: int   # PubMed 가 보고한 검색결과 전체 편수(esearch Count)
    n_fetched: int         # 실제로 받아온 PMID 수

    @property
    def truncated(self) -> bool:
        return self.total_available > self.n_fetched


def esearch_year(
    query: str,
    year: int,
    retmax: int,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
) -> Tuple[int, List[str]]:
    """특정 발행연도 하나로 제한해 (전체 편수, PMID 목록) 을 얻는다."""
    params = {
        "db": "pubmed", "term": query, "retmax": str(retmax), "retmode": "xml",
        "sort": "pub_date", "datetype": "pdat",
        "mindate": f"{year}/01/01", "maxdate": f"{year}/12/31",
        "tool": "pubgap", "email": email, "api_key": api_key,
    }
    root = _parse_xml_safely(http_get(_build_url("esearch", params)), "esearch")
    _assert_esearch_result(root)
    pmids = [el.text for el in root.findall(".//IdList/Id") if el.text]
    node = root.find("Count")
    total = int(node.text.strip()) if node is not None and (node.text or "").strip().isdigit() else len(pmids)
    return total, pmids


def esearch_stratified(
    query: str,
    years: int,
    retmax: int,
    this_year: int,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    sleep: float = 0.34,
) -> Tuple[int, List[str]]:
    """**연도 층화 표집** — 각 발행연도에서 고르게 뽑아 (전체 편수, PMID) 를 반환.

    기본(최신순) 표집은 활발한 분야에서 표본을 **한 해로 붕괴**시킨다(실측: 10년치
    검색인데 표본 300편의 98%가 올해). 그러면 연도 분포도, 부상/쇠퇴도, 심지어
    공백 통계도 '최근 몇 달'에 대한 답이 되어 버린다.

    여기서는 연도마다 esearch 를 한 번씩 돌려 `retmax/연수` 개씩 뽑고, 편수가 모자란
    해의 몫은 남은 해에 재분배한다. esearch 는 PMID 만 받으므로 비용이 낮고, efetch
    비용은 동일하다(어차피 retmax 편만 받는다).
    """
    span = list(range(this_year - max(1, years) + 1, this_year + 1))
    # D5: retmax 가 연수보다 적으면 균등 할당이 1 로 고정돼 **가장 오래된 해들만**
    # 뽑히고, 쓰지도 않을 요청을 연수만큼 보낸다. 그럴 땐 최신 연도부터 잘라 쓴다.
    if retmax < len(span):
        span = span[-max(1, retmax):]

    picked: List[str] = []
    seen: set = set()
    per_year = max(1, retmax // len(span))

    def _take(pmids: List[str], limit: int) -> int:
        """중복을 제외하고 최대 limit 개를 담는다. 실제로 담은 수를 돌려준다."""
        added = 0
        for pid in pmids:
            if added >= limit or len(picked) >= retmax:
                break
            if pid not in seen:
                seen.add(pid)
                picked.append(pid)
                added += 1
        return added

    # 1차: 각 연도에서 균등 할당만큼.
    per_year_pool: Dict[int, List[str]] = {}
    for i, y in enumerate(span):
        _total, pmids = esearch_year(
            query, y, per_year, email=email, api_key=api_key, http_get=http_get
        )
        per_year_pool[y] = pmids
        _take(pmids, per_year)
        if sleep and i + 1 < len(span):
            time.sleep(sleep)

    # 2차: 남은 자리를 최신 연도부터 채운다.
    # D3: 남은 자리는 **중복 제거 후** 개수로 계산해야 한다. 예전엔 연도 경계에서
    # 겹친 PMID 를 그대로 세어 leftover 가 0 이 되고, 채울 수 있는데도 표본이 줄었다.
    if len(picked) < retmax:
        for y in reversed(span):
            if len(picked) >= retmax:
                break
            need = retmax - len(picked)
            _total, pmids = esearch_year(
                query, y, len(per_year_pool.get(y, [])) + need,
                email=email, api_key=api_key, http_get=http_get,
            )
            if _take(pmids, need) and sleep:
                time.sleep(sleep)

    # 전체 편수는 **연도별 Count 의 합이 아니라** 한 번의 esearch 로 얻는다.
    # D4: NLM 은 전자·인쇄 발행일을 모두 pdat 로 검색 가능하게 해서, 연말/연초에
    # 걸친 레코드가 두 해에 중복 계수된다. 합계를 쓰면 전수를 다 받아왔는데도
    # '표본이 잘렸다' 로 잘못 판정돼 추세 출력이 통째로 사라진다.
    grand_total, _ = esearch(
        query, years=years, retmax=0, email=email, api_key=api_key, http_get=http_get
    )
    return grand_total, picked[:retmax]


def fetch_articles(
    query: str,
    years: int = 10,
    retmax: int = 300,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    sample: str = "stratified",
    this_year: Optional[int] = None,
    sleep: float = 0.34,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
) -> FetchResult:
    """esearch + efetch 를 한 번에 수행해 XML 과 전체 편수를 함께 반환.

    sample='stratified'(기본)면 연도별로 고르게 뽑는다 — 잘린 표본이 한 해로
    붕괴하는 것을 막는다. 'recent' 면 예전처럼 최신순 상위 retmax 편.
    """
    if min_year is not None or max_year is not None:
        # 명시적 연도 범위가 있으면 그 창 안에서 최신순으로 받는다(층화는 무의미).
        total, pmids = esearch(
            query, years=years, retmax=retmax, email=email, api_key=api_key,
            http_get=http_get, min_year=min_year, max_year=max_year,
        )
    elif sample == "stratified" and years and years > 0:
        if this_year is None:
            import datetime as _dt

            this_year = _dt.date.today().year
        total, pmids = esearch_stratified(
            query, years=years, retmax=retmax, this_year=this_year,
            email=email, api_key=api_key, http_get=http_get, sleep=sleep,
        )
    else:
        total, pmids = esearch(
            query, years=years, retmax=retmax, email=email, api_key=api_key,
            http_get=http_get,
        )
    xml_text = efetch_xml(
        pmids, email=email, api_key=api_key, http_get=http_get, sleep=sleep
    )
    return FetchResult(xml_text=xml_text, total_available=total, n_fetched=len(pmids))


def fetch_articles_xml(
    query: str,
    years: int = 10,
    retmax: int = 300,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    http_get: HttpGet = _default_http_get,
    sample: str = "recent",
) -> str:
    """esearch + efetch 를 한 번에 수행해 통합 efetch XML 문자열을 반환(구버전 호환)."""
    return fetch_articles(
        query, years=years, retmax=retmax, email=email, api_key=api_key,
        http_get=http_get, sample=sample,
    ).xml_text
