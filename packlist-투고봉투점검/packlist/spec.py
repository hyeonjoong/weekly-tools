"""저널 규정을 **입력으로 받는다**. 코드에 박지 않는다.

어떤 저널이 몇 MB 까지 받는지, 무엇을 필수로 요구하는지는 이 툴이 알 수
없는 외부 지식이다. 알은척하면 그 순간 틀린 판정을 하게 된다.
그래서 ``--limits`` / ``--expect`` 로 받을 때만 판정하고, 없으면
'검사 안 함'이라고 자백한다.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .safeio import UsageError, read_text_any, sanitize

_REQUIRED_KEYS = ("required", "필수", "필수제출물")
_OPTIONAL_KEYS = ("optional", "선택", "선택제출물")
#: '이 봉투가 아니라 저널 포털에 따로 올린다'고 선언한 항목들.
#: 보충자료를 포털로 올리는 워크플로에서 매 회차 같은 경고가 뜨는 것을 끈다.
_ELSEWHERE_KEYS = ("elsewhere", "별도제출", "포털제출")
_TOTAL_KEYS = ("max_total_mb", "봉투_최대MB", "총_최대MB")
_FILE_KEYS = ("max_file_mb", "파일_최대MB")
_COUNT_KEYS = ("max_files", "파일_최대개수")


@dataclass
class Expectations:
    """``--expect`` 로 받은 제출물 목록."""

    required: List[str] = field(default_factory=list)
    optional: List[str] = field(default_factory=list)
    elsewhere: List[str] = field(default_factory=list)
    source: str = ""


@dataclass
class Limits:
    """``--limits`` 로 받은 용량/개수 상한. 값이 없으면 그 항목은 검사하지 않는다."""

    max_total_mb: Optional[float] = None
    max_file_mb: Optional[float] = None
    max_files: Optional[int] = None
    source: str = ""

    @property
    def empty(self) -> bool:
        return self.max_total_mb is None and self.max_file_mb is None and self.max_files is None


def _load_json(path_text: str, what: str) -> Dict:
    path = Path(path_text)
    raw = read_text_any(path, what)
    def _reject_constant(token):
        raise UsageError(
            f"{what} 파일에 숫자가 아닌 값 '{token}' 이 있습니다 — JSON 표준 숫자만 씁니다"
        )

    try:
        data = json.loads(raw, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise UsageError(
            f"{what} 파일이 올바른 JSON 이 아닙니다: {sanitize(path.name)} ({exc.lineno}번째 줄)"
        )
    if not isinstance(data, dict):
        raise UsageError(f"{what} 파일의 최상위는 객체(JSON object)여야 합니다: {sanitize(path.name)}")
    return data


def _pick(data: Dict, keys) -> Optional[object]:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _as_str_list(value, what: str, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise UsageError(f"{what} 의 '{field_name}' 는 문자열 목록이어야 합니다")
    return [v.strip() for v in value if v.strip()]


def _as_number(value, what: str, field_name: str):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageError(f"{what} 의 '{field_name}' 는 숫자여야 합니다")
    # 400자리 정수는 float 로 옮기는 순간 OverflowError 가 난다.
    if isinstance(value, int) and value.bit_length() > 1024:
        raise UsageError(f"{what} 의 '{field_name}' 가 지나치게 큽니다")
    if not math.isfinite(value):
        raise UsageError(f"{what} 의 '{field_name}' 가 유한한 수가 아닙니다")
    if value <= 0:
        raise UsageError(f"{what} 의 '{field_name}' 는 0보다 커야 합니다")
    return value


def load_expectations(path_text: str) -> Expectations:
    data = _load_json(path_text, "--expect")
    expect = Expectations(source=Path(path_text).name)
    expect.required = _as_str_list(_pick(data, _REQUIRED_KEYS), "--expect", "required")
    expect.optional = _as_str_list(_pick(data, _OPTIONAL_KEYS), "--expect", "optional")
    expect.elsewhere = _as_str_list(_pick(data, _ELSEWHERE_KEYS), "--expect", "elsewhere")
    if not expect.required and not expect.optional and not expect.elsewhere:
        raise UsageError("--expect 파일에 'required'(필수) 목록이 없습니다")
    return expect


def load_limits(path_text: str) -> Limits:
    data = _load_json(path_text, "--limits")
    limits = Limits(source=Path(path_text).name)
    limits.max_total_mb = _as_number(_pick(data, _TOTAL_KEYS), "--limits", "max_total_mb")
    limits.max_file_mb = _as_number(_pick(data, _FILE_KEYS), "--limits", "max_file_mb")
    count = _as_number(_pick(data, _COUNT_KEYS), "--limits", "max_files")
    limits.max_files = int(count) if count is not None else None
    if limits.empty:
        raise UsageError("--limits 파일에 확인할 수 있는 상한이 하나도 없습니다")
    return limits
