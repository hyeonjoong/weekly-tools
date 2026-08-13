"""검사 규칙들이 공유하는 설정 한 덩어리."""

from __future__ import annotations

from dataclasses import dataclass, field

from .scales import ScaleRegistry

__all__ = ["Options"]


@dataclass
class Options:
    """실행 설정.

    ``k``  반올림 허용 배수. 1.0 이면 반올림/버림/올림을 모두 허용한다(기본).
           0.5 로 낮추면 반올림만 허용 — 지적이 늘고 오탐도 는다.
    ``alpha``  유의성 문구 검사에 쓰는 기준. 원고가 다른 기준을 쓰면 바꾼다.
    ``quote``  리포트에 원문 발췌를 넣을지. 끄면 줄번호·항목명만 남는다
               (원고를 남에게 보낼 때).
    """

    registry: ScaleRegistry = field(default_factory=ScaleRegistry)
    k: float = 1.0
    alpha: float = 0.05
    lang: str = "ko"
    quote: bool = True
    line_label: str = "줄"
    min_checked: int = 5
    strict_grimmer: bool = True
