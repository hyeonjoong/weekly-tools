"""``--subtest`` 명세 파싱과 목표음소 규칙 채점.

**규칙을 코드에 박지 않는다.** 검사마다 무엇을 맞히면 정답인지가 다르고
(자음검사는 가운데 자음만, 모음검사는 첫 음절 모음만), 그걸 툴이 추론하는
순간 조용히 거짓말을 하게 된다. 그래서 사용자가 문자열로 선언한다.

문법::

    검사이름:목표=RULE[,문항=N][,만점=M][,단위=단어|음소][,시점=사전,사후]

RULE 은 다음 중 하나::

    전체              제시 문자열과 응답 문자열이 통째로 같아야 1점
    <k>음절초성        k 번째 글자의 초성만 비교 (예: 2음절초성)
    <k>음절중성        k 번째 글자의 중성만 비교
    <k>음절종성        k 번째 글자의 종성만 비교 (받침 없음도 하나의 값)
    초중종             1번째 글자의 초·중·종 각 1점 (만점 3)
    <k>음절초중종      k 번째 글자의 초·중·종 각 1점
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .hangul import NO_JONG, jamo_at, normalize

#: 규칙 채점이 불가능할 때. 0점도 1점도 아니다.
UNDECIDABLE = "대조불가"

_NAME_RE = re.compile(r"^[^:,=]{1,40}$")
_SYL_RE = re.compile(r"^(?:(\d+)음절)?(초성|중성|종성|초중종)$")


class SpecError(ValueError):
    """``--subtest`` 문자열이 잘못됐을 때. CLI 가 exit 2 로 바꾼다."""


class Rule:
    """목표음소 규칙 하나. 한 문항을 몇 점으로 채점할지 안다."""

    def __init__(self, text: str):
        self.text = text
        self.kind: str
        self.syllable = 1
        self.position: Optional[str] = None
        if text == "전체":
            self.kind = "전체"
            self.max_points = 1
            return
        m = _SYL_RE.match(text)
        if not m:
            raise SpecError(
                "목표 규칙을 알아볼 수 없습니다: %r\n"
                "  쓸 수 있는 값: 전체 / 2음절초성 / 1음절중성 / 1음절종성 / 초중종"
                % (text,)
            )
        if m.group(1):
            self.syllable = int(m.group(1))
            if self.syllable < 1:
                raise SpecError("음절 번호는 1 이상이어야 합니다: %r" % (text,))
        if m.group(2) == "초중종":
            self.kind = "초중종"
            self.max_points = 3
        else:
            self.kind = "자모"
            self.position = m.group(2)
            self.max_points = 1

    # -- 채점 ---------------------------------------------------------
    def score(self, stimulus: str, response: str):
        """(점수, 근거) 를 돌려준다. 점수가 :data:`UNDECIDABLE` 이면 규칙 대조를 포기한 것."""
        stim = normalize(stimulus)
        resp = normalize(response)
        if not stim:
            return UNDECIDABLE, "제시 자극 공란"
        if not resp:
            return UNDECIDABLE, "응답 공란"
        if self.kind == "전체":
            return (1 if stim == resp else 0), "전체 문자열 비교"
        if self.kind == "자모":
            a = jamo_at(stim, self.syllable, self.position)
            b = jamo_at(resp, self.syllable, self.position)
            if a is None:
                return UNDECIDABLE, "제시 자극에서 %d음절 %s 를 뽑을 수 없음" % (
                    self.syllable, self.position)
            if b is None:
                return UNDECIDABLE, "응답에서 %d음절 %s 를 뽑을 수 없음" % (
                    self.syllable, self.position)
            return (1 if a == b else 0), "%s %s→%s" % (self.position, a, b)
        # 초중종
        got = 0
        parts = []
        for pos in ("초성", "중성", "종성"):
            a = jamo_at(stim, self.syllable, pos)
            b = jamo_at(resp, self.syllable, pos)
            if a is None:
                return UNDECIDABLE, "제시 자극에서 %d음절을 분해할 수 없음" % self.syllable
            if b is None:
                return UNDECIDABLE, "응답에서 %d음절을 분해할 수 없음" % self.syllable
            if a == b:
                got += 1
            else:
                parts.append("%s %s→%s" % (pos, a, b))
        return got, ("일치" if not parts else " · ".join(parts))

    def targets(self, stimulus: str, response: str) -> List[Tuple[str, str, str]]:
        """혼동 행렬용 ``(자모위치, 목표, 응답)`` 목록. 비교 불가면 빈 목록."""
        stim, resp = normalize(stimulus), normalize(response)
        if not stim or not resp or self.kind == "전체":
            return []
        positions = (self.position,) if self.kind == "자모" else ("초성", "중성", "종성")
        out = []
        for pos in positions:
            a = jamo_at(stim, self.syllable, pos)
            b = jamo_at(resp, self.syllable, pos)
            if a is None or b is None:
                return []
            out.append((pos, a, b))
        return out

    def __repr__(self) -> str:
        return "Rule(%r)" % (self.text,)


class SubtestSpec:
    """한 '패스'(= 블록 안에서 한 번 매긴 채점) 의 명세."""

    def __init__(self, name: str, rule: Rule, n_items: Optional[int],
                 max_points: int, unit: str, index: int = 0):
        self.name = name
        self.rule = rule
        self.n_items = n_items
        self.max_points = max_points
        self.unit = unit
        self.index = index

    @property
    def label(self) -> str:
        return "%s(%s)" % (self.name, self.unit)

    def __repr__(self) -> str:
        return "SubtestSpec(%r, %r, 문항=%r, 만점=%d, 단위=%r)" % (
            self.name, self.rule.text, self.n_items, self.max_points, self.unit)


def parse_subtest(text: str) -> SubtestSpec:
    """``"자음:목표=2음절초성,문항=18"`` 한 줄을 :class:`SubtestSpec` 으로."""
    if ":" not in text:
        raise SpecError(
            "--subtest 는 '이름:목표=규칙' 모양이어야 합니다: %r\n"
            "  예) --subtest \"자음:목표=2음절초성,문항=18\"" % (text,)
        )
    name, _, body = text.partition(":")
    name = name.strip()
    if not _NAME_RE.match(name):
        raise SpecError("검사 이름이 잘못됐습니다(빈 값·40자 초과·':' 포함 불가): %r" % (name,))
    fields = {}
    for chunk in body.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SpecError("'키=값' 이 아닌 항목이 있습니다: %r (전체: %r)" % (chunk, text))
        key, _, val = chunk.partition("=")
        key, val = key.strip(), val.strip()
        if key in fields:
            raise SpecError("같은 키가 두 번 나왔습니다: %r (전체: %r)" % (key, text))
        fields[key] = val
    unknown = set(fields) - {"목표", "문항", "만점", "단위"}
    if unknown:
        raise SpecError(
            "모르는 키: %s (쓸 수 있는 키: 목표·문항·만점·단위) — %r"
            % (", ".join(sorted(unknown)), text)
        )
    if "목표" not in fields:
        raise SpecError("'목표=' 가 없습니다: %r" % (text,))
    rule = Rule(fields["목표"])

    n_items = None
    if "문항" in fields:
        n_items = _positive_int(fields["문항"], "문항", text)

    max_points = rule.max_points
    if "만점" in fields:
        max_points = _positive_int(fields["만점"], "만점", text)
        if max_points != rule.max_points:
            raise SpecError(
                "만점=%d 인데 목표=%s 규칙의 만점은 %d 입니다 — 둘 중 하나가 틀렸습니다.\n"
                "  (툴은 만점을 추론하지 않습니다. 명시한 값이 규칙과 맞아야 합니다.)"
                % (max_points, rule.text, rule.max_points)
            )
    unit = fields.get("단위") or ("음소" if max_points > 1 else "단어")
    if unit not in ("단어", "음소"):
        raise SpecError("단위는 '단어' 또는 '음소' 여야 합니다: %r" % (fields["단위"],))
    return SubtestSpec(name, rule, n_items, max_points, unit)


def _positive_int(raw: str, key: str, whole: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise SpecError("%s= 값이 정수가 아닙니다: %r (전체: %r)" % (key, raw, whole))
    if value < 1:
        raise SpecError("%s= 값은 1 이상이어야 합니다: %r" % (key, raw))
    if value > 10_000:
        raise SpecError("%s= 값이 비상식적으로 큽니다: %r" % (key, raw))
    return value


def parse_all(texts: List[str]) -> List[SubtestSpec]:
    """``--subtest`` 여러 개. **같은 이름이 여러 번 나오면 세로로 쌓인 패스**로 본다."""
    specs = [parse_subtest(t) for t in texts]
    seen = {}
    for spec in specs:
        spec.index = seen.get(spec.name, 0)
        seen[spec.name] = spec.index + 1
    labels = [s.label for s in specs]
    dup = {x for x in labels if labels.count(x) > 1}
    if dup:
        raise SpecError(
            "같은 검사에 같은 단위가 두 번 선언됐습니다: %s\n"
            "  같은 블록을 두 번 채점한 경우라면 '단위=단어' / '단위=음소' 로 구분하세요."
            % ", ".join(sorted(dup))
        )
    return specs


def specs_by_name(specs: List[SubtestSpec]):
    """``{검사이름: [패스0, 패스1, ...]}`` (선언 순서 유지)."""
    out = {}
    for spec in specs:
        out.setdefault(spec.name, []).append(spec)
    return out
