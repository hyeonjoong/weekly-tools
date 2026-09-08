"""항목 12종 추출 — 전부 결정론적 정규식. LLM·추론 없음.

각 추출기는 문서 하나에서 `Extraction` 목록을 냅니다. **못 찾으면 빈 목록**을 내고,
그 사실은 compare 단계에서 `대조불가` 로 자백됩니다. 추측해서 채우지 않습니다.

값은 `raw`(원문 조각) 와 `norm`(비교값) 을 함께 가집니다. 비교는 `norm` 으로만 하고,
리포트에는 `raw` 를 (마스킹해서) 인쇄합니다 — 정규화 때문에 같다고 본 쌍은 커버리지
블록에 개수로 자백됩니다.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from . import mask, textnorm
from .model import (Doc, Extraction, Para, ROLE_AD, ROLE_ASSENT, ROLE_CRF, ROLE_ICF,
                    ROLE_PROTOCOL)

#: 문장 분리 — 마침표·물음표·느낌표·줄바꿈. 표 행은 통째로 한 문장.
_SENT_SPLIT = re.compile(r"(?<=[^0-9][.!?。])\s+|(?<=다\.)\s*|(?<=요\.)\s*")  # `2026. 8. 25.` 은 쪼개지 않음


def _sentences(p: Para) -> Iterable[str]:
    if p.table:
        yield p.text
        return
    for s in _SENT_SPLIT.split(p.text):
        s = s.strip()
        if s:
            yield s


def _mk(item: str, sub: str, doc: Doc, raw: str, norm: str, p: Para, sentence: str,
        rules: Tuple[str, ...] = (), source: str = "") -> Extraction:
    return Extraction(item=item, sub=sub, doc=doc.name, label=doc.label, raw=raw.strip(),
                      norm=norm, para=p.idx, where=p.where(), sentence=_cut(sentence.strip()),
                      norm_rules=rules, source=(source or raw).strip())


def _cut(text: str, limit: int = 240) -> str:
    """근거 문장 절단 — 전화번호 한가운데를 자르지 않도록 공백 경계까지 늘립니다 (마스킹 전 부분 노출 방지)."""
    if len(text) <= limit:
        return text
    cut = text.find(" ", limit)
    if cut < 0 or cut > limit + 80:
        cut = limit + 80
    return text[:cut] + "…"


def _dedupe(items: List[Extraction]) -> List[Extraction]:
    seen: Set[Tuple[str, str]] = set()
    out: List[Extraction] = []
    for e in items:
        key = (e.sub, e.norm)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# ----------------------------------------------------------------- 1. 연구제목

_TITLE_RX = re.compile(
    r"(?:^|\t)\s*(?:[0-9]{1,2}[.)]\s*|[①-⑳]\s*|[가-힣][.)]\s*)?(?:연구\s*(?:의\s*)?제목|연구\s*과제명|과제명|임상시험\s*제목|연구명|임상시험명|연구\s*명칭|study\s*title|title|protocol\s*title)"
    r"\s*(?:\((국문|영문|한글|영어|kor|eng)\))?\s*[:：\t]\s*(.+?)\s*$", re.IGNORECASE)


def extract_title(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras[:400]:
        m = _TITLE_RX.search(p.text)
        if not m:
            continue
        raw = m.group(2).split("\t")[0].strip()
        if len(raw) < 6 or len(raw) > 300:
            continue
        lang = (m.group(1) or "").lower()
        if lang in ("영문", "영어", "eng") or (sum(ch.isascii() and ch.isalpha() for ch in raw) > len(raw) * 0.6):
            sub = "영문"
        else:
            sub = "국문"
        out.append(_mk("title", sub, doc, raw, textnorm.squash(raw), p, p.text, ("공백·문장부호 제거",)))
    return _dedupe(out)


# ----------------------------------------------------------------- 2. 버전·날짜

_VER_CTX = re.compile(r"version|ver\b|\bv[0-9]|버전|판번호|개정|작성일|승인일|날짜|date", re.IGNORECASE)
_VER_HISTORY = re.compile(r"개정\s*이력|변경\s*이력|이력|history|최초\s*작성|에서\s*[0-9.]+\s*(?:판|으?로)\s*개정|이전\s*버전|구\s*버전|revision\s*history", re.IGNORECASE)


def _vdisp(token: str) -> str:
    t = token.strip()
    return t if re.match(r"^[vV]", t) else "v" + t


def extract_version(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    fname = textnorm.basic(doc.name)
    head_paras = doc.paras[:60] + [p for p in doc.paras[60:] if p.table and _VER_CTX.search(p.text)][:40]
    found_ver = False
    found_date = False
    for p in head_paras:
        if not _VER_CTX.search(p.text):
            continue
        for s in _sentences(p):
            if _VER_HISTORY.search(s) or len(textnorm._VER.findall(s)) > 1:
                continue  # 개정 이력 줄이나 버전이 둘 이상 적힌 문장은 이 문서의 버전이 아니다
            if not found_ver and re.search(r"version|ver\b|\bv\s*[0-9]|버전|판번호|개정번호|[0-9]\.[0-9]\s*판", s, re.IGNORECASE):
                vm = textnorm.version_match(s)
                if vm:
                    out.append(_mk("version", "버전", doc, _vdisp(vm[1]), vm[0], p, s, ("버전 통일",), source=vm[1]))
                    found_ver = True
            if not found_date and re.search(r"version|버전|작성일|승인일|개정일|date|날짜|일자", s, re.IGNORECASE):
                ds = textnorm.find_dates_with_text(s)
                if ds:
                    out.append(_mk("version", "날짜", doc, ds[0][0], ds[0][0], p, s, ("날짜 통일",), source=ds[0][1]))
                    found_date = True
    if not found_ver:
        vm = textnorm.version_match(fname)
        if not vm:
            m = re.search(r"(?<![0-9.])(?:v|ver)?([0-9]\.[0-9](?:\.[0-9])?)(?![0-9])", fname, re.IGNORECASE)
            vm = (textnorm.canon_version(m.group(1)), m.group(0)) if m else None
        if vm:
            out.append(Extraction(item="version", sub="버전", doc=doc.name, label=doc.label, raw=_vdisp(vm[1]), norm=vm[0],
                                  para=-1, where="파일명", sentence=fname, norm_rules=("버전 통일",), source=vm[1]))
    if not found_date:
        ds = textnorm.find_dates_with_text(fname)
        if ds:
            out.append(Extraction(item="version", sub="날짜", doc=doc.name, label=doc.label, raw=ds[0][0], norm=ds[0][0],
                                  para=-1, where="파일명", sentence=fname, norm_rules=("날짜 통일",), source=ds[0][1]))
    return _dedupe(out)


# ----------------------------------------------------------------- 3. 연구책임자·기관

_PI_RX = re.compile(
    r"(?:연구\s*책임자|책임\s*연구자|시험\s*책임자|principal\s*investigator|\bPI\b)\s*(?:이름|성명|명|\(?성명\)?)?\s*[:：\t]?\s*"
    r"([가-힣]{2,4}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})(?=\s|$|\t|[(,·/]|교수|박사|선생|원장|소장|과장)", re.IGNORECASE)
_ORG_RX = re.compile(
    r"(?:연구\s*기관|실시\s*기관|시험\s*기관|소속\s*기관|기관명|소속|institution|site)\s*(?:명)?\s*[:：\t]\s*([^\t\n,;(]{2,40}?)\s*(?:$|\t|,|;|\()", re.IGNORECASE)
_PI_COVER = re.compile(r"(?:연구\s*책임자|책임\s*연구자)\s*((?:소속|직위|직책|성명|이름|기관)(?:\s*[/·,]\s*(?:소속|직위|직책|성명|이름|기관))+)\s*[:：\t]\s*(.+)$")
_ORG_WORD = re.compile(r"[가-힣A-Za-z]*(?:병원|대학교|대학|의료원|센터|연구소|연구원|의원|클리닉|Hospital|University|Institute|Center)[가-힣A-Za-z]*")


def _org_token(text: str) -> str:
    m = _ORG_WORD.search(text)
    return m.group(0) if m else ""


_NOT_NAME = {"이름", "성명", "소속", "연락처", "서명", "직위", "직책", "전화", "이메일", "기관", "날짜", "확인", "담당", "정보", "연구자"}
#: 라벨 바로 뒤에 붙는 두 글자 이상 조사·접속어 — 한 글자 조사(가·는·의…)는 이름 최소 길이(2)에 걸려 애초에 안 잡힙니다.
_PARTICLE_START = re.compile(r"^(?:에게|로부터|또는|께서|이며|에게서|에서|한테|으로|이다|입니다|이고|이나|이라|그리고|혹은)")


def extract_pi(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras[:600]:
        for s in _sentences(p):
            cover = _PI_COVER.search(s)
            if cover:  # 표지 표: "연구책임자 소속/직위/성명 \t 가상대학교병원 재활의학과 / 교수 / 김가상"
                labels = [x.strip() for x in re.split(r"[/·,]", cover.group(1))]
                values = [x.strip() for x in re.split(r"[/·,]", cover.group(2))]
                if len(labels) == len(values):
                    for lab, val in zip(labels, values):
                        if re.search(r"성명|이름", lab) and re.fullmatch(r"[가-힣]{2,4}", val):
                            out.append(_mk("pi", "책임자", doc, val, textnorm.squash(val), p, s, ("공백·문장부호 제거",)))
                        elif re.search(r"소속|기관", lab) and len(val) >= 2:
                            org = _org_token(val)
                            if org:
                                out.append(_mk("pi", "기관", doc, org, textnorm.squash(org), p, s, ("공백·문장부호 제거",)))
                    continue
            m = _PI_RX.search(s)
            if m:
                name = m.group(1).strip()
                if name not in _NOT_NAME and not re.match(r"^(?:이름|성명|소속)", name) and not _PARTICLE_START.match(name):
                    out.append(_mk("pi", "책임자", doc, name, textnorm.squash(name), p, s, ("공백·문장부호 제거",)))
                    paren = re.match(r"\s*\(([^)]{2,60})\)", s[m.end():])
                    if paren:
                        org = _org_token(paren.group(1))
                        if org:
                            out.append(_mk("pi", "기관", doc, org, textnorm.squash(org), p, s, ("공백·문장부호 제거",)))
            m2 = _ORG_RX.search(s)
            if m2:
                org = m2.group(1).strip()
                if len(org) >= 2 and org not in _NOT_NAME:
                    out.append(_mk("pi", "기관", doc, org, textnorm.squash(org), p, s, ("공백·문장부호 제거",)))
    return _dedupe(out)


# ----------------------------------------------------------------- 4. 연락처

def extract_contact(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras:
        for s in _sentences(p):
            for m in mask._PHONE.finditer(s):
                raw = m.group(0)
                digits = re.sub(r"[^0-9]", "", raw)
                if digits.startswith("82"):
                    digits = "0" + digits[2:].lstrip("0")
                if len(digits) < 8 or len(digits) > 11:
                    continue
                out.append(_mk("contact", "전화", doc, raw, digits, p, s))
            for m in mask._EMAIL.finditer(s):
                raw = m.group(0)
                out.append(_mk("contact", "이메일", doc, raw, raw.lower(), p, s))
    return _dedupe(out)


# ----------------------------------------------------------------- 5. 대상자 수

_N_CTX = re.compile(r"대상자|참여자|참가자|피험자|모집|표본|인원|환자|군|명\s*을|명\s*이|명\s*으로|subjects?|participants?", re.IGNORECASE)
_N_STAFF = re.compile(r"연구자|연구원|간호사|담당자|연구진|심사위원|위원|평가자|검사자|의사\s*[0-9]")
_N_STAFF_ADJ = re.compile(r"(?:연구자|연구원|간호사|담당자|위원|평가자|검사자|의사)\s*(?:은|는|이|가|을|를|약)?\s*$")
_N_BACKGROUND = re.compile(r"선행\s*연구|기존\s*연구|문헌|보고(?:되|된|하였)|메타\s*분석|연구에서는|을?\s*대상으로\s*한\s*연구|에서\s*[0-9,]+\s*명을?\s*(?:분석|보고)|유병률|추정된다|국한|한계가")
_N_CAP_TAIL = re.compile(r"\s*(?:미만|초과|까지|이내|내외|가량|안팎)")
_N_CAP_HEAD = re.compile(r"(?:최대|최소|대부분|평균|많아야|적어도|최소한)\s*$")
_N_NUM = re.compile(r"(?:총\s*)?(?<![0-9.,])([0-9]{1,3}(?:,[0-9]{3}){0,3})\s*명(?![0-9])")


def extract_n(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras:
        for s in _sentences(p):
            if not _N_CTX.search(s):
                continue
            if _N_STAFF.search(s) and not re.search(r"대상자|참여자|피험자|참가자", s):
                continue
            if _N_BACKGROUND.search(s):
                continue  # 배경·선행연구의 인원은 이 연구의 표본수가 아니다
            last_end = 0
            for m in _N_NUM.finditer(s):
                n = int(m.group(1).replace(",", ""))
                prefix = s[max(last_end, m.start() - 12):m.start()]
                tail = s[m.end():m.end() + 8]
                last_end = m.end()
                if n <= 1 or n > 100000:
                    continue  # 1명은 표본수가 아니라 비율·단위 표현("보호자 1명", "1명당")이다
                if re.match(r"\s*의\s*(?:대상자|참여자|피험자|참가자|환자)", tail):
                    pass  # "30명의 참여자" — 뒤의 명사가 대상자면 앞에 연구자가 있어도 표본수
                elif _N_STAFF_ADJ.search(prefix) or re.match(r"\s*(?:의|인)\s*(?:연구자|연구원|간호사|담당자|위원|평가자)", tail):
                    continue  # "연구간호사 2명" · "1명의 연구자" — 대상자가 아니다
                if re.match(r"\s*(?:당|씩|마다|별)", tail):
                    continue  # "대상자 1명당" — 인원이 아니라 단위
                if _N_CAP_TAIL.match(tail) or _N_CAP_HEAD.search(prefix):
                    continue  # "최대 220명까지", "100명 미만" — 상한·하한이지 표본수가 아니다
                out.append(_mk("n", "", doc, m.group(0).strip(), str(n), p, s))
    return _dedupe(out)


# ----------------------------------------------------------------- 6. 연령 범위

def extract_age(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras:
        if "세" not in p.text:
            continue
        for s in _sentences(p):
            if "세" not in s:
                continue
            for pair, token in textnorm.age_ranges_with_text(s):
                if pair[1] > 130:
                    continue
                out.append(_mk("age", "", doc, textnorm.fmt_age(pair), "{}~{}".format(*pair), p, s,
                               ("물결 통일", "전각→반각"), source=token))
    return _dedupe(out)


# ----------------------------------------------------------------- 7. 방문·회기 / 참여 기간

_VISIT_MULTI = re.compile(r"1\s*회\s*또는\s*다\s*회기|단회\s*또는\s*다회기|다회기")
_VISIT_COUNT = re.compile(
    r"(?:총\s*)?([0-9]{1,2}|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:회|번|차례)\s*(?:의\s*)?(?:방문|내원|회기|세션|session)"
    r"|(?:방문|내원|회기|세션|session)\s*(?:은|는|횟수는|횟수|수는|수|:|：|총)?\s*[:：]?\s*(?:총\s*)?([0-9]{1,2}|한|두|세|네|다섯|여섯)\s*(?:회|번|차례)"
    r"|(?:총\s*)?([0-9]{1,2})\s*회기(?:로|의|를|에)", re.IGNORECASE)
_VISIT_ENUM = re.compile(r"(?:\bVisit|\bV|방문|내원|회기)\s*[-#]?\s*([0-9]{1,2})(?![0-9])", re.IGNORECASE)
#: `연구 기간`(전체 연구 창)은 대상자 참여 기간이 아니므로 잡지 않습니다.
_PERIOD = re.compile(r"(?:참여\s*기간|참여하시는\s*기간|총\s*참여|참여\s*예상\s*기간|참여\s*하게\s*되는\s*기간)[^0-9]{0,20}?(?:약\s*)?([0-9]{1,3})\s*(주|개월|일|년)")


def extract_visits(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    enum_nums: Dict[int, Tuple[Para, str]] = {}
    for p in doc.paras:
        for s in _sentences(p):
            if _VISIT_MULTI.search(s):
                out.append(_mk("visits", "방문횟수", doc, _VISIT_MULTI.search(s).group(0), "1회 또는 다회기", p, s))
            for m in _VISIT_COUNT.finditer(s):
                tok = m.group(1) or m.group(2) or m.group(3)
                c = textnorm.count(tok + "회")
                if c is None or c <= 0 or c > 60:
                    continue
                tail = s[m.end():m.end() + 4]
                head = s[max(0, m.start() - 4):m.start()]
                if c == 1 and (re.match(r"\s*(?:시|당|마다|별|에|의|당시)", tail) or re.search(r"매\s*$", head)):
                    continue  # "1회 방문 시 90분" — 횟수가 아니라 '방문당'
                if re.search(r"(?:주|월|일|매주|매월|격주)\s*$", head) or re.search(r"제\s*$", head) or re.match(r"\s*차", tail):
                    continue  # "주 1회 방문" 은 빈도, "제2회 방문"·"3회차 방문" 은 서수
                out.append(_mk("visits", "방문횟수", doc, m.group(0).strip(), str(c), p, s, ("횟수 통일",)))
            for m in _VISIT_ENUM.finditer(s):
                n = int(m.group(1))
                if 0 < n <= 60 and n not in enum_nums:
                    enum_nums[n] = (p, s)
            m = _PERIOD.search(s)
            if m:
                out.append(_mk("visits", "참여기간", doc, m.group(1) + m.group(2), "{}{}".format(m.group(1), m.group(2)), p, s))
    if len(enum_nums) >= 2:
        top = max(enum_nums)
        p, s = enum_nums[top]
        raw = "Visit 1~{} ({}개 회기 칸)".format(top, len(enum_nums)) if not re.search(r"방문|내원|회기", s) else "방문 1~{} ({}개 회기)".format(top, len(enum_nums))
        out.append(_mk("visits", "방문횟수", doc, raw, str(top), p, s))
    return _dedupe(out)


# ----------------------------------------------------------------- 8. 소요시간

_DUR_CTX = re.compile(r"소요|걸리|걸립|정도\s*(?:가\s*)?(?:소요|걸|필요)|약\s*[0-9]+\s*(?:분|시간)|시간이\s*(?:필요|걸)|분\s*(?:정도|가량|내외)|시간\s*(?:정도|가량|내외)")
_DUR_TOK = re.compile(r"(?<![0-9])(?:[0-9]+(?:\.[0-9]+)?\s*시간\s*(?:반|[0-9]+\s*분)?|[0-9]+\s*분|(?:한|두|세|네)\s*시간\s*반?)(?![0-9])")
_DUR_EXCL = re.compile(r"보관|이내에|이전에|이후에|간격|주\s*[0-9]|일\s*[0-9]+\s*시간|수면|취침|착용")
_DUR_TOK_EXCL = re.compile(r"(?:매일|하루|일일|매주|주\s*[0-9]+\s*회)\s*$")


def extract_duration(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras:
        for s in _sentences(p):
            if not _DUR_CTX.search(s) or _DUR_EXCL.search(s):
                continue
            for m in _DUR_TOK.finditer(s):
                mins = textnorm.minutes(m.group(0))
                if mins is None or mins <= 0 or mins > 480:
                    continue
                if _DUR_TOK_EXCL.search(s[max(0, m.start() - 8):m.start()]):
                    continue  # "매일 30분씩 훈련" 은 회기 소요시간이 아니다
                out.append(_mk("duration", "", doc, m.group(0).strip(), str(mins), p, s, ("시간→분",)))
    return _dedupe(out)


# ----------------------------------------------------------------- 9. 보상

_COMP_CTX = re.compile(r"보상|사례비|사례금|교통비|답례|기념품|지급|reimburse|compensat", re.IGNORECASE)
_COMP_NONE = re.compile(r"(?:보상금?|사례비|사례금|금전적\s*(?:보상|대가))\s*(?:은|는|이|:|：)?\s*(?:제공되지|지급되지|지급하지|없습니다|없음|없으며|없고|드리지|따로\s*없)")
_COMP_EXCL = re.compile(r"보험|손상|피해\s*보상|배상|예산|연구비|한도|보상\s*범위|치료비|위탁|용역|인건비")
_COMP_UNIT = re.compile(r"(시간당|시간\s*당|방문\s*당|방문별|회당|회\s*당|1회\s*당|1회당|총|일괄|1인당|인당)")


def extract_compensation(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras:
        for s in _sentences(p):
            if not _COMP_CTX.search(s) or _COMP_EXCL.search(s):
                continue  # 임상시험보험 보상 한도·연구비 예산은 대상자 보상이 아니다
            if _COMP_NONE.search(s):
                out.append(_mk("compensation", "", doc, _COMP_NONE.search(s).group(0), "없음", p, s))
                continue
            if re.search(r"실비", s):
                out.append(_mk("compensation", "", doc, "실비", "실비", p, s))
            for m in re.finditer(r"(?<![0-9])((?:[0-9]{1,3}(?:,[0-9]{3}){0,4}|[0-9]{1,9})(?:\.[0-9]{1,2})?\s*(?:천|만|억){0,2}(?:\s*[0-9]{1,4}\s*(?:천|만|억){1,2})*)\s*원", s):
                amt = textnorm.money(m.group(0))
                if amt is None or amt <= 0:
                    continue
                ctx = s[max(0, m.start() - 12):m.start()]
                ums = list(_COMP_UNIT.finditer(ctx))
                unit = ums[-1].group(1).replace(" ", "") if ums else ""
                unit = {"시간당": "시간당", "방문당": "방문당", "방문별": "방문당", "회당": "회당", "1회당": "회당",
                        "총": "총액", "일괄": "총액", "1인당": "총액", "인당": "총액"}.get(unit, unit)
                norm = "{}원".format(amt) + ("/" + unit if unit else "")
                out.append(_mk("compensation", "", doc, m.group(0).strip() + (" ({})".format(unit) if unit else ""), norm, p, s, ("금액 통일",)))
    return _dedupe(out)


# ----------------------------------------------------------------- 10. 보관기간

_RET_CTX = re.compile(r"보관|보존|폐기|파기|retain|retention", re.IGNORECASE)
_RET_TOK = re.compile(r"(?<![0-9])(?:[0-9]{1,2}\s*년(?:\s*[0-9]{1,2}\s*개월)?|[0-9]{1,3}\s*개월|(?:일|이|삼|사|오|육|칠|팔|구|십)\s*년)(?:간|동안|\s*후|\s*이후|\s*까지|\s*이상)?")


def extract_retention(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    for p in doc.paras:
        for s in _sentences(p):
            if not _RET_CTX.search(s):
                continue
            for m in _RET_TOK.finditer(s):
                mo = textnorm.months(m.group(0))
                if mo is None or mo <= 0 or mo > 1200:
                    continue
                out.append(_mk("retention", "", doc, m.group(0).strip(), "{}개월".format(mo), p, s, ("기간 통일",)))
    return _dedupe(out)


# ----------------------------------------------------------------- 11. 선정·제외기준

_CRIT_HEAD = re.compile(r"^(?:[0-9.)\s]*|[가-힣][.)]\s*|\([0-9]+\)\s*)?(선정\s*기준|제외\s*기준|포함\s*기준|배제\s*기준|inclusion\s*criteria|exclusion\s*criteria)\s*[:：]?\s*$", re.IGNORECASE)
_CRIT_ITEM = re.compile(r"^(?:[0-9]{1,2}[.)]|\([0-9]{1,2}\)|[①-⑳]|[-•·▪◦]|[가-힣][.)]|[a-z][.)])\s*\S")
_CRIT_INLINE = re.compile(r"(선정\s*기준|제외\s*기준|포함\s*기준|배제\s*기준)\s*(?:은|는|:|：)?\s*(?:다음|아래)?", re.IGNORECASE)


def extract_criteria(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    paras = doc.paras
    i = 0
    while i < len(paras):
        p = paras[i]
        m = _CRIT_HEAD.match(p.text) if not p.table else None
        if not m:
            i += 1
            continue
        kind = "제외" if re.search(r"제외|배제|exclusion", m.group(1), re.IGNORECASE) else "선정"
        j = i + 1
        items: List[str] = []
        style = ""
        while j < len(paras):
            q = paras[j]
            if _CRIT_HEAD.match(q.text) or (not q.table and _is_section_heading(q.text, style)):
                break  # 다음 절 제목이 시작되면 끝 ("4. 평가 항목" 이 항목으로 세이지 않게)
            if _CRIT_ITEM.match(q.text) and not style:
                style = _marker_style(q.text)
            elif _CRIT_ITEM.match(q.text) and _marker_style(q.text) != style:
                break  # 번호 스타일이 바뀌면 (1) 2) … 다음 "4. 절제목") 목록이 끝난 것
            if q.table and _is_checklist_header(q.text):
                j += 1
                continue  # CRF 체크리스트의 헤더 행(기준 | 예 | 아니오)은 기준 항목이 아니다
            if _CRIT_ITEM.match(q.text) or (q.table and q.text.strip()):
                body = re.sub(r"^(?:[0-9]{1,2}[.)]|\([0-9]{1,2}\)|[①-⑳]|[-•·▪◦]|[가-힣][.)]|[a-z][.)])\s*", "", q.text).strip()
                if body:
                    items.append(body)
            elif items:
                break
            j += 1
        if items:
            out.append(_mk("criteria", kind + "기준 개수", doc, "{}개".format(len(items)), str(len(items)), p, p.text))
            for it in items:
                out.append(_mk("criteria", kind + "항목", doc, it[:120], textnorm.squash(it)[:80], p, it, ("공백·문장부호 제거",)))
        i = max(j, i + 1)
    return _dedupe(out)


_CHECK_VOCAB = {"기준", "번호", "항목", "예", "아니오", "yes", "no", "y", "n", "충족", "미충족", "확인", "해당", "비해당", "판정", "결과", "no.", "#", "내용", "선정기준", "제외기준", "비고", "체크"}


def _is_checklist_header(row_text: str) -> bool:
    cells = [c.strip().lower() for c in row_text.split("\t") if c.strip()]
    if not cells:
        return True
    return all(c in _CHECK_VOCAB or re.fullmatch(r"[□☐☑✓✔○×xv\-_ ]*", c) for c in cells) or (
        len(cells) >= 2 and sum(1 for c in cells if c in _CHECK_VOCAB) >= len(cells) - 1 and any(c in ("예", "아니오", "yes", "no", "충족", "미충족") for c in cells))


_HEAD_NOUN = re.compile(r"(?:항목|기준|방법|절차|목적|배경|설계|보상|정보|일정|기간|평가|분석|관리|보호|연락처|모집|개요|요약)$")


def _marker_style(text: str) -> str:
    m = re.match(r"^(?:[0-9]{1,2}([.)])|(\([0-9]{1,2}\))|([①-⑳])|([-•·▪◦])|[가-힣]([.)])|[a-z]([.)]))", text.strip())
    if not m:
        return ""
    if m.group(1):
        return "num" + m.group(1)
    if m.group(2):
        return "paren"
    if m.group(3):
        return "circle"
    if m.group(4):
        return "bullet"
    return "alpha" + (m.group(5) or m.group(6) or "")


def _is_section_heading(text: str, item_style: str) -> bool:
    """항목 목록 안에서 '다음 절 제목'으로 볼 줄인가. 짧은 명사구 + 제목형 어미, 또는 리더가 제목으로 본 줄(번호 없음)."""
    t = text.strip()
    body = re.sub(r"^(?:제\s*[0-9]+\s*[장절조항]\s*|[0-9]+(?:\.[0-9]+)*[.)]?\s*|\([0-9]+\)\s*|[①-⑳]\s*|[가-힣][.)]\s*)", "", t)
    if len(body) <= 14 and _HEAD_NOUN.search(body) and not body.endswith(("자", "경우", "사람", "환자")):
        return True
    if not _CRIT_ITEM.match(t) and _looks_like_heading_break(t):
        return True
    return False


def _looks_like_heading_break(text: str) -> bool:
    t = text.strip()
    return len(t) <= 40 and not t.endswith(("다.", "요.", "함", "음", "됨", ".")) and bool(re.match(r"^(?:제\s*[0-9]+\s*[장절조항]|[0-9]+(?:\.[0-9]+)*[.)]?\s+\S|[IVX]+[.)]\s)", t))


# ----------------------------------------------------------------- 12. 평가·검사 항목

_ASSESS_SUFFIX = r"(?:검사|척도|설문지|설문|질문지|지수|일지|평가지|평가|측정|Index|Scale|Inventory|Questionnaire|Test|Diary|Assessment)"
_FORM_LINE = re.compile(r"^(?:[0-9]{1,2}[.)]\s*|[①-⑳]\s*|[-•·]\s*)?([A-Za-z가-힣0-9][A-Za-z가-힣0-9\-\s()/]{1,38}?" + _ASSESS_SUFFIX + r")\s*(?:\(([A-Za-z][A-Za-z0-9\- ]{1,24})\))?\s*$")
_ASSESS_CTX = re.compile(r"평가\s*항목|검사\s*항목|측정\s*항목|평가\s*변수|평가\s*도구|측정\s*도구|평가는|검사는|측정은|다음\s*(?:검사|평가|측정)|다음과\s*같은\s*(?:검사|평가|설문)|(?:검사|평가|설문|척도|지수|설문지|일지)\s*(?:\([^)]{1,24}\))?\s*[)）]?\s*(?:를|을|와|과|및|,)?\s*(?:실시|시행|수행|평가|측정|작성)(?:한다|합니다|하며|하고|됩니다|하게|하시게|받으시게|받게|받습니다|하십니다|하시고|되며)", re.IGNORECASE)
_ASSESS_TOK = re.compile(r"([A-Za-z가-힣][A-Za-z가-힣0-9\- ]{1,30}?(?:" + _ASSESS_SUFFIX + r")+)(?:\s*\(([A-Za-z][A-Za-z0-9\- ]{1,24})\))?")
_ASSESS_LEADIN = re.compile(r"(?:다음|아래)(?:과\s*같은|의|과\s*같이)?\s*(?:설문|검사|평가|측정|도구|항목|척도)[^.]{0,20}(?:실시|시행|수행|평가|측정|작성|사용)|[:：]\s*$")
_ASSESS_LEAD = re.compile(r"^(?:다음|아래|위|해당|이|그|각|모든|본|재|전|후|주요|기타|같은|등의|위한|및|또는|그리고|필요한|추가|관련)$")


def _clean_assess_name(name: str) -> str:
    """'다음과 같은 검사' → '' / '삶의질 설문지' → '삶의질 설문지'. 앞쪽 조사·지시어 토큰을 떼어 냅니다."""
    name = re.sub(r"^.*?(?:방문|회기|내원|Visit\s*[0-9]+)\s*(?:시|에|때|마다|에는)?\s*", "", name)  # "제2회 방문 시 혈액검사" → "혈액검사"
    toks = name.split()
    while toks and (_ASSESS_LEAD.match(toks[0]) or re.search(r"(?:은|는|이|가|을|를|의|과|와|로|으로|에서|에)$", toks[0]) and len(toks) > 1):
        toks = toks[1:]
    return " ".join(toks)
_ASSESS_STOP = {"평가", "검사", "측정", "설문", "척도", "지수", "일지", "본검사", "재검사", "전검사", "후검사", "다음검사", "해당검사", "각검사", "모든검사", "이검사", "모든평가", "각평가", "결과평가", "안전성평가", "유효성평가"}


def extract_assessments(doc: Doc) -> List[Extraction]:
    out: List[Extraction] = []
    carry = 0
    for p in doc.paras:
        if doc.role == ROLE_CRF:
            cells = p.text.split("\t") if p.table else [p.text]
            for cell in cells[:1] if p.table else cells:
                m = _FORM_LINE.match(cell.strip())
                if m:
                    name = m.group(1).strip()
                    key = textnorm.squash(name)
                    if key in _ASSESS_STOP or len(key) < 3:
                        continue
                    out.append(_mk("assessments", "", doc, name + (" ({})".format(m.group(2)) if m.group(2) else ""), key, p, cell, ("공백·문장부호 제거",)))
            continue
        if p.section == p.text or _looks_like_heading_break(p.text):
            carry = 0
            continue  # "4.3 평가 항목" 같은 절 제목은 평가항목이 아니다
        for s in _sentences(p):
            if not _ASSESS_CTX.search(s) and carry <= 0:
                if _ASSESS_LEADIN.search(s):
                    carry = 3  # "다음 설문을 실시한다:" — 목록이 다음 문장·문단에 온다
                continue
            if not _ASSESS_CTX.search(s):
                carry -= 1
            elif _ASSESS_LEADIN.search(s):
                carry = 3
            for m in _ASSESS_TOK.finditer(s):
                name = _clean_assess_name(m.group(1).strip())
                key = textnorm.squash(name)
                if not name or key in _ASSESS_STOP or len(key) < 3 or re.match(r"^(?:다음|위|아래|해당|이|그|각|모든|본|재|전|후|주요|기타)(?:\s|$)", name):
                    continue
                out.append(_mk("assessments", "", doc, name + (" ({})".format(m.group(2)) if m.group(2) else ""), key, p, s, ("공백·문장부호 제거",)))
    return _dedupe(out)


# ----------------------------------------------------------------- 진입점

EXTRACTORS: Dict[str, Callable[[Doc], List[Extraction]]] = {
    "title": extract_title,
    "version": extract_version,
    "pi": extract_pi,
    "contact": extract_contact,
    "n": extract_n,
    "age": extract_age,
    "visits": extract_visits,
    "duration": extract_duration,
    "compensation": extract_compensation,
    "retention": extract_retention,
    "criteria": extract_criteria,
    "assessments": extract_assessments,
}


def extract_all(doc: Doc) -> List[Extraction]:
    if not doc.readable:
        return []
    out: List[Extraction] = []
    for key, fn in EXTRACTORS.items():
        out.extend(fn(doc))
    return out
