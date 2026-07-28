"""
[2단계 세션 2] D-3 결제 알림에 붙인 텔레그램 인라인 버튼(✅ 이번 달 씀 / ❌ 안 씀)
응답을 읽어서 Notion "마지막 이용일" 필드에 반영하는 모듈.

동작 방식 (요약):
  1. telegram.get_updates()로 아직 확인 안 된 업데이트를 전부 가져온다.
  2. callback_data가 "lu|<page_id>|y" 또는 "lu|<page_id>|n" 형식인 것만 처리한다
     (다른 봇 명령/업데이트가 섞여 있어도 무시).
  3. "y"(이번 달 씀)면 notion_subscriptions.update_last_used_date()로 오늘 날짜를
     기록한다. "n"(안 씀)은 아무것도 갱신하지 않는다 — 필드가 그대로 남아 있어야
     나중(세션 3)에 "N개월째 이용 안 함"을 판단할 수 있기 때문.
  4. 처리한 업데이트 중 가장 큰 update_id로 confirm_updates()를 호출해 텔레그램
     서버에 확인 처리시킨다. 이게 우리 쪽의 유일한 "상태 저장"이고, 텔레그램
     서버가 대신 들고 있어주므로 이 프로젝트에 별도 DB/상태 파일이 필요 없다.

이 모듈은 daily-report 크론이 돌 때마다(익일 포함) 매번 호출된다. D-3 알림을
보낸 당일 사람이 바로 누르지 않아도, 다음날/그다음날 크론이 돌 때 결국 처리되므로
최대 지연은 "크론 주기(하루)"이고, 이건 세션 2 브레인스토밍에서 사용자가 명시적으로
선택한 트레이드오프다("지연 허용").
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import telegram
from providers import notion_subscriptions

KST = ZoneInfo("Asia/Seoul")

CALLBACK_PREFIX = "lu"  # last-used 응답 콜백 데이터 접두사


def _name_lookup() -> dict[str, str]:
    """page_id -> 구독 이름 매핑. 조회 실패(설정 미완료 등) 시 빈 dict를 반환하고
    호출부는 이름 대신 page_id를 그대로 표시한다 (기능이 죽지 않게)."""
    try:
        subs = notion_subscriptions.fetch_active_subscriptions()
        return {s["page_id"]: s["name"] for s in subs if s.get("page_id")}
    except Exception:
        return {}


def process_pending_replies() -> list[str]:
    """
    확인 안 된 텔레그램 응답을 전부 처리하고, 사람이 읽을 결과 문구 리스트를 반환한다.
    처리할 응답이 하나도 없으면 빈 리스트를 반환한다(이 경우 리포트에 섹션 자체를
    추가하지 않는 게 자연스럽다 — report.py에서 빈 리스트를 그렇게 다룬다).
    """
    try:
        updates = telegram.get_updates()
    except telegram.TelegramConfigError:
        return []  # 텔레그램 미설정 시 나머지 리포트는 그대로 진행 (report.py에서 별도 처리)
    except Exception as e:
        return [f"⚠️ 텔레그램 응답 확인 실패: {e}"]

    if not updates:
        return []

    names = None  # 실제로 처리할 응답이 있을 때만 조회 (불필요한 Notion 호출 방지)
    results: list[str] = []
    max_update_id = None
    today = datetime.now(KST).date()

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)

        callback = update.get("callback_query")
        if not callback:
            continue
        data = callback.get("data", "")
        parts = data.split("|")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            continue

        _, page_id, answer = parts
        if names is None:
            names = _name_lookup()
        label = names.get(page_id, page_id)

        if answer == "y":
            try:
                notion_subscriptions.update_last_used_date(page_id, today)
                results.append(f"✅ {label} — 이번 달 사용함으로 기록했어요")
            except Exception as e:
                results.append(f"⚠️ {label} 응답 반영 실패: {e}")
        elif answer == "n":
            results.append(f"➖ {label} — 이번 달 안 씀으로 확인 (마지막 이용일 변경 없음)")
        # answer가 y/n이 아닌 값이면 형식 오류로 보고 조용히 무시

    if max_update_id is not None:
        try:
            telegram.confirm_updates(max_update_id)
        except Exception:
            pass  # 확인 처리 실패해도 다음 실행에서 다시 시도되니 리포트 발송을 막지 않는다

    return results
