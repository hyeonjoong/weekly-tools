"""설계 JSON — 무엇을 어떤 조건으로 묶고, 무엇을 주장하는가.

**필수가 아닙니다.** 설계 JSON 없이도 파일 위생과 전 파일 쌍 음량 행렬은
나옵니다(가치의 절반). 조건 판정과 주장 대조만 설계 JSON 을 요구합니다.
`--inspect` 로 값을 먼저 보고 `--emit-design` 으로 뼈대를 받아 채우면 됩니다.
`visitaudit` 이 프로토콜 JSON 을 필수로 요구하는 것과는 반대 선택인데,
거기서는 프로토콜 없이 판정 자체가 불가능하지만 여기서는 아니기 때문입니다.

스키마
------
```json
{
  "study": "RESONATE-pilot",
  "conditions": { "active": ["S1.wav", "S2.wav"], "control": ["S3.wav"] },
  "contrast": "modulation_peak_hz",
  "claims": { "S6.wav": { "mod_hz": 0.1, "duration_s": 20.0 } },
  "pairs":  { "싱잉볼_bi.wav": "bi.wav" }
}
```
`conditions` 의 값은 **파일 이름(basename)** 입니다 — 절대경로를 적으면
설계 파일이 이 사람 컴퓨터 밖에서 못 쓰게 되므로 basename 으로 대조합니다.
"""
from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

#: `claims` 에서 지원하는 키. 여기 없는 키는 조용히 무시하지 않고 오류로 거절합니다.
SUPPORTED_CLAIMS = ("carrier_hz", "beat_hz", "mod_hz", "duration_s")
#: 최상위에서 허용하는 키.
TOP_LEVEL_KEYS = ("study", "conditions", "contrast", "claims", "pairs", "notes")


def _short(path: str) -> str:
    """오류 메시지에 절대경로(홈 디렉터리·사용자 이름)를 흘리지 않습니다.

    리포트 산출물은 이미 basename 만 쓰는데 오류 메시지만 전체 경로를
    인쇄하고 있었습니다 — 화면 캡처를 공유하는 순간 그대로 새어 나갑니다.
    """
    import os
    return os.path.basename(path) or path


def _key(name: str) -> str:
    """설계 JSON 의 파일 이름을 입력 쪽과 같은 방식(NFC basename)으로 맞춥니다.

    macOS 는 파일명을 NFD 로 저장하고 편집기는 NFC 로 저장합니다. 한쪽만
    정규화하면 눈으로는 똑같은 `싱잉볼_bi.wav` 가 매칭에 실패해
    "설계 JSON 이 입력에 없는 파일을 가리킵니다"라는 엉뚱한 오류가 납니다.
    """
    return unicodedata.normalize("NFC", os.path.basename(name))


class DesignError(Exception):
    """설계 JSON 이 잘못됐습니다 — CLI 가 종료코드 2 로 바꿉니다."""


@dataclass
class Design:
    study: str = ""
    conditions: Dict[str, List[str]] = field(default_factory=dict)
    contrast: Optional[str] = None
    claims: Dict[str, Dict[str, float]] = field(default_factory=dict)
    pairs: Dict[str, str] = field(default_factory=dict)
    source_path: str = ""

    def condition_of(self, name: str) -> Optional[str]:
        for cond, files in self.conditions.items():
            if name in files:
                return cond
        return None

    @property
    def n_conditions(self) -> int:
        return len(self.conditions)


def load(path: str) -> Design:
    """설계 JSON 을 읽고 검증합니다."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise DesignError("설계 JSON 을 찾을 수 없습니다: {}".format(_short(path))) from None
    except UnicodeDecodeError as exc:
        raise DesignError(
            "설계 JSON 이 UTF-8 이 아닙니다: {}\n사유: {}".format(_short(path), exc)) from exc
    except json.JSONDecodeError as exc:
        raise DesignError(
            "설계 JSON 을 해석할 수 없습니다: {}\n{}번째 줄: {}".format(_short(path), exc.lineno, exc.msg)) from exc
    except RecursionError:
        # `json.load` 는 깊게 중첩된 문서에서 JSONDecodeError 가 아니라
        # RecursionError 를 냅니다 — 잡지 않으면 트레이스백으로 죽습니다.
        raise DesignError(
            "설계 JSON 의 중첩이 너무 깊습니다: {}".format(_short(path))) from None
    except OSError as exc:
        raise DesignError("설계 JSON 을 열 수 없습니다: {}\n사유: {}".format(
            path, exc.strerror or exc)) from exc
    if not isinstance(raw, dict):
        raise DesignError("설계 JSON 의 최상위는 객체({...})여야 합니다.")
    unknown = [k for k in raw if k not in TOP_LEVEL_KEYS]
    if unknown:
        raise DesignError(
            "설계 JSON 에 모르는 항목이 있습니다: {}\n허용: {}".format(
                ", ".join(sorted(unknown)), ", ".join(TOP_LEVEL_KEYS)))

    d = Design(source_path=path)
    d.study = str(raw.get("study", "") or "")

    conds = raw.get("conditions", {}) or {}
    if not isinstance(conds, dict):
        raise DesignError("`conditions` 는 {조건이름: [파일이름, ...]} 형태여야 합니다.")
    seen: Dict[str, str] = {}
    for cond, files in conds.items():
        # 조건 이름은 사람이 적은 값이고, 리포트·CSV·터미널에 그대로 인쇄됩니다.
        # 제어문자(ESC 시퀀스 포함)가 섞여 있으면 화면 출력을 조작할 수 있으므로
        # 여기서 막습니다 — 조건 이름은 사람이 읽는 라벨이지 이스케이프가 아닙니다.
        if any(ord(c) < 32 or ord(c) == 127 for c in str(cond)):
            raise DesignError("조건 이름에 제어문자가 들어 있습니다 — "
                              "조건 이름은 사람이 읽는 라벨이어야 합니다.")
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise DesignError("`conditions.{}` 는 파일 이름 문자열의 배열이어야 합니다.".format(cond))
        if not files:
            raise DesignError("`conditions.{}` 가 비어 있습니다 — 조건은 파일이 최소 1개여야 합니다.".format(cond))
        names = []
        for f in files:
            base = _key(f)
            if base in seen:
                raise DesignError(
                    "파일 `{}` 가 조건 `{}` 와 `{}` 양쪽에 들어 있습니다 — "
                    "한 파일은 한 조건에만 속해야 합니다.".format(base, seen[base], cond))
            seen[base] = cond
            names.append(base)
        d.conditions[str(cond)] = names

    contrast = raw.get("contrast")
    if contrast is not None and not isinstance(contrast, str):
        raise DesignError("`contrast` 는 문자열(매니페스트의 열 이름)이어야 합니다.")
    d.contrast = contrast

    claims = raw.get("claims", {}) or {}
    if not isinstance(claims, dict):
        raise DesignError("`claims` 는 {파일이름: {항목: 값}} 형태여야 합니다.")
    for fname, spec in claims.items():
        if not isinstance(spec, dict):
            raise DesignError("`claims.{}` 는 객체여야 합니다.".format(fname))
        clean: Dict[str, float] = {}
        for key, val in spec.items():
            if key not in SUPPORTED_CLAIMS:
                raise DesignError(
                    "`claims.{}` 의 `{}` 는 지원하지 않는 주장입니다.\n지원: {}\n"
                    "(러프니스·샤프니스 같은 심리음향량은 이 툴이 재지 않습니다 — "
                    "DEBUSSY 매니페스트를 --manifest 로 붙이십시오.)".format(
                        fname, key, ", ".join(SUPPORTED_CLAIMS)))
            if isinstance(val, bool):
                # `float(True)` 는 1.0 이라 조용히 통과합니다 — 주장값이 아닙니다.
                raise DesignError(
                    "`claims.{}.{}` 의 값이 참/거짓입니다 — 숫자를 적으십시오.".format(fname, key))
            try:
                num = float(val)
            except (TypeError, ValueError):
                raise DesignError(
                    "`claims.{}.{}` 의 값이 숫자가 아닙니다: {!r}".format(fname, key, val)) from None
            if not (num == num) or num in (float("inf"), float("-inf")):
                raise DesignError("`claims.{}.{}` 의 값이 유한한 숫자가 아닙니다.".format(fname, key))
            if num <= 0:
                raise DesignError("`claims.{}.{}` 의 값은 0보다 커야 합니다.".format(fname, key))
            clean[key] = num
        d.claims[_key(fname)] = clean

    pairs = raw.get("pairs", {}) or {}
    if not isinstance(pairs, dict):
        raise DesignError("`pairs` 는 {새파일이름: 기준파일이름} 형태여야 합니다.")
    for new, old in pairs.items():
        if not isinstance(old, str):
            raise DesignError("`pairs.{}` 의 값은 파일 이름 문자열이어야 합니다.".format(new))
        d.pairs[_key(new)] = _key(old)
    return d


def unassigned_inputs(d: Design, input_names: Sequence[str]) -> List[str]:
    """입력에는 있는데 어느 조건에도 속하지 않은 파일들.

    거절하지는 않습니다(부분 설계는 정당한 사용입니다). 다만 **조용히 빼면
    설계 JSON 의 오타 하나로 자극 하나가 대조에서 사라지므로** 경고로 알립니다.
    """
    if not d.conditions:
        return []
    assigned = {f for files in d.conditions.values() for f in files}
    return [n for n in input_names if n not in assigned]


def check_against_inputs(d: Design, input_names: Sequence[str]) -> None:
    """설계 JSON 이 실제 입력에 없는 파일을 가리키면 거절합니다(종료코드 2).

    조용히 무시하면 "조건 3개"라고 인쇄해 놓고 실제로는 2개만 비교하는
    거짓 리포트가 나옵니다. (반대 방향 — 입력에 있는데 조건에 없는 파일 —
    은 `unassigned_inputs` 가 경고로 알립니다.)
    """
    have = set(input_names)
    missing: List[str] = []
    for cond, files in d.conditions.items():
        for f in files:
            if f not in have:
                missing.append("conditions.{} → {}".format(cond, f))
    for f in d.claims:
        if f not in have:
            missing.append("claims → {}".format(f))
    if missing:
        raise DesignError(
            "설계 JSON 이 입력에 없는 파일을 가리킵니다:\n  {}\n"
            "입력 {}개: {}\n"
            "(설계 JSON 은 절대경로가 아니라 파일 이름으로 대조합니다.)".format(
                "\n  ".join(missing), len(have), ", ".join(sorted(have)[:12])))


def emit_skeleton(input_names: Sequence[str], study: str = "") -> str:
    """`--emit-design` 이 인쇄하는 설계 JSON 뼈대.

    조건 이름을 넣어 주지 않습니다 — 자동으로 지어내면 사람이 확인하지 않고
    그대로 쓰게 되고, 그러면 이 툴이 가장 중요한 질문(무엇이 대조군인가)에
    스스로 답해 버리는 셈이 됩니다. 파일 목록만 채워서 내놓습니다.
    """
    payload = {
        "study": study or "(연구 이름을 적으세요)",
        "conditions": {
            "조건이름을_바꾸세요": [_key(n) for n in input_names],
        },
        "contrast": None,
        "claims": {_key(n): {} for n in input_names},
        "notes": "① conditions 를 실제 조건별로 **나누세요** — 전부 한 조건에 있으면 "
                 "조건 간 비교가 성립하지 않아 정보만 출력합니다. "
                 "② claims 에 설계상 주장한 값을 적으세요(빈 객체는 검사 안 함). "
                 "지원 주장: " + ", ".join(SUPPORTED_CLAIMS),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
