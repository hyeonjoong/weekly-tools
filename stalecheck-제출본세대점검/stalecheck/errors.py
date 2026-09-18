"""종료코드를 실어 나르는 예외들.

종료코드 규약(README·사용법.md와 동일):
    0  치명 0건
    1  치명 N건 — 봉투가 구세대인 쌍이 있다
    2  판정 없이 거절 — 입력을 특정하지 못함
    3  판정 불가 — 읽지 못한 파일이 있거나 커버리지 미달 (3이 1보다 우선)
"""

EXIT_OK = 0
EXIT_CRITICAL = 1
EXIT_REFUSED = 2
EXIT_UNDECIDABLE = 3


class StalecheckError(Exception):
    """사용자에게 한국어 한 줄로 보여 줄 오류. 트레이스백을 내지 않는다."""

    exit_code = EXIT_REFUSED

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class RefusedError(StalecheckError):
    """입력을 특정하지 못해 판정 자체를 거절한다 (exit 2)."""

    exit_code = EXIT_REFUSED


class UndecidableError(StalecheckError):
    """읽지 못한 파일/커버리지 미달로 판정을 신뢰할 수 없다 (exit 3)."""

    exit_code = EXIT_UNDECIDABLE


class ReportIntegrityError(StalecheckError):
    """[커버리지 자백]이 빠진 리포트는 출력하지 않는다.

    자백 없는 리포트는 '전부 확인했다'로 읽히기 때문에, 자백이 비면
    리포트를 내보내는 대신 이 오류로 죽는다.
    """

    exit_code = EXIT_REFUSED
