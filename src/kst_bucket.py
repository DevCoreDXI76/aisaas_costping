"""
Usage API의 UTC 시간 단위(1h) 버킷을 KST 캘린더 날짜 기준으로 다루기 위한 순수 날짜/시간 유틸.

핵심 아이디어: Cost API(cost_report)는 bucket_width가 하루(1d) 단위만 지원하고
그 하루는 UTC 자정에 고정되어 있어 KST로 옮길 수 없다(2단계 세션 1 조사 결과,
PLAN.md 참고). 반면 Usage API(usage_report/messages 등)는 1시간(1h) 단위 버킷을
지원하므로, "KST 기준 어제 하루"에 정확히 대응하는 UTC 24시간 구간을 계산해서
그 구간만 질의하면 별도 사후 집계 없이도 KST 날짜 경계가 정확히 맞는다.

한국은 서머타임이 없어 KST는 연중 고정 UTC+9이므로 이 계산은 DST 예외를 신경 쓸
필요가 없다.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_day_to_utc_range(kst_date: date) -> tuple[datetime, datetime]:
    """
    주어진 KST 캘린더 날짜(자정~다음날 자정)에 정확히 대응하는 UTC 구간을
    (시작, 끝(미포함)) tz-aware datetime 쌍으로 반환한다.

    예: kst_date=2026-07-28 (KST 7/28 00:00~7/29 00:00)
        -> (2026-07-27T15:00:00+00:00, 2026-07-28T15:00:00+00:00)

    Usage API에 이 구간을 bucket_width=1h로 질의하면 정확히 24개의 시간 버킷이
    돌아오고, 그 전체 합이 곧 "KST 기준 그 하루"의 정밀한 사용량/비용이 된다.
    """
    start_kst = datetime(kst_date.year, kst_date.month, kst_date.day, tzinfo=KST)
    end_kst = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc), end_kst.astimezone(timezone.utc)


def kst_range_to_utc_range(start_kst_date: date, end_kst_date_exclusive: date) -> tuple[datetime, datetime]:
    """
    [start_kst_date, end_kst_date_exclusive) KST 날짜 범위(여러 날)에 대응하는
    UTC 구간을 반환한다. 주간/월간 KST 정밀 요약에 사용 예정(2단계 세션 C).
    """
    start_utc, _ = kst_day_to_utc_range(start_kst_date)
    _, end_utc = kst_day_to_utc_range(end_kst_date_exclusive - timedelta(days=1))
    return start_utc, end_utc


def kst_date_for_utc_hour(hour_start_utc: datetime) -> date:
    """UTC 시간 버킷의 시작 시각을 KST로 변환해 그 시각이 속하는 KST 캘린더 날짜를 반환한다.
    (하루보다 넓은 구간을 받아와 KST 날짜별로 다시 쪼개야 할 때 사용)"""
    if hour_start_utc.tzinfo is None:
        hour_start_utc = hour_start_utc.replace(tzinfo=timezone.utc)
    return hour_start_utc.astimezone(KST).date()


def group_records_by_kst_date(records: list[dict], hour_key: str = "hour_start_utc") -> dict[date, list[dict]]:
    """레코드 리스트(각 레코드는 hour_key에 UTC 시각을 담고 있어야 함)를
    KST 캘린더 날짜별로 묶어서 반환한다."""
    buckets: dict[date, list[dict]] = {}
    for r in records:
        d = kst_date_for_utc_hour(r[hour_key])
        buckets.setdefault(d, []).append(r)
    return buckets


if __name__ == "__main__":
    start, end = kst_day_to_utc_range(date(2026, 7, 28))
    print("KST 2026-07-28 하루 =>", start.isoformat(), "~", end.isoformat())

    # DST 없는 한국 특성 확인 (연중 고정 오프셋)
    for d in (date(2026, 1, 15), date(2026, 7, 28), date(2026, 12, 31)):
        s, e = kst_day_to_utc_range(d)
        print(d, "->", s.hour, "~", e.hour if e.date() == s.date() else f"{e.hour}(다음날)")
