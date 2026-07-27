"""cuesift — AI 자막 번역·검수 트리아지 엔진."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cuesift")
except PackageNotFoundError:  # 소스 트리에서 미설치 상태로 실행되는 경우
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
