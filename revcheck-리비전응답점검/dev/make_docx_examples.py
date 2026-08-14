"""examples/docx/ 의 세 .docx 를 다시 만든다 (개발용, 배포물 아님).

왜 필요한가: 실무 원고는 거의 전부 .docx 이고, .docx 에서만 생기는 오탐 요인이
따로 있다 — 워드가 문장을 ``<w:r>`` 여러 개로 쪼개 저장하고, 곧은 따옴표를
굽은 따옴표로 바꾸고, 변경내용 추적을 켜 둔 채 저장한다. 그래서 examples/clean/
과 **내용이 같은** 워드풍 3종 세트를 만들어 두고, 여기서도 치명이 0건인지 본다.

    python3 dev/make_docx_examples.py

만들어지는 것: examples/docx/제출본.docx / 개정본.docx / 응답서.docx
(개정본에는 변경내용 추적 흔적을 일부러 남긴다 — 리포트 첫머리 배너 확인용)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from docx_fixture import p, p_tracked, tbl, write_docx  # noqa: E402
from revcheck.docio import read_document  # noqa: E402

CLEAN = ROOT / "examples" / "clean"
OUT = ROOT / "examples" / "docx"

WORD_SPLIT = 7  # 워드가 문장을 쪼개는 정도


def curly(text: str) -> str:
    """곧은 따옴표를 워드처럼 굽은 따옴표로 바꾼다."""
    out = []
    opening = True
    for ch in text:
        if ch == '"':
            out.append("“" if opening else "”")
            opening = not opening
        else:
            out.append(ch)
    return "".join(out)


def blocks_for(doc, tracked_against=None):
    """Document → docx 블록 목록. 달라진 문단은 변경내용 추적으로 표시한다."""
    old_texts = [para.text for para in tracked_against.paras] if tracked_against else []
    old_set = set(old_texts)
    blocks = []
    for para in doc.paras:
        if para.kind == "heading":
            blocks.append(p(para.text, style="Heading1"))
            continue
        if tracked_against is None or para.text in old_set:
            blocks.append(p(para.text, split=WORD_SPLIT))
            continue
        prefix = next(
            (old for old in old_texts if old and para.text.startswith(old)), None
        )
        if prefix:
            blocks.append(p_tracked(before=prefix, inserted=para.text[len(prefix):]))
        else:
            blocks.append(p_tracked(inserted=para.text))
    return blocks


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    old_doc = read_document(CLEAN / "제출본.md", "제출본")
    new_doc = read_document(CLEAN / "개정본.md", "개정본")
    resp_doc = read_document(CLEAN / "응답서.md", "응답서")

    write_docx(OUT / "제출본.docx", blocks_for(old_doc))

    new_blocks = blocks_for(new_doc, tracked_against=old_doc)
    # 개정본에는 새 Table 2 도 넣는다 — .docx 표 셀 읽기를 보여 주기 위해서다.
    new_blocks.append(p("Table 2. Completers by arm"))
    new_blocks.append(
        tbl([["Arm", "Randomised", "Completed"], ["Active", "42", "40"], ["Sham", "42", "38"]])
    )
    write_docx(OUT / "개정본.docx", new_blocks)

    resp_blocks = []
    for para in resp_doc.paras:
        text = curly(para.text).replace("lines 36-38", "lines 36–38")
        resp_blocks.append(p(text, split=WORD_SPLIT))
    write_docx(OUT / "응답서.docx", resp_blocks)

    for name in ("제출본.docx", "개정본.docx", "응답서.docx"):
        print(f"  {OUT / name}  ({(OUT / name).stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
