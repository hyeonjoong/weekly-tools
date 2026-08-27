"""커버리지 자백.

**절대 버리면 안 되는 것 #1.** 다 못 본 채로 "치명 0건"을 말하면 이 툴은
없느니만 못합니다. 그래서 자백 블록을 만들 수 없으면 리포트를 아예
출력하지 않습니다(`CoverageError`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

# 검사 가능 비율이 이보다 낮으면 판정불가(종료코드 3).
MIN_SCAN_RATIO = 0.80


class CoverageError(Exception):
    """자백 블록을 만들 수 없음 — 리포트를 내면 안 됩니다."""


@dataclass
class Coverage:
    """무엇을 얼마나 봤는지에 대한 기록."""

    files_given: int = 0
    files_read: int = 0
    sheets: int = 0
    columns: int = 0
    cells: int = 0
    cells_skipped: int = 0
    unreadable_sheets: int = 0
    free_text_columns: List[Tuple[str, str, str]] = field(default_factory=list)  # (표, 열, 사유)
    non_free_text_columns: List[Tuple[str, str, str]] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (대상, 사유)
    not_computed: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def scan_ratio(self) -> float:
        total = self.cells + self.cells_skipped
        return 1.0 if total == 0 else self.cells / total

    @property
    def file_ratio(self) -> float:
        return 1.0 if self.files_given == 0 else self.files_read / self.files_given

    @property
    def undetermined(self) -> bool:
        """판정불가(종료코드 3) 여부.

        **읽지 못한 파일이 하나라도 있으면 판정불가입니다.** 비율로 봐주면
        5개 중 1개가 암호 워크북이어도 "치명 0건"이 나옵니다 — 그 1개가
        바로 명단 파일일 수 있습니다.
        """
        return (
            self.files_read == 0
            or self.files_read < self.files_given
            or self.unreadable_sheets > 0
            or self.scan_ratio < MIN_SCAN_RATIO
        )

    def validate(self) -> None:
        """자백 블록을 만들 수 있는 상태인지 확인합니다."""
        if self.files_given <= 0:
            raise CoverageError("입력 파일이 없어 커버리지 자백을 만들 수 없습니다")
        if self.files_read <= 0:
            raise CoverageError("읽은 파일이 하나도 없어 커버리지 자백을 만들 수 없습니다")
        if self.columns <= 0:
            raise CoverageError("검사한 열이 없어 커버리지 자백을 만들 수 없습니다")

    def headline(self) -> str:
        parts = [f"입력 {self.files_given}개 파일"]
        if self.sheets:
            parts.append(f"{self.sheets}개 시트")
        parts.append(f"{self.columns}개 열")
        parts.append(f"{self.cells:,} 셀 검사")
        return " / ".join(parts)

    def block(self) -> List[str]:
        """자백 블록의 각 줄. `validate()` 를 통과해야 호출됩니다."""
        self.validate()
        lines = [
            f"  검사: {self.files_read}/{self.files_given}파일 / {self.sheets}시트 / "
            f"{self.columns}열 / {self.cells:,}셀 (읽은 파일 안 검사율 {self.scan_ratio:.1%})"
        ]
        if self.unreadable_sheets:
            lines.append(
                f"  ** 시트 {self.unreadable_sheets}개를 읽지 못했습니다(비어 있는 것과 다릅니다) — "
                "그 시트에 대해서는 아무 말도 할 수 없습니다(판정불가). **"
            )
        if self.files_read < self.files_given:
            lines.append(
                f"  ** {self.files_given - self.files_read}개 파일을 아예 읽지 못했습니다 — "
                "그 파일들에 대해서는 아무 말도 할 수 없습니다(판정불가). **"
            )
        if self.free_text_columns:
            names = ", ".join(f"{col}" for _, col, _ in self.free_text_columns)
            lines.append(f"  자유텍스트로 판정한 열: {names} ({len(self.free_text_columns)}개) — 전 행 스캔함")
        else:
            lines.append("  자유텍스트로 판정한 열: 없음 — 자유기술 칸이 있는데도 0개라면 판정이 좁은 것입니다")
        if self.skipped:
            lines.append(f"  건너뜀: {len(self.skipped)}개")
            for target, reason in self.skipped[:20]:
                lines.append(f"    · {target} — {reason}")
            if len(self.skipped) > 20:
                lines.append(f"    · … 외 {len(self.skipped) - 20}개")
        else:
            lines.append("  건너뜀: 0개")
        if self.not_computed:
            lines.append("  계산 안 함:")
            for item in self.not_computed:
                lines.append(f"    · {item}")
        else:
            lines.append("  계산 안 함: 없음")
        for note in self.notes:
            lines.append(f"  · {note}")
        return lines
