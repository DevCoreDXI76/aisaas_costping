# CostPing

AI API 비용 + 구독 결제일을 매일 아침 8시(KST) 텔레그램으로 받는 개인 도구.
서버·DB 없음: GitHub Actions cron → Python 스크립트 → Telegram Bot API.

## 아키텍처

```
GitHub Actions (cron, 매일 23:00 UTC = 08:00 KST)
  └─ src/report.py
       ├─ src/providers/anthropic_cost.py  → 어제/이달/기간별 Anthropic 비용 (UTC 기준)
       │    ├─ get_*_by_project*            → 키(프로젝트)별 추정 비용 (Usage API+단가표)
       │    └─ get_kst_precise_cost_for_day → KST 정밀 비용(실험적, Usage API+단가표)
       ├─ src/providers/openai_cost.py     → 어제/이달/기간별 OpenAI 비용 (UTC 기준)
       │    ├─ get_*_by_project*            → 프로젝트별 비용 (Costs API group_by)
       │    └─ get_kst_precise_cost_for_day → KST 정밀 비용(실험적, Usage API+단가표)
       ├─ src/project_map.py + projects.json → 키/프로젝트 → 리포트 표시명 매핑
       ├─ src/pricing.py + prices.json     → 프로젝트별·KST 정밀 비용 계산용 간소화판 단가표
       ├─ src/kst_bucket.py                → KST 캘린더 날짜 ↔ UTC 시간 변환
       ├─ src/budget.py + budget.json      → 월 예산 초과 경고
       ├─ src/weekly_monthly.py            → 매주 월요일 "지난주 요약",
       │                                      매월 1일 "지난달 요약" 추가
       ├─ src/providers/notion_subscriptions.py + notion_config.json
       │                                     → Notion "구독 중" DB에서 오늘·내일·D-3 결제 예정 구독 조회
       │                                        + "마지막 이용일" 필드 기록(get_due_in_days/update_last_used_date)
       │                                        + 신용카드·유형 읽기, 월 환산 원화(monthly_krw, 매월 1일 회사/개인 구독 섹션)
       ├─ src/reply_processor.py           → 텔레그램 인라인 버튼(✅/❌) 응답 회수 → Notion 반영
       └─ src/telegram.py                  → 리포트 발송(+인라인 버튼) + getUpdates 폴링 + 에러 알림
```

**D-3 알림 + 원탭 버튼 흐름**: 결제 3일 전인 구독은 리포트 메시지 하단에 "✅ 이번 달 씀 / ❌ 안 씀"
버튼이 함께 붙습니다. 버튼을 누르면 텔레그램이 그 응답(`callback_query`)을 서버에 보관해두고,
**다음 크론 실행(보통 다음날 08:00) 때** `src/reply_processor.py`가 그걸 읽어와 Notion "마지막
이용일" 필드에 반영합니다. 실시간 웹훅 서버를 두지 않는 대신 응답 반영에 최대 24시간 정도 지연이
생길 수 있습니다 — "서버 없음" 아키텍처를 지키기 위해 의도적으로 선택한 트레이드오프입니다.

## 프로젝트 구조

```
aisaas_costping/
├─ .github/workflows/daily-report.yml
├─ src/
│  ├─ report.py             # 진입점: 수집 → 포맷 → 발송
│  ├─ providers/
│  │  ├─ anthropic_cost.py
│  │  ├─ openai_cost.py
│  │  └─ notion_subscriptions.py  # Notion "구독 중" DB 조회/기록 (결제일 계산 + 마지막 이용일)
│  ├─ budget.py             # 월 예산 초과 경고
│  ├─ weekly_monthly.py     # 주간/월간 요약 날짜 범위 계산
│  ├─ reply_processor.py    # D-3 알림 버튼 응답 회수 → Notion 반영
│  ├─ pricing.py            # 토큰 → USD 계산기(간소화판 단가표 기반) — 아래 참고
│  ├─ project_map.py        # projects.json 로더 (키/프로젝트 → 표시명)
│  ├─ kst_bucket.py         # KST 캘린더 날짜 ↔ UTC 시간 변환
│  └─ telegram.py           # 발송(+인라인 버튼) + getUpdates 폴링 + 에러 알림
├─ scripts/
│  ├─ explore_anthropic_usage.py  # Usage API 응답 확인용 1회성 탐색 스크립트(파이프라인 아님)
│  └─ analyze_usage.py            # 30일 키별·모델별 비용 분해 1회성 진단(파이프라인 아님)
├─ notion_config.json       # Notion 구독 DB의 database_id 등 (비밀 아님, 커밋됨)
├─ budget.json              # 월 예산 한도 설정 (없으면 예산 경고 기능 자체가 생략됨)
├─ projects.json            # API 키 이름/OpenAI 프로젝트 ID → 표시명 (비밀 아님, 커밋됨)
├─ prices.json              # 모델별 토큰 단가표 (간소화판, 프로젝트별·KST 정밀 비용 계산용)
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
4. **Notion Integration 발급 + 연결**: [notion.so/my-integrations](https://www.notion.so/my-integrations)
   → **New integration** → 이름 아무거나(예: CostPing) → 워크스페이스 선택 → 생성. **Internal Integration
   Secret**(`ntn_...` 또는 `secret_...`로 시작)을 복사해둡니다. 그 다음 구독을 관리 중인 Notion
   데이터베이스(예: "구독 중") 페이지를 열어 우측 상단 `···` → **연결(Connections)** → 방금 만든
   Integration을 추가해줘야 API로 읽을 수 있습니다(이 단계를 빼먹으면 401 오류가 납니다).
5. **GitHub Secrets 등록**: repo → Settings → Secrets and variables → Actions → New repository secret
   으로 아래 5개 등록.

   | Name | 설명 |
   |---|---|
   | `ANTHROPIC_ADMIN_KEY` | Anthropic Admin API 키 (`sk-ant-admin01-...`) |
   | `OPENAI_ADMIN_KEY` | OpenAI Admin Key |
   | `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
   | `TELEGRAM_CHAT_ID` | 텔레그램 chat_id |
   | `NOTION_API_KEY` | Notion Integration Secret (위 4번 단계에서 발급) |

6. **notion_config.json 확인**: 데이터베이스가 다르면 `database_id`를 바꿔야 합니다. 아래
   "구독 관리 방법(Notion)" 참고.
7. **budget.json 확인/수정**: 아래 "예산 경고 설정 방법" 참고. (선택 사항 — 안 써도 나머지 기능은 그대로 동작)
8. **projects.json 확인/수정**: 아래 "프로젝트별 비용" 참고. (선택 사항 — 없으면 키 이름이 그대로 표시됨)
9. 커밋/푸시하면 GitHub Actions가 매일 KST 08:00에 자동 실행됩니다. Actions 탭 →
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
NOTION_API_KEY=...
```

```powershell
python src/report.py
```

Notion 연동만 따로 확인하고 싶으면:

```powershell
python src/providers/notion_subscriptions.py
```

## 구독 관리 방법 (Notion)

구독 결제 알림의 소스는 **Notion**입니다(과거 `subscriptions.json` 수동 관리는 폐기됨 — 실제
결제 중인 구독과 자꾸 어긋나는 문제가 있었습니다). Notion에서 구독을 추가/해지/금액 변경하면
다음 리포트부터 그대로 반영됩니다. 별도로 커밋/푸시할 필요가 없습니다.

> ⚠️ **중요**: 원래 쓰시던 구독 관리 DB가 Notion이 "데이터소스" 개념을 도입하기 전에 만들어진
> 것이라, 새로 만든 Integration이 그 DB의 데이터를 API로 읽을 수 없는 특이 케이스를 만났습니다
> (자세한 트러블슈팅 과정은 `PLAN.md`의 2단계 세션 3-1 로그 참고). 그래서 CostPing 전용으로
> 완전히 새로운 Notion 데이터베이스를 만들어 그리로 데이터를 옮겼습니다. **구독 추가/해지/금액
> 변경은 이제 이 전용 DB에서 해야 리포트에 반영됩니다** — 기존 DB를 계속 쓰던 습관이 있다면
> 헷갈리지 않게 주의하세요. `notion_config.json`의 `database_id`가 이 전용 DB를 가리킵니다.

이 Notion 데이터베이스에서 다음 필드를 사용합니다.

| 필드 | 타입 | 설명 |
|---|---|---|
| 이름 | title | 구독명 |
| 상태 | select | `구독 중`이어야 알림 대상에 포함됨 (`구독 해지`는 제외) |
| 결제 시작일 | date | 최초 결제(또는 갱신 기준)일 |
| 결제 주기 | select | `월` 또는 `연` |
| 결제 금액(KRW) / 결제 금액(USD) | number | 둘 중 하나만 채우면 됨 (USD가 있으면 USD 우선) |
| 비고 | text | 선택 사항 |
| 마지막 이용일 | date | **사람이 직접 채우지 않아도 됨.** D-3 알림의 "✅ 이번 달 씀" 버튼을 누르면 코드가 자동으로 오늘 날짜를 채웁니다. |

- 다음 결제일은 Notion의 수식 필드를 그대로 읽는 게 아니라, `결제 시작일` + `결제 주기`로
  `src/providers/notion_subscriptions.py`가 직접 계산합니다(말일 보정 포함, 예: 1/31 매달 결제 →
  2월엔 2/28). 실사용 데이터로 Notion 화면의 "다음 결제일" 수식 값과 전부 일치함을 확인했습니다.
- 다른 Notion 데이터베이스를 쓰거나 필드명을 바꿨다면 `notion_config.json`의 `database_id`와
  `active_status_value`를 맞게 수정하세요(Notion에서 해당 데이터베이스를 열고 URL에 있는
  32자리 ID 부분). 필드명 자체(이름/상태/결제 시작일 등)가 다르면
  `src/providers/notion_subscriptions.py`의 `_parse_subscription_page()` 안의 키 문자열을 함께
  바꿔야 합니다.
- Notion 조회가 실패하면(Integration 연결 안 함, `NOTION_API_KEY` 미설정 등) 이 섹션만 조용히
  건너뛰고 나머지 리포트는 정상 발송됩니다.

## 구독 이용 확인 (D-3 알림 + 원탭 버튼)

"좀비 구독"(안 쓰는데 계속 결제되는 구독) 방지를 위해, 결제 3일 전인 구독은 리포트 메시지에
"✅ {구독명} 씀" / "❌ {구독명} 안 씀" 버튼이 함께 붙습니다.

- **✅ 씀**을 누르면 → 다음 크론 실행 때 Notion "마지막 이용일"이 그날 날짜로 갱신됩니다.
- **❌ 안 씀**을 누르면 → Notion은 갱신되지 않습니다(마지막 이용일이 그대로 남아 "몇 개월째 안
  씀"을 나중에 판단할 수 있는 근거가 됩니다).
- 버튼을 안 누르면 아무 일도 일어나지 않습니다(다음 D-3 알림 때 다시 물어봅니다).
- 응답은 **실시간이 아니라 다음 크론 실행 때** 반영됩니다(최대 24시간 정도 지연 가능) — 별도
  웹훅 서버 없이 매일 08:00 폴링만으로 처리하기 위한 의도적인 설계입니다. 리포트 상단에 "📋 지난
  답변 처리" 섹션으로 처리 결과가 표시됩니다(처리할 응답이 없으면 이 섹션 자체가 생략됩니다).
- 로컬에서 직접 확인하려면: `python src/report.py`로 알림을 받고 버튼을 누른 뒤, 다시
  `python src/report.py`를 한 번 더 실행하면 "📋 지난 답변 처리" 섹션에 결과가 뜹니다.

### 매월 1일 회사/개인 구독 섹션

매월 1일 "🗓️ 지난달 요약" 바로 다음에 Notion `신용카드` 값으로 구독을 나눠 보여줍니다
(회사 방침: AI·개발 구독은 법인카드, OTT 등은 개인 카드).

- **💼 회사 월 고정비 · 월간 점검** (`신용카드` = 법인카드): 월 환산 원화 합계 + 건수, 항목은 월 환산
  금액 내림차순. `마지막 이용일`이 없거나 3개월 이상 지났으면 "⚠️ 이용 미확인"이 붙습니다.
- **🎬 개인 구독(참고)** (법인카드 외): 월 합계 + 건수, 이름·금액만.
- `신용카드`가 비어 있는 구독은 어느 합계에도 넣지 않고 "❓ 카드 미지정"으로 따로 표시됩니다.
- 월 환산: USD는 `budget.json`의 `usd_krw`(고정 환율, 분기마다 갱신, 없으면 1400)를 곱하고, 연 결제는
  12로 나눈 뒤 원 단위 반올림합니다.
- 1일이 아니어도 확인하려면: 로컬은 `COSTPING_FORCE_MONTHLY=1 python src/report.py --dry-run`
  (텔레그램 발송·지난 버튼 응답 처리 없이 콘솔 출력만), Actions는 수동 실행 시 `force_monthly`를 체크.

## 프로젝트별 비용

일일 리포트의 합계 섹션 바로 아래에 어제 비용을 프로젝트(API 키)별로 나눠 보여줍니다. 시간 기준은
위 "어제 API 비용 (UTC 기준)"과 같은 **UTC 하루**라서, 프로젝트별 금액 + 미귀속 = 위 합계가 됩니다.

```
🗂 프로젝트별 (어제, UTC)
· MotionToon Studio (Anthropic) $0.38 — Sonnet 73% · Haiku 27% | 월 $4.51
· 네이버 블로그 자동화 (Anthropic) $0.08 — Haiku 100% | 월 $0.65
```

- **Anthropic**: Usage API를 `api_key_id`·`model`별로 받아 `prices.json` 단가로 키별 비용을 추정하고,
  실제 청구액(cost_report) 합계와 대조합니다. cost_report는 키별 구분을 지원하지 않아서(`group_by`는
  `workspace_id`·`description`만 가능) 이 방식을 씁니다.
- **OpenAI**: Costs API를 `project_id`별로 받습니다(실제 청구액이라 추정 없음).
- 표시 규칙: 어제 비용 상위 5개 프로젝트 + 나머지는 "기타", $0.01 미만 줄(키 없이 쓴 Console 사용,
  소액 OpenAI 프로젝트 등)은 숨김, 각 줄에 모델 계열 비중과 이번 달 누적.
- **미귀속**: 청구 합계 - 프로젝트별 추정 합계. 단가표에 없는 모델·요금 항목(데이터 레지던시
  +10%, 빠른 모드 등)이 여기 드러납니다. 음수면 "⚠️ 추정이 청구액보다 $x 많음 (prices.json 확인
  필요)" — 단가표가 실제보다 비싸게 잡혀 있다는 뜻입니다.
- 화면의 각 줄은 반올림 표시라 합이 위 합계와 1센트 정도 다르게 보일 수 있습니다(실제 값은 일치).
- 조회가 실패하면 이 섹션만 "⚠️ 프로젝트별 비용 조회 실패: …"로 바뀌고 나머지 리포트는 정상 발송,
  한 제공사만 실패하면 다른 제공사 줄은 그대로 표시됩니다.
- 메시지가 3,800자(텔레그램 제한 4,096자)를 넘으면 일일·주간·월간 프로젝트 목록의 줄 수를 같이
  줄입니다(넘친 프로젝트는 "기타"로 합쳐짐).

### projects.json (표시명 매핑)

```json
{
  "anthropic": { "motiontoon-studio-key": "MotionToon Studio", "naver-blog-actions": "네이버 블로그 자동화" },
  "openai": { "proj_...": "표시명" }
}
```

- `anthropic`: **키 이름**(Console에서 키 발급 시 붙인 이름) → 표시명. 시크릿(키 값)은 넣지 않습니다.
  같은 프로젝트 키는 같은 이름으로 발급하면(재발급 포함) 자동으로 한 줄로 합쳐집니다.
- `openai`: 프로젝트 ID → 표시명.
- 매핑에 없는 키는 원래 키 이름(OpenAI는 프로젝트 이름)이 그대로 표시됩니다. 키 목록 조회가
  실패하면 그 실행 동안에는 키 ID가 표시됩니다.

## 예산 경고 설정 방법

`budget.json`에서 하루/이번 달 API 비용(Anthropic+OpenAI **종량제 사용 비용 합계만**, Notion에
등록된 정액 구독료는 포함 안 됨) 한도를 각각 설정합니다.

```json
{
  "daily_budget_usd": 1,
  "monthly_budget_usd": 15,
  "warning_threshold_pct": 80,
  "usd_krw": 1400
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

- **매주 월요일**: "📅 지난주 요약" (지난주 월~일, 그 전주 대비)
- **매월 1일**: "🗓️ 지난달 요약" (지난달 한 달 전체, 그 전달 대비 — 월 일수 차이는 보정하지 않음)

```
📅 지난주 요약 (10/12~10/18)
· Anthropic  $1.58
· OpenAI     $0.00
· 합계        $1.58 (전주 대비 ▼$0.09, -5%)
프로젝트별 (전주 대비)
· MotionToon Studio (Anthropic) $1.38 ▼$0.16 (-11%)
· 네이버 블로그 자동화 (Anthropic) $0.20 ▲$0.08 (+60%)
```

- 증감: `▲/▼$금액 (±%)`. 지난 기간이 $0.10 미만이면 %는 빼고 금액만(소액 % 과장 방지), 지난 기간
  $0이면 "(신규)", 변화가 $0.01 미만이면 표시 생략. 일부 제공사 조회 실패로 부분 합계일 때는 합계
  줄의 증감만 생략(프로젝트 줄 증감은 유지).
- 지난 기간 $0.01 이상이었는데 이번 기간 사용이 없으면 "$0.00 ▼$x (중단)" 줄로 사용 중인 프로젝트
  아래에 표시합니다(상위 5줄 제한 안에 포함). 나머지 규칙(기타·미귀속·$0.01 미만 숨김)은 일일
  프로젝트별 섹션과 같습니다.
- 이번 기간과 지난 기간을 제공사별로 한 번에 조회해 날짜로 나눕니다(합계 cost_report 1회 +
  프로젝트별 1회, 월간은 62일이라 Anthropic이 페이지 1개씩 더).
- 월요일·1일이 아니어도 확인하려면: 로컬은 `COSTPING_FORCE_WEEKLY=1` / `COSTPING_FORCE_MONTHLY=1`을
  붙여 `python src/report.py --dry-run`, Actions는 수동 실행 시 `force_weekly` / `force_monthly`를 체크.

DB 없이 그날그날 Anthropic/OpenAI 비용 API에 해당 기간을 다시 질의하는 방식이라 별도 저장소가
필요 없습니다(`src/weekly_monthly.py`가 날짜 범위 계산, `src/providers/*.py`의
`get_cost_for_periods()`·`get_cost_by_project_for_periods()`가 조회).

## KST 정밀 비용 계산 (실험적)

리포트의 "어제 API 비용 (UTC 기준)" 섹션은 이름 그대로 **UTC 자정 기준**입니다. Anthropic Cost
API·OpenAI Costs API 둘 다 하루(1d) 단위 버킷만 지원하고 그 하루가 UTC 자정에 고정돼 있어서, 이
API들만으로는 KST 자정에 정확히 맞출 방법이 없습니다(2단계 세션 2 조사 결과, `PLAN.md` 참고).

대신 시간(1h) 단위를 지원하는 **Usage API**(토큰 개수)를 받아와 직접 관리하는 단가표
(`prices.json`)로 USD를 계산하는 방식으로 "🧪 KST 정밀 어제 비용 (실험적)" 섹션을 만들었습니다.
매일 리포트에서 기존 "어제 API 비용 (UTC 기준)" 섹션 **바로 아래에 나란히** 뜹니다(대체 아님).

다만 실제 단가 체계가 캐시 요율·컨텍스트 길이·배치 할인 등으로 복잡해서, **input/output/캐시 읽기
+ 5분·1시간 캐시 쓰기 + Anthropic 웹 검색 도구 사용료까지 반영한 간소화판**(`src/pricing.py`,
`prices.json`)으로 범위를 좁혔습니다. 배치(batch) 처리·20만 토큰 초과 컨텍스트·오디오 토큰·데이터
레지던시처럼 이 단가표가 못 다루는 사용량을 만나면 그 부분은 계산에서 빼고(또는 근사치로 처리하고)
⚠️ 경고 문구를 리포트에 같이 표시합니다 — 그래서 이 섹션의 숫자는 실제 청구액보다 살짝 낮거나
(Anthropic 쪽 갭) 높게(OpenAI batch 감지 시) 나올 수 있습니다. 같은 단가표를 프로젝트별 비용
섹션도 씁니다.

`prices.json`의 가격은 수동으로 유지해야 합니다. 1시간 캐시 쓰기(기본 입력 단가 x2)와 웹 검색
($10 / 1,000회) 단가는 2026-10-24 Anthropic 공식 가격 페이지
(https://platform.claude.com/docs/en/about-claude/pricing)에서 확인했습니다. Claude Sonnet 5의
$2/$10은 표준가로 확정됐습니다(9/1 인상 취소, `prices.json`의 `_readme` 참고).

**아직 "실험적"으로 표시해둔 이유**: 새 계산 방식이 며칠 더 콘솔(platform.claude.com,
platform.openai.com)의 실제 청구 금액과 비교되며 검증되기 전까지는, 기존 "어제 API 비용 (UTC
기준)" 섹션을 신뢰할 수 있는 기준으로 그대로 둡니다. 두 섹션이 충분히 신뢰할 만하다고 판단되면
그때 UTC 섹션을 KST 정밀 섹션으로 교체할지 별도로 결정할 예정입니다.

## 알려진 이슈 / 주의사항

- **시간 기준은 UTC**: "어제/이번 달/지난주/지난달"과 프로젝트별 비용은 모두 UTC 자정 기준으로
  계산합니다. KST 자정과는 최대 9시간 어긋나므로, 아침 8시(KST) 기준으로 보면 "그저께 낮~어제
  낮(UTC로는 어제 하루)"의 비용이라고 보면 됩니다. KST 기준 하루는 "🧪 KST 정밀" 섹션(실험적)에서만
  보여 주며, 두 기준을 통합할지는 백로그입니다 (PLAN.md 참고).
- **OpenAI 비용 API의 `amount.value`**: 실제 응답에서 숫자/문자열이 섞여 오는 경우가 관측되어
  `float()`로 명시 변환하도록 처리했습니다 (`src/providers/openai_cost.py`).
- **OpenAI 페이지네이션**: `group_by` 파라미터를 쓰면서 `limit`을 지정하지 않으면 `next_page` 토큰이
  깨지는 버그가 커뮤니티에 보고된 적이 있어(2026년 3~4월), 이 프로젝트는 Costs API를 부를 때
  `limit`을 항상 명시합니다(프로젝트별 조회의 `group_by=project_id,line_item` 포함).
- **텔레그램 발송은 기본 plain text**: 구독명/에러 메시지 등에 `_`, `*`, `` ` `` 같은 문자가 섞이면
  Markdown 파싱이 깨져 400 에러가 나기 때문에, 기본값을 plain text로 뒀습니다
  (`src/telegram.py`의 `send_message(text, parse_mode=None)`).
- **시크릿 노출 주의**: 에러 로그/메시지에 봇 토큰이 그대로 노출되지 않도록 처리했지만
  (`telegram.py`), 혹시라도 토큰이 어딘가(채팅, 로그 등)에 평문으로 노출됐다면 `@BotFather`에서
  즉시 재발급하고 GitHub Secrets/`.env`를 갱신하세요.

## 에러 처리 방식

- Anthropic/OpenAI 중 한쪽 조회가 실패해도 나머지는 정상 표시되고, "부분 합계"로 표시됩니다.
  (일간/주간/월간/KST 정밀 섹션 모두 동일)
- 프로젝트별 비용(일일 섹션, 주간·월간의 "프로젝트별" 블록)은 합계와 따로 방어합니다 — 프로젝트별
  조회가 실패해도 그 부분만 "⚠️ … 프로젝트별 조회 실패: …"로 바뀌고 합계 줄은 그대로 나옵니다.
- "🧪 KST 정밀 어제 비용 (실험적)" 섹션은 Anthropic·OpenAI 둘 다 조회에 실패하면 섹션 자체가
  조용히 생략됩니다(실험 기능 하나 때문에 핵심 리포트에 에러를 노출하지 않기 위함).
- Notion 구독 조회가 실패해도(설정 미완료, 네트워크 오류, 필드명 불일치 등) 비용 섹션은 정상
  발송되고 구독 섹션에만 경고 문구가 표시됩니다.
- "🧟 3개월 이상 미이용 추정 구독" 섹션도 동일한 방어 패턴입니다 — Notion 미설정이면
  조용히 생략되고(이미 구독 섹션에 경고가 뜨므로 중복 표시 안 함), 그 외 예상 못 한 에러만
  경고 문구로 표시됩니다.
- D-3 알림/버튼 응답 처리(`reply_processor.py`)가 실패해도 리포트의 나머지 섹션은 정상
  발송됩니다 — 텔레그램 조회 실패, Notion 반영 실패 모두 경고 문구로만 표시되고 예외가 전체
  리포트 발송을 막지 않습니다.
- `budget.json`이 없으면 예산 경고 섹션만 조용히 생략되고, 형식이 깨지면 경고 문구로 표시됩니다
  (리포트 전체는 죽지 않음).
- 그 외 예상 못 한 에러가 나면 전체 리포트 대신 에러 요약을 텔레그램으로 발송합니다(가능한 경우).
  GitHub Actions 실행이 실패(빨간 X)로 표시되므로 Actions 탭에서도 확인 가능합니다.

## 2단계 백로그

- Next.js + Supabase 웹 대시보드, 구독 CRUD UI
- "🧪 KST 정밀" 섹션과 UTC 섹션 통합 여부 결정
- 프로젝트별 비용 확장: 일일 스냅샷 커밋, `/stats` 즉시 조회, 자동 비용 절감 진단 규칙
- Google Gemini API 비용 추가 (GCP BigQuery Billing Export 필요, MVP 아키텍처와 무게감이 다름)
- Voyage AI 비용 추가 (MongoDB Atlas 산하, 공개 비용 API 없음 — 별도 문의 필요)
- 기타 제공사 추가

> 월 예산 초과 경고 / 주간·월간 요약은 완료되어 위 목록에서 빠졌습니다. 상세 이력은 `PLAN.md` 참고.
