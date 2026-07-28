# CostPing

AI API 비용 + 구독 결제일을 매일 아침 8시(KST) 텔레그램으로 받는 개인 도구.
서버·DB 없음: GitHub Actions cron → Python 스크립트 → Telegram Bot API.

## 아키텍처

```
GitHub Actions (cron, 매일 23:00 UTC = 08:00 KST)
  └─ src/report.py
       ├─ src/providers/anthropic_cost.py  → 어제/이달/기간별 Anthropic 비용
       ├─ src/providers/openai_cost.py     → 어제/이달/기간별 OpenAI 비용
       ├─ src/budget.py + budget.json      → 월 예산 초과 경고
       ├─ src/weekly_monthly.py            → 매주 월요일 "지난주 요약",
       │                                      매월 1일 "지난달 요약" 추가
       ├─ subscriptions.json               → 오늘·내일 결제 예정 구독
       └─ src/telegram.py                  → 리포트 발송 (실패 시에도 에러 알림 발송)
```

## 프로젝트 구조

```
aisaas_costping/
├─ .github/workflows/daily-report.yml
├─ src/
│  ├─ report.py             # 진입점: 수집 → 포맷 → 발송
│  ├─ providers/
│  │  ├─ anthropic_cost.py
│  │  └─ openai_cost.py
│  ├─ subscriptions.py      # 결제일 계산 (오늘/내일, 말일 처리)
│  ├─ budget.py             # 월 예산 초과 경고
│  ├─ weekly_monthly.py     # 주간/월간 요약 날짜 범위 계산
│  └─ telegram.py           # 발송 + 에러 알림
├─ subscriptions.json
├─ budget.json              # 월 예산 한도 설정 (없으면 예산 경고 기능 자체가 생략됨)
├─ requirements.txt
├─ PLAN.md                  # 세션별 개발 계획 + 2단계 백로그
└─ README.md                # 이 문서
```

## 셋업 방법 (처음 한 번만)

1. **텔레그램 봇 생성**: `@BotFather`에게 `/newbot` → 봇 토큰 확보. 만든 봇에게 메시지 1개 보낸 뒤
   `https://api.telegram.org/bot<토큰>/getUpdates` 로 접속해 `chat.id` 확인.
2. **Anthropic Admin API 키 발급**: [platform.claude.com](https://platform.claude.com) → Settings →
   Organization → **관리자 키(Admin keys)** → Create key.
   - ⚠️ 계정이 "Individual Org" 상태면 이 메뉴가 사이드바에 안 보일 수 있습니다. 그럴 땐 Settings →
     Organization 화면 하단의 **"Team으로 전환"**을 눌러 조직 프로필 설문(기관 유형/소재지/사용 목적 등,
     결제 방식과 무관한 컴플라이언스 설문)을 제출하면 관리자 키 메뉴가 나타납니다.
   - 키 접두사는 `sk-ant-admin01-...` 입니다.
3. **OpenAI Admin Key 발급**: [platform.openai.com](https://platform.openai.com) → Settings →
   Organization → API keys → Admin keys → Create new secret key. (조직 **Owner** 권한 필요)
4. **GitHub Secrets 등록**: repo → Settings → Secrets and variables → Actions → New repository secret
   으로 아래 4개 등록.

   | Name | 설명 |
   |---|---|
   | `ANTHROPIC_ADMIN_KEY` | Anthropic Admin API 키 (`sk-ant-admin01-...`) |
   | `OPENAI_ADMIN_KEY` | OpenAI Admin Key |
   | `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
   | `TELEGRAM_CHAT_ID` | 텔레그램 chat_id |

5. **subscriptions.json 작성**: 아래 "구독 추가/수정 방법" 참고.
6. **budget.json 확인/수정**: 아래 "예산 경고 설정 방법" 참고. (선택 사항 — 안 써도 나머지 기능은 그대로 동작)
7. 커밋/푸시하면 GitHub Actions가 매일 KST 08:00에 자동 실행됩니다. Actions 탭 →
   "Daily Cost Report" → **Run workflow** 로 언제든 수동 테스트 가능합니다.

## 로컬에서 테스트하기

```powershell
python -m pip install -r requirements.txt
```

프로젝트 루트에 `.env` 파일 생성 (git에는 안 올라감, `.gitignore`에 이미 등록됨):

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ANTHROPIC_ADMIN_KEY=...
OPENAI_ADMIN_KEY=...
```

```powershell
python src/report.py
```

## 구독 추가/수정 방법

`subscriptions.json`에 아래 형식으로 항목을 추가/수정하면 됩니다.

```json
[
  {
    "name": "구독명",
    "amount": 20,
    "currency": "USD",
    "billing_day": 15,
    "memo": ""
  }
]
```

- `billing_day`가 해당 월의 마지막 날보다 크면(예: 31일인데 4월/6월/9월/11월, 또는 2월) 그 달의
  마지막 날에 결제되는 것으로 자동 처리됩니다 (`src/subscriptions.py`의 말일 보정 로직).
- 수정 후 커밋/푸시하면 다음 실행부터 반영됩니다.

## 예산 경고 설정 방법

`budget.json`에서 하루/이번 달 API 비용(Anthropic+OpenAI **종량제 사용 비용 합계만**,
`subscriptions.json`의 정액 구독료는 포함 안 됨) 한도를 각각 설정합니다.

```json
{
  "daily_budget_usd": 5,
  "monthly_budget_usd": 60,
  "warning_threshold_pct": 80
}
```

- `warning_threshold_pct`(기본 80, 하루/월 공통) 이상 쓰면 ⚠️ 경고, 한도를 넘으면 🚨 초과 경고가
  리포트에 각각 한 줄씩 추가됩니다(둘 다 걸리면 두 줄). 둘 다 안 넘으면 이 섹션 자체가 안 보입니다.
- `daily_budget_usd` / `monthly_budget_usd`는 서로 독립적으로 켜고 끌 수 있습니다. 값을 0으로 두거나
  키 자체를 빼면 해당 경고만 꺼지고, `budget.json` 파일을 아예 지우면 예산 경고 기능 전체가 조용히
  꺼집니다(나머지 리포트는 그대로 정상 발송).
- 수정 후 커밋/푸시하면 다음 실행부터 반영됩니다.

## 주간/월간 요약

별도 cron 추가 없이 기존 매일 08:00(KST) 실행에 조건부로 섹션이 더 붙습니다.

- **매주 월요일**: "📅 지난주 요약" (지난주 월~일, Anthropic/OpenAI 비용)
- **매월 1일**: "🗓️ 지난달 요약" (지난달 한 달 전체, Anthropic/OpenAI 비용)

DB 없이 그날그날 Anthropic/OpenAI 비용 API에 해당 기간을 다시 질의하는 방식이라 별도 저장소가
필요 없습니다(`src/weekly_monthly.py`가 날짜 범위 계산, `src/providers/*.py`의
`get_cost_for_range()`가 조회).

## 알려진 이슈 / 주의사항

- **시간 기준은 UTC**: "어제/이번 달/지난주/지난달"은 모두 UTC 자정 기준으로 계산합니다. KST
  자정과는 최대 9시간 어긋나므로, 아침 8시(KST) 기준으로 보면 "그저께 낮~어제 낮(UTC로는 어제
  하루)"의 비용이라고 보면 됩니다. 정밀화(KST 기준 일 경계)는 2단계 백로그입니다 (PLAN.md 참고).
- **OpenAI 비용 API의 `amount.value`**: 실제 응답에서 숫자/문자열이 섞여 오는 경우가 관측되어
  `float()`로 명시 변환하도록 처리했습니다 (`src/providers/openai_cost.py`).
- **OpenAI 페이지네이션**: `group_by` 파라미터를 쓰면서 `limit`을 지정하지 않으면 `next_page` 토큰이
  깨지는 버그가 커뮤니티에 보고된 적이 있어(2026년 3~4월), 이 프로젝트는 `group_by`를 쓰지 않고
  `limit`을 항상 명시해 회피합니다.
- **텔레그램 발송은 기본 plain text**: 구독명/에러 메시지 등에 `_`, `*`, `` ` `` 같은 문자가 섞이면
  Markdown 파싱이 깨져 400 에러가 나기 때문에, 기본값을 plain text로 뒀습니다
  (`src/telegram.py`의 `send_message(text, parse_mode=None)`).
- **시크릿 노출 주의**: 에러 로그/메시지에 봇 토큰이 그대로 노출되지 않도록 처리했지만
  (`telegram.py`), 혹시라도 토큰이 어딘가(채팅, 로그 등)에 평문으로 노출됐다면 `@BotFather`에서
  즉시 재발급하고 GitHub Secrets/`.env`를 갱신하세요.

## 에러 처리 방식

- Anthropic/OpenAI 중 한쪽 조회가 실패해도 나머지는 정상 표시되고, "부분 합계"로 표시됩니다.
  (일간/주간/월간 섹션 모두 동일)
- `subscriptions.json`이 없거나 형식이 깨져도 비용 섹션은 정상 발송됩니다.
- `budget.json`이 없으면 예산 경고 섹션만 조용히 생략되고, 형식이 깨지면 경고 문구로 표시됩니다
  (리포트 전체는 죽지 않음).
- 그 외 예상 못 한 에러가 나면 전체 리포트 대신 에러 요약을 텔레그램으로 발송합니다(가능한 경우).
  GitHub Actions 실행이 실패(빨간 X)로 표시되므로 Actions 탭에서도 확인 가능합니다.

## 2단계 백로그

- Next.js + Supabase 웹 대시보드, 구독 CRUD UI
- KST 정밀 일 경계 (시간 단위 버킷 집계)
- Google Gemini API 비용 추가 (GCP BigQuery Billing Export 필요, MVP 아키텍처와 무게감이 다름)
- Voyage AI 비용 추가 (MongoDB Atlas 산하, 공개 비용 API 없음 — 별도 문의 필요)
- 기타 제공사 추가

> 월 예산 초과 경고 / 주간·월간 요약은 완료되어 위 목록에서 빠졌습니다. 상세 이력은 `PLAN.md` 참고.
