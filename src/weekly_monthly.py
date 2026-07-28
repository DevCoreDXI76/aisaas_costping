"""
주간/월간 요약 리포트 — 날짜 범위 계산 (Asia/Seoul 기준으로 호출한다고 가정).

DB 없이 매일 실행되는 daily-report.yml 하나만 그대로 재사용한다:
- 오늘이 월요일이면 리포트에 "지난주(월~일) 요약" 섹션을 추가로 붙인다.
- 오늘이 매월 1일이면 리포트에 "지난달 요약" 섹션을 추가로 붙인다.

비용 API(cost_report / costs)는 원하는 기간을 그때그때 조회할 수 있으므로,
별도 저장소 없이 실행 시점에 지난주/지난달 범위만 계산해서 바로 질의하면 된다.
"""

from datetime import date, timedelta


def is_monday(today: date) -> bool:
    return today.weekday() == 0


def is_first_of_month(today: date) -> bool:
    return today.day == 1


def last_week_range(today: date) -> tuple[date, date]:
    """오늘 기준 '지난주 월요일 ~ 이번주 월요일 직전(일요일)' 범위를
    (시작일, 종료일 다음날(exclusive)) 형태로 반환한다.
    예: today=2026-08-03(월) -> (2026-07-27, 2026-08-03) = 7/27(월)~8/2(일)."""
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    return last_monday, this_monday


def last_month_range(today: date) -> tuple[date, date]:
    """오늘 기준 지난달 범위를 (시작일, 종료일 다음날(exclusive)) 형태로 반환한다.
    예: today=2026-08-01 -> (2026-07-01, 2026-08-01) = 7월 한 달."""
    this_month_start = today.replace(day=1)
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)
    return last_month_start, this_month_start


if __name__ == "__main__":
    monday = date(2026, 8, 3)
    print("is_monday:", is_monday(monday), "range:", last_week_range(monday))
    first = date(2026, 8, 1)
    print("is_first_of_month:", is_first_of_month(first), "range:", last_month_range(first))
    jan_first = date(2027, 1, 1)
    print("연도 경계:", last_month_range(jan_first))
