"""프로토콜 JSON 로딩·검증.

프로토콜이 이 툴의 존재 이유다 — 프로토콜 없이 돌아가는 순간 joinaudit 재탕이
되므로, 없거나 깨져 있으면 즉시 exit 2 로 죽는다(cli 쪽에서).

한국어 키가 기본이고, 영문 키 별칭도 받는다.

스키마는 엄격하다: 알 수 없는 키는 어느 레벨에서든 즉시 ProtocolError 다.
프로토콜 파일이 판정 규칙의 계약서인데, 오타(`창이탈일수초괴`)가 조용히
무시되면 규칙 하나가 소리 없이 꺼진다 — 그게 최악의 실패 모드다.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import List, Optional


class ProtocolError(Exception):
    """프로토콜을 신뢰할 수 없다 — 판정을 시작하면 안 된다."""


_OPS = {">=", "<=", ">", "<", "==", "!="}

_KEYS_TOP = {"연구명", "study", "기준방문", "anchor", "목표N", "target_n",
             "방문", "visits", "선정기준", "inclusion", "제외기준", "exclusion",
             "PP제외규칙", "pp_rules"}
_KEYS_VISIT = {"이름", "name", "오프셋", "offset", "창", "window", "필수", "required"}
_KEYS_CRIT = {"항목", "item", "연산", "op", "값", "value"}
_KEYS_PP = {"필수방문결측", "missing_required", "창이탈일수초과", "max_days_out",
            "선정기준위반", "eligibility_violation", "탈락", "dropout"}


def _check_keys(obj: dict, allowed: set, context: str) -> None:
    """알 수 없는 키 → 즉시 오류 (가장 비슷한 유효 키를 같이 알려 준다)."""
    for k in obj:
        if k not in allowed:
            close = difflib.get_close_matches(str(k), sorted(allowed), n=1, cutoff=0.5)
            hint = f" — 혹시 {close[0]!r} 인가요?" if close else ""
            raise ProtocolError(
                f"{context}에 알 수 없는 키 {k!r}{hint} "
                f"(허용되는 키: {', '.join(sorted(allowed))})"
            )


@dataclass(frozen=True)
class VisitDef:
    name: str
    offset: int          # 기준방문일 + offset = 예정일
    win_lo: int          # 예정일 + win_lo = 창 시작 (경계 포함)
    win_hi: int          # 예정일 + win_hi = 창 종료 (경계 포함)
    required: bool


@dataclass(frozen=True)
class Criterion:
    item: str
    op: str
    value: object        # 숫자 또는 문자열
    kind: str            # "선정" | "제외"

    def describe(self) -> str:
        return f"{self.item} {self.op} {self.value}"


@dataclass
class PPRules:
    missing_required: bool = False   # 필수방문결측
    max_days_out: Optional[int] = None  # 창이탈일수초과 (이 값을 '초과'하면 제외)
    eligibility_violation: bool = True  # 선정/제외기준 위반 (명시 없으면 켬)
    dropout: bool = True                # 중도탈락 (명시 없으면 켬)


@dataclass
class Protocol:
    study: str
    anchor: str
    target_n: Optional[int]
    visits: List[VisitDef]
    inclusion: List[Criterion] = field(default_factory=list)
    exclusion: List[Criterion] = field(default_factory=list)
    pp_rules: Optional[PPRules] = None

    def visit_names(self) -> List[str]:
        return [v.name for v in self.visits]

    def get_visit(self, name: str) -> VisitDef:
        for v in self.visits:
            if v.name == name:
                return v
        raise KeyError(name)


def _pick(obj: dict, *keys, default=None):
    for k in keys:
        if k in obj:
            return obj[k]
    return default


def _as_int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{what} 은(는) 정수여야 합니다: {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ProtocolError(f"{what} 은(는) 정수여야 합니다: {value!r}")
    return int(value)


# 오프셋·창은 날짜 산술에 그대로 들어간다. datetime 이 감당하는 범위를 넘으면
# OverflowError 트레이스백으로 죽으므로(그리고 그 종료코드 1 은 '이탈 발견'과
# 충돌한다) 여기서 상식적인 범위로 미리 자른다. 100년이면 어떤 임상시험도 넉넉하다.
MAX_DAY_OFFSET = 36500


def _check_day_range(days: int, what: str) -> None:
    if abs(days) > MAX_DAY_OFFSET:
        raise ProtocolError(
            f"{what} 이(가) 범위를 벗어났습니다({days}일) — ±{MAX_DAY_OFFSET}일(약 100년) 안이어야 합니다")


def _parse_visit(obj, idx: int) -> VisitDef:
    if not isinstance(obj, dict):
        raise ProtocolError(f"방문 정의 {idx + 1}번째가 객체가 아닙니다")
    _check_keys(obj, _KEYS_VISIT, f"방문 정의 {idx + 1}번째")
    name = _pick(obj, "이름", "name")
    if not name or not isinstance(name, str):
        raise ProtocolError(f"방문 정의 {idx + 1}번째에 '이름' 이 없습니다")
    offset = _as_int(_pick(obj, "오프셋", "offset"), f"방문 '{name}' 의 오프셋")
    _check_day_range(offset, f"방문 '{name}' 의 오프셋")
    win = _pick(obj, "창", "window")
    if not isinstance(win, list) or len(win) != 2:
        raise ProtocolError(f"방문 '{name}' 의 창은 [시작, 끝] 두 정수여야 합니다: {win!r}")
    lo = _as_int(win[0], f"방문 '{name}' 창 시작")
    hi = _as_int(win[1], f"방문 '{name}' 창 끝")
    _check_day_range(lo, f"방문 '{name}' 의 창 시작")
    _check_day_range(hi, f"방문 '{name}' 의 창 끝")
    if lo > hi:
        raise ProtocolError(f"방문 '{name}' 의 창 시작({lo})이 끝({hi})보다 큽니다")
    required = _pick(obj, "필수", "required", default=True)
    if not isinstance(required, bool):
        raise ProtocolError(f"방문 '{name}' 의 '필수' 는 true/false 여야 합니다")
    return VisitDef(name=name, offset=offset, win_lo=lo, win_hi=hi, required=required)


def _parse_criteria(items, kind: str) -> List[Criterion]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ProtocolError(f"{kind}기준은 목록이어야 합니다")
    out = []
    for i, obj in enumerate(items):
        if not isinstance(obj, dict):
            raise ProtocolError(f"{kind}기준 {i + 1}번째가 객체가 아닙니다")
        _check_keys(obj, _KEYS_CRIT, f"{kind}기준 {i + 1}번째")
        item = _pick(obj, "항목", "item")
        op = _pick(obj, "연산", "op")
        value = _pick(obj, "값", "value")
        if not item or not isinstance(item, str):
            raise ProtocolError(f"{kind}기준 {i + 1}번째에 '항목' 이 없습니다")
        if op not in _OPS:
            raise ProtocolError(
                f"{kind}기준 '{item}' 의 연산 {op!r} 을 모릅니다 (지원: {' '.join(sorted(_OPS))})"
            )
        if value is None:
            raise ProtocolError(f"{kind}기준 '{item}' 에 '값' 이 없습니다")
        out.append(Criterion(item=item, op=op, value=value, kind=kind))
    return out


def _parse_pp(obj) -> Optional[PPRules]:
    if obj is None:
        return None
    if not isinstance(obj, dict):
        raise ProtocolError("PP제외규칙은 객체여야 합니다")
    _check_keys(obj, _KEYS_PP, "PP제외규칙")
    rules = PPRules()
    rules.missing_required = bool(_pick(obj, "필수방문결측", "missing_required", default=False))
    raw = _pick(obj, "창이탈일수초과", "max_days_out")
    if raw is not None:
        n = _as_int(raw, "PP제외규칙.창이탈일수초과")
        if n < 0:
            raise ProtocolError("PP제외규칙.창이탈일수초과 는 0 이상이어야 합니다")
        rules.max_days_out = n
    rules.eligibility_violation = bool(
        _pick(obj, "선정기준위반", "eligibility_violation", default=True)
    )
    rules.dropout = bool(_pick(obj, "탈락", "dropout", default=True))
    return rules


def _read_protocol_text(path: str) -> str:
    """프로토콜 JSON 본문. CSV 쪽과 같은 인코딩 규칙(utf-8 → cp949)을 쓴다.

    메모장 'ANSI'(cp949)나 엑셀 '유니코드 텍스트'(utf-16)로 저장된 프로토콜이
    UnicodeDecodeError 트레이스백으로 죽던 자리다. UnicodeDecodeError 는
    ValueError 라서 OSError 로는 잡히지 않았다.
    """
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except FileNotFoundError:
        raise ProtocolError(f"프로토콜 파일이 없습니다: {path}")
    except OSError as e:
        raise ProtocolError(f"프로토콜 파일을 읽을 수 없습니다: {path} ({e})")
    # utf-16 은 아무 짝수 길이 바이트열이나 받아 주기 때문에 순서대로 시도하면
    # cp949 파일을 삼켜 깨진 글자를 내놓는다. BOM 이 있을 때만 utf-16 으로 본다.
    if blob[:2] in (b"\xff\xfe", b"\xfe\xff"):
        candidates = ("utf-16", "utf-8-sig", "cp949")
    else:
        candidates = ("utf-8-sig", "cp949")
    for enc in candidates:
        try:
            return blob.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ProtocolError(
        f"프로토콜 파일의 인코딩을 해석할 수 없습니다(utf-8/utf-16/cp949 아님): {path}")


def load_protocol(path: str) -> Protocol:
    """프로토콜 JSON → Protocol. 판정 규칙 전부가 여기서 온다.

    스키마는 엄격하다 — 알 수 없는 키는 어느 레벨에서든 거부하고 비슷한 키를
    제안한다(오타 한 글자가 규칙 하나를 조용히 끄는 것을 막기 위해). 기준방문의
    오프셋은 0이어야 하고, 방문은 오프셋 오름차순이어야 한다.
    """
    text = _read_protocol_text(path)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"프로토콜 JSON 이 깨져 있습니다: {path} ({e})")
    except RecursionError:
        raise ProtocolError(f"프로토콜 JSON 이 너무 깊게 중첩돼 있습니다: {path}")
    if not isinstance(data, dict):
        raise ProtocolError("프로토콜 최상위는 JSON 객체여야 합니다")
    _check_keys(data, _KEYS_TOP, "프로토콜 최상위")

    anchor = _pick(data, "기준방문", "anchor")
    if not anchor or not isinstance(anchor, str):
        raise ProtocolError("프로토콜에 '기준방문' 이 없습니다 — 어느 방문을 0일로 삼을지 몰라 판정할 수 없습니다")

    visits_raw = _pick(data, "방문", "visits")
    if not isinstance(visits_raw, list) or not visits_raw:
        raise ProtocolError("프로토콜에 '방문' 목록이 없습니다")
    visits = [_parse_visit(v, i) for i, v in enumerate(visits_raw)]

    names = [v.name for v in visits]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ProtocolError(f"방문 이름이 중복됩니다: {', '.join(sorted(dup))}")
    if anchor not in names:
        raise ProtocolError(f"기준방문 {anchor!r} 이 방문 목록에 없습니다")

    # 기준방문의 예정일은 정의상 '그 방문이 실제로 있었던 날' 이다. 오프셋이 0 이
    # 아니면 예정일 = 실제일 + 오프셋 이 되어 기준방문이 제 창 밖으로 떨어지고,
    # 그 어긋난 기준으로 나머지 방문이 전부 판정돼 100% 이탈이 뜬다.
    anchor_def = next(v for v in visits if v.name == anchor)
    if anchor_def.offset != 0:
        raise ProtocolError(
            f"기준방문 {anchor!r} 의 오프셋은 0 이어야 합니다(지금 {anchor_def.offset}). "
            f"기준방문일이 곧 0일이므로, 모든 오프셋에서 {anchor_def.offset} 을 빼서 "
            f"{anchor!r} 이 0 이 되도록 맞추세요."
        )

    # 순서 위반 판정과 '마지막 방문'(완료 여부)은 방문 목록의 나열 순서를 그대로
    # 시간 순서로 믿는다. 오프셋이 뒤죽박죽이면 정상 데이터에서 순서 위반이
    # 만들어지고, 그 근거 문장이 스스로를 부정하는 꼴이 된다.
    for a, b in zip(visits, visits[1:]):
        if b.offset < a.offset:
            raise ProtocolError(
                f"방문을 시간 순서(오프셋 오름차순)로 나열해 주세요 — "
                f"{a.name!r}(오프셋 {a.offset}) 다음에 {b.name!r}(오프셋 {b.offset}) 이 옵니다. "
                f"순서 위반 판정과 '마지막 방문' 판정이 이 순서를 그대로 씁니다."
            )

    target_raw = _pick(data, "목표N", "target_n")
    target_n = None
    if target_raw is not None:
        target_n = _as_int(target_raw, "목표N")
        if target_n <= 0:
            raise ProtocolError("목표N 은 1 이상이어야 합니다")

    return Protocol(
        study=str(_pick(data, "연구명", "study", default="(연구명 미기재)")),
        anchor=anchor,
        target_n=target_n,
        visits=visits,
        inclusion=_parse_criteria(_pick(data, "선정기준", "inclusion"), "선정"),
        exclusion=_parse_criteria(_pick(data, "제외기준", "exclusion"), "제외"),
        pp_rules=_parse_pp(_pick(data, "PP제외규칙", "pp_rules")),
    )
