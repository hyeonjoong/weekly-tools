"""방향 판정 — 증거가 있을 때만.

불일치 사실은 mtime 없이도 참이다. **어느 쪽이 낡았는지**만 mtime 에 기댄다.
클라우드 동기화·``cp`` 는 mtime 을 갈아 치우므로, mtime 이 못 미더우면
``[판정불가]`` 로 떨어지고 불일치 사실 자체는 여전히 보고된다.

추측으로 방향을 말하면 조용히 거짓말하는 것이므로, 판정 함수에는
"애매하면 최신 쪽으로" 같은 경로가 **없다**.
"""

#: 파일시스템 mtime 해상도(FAT=2초, 일부 네트워크 FS)를 감안한 동률 허용폭.
#: 이 안쪽 차이는 방향 근거로 쓰지 않는다.
MTIME_TOLERANCE_SEC = 2.0

SAME = "일치"
CRITICAL_STALE_PACKAGE = "[치명] 봉투가 구세대"
WARN_PACKAGE_NEWER = "[경고] 봉투가 더 최신"
UNDECIDABLE = "[판정불가]"

#: 리포트에서 치명으로 세는 판정
CRITICAL_VERDICTS = frozenset({CRITICAL_STALE_PACKAGE})


class Verdict(object):
    __slots__ = ("label", "reason")

    def __init__(self, label, reason=""):
        self.label = label
        self.reason = reason

    def __eq__(self, other):  # pragma: no cover - 테스트 편의
        return isinstance(other, Verdict) and self.label == other.label

    def __repr__(self):  # pragma: no cover
        return "Verdict(%r, %r)" % (self.label, self.reason)


def judge(pair, tolerance=MTIME_TOLERANCE_SEC):
    """쌍 하나의 판정.

    해시가 같으면 ``일치``. 다르면 mtime 차이가 ``tolerance`` 를 넘을 때에만
    방향을 말하고, 그 밖에는 ``[판정불가]``.
    """
    if pair.pkg_hash is None or pair.work_hash is None:
        return Verdict(UNDECIDABLE, "파일을 읽지 못해 내용을 비교할 수 없음")
    if pair.same:
        return Verdict(SAME, "")
    delta = pair.work.mtime - pair.pkg.mtime
    if delta > tolerance:
        return Verdict(CRITICAL_STALE_PACKAGE, "작업본이 봉투보다 나중에 수정됨")
    if delta < -tolerance:
        return Verdict(WARN_PACKAGE_NEWER,
                       "봉투가 작업본보다 나중에 수정됨 — 봉투에서 손으로 고쳤을 수 있음")
    return Verdict(UNDECIDABLE,
                   "내용은 다르지만 수정시각 차이가 %.0f초 이내라 방향을 말할 수 없음"
                   % tolerance)
