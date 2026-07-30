"""사전연구(pilot) CSV → 관측 효과크기 → 본연구 표본수.

**이 모듈이 존재하는 이유**: 사전연구에서 관측한 효과크기를 그대로 본연구 표본수
계산에 넣는 것은 흔하지만 위험한 실수다. 소표본에서 얻은 d는 우연히 큰 값이 나오기
쉬워(승자의 저주) 표본수가 체계적으로 과소해진다. 그래서 여기서는 항상

  ① 관측 d (Hedges g 보정 포함)  ② d의 **정확 신뢰구간**(비중심 t 역산)
  ③ 신뢰구간 하한으로 계산한 **보수적 표본수**

를 함께 보여준다. 프로토콜에는 ③ 또는 임상적으로 의미있는 값을 쓰는 것이 안전하다.

CSV 처리 원칙: 파일을 **진짜로 스트리밍**한다 — 앞 64KB만 읽어 인코딩·구분자를
판별한 뒤 파일 객체를 그대로 csv.reader에 넘기고, 통계는 Welford 온라인 알고리즘으로
누적한다. 따라서 파일 크기와 무관하게 메모리는 수십 MB 수준이다. 인코딩은
utf-8-sig → cp949(한국어 엑셀) → latin-1 순으로 시도하며, 구분자는 자동 감지한다.

**군 라벨은 앞뒤 공백만 제거한 원본 문자열로 집계**한다 (표시할 때만 60자로 자른다).
표시용으로 자른 문자열을 집계 키로 쓰면, 앞부분이 같은 긴 군 이름 두 개가 조용히
하나로 합쳐져 표본수가 틀린다 — 그래서 키와 표시를 분리했다. 표시 라벨이 겹치면
`#2`를 붙여 구분한다.
"""

from __future__ import annotations

import codecs
import csv
import math
import os
import re
import stat
from dataclasses import dataclass, field

from .distributions import nct_ncp_ci
from .effects import hedges_correction
from .validate import PowerPlanError

__all__ = ["GroupStats", "read_two_group", "read_paired", "effect_from_two_group",
           "effect_from_paired", "strip_unsafe", "MISSING_TOKENS", "MAX_FILE_BYTES"]

#: 결측으로 볼 문자열 (대소문자 무시, 앞뒤 공백 제거 후 비교)
MISSING_TOKENS = frozenset({"", "na", "n/a", "nan", "none", "null", ".", "-", "--",
                            "missing", "결측", "없음", "#n/a", "#null!", "?"})
#: 이보다 큰 파일은 실수로 지정한 것으로 보고 거절한다 (스트리밍이라 메모리 문제는 아님)
MAX_FILE_BYTES = 2 * 1024 ** 3
_ENCODINGS = ("utf-8-sig", "cp949", "latin-1")
_DELIMITERS = (",", "\t", ";", "|")
_MAX_GROUPS = 500
_MAX_ERROR_EXAMPLES = 5
_SNIFF_BYTES = 65536
#: csv 모듈의 필드 길이 상한. 자유기술 항목이 있는 임상 CSV는 기본값(128KB)을
#: 넘길 수 있으므로 조금 올려 잡되, 무한정 늘리지는 않는다.
_MAX_FIELD_BYTES = 1024 * 1024
#: "1,234,567" 형태의 천단위 구분자만 제거 대상으로 인정
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d*)?$")

#: 표시 전 제거할 문자 — 터미널 이스케이프·줄바꿈 위장·양방향 텍스트 조작·폭 0 문자.
#: 데이터 값이 화면이나 문서를 조작하지 못하게 하는 유일한 관문이다.
_CTRL = {c: None for c in range(32)}          # C0 제어문자 (ESC 포함)
_CTRL[127] = None                              # DEL
_CTRL.update({c: None for c in range(0x80, 0xA0)})        # C1 제어문자 (8비트 CSI 등)
_CTRL.update({c: None for c in (0x2028, 0x2029, 0xFEFF)})  # 줄/문단 구분자, BOM
_CTRL.update({c: None for c in range(0x200B, 0x2010)})     # 폭 0 문자 + LRM/RLM
_CTRL.update({c: None for c in range(0x202A, 0x2030)})     # 양방향 임베딩/오버라이드
_CTRL.update({c: None for c in range(0x2066, 0x206A)})     # 양방향 격리
#: 스프레드시트가 수식으로 해석하는 시작 문자 (CSV 수식 주입 방지)
_FORMULA_LEADERS = ("=", "+", "-", "@")


def strip_unsafe(text, limit: int = 200) -> str:
    """제어문자·양방향 조작·폭 0 문자만 제거한다 (수식 이스케이프는 하지 않음).

    명령줄처럼 ``-``로 시작하는 것이 정상인 문자열에는 `_safe_label`의 수식 방지
    어포스트로피를 붙이면 안 된다 — 붙이면 재현용 명령이 깨진다.
    """
    cleaned = str(text).translate(_CTRL)
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


def _safe_label(text: str, limit: int = 60) -> str:
    """표시용 문자열 정리 — 위험문자 제거 + 수식 무력화 + 길이 제한.

    표시·저장 경로 전용이다. **집계 키로 쓰면 안 된다** (서로 다른 군이 합쳐진다).
    """
    cleaned = str(text).translate(_CTRL).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    if not cleaned:
        return "(빈 값)"
    # 엑셀/구글시트에 붙여넣었을 때 수식으로 실행되지 않도록 앞에 어포스트로피
    if cleaned[0] in _FORMULA_LEADERS:
        cleaned = "'" + cleaned
    return cleaned


def _display_labels(raw_labels: list[str]) -> list[str]:
    """표시용 라벨 목록 — 잘린 뒤 겹치면 번호를 붙여 구분한다."""
    out: list[str] = []
    counts: dict[str, int] = {}
    for raw in raw_labels:
        shown = _safe_label(raw)
        counts[shown] = counts.get(shown, 0) + 1
        out.append(shown if counts[shown] == 1 else f"{shown}#{counts[shown]}")
    return out


@dataclass
class GroupStats:
    """한 군의 온라인(스트리밍) 요약 통계 — Welford 알고리즘."""

    label: str
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0
    missing: int = 0
    values_min: float = field(default=float("inf"))
    values_max: float = field(default=float("-inf"))

    def add(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)
        if x < self.values_min:
            self.values_min = x
        if x > self.values_max:
            self.values_max = x

    @property
    def sd(self) -> float:
        """표본 표준편차 (n < 2면 0)."""
        if self.n < 2:
            return 0.0
        return math.sqrt(max(self.m2, 0.0) / (self.n - 1))

    def as_dict(self, label: str | None = None) -> dict:
        return {"label": label if label is not None else _safe_label(self.label),
                "n": self.n, "mean": self.mean, "sd": self.sd,
                "missing": self.missing,
                "min": self.values_min if self.n else None,
                "max": self.values_max if self.n else None}


@dataclass
class PairedAccumulator:
    """두 열의 상관을 스트리밍으로 계산 (기저값-추적값 상관 r 추정용)."""

    n: int = 0
    mean_x: float = 0.0
    mean_y: float = 0.0
    m2_x: float = 0.0
    m2_y: float = 0.0
    c_xy: float = 0.0

    def add(self, x: float, y: float) -> None:
        self.n += 1
        dx = x - self.mean_x
        dy = y - self.mean_y
        self.mean_x += dx / self.n
        self.mean_y += dy / self.n
        self.m2_x += dx * (x - self.mean_x)
        self.m2_y += dy * (y - self.mean_y)
        self.c_xy += dx * (y - self.mean_y)

    @property
    def correlation(self) -> float | None:
        """Pearson r (n < 3 또는 분산 0이면 None)."""
        if self.n < 3 or self.m2_x <= 0.0 or self.m2_y <= 0.0:
            return None
        return self.c_xy / math.sqrt(self.m2_x * self.m2_y)


def _raise_field_limit() -> None:
    """csv 필드 길이 상한을 _MAX_FIELD_BYTES로 올린다 (플랫폼 상한을 넘지 않게)."""
    try:
        if csv.field_size_limit() < _MAX_FIELD_BYTES:
            csv.field_size_limit(_MAX_FIELD_BYTES)
    except (OverflowError, ValueError):  # pragma: no cover - 플랫폼 의존
        pass


def _sniff(path: str) -> tuple[str, str]:
    """앞부분만 읽어 (인코딩, 구분자)를 판별한다. 파일 전체를 읽지 않는다."""
    if not os.path.exists(path):
        raise PowerPlanError(f"파일을 찾을 수 없습니다: {path}")
    try:
        info = os.stat(path)
    except OSError as exc:
        raise PowerPlanError(f"파일 정보를 읽을 수 없습니다: {path} ({exc.strerror})") from None
    if stat.S_ISDIR(info.st_mode):
        raise PowerPlanError(f"파일이 아니라 폴더입니다: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise PowerPlanError(
            f"일반 파일이 아닙니다(장치/파이프 등): {path}. CSV 파일 경로를 지정하세요"
        )
    if info.st_size > MAX_FILE_BYTES:
        raise PowerPlanError(
            f"파일이 너무 큽니다 ({info.st_size / 1024 ** 3:.1f} GB > "
            f"{MAX_FILE_BYTES / 1024 ** 3:.0f} GB): {path}. 필요한 열만 잘라서 쓰세요"
        )
    try:
        with open(path, "rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError as exc:
        raise PowerPlanError(f"파일을 읽을 수 없습니다: {path} ({exc.strerror})") from None
    if not head.strip():
        raise PowerPlanError(f"빈 파일입니다: {path}")
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or head[:4] in (
            b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        # 엑셀의 "유니코드 텍스트(.txt)"가 UTF-16이다 — NUL 바이트 때문에 이진 파일로
        # 오해받기 쉬우므로, 무엇을 하면 되는지 정확히 알려 준다.
        raise PowerPlanError(
            f"UTF-16으로 저장된 파일입니다: {os.path.basename(path)}. 엑셀에서 "
            "'CSV UTF-8(쉼표로 분리)'로 다시 저장하거나, 메모장에서 열어 UTF-8로 "
            "저장한 뒤 다시 시도하세요"
        )
    if b"\x00" in head:
        raise PowerPlanError(
            f"CSV가 아닌 것 같습니다(이진 파일): {os.path.basename(path)}. "
            ".xlsx라면 CSV로 저장해 주세요"
        )
    encoding = None
    sample = ""
    for enc in _ENCODINGS:
        try:
            # 증분 디코더는 64KB 경계에서 문자가 잘려도 실패하지 않는다
            sample = codecs.getincrementaldecoder(enc)().decode(head, False)
        except UnicodeDecodeError:
            continue
        encoding = enc
        break
    if encoding is None:  # latin-1은 모든 바이트를 받으므로 실질적으로 도달 불가
        raise PowerPlanError(f"인코딩을 인식할 수 없습니다: {path}")
    return encoding, _sniff_delimiter(sample)


def _sniff_delimiter(sample: str) -> str:
    """헤더 줄에서 가장 많이 쓰인 구분자를 고른다 (인용부호 밖 기준)."""
    first = sample.splitlines()[0] if sample.splitlines() else ""
    best, best_count = ",", 0
    for delim in _DELIMITERS:
        try:
            fields = next(csv.reader([first], delimiter=delim))
        except csv.Error:
            continue
        if len(fields) - 1 > best_count:
            best, best_count = delim, len(fields) - 1
    return best


def _resolve_column(header: list[str], name: str, what: str) -> int:
    """열 이름 → 인덱스. 정확일치 → 공백/대소문자 무시 일치 순으로 찾는다."""
    if name in header:
        return header.index(name)
    norm = {h.strip().lower(): i for i, h in enumerate(header)}
    key = name.strip().lower()
    if key in norm:
        return norm[key]
    available = ", ".join(_safe_label(h, 30) for h in header[:20])
    raise PowerPlanError(
        f"{what} 열 '{_safe_label(name)}'을 찾을 수 없습니다. 파일의 열: {available}"
        + (" ..." if len(header) > 20 else "")
    )


def _parse_number(text: str, row_no: int, column: str, errors: list[str],
                  show_values: bool = False) -> float | None:
    """숫자 파싱. 결측이면 None, 이상하면 errors에 기록하고 None.

    오류 메시지에 **원본 셀 값을 넣지 않는 것이 기본**이다. 숫자 열에 자유기술이
    섞여 들어가는 일은 임상 자료에서 흔하고(이름·MRN·전화번호), 그 값이 터미널·
    셸 기록·CI 로그·스크린샷으로 새어 나가면 재식별 위험이 된다. 값이 꼭 필요하면
    `--show-values`로 명시적으로 요청해야 한다.
    """
    token = text.strip().strip('"').strip()
    if token.lower() in MISSING_TOKENS:
        return None
    # 천단위 구분자만 제거한다. "1,5"(유럽식 소수점)는 일부러 오류로 남겨
    # 15로 조용히 잘못 읽히는 일을 막는다.
    cleaned = token.replace(",", "") if _THOUSANDS_RE.match(token) else token

    def note(suffix: str = "") -> None:
        if len(errors) >= _MAX_ERROR_EXAMPLES:
            return
        where = f"{row_no}행 '{_safe_label(column, 30)}'"
        if show_values:
            errors.append(f"{where} = {_safe_label(token, 20)}{suffix}")
        else:
            errors.append(f"{where}: 숫자로 읽을 수 없음{suffix}")

    try:
        value = float(cleaned)
    except ValueError:
        note()
        return None
    if not math.isfinite(value):
        note(" (무한/NaN)")
        return None
    return value


def _resolve_filters(header: list[str], filters) -> list[tuple[int, str, str]]:
    """[(열 인덱스, 열 이름, 기대값)] — 행 선택 조건."""
    out = []
    for column, expected in (filters or ()):
        idx = _resolve_column(header, column, "조건(--filter)")
        out.append((idx, column, str(expected).strip()))
    return out


def _row_matches(row: list[str], resolved) -> bool:
    for idx, _column, expected in resolved:
        if idx >= len(row) or row[idx].strip() != expected:
            return False
    return True


def _read_rows(path: str):
    """(파일 핸들, csv.reader, header, 인코딩, 구분자) — 스트리밍으로 연다.

    호출부가 핸들을 닫아야 한다 (아래 read_* 함수들은 try/finally로 감싼다).
    """
    enc, delim = _sniff(path)
    _raise_field_limit()
    try:
        handle = open(path, "r", encoding=enc, newline="")
    except OSError as exc:
        raise PowerPlanError(f"파일을 열 수 없습니다: {path} ({exc.strerror})") from None
    try:
        reader = csv.reader(handle, delimiter=delim)
        try:
            header = next(reader)
        except StopIteration:
            raise PowerPlanError(f"헤더 줄이 없습니다: {path}") from None
        except csv.Error as exc:
            raise PowerPlanError(f"헤더 줄을 읽을 수 없습니다: {path} ({exc})") from None
        header = [h.strip().lstrip("﻿") for h in header]
        if not any(header):
            raise PowerPlanError(f"헤더 줄이 비어 있습니다: {path}")
    except Exception:
        handle.close()
        raise
    return handle, reader, header, enc, delim


def _iter_rows(reader, path: str):
    """(행 번호, 행) 생성기 — csv/인코딩 오류를 한국어 메시지로 바꿔 준다."""
    row_no = 1
    while True:
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error as exc:
            if "field larger than field limit" in str(exc):
                # 필드 길이 초과는 따옴표 문제가 아니다 — 예전에는 둘을 같은
                # 메시지로 묶어 "따옴표를 확인하라"는 엉뚱한 조언을 했다.
                raise PowerPlanError(
                    f"한 칸의 값이 너무 깁니다 ({row_no}행 이후, 상한 "
                    f"{_MAX_FIELD_BYTES // 1024:,}KB). 따옴표(\")의 짝이 맞지 않아 "
                    "여러 줄이 한 칸으로 붙었을 가능성이 큽니다 — 원본을 확인하세요"
                ) from None
            raise PowerPlanError(
                f"CSV 형식이 깨졌습니다 ({row_no}행 이후): {exc}. "
                "따옴표(\")의 짝이 맞는지 확인하세요"
            ) from None
        except UnicodeDecodeError:
            raise PowerPlanError(
                f"인코딩 문제로 읽을 수 없는 줄이 있습니다 ({row_no}행 이후): {path}. "
                "엑셀에서 'CSV UTF-8'로 다시 저장해 보세요"
            ) from None
        row_no += 1
        yield row_no, row


def read_two_group(path: str, value_col: str, group_col: str,
                   groups: tuple[str, str] | None = None,
                   skip_invalid: bool = False,
                   filters=None, baseline_col: str | None = None,
                   show_values: bool = False, redact: bool = False) -> dict:
    """두 군 CSV를 스트리밍으로 읽어 군별 요약 통계를 만든다.

    Args:
        groups: 비교할 두 군의 **원본 라벨**. None이면 파일에 군이 정확히 둘일 때만 진행.
        filters: [(열, 값)] 형태의 행 선택 조건 (예: 특정 사이트/방문차수만).
        baseline_col: 주면 기저값-추적값의 군내 상관 r을 함께 추정한다(ANCOVA 계획용).
        show_values: 오류 메시지에 원본 셀 값을 넣을지 (기본 False — 재식별 위험).
        redact: 군 라벨·관측 범위를 결과에서 지운다 (출력물을 공유해야 할 때).
    """
    handle, reader, header, enc, delim = _read_rows(path)
    try:
        vi = _resolve_column(header, value_col, "값(--value)")
        gi = _resolve_column(header, group_col, "군(--group)")
        resolved = _resolve_filters(header, filters)
        bi = _resolve_column(header, baseline_col, "기저값(--baseline)") if baseline_col else None
        stats: dict[str, GroupStats] = {}
        paired: dict[str, PairedAccumulator] = {}
        # --groups로 제외된 군은 값을 파싱하지 않고 행 수만 센다
        # (관심 없는 군의 지저분한 값 때문에 분석이 막히지 않도록)
        skipped: dict[str, int] = {}
        errors: list[str] = []
        safe_errors: list[str] = []
        bad_rows = 0
        short_rows = 0
        filtered_out = 0
        for row_no, row in _iter_rows(reader, path):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) <= max(vi, gi, bi if bi is not None else 0):
                short_rows += 1
                if len(errors) < _MAX_ERROR_EXAMPLES:
                    errors.append(f"{row_no}행: 열 수가 부족합니다({len(row)}개)")
                    safe_errors.append(f"{row_no}행: 열 수 부족")
                continue
            if resolved and not _row_matches(row, resolved):
                filtered_out += 1
                continue
            label = row[gi].strip()          # 집계는 항상 원본 라벨로 (표시할 때만 정리)
            if groups is not None and label not in groups:
                if len(skipped) < _MAX_GROUPS:
                    skipped[label] = skipped.get(label, 0) + 1
                continue
            if label not in stats:
                if len(stats) >= _MAX_GROUPS:
                    raise PowerPlanError(
                        f"군 값이 {_MAX_GROUPS}종을 넘었습니다 — '{_safe_label(group_col)}'이 "
                        "정말 군 열인지 확인하세요"
                    )
                stats[label] = GroupStats(label)
                paired[label] = PairedAccumulator()
            before = len(errors)
            value = _parse_number(row[vi], row_no, value_col, errors, show_values)
            if len(errors) > before:
                safe_errors.append(f"{row_no}행 '{_safe_label(value_col, 30)}': 숫자 아님")
            if value is None:
                stats[label].missing += 1
                if len(errors) > before:
                    bad_rows += 1
                continue
            stats[label].add(value)
            if bi is not None:
                base = _parse_number(row[bi], row_no, baseline_col or "", [], False)
                if base is not None:
                    paired[label].add(base, value)
        if (bad_rows or short_rows) and not skip_invalid:
            raise PowerPlanError(
                f"숫자로 읽을 수 없는 값이 {bad_rows + short_rows}개 있습니다: "
                + "; ".join(errors)
                + ". 결측이라면 빈 칸이나 NA로 두고, 무시하려면 --skip-invalid를 쓰세요"
            )
        found = sorted(stats.values(), key=lambda s: (-s.n, s.label))
        usable = [s for s in found if s.n >= 2]
        detail = ", ".join(f"{_safe_label(s.label)}(n={s.n})" for s in found[:10]) or "없음"
        if resolved and not found:
            raise PowerPlanError(
                "--filter 조건에 맞는 행이 없습니다 ("
                + ", ".join(f"{_safe_label(c)}={_safe_label(v)}" for _i, c, v in resolved)
                + f"). 제외된 행 {filtered_out}개"
            )
        if groups is not None:
            # --groups를 준 경우에는 "지정한 군"에 대해 먼저 말해주는 게 도움이 된다
            by_label = {s.label: s for s in usable}
            missing = [g for g in groups if g not in by_label]
            if missing:
                seen = ", ".join(_safe_label(label) for label in sorted(skipped)[:10])
                raise PowerPlanError(
                    "--groups로 지정한 군을 찾을 수 없거나 n < 2입니다: "
                    + ", ".join(_safe_label(g) for g in missing)
                    + f". 읽은 군: {detail}"
                    + (f" · 제외된 군: {seen}" if seen else "")
                )
            chosen = [by_label[g] for g in groups]
        elif len(usable) < 2:
            hint = (f" (--filter로 {filtered_out}개 행을 제외했습니다 — 조건이 한쪽 군만 "
                    "남기고 있지 않은지 확인하세요)" if filtered_out else "")
            raise PowerPlanError(
                f"두 군 비교에는 각 군 n ≥ 2가 필요합니다. 읽은 군: {detail}{hint}"
            )
        elif len(usable) > 2:
            raise PowerPlanError(
                f"군이 {len(usable)}개입니다: "
                + ", ".join(f"{_safe_label(s.label)}(n={s.n})" for s in usable[:10])
                + ". 비교할 두 군을 --groups 군A,군B 로 지정하세요"
            )
        else:
            chosen = usable
        shown = (["군1", "군2"] if redact
                 else _display_labels([s.label for s in chosen]))
        others = [s for s in found if all(s is not c for c in chosen)]
        out = {
            # 경로 전체가 아니라 파일 이름만 남긴다 (임상 파일명에는 연구·사이트·
            # 대상자 정보가 들어가는 일이 흔하다)
            "path": _safe_label(os.path.basename(path), 80),
            "encoding": enc, "delimiter": delim,
            "redacted": bool(redact),
            "group1": _maybe_redact(chosen[0].as_dict(shown[0]), redact),
            "group2": _maybe_redact(chosen[1].as_dict(shown[1]), redact),
            "other_groups": ([] if redact else
                             [s.as_dict() for s in others]
                             + [{"label": _safe_label(label), "rows": count}
                                for label, count in sorted(skipped.items(),
                                                           key=lambda kv: -kv[1])]),
            "invalid_ignored": bad_rows + short_rows if skip_invalid else 0,
            # 저장 파일에는 원본 값을 남기지 않는다 (행·열 위치만)
            "invalid_examples": safe_errors if skip_invalid else [],
            "filtered_out": filtered_out,
            "filters": [{"column": _safe_label(c),
                         "value": "(가림)" if redact else _safe_label(v)}
                        for _i, c, v in resolved],
        }
        if bi is not None:
            out["baseline_column"] = _safe_label(baseline_col or "")
            out["baseline_r"] = _pooled_correlation([paired[s.label] for s in chosen])
        return out
    finally:
        handle.close()


def _pooled_correlation(accumulators) -> float | None:
    """군내 합동 상관 r = Σc_xy / √(Σm2_x · Σm2_y) (기저값 보정 계획용)."""
    sxy = math.fsum(a.c_xy for a in accumulators)
    sxx = math.fsum(a.m2_x for a in accumulators)
    syy = math.fsum(a.m2_y for a in accumulators)
    total_n = sum(a.n for a in accumulators)
    if total_n < 4 or sxx <= 0.0 or syy <= 0.0:
        return None
    r = sxy / math.sqrt(sxx * syy)
    return max(-0.999, min(0.999, r))


def _maybe_redact(group: dict, redact: bool) -> dict:
    """군 요약에서 재식별 소지가 있는 항목을 지운다.

    min/max는 **개별 대상자 한 명의 실제 측정값**이다 (평균·SD와 달리 집계가 아니다).
    출력물을 외부와 공유해야 하는 상황에서는 지우는 편이 안전하다.
    """
    if not redact:
        return group
    out = dict(group)
    out["min"] = None
    out["max"] = None
    return out


def read_paired(path: str, pre_col: str, post_col: str,
                skip_invalid: bool = False, filters=None,
                show_values: bool = False, redact: bool = False) -> dict:
    """사전-사후 두 열을 읽어 **차이**의 요약 통계를 만든다 (쌍 단위).

    `filters`로 특정 군/사이트/방문차수만 골라낼 수 있다. 중재군과 대조군을
    섞어 전후 비교를 하면 무의미한 효과크기가 나오므로, 두 군이 섞인 파일에서는
    `--filter 군=중재` 처럼 반드시 한쪽만 골라야 한다.
    """
    handle, reader, header, enc, delim = _read_rows(path)
    try:
        pi = _resolve_column(header, pre_col, "사전(--pre)")
        qi = _resolve_column(header, post_col, "사후(--post)")
        if pi == qi:
            raise PowerPlanError("--pre와 --post가 같은 열입니다")
        resolved = _resolve_filters(header, filters)
        diff = GroupStats("차이(post − pre)")
        pre_stats, post_stats = GroupStats(pre_col), GroupStats(post_col)
        together = PairedAccumulator()
        errors: list[str] = []
        safe_errors: list[str] = []
        bad_rows = 0
        incomplete = 0
        filtered_out = 0
        for row_no, row in _iter_rows(reader, path):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) <= max(pi, qi):
                bad_rows += 1
                if len(errors) < _MAX_ERROR_EXAMPLES:
                    errors.append(f"{row_no}행: 열 수가 부족합니다({len(row)}개)")
                    safe_errors.append(f"{row_no}행: 열 수 부족")
                continue
            if resolved and not _row_matches(row, resolved):
                filtered_out += 1
                continue
            before = len(errors)
            pre = _parse_number(row[pi], row_no, pre_col, errors, show_values)
            post = _parse_number(row[qi], row_no, post_col, errors, show_values)
            if len(errors) > before:
                bad_rows += 1
                safe_errors.append(f"{row_no}행: 숫자 아님")
                continue
            if pre is None or post is None:
                incomplete += 1
                continue
            pre_stats.add(pre)
            post_stats.add(post)
            diff.add(post - pre)
            together.add(pre, post)
        if bad_rows and not skip_invalid:
            raise PowerPlanError(
                f"숫자로 읽을 수 없는 값이 {bad_rows}개 있습니다: " + "; ".join(errors)
                + ". 무시하려면 --skip-invalid를 쓰세요"
            )
        if diff.n < 2:
            extra = f" (--filter로 {filtered_out}개 행을 제외했습니다)" if filtered_out else ""
            raise PowerPlanError(
                f"사전·사후가 모두 있는 쌍이 {diff.n}개뿐입니다 (최소 2쌍 필요){extra}"
            )
        return {
            "path": _safe_label(os.path.basename(path), 80),
            "encoding": enc, "delimiter": delim,
            "redacted": bool(redact),
            "pre": _maybe_redact(pre_stats.as_dict(_safe_label(pre_col)), redact),
            "post": _maybe_redact(post_stats.as_dict(_safe_label(post_col)), redact),
            "diff": _maybe_redact(diff.as_dict(), redact),
            "incomplete_pairs": incomplete,
            "invalid_ignored": bad_rows if skip_invalid else 0,
            "invalid_examples": safe_errors if skip_invalid else [],
            "filtered_out": filtered_out,
            "filters": [{"column": _safe_label(c),
                         "value": "(가림)" if redact else _safe_label(v)}
                        for _i, c, v in resolved],
            "pre_post_r": together.correlation,
        }
    finally:
        handle.close()


def _d_ci(t_obs: float, df: float, se_unit: float, conf: float) -> tuple[float, float]:
    """비중심 t 역산으로 얻는 d의 정확 신뢰구간."""
    lo_ncp, hi_ncp = nct_ncp_ci(t_obs, df, conf)
    return lo_ncp * se_unit, hi_ncp * se_unit


def effect_from_two_group(data: dict, conf: float = 0.95) -> dict:
    """두 군 요약 → Cohen's d, Hedges g, d의 정확 신뢰구간."""
    g1, g2 = data["group1"], data["group2"]
    n1, n2 = g1["n"], g2["n"]
    df = n1 + n2 - 2
    num = (n1 - 1) * g1["sd"] ** 2 + (n2 - 1) * g2["sd"] ** 2
    sd_pooled = math.sqrt(num / df) if df > 0 else 0.0
    if sd_pooled <= 0.0:
        raise PowerPlanError(
            "두 군의 표준편차가 모두 0입니다 (모든 값이 동일) — 효과크기를 계산할 수 없습니다"
        )
    if not math.isfinite(sd_pooled):
        raise PowerPlanError(
            "관측값이 너무 커서 분산 계산이 넘쳤습니다(무한대). 값의 자릿수를 "
            "확인하거나 단위를 바꿔(예: 그램 → 킬로그램) 다시 시도하세요"
        )
    diff = g1["mean"] - g2["mean"]
    d = diff / sd_pooled
    se_unit = math.sqrt(1.0 / n1 + 1.0 / n2)
    t_obs = d / se_unit
    lo, hi = _d_ci(t_obs, df, se_unit, conf)
    j = hedges_correction(df)
    return {
        "kind": "two_group",
        "n1": n1, "n2": n2, "df": df,
        "mean_diff": diff, "sd_pooled": sd_pooled,
        "d": d, "hedges_g": d * j, "t": t_obs,
        "ci": {"conf": conf, "low": lo, "high": hi},
        "conservative_d": min(abs(lo), abs(hi)) if lo * hi > 0 else 0.0,
    }


def effect_from_paired(data: dict, conf: float = 0.95) -> dict:
    """사전-사후 차이 → dz, dz의 정확 신뢰구간."""
    diff = data["diff"]
    n = diff["n"]
    if diff["sd"] <= 0.0:
        raise PowerPlanError(
            "모든 쌍의 변화량이 동일합니다(차이의 SD = 0) — 효과크기를 계산할 수 없습니다"
        )
    if not math.isfinite(diff["sd"]):
        raise PowerPlanError(
            "변화량이 너무 커서 분산 계산이 넘쳤습니다(무한대). 값의 자릿수를 "
            "확인하거나 단위를 바꿔 다시 시도하세요"
        )
    dz = diff["mean"] / diff["sd"]
    df = n - 1
    se_unit = 1.0 / math.sqrt(n)
    t_obs = dz / se_unit
    lo, hi = _d_ci(t_obs, df, se_unit, conf)
    # df = 1 (쌍 2개)에서는 Hedges 보정계수가 정의되지 않는다 → 보정값을 만들지 않는다
    hedges_g = dz * hedges_correction(df) if df > 1 else None
    return {
        "kind": "paired",
        "n": n, "df": df,
        "mean_diff": diff["mean"], "sd_diff": diff["sd"],
        "dz": dz, "hedges_g": hedges_g, "t": t_obs,
        "ci": {"conf": conf, "low": lo, "high": hi},
        "conservative_d": min(abs(lo), abs(hi)) if lo * hi > 0 else 0.0,
    }
