"""
구독 결제일 계산 (Asia/Seoul 기준).

subscriptions.json 스키마:
[
  {
    "name": "구독명",
    "amount": 20,          # 월 결제 금액 (숫자)
    "currency": "USD",     # 통화 코드
    "billing_day": 15,     # 매달 결제일 (1~31)
    "memo": ""              # 선택, 비워둬도 됨
  },
  ...
]

billing_day가 해당 월의 마지막 날보다 크면(예: 31일인데 2월/4월/6월/9월/11월인 경우)
그 달의 마지막 날에 결제되는 것으로 간주한다.
"""

import calendar
import json
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

DEFAULT_SUBSCRIPTIONS_PATH = Path(__file__).resolve().parent.parent / "subscriptions.json"


def load_subscriptions(path: Path = DEFAULT_SUBSCRIPTIONS_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _effective_billing_date(billing_day: int, year: int, month: int) -> int:
    """해당 연/월에서 billing_day가 실제로 결제되는 '일'을 반환 (말일 보정)."""
    last_day_of_month = calendar.monthrange(year, month)[1]
    return min(billing_day, last_day_of_month)


def is_due_on(subscription: dict, target: date) -> bool:
    billing_day = subscription["billing_day"]
    effective_day = _effective_billing_date(billing_day, target.year, target.month)
    return effective_day == target.day


def due_on(subscriptions: list[dict], target: date) -> list[dict]:
    return [s for s in subscriptions if is_due_on(s, target)]


def _now_kst():
    from datetime import datetime

    return datetime.now(KST)


def today_kst() -> date:
    return _now_kst().date()


def get_due_today_and_tomorrow(subscriptions: list[dict]) -> tuple[list[dict], list[dict]]:
    """(오늘 결제 예정, 내일 결제 예정) 튜플을 반환한다."""
    today = _now_kst().date()
    tomorrow = today + timedelta(days=1)
    return due_on(subscriptions, today), due_on(subscriptions, tomorrow)


if __name__ == "__main__":
    subs = load_subscriptions()
    due_today, due_tomorrow = get_due_today_and_tomorrow(subs)
    print("오늘 결제:", due_today)
    print("내일 결제:", due_tomorrow)
