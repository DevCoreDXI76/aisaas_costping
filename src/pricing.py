"""
간소화판 토큰 단가표 기반 비용 계산기.

이 모듈은 순수 계산만 담당한다: (모델명, 토큰 개수) → USD.
Anthropic/OpenAI Usage API 원본 응답을 이 형태로 정규화하는 작업은 provider 어댑터
(2단계 세션 B/C에서 구현 예정)의 몫이다. 아직 report.py의 실제 발송 경로에는 연결되어
있지 않다 — "KST 정밀 비용" 기능의 계산 엔진만 먼저 만들고 검증하는 단계.

가격표(prices.json) 스키마와 반영 범위/한계는 prices.json의 "_readme"를 참고.
요약: 5분 프롬프트 캐시 쓰기 요율 기준 간소화판. 1시간 캐시 쓰기, 20만 토큰 초과
컨텍스트, 배치 50% 할인, 데이터 레지던시 +10%는 반영하지 않는다 — 대신 해당
사용량이 감지되면 경고를 남기고 그 부분은 근사치로 처리한다(0으로 세지 않고
가능한 필드만 사용).

모델 매칭: prices.json의 키는 실제 API가 돌려주는 모델 ID 문자열의 '접두사'다.
예) prices.json에 "claude-sonnet-5"가 있으면 실제 모델 ID가
"claude-sonnet-5-20260615"여도 매칭된다. 여러 접두사가 매치되면 가장 긴(구체적인)
것을 우선한다.
"""

import json
from pathlib import Path

DEFAULT_PRICES_PATH = Path(__file__).resolve().parent.parent / "prices.json"


class PricingConfigError(RuntimeError):
    """prices.json이 없거나 형식이 깨졌을 때 발생."""


def load_prices(path: Path = DEFAULT_PRICES_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_price_entry(provider_prices: dict, model: str) -> dict | None:
    """model 문자열이 시작하는 접두사 키를 찾는다. 여러 개 매치되면 가장 긴 키 우선."""
    matches = [key for key in provider_prices if model.startswith(key)]
    if not matches:
        return None
    best = max(matches, key=len)
    return provider_prices[best]


class CostAccumulator:
    """여러 모델/시간대에 걸친 비용 계산 결과를 누적한다.
    단가표에 없는 모델을 만나도 예외를 던지지 않고 경고만 쌓아서, 한 모델 때문에
    전체 계산이 죽지 않게 한다(기존 provider 모듈들의 방어 패턴과 동일한 철학)."""

    def __init__(self):
        self.total_usd = 0.0
        self.warnings: list[str] = []

    def add(self, cost_usd: float, warning: str | None = None) -> None:
        self.total_usd += cost_usd
        if warning and warning not in self.warnings:
            self.warnings.append(warning)


def calc_anthropic_cost_usd(
    model: str,
    uncached_input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
    prices: dict,
) -> tuple[float, str | None]:
    """
    Anthropic 토큰 개수를 USD로 환산한다 (5분 캐시 쓰기 요율 기준 간소화판).
    Returns: (비용 USD, 경고문구 또는 None). 단가표에 없는 모델이면 (0.0, 경고).
    """
    entry = _find_price_entry(prices.get("anthropic", {}), model)
    if entry is None:
        return 0.0, f"단가표에 없는 Anthropic 모델: {model} (해당 사용량은 집계에서 제외됨)"

    cost = (
        uncached_input_tokens * entry["input"]
        + output_tokens * entry["output"]
        + cache_creation_input_tokens * entry["cache_write_5m"]
        + cache_read_input_tokens * entry["cache_read"]
    ) / 1_000_000
    return cost, None


def calc_openai_cost_usd(
    model: str,
    total_input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    prices: dict,
) -> tuple[float, str | None]:
    """
    OpenAI 토큰 개수를 USD로 환산한다. total_input_tokens는 캐시 히트분을 포함한
    전체 입력 토큰(OpenAI usage 응답의 input_tokens), cached_input_tokens는 그중
    캐시로 처리된 부분(input_tokens_details.cached_tokens)이라고 가정한다.
    Returns: (비용 USD, 경고문구 또는 None). 단가표에 없는 모델이면 (0.0, 경고).
    """
    entry = _find_price_entry(prices.get("openai", {}), model)
    if entry is None:
        return 0.0, f"단가표에 없는 OpenAI 모델: {model} (해당 사용량은 집계에서 제외됨)"

    uncached_input_tokens = max(total_input_tokens - cached_input_tokens, 0)
    cost = (
        uncached_input_tokens * entry["input"]
        + cached_input_tokens * entry["cached_input"]
        + output_tokens * entry["output"]
    ) / 1_000_000
    return cost, None


if __name__ == "__main__":
    prices = {
        "anthropic": {"claude-sonnet-5": {"input": 2, "output": 10, "cache_write_5m": 2.5, "cache_read": 0.2}},
        "openai": {"gpt-5.6-terra": {"input": 2.5, "output": 15, "cached_input": 0.25}},
    }

    cost, warn = calc_anthropic_cost_usd(
        "claude-sonnet-5-20260615", 100_000, 20_000, 5_000, 50_000, prices
    )
    print(f"Anthropic 예시: ${cost:.4f} (경고: {warn})")

    cost, warn = calc_openai_cost_usd("gpt-5.6-terra-2026-06-01", 100_000, 30_000, 20_000, prices)
    print(f"OpenAI 예시: ${cost:.4f} (경고: {warn})")

    cost, warn = calc_anthropic_cost_usd("claude-future-model-x", 1000, 1000, 0, 0, prices)
    print(f"알 수 없는 모델 예시: ${cost:.4f} (경고: {warn})")
