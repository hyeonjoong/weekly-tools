"""경계 강제 장치 — 이 툴이 하지 않는 일을 코드로 막습니다.

1. 원고를 감지하면 아무 판정도 하지 않고 exit 2 (`draftcheck`·`numcheck`·`revcheck` 를 가리킴).
2. 표본수·검정력 재계산 함수가 없음 (tests 가 AST 로 확인).
3. 문서를 고치는 코드 경로가 없음 (수정본·정정본·답변서 생성 없음).
4. 규정 준수 판정 문구가 없음 (금지어 테스트).
5. "어느 값이 맞다"는 판정이 없음.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .model import Doc

_MS_HEADS = [
    (re.compile(r"^\s*(?:[0-9.]+\s*)?abstract\s*$", re.I), 2),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?introduction\s*$", re.I), 1),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?(?:materials\s+and\s+)?methods\s*$", re.I), 1),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?results\s*$", re.I), 1),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?discussion\s*$", re.I), 2),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?references\s*$", re.I), 1),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?(?:초록|국문\s*초록|영문\s*초록)\s*$"), 2),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?고찰\s*$"), 2),
    (re.compile(r"^\s*(?:[0-9.]+\s*)?(?:서론|결과|참고문헌)\s*$"), 1),
]
_MS_TOKENS = re.compile(r"\\(?:begin\{document\}|section\{|documentclass|cite\{|bibliography)")


def manuscript_reason(doc: Doc) -> Optional[str]:
    """원고로 보이면 사유 문자열, 아니면 None."""
    if doc.ext == ".tex":
        return ".tex 파일 — 논문 원고"
    if not doc.readable:
        return None
    score = 0
    hits: List[str] = []
    strong = False
    for p in doc.paras[:3000]:
        for rx, w in _MS_HEADS:
            if rx.match(p.text):
                score += w
                hits.append(p.text.strip())
                if w == 2:
                    strong = True
                break
    if _MS_TOKENS.search(doc.text[:20000]):
        return "LaTeX 명령이 있음 — 논문 원고"
    if strong and score >= 4:
        return "원고 구조 절 제목 감지 ({})".format(" / ".join(hits[:5]))
    return None


MANUSCRIPT_MESSAGE = (
    "[중단] {name}: {why}\n"
    "  이건 원고입니다. irbpack 은 원고를 읽지 않습니다 — draftcheck · numcheck · revcheck 를 쓰세요.\n"
    "  (문서 사이가 아니라 문서 하나의 자기 정합성은 그쪽 툴의 일입니다)")
