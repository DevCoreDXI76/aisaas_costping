"""
Anthropic 비용 수집.

- cost_report 엔드포인트(get_yesterday_and_month_to_date_cost, get_cost_for_range):
  MVP 단순화, 시간 기준은 UTC. 텔레그램 리포트가 KST 08:00에 발송되므로 "어제"는
  정확히는 "UTC 기준 어제"를 의미하며 KST 자정 경계와는 다소 어긋난다. Cost API는
  bucket_width가 하루(1d)만 지원하고 UTC 자정에 고정되는 것으로 보여, 이 API만으로는
  KST 자정에 정밀하게 맞출 방법이 없다(2단계 세션 2 조사 결과, PLAN.md 참고).
- usage_report/messages 엔드포인트(get_kst_precise_cost_for_day): 시간(1h) 단위 버킷을
  지원해서 KST 자정에 정확히 맞는 24시간 구간을 그대로 질의할 수 있다. 대신 $ 비용을
  API가 직접 안 주고 토큰 개수만 주기 때문에, prices.json(간소화판 단가표)으로 직접
  환산한다 — 그래서 위 cost_report 기반 함수들보다 근사치가 섞일 수 있다(경고로 표시됨).
  **아직 실험적** — report.py의 실제 발송 경로에는 연결되지 않았다(2단계 세션 2B).

환경변수:
  ANTHROPIC_ADMIN_KEY - platform.claude.com/settings/admin-keys 에서 발급한
                        Admin API 키 (sk-ant-admin01-...)
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# usage_report/messages 기반 함수(get_kst_precise_cost_for_day)에서 src/의 kst_bucket,
# pricing 모듈을 가져다 쓰기 위해, 이 파일이 단독 실행(python src/providers/anthropic_cost.py)
# 되어도 임포트가 되도록 src/ 를 sys.path에 추가해둔다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kst_bucket  # noqa: E402
import pricing  # noqa: E402

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


def get_cost_for_range(start_date: date, end_date_exclusive: date, api_key: str | None = None) -> float:
    """
    [start_date, end_date_exclusive) 구간(둘 다 UTC 날짜 기준)의 합계 비용(USD)을 반환한다.
    주간/월간 요약 리포트(src/weekly_monthly.py)에서 사용.
    """
    starting_at = _iso_z(start_date)
    ending_at = _iso_z(end_date_exclusive)
    data = fetch_cost_report(starting_at, ending_at, api_key)
    return _sum_amount_usd(data)


def get_kst_precise_cost_for_day(
    kst_date: date, api_key: str | None = None
) -> tuple[float, list[str]]:
    """
    [실험적, 2단계 세션 2B] usage_report/messages(시간 단위 버킷)로 KST 캘린더 날짜
    하루의 비용을 prices.json 단가표 기준으로 계산한다. cost_report 기반 함수들과
    달리 진짜 KST 자정에 맞는 하루를 계산한다(kst_bucket.kst_day_to_utc_range).

    간소화판 단가표가 반영하지 않는 사용량(1시간 캐시 쓰기, service_tier가 standard가
    아닌 경우, context_window 명시, 웹 검색 도구 사용 등)을 만나면 비용 계산에서
    빼거나 근사치로 두고 경고 문구를 남긴다 — 실제 청구 금액보다 살짝 낮게 나올 수
    있다는 뜻. 단가표에 아예 없는 모델을 만나면 그 모델분은 0으로 집계에서 빠진다.

    Returns:
        (총 비용 USD, 경고 문구 리스트) — 경고가 있어도 예외를 던지지 않는다
        (report.py에 연결될 때 기존 방어 패턴과 동일하게 다루기 위함).
    """
    api_key = api_key or _get_api_key()
    start_utc, end_utc = kst_bucket.kst_day_to_utc_range(kst_date)

    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "x-api-key": api_key,
    }
    url = f"{ANTHROPIC_API_BASE}/v1/organizations/usage_report/messages"

    prices = pricing.load_prices()
    acc = pricing.CostAccumulator()

    page = None
    while True:
        params = {
            "starting_at": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ending_at": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bucket_width": "1h",
            "group_by[]": "model",
        }
        if page:
            params["page"] = page

        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        body = resp.json()

        for bucket in body.get("data", []):
            for result in bucket.get("results", []):
                model = result.get("model") or "(model 필드 없음)"
                uncached_input = result.get("uncached_input_tokens") or 0
                output = result.get("output_tokens") or 0
                cache_read = result.get("cache_read_input_tokens") or 0
                cache_creation = result.get("cache_creation") or {}
                cache_write_5m = cache_creation.get("ephemeral_5m_input_tokens") or 0
                cache_write_1h = cache_creation.get("ephemeral_1h_input_tokens") or 0

                cost, warn = pricing.calc_anthropic_cost_usd(
                    model, uncached_input, output, cache_write_5m, cache_read, prices
                )
                acc.add(cost, warn)

                if cache_write_1h:
                    acc.add(
                        0.0,
                        f"{model}: 1시간 캐시 쓰기 {cache_write_1h:,}토큰은 간소화판 단가표에 "
                        "없어 비용 계산에서 제외됨(실제보다 과소 계산될 수 있음)",
                    )

                service_tier = result.get("service_tier")
                if service_tier and service_tier != "standard":
                    acc.add(0.0, f"{model}: service_tier={service_tier} 감지(배치 할인 등 반영 안 됨, 근사치)")

                context_window = result.get("context_window")
                if context_window:
                    acc.add(
                        0.0,
                        f"{model}: context_window={context_window} 감지"
                        "(20만 토큰 초과 요율 반영 안 됨, 근사치)",
                    )

                web_search = (result.get("server_tool_use") or {}).get("web_search_requests") or 0
                if web_search:
                    acc.add(0.0, f"{model}: 웹 검색 도구 {web_search}회 사용 감지(별도 과금, 이 계산에 미반영)")

        if body.get("has_more") and body.get("next_page"):
            page = body["next_page"]
        else:
            break

    return acc.total_usd, acc.warnings


def _load_dotenv_if_present() -> None:
    """로컬 개발 편의용. report.py의 동일 함수와 같은 목적 — 이 파일을 report.py를 거치지
    않고 단독 실행(python src/providers/anthropic_cost.py)할 때도 .env가 로드되게 한다."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


if __name__ == "__main__":
    _load_dotenv_if_present()

    y, m = get_yesterday_and_month_to_date_cost()
    print(f"어제(UTC) 비용: ${y:,.2f}")
    print(f"이번 달 누적: ${m:,.2f}")

    yesterday_kst = datetime.now(timezone.utc).astimezone(kst_bucket.KST).date() - timedelta(days=1)
    kst_cost, warnings = get_kst_precise_cost_for_day(yesterday_kst)
    print(f"\n[실험적] 어제(KST {yesterday_kst} 정밀, 간소화 단가표) 비용: ${kst_cost:,.2f}")
    for w in warnings:
        print(f"  ⚠️ {w}")
    if not warnings:
        print("  (경고 없음 — 간소화판 단가표로 커버되는 사용량만 있었음)")
