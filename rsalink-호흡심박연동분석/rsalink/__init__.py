"""rsalink — 호흡 신호 + RR 간격 연동 분석 (stdlib 전용, 오프라인).

실측 호흡(파형 또는 흡기 시작 이벤트)과 RR 간격을 함께 읽어
호흡별 호흡수 · RSA peak-valley(Grossman 1990) · 고정 HF vs 호흡중심 대역 파워 ·
호흡–심박 결맞음 · 페이싱 동조율을 계산하고, 조건별 비교표로
"HRV 변화가 호흡 변화와 동반됐는가"를 숫자로 보여준다. 인과를 주장하지 않는다.
"""

__version__ = "0.1.0"


class RsalinkError(Exception):
    """입력·인자 오류 (exit 2)."""
