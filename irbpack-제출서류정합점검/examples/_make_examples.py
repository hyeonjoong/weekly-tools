"""합성 예제 패킷 생성기 — 전부 가짜입니다. 실제 환자·실제 IRB 문서가 아닙니다.

    python3 examples/_make_examples.py

만드는 것:
  정합_패킷/        문서 6종이 같은 말을 함 → exit 0 (이게 조용해야 툴이 쓸모 있습니다)
  불일치_패킷/      방문횟수·소요시간·보상·연락처·버전·CRF 범위가 어긋남 → exit 1
  판정불가_패킷/    부속문서 하나가 .hwp → exit 3 (치명이 있어도 3 이 우선)
  개정전_패킷/      --baseline 용 (v1.1: 2회 방문)
  개정후_패킷/      프로토콜만 v1.2 로 3회 방문으로 바뀌고 동의서는 그대로 → --baseline 으로 잡힘

.docx 는 표준 라이브러리(zipfile)로 직접 씁니다 — 이 툴의 파서가 표 셀까지 읽는지 확인하기 위해
핵심 값 몇 개는 일부러 표 안에 넣었습니다.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from typing import List, Sequence, Union
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
Row = Sequence[str]
Block = Union[str, List[Row]]   # 문자열 = 문단, 리스트 = 표(행 목록)


def _p(text: str, heading: bool = False) -> str:
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    return '<w:p>{}<w:r><w:t xml:space="preserve">{}</w:t></w:r></w:p>'.format(style, escape(text))


def _tbl(rows: List[Row]) -> str:
    out = ["<w:tbl>"]
    for row in rows:
        out.append("<w:tr>")
        for cell in row:
            out.append("<w:tc>{}</w:tc>".format(_p(cell)))
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def write_docx(path: str, blocks: Sequence[Block]) -> None:
    body = []
    for b in blocks:
        if isinstance(b, str):
            body.append(_p(b.lstrip("#").strip(), heading=b.startswith("#")))
        else:
            body.append(_tbl(b))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>{}<w:sectPr/></w:body></w:document>').format("".join(body))
    ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
              '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc)


# ----------------------------------------------------------------- 합성 내용 (BELL 톤, 전부 가짜)

TITLE = "소아 인공와우 사용자 대상 음악 기반 청능재활 앱의 유효성 탐색 연구"
PI = "김가상"
ORG = "가상대학교병원"
OFFICE = "02-000-0000"
MOBILE = "010-1234-5678"


def protocol(ver: str, date: str, visits: int = 2, comp: str = "교통비 실비 지급 (해당 기관 기준 적용)") -> List[Block]:
    return [
        "연구계획서",
        [["연구제목", TITLE], ["Version No", ver], ["작성일", date], ["연구책임자 이름", PI], ["연구기관", ORG]],
        "# 1. 연구 배경",
        "인공와우를 사용하는 소아는 음악 지각에 어려움을 겪는 것으로 알려져 있다.",
        "# 2. 연구 목적",
        "본 연구는 음악 기반 청능재활 앱의 유효성을 탐색하는 것을 목적으로 한다.",
        "# 3. 연구 대상자",
        "연구 대상자는 총 90명이며, 소아군 30명, 보호자군 20명, 성인 대조군 40명으로 구성한다.",
        "소아군의 연령은 만 5~12세, 성인 대조군은 만 19~45세이다.",
        "# 3.1 선정기준",
        "1) 인공와우를 1년 이상 사용한 자",
        "2) 보호자가 서면 동의한 자",
        "3) 한국어를 모국어로 사용하는 자",
        "# 3.2 제외기준",
        "1) 중복 장애가 있는 자",
        "2) 최근 3개월 내 다른 임상연구에 참여한 자",
        "# 4. 연구 방법",
        "본 연구는 총 {}회 방문으로 진행한다. 각 방문의 소요시간은 약 90분이다.".format(visits),
        "평가 항목은 다음과 같다: 음악지각검사, 어음인지검사, 삶의질 설문지(QoL), 청능재활 만족도 척도.",
        "# 5. 대상자 모집 및 보상",
        "대상자에게는 {}한다.".format(comp),
        "# 6. 개인정보 보호",
        "수집된 개인정보는 연구 종료 후 3년간 보관한 뒤 폐기한다.",
        "문의: 연구책임자 {} ({}) 전화 {}".format(PI, ORG, OFFICE),
    ]


def icf(kind: str, ver: str, date: str, visits_text: str = "총 2회 방문", dur: str = "약 90분",
        comp: str = "교통비 실비", contact: str = OFFICE, n_text: str = "총 90명(소아 30명, 보호자 20명, 성인 대조군 40명)") -> List[Block]:
    return [
        "연구대상자 설명문 및 동의서 ({})".format(kind),
        [["연구제목", TITLE], ["버전", ver], ["날짜", date]],
        "# 1. 연구 소개",
        "귀하는 본 연구에 참여하도록 요청받았습니다. 참여는 자발적이며 언제든 철회할 수 있습니다.",
        "연구책임자: {} ({})".format(PI, ORG),
        "# 2. 연구 대상",
        "본 연구에는 {} 이 참여합니다. 소아는 만 5~12세, 성인은 만 19~45세입니다.".format(n_text),
        "# 3. 연구 절차",
        "귀하는 {}을 하게 되며, 1회 방문 시 {}이 소요됩니다.".format(visits_text, dur),
        "방문 시 음악지각검사, 어음인지검사, 삶의질 설문지(QoL)를 시행합니다.",
        "# 4. 보상",
        "연구 참여에 대한 보상으로 {}가 지급됩니다.".format(comp),
        "# 5. 개인정보",
        "귀하의 개인정보는 연구 종료 후 3년간 보관된 뒤 폐기됩니다.",
        "# 6. 연락처",
        "연구에 대한 문의: {} 연구책임자, 전화 {}".format(PI, contact),
        "위 내용을 이해하였으며 연구 참여에 자발적으로 동의합니다.",
    ]


def assent(ver: str, date: str, dur: str = "약 90분") -> List[Block]:
    return [
        "어린이용 승낙서 (Assent)",
        [["연구제목", TITLE], ["버전", ver], ["날짜", date]],
        "어린이 여러분, 안녕하세요. 우리는 음악으로 소리를 더 잘 듣게 돕는 앱을 연구하고 있어요.",
        "연구에 참여하면 병원에 총 2회 방문하게 되고, 한 번 올 때마다 {} 정도 걸려요.".format(dur),
        "음악지각검사 와 어음인지검사 를 해요. 하기 싫으면 언제든 그만해도 괜찮아요.",
        "궁금한 게 있으면 {} 선생님에게 물어보세요. 전화 {}".format(PI, OFFICE),
    ]


def crf(ver: str, date: str, visits: int = 2, extra_form: bool = False) -> List[Block]:
    rows = [["Visit {}".format(i), "방문일", "완료 여부"] for i in range(1, visits + 1)]
    forms: List[Block] = [
        "증례기록서 (CRF)",
        [["연구제목", TITLE], ["Version", ver], ["Date", date], ["대상자 번호", "____"]],
        "# 방문 일정",
        [["회기", "일자", "비고"]] + rows,
        "# 평가 폼",
        "음악지각검사",
        "어음인지검사",
        "삶의질 설문지 (QoL)",
        "청능재활 만족도 척도",
    ]
    if extra_form:
        forms.append("수면일지 (Sleep Diary)")
    return forms


def ad(ver: str, contact: str = OFFICE, n_text: str = "소아 30명, 보호자 20명, 성인 대조군 40명 (총 90명)") -> List[Block]:
    return [
        "연구대상자 모집 공고",
        "{} 에서 연구 참여자를 모집합니다.".format(ORG),
        [["연구제목", TITLE], ["버전", ver]],
        "모집 대상: 만 5~12세 인공와우 사용 소아와 만 19~45세 성인. 모집 인원: {}.".format(n_text),
        "총 2회 방문하며 1회 방문 시 약 90분이 소요됩니다.",
        "참여자에게는 교통비 실비 가 지급됩니다.",
        "문의: {} 연구책임자 ({}) / 연락처: {}".format(PI, ORG, contact),
    ]


def _packet(dirname: str, files: dict) -> str:
    d = os.path.join(HERE, dirname)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    for name, blocks in files.items():
        write_docx(os.path.join(d, name), blocks)
    return d


def main() -> None:
    v, dt = "1.2", "2026-08-25"
    _packet("정합_패킷", {
        "연구계획서_v1.2_20260825.docx": protocol(v, dt),
        "ICF_성인용_v1.2_20260825.docx": icf("성인용", v, dt),
        "ICF_보호자용_v1.2_20260825.docx": icf("보호자용", v, dt),
        "Assent_소아용_v1.2_20260825.docx": assent(v, dt),
        "CRF_통합_v1.2_20260825.docx": crf(v, dt),
        "모집공고안_v1.2.docx": ad(v),
    })
    _packet("불일치_패킷", {
        "연구계획서_v1.2_20260825.docx": protocol(v, dt),
        "ICF_성인용_v1.2_20260825.docx": icf("성인용", v, dt, dur="약 90분, 검사가 길어지면 120분",
                                              comp="방문별 시간당 10,000원(교통비 및 참여 사례비 포함)"),
        "ICF_보호자용_v1.2_20260825.docx": icf("보호자용", v, dt, visits_text="1회 또는 다회기 방문",
                                               dur="약 60분, 검사가 길어지면 180분",
                                               comp="방문별 시간당 10,000원(교통비 및 참여 사례비 포함)"),
        "Assent_소아용_v1.1_20260811.docx": assent("1.1", "2026-08-11", dur="30분에서 1시간"),
        "CRF_통합_v1.2_20260825.docx": crf(v, dt, visits=6, extra_form=True),
        "모집공고안_v1.2.docx": ad(v, contact=MOBILE, n_text="소아 30명, 보호자 20명"),
    })
    d = _packet("판정불가_패킷", {
        "연구계획서_v1.2_20260825.docx": protocol(v, dt),
        "ICF_성인용_v1.2_20260825.docx": icf("성인용", v, dt, comp="방문별 시간당 10,000원"),
    })
    with open(os.path.join(d, "ICF_보호자용_v1.2.hwp"), "wb") as fh:
        fh.write(b"HWP Document File V5.00 \x1a\x01\x02\x03\x04\x05" + b"\x00" * 64)
    _packet("개정전_패킷", {
        "연구계획서_v1.1_20260811.docx": protocol("1.1", "2026-08-11", visits=2),
        "ICF_성인용_v1.1_20260811.docx": icf("성인용", "1.1", "2026-08-11"),
        "CRF_통합_v1.1_20260811.docx": crf("1.1", "2026-08-11"),
    })
    _packet("개정후_패킷", {
        "연구계획서_v1.2_20260825.docx": protocol("1.2", "2026-08-25", visits=3),
        "ICF_성인용_v1.2_20260825.docx": icf("성인용", "1.2", "2026-08-25"),
        "CRF_통합_v1.2_20260825.docx": crf("1.2", "2026-08-25"),
    })
    print("예제 패킷 5종 생성 완료:", HERE)


if __name__ == "__main__":
    main()
