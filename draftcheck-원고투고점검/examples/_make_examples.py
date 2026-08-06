#!/usr/bin/env python3
"""번들 예제를 만드는 스크립트 (예제가 어떻게 만들어졌는지 그 자체가 문서다).

``manuscript_clean.md`` 하나만 손으로 쓰고, 나머지 셋은 여기서 파생시킨다.

* ``manuscript_flawed.md``   — 아래 DEFECTS 목록의 결함을 **하나씩 의도적으로** 심은 판
* ``manuscript_clean.docx``  — 같은 내용을 최소 OOXML 로 만든 Word 판(표는 진짜 ``w:tbl``)
* ``manuscript_flawed.docx`` — 결함본 + **추적 변경 삭제문**과 **EndNote 필드 인용**까지 심은 판

마지막 두 개는 파서를 진짜로 시험하기 위한 것이다. 추적 변경으로 지워진 문장에는
``[99]`` 인용이 들어 있고, EndNote 필드 코드 안에는 숫자가 잔뜩 들어 있다.
둘 중 하나라도 텍스트로 새어 나오면 인용 교차 대조가 통째로 틀리므로,
테스트가 "[99] 가 결과에 없을 것"을 직접 확인한다.

    python3 examples/_make_examples.py

원고는 전부 **합성**이다. 실제 환자 데이터나 실제 원고가 아니다.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent

# ── 심는 결함 목록 ────────────────────────────────────────────────────────────
# (설명, 찾을 문자열, 바꿀 문자열) — 각 항목은 정확히 한 번만 적용되어야 한다.
DEFECTS = [
    (
        "D1 인용누락: 참고문헌은 26개인데 본문이 [27]을 인용",
        "so the confidence intervals around the secondary outcomes are wide [23].",
        "so the confidence intervals around the secondary outcomes are wide [23,27].",
    ),
    (
        "D2 미인용문헌: 26번 문헌이 본문 어디에서도 인용되지 않음",
        "Finally, the four-week horizon leaves the durability of the effect unknown [26].",
        "Finally, the four-week horizon leaves the durability of the effect unknown.",
    ),
    (
        "D3a 인용순서역전: 방법에서 [18]을 먼저 씀",
        "under a missing-at-random assumption [17].",
        "under a missing-at-random assumption [18].",
    ),
    (
        "D3b 인용순서역전: 고찰에서 [17]이 뒤늦게 처음 등장",
        "comparable with what has been reported for closed-loop acoustic stimulation [18]",
        "comparable with what has been reported for closed-loop acoustic stimulation [17]",
    ),
    (
        "D4 그림 미언급: Figure 3 캡션은 있으나 본문에서 사라짐",
        "The night-by-night trajectory of high-frequency HRV is shown in Figure 3, which "
        "separates the guidance hour from the remainder of the night.",
        "The trajectory of high-frequency HRV across the night separated the guidance hour "
        "from the remainder of the night.",
    ),
    (
        "D5 효과크기·CI 없는 p 값",
        "All secondary outcomes are summarised in Table 2.",
        "Sleep efficiency also differed between the arms (p = 0.041). "
        "All secondary outcomes are summarised in Table 2.",
    ),
    (
        "D6a 표 번호 건너뜀: 본문이 Table 3을 가리킴",
        "All secondary outcomes are summarised in Table 2.",
        "All secondary outcomes are summarised in Table 3.",
    ),
    (
        "D6b 표 번호 건너뜀: 캡션이 Table 1 → Table 3 (2번이 없음)",
        "**Table 2.** Secondary outcomes at week 4, adjusted for baseline.",
        "**Table 3.** Secondary outcomes at week 4, adjusted for baseline.",
    ),
    (
        "D7a 숫자 불일치: 초록만 48명 (본문·표는 45명)",
        "parallel-group trial (SERENE), 45 adults with chronic insomnia disorder",
        "parallel-group trial (SERENE), 48 adults with chronic insomnia disorder",
    ),
    (
        "D7b 숫자 불일치: 초록 결과의 표본수도 48로",
        "**Results.** Of 45 participants, 43 completed the week-4 assessment.",
        "**Results.** Of 48 participants, 46 completed the week-4 assessment.",
    ),
    (
        "D8 p = 0.000 (정확히 0인 p 값은 존재할 수 없음)",
        "ISI scores improved by 3.2 points more in the intervention arm "
        "(95% CI, 1.4 to 5.0; Hedges g = 0.58; p = 0.004)",
        "ISI scores improved by 3.2 points more in the intervention arm "
        "(95% CI, 1.4 to 5.0; Hedges g = 0.58; p = 0.000)",
    ),
    (
        "D9 p 값 표기 혼재: 앞자리 0을 뺀 .017 이 섞임",
        "WASO fell by 14.1 min more (95% CI, 2.6 to 25.6; Hedges g = 0.44; p = 0.017)",
        "WASO fell by 14.1 min more (95% CI, 2.6 to 25.6; Hedges g = 0.44; p = .017)",
    ),
    (
        "D10 임계값만 보고: 초록의 주요 결과가 p < 0.05",
        "than in the sham arm (95% CI, 4.1 to 20.7; Hedges g = 0.61; p = 0.003)",
        "than in the sham arm (95% CI, 4.1 to 20.7; Hedges g = 0.61; p < 0.05)",
    ),
    (
        "D11 약어 정의 전 사용: 서론에서 ISI를 먼저 씀 (정의는 방법에)",
        "guidelines place behavioural treatment first [3].",
        "guidelines place behavioural treatment first [3]. Most trials enrol patients "
        "with an ISI score above the clinical threshold.",
    ),
    (
        "D12 초록 단어수 초과: 결론 문단을 늘려 250단어 한도를 넘김",
        "supporting the proposed respiratory–parasympathetic pathway.",
        "supporting the proposed respiratory–parasympathetic pathway. The effect was "
        "present in both men and women and did not depend on baseline insomnia severity, "
        "which suggests that the mechanism is not limited to the most severely affected "
        "patients. Because the device requires no contact with the body and no consumable "
        "parts, the intervention is straightforward to deliver at home over long periods, "
        "and a pragmatic multicentre trial with a twelve-month follow-up is the natural "
        "next step for confirming durability and for establishing whether the change in "
        "slow-wave sleep translates into daytime benefit.",
    ),
]


def build_flawed(clean_text: str) -> str:
    text = clean_text
    for label, old, new in DEFECTS:
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                f"결함 '{label}' 의 대상 문자열이 {count}번 나타납니다(1번이어야 함).\n  {old[:70]}…"
            )
        text = text.replace(old, new, 1)
    return text


# ── 최소 OOXML(.docx) 작성기 ─────────────────────────────────────────────────

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

# 추적 변경으로 '삭제된' 문장. 최종본에는 없는 글자이므로 draftcheck 는 이걸 읽으면 안 된다.
DELETED_RUN = (
    '<w:del w:id="901" w:author="co-author" w:date="2026-08-01T09:00:00Z">'
    '<w:r><w:delText xml:space="preserve"> An earlier draft cited [99] here and '
    "mentioned Figure 9.</w:delText></w:r></w:del>"
)

# EndNote 필드 인용. 화면에 보이는 것은 결과 텍스트 '[2]' 뿐이고,
# instrText 안의 숫자들은 사람이 보는 글자가 아니다.
FIELD_CITATION = (
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
    '<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE '
    "&lt;EndNote&gt;&lt;Cite&gt;&lt;Author&gt;Baglioni&lt;/Author&gt;"
    "&lt;Year&gt;2021&lt;/Year&gt;&lt;RecNum&gt;77&lt;/RecNum&gt;"
    "&lt;DisplayText&gt;[2]&lt;/DisplayText&gt;&lt;record&gt;"
    "&lt;rec-number&gt;77&lt;/rec-number&gt;&lt;ref-type name=&quot;Journal Article&quot;&gt;"
    "17&lt;/ref-type&gt;&lt;/record&gt;&lt;/Cite&gt;&lt;/EndNote&gt; </w:instrText></w:r>"
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    "<w:r><w:t>[2]</w:t></w:r>"
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
)


def _run(text: str) -> str:
    if not text:
        return ""
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _plain_text(md_line: str) -> str:
    """마크다운 표시를 벗겨 워드에서 보일 글자만 남긴다."""
    line = md_line
    if line.lstrip().startswith("#"):
        line = line.lstrip().lstrip("#").strip()
    return line.replace("**", "")


def _paragraph(md_line: str, inject: bool) -> str:
    """마크다운 한 줄 → ``w:p`` 하나. 결함본에는 필드 인용과 추적 삭제를 심는다."""
    text = _plain_text(md_line)
    if inject and "Persistent insomnia predicts incident depression" in text and "[2]" in text:
        before, _, after = text.partition("[2]")
        runs = _run(before) + FIELD_CITATION + _run(after)
    else:
        runs = _run(text)
    if inject and text.startswith("On the week-4 PSG night"):
        runs += DELETED_RUN
    return f"<w:p>{runs}</w:p>"


def _table(rows: list) -> str:
    out = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>']
    for cells in rows:
        out.append("<w:tr>")
        for cell in cells:
            out.append(
                '<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>'
                f"<w:p>{_run(cell)}</w:p></w:tc>"
            )
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def _is_separator_row(line: str) -> bool:
    return set(line.replace("|", "").replace(" ", "")) <= {"-", ":"} and "-" in line


def md_to_docx(md_text: str, out_path: Path, inject: bool = False) -> None:
    body: list = []
    pending_rows: list = []

    def flush() -> None:
        if pending_rows:
            body.append(_table(list(pending_rows)))
            pending_rows.clear()

    for line in md_text.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("|"):
            if _is_separator_row(line):
                continue
            cells = [c.strip().replace("**", "") for c in line.strip().strip("|").split("|")]
            pending_rows.append(cells)
            continue
        flush()
        body.append(_paragraph(line, inject))
    flush()

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>' + "".join(body) + "</w:body></w:document>"
    )
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", document)


def main() -> int:
    clean_md = HERE / "manuscript_clean.md"
    clean_text = clean_md.read_text(encoding="utf-8")
    flawed_text = build_flawed(clean_text)
    (HERE / "manuscript_flawed.md").write_text(flawed_text, encoding="utf-8")
    md_to_docx(clean_text, HERE / "manuscript_clean.docx", inject=False)
    md_to_docx(flawed_text, HERE / "manuscript_flawed.docx", inject=True)
    print(f"만들었습니다: manuscript_flawed.md, manuscript_clean.docx, manuscript_flawed.docx")
    print(f"심은 결함 {len(DEFECTS)}건:")
    for label, _, _ in DEFECTS:
        print(f"  · {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
