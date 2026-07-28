"""
1회성 탐색 스크립트: Anthropic Usage API의 실제 응답 형태를 확인한다.

리포트 발송 파이프라인의 일부가 아니다. 2단계 세션 2의 "세션 B"에서 prices.json의
모델 ID 접두사를 실제 값에 맞게 보정하고, service_tier/context_window 등 필드명을
확인하기 위해 딱 한 번(또는 몇 번) 실행해보는 디버깅용 도구다. 조회만 하는 GET
요청이라 데이터를 바꾸지 않는다 — 안전하게 여러 번 실행해도 된다.

실행 (프로젝트 루트에서):
    python scripts/explore_anthropic_usage.py

.env(또는 환경변수)에 ANTHROPIC_ADMIN_KEY가 있어야 한다(report.py 로컬 테스트와 동일).
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import kst_bucket  # noqa: E402

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


def _load_dotenv_if_present() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    _load_dotenv_if_present()
    api_key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not api_key:
        print("ANTHROPIC_ADMIN_KEY가 설정되어 있지 않습니다 (.env 확인).", file=sys.stderr)
        sys.exit(1)

    # 어제(KST) 하루에 정확히 대응하는 UTC 구간을 계산 (kst_bucket.py, 2단계 세션 2A에서 검증됨)
    yesterday_kst = date.today() - timedelta(days=1)
    start_utc, end_utc = kst_bucket.kst_day_to_utc_range(yesterday_kst)
    print(f"조회 구간 (KST {yesterday_kst} 하루): {start_utc.isoformat()} ~ {end_utc.isoformat()}")

    url = f"{ANTHROPIC_API_BASE}/v1/organizations/usage_report/messages"
    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "x-api-key": api_key,
    }
    params = {
        "starting_at": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ending_at": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket_width": "1h",
        "group_by[]": "model",
    }

    resp = requests.get(url, headers=headers, params=params, timeout=20)
    print(f"HTTP 상태: {resp.status_code}")
    if not resp.ok:
        print("응답 본문:", resp.text[:2000])
        sys.exit(1)

    body = resp.json()

    # 원본 응답 전체를 파일로 저장 (다음 세션에서 참고하기 편하게). .gitignore에 이미 등록됨.
    out_path = Path(__file__).resolve().parent / "usage_sample_response.json"
    out_path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"원본 응답 저장: {out_path}")

    seen_models = set()
    seen_service_tiers = set()
    seen_context_windows = set()
    total_buckets = 0
    total_results = 0
    for bucket in body.get("data", []):
        total_buckets += 1
        for result in bucket.get("results", []):
            total_results += 1
            seen_models.add(result.get("model", "(model 필드 없음)"))
            if "service_tier" in result:
                seen_service_tiers.add(result["service_tier"])
            if "context_window" in result:
                seen_context_windows.add(result["context_window"])

    print(f"\n버킷 개수: {total_buckets} / 총 결과(result) 개수: {total_results}")
    print(f"등장한 모델 ID: {sorted(seen_models)}")
    print(f"등장한 service_tier: {sorted(seen_service_tiers) if seen_service_tiers else '(필드 없음/미등장)'}")
    print(f"등장한 context_window: {sorted(seen_context_windows) if seen_context_windows else '(필드 없음/미등장)'}")
    print(f"has_more: {body.get('has_more')}")

    if total_results:
        print("\n첫 번째 result 샘플 (필드명 확인용):")
        first_bucket = next(b for b in body.get("data", []) if b.get("results"))
        print(json.dumps(first_bucket["results"][0], indent=2, ensure_ascii=False))
    else:
        print("\n어제 사용량이 0인 것 같습니다 — 다른 날짜로 다시 시도해볼 수 있습니다.")

    print("\n--- 위 출력 전체(또는 scripts/usage_sample_response.json 내용)를 대화에 붙여넣어 주세요 ---")


if __name__ == "__main__":
    main()
