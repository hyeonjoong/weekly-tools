"""numcheck — 원고에 적힌 숫자를 전수 재계산해 대조하는 오프라인 CLI.

원고(.docx/.md/.tex/.txt) 하나만 있으면 된다. 데이터 파일도, 네트워크도 필요
없다. 비율이 분자/분모와 맞는지, 보고된 검정통계량에서 나오는 p 가 적힌 p 와
같은지, 하위군 N 의 합이 전체와 맞는지, 정수 척도의 평균이 그 N 에서 산술적으로
가능한 값인지(GRIM), 변화량이 사후−사전과 맞는지, 점추정치가 자기 신뢰구간
안에 있는지를 전수 대조한다.

    from numcheck import analyze
    report = analyze("원고.docx")
    print(report.n_checked, report.counts())
"""

from __future__ import annotations

__version__ = "0.1.0"

from .engine import analyze, analyze_manuscript  # noqa: E402
from .model import Claim, Finding, Report  # noqa: E402
from .options import Options  # noqa: E402
from .scales import Scale, ScaleRegistry  # noqa: E402

__all__ = [
    "__version__",
    "analyze",
    "analyze_manuscript",
    "Claim",
    "Finding",
    "Report",
    "Options",
    "Scale",
    "ScaleRegistry",
]
