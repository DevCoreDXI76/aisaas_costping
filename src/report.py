"""
CostPing 진입점 (2단계 세션 3 버전: MVP + 월 예산 초과 경고 + 주간/월간 요약
+ KST 정밀 어제 비용(실험적) + Notion 구독 연동).

로컬 테스트:
  1) 프로젝트 루트에 .env 파일을 만들고 아래처럼 채운다 (커밋 금지, .gitignore에 포함됨)
       TELEGRAM_BOT_TOKEN=...
       TELEGRAM_CHAT_ID=...
       ANTHROPIC_ADMIN_KEY=...
       OPENAI_ADMIN_KEY=...
       NOTION_API_KEY=...
  2) python src/report.py 실행

GitHub Actions에서는 .env 없이 Secrets → env로 주입되므로 그대로 동작한다.

새로 추가된 것 (2단계):
  - budget.json 기반 월 예산 초과 경고 (src/budget.py)
  - 매주 월요일에 "지난주 요약", 매월 1일에 "지난달 요약" 섹션 자동 추가
    (src/weekly_monthly.py) — 별도 cron 없이 기존 매일 08:00 실행에 조건부로 얹는다.
  - "🧪 KST 정밀 어제 비용 (실험적)" 섹션 (세션 2C): 기존 "어제 API 비용 (UTC 기준)"
    섹션을 대체하는 게 아니라 그 아래에 나란히 추가된다. Usage API + prices.json
    간소화판 단가표로 계산하므로 cost_report 기반 숫자보다 근사치가 섞일 수 있고,
    그 경우 경고 문구가 같이 표시된다(src/providers/{anthropic_cost,openai_cost}.py의
    get_kst_precise_cost_for_day). 두 제공사 다 조회에 실패하면 이 섹션 자체가
    조용히 생략된다 — 실험 기능 하나 때문에 핵심 리포트에 에러를 노출하지 않기 위함.
  - 구독 결제 알림의 소스를 Notion "구독 중" 데이터소스로 전환 (2단계 세션 3):
    기존 subscriptions.json 수동 관리를 대체한다. 실제 결제 중인 구독(OTT 포함)과
    상시 어긋나는 문제를 해결하기 위함 — src/providers/notion_subscriptions.py,
    notion_config.json 참고. NOTION_API_KEY 미설정 시 이 섹션만 건너뛰고 나머지는
    정상 발송된다(다른 섹션들과 동일한 방어 패턴).
  - "좀비 구독"(안 쓰는데 계속 결제되는 구독) 방지를 위한 D-3 알림 + 원탭 버튼
    + Notion 기록 자동화 (2단계 세션 3 세션 2): 결제 3일 전에 "이번 달 쓰셨나요?"
    를 인라인 버튼(✅/❌)과 함께 물어보고, 사용자가 버튼을 누르면 다음 크론 실행
    때 그 응답을 읽어와 Notion "마지막 이용일" 필드에 반영한다(src/reply_processor.py,
    src/telegram.py의 send_message_with_keyboard/get_updates/confirm_updates,
    src/providers/notion_subscriptions.py의 get_due_in_days/update_last_used_date).
    웹훅 서버 없이 매일 08:00 폴링만으로 처리하기 때문에 응답 반영까지 최대 24시간
    지연될 수 있다 — "서버 없음" 아키텍처를 지키기 위해 의도적으로 감수한 트레이드오프.
  - 매월 1일 "지난달 요약" 아래에 "🧟 3개월 이상 미이용 추정 구독" 하이라이트 추가
    (2단계 세션 3(선택)): "마지막 이용일"(또는 그마저 없으면 결제 시작일)로부터 3개월
    이상 지난 활성 구독을 모아 보여준다. D-3 알림에 응답을 안 하고 넘어가면 놓치기
    쉬운 좀비 구독을 한 달에 한 번 눈에 띄게 정리해서 보여주는 게 목적
    (src/providers/notion_subscriptions.py의 get_unused_subscriptions).
"""

import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# src/ 를 sys.path에 추가해 telegram, budget, providers.* 모듈을 바로 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

import telegram  # noqa: E402
import budget  # noqa: E402
import weekly_monthly  # noqa: E402
import reply_processor  # noqa: E402
from providers import anthropic_cost, openai_cost, notion_subscriptions  # noqa: E402

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


def _fetch_provider_cost(provider_module, config_error_cls):
    """제공사 비용 조회 헬퍼. 성공하면 (yesterday_usd, month_usd, None) 실패하면 (0, 0, 경고문구)를 반환.
    한 제공사가 실패해도 나머지는 정상 발송되도록 예외를 여기서 흡수한다."""
    try:
        yesterday_usd, month_usd = provider_module.get_yesterday_and_month_to_date_cost()
        return yesterday_usd, month_usd, None
    except config_error_cls as e:
        return 0.0, 0.0, f"건너뜀: {e}"
    except Exception as e:
        return 0.0, 0.0, f"조회 실패: {e}"


def _fetch_provider_range_cost(provider_module, config_error_cls, start, end):
    """제공사 기간별 비용 조회 헬퍼 (주간/월간 요약용). 성공하면 (cost_usd, None),
    실패하면 (0, 경고문구)를 반환한다. _fetch_provider_cost와 동일한 방어 패턴."""
    try:
        return provider_module.get_cost_for_range(start, end), None
    except config_error_cls as e:
        return 0.0, f"건너뜀: {e}"
    except Exception as e:
        return 0.0, f"조회 실패: {e}"


def build_cost_section() -> tuple[str, float | None, float | None]:
    """
    Anthropic + OpenAI 비용 섹션. 한쪽 API가 실패해도 나머지는 정상 표시되고,
    전체 리포트(구독 알림 포함)는 죽지 않는다.

    Returns:
        (섹션 텍스트, 어제 하루 비용 USD 또는 None, 이번 달 누적 비용 USD 또는 None)
        두 제공사 모두 실패하면 둘 다 None(계산 불가). yesterday_total/month_total은
        budget.py의 하루/월 예산 경고 계산에 재사용한다.
    """
    a_yesterday, a_month, a_error = _fetch_provider_cost(
        anthropic_cost, anthropic_cost.AnthropicConfigError
    )
    o_yesterday, o_month, o_error = _fetch_provider_cost(
        openai_cost, openai_cost.OpenAIConfigError
    )

    lines = ["어제 API 비용 (UTC 기준)"]
    if a_error:
        lines.append(f"· Anthropic  ⚠️ {a_error}")
    else:
        lines.append(f"· Anthropic  {format_currency(a_yesterday, 'USD')}")
    if o_error:
        lines.append(f"· OpenAI     ⚠️ {o_error}")
    else:
        lines.append(f"· OpenAI     {format_currency(o_yesterday, 'USD')}")

    yesterday_total = None
    month_total = None
    if not a_error or not o_error:
        yesterday_total = a_yesterday + o_yesterday
        month_total = a_month + o_month
        note = ""
        if a_error or o_error:
            note = " (일부 제공사 조회 실패, 부분 합계)"
        lines.append(f"· 합계        {format_currency(yesterday_total, 'USD')}{note}")
        lines.append("")
        lines.append(f"이번 달 누적: {format_currency(month_total, 'USD')}{note}")

    return "\n".join(lines), yesterday_total, month_total


def _build_subscription_section_safe() -> str:
    """
    [2단계 세션 3] Notion "구독 중" 데이터소스가 결제 알림의 단일 소스다
    (기존 subscriptions.json 수동 관리는 폐기 — src/providers/notion_subscriptions.py 참고).
    조회 실패해도(Notion 설정 미완료 포함) 나머지 리포트는 정상 발송되도록 여기서
    예외를 흡수한다 (build_cost_section과 동일한 방어 패턴)."""
    try:
        due_today, due_tomorrow = notion_subscriptions.get_due_today_and_tomorrow()
        return build_subscription_section(due_today, due_tomorrow)
    except notion_subscriptions.NotionConfigError as e:
        return f"⚠️ 구독 정보 조회 건너뜀: {e}"
    except Exception as e:
        return f"⚠️ 구독 정보 조회 실패: {e}"


def _build_reply_processing_section_safe() -> str | None:
    """[2단계 세션 2] 지난 D-3 알림에 달린 텔레그램 버튼 응답을 처리하고 결과를
    요약하는 섹션. 처리할 응답이 없으면 None(섹션 생략) — 매일 뜨는 리포트에
    "처리할 응답 없음"까지 매번 표시하면 노이즈만 늘어나기 때문."""
    try:
        results = reply_processor.process_pending_replies()
    except Exception as e:
        return f"⚠️ 지난 응답 처리 중 오류: {e}"
    if not results:
        return None
    return "\n".join(["📋 지난 답변 처리"] + [f"· {r}" for r in results])


def _build_d3_alert_section_and_keyboard() -> tuple[str | None, list]:
    """
    [2단계 세션 2] 결제 3일 전인 구독에 "이번 달 쓰셨나요?" 인라인 버튼을 붙인다.
    D-day가 아니라 D-3에 물어보는 이유: 해지하고 싶다면 실제 결제가 일어나기 전에
    판단할 시간이 있어야 의미가 있어서다.

    Returns:
        (섹션 텍스트 또는 None, 인라인 키보드 행 리스트) — 대상 구독이 없으면
        (None, []). Notion 조회 실패 시에도 리포트 전체는 죽지 않도록 여기서
        예외를 흡수한다(다른 _..._safe 함수들과 동일한 방어 패턴).
    """
    try:
        due_in_3 = notion_subscriptions.get_due_in_days(3)
    except notion_subscriptions.NotionConfigError:
        return None, []  # Notion 미설정 — 이미 다른 섹션에서 경고가 뜨므로 중복 표시 안 함
    except Exception as e:
        return f"⚠️ D-3 알림 대상 조회 실패: {e}", []

    if not due_in_3:
        return None, []

    lines = ["⏰ 3일 후 결제 예정 — 이번 달 쓰셨나요?"]
    keyboard: list[list[dict]] = []
    for s in due_in_3:
        amt = format_currency(s["amount"], s["currency"])
        lines.append(f"· {s['name']} {amt}")
        keyboard.append(
            [
                {"text": f"✅ {s['name']} 씀", "callback_data": f"lu|{s['page_id']}|y"},
                {"text": f"❌ {s['name']} 안 씀", "callback_data": f"lu|{s['page_id']}|n"},
            ]
        )
    lines.append("(아래 버튼으로 답해주세요 — 응답은 다음 리포트 때 반영돼요)")
    return "\n".join(lines), keyboard


def _build_range_summary_section(title_emoji: str, title: str, label: str, start, end) -> str:
    a_cost, a_error = _fetch_provider_range_cost(
        anthropic_cost, anthropic_cost.AnthropicConfigError, start, end
    )
    o_cost, o_error = _fetch_provider_range_cost(
        openai_cost, openai_cost.OpenAIConfigError, start, end
    )

    lines = [f"{title_emoji} {title} ({label})"]
    if a_error:
        lines.append(f"· Anthropic  ⚠️ {a_error}")
    else:
        lines.append(f"· Anthropic  {format_currency(a_cost, 'USD')}")
    if o_error:
        lines.append(f"· OpenAI     ⚠️ {o_error}")
    else:
        lines.append(f"· OpenAI     {format_currency(o_cost, 'USD')}")

    if not a_error or not o_error:
        note = " (일부 제공사 조회 실패, 부분 합계)" if (a_error or o_error) else ""
        lines.append(f"· 합계        {format_currency(a_cost + o_cost, 'USD')}{note}")

    return "\n".join(lines)


def _build_weekly_section_safe(today) -> str | None:
    """오늘이 월요일이 아니면 None(섹션 생략). 계산 중 예상 못 한 에러가 나도
    전체 리포트는 죽지 않도록 여기서 흡수한다."""
    if not weekly_monthly.is_monday(today):
        return None
    try:
        start, end = weekly_monthly.last_week_range(today)
        last_day = end - timedelta(days=1)
        label = f"{start.month}/{start.day}~{last_day.month}/{last_day.day}"
        return _build_range_summary_section("📅", "지난주 요약", label, start, end)
    except Exception as e:
        return f"⚠️ 지난주 요약 생성 실패: {e}"


def _build_monthly_section_safe(today) -> str | None:
    """오늘이 매월 1일이 아니면 None(섹션 생략)."""
    if not weekly_monthly.is_first_of_month(today):
        return None
    try:
        start, end = weekly_monthly.last_month_range(today)
        label = f"{start.year}년 {start.month}월"
        return _build_range_summary_section("🗓️", "지난달 요약", label, start, end)
    except Exception as e:
        return f"⚠️ 지난달 요약 생성 실패: {e}"


def _build_unused_subscription_highlight_safe(today) -> str | None:
    """
    [2단계 세션 3(선택)] 매월 1일 "지난달 요약" 바로 아래에 "3개월 이상 미이용 추정"
    구독을 하이라이트한다. 좀비 구독(안 쓰는데 계속 결제)을 D-3 알림만으로는 놓치기
    쉬워서(버튼을 안 누르고 넘어가면 그냥 다음 달로 넘어감) 한 달에 한 번 눈에 띄게
    모아서 보여주는 게 목적 — src/providers/notion_subscriptions.get_unused_subscriptions()
    참고(판단 기준: 마지막 이용일 또는 결제 시작일로부터 3개월 경과).

    다른 섹션들과 동일한 방어 패턴: Notion 미설정이면 조용히 생략(이미 구독 섹션에서
    경고가 뜨므로 중복 표시 안 함), 대상이 없으면 생략, 예상 못 한 에러만 경고로 표시.
    """
    if not weekly_monthly.is_first_of_month(today):
        return None
    try:
        unused = notion_subscriptions.get_unused_subscriptions(3)
    except notion_subscriptions.NotionConfigError:
        return None
    except Exception as e:
        return f"⚠️ 미이용 구독 하이라이트 생성 실패: {e}"

    if not unused:
        return None

    lines = ["🧟 3개월 이상 미이용 추정 구독"]
    for s in unused:
        amt = format_currency(s["amount"], s["currency"])
        if s["unused_reason"] == "last_used":
            detail = f"마지막 이용 {s['reference_date']}"
        else:
            detail = f"이용 확인 이력 없음, 구독 시작 {s['reference_date']}"
        lines.append(f"· {s['name']} {amt} — {s['months_since']}개월째 ({detail})")
    lines.append("(해지를 고려해보세요 — Notion에서 상태를 '구독 해지'로 바꾸면 다음부터 제외돼요)")
    return "\n".join(lines)


def _fetch_provider_kst_precise_cost(provider_module, config_error_cls, kst_date):
    """KST 정밀(실험적) 비용 조회 헬퍼. 성공하면 (cost_usd, warnings, None),
    실패하면 (0, [], 경고문구)를 반환한다. _fetch_provider_cost와 동일한 방어 패턴."""
    try:
        cost, warnings = provider_module.get_kst_precise_cost_for_day(kst_date)
        return cost, warnings, None
    except config_error_cls as e:
        return 0.0, [], f"건너뜀: {e}"
    except Exception as e:
        return 0.0, [], f"조회 실패: {e}"


def _build_kst_precise_section_safe(today) -> str | None:
    """
    [실험적, 2단계 세션 2C] KST 정밀 어제 비용 섹션. 기존 "어제 API 비용 (UTC 기준)"
    섹션과 나란히 추가되는 것이지 그걸 대체하는 게 아니다 — Usage API + 간소화판
    단가표(prices.json) 기반이라 cost_report보다 근사치가 섞일 수 있다(경고로 표시됨).

    두 제공사 다 실패하면 이 실험적 섹션 자체를 조용히 생략한다(핵심 UTC 섹션은 이미
    따로 있으니 실험 기능 하나 때문에 눈에 띄는 에러를 보여줄 필요는 없다는 판단).
    """
    try:
        yesterday_kst = today - timedelta(days=1)
        a_cost, a_warnings, a_error = _fetch_provider_kst_precise_cost(
            anthropic_cost, anthropic_cost.AnthropicConfigError, yesterday_kst
        )
        o_cost, o_warnings, o_error = _fetch_provider_kst_precise_cost(
            openai_cost, openai_cost.OpenAIConfigError, yesterday_kst
        )

        if a_error and o_error:
            return None

        lines = [f"🧪 KST 정밀 어제 비용 (실험적, {yesterday_kst})"]
        if a_error:
            lines.append(f"· Anthropic  ⚠️ {a_error}")
        else:
            lines.append(f"· Anthropic  {format_currency(a_cost, 'USD')}")
        if o_error:
            lines.append(f"· OpenAI     ⚠️ {o_error}")
        else:
            lines.append(f"· OpenAI     {format_currency(o_cost, 'USD')}")

        note = " (일부 제공사 조회 실패, 부분 합계)" if (a_error or o_error) else ""
        lines.append(f"· 합계        {format_currency(a_cost + o_cost, 'USD')}{note}")

        for w in a_warnings + o_warnings:
            lines.append(f"  ⚠️ {w}")

        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ KST 정밀 비용(실험적) 계산 실패: {e}"


def build_report() -> tuple[str, list]:
    """
    Returns:
        (리포트 본문 텍스트, 인라인 키보드 행 리스트) 튜플. 키보드가 빈 리스트면
        일반 텍스트 메시지로, 아니면(D-3 알림 대상이 있으면) 버튼이 달린 메시지로
        보내야 한다 — main()에서 이 값을 보고 telegram.send_message /
        send_message_with_keyboard 중 하나를 고른다.
    """
    now = datetime.now(KST)
    today = now.date()
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    header = f"📊 CostPing — {now.month}/{now.day}({weekday_kr})"

    cost_text, yesterday_total, month_total = build_cost_section()
    subscription_section = _build_subscription_section_safe()

    sections = [header]

    # [2단계 세션 2] 지난 D-3 알림 응답 처리 결과 — 있으면 맨 위(헤더 바로 다음)에 표시
    reply_text = _build_reply_processing_section_safe()
    if reply_text:
        sections.append(reply_text)

    sections.append(cost_text)

    if yesterday_total is not None or month_total is not None:
        budget_text = budget.build_budget_section_safe(yesterday_total, month_total)
        if budget_text:
            sections.append(budget_text)

    kst_precise_text = _build_kst_precise_section_safe(today)
    if kst_precise_text:
        sections.append(kst_precise_text)

    sections.append(subscription_section)

    d3_text, keyboard = _build_d3_alert_section_and_keyboard()
    if d3_text:
        sections.append(d3_text)

    weekly_text = _build_weekly_section_safe(today)
    if weekly_text:
        sections.append(weekly_text)

    monthly_text = _build_monthly_section_safe(today)
    if monthly_text:
        sections.append(monthly_text)

    unused_text = _build_unused_subscription_highlight_safe(today)
    if unused_text:
        sections.append(unused_text)

    return "\n\n".join(sections), keyboard


def main() -> None:
    _load_dotenv_if_present()
    try:
        message, keyboard = build_report()
        if keyboard:
            telegram.send_message_with_keyboard(message, keyboard)
        else:
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
