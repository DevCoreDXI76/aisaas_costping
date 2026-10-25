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
     - --dry-run: 텔레그램 발송 없이 콘솔 출력만(지난 D-3 버튼 응답 처리도 건너뜀 —
       로컬 확인만으로 실제 응답이 소비돼 다음 cron에서 사라지지 않게 하기 위함)
     - COSTPING_FORCE_MONTHLY=1: 오늘이 1일이 아니어도 월간 섹션(지난달 요약 +
       회사/개인 구독) 생성. Actions에서는 workflow_dispatch의 force_monthly 입력으로 켠다.

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
  - 매월 1일 "지난달 요약" 아래에 회사/개인 구독 월간 점검 섹션 (개발 세션 1, 기존
    "🧟 3개월 이상 미이용 추정 구독" 하이라이트를 대체): Notion "신용카드"가 법인카드인
    구독은 "💼 회사 월 고정비 · 월간 점검"(월 환산 원화 합계 + 이용 미확인 표시)으로,
    나머지는 "🎬 개인 구독(참고)"로 나눠 보여준다. 원화 환산 환율은 budget.json의 usd_krw.
"""

import argparse
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
COMPANY_CARD = "법인카드"  # Notion "신용카드" 값 중 회사 월 고정비로 분류할 값


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


def build_cost_section() -> tuple[str, float | None, float | None, dict[str, float | None]]:
    """
    Anthropic + OpenAI 비용 섹션. 한쪽 API가 실패해도 나머지는 정상 표시되고,
    전체 리포트(구독 알림 포함)는 죽지 않는다.

    Returns:
        (섹션 텍스트, 어제 하루 비용 USD 또는 None, 이번 달 누적 비용 USD 또는 None,
        {제공사명: 어제 비용 USD 또는 None(조회 실패)})
        두 제공사 모두 실패하면 둘 다 None(계산 불가). yesterday_total/month_total은
        budget.py의 하루/월 예산 경고 계산에, 제공사별 어제 비용은 프로젝트별 섹션의
        '미귀속' 계산에 재사용한다(같은 cost_report 결과라 추가 API 호출 없음).
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

    provider_yesterday = {
        "Anthropic": None if a_error else a_yesterday,
        "OpenAI": None if o_error else o_yesterday,
    }
    return "\n".join(lines), yesterday_total, month_total, provider_yesterday


PROJECT_SECTION_MAX_ROWS = 5  # 비용 상위 N개 프로젝트만 표시, 나머지는 "기타"로 묶음
MESSAGE_SAFE_LIMIT = 3800  # 텔레그램 4096자 제한에 여유를 둔 상한(넘으면 프로젝트 줄을 줄임)
MIN_SHOWN_USD = 0.01  # 이보다 작은 줄(프로젝트·키 없음·미귀속)은 숨김


def _fetch_provider_projects(provider_module, config_error_cls) -> dict:
    """제공사 프로젝트별 비용 조회 헬퍼. 다른 _fetch_* 헬퍼와 같은 방어 패턴:
    키 미설정이면 skipped(위 합계 섹션에서 이미 "건너뜀"이 표시되므로 여기선 조용히 생략),
    그 밖의 예외는 error 문구로 돌려준다."""
    try:
        yesterday_rows, month_rows = provider_module.get_yesterday_and_month_to_date_cost_by_project()
        return {"yesterday": yesterday_rows, "month": month_rows, "error": None, "skipped": False}
    except config_error_cls:
        return {"yesterday": [], "month": [], "error": None, "skipped": True}
    except Exception as e:
        return {"yesterday": [], "month": [], "error": str(e)[:150], "skipped": False}


def fetch_project_costs() -> dict[str, dict]:
    """{제공사명: _fetch_provider_projects 결과}. 한 제공사가 실패해도 다른 쪽은 그대로 쓴다."""
    return {
        "Anthropic": _fetch_provider_projects(anthropic_cost, anthropic_cost.AnthropicConfigError),
        "OpenAI": _fetch_provider_projects(openai_cost, openai_cost.OpenAIConfigError),
    }


def _short_model_name(model: str) -> str:
    """'claude-sonnet-5' / 'claude-haiku-4-5-20251001' -> 'Sonnet' / 'Haiku'. OpenAI 등은 그대로."""
    name = model.removeprefix("claude-")
    for family in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if name.startswith(family):
            return family.capitalize()
    return model


def _format_model_shares(models: dict[str, float], total: float, top_n: int = 3) -> str:
    """{모델: USD} -> 'Sonnet 61% · Haiku 39%' (모델 계열별로 합치고 1% 미만은 생략)."""
    if total <= 0:
        return ""
    by_family: dict[str, float] = {}
    for model, usd in models.items():
        family = _short_model_name(model)
        by_family[family] = by_family.get(family, 0.0) + usd
    shares = sorted(by_family.items(), key=lambda x: x[1], reverse=True)
    parts = [f"{name} {usd / total * 100:.0f}%" for name, usd in shares if usd / total >= 0.01]
    return " · ".join(parts[:top_n])


def _format_unattributed_line(unattributed: float) -> str | None:
    """미귀속(청구 합계 - 프로젝트별 추정 합계) 줄. ±$0.01 미만이면 None(숨김).
    음수는 단가표가 실제보다 비싸게 잡혀 있다는 신호라 경고로 표시한다."""
    if unattributed >= MIN_SHOWN_USD:
        return f"· 미귀속 {format_currency(unattributed, 'USD')} (단가표 밖 사용분 등)"
    if unattributed <= -MIN_SHOWN_USD:
        return f"⚠️ 추정이 청구액보다 {format_currency(-unattributed, 'USD')} 많음 (prices.json 확인 필요)"
    return None


def build_project_section(
    project_costs: dict[str, dict],
    provider_yesterday: dict[str, float | None],
    max_rows: int = PROJECT_SECTION_MAX_ROWS,
) -> str | None:
    """
    "🗂 프로젝트별 (어제, UTC)" 섹션 텍스트. 조회는 하지 않는 순수 포맷 함수 — 메시지가
    길면 build_report가 max_rows를 줄여 다시 부를 수 있게 분리했다.

    - 어제 비용 상위 max_rows개 프로젝트를 금액 내림차순으로, 나머지(및 $0.01 미만 줄)는 "기타"로
    - 각 줄: 표시명 (제공사) 어제 금액 — 모델 계열 비중 | 이번 달 누적
    - 미귀속 = 제공사별 cost_report 어제 합계 - 프로젝트별 추정 합계. 단가표에 없는 모델·요금
      항목, 키 없이 쓴 사용량 집계 누락분 등이 여기 드러난다. 합계 조회가 실패한 제공사는 계산 제외
    - 두 제공사 다 키 미설정이면 None(섹션 생략)
    """
    if all(p["skipped"] for p in project_costs.values()):
        return None

    errors = {name: p["error"] for name, p in project_costs.items() if p["error"]}
    active = {name: p for name, p in project_costs.items() if not p["error"] and not p["skipped"]}
    if not active:
        detail = " / ".join(f"{name}: {err}" for name, err in errors.items())
        return f"⚠️ 프로젝트별 비용 조회 실패: {detail}"

    rows = []
    unattributed = 0.0
    for provider, p in active.items():
        month_by_project = {r["project"]: r["cost_usd"] for r in p["month"]}
        for r in p["yesterday"]:
            rows.append({**r, "month_usd": month_by_project.get(r["project"], r["cost_usd"])})
        provider_total = provider_yesterday.get(provider)
        if provider_total is not None:
            unattributed += provider_total - sum(r["cost_usd"] for r in p["yesterday"])
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)

    visible = [r for r in rows if r["cost_usd"] >= MIN_SHOWN_USD]
    shown, rest = visible[:max_rows], visible[max_rows:] + [r for r in rows if r["cost_usd"] < MIN_SHOWN_USD]

    lines = ["🗂 프로젝트별 (어제, UTC)"]
    for r in shown:
        shares = _format_model_shares(r["models"], r["cost_usd"])
        shares_text = f" — {shares}" if shares else ""
        lines.append(
            f"· {r['project']} ({r['provider']}) {format_currency(r['cost_usd'], 'USD')}"
            f"{shares_text} | 월 {format_currency(r['month_usd'], 'USD')}"
        )

    rest_usd = sum(r["cost_usd"] for r in rest)
    if rest_usd >= MIN_SHOWN_USD:
        rest_month = sum(r["month_usd"] for r in rest)
        lines.append(
            f"· 기타 {len(rest)}개 {format_currency(rest_usd, 'USD')} | 월 {format_currency(rest_month, 'USD')}"
        )

    unattributed_line = _format_unattributed_line(unattributed)
    if unattributed_line:
        lines.append(unattributed_line)

    if len(lines) == 1:
        lines.append("· 어제 사용 없음")

    for name, err in errors.items():
        lines.append(f"⚠️ {name} 프로젝트별 조회 실패: {err}")

    return "\n".join(lines)


def _safe_builder(build, error_prefix: str):
    """build(max_rows) -> 섹션 텍스트를 감싸, 포맷 중 예상 못 한 에러가 나도 그 섹션만
    "error_prefix: 에러" 한 줄로 바뀌고 리포트는 정상 발송되게 한다. max_rows를 인자로 받는
    이유는 메시지가 길 때 build_report가 조회 없이 줄 수만 줄여 다시 만들 수 있게 하기 위함."""
    def builder(max_rows: int) -> str | None:
        try:
            return build(max_rows)
        except Exception as e:
            return f"{error_prefix}: {str(e)[:150]}"
    return builder


def _telegram_length(text: str) -> int:
    """텔레그램 글자 수 제한은 UTF-16 코드 단위 기준이다(이모지 1개 = 2)."""
    return len(text.encode("utf-16-le")) // 2


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


PROVIDER_LABELS = {"Anthropic": "Anthropic  ", "OpenAI": "OpenAI     "}  # 기존 섹션과 같은 정렬
NEW_THRESHOLD_USD = 0.005  # 지난 기간이 이보다 작으면($0.00으로 표시되는 수준) "신규"
PCT_MIN_PREV_USD = 0.10  # 지난 기간이 이보다 작으면 %는 빼고 금액 증감만(소액 % 과장 방지)


def _fetch_provider_periods(provider_module, config_error_cls, periods) -> dict:
    """주간/월간 요약용 제공사 조회. 합계(cost_report)와 프로젝트별(usage/costs)을 각각 방어해서
    한쪽이 실패해도 다른 쪽은 표시된다. periods = [(이번 기간), (지난 기간)], 각 결과는 같은 순서.
    키 미설정이면 skipped(합계 줄에 "건너뜀"이 이미 표시되므로 프로젝트별 부분은 조용히 생략)."""
    result = {"totals": None, "total_error": None, "projects": None, "project_error": None, "skipped": False}
    try:
        result["totals"] = provider_module.get_cost_for_periods(periods)
    except config_error_cls as e:
        result["total_error"] = f"건너뜀: {e}"
        result["skipped"] = True
        return result
    except Exception as e:
        result["total_error"] = f"조회 실패: {e}"
    try:
        result["projects"] = provider_module.get_cost_by_project_for_periods(periods)
    except config_error_cls:
        result["skipped"] = True
    except Exception as e:
        result["project_error"] = str(e)[:150]
    return result


def fetch_range_costs(start, end, prev_start) -> dict[str, dict]:
    """[start, end) 이번 기간과 [prev_start, start) 지난 기간을 제공사별로 한 번에 조회한다."""
    periods = [(start, end), (prev_start, start)]
    return {
        "Anthropic": _fetch_provider_periods(anthropic_cost, anthropic_cost.AnthropicConfigError, periods),
        "OpenAI": _fetch_provider_periods(openai_cost, openai_cost.OpenAIConfigError, periods),
    }


def _delta_parts(cur: float, prev: float) -> tuple[str, str | None] | None:
    """(증감 텍스트, % 텍스트 또는 None). 지난 기간이 사실상 $0이면 ("신규", None),
    변화가 $0.01 미만이면 None(표시 생략). 지난 기간 $0.10 미만이면 %는 생략."""
    if prev < NEW_THRESHOLD_USD:
        return ("신규", None) if cur >= MIN_SHOWN_USD else None
    diff = cur - prev
    if abs(diff) < MIN_SHOWN_USD:
        return None
    amount = f"{'▲' if diff > 0 else '▼'}{format_currency(abs(diff), 'USD')}"
    pct = f"{diff / prev * 100:+.0f}%" if prev >= PCT_MIN_PREV_USD else None
    return amount, pct


def _format_project_delta(cur: float, prev: float) -> str:
    """프로젝트 줄 뒤에 붙는 증감: ' ▲$0.50 (+23%)' / ' ▲$0.05' / ' (신규)' / ''."""
    parts = _delta_parts(cur, prev)
    if parts is None:
        return ""
    amount, pct = parts
    if amount == "신규":
        return " (신규)"
    return f" {amount} ({pct})" if pct else f" {amount}"


def _range_project_lines(data: dict[str, dict], compare_label: str, max_rows: int) -> list[str]:
    """주간/월간 요약의 "프로젝트별 (전주/전월 대비)" 블록. 일일 섹션과 같은 규칙:
    이번 기간 상위 max_rows개 + "기타", $0.01 미만 줄 숨김, 미귀속(음수는 경고).
    지난 기간 $0.01 이상이었는데 이번 기간 $0.01 미만인 프로젝트는 "(중단)" 줄로 사용 중인
    프로젝트 아래에 두되 max_rows 제한 안에 포함한다."""
    if all(p["skipped"] for p in data.values()):
        return []

    errors = {name: p["project_error"] for name, p in data.items() if p["project_error"]}
    active = {name: p for name, p in data.items() if p["projects"] is not None}
    if not active:
        if not errors:
            return []
        detail = " / ".join(f"{name}: {err}" for name, err in errors.items())
        return [f"⚠️ 프로젝트별 조회 실패: {detail}"]

    entries = []
    unattributed = 0.0
    for provider, p in active.items():
        cur_rows, prev_rows = p["projects"]
        cur_map = {r["project"]: r["cost_usd"] for r in cur_rows}
        prev_map = {r["project"]: r["cost_usd"] for r in prev_rows}
        for project in cur_map.keys() | prev_map.keys():
            entries.append(
                {"project": project, "provider": provider, "cur": cur_map.get(project, 0.0), "prev": prev_map.get(project, 0.0)}
            )
        if p["totals"] is not None:
            unattributed += p["totals"][0] - sum(cur_map.values())

    in_use = sorted((e for e in entries if e["cur"] >= MIN_SHOWN_USD), key=lambda e: e["cur"], reverse=True)
    stopped = sorted(
        (e for e in entries if e["cur"] < MIN_SHOWN_USD and e["prev"] >= MIN_SHOWN_USD),
        key=lambda e: e["prev"], reverse=True,
    )
    tiny = [e for e in entries if e["cur"] < MIN_SHOWN_USD and e["prev"] < MIN_SHOWN_USD]
    candidates = in_use + stopped
    shown = candidates[:max_rows]
    rest = [e for e in candidates[max_rows:] + tiny if e["cur"] > 0]  # 넘친 (중단) 줄은 "기타" 개수에서 제외

    lines = [f"프로젝트별 ({compare_label} 대비)"]
    for e in shown:
        head = f"· {e['project']} ({e['provider']}) {format_currency(e['cur'], 'USD')}"
        if e["cur"] < MIN_SHOWN_USD:
            lines.append(f"{head} ▼{format_currency(e['prev'] - e['cur'], 'USD')} (중단)")
        else:
            lines.append(f"{head}{_format_project_delta(e['cur'], e['prev'])}")

    rest_usd = sum(e["cur"] for e in rest)
    if rest_usd >= MIN_SHOWN_USD:
        lines.append(f"· 기타 {len(rest)}개 {format_currency(rest_usd, 'USD')}")

    unattributed_line = _format_unattributed_line(unattributed)
    if unattributed_line:
        lines.append(unattributed_line)

    if len(lines) == 1:
        lines.append("· 사용 없음")

    for name, err in errors.items():
        lines.append(f"⚠️ {name} 프로젝트별 조회 실패: {err}")
    return lines


def build_range_summary_section(
    title_emoji: str, title: str, label: str, compare_label: str, data: dict[str, dict],
    max_rows: int = PROJECT_SECTION_MAX_ROWS,
) -> str:
    """주간/월간 요약 섹션(조회 없는 순수 포맷 함수). 제공사별 합계, 합계의 지난 기간 대비 증감,
    프로젝트별 블록 순서. 합계 줄은 기존 형식을 유지하고 끝에 "(전주 대비 ▲$x, +y%)"만 붙인다."""
    lines = [f"{title_emoji} {title} ({label})"]
    ok = {}
    for name, p in data.items():
        if p["total_error"]:
            lines.append(f"· {PROVIDER_LABELS[name]}⚠️ {p['total_error']}")
        else:
            lines.append(f"· {PROVIDER_LABELS[name]}{format_currency(p['totals'][0], 'USD')}")
            ok[name] = p["totals"]

    if ok:
        cur = sum(t[0] for t in ok.values())
        prev = sum(t[1] for t in ok.values())
        note = " (일부 제공사 조회 실패, 부분 합계)" if len(ok) < len(data) else ""
        # 부분 합계끼리의 증감은 오해 소지가 있어 생략(프로젝트 줄 증감은 유지)
        parts = None if note else _delta_parts(cur, prev)
        delta = ""
        if parts:
            amount, pct = parts
            delta = f" ({compare_label} 대비 {amount}{', ' + pct if pct else ''})"
        lines.append(f"· 합계        {format_currency(cur, 'USD')}{note}{delta}")

    lines.extend(_range_project_lines(data, compare_label, max_rows))
    return "\n".join(lines)


def _prepare_range_section(title_emoji: str, title: str, label: str, compare_label: str, start, end, prev_start):
    """조회를 한 번 해 두고, max_rows만 바꿔 다시 포맷할 수 있는 builder를 돌려준다."""
    data = fetch_range_costs(start, end, prev_start)
    return _safe_builder(
        lambda max_rows: build_range_summary_section(title_emoji, title, label, compare_label, data, max_rows),
        f"⚠️ {title} 생성 실패",
    )


def _prepare_weekly_section(today, force: bool = False):
    """오늘이 월요일이 아니면(force 제외) None(섹션 생략). 지난주(월~일)와 그 전주를 비교한다.
    날짜 계산·조회 중 예상 못 한 에러가 나도 전체 리포트는 죽지 않도록 여기서 흡수한다."""
    if not (weekly_monthly.is_monday(today) or force):
        return None
    try:
        start, end = weekly_monthly.last_week_range(today)
        last_day = end - timedelta(days=1)
        label = f"{start.month}/{start.day}~{last_day.month}/{last_day.day}"
        return _prepare_range_section("📅", "지난주 요약", label, "전주", start, end, start - timedelta(days=7))
    except Exception as e:
        return lambda max_rows: f"⚠️ 지난주 요약 생성 실패: {e}"


def _prepare_monthly_section(today, force: bool = False):
    """오늘이 매월 1일이 아니면(force 제외) None(섹션 생략). 지난달과 그 전달을 비교한다
    (월 일수 차이는 보정하지 않음)."""
    if not (weekly_monthly.is_first_of_month(today) or force):
        return None
    try:
        start, end = weekly_monthly.last_month_range(today)
        prev_start, _ = weekly_monthly.last_month_range(start)
        label = f"{start.year}년 {start.month}월"
        return _prepare_range_section("🗓️", "지난달 요약", label, "전월", start, end, prev_start)
    except Exception as e:
        return lambda max_rows: f"⚠️ 지난달 요약 생성 실패: {e}"


def _format_usd_compact(amount: float) -> str:
    """$100.00 -> $100, $19.99 -> $19.99 (월간 점검 줄을 짧게 유지하기 위함)."""
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def _format_original_price(s: dict) -> str:
    """월 환산 금액 옆 괄호에 붙일 원래 결제 금액. 원화 월 결제는 환산값과 같으므로 생략."""
    cycle = s.get("cycle") or "월"
    if s["currency"] == "USD":
        return f" ({_format_usd_compact(s['amount'])}/{cycle})"
    if cycle == "연":
        return f" ({format_currency(s['amount'], 'KRW')}/연)"
    return ""


def _build_company_subscription_section(subs: list[dict], today, usd_krw: float) -> str:
    """[개발 세션 1] 섹션 A — 신용카드 == 법인카드. 유지/해지 판단용으로 월 환산 금액
    내림차순 + 마지막 이용일 + 이용 미확인 표시."""
    company = [(notion_subscriptions.monthly_krw(s, usd_krw), s) for s in subs if s["card"] == COMPANY_CARD]
    company.sort(key=lambda x: x[0], reverse=True)
    total = sum(krw for krw, _ in company)

    lines = [
        "💼 회사 월 고정비 · 월간 점검",
        f"월 합계 {format_currency(total, 'KRW')} ({len(company)}건)",
    ]
    if not company:
        lines.append("· 법인카드 구독 없음")
    for krw, s in company:
        last_used = s["last_used_date"]
        used_text = f"마지막 이용 {last_used.month}/{last_used.day}" if last_used else "마지막 이용 기록 없음"
        warn = " ⚠️ 이용 미확인" if notion_subscriptions.is_usage_unconfirmed(s, today, 3) else ""
        lines.append(
            f"· {s['name']} {format_currency(krw, 'KRW')}{_format_original_price(s)} — {used_text}{warn}"
        )
    lines.append("(안 쓰는 건 Notion에서 상태를 '구독 해지'로 바꾸세요)")
    return "\n".join(lines)


def _build_personal_subscription_section(subs: list[dict], usd_krw: float) -> str:
    """[개발 세션 1] 섹션 B — 법인카드가 아닌 구독(참고용, 점검 표시 없음).
    신용카드가 비어 있는 구독은 회사/개인 어느 합계에도 넣지 않고 "❓ 카드 미지정"으로 따로 표시."""
    personal = [
        (notion_subscriptions.monthly_krw(s, usd_krw), s)
        for s in subs
        if s["card"] and s["card"] != COMPANY_CARD
    ]
    personal.sort(key=lambda x: x[0], reverse=True)
    total = sum(krw for krw, _ in personal)

    lines = [
        "🎬 개인 구독(참고)",
        f"월 합계 {format_currency(total, 'KRW')} ({len(personal)}건)",
    ]
    for krw, s in personal:
        lines.append(f"· {s['name']} {format_currency(krw, 'KRW')}")

    unassigned = [s["name"] for s in subs if not s["card"]]
    if unassigned:
        lines.append("")
        lines.append(f"❓ 카드 미지정: {', '.join(unassigned)} (Notion에서 신용카드를 입력하세요)")
    return "\n".join(lines)


def _build_subscription_review_sections_safe(today, force: bool = False) -> list[str]:
    """
    [개발 세션 1] 매월 1일(또는 force) "지난달 요약" 아래에 회사/개인 구독 섹션을 붙인다.
    기존 "🧟 3개월 이상 미이용 추정 구독" 하이라이트를 대체 — 회사 방침상 AI·개발 구독은
    법인카드로 결제하므로, 회사 월 고정비를 정확히 보고 유지/해지를 판단하는 게 목적.

    Notion 조회는 한 번만 하고 두 섹션이 결과를 공유한다(API 호출 최소화).
    다른 섹션들과 동일한 방어 패턴: Notion 미설정이면 조용히 생략(이미 구독 섹션에서
    경고가 뜨므로 중복 표시 안 함), 섹션별로 실패하면 그 섹션만 경고 한 줄로 대체.
    """
    if not (weekly_monthly.is_first_of_month(today) or force):
        return []
    try:
        subs = notion_subscriptions.fetch_active_subscriptions()
    except notion_subscriptions.NotionConfigError:
        return []
    except Exception as e:
        return [f"⚠️ 구독 월간 점검 생성 실패: {e}"]

    usd_krw = budget.load_usd_krw()
    sections = []
    try:
        sections.append(_build_company_subscription_section(subs, today, usd_krw))
    except Exception as e:
        sections.append(f"⚠️ 회사 월 고정비 섹션 생성 실패: {e}")
    try:
        sections.append(_build_personal_subscription_section(subs, usd_krw))
    except Exception as e:
        sections.append(f"⚠️ 개인 구독 섹션 생성 실패: {e}")
    return sections


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


def build_report(
    dry_run: bool = False, force_monthly: bool = False, force_weekly: bool = False
) -> tuple[str, list]:
    """
    Args:
        dry_run: True면 지난 D-3 버튼 응답 처리(텔레그램 getUpdates/확정 + Notion 기록)를
            건너뛴다 — 로컬 확인만으로 실제 응답이 소비되지 않게 하기 위함.
        force_monthly: True면 1일이 아니어도 월간 섹션(지난달 요약 + 회사/개인 구독) 생성.
        force_weekly: True면 월요일이 아니어도 주간 섹션(지난주 요약) 생성.

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

    cost_text, yesterday_total, month_total, provider_yesterday = build_cost_section()
    subscription_section = _build_subscription_section_safe()

    sections = [header]

    # [2단계 세션 2] 지난 D-3 알림 응답 처리 결과 — 있으면 맨 위(헤더 바로 다음)에 표시
    reply_text = None if dry_run else _build_reply_processing_section_safe()
    if reply_text:
        sections.append(reply_text)

    sections.append(cost_text)

    # 프로젝트 줄이 들어가는 섹션들(일일 프로젝트별, 주간, 월간)은 조회를 한 번만 하고 builder로
    # 보관해 둔다 — 메시지가 길면 맨 아래 안전장치에서 줄 수만 줄여 다시 만든다.
    shrinkable: list[tuple[int, object]] = []

    def add_shrinkable(builder) -> None:
        if builder is None:
            return
        text = builder(PROJECT_SECTION_MAX_ROWS)
        if text:
            shrinkable.append((len(sections), builder))
            sections.append(text)

    # 프로젝트별 섹션 — 합계 섹션 바로 아래
    project_costs = fetch_project_costs()
    add_shrinkable(
        _safe_builder(
            lambda max_rows: build_project_section(project_costs, provider_yesterday, max_rows),
            "⚠️ 프로젝트별 비용 조회 실패",
        )
    )

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

    add_shrinkable(_prepare_weekly_section(today, force_weekly))
    add_shrinkable(_prepare_monthly_section(today, force_monthly))

    sections.extend(_build_subscription_review_sections_safe(today, force_monthly))

    # 안전장치: 텔레그램 4096자 제한 전에 모든 프로젝트 목록의 줄을 하나씩 같이 줄인다
    # (넘친 프로젝트는 "기타"로 합쳐짐).
    max_rows = PROJECT_SECTION_MAX_ROWS
    while shrinkable and max_rows > 0 and _telegram_length("\n\n".join(sections)) > MESSAGE_SAFE_LIMIT:
        max_rows -= 1
        for index, builder in shrinkable:
            sections[index] = builder(max_rows) or ""

    return "\n\n".join(sections), keyboard


def main() -> None:
    # Windows 기본 콘솔(cp949)에서 이모지 print가 UnicodeEncodeError로 죽지 않게 한다.
    # 리눅스(Actions)는 원래 UTF-8이라 동작 변화 없음, 텔레그램 발송 본문과도 무관.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="CostPing 일일 리포트")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 발송 없이 콘솔 출력만")
    args = parser.parse_args()

    _load_dotenv_if_present()
    force_monthly = os.environ.get("COSTPING_FORCE_MONTHLY") == "1"
    force_weekly = os.environ.get("COSTPING_FORCE_WEEKLY") == "1"
    try:
        message, keyboard = build_report(
            dry_run=args.dry_run, force_monthly=force_monthly, force_weekly=force_weekly
        )
        if args.dry_run:
            print(f"[dry-run] 발송 생략 (버튼 {len(keyboard)}행)\n" + message)
            return
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
        if args.dry_run:
            sys.exit(1)  # dry-run은 로컬 확인용이라 오류도 콘솔에만 출력
        try:
            telegram.send_message(f"⚠️ CostPing 실행 중 오류 발생\n```\n{err_text[-500:]}\n```")
        except Exception:
            pass  # 텔레그램 발송 자체가 실패하면 더 할 수 있는 게 없음
        sys.exit(1)


if __name__ == "__main__":
    main()
