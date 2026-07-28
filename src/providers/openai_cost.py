"""
OpenAI 비용 수집 (Organization Costs API).

환경변수:
  OPENAI_ADMIN_KEY - OpenAI Platform > Organization > Admin keys 에서 발급한 Admin Key

MVP 단순화: Anthropic과 동일하게 UTC 기준 "어제"를 사용한다.

참고(2026-07 기준 커뮤니티 리포트): group_by를 쓰면서 limit을 지정하지 않으면
next_page 페이지네이션 토큰이 깨지는 버그가 보고된 적이 있어, 이 모듈은
group_by를 쓰지 않고 limit을 항상 명시적으로 지정해 회피한다.
"""

import os
from datetime import datetime, timedelta, timezone

import requests

OPENAI_API_BASE = "https://api.openai.com"
MAX_LIMIT = 180  # OpenAI가 허용하는 bucket 개수 상한 (한 달 최대 31일이면 충분)


class OpenAIConfigError(RuntimeError):
    """OPENAI_ADMIN_KEY 미설정 시 발생."""


def _get_api_key() -> str:
    api_key = os.environ.get("OPENAI_ADMIN_KEY")
    if not api_key:
        raise OpenAIConfigError("OPENAI_ADMIN_KEY 환경변수가 설정되지 않았습니다.")
    return api_key


def fetch_cost_report(start_time: int, end_time: int, api_key: str | None = None) -> list[dict]:
    """
    GET /v1/organization/costs 를 페이지네이션 처리하며 전부 호출해
    'data' 버킷 리스트를 합쳐서 반환한다. group_by는 지정하지 않는다.
    """
    api_key = api_key or _get_api_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{OPENAI_API_BASE}/v1/organization/costs"

    all_data = []
    page = None
    while True:
        params = {
            "start_time": start_time,
            "end_time": end_time,
            "bucket_width": "1d",
            "limit": MAX_LIMIT,
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
    """amount.value는 이미 달러(소수) 단위. 실제 응답에서 숫자/문자열 둘 다 관측되어
    float()로 명시 변환한다 (문자열이면 float, None/누락이면 0)."""
    total = 0.0
    for bucket in data_buckets:
        for result in bucket.get("results", []):
            raw_value = result.get("amount", {}).get("value")
            total += float(raw_value) if raw_value is not None else 0.0
    return total


def get_yesterday_and_month_to_date_cost(api_key: str | None = None) -> tuple[float, float]:
    """
    (어제 비용 USD, 이번 달 1일~어제 누적 비용 USD) 를 반환한다.
    기준 시간대는 UTC.
    """
    now_utc = datetime.now(timezone.utc)
    yesterday = (now_utc - timedelta(days=1)).date()
    month_start = yesterday.replace(day=1)

    start_dt = datetime(month_start.year, month_start.month, month_start.day, tzinfo=timezone.utc)
    end_dt = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc) + timedelta(days=1)

    start_time = int(start_dt.timestamp())
    end_time = int(end_dt.timestamp())

    data = fetch_cost_report(start_time, end_time, api_key)

    month_to_date_usd = _sum_amount_usd(data)

    yesterday_start_ts = int(datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc).timestamp())
    yesterday_buckets = [b for b in data if b.get("start_time") == yesterday_start_ts]
    yesterday_usd = _sum_amount_usd(yesterday_buckets)

    return yesterday_usd, month_to_date_usd


if __name__ == "__main__":
    y, m = get_yesterday_and_month_to_date_cost()
    print(f"어제(UTC) 비용: ${y:,.2f}")
    print(f"이번 달 누적: ${m:,.2f}")
