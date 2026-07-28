"""
CostPing 진입점 (세션 2 버전: 구독 알림 + Anthropic 비용).

OpenAI 비용 수집은 세션 3에서 이 파일에 추가될 예정.

로컬 테스트:
  1) 프로젝트 루트에 .env 파일을 만들고 아래처럼 채운다 (커밋 금지, .gitignore에 포함됨)
       TELEGRAM_BOT_TOKEN=...
       TELEGRAM_CHAT_ID=...
       ANTHROPIC_ADMIN_KEY=...
  2) python src/report.py 실행

GitHub Actions에서는 .env 없이 Secrets → env로 주입되므로 그대로 동작한다.
"""

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# src/ 를 sys.path에 추가해 telegram, subscriptions, providers.* 모듈을 바로 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

import telegram  # noqa: E402
import subscriptions as subs_mod  # noqa: E402
from providers import anthropic_cost  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def _load_dotenv_if_present() -> None:
    """로컬 개발 편의용. .env 파일이 있으면 os.environ에 채워 넣는다.
    표준 라이브러리만 사용(추가 패키지 설치 불필요)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        os.environ.setdefault(key, value)


def format_currency(amount: float, currency: str) -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    if currency == "KRW":
        return f"₩{amount:,.0f}"
    return f"{amount:,.2f} {currency}"


def build_subscription_section(due_today: list[dict], due_tomorrow: list[dict]) -> str:
    if not due_today and not due_tomorrow:
        return "💳 결제 예정\n· 오늘/내일 결제 예정 구독 없음"

    lines = ["💳 결제 예정"]
    for s in due_today:
        lines.append(f"· 오늘: {s['name']} {format_currency(s['amount'], s['currency'])}")
    for s in due_tomorrow:
        lines.append(f"· 내일: {s['name']} {format_currency(s['amount'], s['currency'])}")
    return "\n".join(lines)


def build_cost_section() -> str:
    """
    Anthropic 비용 섹션. API 호출이 실패해도 전체 리포트가 죽지 않도록
    여기서 예외를 잡고 실패 문구를 대신 넣는다 (OpenAI는 세션 3에서 합류 예정).
    """
    try:
        yesterday_usd, month_to_date_usd = anthropic_cost.get_yesterday_and_month_to_date_cost()
    except anthropic_cost.AnthropicConfigError as e:
        return f"⚠️ API 비용 조회 건너뜀: {e}"
    except Exception as e:
        return f"⚠️ Anthropic 비용 조회 실패: {e}"

    lines = [
        "어제 API 비용 (UTC 기준)",
        f"· Anthropic  {format_currency(yesterday_usd, 'USD')}",
        "",
        f"이번 달 누적: {format_currency(month_to_date_usd, 'USD')} (Anthropic만)",
    ]
    return "\n".join(lines)


def build_report() -> str:
    now = datetime.now(KST)
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    header = f"📊 CostPing — {now.month}/{now.day}({weekday_kr})"

    cost_section = build_cost_section()

    subs = subs_mod.load_subscriptions()
    due_today, due_tomorrow = subs_mod.get_due_today_and_tomorrow(subs)
    subscription_section = build_subscription_section(due_today, due_tomorrow)

    # 세션 3에서 여기 OpenAI 비용이 cost_section에 합류할 예정
    return f"{header}\n\n{cost_section}\n\n{subscription_section}"


def main() -> None:
    _load_dotenv_if_present()
    try:
        message = build_report()
        telegram.send_message(message)
        print("발송 완료:\n" + message)
    except telegram.TelegramConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # 조용히 죽지 않기: 무슨 일이 있어도 에러를 텔레그램으로도 알린다
        err_text = traceback.format_exc()
        print(err_text, file=sys.stderr)
        try:
            telegram.send_message(f"⚠️ CostPing 실행 중 오류 발생\n```\n{err_text[-500:]}\n```")
        except Exception:
            pass  # 텔레그램 발송 자체가 실패하면 더 할 수 있는 게 없음
        sys.exit(1)


if __name__ == "__main__":
    main()
