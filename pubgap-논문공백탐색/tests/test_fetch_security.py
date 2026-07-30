"""네트워크 계층의 보안 가드 — 리다이렉트/응답크기/엔티티폭탄/Count.

회귀 배경: `fetch.py` 의 하드닝 코드(리다이렉트 허용목록, 256MB 상한, 엔티티 가드)는
테스트가 하나도 없어, 통째로 지워도 전체 스위트가 초록이었다(변이 테스트에서 HIGH
등급 생존자 5건). 여기서 각 가드를 직접 호출해 고정한다. 실제 네트워크는 쓰지 않는다.
"""

import gzip
import io
import urllib.error

import pytest

from pubgap import fetch


# --------------------------------------------------------------------------- #
# 리다이렉트 허용목록 — https + *.nlm.nih.gov 만
# --------------------------------------------------------------------------- #
def _redirect(newurl):
    """실제 urllib Request 로 리다이렉트 판정을 호출한다."""
    import email.message
    import urllib.request

    req = urllib.request.Request(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed"
    )
    headers = email.message.Message()
    return fetch._SafeRedirectHandler().redirect_request(
        req, io.BytesIO(b""), 302, "Found", headers, newurl
    )


ALLOWED = [
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
    "https://nlm.nih.gov/x",
    "https://NLM.NIH.GOV/x",                 # 호스트는 대소문자 무시
    "HTTPS://eutils.ncbi.nlm.nih.gov/x",     # 스킴도 대소문자 무시
    "https://sub.deep.nlm.nih.gov/x",
    "https://eutils.ncbi.nlm.nih.gov:8443/x",
]

BLOCKED = [
    "http://eutils.ncbi.nlm.nih.gov/x",      # 평문 다운그레이드
    "ftp://eutils.ncbi.nlm.nih.gov/x",
    "file:///etc/passwd",
    "https://evil.com/x",
    "https://evil-nlm.nih.gov/x",            # 점이 없으면 접미사가 아니다
    "https://nlm.nih.gov.evil.com/x",        # 접미사처럼 보이는 하위도메인 사칭
    "https://nlm.nih.gov@evil.com/",         # userinfo 사칭
    "https://user:pw@nlm.nih.gov.evil.com/",
    "https://xn--nlm-nih-gov.evil.com/",     # 퓨니코드
    "https://127.0.0.1/x",
    "https://[::1]/x",
    "https://169.254.169.254/latest/meta-data/",  # 클라우드 메타데이터
]


@pytest.mark.parametrize("url", ALLOWED)
def test_redirect_allows_ncbi_https(url):
    assert _redirect(url) is not None


@pytest.mark.parametrize("url", BLOCKED)
def test_redirect_blocks_everything_else(url):
    with pytest.raises(urllib.error.URLError):
        _redirect(url)


def test_opener_actually_uses_the_safe_handler():
    """build_opener 가 기본 핸들러를 **대체**했는지(추가만 한 게 아닌지)."""
    handlers = fetch._OPENER.handlers
    assert any(isinstance(h, fetch._SafeRedirectHandler) for h in handlers)
    plain = [
        h for h in handlers
        if type(h) is __import__("urllib.request", fromlist=["x"]).HTTPRedirectHandler
    ]
    assert plain == [], "기본 리다이렉트 핸들러가 남아 있으면 가드가 무력화된다"


# --------------------------------------------------------------------------- #
# 응답 크기 상한
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self, n=None):
        return self._payload[:n] if n is not None else self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_response_size_cap_rejects_oversized_body(monkeypatch):
    big = b"x" * (fetch.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(fetch._OPENER, "open", lambda *a, **k: _Resp(big))
    with pytest.raises(RuntimeError, match="너무 큽니다"):
        fetch._default_http_get("https://eutils.ncbi.nlm.nih.gov/x")


def test_response_under_cap_is_returned(monkeypatch):
    payload = b"<eSearchResult><Count>3</Count></eSearchResult>"
    monkeypatch.setattr(fetch._OPENER, "open", lambda *a, **k: _Resp(payload))
    assert fetch._default_http_get("https://eutils.ncbi.nlm.nih.gov/x") == payload


def test_default_http_get_sets_a_user_agent(monkeypatch):
    seen = {}

    def fake_open(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp(b"<eSearchResult/>")

    monkeypatch.setattr(fetch._OPENER, "open", fake_open)
    fetch._default_http_get("https://eutils.ncbi.nlm.nih.gov/x")
    assert "pubgap" in (seen["ua"] or "")


# --------------------------------------------------------------------------- #
# 엔티티 폭탄 가드 — 네트워크 경로도 파일 경로와 동일하게 보호
# --------------------------------------------------------------------------- #
BOMB = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE r [<!ENTITY a "AAAAAAAAAA">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    "<eSearchResult><Count>1</Count><Term>&b;</Term></eSearchResult>"
)


def test_entity_bomb_blocked_on_network_path_utf8():
    with pytest.raises(ValueError, match="내부 엔티티"):
        fetch._parse_xml_safely(BOMB.encode("utf-8"), "esearch")


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be", "utf-32"])
def test_entity_bomb_blocked_even_when_utf16_encoded(encoding):
    """회귀: 바이트 정규식만 쓰면 UTF-16 응답에서 '<\\x00!\\x00E…' 라 가드를 우회했다.

    expat 는 BOM/XML 선언을 보고 제대로 디코드해 엔티티를 확장하므로, 가드도
    디코드한 텍스트에 걸어야 한다.
    """
    raw = BOMB.encode(encoding)
    assert b"<!ENTITY" not in raw          # 바이트로는 안 보인다
    with pytest.raises(ValueError, match="내부 엔티티"):
        fetch._parse_xml_safely(raw, "esearch")


def test_normal_response_still_parses():
    root = fetch._parse_xml_safely(
        b"<eSearchResult><Count>7</Count></eSearchResult>", "esearch"
    )
    assert root.find("Count").text == "7"


def test_html_error_page_becomes_korean_runtime_error():
    with pytest.raises(RuntimeError, match="해석하지 못했습니다"):
        fetch._parse_xml_safely(b"<html><body>429", "esearch")


# --------------------------------------------------------------------------- #
# <Count> 파싱 — 표본 절단 감지 사슬의 유일한 원천
# --------------------------------------------------------------------------- #
def _esearch_xml(count, ids=("1", "2")):
    id_xml = "".join(f"<Id>{i}</Id>" for i in ids)
    return (
        f"<eSearchResult><Count>{count}</Count><RetMax>{len(ids)}</RetMax>"
        f"<IdList>{id_xml}</IdList></eSearchResult>"
    ).encode()


def test_esearch_reports_total_count_not_returned_count():
    """회귀: Count 를 버리면 '2,431편 중 300편' 을 말할 수 없어 절단이 안 보인다."""
    total, pmids = fetch.esearch("q", http_get=lambda u: _esearch_xml(2431))
    assert total == 2431
    assert pmids == ["1", "2"]


def test_esearch_falls_back_to_id_count_when_count_missing():
    xml = b"<eSearchResult><IdList><Id>1</Id><Id>2</Id></IdList></eSearchResult>"
    total, pmids = fetch.esearch("q", http_get=lambda u: xml)
    assert total == 2 == len(pmids)


def test_esearch_count_endpoint_returns_the_number():
    assert fetch.esearch_count("q", http_get=lambda u: _esearch_xml(99, ids=())) == 99


def test_esearch_count_raises_without_count():
    with pytest.raises(RuntimeError, match="Count"):
        fetch.esearch_count("q", http_get=lambda u: b"<eSearchResult/>")


def test_fetch_result_truncated_property():
    r = fetch.FetchResult(xml_text="<x/>", total_available=2431, n_fetched=300)
    assert r.truncated is True
    assert fetch.FetchResult(xml_text="<x/>", total_available=5, n_fetched=5).truncated is False


# --------------------------------------------------------------------------- #
# 전수 검증 조회 — 계층 artifact 를 실제로 드러내는지
# --------------------------------------------------------------------------- #
def test_verify_pairs_online_recomputes_over_whole_result_set():
    """실측 시나리오: 표본에서는 0~13편이지만 전수에서는 수천~수만 편."""
    # 상하위어 쌍(Insomnia ⊂ Sleep Wake Disorders): 부모∩자식 = 자식 편수이므로
    # 전수 lift 가 1 을 넘는다 → 공백이 아니다.
    counts = {
        "q": 30000,
        '(q) AND ("Insomnia"[MeSH Terms])': 21673,
        '(q) AND ("Sleep Wake Disorders"[MeSH Terms])': 22000,
        '(q) AND ("Insomnia"[MeSH Terms]) AND ("Sleep Wake Disorders"[MeSH Terms])': 21673,
        '(q) AND ("Sleep Quality"[MeSH Terms])': 600,
        '(q) AND ("Hypnotics and Sedatives"[MeSH Terms])': 900,
        '(q) AND ("Sleep Quality"[MeSH Terms]) AND ("Hypnotics and Sedatives"[MeSH Terms])': 5,
    }
    seen = []

    def http(url):
        from urllib.parse import parse_qs, urlparse

        term = parse_qs(urlparse(url).query)["term"][0]
        seen.append(term)
        return f"<eSearchResult><Count>{counts[term]}</Count></eSearchResult>".encode()

    pairs = [("Insomnia", "Sleep Wake Disorders"),
             ("Sleep Quality", "Hypnotics and Sedatives")]
    got = fetch.verify_pairs_online(pairs, query="q", http_get=http, sleep=0)

    # 호출 수 = 1(전체) + 고유 주제 4 + 쌍 2
    assert len(seen) == 7
    assert got["__total__"] == 30000
    # 상하위어 쌍: 전수 lift 가 1 을 크게 넘어 '공백 아님' 으로 판정된다.
    exp_hier = got["Insomnia"] * got["Sleep Wake Disorders"] / got["__total__"]
    assert got["Insomnia||Sleep Wake Disorders"] / exp_hier > 1.0
    # 진짜 얇은 쌍: lift 가 0.5 아래.
    exp_real = got["Sleep Quality"] * got["Hypnotics and Sedatives"] / got["__total__"]
    assert got["Sleep Quality||Hypnotics and Sedatives"] / exp_real < 0.5


def test_verify_pairs_online_without_query_uses_whole_pubmed():
    seen = []

    def http(url):
        from urllib.parse import parse_qs, urlparse

        seen.append(parse_qs(urlparse(url).query)["term"][0])
        return b"<eSearchResult><Count>10</Count></eSearchResult>"

    fetch.verify_pairs_online([("A", "B")], query=None, http_get=http, sleep=0)
    assert seen[0] == "all[sb]"
    assert '"A"[MeSH Terms]' in seen[1]
