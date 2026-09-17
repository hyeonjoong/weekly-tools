"""doseaudit 가 쓰는 예외와 종료코드.

종료코드 규약 (README·사용법.md 와 동일):

    0  불일치 없음
    1  치명 발견
    2  판정 없이 거절 — 정의 미선언 / 규격 불일치 / 출력 경로 거절 / 다른 툴의 일
    3  판정 불가 — 못 읽은 파일이 하나라도 있음. **3 이 1 보다 우선한다.**

3 이 1 보다 우선하는 이유: 못 읽은 파일이 있는데 "치명 2건"을 보고하면, 사람은
그 2건이 전부라고 읽는다. 무엇을 못 봤는지가 먼저다.
"""

EXIT_CLEAN = 0
EXIT_CRITICAL = 1
EXIT_REFUSE = 2
EXIT_UNJUDGEABLE = 3


class DoseAuditError(Exception):
    """doseaudit 예외의 뿌리."""

    exit_code = EXIT_REFUSE


class RefuseError(DoseAuditError):
    """판정하지 않고 거절한다 (exit 2).

    정의 미선언, 5열 규격 불일치, 출력 경로 거절, 이 툴의 일이 아닌 입력 등
    **추론하면 조용히 거짓말하게 되는 모든 자리**에서 던진다.
    """

    exit_code = EXIT_REFUSE


class ReadError(DoseAuditError):
    """파일을 읽지 못했다 (exit 3 으로 집계된다).

    던져서 죽는 대신 커버리지 자백에 '못 읽음'으로 실려야 하는 경우가 많으므로,
    호출부가 잡아서 기록하는 쪽을 기본으로 한다.
    """

    exit_code = EXIT_UNJUDGEABLE


class ReportIntegrityError(DoseAuditError):
    """`[커버리지 자백]` 없는 리포트를 내보내려 했다.

    이 툴의 리포트는 '무엇을 안 봤는지'가 본문의 일부다. 자백 블록이 빠진 리포트는
    출력하느니 죽는 편이 낫다 — 자백 없는 리포트는 '전부 봤다'로 읽히기 때문이다.
    """

    exit_code = EXIT_REFUSE
