"""
Notion "구독 중" 데이터소스 연동 (2단계 세션 3 — 세션 1: 읽기 전용 전환 / 세션 2: D-3 알림
+ 원탭 버튼 + "마지막 이용일" 기록 / 세션 3(선택): N개월 이상 미이용 하이라이트).

기존에는 subscriptions.json을 수동으로 관리했는데, 실제로 쓰고 있는 Notion
구독 관리 DB랑 내용이 어긋나는 문제가 있었다(예: JSON에는 "Claude MAX" $100로
남아있었는데 Notion에는 "Claude" $22로 다르게 기록됨). 이제부터는 Notion을
결제 알림의 단일 소스로 쓴다 — subscriptions.json/src/subscriptions.py는 폐기.

Notion 쪽 "구독 중" 데이터소스 스키마(사람이 직접 채우는 필드만 표시):
  이름            (title)
  유형            (select: OTT, AI Agent, Productivity, ... )
  상태            (select: "구독 중" / "구독 해지")
  결제 시작일      (date)
  결제 주기        (select: "월" / "연")
  결제 금액(KRW)   (number, 원화 결제 항목만 채워짐)
  결제 금액(USD)   (number, 달러 결제 항목만 채워짐)
  비고            (rich_text)
  마지막 이용일    (date, 2단계 세션 2에서 추가 — D-3 알림에 달린 텔레그램 버튼
                   응답을 코드가 자동으로 채워준다. 사람이 직접 채울 필요 없음)

Notion에는 "다음 결제일"이라는 수식(formula) 필드가 이미 있지만, 공개 API로는
그 계산 로직 자체를 가져올 수 없어서(수식 결과만 읽기 전용으로 노출되고, 필터/파싱이
번거로움) 여기서는 결제 시작일 + 결제 주기로 다음 결제일을 직접 계산한다. 그래야
말일 보정(예: 1/31 매달 결제 -> 2월엔 2/28) 같은 규칙을 우리가 통제할 수 있다.

환경변수:
  NOTION_API_KEY - notion.so/my-integrations 에서 발급한 Internal Integration 토큰.
                   발급 후 대상 Notion 페이지(또는 DB)에 "연결(Connections)"에서
                   이 Integration을 추가해줘야 실제로 읽을 수 있다(권한 공유 별도 필요).

notion_config.json 스키마 (프로젝트 루트):
  {
    "database_id": "...",          # Notion "구독 중" 데이터베이스 ID
    "active_status_value": "구독 중"  # 상태 필드에서 "지금 결제 중"으로 볼 값
  }

2026-07-28 실사용 검증 메모 (트러블슈팅 전체 과정은 PLAN.md 2단계 세션 3-1 로그 참고):
처음엔 Notion의 "데이터소스(data source)" 단위 쿼리 엔드포인트(`/v1/data_sources/{id}/query`,
Notion-Version 2025-09-03)로 시도했는데 404가 났고, 오래되고 안정적인 **데이터베이스 단위
쿼리 엔드포인트**(`/v1/databases/{id}/query`, Notion-Version 2022-06-28)로 바꿔봐도
"이 API bot이 접근 가능한 데이터소스가 없다"는 400이 났다. 원인은 사용자가 원래 쓰던 구독
관리 DB가 Notion이 데이터소스 개념을 도입하기 전에 만들어진 것이라, 새로 만든 Integration이
그 DB의 데이터소스에 접근할 수 없는 특이 케이스였다(다른 기존 DB로 테스트해보니 정상 동작 —
Integration 자체 문제는 아님). Duplicate로 복제해도 동일 데이터소스를 그대로 참조해서
안 됐고, 최종적으로 Notion API로 완전히 새 데이터베이스를 만들어 데이터를 옮기는 것으로
해결했다. **따라서 이 코드가 가리키는 DB(`notion_config.json`의 `database_id`)는 사용자가
원래 관리하던 DB가 아니라 CostPing 전용으로 새로 만든 DB다** — 구독 추가/해지는 이 DB에서
해야 한다.
"""

import calendar
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")

NOTION_API_BASE = "https://api.notion.com"
NOTION_VERSION = "2022-06-28"  # 데이터베이스 단위 query 엔드포인트의 안정적인 버전

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "notion_config.json"


class NotionConfigError(RuntimeError):
    """NOTION_API_KEY 미설정 또는 notion_config.json 문제(없음/형식 깨짐) 시 발생."""


def _get_api_key() -> str:
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        raise NotionConfigError("NOTION_API_KEY 환경변수가 설정되지 않았습니다.")
    return api_key


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise NotionConfigError(f"{path.name} 파일이 없습니다.")
    if not cfg.get("database_id"):
        raise NotionConfigError(f"{path.name}에 database_id가 없습니다.")
    return cfg


def _add_months(d: date, months: int) -> date:
    """월 단위로 날짜를 굴리되 말일 보정한다 (예: 1/31 + 1개월 -> 2/28 또는 2/29)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _months_between(early: date, late: date) -> int:
    """early부터 late까지 지난 '만 개월 수'를 계산한다 (예: 1/15 -> 4/10은 아직 3개월,
    4/15부터 3개월). late < early면 0."""
    if late < early:
        return 0
    months = (late.year - early.year) * 12 + (late.month - early.month)
    if late.day < early.day:
        months -= 1
    return max(months, 0)


def _next_payment_date(start: date, cycle: str, today: date) -> date:
    """결제 시작일이 오늘/미래면 그대로, 과거면 오늘 이후로 올 때까지 결제 주기만큼 굴린다."""
    step_months = 1 if cycle == "월" else 12
    candidate = start
    for _ in range(2000):  # 결제 시작일이 아주 오래됐어도 충분한 반복 횟수(안전장치)
        if candidate >= today:
            return candidate
        candidate = _add_months(candidate, step_months)
    return candidate  # pragma: no cover


def _get_title(prop: dict | None) -> str | None:
    if not prop or prop.get("type") != "title":
        return None
    text = "".join(p.get("plain_text", "") for p in prop.get("title", []))
    return text or None


def _get_date_start(prop: dict | None) -> str | None:
    if not prop or prop.get("type") != "date":
        return None
    date_obj = prop.get("date")
    return date_obj.get("start") if date_obj else None


def _get_select(prop: dict | None) -> str | None:
    if not prop or prop.get("type") != "select":
        return None
    sel = prop.get("select")
    return sel.get("name") if sel else None


def _get_number(prop: dict | None) -> float | None:
    if not prop or prop.get("type") != "number":
        return None
    return prop.get("number")


def _get_rich_text(prop: dict | None) -> str:
    if not prop or prop.get("type") != "rich_text":
        return ""
    return "".join(p.get("plain_text", "") for p in prop.get("rich_text", []))


def _parse_subscription_page(page: dict, today: date) -> dict | None:
    """이름/결제 시작일/결제 주기 중 하나라도 비어 있으면 (빈 줄 등) None을 반환해 건너뛴다."""
    props = page.get("properties", {})
    name = _get_title(props.get("이름"))
    start_str = _get_date_start(props.get("결제 시작일"))
    cycle = _get_select(props.get("결제 주기"))
    if not name or not start_str or cycle not in ("월", "연"):
        return None

    start = date.fromisoformat(start_str[:10])
    next_payment = _next_payment_date(start, cycle, today)

    amount_usd = _get_number(props.get("결제 금액(USD)"))
    amount_krw = _get_number(props.get("결제 금액(KRW)"))
    if amount_usd is not None:
        amount, currency = amount_usd, "USD"
    elif amount_krw is not None:
        amount, currency = amount_krw, "KRW"
    else:
        amount, currency = 0, "KRW"

    last_used_str = _get_date_start(props.get("마지막 이용일"))
    last_used = date.fromisoformat(last_used_str[:10]) if last_used_str else None

    return {
        "page_id": page.get("id"),
        "name": name,
        "amount": amount,
        "currency": currency,
        "start_date": start,
        "next_payment_date": next_payment,
        "memo": _get_rich_text(props.get("비고")),
        "last_used_date": last_used,
    }


def fetch_active_subscriptions(
    api_key: str | None = None, config: dict | None = None
) -> list[dict]:
    """
    Notion "구독 중" 데이터베이스에서 상태 = active_status_value인 행을 전부 읽어와
    [{"page_id", "name", "amount", "currency", "start_date"(date 객체), "next_payment_date"
    (date 객체), "memo", "last_used_date"(date 객체 또는 None)}, ...] 로 반환한다.
    """
    api_key = api_key or _get_api_key()
    config = config or load_config()
    database_id = config["database_id"]
    active_value = config.get("active_status_value", "구독 중")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"{NOTION_API_BASE}/v1/databases/{database_id}/query"

    today = datetime.now(KST).date()
    results: list[dict] = []
    start_cursor = None

    while True:
        body = {"filter": {"property": "상태", "select": {"equals": active_value}}}
        if start_cursor:
            body["start_cursor"] = start_cursor

        resp = requests.post(url, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        payload = resp.json()

        for page in payload.get("results", []):
            sub = _parse_subscription_page(page, today)
            if sub:
                results.append(sub)

        if payload.get("has_more") and payload.get("next_cursor"):
            start_cursor = payload["next_cursor"]
        else:
            break

    return results


def get_due_today_and_tomorrow(
    api_key: str | None = None, config: dict | None = None
) -> tuple[list[dict], list[dict]]:
    """(오늘 결제 예정, 내일 결제 예정) 튜플을 반환한다. report.py의
    build_subscription_section()이 기대하는 형식(name/amount/currency)과 호환된다."""
    subs = fetch_active_subscriptions(api_key, config)
    today = datetime.now(KST).date()
    tomorrow = today + timedelta(days=1)
    due_today = [s for s in subs if s["next_payment_date"] == today]
    due_tomorrow = [s for s in subs if s["next_payment_date"] == tomorrow]
    return due_today, due_tomorrow


def get_due_in_days(
    days: int, api_key: str | None = None, config: dict | None = None
) -> list[dict]:
    """[2단계 세션 2] 오늘로부터 정확히 days일 후 결제 예정인 구독만 반환한다.
    D-3 "이번 달 쓰셨나요?" 알림에 사용 — 결제 전에 미리 물어봐야 해지 여부를
    실제 결제 전에 결정할 수 있기 때문에 D-day가 아니라 D-3에 묻는다."""
    subs = fetch_active_subscriptions(api_key, config)
    target = datetime.now(KST).date() + timedelta(days=days)
    return [s for s in subs if s["next_payment_date"] == target]


def get_unused_subscriptions(
    months: int = 3, api_key: str | None = None, config: dict | None = None
) -> list[dict]:
    """[2단계 세션 3(선택)] 월간 리포트용 "N개월 이상 미이용" 하이라이트 대상을 반환한다.

    "마지막 이용일"은 D-3 알림에서 "✅ 씀"을 눌러야만 채워지고 "❌ 안 씀"은 갱신하지
    않는다(update_last_used_date 참고) — 그래서 이 필드가 비어 있는 것 자체가 "한 번도
    이용을 확인 못 함"이라는 신호가 된다. 다만 이 필드는 2026-07-28에 막 추가돼서,
    구독을 시작한 지 얼마 안 됐거나 아직 D-3 알림이 한 번도 안 온 구독까지 무작정
    "미이용"으로 몰면 오탐이 된다. 그래서 두 경우를 나눠서 판단한다:
      1) last_used_date가 있으면: 그 날짜로부터 months개월 이상 지났는지로 판단.
      2) last_used_date가 없으면: 결제 시작일(start_date)로부터 months개월 이상 지났는데도
         (=D-3 알림을 최소 한 번은 받았을 만한 기간인데도) 한 번도 "✅ 씀"이 안 눌렸는지로
         판단. 시작한 지 얼마 안 된 구독은 제외된다.

    반환값의 각 항목은 기존 dict에 "unused_reason"("last_used"|"never_confirmed")과
    "reference_date"(판단 기준이 된 날짜), "months_since"(그 날짜로부터 지난 만 개월 수)를
    덧붙인 것이다. next_payment_date 오름차순이 아니라 months_since 내림차순(가장 오래
    안 쓴 것부터)으로 정렬해서 반환한다.
    """
    subs = fetch_active_subscriptions(api_key, config)
    today = datetime.now(KST).date()

    unused: list[dict] = []
    for s in subs:
        if s["last_used_date"] is not None:
            reference_date = s["last_used_date"]
            reason = "last_used"
        else:
            reference_date = s["start_date"]
            reason = "never_confirmed"

        months_since = _months_between(reference_date, today)
        if months_since >= months:
            unused.append(
                {
                    **s,
                    "unused_reason": reason,
                    "reference_date": reference_date,
                    "months_since": months_since,
                }
            )

    unused.sort(key=lambda x: x["months_since"], reverse=True)
    return unused


def update_last_used_date(
    page_id: str,
    used_date: date,
    api_key: str | None = None,
) -> None:
    """[2단계 세션 2] 텔레그램 "✅ 이번 달 씀" 버튼 응답을 Notion에 반영한다.
    "마지막 이용일" 필드를 used_date로 덮어쓴다 (PATCH /v1/pages/{page_id}).
    "❌ 안 씀" 응답은 이 함수를 호출하지 않는다 — 그래야 필드가 갱신되지 않고
    그대로 남아 있고, 이게 나중(세션 3)에 "N개월째 안 씀"을 판단하는 근거가 된다."""
    api_key = api_key or _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"{NOTION_API_BASE}/v1/pages/{page_id}"
    body = {"properties": {"마지막 이용일": {"date": {"start": used_date.isoformat()}}}}
    resp = requests.patch(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()


def _load_dotenv_if_present() -> None:
    """로컬 개발 편의용. anthropic_cost.py/openai_cost.py와 같은 목적 — 이 파일을
    단독 실행(python src/providers/notion_subscriptions.py)할 때도 .env가 로드되게 한다."""
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

    subs = fetch_active_subscriptions()
    print(f"구독 중인 항목 {len(subs)}개:")
    for s in sorted(subs, key=lambda x: x["next_payment_date"]):
        amt = f"${s['amount']:,.2f}" if s["currency"] == "USD" else f"₩{s['amount']:,.0f}"
        memo = f"  ({s['memo']})" if s["memo"] else ""
        print(f"  {s['next_payment_date']}  {s['name']:<16} {amt}{memo}")

    due_today, due_tomorrow = get_due_today_and_tomorrow()
    print(f"\n오늘 결제: {[s['name'] for s in due_today] or '없음'}")
    print(f"내일 결제: {[s['name'] for s in due_tomorrow] or '없음'}")

    unused = get_unused_subscriptions(3)
    print(f"\n3개월 이상 미이용 추정 {len(unused)}건:")
    for s in unused:
        reason = "마지막 이용일" if s["unused_reason"] == "last_used" else "이용 확인 이력 없음(시작일 기준)"
        print(f"  {s['name']:<16} {s['months_since']}개월째 ({reason}: {s['reference_date']})")
