"""
API 키(프로젝트) → 리포트 표시명 매핑 (projects.json).

매핑에 없는 키는 원래 이름을 그대로 돌려준다 — 새 키를 발급하고 projects.json을
깜빡해도 리포트에는 키 이름으로라도 나오게 하기 위함. projects.json이 없거나 깨져도
예외를 던지지 않고 빈 매핑으로 동작한다(매핑은 표시용일 뿐 비용 계산과는 무관).
"""

import json
from pathlib import Path

DEFAULT_PROJECTS_PATH = Path(__file__).resolve().parent.parent / "projects.json"


def load_project_map(path: Path = DEFAULT_PROJECTS_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def display_name(provider: str, key: str, fallback: str | None = None, mapping: dict | None = None) -> str:
    """provider("anthropic"/"openai")의 매핑 키(Anthropic 키 이름 / OpenAI 프로젝트 ID)를
    표시명으로 바꾼다. 매핑에 없으면 fallback(OpenAI 프로젝트 이름 등), 그것도 없으면 key."""
    if mapping is None:
        mapping = load_project_map()
    return (mapping.get(provider) or {}).get(key) or fallback or key
