"""
텔레그램 발송 래퍼.

환경변수:
  TELEGRAM_BOT_TOKEN - @BotFather에서 발급받은 봇 토큰
  TELEGRAM_CHAT_ID   - getUpdates로 확인한 chat_id
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


def send_message(text: str, parse_mode: str = "Markdown") -> dict:
    """
    텔레그램으로 메시지 1건을 발송한다.

    Args:
        text: 발송할 메시지 본문
        parse_mode: "Markdown" | "HTML" | None

    Returns:
        텔레그램 API 응답(JSON)을 dict로 반환

    Raises:
        TelegramConfigError: 토큰/chat_id 미설정 시
        requests.HTTPError: 텔레그램 API가 에러를 반환한 경우
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
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    # 단독 실행 시 연결 테스트용 메시지 발송
    result = send_message("✅ CostPing 텔레그램 연결 테스트")
    print(result)
