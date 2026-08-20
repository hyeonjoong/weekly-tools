"""파이프라인 — 원고 읽기 → 숫자 추출 → 번들 색인 → 판정 → 커버리지 계산.

종료 코드 결정도 여기서 합니다. **판정불가(3)가 치명(1)보다 우선**입니다.
대조율이 낮은 상태에서 낸 치명은 신뢰할 수 없기 때문입니다.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .bundle import Bundle
from .judge import (GRADE_CRITICAL, GRADE_INFO, GRADE_WARN, Judgement,
                    judge_all)
from .manuscript import Manuscript, SECTION_LABEL
from .match import NumberIndex, needed_decimals
from .numbers import Number, extract_numbers

EXIT_OK = 0
EXIT_CRITICAL = 1
EXIT_WARN = 2
EXIT_UNDECIDABLE = 3

DEFAULT_SECTIONS = ("abstract", "results", "tables", "captions")

SECTION_ALIASES = {
    "abstract": "abstract", "초록": "abstract", "요약": "abstract",
    "results": "results", "result": "results", "결과": "results",
    "tables": "tables", "table": "tables", "표": "tables",
    "captions": "captions", "caption": "captions", "캡션": "captions",
    "methods": "methods", "method": "methods", "방법": "methods",
    "introduction": "introduction", "intro": "introduction", "서론": "introduction",
    "discussion": "discussion", "고찰": "discussion", "결론": "discussion",
    "references": "references", "참고문헌": "references",
    "other": "other", "기타": "other",
}


class SectionError(Exception):
    """`--sections` 값이 잘못됐을 때."""


def parse_sections(spec: str) -> List[str]:
    if not spec or not spec.strip():
        return list(DEFAULT_SECTIONS)
    out: List[str] = []
    for token in spec.replace(" ", "").split(","):
        if not token:
            continue
        low = token.lower()
        if low in ("all", "전체"):
            return ["abstract", "results", "tables", "captions", "methods",
                    "introduction", "discussion", "references", "other"]
        if low not in SECTION_ALIASES:
            raise SectionError(
                "알 수 없는 절 이름: %s (쓸 수 있는 값: %s)"
                % (token, ", ".join(sorted(set(SECTION_ALIASES.values())))))
        name = SECTION_ALIASES[low]
        if name not in out:
            out.append(name)
    return out or list(DEFAULT_SECTIONS)


@dataclass
class Coverage:
    extracted: int = 0
    compared: int = 0
    skipped: int = 0
    skip_counts: Dict[str, int] = field(default_factory=dict)
    by_section: Dict[str, int] = field(default_factory=dict)
    matched: int = 0
    unmatched: int = 0
    off_section: int = 0

    @property
    def unmatched_rate(self) -> float:
        if self.compared <= 0:
            return 0.0
        return 100.0 * self.unmatched / self.compared


@dataclass
class Analysis:
    manuscript: Manuscript
    current: Bundle
    previous: Optional[Bundle]
    sections: List[str]
    numbers: List[Number]
    judgements: List[Judgement]
    coverage: Coverage
    exit_code: int
    undecidable: Optional[str] = None
    max_unmatched: float = 30.0
    min_comparable: int = 5
    warnings: List[str] = field(default_factory=list)

    @property
    def criticals(self) -> List[Judgement]:
        return [j for j in self.judgements if j.grade == GRADE_CRITICAL]

    @property
    def warns(self) -> List[Judgement]:
        return [j for j in self.judgements if j.grade == GRADE_WARN]

    @property
    def infos(self) -> List[Judgement]:
        return [j for j in self.judgements if j.grade == GRADE_INFO]


def analyze(manuscript: Manuscript, current: Bundle, previous: Optional[Bundle],
            *, sections: List[str], max_unmatched: float = 30.0,
            min_comparable: int = 5, chance_matches: int = 12) -> Analysis:
    all_numbers: List[Number] = []
    off_section = 0
    for block in manuscript.blocks:
        if block.kind == "heading":
            continue
        found = extract_numbers(block)
        if block.target_key in sections:
            all_numbers.extend(found)
        else:
            off_section += len(found)

    coverage = Coverage(extracted=len(all_numbers), off_section=off_section)
    comparable: List[Number] = []
    for number in all_numbers:
        if number.skip:
            coverage.skipped += 1
            coverage.skip_counts[number.skip] = \
                coverage.skip_counts.get(number.skip, 0) + 1
        else:
            comparable.append(number)
            key = number.target_key
            coverage.by_section[key] = coverage.by_section.get(key, 0) + 1
    coverage.compared = len(comparable)

    decimals = needed_decimals(n.decimals for n in comparable) or {0, 1, 2, 3}
    current_index = NumberIndex(current.cells, decimals)
    previous_index = (NumberIndex(previous.cells, decimals)
                      if previous is not None else None)

    judgements = judge_all(comparable, current_index, previous_index,
                           chance_matches=chance_matches)
    coverage.matched = sum(1 for j in judgements if j.grade != GRADE_CRITICAL)
    coverage.unmatched = sum(1 for j in judgements if j.grade == GRADE_CRITICAL)

    warnings: List[str] = list(manuscript.notes)
    if previous is None:
        warnings.append(
            "`--previous` 미지정 — **구버전 잔존 검사는 수행되지 않았습니다.** "
            "재분석 전 출력 폴더를 함께 지정하면 '옛 값이 원고에 남아 있는지'를 잡습니다.")
    if current.truncated:
        warnings.append("번들을 **끝까지 읽지 못했습니다** — 아래 '읽지 못한 파일' "
                        "목록의 사유를 확인하세요(상한 초과·손상·중첩 초과 등). "
                        "거기 있던 값은 '출처 없음'으로 잡힐 수 있습니다.")
    if previous is not None and previous.truncated:
        warnings.append("이전 번들도 끝까지 읽지 못했습니다(같은 목록 참조).")

    undecidable = _undecidable_reason(coverage, current, manuscript,
                                      max_unmatched, min_comparable)
    exit_code = _exit_code(coverage, undecidable)
    return Analysis(manuscript=manuscript, current=current, previous=previous,
                    sections=sections, numbers=all_numbers,
                    judgements=judgements, coverage=coverage,
                    exit_code=exit_code, undecidable=undecidable,
                    max_unmatched=max_unmatched, min_comparable=min_comparable,
                    warnings=warnings)


def _undecidable_reason(coverage: Coverage, current: Bundle,
                        manuscript: Manuscript, max_unmatched: float,
                        min_comparable: int) -> Optional[str]:
    if not manuscript.blocks:
        return "원고에서 문단을 하나도 읽지 못했습니다(형식·인코딩을 확인하세요)."
    if current.cell_count == 0:
        return ("출력 번들에서 수치 셀을 하나도 찾지 못했습니다 — 폴더가 비었거나 "
                "읽을 수 있는 형식(.csv/.tsv/.json/.xlsx/.md/.txt)이 없습니다.")
    if coverage.compared < min_comparable:
        return ("대조 가능한 숫자가 %d개뿐입니다(최소 %d개). 절 분류가 안 됐거나 "
                "대조 대상 절(%s)에 숫자가 거의 없습니다."
                % (coverage.compared, min_comparable,
                   ", ".join(SECTION_LABEL.get(s, s) for s in ("abstract", "results"))))
    if coverage.unmatched_rate > max_unmatched:
        return ("미매칭율이 %.1f%% 로 임계(%.0f%%)를 넘습니다 — 이 번들이 이 원고의 "
                "분석 결과가 아닐 가능성이 높습니다(폴더를 잘못 지정한 경우가 가장 흔합니다). "
                "치명 목록을 쏟아내지 않고 멈춥니다."
                % (coverage.unmatched_rate, max_unmatched))
    return None


def _exit_code(coverage: Coverage, undecidable: Optional[str]) -> int:
    if undecidable:
        return EXIT_UNDECIDABLE
    if coverage.unmatched > 0:
        return EXIT_CRITICAL
    return EXIT_OK


def finalize_exit(analysis: Analysis) -> int:
    """경고만 있는 경우까지 반영한 최종 종료 코드."""
    if analysis.exit_code != EXIT_OK:
        return analysis.exit_code
    if analysis.warns:
        return EXIT_WARN
    return EXIT_OK


def percent(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else 0.0


