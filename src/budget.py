"""
하루/월 예산 초과 경고.

budget.json 스키마 (프로젝트 루트, notion_config.json과 동일한 레벨):
{
  "daily_budget_usd": 1,          # 어제 하루 API 비용(Anthropic+OpenAI 합계) 한도, USD
  "monthly_budget_usd": 15,       # 이번 달 API 비용(Anthropic+OpenAI 합계) 한도, USD
  "warning_threshold_pct": 80,    # 이 %를 넘으면 경고, 100% 넘으면 "초과" 경고로 문구가 바뀜 (하루/월 공통)
  "usd_krw": 1400                 # 구독 월 고정비 원화 환산용 고정 환율 (분기마다 사람이 갱신)
}

주의: 이 예산은 Notion에 등록된 구독 결제액(OTT/AI 정액 구독 등, src/providers/notion_subscriptions.py)과는
별개로, Anthropic+OpenAI API 종량제 사용 비용에만 적용된다.

daily_budget_usd / monthly_budget_usd는 각각 독립적으로 켜고 끌 수 있다. 둘 다 0 이하로
두거나(또는 키 자체를 생략) budget.json 파일 자체를 만들지 않으면 해당 경고는 조용히 생략된다.
"""

import json
from pathlib import Path

DEFAULT_BUDGET_PATH = Path(__file__).resolve().parent.parent / "budget.json"
DEFAULT_USD_KRW = 1400  # budget.json에 usd_krw가 없거나 파일을 못 읽을 때 쓰는 fallback 환율


class BudgetConfigError(RuntimeError):
    """budget.json이 있는데 형식이 깨졌을 때 발생."""


def load_budget(path: Path = DEFAULT_BUDGET_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_usd_krw(path: Path = DEFAULT_BUDGET_PATH) -> float:
    """budget.json의 usd_krw(고정 환율)를 반환한다. 파일/키가 없거나 깨졌으면 DEFAULT_USD_KRW.
    환율 하나 때문에 월간 구독 섹션 전체가 죽지 않도록 예외는 여기서 흡수한다."""
    try:
        value = load_budget(path).get("usd_krw")
    except Exception:
        return DEFAULT_USD_KRW
    return value if isinstance(value, (int, float)) and value > 0 else DEFAULT_USD_KRW


def _build_single_warning(label: str, used_usd: float, limit: float, threshold_pct: float) -> str | None:
    """한도 미설정(0 이하)이거나 임계치 미도달이면 None을 반환한다(섹션 자체를 생략)."""
    if limit <= 0:
        return None

    used_pct = (used_usd / limit) * 100

    if used_usd >= limit:
        return f"🚨 {label} 예산 초과! ${used_usd:,.2f} / ${limit:,.2f} ({used_pct:.0f}%)"
    if used_pct >= threshold_pct:
        return f"⚠️ {label} 예산의 {used_pct:.0f}% 사용 (${used_usd:,.2f} / ${limit:,.2f})"
    return None


def build_budget_warnings(
    yesterday_usd: float | None, month_to_date_usd: float | None, cfg: dict
) -> list[str]:
    """
    어제 하루 비용(yesterday_usd)과 이번 달 누적 비용(month_to_date_usd)을 각각 budget.json의
    한도와 비교해 경고 문구 리스트를 반환한다(0~2개). 해당 비용 자체를 조회 못 했으면(None)
    그 항목은 건너뛴다.
    """
    threshold_pct = cfg.get("warning_threshold_pct", 80)
    warnings = []

    if yesterday_usd is not None:
        daily_limit = cfg.get("daily_budget_usd") or 0
        w = _build_single_warning("어제 하루", yesterday_usd, daily_limit, threshold_pct)
        if w:
            warnings.append(w)

    if month_to_date_usd is not None:
        monthly_limit = cfg.get("monthly_budget_usd") or 0
        w = _build_single_warning("이번 달", month_to_date_usd, monthly_limit, threshold_pct)
        if w:
            warnings.append(w)

    return warnings


def build_budget_section_safe(
    yesterday_usd: float | None,
    month_to_date_usd: float | None,
    path: Path = DEFAULT_BUDGET_PATH,
) -> str | None:
    """budget.json이 없으면(아직 설정 안 함) 조용히 섹션을 생략하고,
    있는데 형식이 깨졌으면 리포트 전체는 죽지 않도록 경고 문구로 흡수한다
    (build_cost_section/_build_subscription_section_safe와 동일한 방어 패턴)."""
    try:
        cfg = load_budget(path)
    except FileNotFoundError:
        return None
    except Exception as e:
        return f"⚠️ 예산 설정(budget.json) 조회 실패: {e}"

    warnings = build_budget_warnings(yesterday_usd, month_to_date_usd, cfg)
    if not warnings:
        return None
    return "\n".join(warnings)


if __name__ == "__main__":
    cfg = {"daily_budget_usd": 1, "monthly_budget_usd": 15, "warning_threshold_pct": 80}
    for y, m in [(0.2, 3), (0.85, 12.5), (1.2, 16.1), (0.1, 5)]:
        print(f"어제 ${y} / 이번달 ${m} ->", build_budget_warnings(y, m, cfg))
