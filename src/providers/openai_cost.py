"""
OpenAI 비용 수집.

- Organization Costs API(get_yesterday_and_month_to_date_cost, get_cost_for_range):
  MVP 단순화, Anthropic과 동일하게 UTC 기준 "어제"를 사용한다. bucket_width가 하루(1d)만
  지원되고 UTC 자정에 고정되는 것으로 보여, 이 API만으로는 KST 자정에 정밀하게 맞출
  방법이 없다(2단계 세션 2 조사 결과, PLAN.md 참고).
- Usage API(usage/completions, get_kst_precise_cost_for_day): 시간(1h) 단위 버킷을
  지원해서 KST 자정에 정확히 맞는 24시간 구간을 그대로 질의할 수 있다. $ 비용은 API가
  안 주고 토큰 개수만 주기 때문에 prices.json(간소화판 단가표)으로 직접 환산한다.
  **아직 실험적** — Anthropic 쪽(src/providers/anthropic_cost.py)과 동일한 설계.

환경변수:
  OPENAI_ADMIN_KEY - OpenAI Platform > Organization > Admin keys 에서 발급한 Admin Key

참고(2026-07 기준 커뮤니티 리포트): group_by를 쓰면서 limit을 지정하지 않으면
next_page 페이지네이션 토큰이 깨지는 버그가 보고된 적이 있어, Costs API 호출부는
limit을 항상 명시적으로 지정해 회피한다(합계 함수는 group_by 없이, 프로젝트별 함수는
group_by=project_id,line_item + limit).
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# get_kst_precise_cost_for_day()에서 src/의 kst_bucket, pricing 모듈을 쓰기 위해
# 이 파일을 단독 실행(python src/providers/openai_cost.py)해도 임포트되게 src/를 추가.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kst_bucket  # noqa: E402
import pricing  # noqa: E402
import project_map  # noqa: E402

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


def get_cost_for_range(start_date: date, end_date_exclusive: date, api_key: str | None = None) -> float:
    """
    [start_date, end_date_exclusive) 구간(둘 다 UTC 날짜 기준)의 합계 비용(USD)을 반환한다.
    주간/월간 요약 리포트(src/weekly_monthly.py)에서 사용.
    """
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    end_dt = datetime(
        end_date_exclusive.year, end_date_exclusive.month, end_date_exclusive.day, tzinfo=timezone.utc
    )
    data = fetch_cost_report(int(start_dt.timestamp()), int(end_dt.timestamp()), api_key)
    return _sum_amount_usd(data)


def get_kst_precise_cost_for_day(
    kst_date: date, api_key: str | None = None
) -> tuple[float, list[str]]:
    """
    [실험적, 2단계 세션 2C] usage/completions(시간 단위 버킷)로 KST 캘린더 날짜 하루의
    비용을 prices.json 단가표 기준으로 계산한다. Anthropic 쪽
    (anthropic_cost.get_kst_precise_cost_for_day)과 동일한 설계 — kst_bucket.py로
    KST 자정에 정확히 맞는 UTC 24시간 구간을 계산해서 그 구간만 질의한다.

    간소화판 단가표가 반영하지 않는 사용량(오디오 토큰, batch 처리, 기본이 아닌
    service_tier)을 만나면 그 부분은 비용 계산에서 빼고 경고를 남긴다.
    batch는 실제로는 50% 할인되므로, 감지되면 이 함수가 계산한 값이 실제보다
    **높게**(과대 계상) 나올 수 있다는 뜻이다(Anthropic 쪽 경고들과 반대 방향).

    Returns:
        (총 비용 USD, 경고 문구 리스트)
    """
    api_key = api_key or _get_api_key()
    start_utc, end_utc = kst_bucket.kst_day_to_utc_range(kst_date)

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{OPENAI_API_BASE}/v1/organization/usage/completions"

    prices = pricing.load_prices()
    acc = pricing.CostAccumulator()

    page = None
    while True:
        params = {
            "start_time": int(start_utc.timestamp()),
            "end_time": int(end_utc.timestamp()),
            "bucket_width": "1h",
            "group_by[]": "model",
            "limit": 24,
        }
        if page:
            params["page"] = page

        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        body = resp.json()

        for bucket in body.get("data", []):
            for result in bucket.get("results", []):
                model = result.get("model") or "(model 필드 없음)"
                total_input = result.get("input_tokens") or 0
                cached_input = result.get("input_cached_tokens") or 0
                output = result.get("output_tokens") or 0

                cost, warn = pricing.calc_openai_cost_usd(model, total_input, cached_input, output, prices)
                acc.add(cost, warn)

                audio_in = result.get("input_audio_tokens") or 0
                audio_out = result.get("output_audio_tokens") or 0
                if audio_in or audio_out:
                    acc.add(
                        0.0,
                        f"{model}: 오디오 토큰(입력 {audio_in:,}/출력 {audio_out:,})은 간소화판 "
                        "단가표에 없어 비용 계산에서 제외됨",
                    )

                if result.get("batch"):
                    acc.add(
                        0.0,
                        f"{model}: batch 처리 감지(50% 할인 미반영 — 이 계산은 실제보다 "
                        "과대 계상될 수 있음)",
                    )

                service_tier = result.get("service_tier")
                if service_tier and service_tier not in ("default",):
                    acc.add(0.0, f"{model}: service_tier={service_tier} 감지(요율 차이 미반영, 근사치)")

        if body.get("has_more") and body.get("next_page"):
            page = body["next_page"]
        else:
            break

    return acc.total_usd, acc.warnings


def _get_paginated(path: str, params: dict, api_key: str) -> list[dict]:
    """Admin API GET을 next_page가 없을 때까지 따라가며 'data'를 전부 합쳐 반환한다."""
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{OPENAI_API_BASE}{path}"
    all_data = []
    page = None
    while True:
        p = dict(params)
        if page:
            p["page"] = page
        resp = requests.get(url, headers=headers, params=p, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        all_data.extend(body.get("data", []))
        if body.get("has_more") and body.get("next_page"):
            page = body["next_page"]
        else:
            return all_data


def fetch_project_names(api_key: str | None = None) -> dict[str, str]:
    """{project_id: 프로젝트 이름}. 보관된 프로젝트도 포함한다(지난 비용이 남아 있을 수 있어서)."""
    api_key = api_key or _get_api_key()
    projects = _get_paginated(
        "/v1/organization/projects", {"limit": 100, "include_archived": "true"}, api_key
    )
    return {p["id"]: p.get("name") or p["id"] for p in projects}


def fetch_costs_by_project_daily(start_time: int, end_time: int, api_key: str | None = None) -> list[dict]:
    """Costs API를 group_by=project_id,line_item으로 조회한다. 모듈 상단 주석의 페이지네이션
    버그를 피하려고 limit을 항상 명시한다(1d 버킷 상한이라 한 달이 한 번에 온다)."""
    api_key = api_key or _get_api_key()
    return _get_paginated(
        "/v1/organization/costs",
        {
            "start_time": start_time,
            "end_time": end_time,
            "bucket_width": "1d",
            "limit": MAX_LIMIT,
            "group_by": ["project_id", "line_item"],
        },
        api_key,
    )


def _aggregate_by_project(buckets: list[dict], project_names: dict[str, str], mapping: dict) -> list[dict]:
    """Costs 버킷을 표시명(projects.json 매핑 → API 프로젝트 이름 → 프로젝트 ID 순) 단위로 합쳐
    [{project, provider, cost_usd, models: {model: usd}}]를 비용 내림차순으로 반환한다.
    Costs API는 실제 청구 금액이라 단가표 추정이 필요 없다. line_item("gpt-5.6-terra, input"
    형태)의 쉼표 앞부분을 모델명으로 쓴다."""
    rows: dict[str, dict] = {}
    for bucket in buckets:
        for r in bucket.get("results", []):
            raw_value = (r.get("amount") or {}).get("value")
            cost = float(raw_value) if raw_value is not None else 0.0
            project_id = r.get("project_id") or "(프로젝트 없음)"
            project = project_map.display_name(
                "openai", project_id, fallback=project_names.get(project_id), mapping=mapping
            )
            model = (r.get("line_item") or "(기타)").split(",")[0].strip()

            row = rows.setdefault(project, {"project": project, "provider": "OpenAI", "cost_usd": 0.0, "models": {}})
            row["cost_usd"] += cost
            row["models"][model] = row["models"].get(model, 0.0) + cost

    return sorted(rows.values(), key=lambda x: x["cost_usd"], reverse=True)


_project_names_cache: dict[str, str] | None = None


def _fetch_project_names_safe(api_key: str) -> dict[str, str]:
    """프로젝트 목록은 실행(프로세스) 1회당 한 번만 조회해 일일/주간/월간 섹션이 같이 쓴다.
    조회가 실패해도 비용 집계는 계속하며, 실패 결과(빈 매핑)도 캐시해 이번 실행 동안에는
    프로젝트 ID가 그대로 표시된다."""
    global _project_names_cache
    if _project_names_cache is None:
        try:
            _project_names_cache = fetch_project_names(api_key)
        except Exception:
            _project_names_cache = {}
    return _project_names_cache


def _utc_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _period_index(bucket: dict, periods: list[tuple[date, date]]) -> int | None:
    """버킷(start_time, UTC 자정 timestamp)이 속한 [start, end) 구간의 인덱스. 없으면 None."""
    ts = bucket.get("start_time")
    for i, (start, end) in enumerate(periods):
        if ts is not None and _utc_ts(start) <= ts < _utc_ts(end):
            return i
    return None


def get_cost_for_periods(periods: list[tuple[date, date]], api_key: str | None = None) -> list[float]:
    """여러 [start, end) (UTC 날짜) 구간의 Costs 합계를 조회 한 번으로 구한다(주간/월간 비교용)."""
    data = fetch_cost_report(
        _utc_ts(min(s for s, _ in periods)), _utc_ts(max(e for _, e in periods)), api_key
    )
    totals = [0.0] * len(periods)
    for b in data:
        i = _period_index(b, periods)
        if i is not None:
            totals[i] += _sum_amount_usd([b])
    return totals


def get_cost_by_project_for_periods(
    periods: list[tuple[date, date]], api_key: str | None = None
) -> list[list[dict]]:
    """여러 [start, end) (UTC 날짜) 구간의 프로젝트별 비용을 조회 한 번으로 구해 구간별로 돌려준다."""
    api_key = api_key or _get_api_key()
    buckets = fetch_costs_by_project_daily(
        _utc_ts(min(s for s, _ in periods)), _utc_ts(max(e for _, e in periods)), api_key
    )
    project_names = _fetch_project_names_safe(api_key)
    mapping = project_map.load_project_map()

    per_period: list[list[dict]] = [[] for _ in periods]
    for b in buckets:
        i = _period_index(b, periods)
        if i is not None:
            per_period[i].append(b)
    return [_aggregate_by_project(bs, project_names, mapping) for bs in per_period]


def get_cost_by_project(
    start_date: date, end_date_exclusive: date, api_key: str | None = None
) -> list[dict]:
    """[start_date, end_date_exclusive) (UTC 날짜) 구간의 프로젝트별 비용. 주간/월간 요약에서 사용."""
    api_key = api_key or _get_api_key()
    buckets = fetch_costs_by_project_daily(_utc_ts(start_date), _utc_ts(end_date_exclusive), api_key)
    return _aggregate_by_project(buckets, _fetch_project_names_safe(api_key), project_map.load_project_map())


def get_yesterday_and_month_to_date_cost_by_project(
    api_key: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """(어제, 이번 달 1일~어제 누적)의 프로젝트별 비용. get_yesterday_and_month_to_date_cost와
    같은 UTC 구간을 조회 1회(+프로젝트 목록 1회)로 받아 어제 버킷만 따로 골라낸다."""
    api_key = api_key or _get_api_key()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    month_start = yesterday.replace(day=1)

    buckets = fetch_costs_by_project_daily(_utc_ts(month_start), _utc_ts(yesterday + timedelta(days=1)), api_key)
    project_names = _fetch_project_names_safe(api_key)
    mapping = project_map.load_project_map()

    yesterday_buckets = [b for b in buckets if b.get("start_time") == _utc_ts(yesterday)]
    return (
        _aggregate_by_project(yesterday_buckets, project_names, mapping),
        _aggregate_by_project(buckets, project_names, mapping),
    )


def _load_dotenv_if_present() -> None:
    """로컬 개발 편의용. anthropic_cost.py의 동일 함수와 같은 목적 — 이 파일을 단독
    실행(python src/providers/openai_cost.py)할 때도 .env가 로드되게 한다."""
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
