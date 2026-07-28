"""
텔레그램 발송 래퍼.

환경변수:
  TELEGRAM_BOT_TOKEN - @BotFather에서 발급받은 봇 토큰
  TELEGRAM_CHAT_ID   - getUpdates로 확인한 chat_id

[2단계 세션 2] D-3 결제 알림에 원탭 버튼(인라인 키보드)을 붙이고, 다음날 크론에서
버튼 응답(callback_query)을 읽어오는 기능을 추가했다. 별도 서버 없이(웹훅 없이)
매일 08:00 cron에서 getUpdates를 폴링하는 방식이라 응답 반영에 최대 24시간
지연이 있을 수 있다 — "no server" 아키텍처를 지키기 위한 의도적인 트레이드오프.

상태 저장이 필요 없는 이유: 텔레그램 서버가 각 업데이트를 "확인(offset으로 ack)"
되기 전까지 계속 들고 있어준다. 그래서 매번 offset 없이(=0) getUpdates를 부르면
"아직 확인 안 된" 업데이트가 전부 돌아오고, 처리 후 offset=max_update_id+1로
한 번 더 불러주면 그 시점까지의 업데이트는 텔레그램 쪽에서 확인 처리되어 다음
번엔 다시 안 돌아온다. 그래서 우리 쪽에 마지막 offset을 파일/DB로 저장해둘
필요가 없다 (repo에 상태 파일을 두지 않는다는 프로젝트 원칙과도 맞음).
"""

import os
import requests

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramConfigError(RuntimeError):
    """필수 환경변수가 없을 때 발생."""


def _get_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise TelegramConfigError(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다."
        )
    return token, chat_id


def send_message(text: str, parse_mode: str | None = None) -> dict:
    """
    텔레그램으로 메시지 1건을 발송한다.

    Args:
        text: 발송할 메시지 본문
        parse_mode: None(기본, plain text) | "Markdown" | "HTML"
            기본값을 plain text로 둔 이유: 구독명/모델명/에러 메시지 등에
            _, *, ` 같은 문자가 섞이면 Markdown 파싱이 깨져 400 에러가 나기 때문.
            굵게/기울임 같은 서식이 꼭 필요할 때만 명시적으로 켜서 쓸 것.

    Returns:
        텔레그램 API 응답(JSON)을 dict로 반환

    Raises:
        TelegramConfigError: 토큰/chat_id 미설정 시
        RuntimeError: 텔레그램 API가 에러를 반환한 경우 (봇 토큰이 노출되지 않도록
            URL 대신 상태 코드/응답 본문만 담아서 재발생시킨다)
    """
    token, chat_id = _get_config()
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    resp = requests.post(url, json=payload, timeout=10)
    if not resp.ok:
        # resp.raise_for_status()를 그대로 쓰면 예외 메시지에 위 url(토큰 포함)이
        # 그대로 노출되므로, 여기서 상태 코드/본문만 담아 안전하게 재발생시킨다.
        raise RuntimeError(
            f"텔레그램 발송 실패 (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()


def send_message_with_keyboard(text: str, keyboard: list[list[dict]]) -> dict:
    """
    [2단계 세션 2] 인라인 키보드(버튼)가 달린 메시지를 발송한다.

    Args:
        text: 발송할 메시지 본문
        keyboard: 버튼 행(row)의 리스트. 각 행은 다시 버튼 dict의 리스트이며,
            버튼 dict는 {"text": "표시 문구", "callback_data": "내부 식별 문자열"}
            형태여야 한다 (텔레그램 InlineKeyboardButton 규격).
            예) [[{"text": "✅ Netflix 씀", "callback_data": "lu|<page_id>|y"},
                 {"text": "❌ Netflix 안씀", "callback_data": "lu|<page_id>|n"}]]

    Returns/Raises: send_message와 동일.
    """
    token, chat_id = _get_config()
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    resp = requests.post(url, json=payload, timeout=10)
    if not resp.ok:
        raise RuntimeError(
            f"텔레그램 발송 실패 (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()


def get_updates(offset: int | None = None) -> list[dict]:
    """
    [2단계 세션 2] 아직 확인(ack)되지 않은 업데이트를 전부 가져온다.
    offset을 안 주면(None) 확인 안 된 것 전부, offset=N을 주면 update_id가
    N 미만인 것들을 확인 처리하면서 N 이상인 것들을 반환한다 (텔레그램 표준 동작).

    timeout=0(롱폴링 없이 즉시 응답)으로 고정 — 크론 잡은 매일 한 번 짧게 돌고
    끝나야 하므로 응답을 기다리며 대기할 이유가 없다.
    """
    token, _ = _get_config()
    url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates"
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(url, params=params, timeout=15)
    if not resp.ok:
        raise RuntimeError(
            f"텔레그램 getUpdates 실패 (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    return resp.json().get("result", [])


def confirm_updates(last_update_id: int) -> None:
    """[2단계 세션 2] update_id가 last_update_id 이하인 업데이트를 전부 확인(ack)
    처리한다 — 별도 offset 저장소 없이도 다음 실행에서 같은 업데이트가 다시
    돌아오지 않게 하기 위함 (get_updates 상단 설명 참고)."""
    get_updates(offset=last_update_id + 1)


if __name__ == "__main__":
    # 단독 실행 시 연결 테스트용 메시지 발송
    result = send_message("✅ CostPing 텔레그램 연결 테스트")
    print(result)
