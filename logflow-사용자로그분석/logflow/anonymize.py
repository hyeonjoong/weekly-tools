"""사용자 ID 가명화 — 리포트를 외부와 공유할 수 있게 만든다.

logflow 의 출력(텍스트 상위 사용자, JSON `users[]`, `users.csv`, `adherence_users.csv`)
에는 입력의 **사용자 ID 가 그대로** 담긴다. 실데이터의 ID 는 종종 이메일·기기 식별자·
피험자 번호라서, 리포트 파일을 그대로 공유하면 그 자체가 개인정보 유출이 된다.

이 모듈은 로드 직후 이벤트의 사용자 ID 를 `U001`, `U002` … 로 바꿔 **이후 모든 계산과
출력이 가명만 보게** 한다. 지표는 ID 문자열에 의존하지 않으므로 수치는 완전히 동일하다.

번호 순서는 **(첫 활동 시각, 원래 ID)** 오름차순이다 — 같은 입력이면 언제 돌려도 같은
가명이 나오므로(결정적) 두 번 돌린 리포트를 서로 대조할 수 있다. 대응표는 어디에도
저장하지 않는다: 저장하는 순간 그 파일이 다시 식별 정보가 되기 때문이다. 원본과 다시
연결해야 한다면 원본 로그에 같은 옵션을 적용해 순서를 재현하면 된다.

주의 — 가명화는 **ID 만** 가린다. 이벤트 이름·군 라벨·타임스탬프는 그대로이므로,
드문 이벤트나 인원이 적은 군은 여전히 개인을 좁힐 수 있다(k-익명성을 보장하지 않는다).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Dict, Iterable, List, Sequence, Tuple

from .dataio import Event

# 가명 접두어 후보와 최소 자릿수 (U001 …; 1000명이 넘으면 자릿수가 자동으로 늘어난다).
# 임상 로그의 피험자 번호가 이미 `U001` 꼴인 경우가 흔한데, 그대로 쓰면 원본 `U001`
# 이 다른 사람의 가명과 같은 문자열이 되어 **엉뚱한 사람에게 값이 붙는다**.
# 그래서 입력에 충돌하는 ID 가 있으면 다음 후보로 넘어간다.
_PREFIXES = ("U", "PID", "ANON")
_MIN_DIGITS = 3


def _collides(prefix: str, users: Iterable[str]) -> bool:
    pattern = re.compile(rf"^{re.escape(prefix)}\d+$")
    return any(pattern.match(u) for u in users)


def choose_prefix(users: Iterable[str]) -> str:
    """입력 ID 와 충돌하지 않는 가명 접두어를 고른다 (없으면 마지막 후보)."""
    users = list(users)
    for prefix in _PREFIXES:
        if not _collides(prefix, users):
            return prefix
    return _PREFIXES[-1]


def build_alias_map(events: Sequence[Event]) -> Dict[str, str]:
    """사용자 ID → 가명(`U001`…) 대응을 만든다 (첫 활동 시각, 원래 ID 순).

    반환된 대응표는 호출자가 쓰고 버리는 용도다 — 파일로 저장하면 그 파일이 다시
    식별 정보가 된다는 점을 유의하라.
    """
    first: Dict[str, object] = {}
    for e in events:
        if e.user not in first or e.ts < first[e.user]:
            first[e.user] = e.ts
    order = sorted(first, key=lambda u: (first[u], u))
    prefix = choose_prefix(order)
    digits = max(_MIN_DIGITS, len(str(len(order))))
    return {u: f"{prefix}{i:0{digits}d}" for i, u in enumerate(order, start=1)}


def anonymize_users(events: Sequence[Event]) -> Tuple[List[Event], int, str]:
    """이벤트의 사용자 ID 를 가명으로 바꾼 새 리스트를 반환한다.

    Event 는 frozen dataclass 이므로 원본은 변형되지 않는다(순수 함수).
    반환: (가명화된 이벤트, 가명을 부여한 사용자 수, 쓴 접두어)
    """
    alias = build_alias_map(events)
    prefix = choose_prefix(alias)
    return ([replace(e, user=alias[e.user]) for e in events], len(alias), prefix)
