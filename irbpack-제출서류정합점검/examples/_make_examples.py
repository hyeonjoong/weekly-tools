#!/usr/bin/env python3
"""번들 예제 패킷 3종을 다시 만든다 (전부 **합성** — 실제 환자·실제 IRB 문서 없음).

  정합_패킷/        서로 말이 맞는 패킷        → 치명 0건, exit 0
  불일치_패킷/      일부러 어긋뜨린 패킷        → 치명 4건 · 경고 1건, exit 1
  판정불가_패킷/    .hwp 가 섞여 못 읽는 패킷    → exit 3
  개정_이전_패킷/   v1.1 패킷 (--baseline 용)
  개정_이후_패킷/   v1.2 패킷 — 계획서만 5회로 고치고 동의서를 안 고쳤다 → 치명

같은 내용을 `docx/<패킷>/` 에도 .docx 로 만들어 둔다(표 파싱 경로를 실제로 밟기 위해서).

    python3 examples/_make_examples.py
"""

import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 최소 .docx 작성기

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _paragraph(text):
    return "<w:p><w:r><w:t xml:space=\"preserve\">%s</w:t></w:r></w:p>" % _escape(text)


def _table(rows):
    out = ["<w:tbl>"]
    for row in rows:
        out.append("<w:tr>")
        for cell in row:
            out.append("<w:tc>%s</w:tc>" % _paragraph(cell))
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def write_docx(path, blocks):
    """blocks: ('p', 문단) 또는 ('t', [[셀, 셀], ...]) 의 목록."""
    body = []
    for kind, payload in blocks:
        body.append(_paragraph(payload) if kind == "p" else _table(payload))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>%s</w:body></w:document>" % "".join(body)
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return path


def blocks_to_markdown(blocks):
    lines = []
    for kind, payload in blocks:
        if kind == "p":
            lines.append(payload)
        else:
            for row in payload:
                lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------- 합성 문서 내용
# 전부 가상의 연구입니다. 실제 대상자·실제 기관·실제 연락처가 아닙니다.

TITLE = "비접촉 호흡유도 음향자극의 불면증 개선 효과 탐색 연구"
SITE = "가상 벨수면의원 수면의학과"
PI = "김하늘"
PHONE = "02-1234-5678"
EMAIL = "serene@example.org"


def protocol_blocks(version="1.2", visits="3회 방문", age="만 20~65세", total="총 60명",
                    amount="30,000 원", duration="60~90분"):
    return [
        ("p", "연구계획서"),
        ("p", "Version No: %s" % version),
        ("t", [["연구제목", "(국문) %s" % TITLE],
               ["연구책임자", "%s 교수 (%s)" % (PI, SITE)],
               ["연구 실시기관", SITE],
               ["연구 대상자 수", "%s (중재군 30명, 대조군 30명)" % total],
               ["연구 기간", "IRB 승인일로부터 12개월"],
               ["검사/방문일정", "%s (기저·4주·8주)" % visits],
               ["주요 선정기준", "G1: %s 성인 불면증(ISI 15점 이상), G2: %s 정상수면 대조군" % (age, age)]]),
        ("p", "연구책임자: %s, 직위: 교수 (%s)" % (PI, SITE)),
        ("p", "연구 관련 문의 전화: %s / 이메일: %s" % (PHONE, EMAIL)),
        ("p", "대상자는 %s 성인이며, 총 참여 기간은 8주이다." % age),
        ("p", "각 방문의 검사 소요시간은 약 %s이며, 방문 사이에 수면일기를 작성한다." % duration),
        ("p", "평가 항목: 불면증심각도척도, 수면다원검사, 심박변이도검사를 시행한다."),
        ("p", "경제적 보상: 방문당 교통비 실비 %s을 지급한다." % amount),
        ("p", "연구 관련 기록은 연구 종료 시점부터 3년간 보관 후 파기한다."),
    ]


def consent_blocks(version="1.2", visits="3회 방문", age="만 20~65세", total="총 60명",
                   amount="30,000 원", duration="60~90분"):
    return [
        ("p", "연구대상자 설명문 및 동의서"),
        ("p", "Version No: %s" % version),
        ("p", "연구제목: %s" % TITLE),
        ("p", "연구책임자: %s 교수 (%s)" % (PI, SITE)),
        ("p", "귀하는 본 연구에 참여할 것을 권유 받았습니다. 참여 여부는 전적으로 자발적인 결정에 의한 것입니다."),
        ("p", "본 연구에는 %s 성인 %s이 참여할 예정입니다(중재군 30명, 대조군 30명)." % (age, total)),
        ("p", "귀하의 연구 참여 기간은 8주이며, %s하시게 됩니다." % visits),
        ("p", "1회 방문의 검사 소요시간은 약 %s입니다." % duration),
        ("p", "검사 항목은 불면증심각도척도, 수면다원검사, 심박변이도검사입니다."),
        ("p", "연구 참여 보상으로 방문당 교통비 실비 %s이 지급됩니다." % amount),
        ("p", "연구 관련 기록은 연구 종료 시점부터 3년간 보관 후 파기됩니다."),
        ("t", [["연구책임자", "%s 교수 (%s) 전화: %s" % (PI, SITE, PHONE)],
               ["연구대상자 권리 문의", "기관생명윤리위원회 이메일: %s" % EMAIL]]),
    ]


def assent_blocks(version="1.2"):
    return [
        ("p", "연구대상자(아동) 승낙서"),
        ("p", "Version No: %s" % version),
        ("p", "연구제목: %s" % TITLE),
        ("p", "이 연구는 잠을 잘 자게 도와주는 소리를 알아보는 연구예요."),
        ("p", "검사하러 3회 방문하게 돼요. 한 번에 60~90분 정도 걸려요."),
        ("p", "참여할지 안 할지는 여러분이 직접 결정할 수 있어요."),
    ]


def crf_blocks(version="1.2", age="만 20~65세", extra_form=None):
    blocks = [
        ("p", "증례기록서 (Case Report Form)"),
        ("t", [["연구제목", TITLE],
               ["Version", version],
               ["연구책임자", "%s 교수" % PI],
               ["연구 실시기관", SITE]]),
        ("p", "1. 선정·제외기준 확인"),
        ("t", [["1", "%s 성인" % age, "예 / 아니오"],
               ["2", "ISI 15점 이상 (G1)", "예 / 아니오"],
               ["3", "G2 정상수면 대조군", "예 / 아니오"]]),
        ("p", "2. 회기별 방문 기록 (Visit Log)"),
        ("t", [["회기", "방문일자", "소요시간 (분)", "검사자"],
               ["Visit 1", "", "", ""],
               ["Visit 2", "", "", ""],
               ["Visit 3", "", "", ""]]),
        ("p", "3. 평가 기록"),
        ("p", "불면증심각도척도"),
        ("p", "수면다원검사"),
        ("p", "심박변이도검사"),
    ]
    if extra_form:
        blocks.append(("p", extra_form))
        blocks.append(("t", [["항목", "점수"], ["총점", ""]]))
    return blocks


def ad_blocks(visits="3회 방문", age="만 20~65세", total="총 60명", amount="30,000 원",
              duration="60~90분", phone=PHONE):
    return [
        ("p", "연구대상자 모집 광고안"),
        ("p", "임상시험명: %s" % TITLE),
        ("p", "모집 대상: %s 성인, %s (중재군 30명, 대조군 30명)" % (age, total)),
        ("p", "참여 방법: %s (기저·4주·8주), 1회 방문 소요시간 약 %s" % (visits, duration)),
        ("p", "참여 조건 G1: 불면증(ISI 15점 이상) / G2: 정상수면 대조군"),
        ("p", "보상: 방문당 교통비 실비 %s 지급" % amount),
        ("p", "참여문의: %s 연구간호사 / 연락처: %s" % (SITE, phone)),
    ]


def build():
    packets = {}

    packets["정합_패킷"] = [
        ("연구계획서_SERENE_v1.2_2026-09-01", protocol_blocks()),
        ("ICF_성인용_SERENE_v1.2_2026-09-01", consent_blocks()),
        ("CRF_통합_SERENE_v1.2_2026-09-01", crf_blocks()),
        ("모집공고안_SERENE_v1.2_2026-09-01", ad_blocks()),
    ]

    packets["불일치_패킷"] = [
        # 프로토콜은 그대로 두고 부속문서만 어긋뜨린다.
        ("연구계획서_SERENE_v1.2_2026-09-01", protocol_blocks()),
        # ① 보상 금액만 다름 (30,000 → 50,000)
        ("ICF_성인용_SERENE_v1.2_2026-09-01", consent_blocks(amount="50,000 원")),
        # ② Assent 만 구버전 (v1.1 / 2026-08-01)
        ("Assent_SERENE_v1.1_2026-08-01", assent_blocks(version="1.1")),
        # ③ 연령만 1세 차 (65 → 64), ④ CRF 에만 있는 평가 폼
        ("CRF_통합_SERENE_v1.2_2026-09-01", crf_blocks(age="만 20~64세", extra_form="우울증선별검사")),
        # ⑤ 방문 횟수만 다름 (3회 → 5회)
        ("모집공고안_SERENE_v1.2_2026-09-01", ad_blocks(visits="5회 방문")),
    ]

    # 개정 축 시연 — 계획서만 3회 → 5회로 바뀌고 동의서·공고는 그대로다.
    packets["개정_이전_패킷"] = [
        ("연구계획서_SERENE_v1.1_2026-08-01", protocol_blocks(version="1.1")),
        ("ICF_성인용_SERENE_v1.1_2026-08-01", consent_blocks(version="1.1")),
        ("모집공고안_SERENE_v1.1_2026-08-01", ad_blocks()),
    ]
    packets["개정_이후_패킷"] = [
        ("연구계획서_SERENE_v1.2_2026-09-01", protocol_blocks(visits="5회 방문")),
        ("ICF_성인용_SERENE_v1.2_2026-09-01", consent_blocks()),
        ("모집공고안_SERENE_v1.2_2026-09-01", ad_blocks()),
    ]

    packets["판정불가_패킷"] = [
        ("연구계획서_SERENE_v1.2_2026-09-01", protocol_blocks()),
        ("ICF_성인용_SERENE_v1.2_2026-09-01", consent_blocks()),
    ]

    for packet, documents in packets.items():
        md_dir = os.path.join(HERE, packet)
        docx_dir = os.path.join(HERE, "docx", packet)
        for directory in (md_dir, docx_dir):
            if os.path.isdir(directory):
                shutil.rmtree(directory)
            os.makedirs(directory)
        for name, blocks in documents:
            with open(os.path.join(md_dir, name + ".md"), "w", encoding="utf-8") as handle:
                handle.write(blocks_to_markdown(blocks))
            write_docx(os.path.join(docx_dir, name + ".docx"), blocks)

    # 판정불가 패킷에는 읽지 못하는 .hwp 를 끼워 넣는다 (조용히 무시되지 않는지 보기 위해).
    for directory in (os.path.join(HERE, "판정불가_패킷"), os.path.join(HERE, "docx", "판정불가_패킷")):
        with open(os.path.join(directory, "동의서_보호자용_SERENE_v1.2.hwp"), "w", encoding="utf-8") as handle:
            handle.write("(합성 예제) 한글 파일 자리표시자 — irbpack 은 .hwp 를 읽지 않고 '읽지 못한 문서'로 셉니다.\n")

    print("예제 패킷을 다시 만들었습니다: %s" % ", ".join(sorted(packets)))
    return 0


if __name__ == "__main__":
    sys.exit(build())
