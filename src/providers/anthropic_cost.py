"""
Anthropic 비용 수집 (Admin API의 cost_report 엔드포인트).

환경변수:
  ANTHROPIC_ADMIN_KEY - platform.claude.com/settings/admin-keys 에서 발급한
                        Admin API 키 (sk-ant-admin01-...)

MVP 단순화: 시간 기준은 UTC. 텔레그램 리포트가 KST 08:00에 발송되므로
"어제"는 정확히는 "UTC 기준 어제"를 의미하며 KST 자정 경계와는 다소 어긋난다.
(정밀화는 PLAN.md 2단계 백로그 항목)
"""

import os
from datetime import date, datetime, timedelta, timezone

import requests

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicConfigError(RuntimeError):
    """ANTHROPIC_ADMIN_KEY 미설정 시 발생."""


def _get_api_key() -> str:
    api_key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not api_key:
        raise AnthropicConfigError("ANTHROPIC_ADMIN_KEY 환경변수가 설정되지 않았습니다.")
    return api_key


def _iso_z(d: date) -> str:
    """date를 RFC3339 UTC 자정 타임스탬프 문자열로 변환. 예: 2026-07-27 -> '2026-07-27T00:00:00Z'"""
    return f"{d.isoformat()}T00:00:00Z"


def fetch_cost_report(starting_at: str, ending_at: str, api_key: str | None = None) -> list[dict]:
    """
    GET /v1/organizations/cost_report 를 페이지네이션 처리하며 전부 호출해
    'data' 버킷 리스트를 합쳐서 반환한다. group_by는 지정하지 않아
    버킷(하루)당 하나의 합산 결과만 오도록 한다.
    """
    api_key = api_key or _get_api_key()
    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "x-api-key": api_key,
    }
    url = f"{ANTHROPIC_API_BASE}/v1/organizations/cost_report"

    all_data = []
    page = None
    while True:
        params = {
            "starting_at": starting_at,
            "ending_at": ending_at,
            "bucket_width": "1d",
        }
        if page:
            params["page"] = page

        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        body = resp.json()

        all_data.extend(body.get("data", []))

        if body.get("has_more") and body.get("next_page"):
            page = body["next_page"]
        else:
            break

    return all_data


def _sum_amount_usd(data_buckets: list[dict]) -> float:
    """amount는 '최소 통화 단위(센트)'의 소수 문자열이므로 100으로 나눠 달러로 변환."""
    total_cents = 0.0
    for bucket in data_buckets:
        for result in bucket.get("results", []):
            total_cents += float(result["amount"])
    return total_cents / 100


def get_yesterday_and_month_to_date_cost(api_key: str | None = None) -> tuple[float, float]:
    """
    (어제 비용 USD, 이번 달 1일~어제 누적 비용 USD) 를 반환한다.
    기준 시간대는 UTC.
    """
    now_utc = datetime.now(timezone.utc)
    yesterday = (now_utc - timedelta(days=1)).date()
    month_start = yesterday.replace(day=1)

    starting_at = _iso_z(month_start)
    ending_at = _iso_z(yesterday + timedelta(days=1))  # ending_at은 미포함(exclusive) 상한

    data = fetch_cost_report(starting_at, ending_at, api_key)

    month_to_date_usd = _sum_amount_usd(data)

    yesterday_str = yesterday.isoformat()
    yesterday_buckets = [b for b in data if b.get("starting_at", "").startswith(yesterday_str)]
    yesterday_usd = _sum_amount_usd(yesterday_buckets)

    return yesterday_usd, month_to_date_usd


if __name__ == "__main__":
    y, m = get_yesterday_and_month_to_date_cost()
    print(f"어제(UTC) 비용: ${y:,.2f}")
    print(f"이번 달 누적: ${m:,.2f}")
