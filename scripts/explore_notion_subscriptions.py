"""
1회성 진단 스크립트: Notion 연동이 안 될 때 어느 단계에서 막히는지 좁히기 위한 것.
파이프라인 아님 — 결과를 보고 다음 조치를 정한다.

확인하는 것 3가지 (순서대로):
  1) GET /v1/users/me                    — 토큰 자체가 유효한지 (401이면 토큰 문제)
  2) GET /v1/databases/{id}              — Integration이 이 데이터베이스에 연결(공유)됐는지
                                            (404면 "연결(Connections)"에 Integration을 못 찾은 것,
                                             거의 항상 이게 원인)
  3) POST /v1/databases/{id}/query       — notion_subscriptions.py가 실제로 쓰는 조회 엔드포인트
     (2026-07-28: 처음엔 /v1/data_sources/{id}/query로 시도했다가 이 워크스페이스에서
     404가 나서, 안정적인 데이터베이스 단위 쿼리로 바꿈 — PLAN.md 세션 3-1 참고)
"""

import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "providers"))
import notion_subscriptions as ns  # noqa: E402


def _print_response(label: str, resp: requests.Response) -> None:
    print(f"\n[{label}] status={resp.status_code}")
    try:
        body = resp.json()
        print(json.dumps(body, ensure_ascii=False, indent=2)[:800])
    except Exception:
        print(resp.text[:500])


def main() -> None:
    ns._load_dotenv_if_present()
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        print("NOTION_API_KEY가 .env에 없습니다.")
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": ns.NOTION_VERSION,
    }

    cfg = ns.load_config()
    database_id = cfg["database_id"]

    r1 = requests.get("https://api.notion.com/v1/users/me", headers=headers, timeout=15)
    _print_response("1) 토큰 확인 (GET /v1/users/me)", r1)

    r2 = requests.get(f"https://api.notion.com/v1/databases/{database_id}", headers=headers, timeout=15)
    _print_response("2) 데이터베이스 접근 확인 (GET /v1/databases/{id})", r2)

    r3 = requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers={**headers, "Content-Type": "application/json"},
        json={},
        timeout=15,
    )
    _print_response("3) 데이터베이스 쿼리 (POST /v1/databases/{id}/query)", r3)


if __name__ == "__main__":
    main()
