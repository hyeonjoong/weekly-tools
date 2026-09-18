"""짝짓기 — 보수적으로.

유사 파일명 추측 짝짓기는 **조용히 거짓말하는 경로**다. v1은 세 단계만 쓴다:

1. 파일명 완전 일치(NFC·casefold) → 쌍
2. 이름이 안 맞아도 **정규화 해시가 정확히 한 개** 맞으면 → 같은 파일(이름만 바뀐 사본).
   해시가 여러 작업본과 맞으면 어느 쪽인지 모르므로 짝짓지 않는다.
3. 그 외는 짝짓지 않고 ``짝없음`` 으로 자백한다.

같은 이름의 작업본이 여럿이면(예: ``figures/`` 와 ``manuscript/`` 양쪽에 ``Fig1.png``)
해시가 맞는 쪽을 고르고, 아무것도 안 맞으면 **판정하지 않고** 후보 수를 밝힌다.
"""

import os
from collections import defaultdict

from .normalize import UnreadableFile, content_hash

MATCH_NAME = "이름일치"
MATCH_HASH = "내용일치(이름다름)"
AMBIGUOUS = "이름중복"


class Pair(object):
    """봉투 파일 하나와 작업본 하나."""

    __slots__ = ("pkg", "work", "how", "pkg_hash", "work_hash",
                 "pkg_norm", "work_norm", "candidates")

    def __init__(self, pkg, work, how, pkg_hash, work_hash, pkg_norm, work_norm,
                 candidates=1):
        self.pkg = pkg
        self.work = work
        self.how = how
        self.pkg_hash = pkg_hash
        self.work_hash = work_hash
        self.pkg_norm = pkg_norm
        self.work_norm = work_norm
        self.candidates = candidates

    @property
    def same(self):
        return self.pkg_hash == self.work_hash

    @property
    def normalized(self):
        """정규화가 실제로 개입했는가 (양쪽 중 하나라도)."""
        for tag in (self.pkg_norm, self.work_norm):
            if tag and tag != "none":
                return tag
        return "none"


class HashCache(object):
    """경로 → (해시, 정규화종류). 못 읽은 파일은 삼키지 않고 모아 둔다."""

    def __init__(self):
        self._cache = {}
        self.unreadable = []
        #: 현재 어느 봉투를 읽는 중인지 (자백 경로에 라벨을 붙이기 위한 것).
        #: 라벨이 없으면 `submission/x.pdf` 가 그냥 `x.pdf` 로 자백되어
        #: 작업 폴더의 동명 파일과 구분되지 않고 중복 제거에 먹힌다.
        self.label = ""

    def get(self, record):
        key = record.path
        if key in self._cache:
            return self._cache[key]
        try:
            value = content_hash(record.path)
        except UnreadableFile as exc:
            rel = os.path.join(self.label, record.rel) if self.label else record.rel
            self.unreadable.append((rel, exc.reason))
            value = (None, "none")
        self._cache[key] = value
        return value


class PairingResult(object):
    """짝짓기 결과.

    ``pairs`` 는 **이름으로 짝지은 것만** 담는다 — 세대 판정은 여기에서만 일어난다.
    ``renamed_identical`` 은 이름이 다르지만 내용이 똑같은 것들로, 낡았을 수가
    없으므로 판정 대상이 아니고 ``일치목록.csv`` 에만 조용히 실린다.
    """

    def __init__(self):
        self.pairs = []             # Pair (이름 일치)
        self.renamed_identical = [] # Pair (이름 다름·내용 동일)
        self.pkg_only = []          # FileRecord
        self.work_only = []         # FileRecord
        self.ambiguous = []         # (pkg record, [work records])

    @property
    def coverage(self):
        """봉투 파일 중 짝지어진 비율. 분모가 0이면 None."""
        total = (len(self.pairs) + len(self.renamed_identical)
                 + len(self.pkg_only) + len(self.ambiguous))
        if total == 0:
            return None
        return (len(self.pairs) + len(self.renamed_identical)) / float(total)


def pair_files(pkg_records, work_records, cache):
    """봉투 파일과 작업 파일을 짝짓는다."""
    result = PairingResult()

    by_name = defaultdict(list)
    for rec in work_records:
        by_name[rec.name_key].append(rec)

    by_hash = defaultdict(list)
    for rec in work_records:
        h, _ = cache.get(rec)
        if h is not None:
            by_hash[h].append(rec)

    used_work = set()

    for prec in pkg_records:
        phash, pnorm = cache.get(prec)
        candidates = by_name.get(prec.name_key, [])

        chosen = None
        how = MATCH_NAME
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1:
            # 같은 이름의 작업본이 여럿이다. 봉투와 해시가 맞는 쪽을 고르면
            # **결과가 언제나 '일치'로 고정**된다 — 갈라진 최신본이 있어도 조용해진다.
            # 후보 전부가 같은 내용일 때에만 짝짓고, 아니면 판정하지 않는다.
            hashes = {cache.get(w)[0] for w in candidates}
            if len(hashes) == 1 and None not in hashes:
                chosen = candidates[0]
            else:
                result.ambiguous.append((prec, list(candidates)))
                continue
        else:
            # 이름이 안 맞는다 — 내용이 정확히 한 개와 같을 때만 짝으로 인정한다.
            # 빈 파일이나 확장자가 다른 파일까지 엮으면(0바이트 `__init__.py` ↔ 빈 CSV)
            # 짝지음 비율만 부풀고 양쪽의 진짜 결손이 가려진다.
            same_content = by_hash.get(phash, []) if phash is not None else []
            same_content = [w for w in same_content
                            if w.ext == prec.ext and w.size > 0]
            if len(same_content) == 1:
                chosen = same_content[0]
                how = MATCH_HASH
            else:
                result.pkg_only.append(prec)
                continue

        whash, wnorm = cache.get(chosen)
        used_work.add(chosen.path)
        pair = Pair(prec, chosen, how, phash, whash, pnorm, wnorm,
                    len(candidates) or 1)
        if how == MATCH_HASH:
            result.renamed_identical.append(pair)
        else:
            result.pairs.append(pair)

    for rec in work_records:
        if rec.path not in used_work:
            result.work_only.append(rec)

    return result
