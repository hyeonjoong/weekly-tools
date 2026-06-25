"""paperforge — 보유 데이터(매니페스트)에서 멀티모달 논문 아이디어 매트릭스 생성."""

__version__ = "0.1.0"

from .engine import IdeaResult, evaluate  # noqa: F401
from .manifest import Manifest, load_manifest, parse_manifest  # noqa: F401
