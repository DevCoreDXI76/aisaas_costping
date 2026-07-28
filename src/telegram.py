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


if __name__ == "__main__":
    # 단독 실행 시 연결 테스트용 메시지 발송
    result = send_message("✅ CostPing 텔레그램 연결 테스트")
    print(result)
