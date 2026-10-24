# CostPing — AI 비용 텔레그램 리포터 프로젝트 계획

> AI API 비용 + 구독 결제일을 매일 아침 8시(KST) 텔레그램으로 받는 개인 도구.
> 서버·DB 없음: GitHub Actions cron → Python 스크립트 → Telegram Bot API.

작성일: 2026-07-27 · 상태: **1단계 MVP 완료 + 2단계 세션 1(예산 경고/주간·월간 요약)·세션 2(KST 정밀 비용, 실험적)·세션 3-1(Notion 구독 연동, 읽기 전용)·세션 3-2(D-3 원탭 알림 + 마지막 이용일 기록)·세션 3-3(3개월 이상 미이용 하이라이트) 완료**

---

## 1. 아키텍처 한 장 요약

```
GitHub Actions (cron, 매일 23:00 UTC = 08:00 KST)
  └─ report.py
       ├─ Anthropic Admin API  → 전일/이달 API 비용
       ├─ OpenAI Costs API     → 전일/이달 API 비용
       ├─ Notion "구독 중" DB   → 오늘·내일·D-3 결제 예정 구독 (2단계 세션 3, subscriptions.json 대체)
       ├─ Telegram getUpdates  → D-3 알림 버튼(✅/❌) 응답 회수 (2단계 세션 3-2, 웹훅 없음)
       └─ Telegram Bot API     → 리포트 발송 (D-3 알림엔 인라인 버튼 포함, 실패 시에도 에러 알림 발송)

응답 반영 흐름(요약): 리포트에 D-3 인라인 버튼 발송 → 사용자가 버튼 탭 → 다음날(또는 그 다음)
크론이 돌 때 `getUpdates`로 안 읽은 응답을 모아 Notion "마지막 이용일"에 반영 → 최대 24시간
지연 허용(사용자 확정, 웹훅 서버 없이 stateless cron으로 처리하기 위한 트레이드오프).
```

- 자동 수집: Anthropic + OpenAI (둘 다 **Admin 키** 필요)
- 구독 결제 데이터: Notion "구독 중" DB (구독명, 금액, 결제 주기/시작일, 상태) — 사용자가 이미
  운영 중이던 DB를 그대로 소스로 사용 (2단계 세션 3)
- 시크릿 5개는 전부 GitHub Secrets: `ANTHROPIC_ADMIN_KEY`, `OPENAI_ADMIN_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NOTION_API_KEY`

## 2. repo 구조 (목표)

```
aisaas_costping/
├─ .github/workflows/daily-report.yml
├─ src/
│  ├─ report.py            # 진입점: 수집 → 포맷 → 발송
│  ├─ providers/
│  │  ├─ anthropic_cost.py
│  │  ├─ openai_cost.py
│  │  └─ notion_subscriptions.py  # Notion "구독 중" DB 조회/기록 (2단계 세션 3)
│  ├─ budget.py            # 월 예산 초과 경고 (2단계 세션 1)
│  ├─ weekly_monthly.py    # 주간/월간 요약 날짜 범위 계산 (2단계 세션 1)
│  ├─ reply_processor.py   # D-3 버튼 응답 회수 → Notion 반영 (2단계 세션 3-2)
│  └─ telegram.py          # 발송(+인라인 버튼) + getUpdates 폴링 + 에러 알림
├─ notion_config.json       # Notion 구독 DB의 database_id 등 (2단계 세션 3)
├─ budget.json              # 월 예산 한도 설정 (2단계 세션 1)
├─ PLAN.md                 # 이 문서
└─ README.md
```

## 3. 세션 계획 (저녁 1~2시간 단위, 각 세션은 그 자체로 완결)

### 세션 0 — 준비물 확보 (코딩 없음, 30분~1시간)
- [ ] GitHub repo 생성 (`aisaas_costping`), 로컬 폴더와 연결 (git init → remote 연결)
- [ ] 텔레그램 @BotFather 로 봇 생성 → **봇 토큰** 확보
- [ ] 봇에게 아무 메시지 1개 보낸 뒤 `getUpdates`로 **chat_id** 확인
- [ ] Anthropic Console → Organization Settings에서 **Admin API 키**(`sk-ant-admin...`) 발급
- [ ] OpenAI Platform → Organization Settings에서 **Admin Key** 발급
- [ ] 4개 값을 repo Settings → Secrets and variables → Actions 에 등록
- ✅ 완료 기준: Secrets 4개 등록 완료

### 세션 1 — 텔레그램 발송 + 구독 알림 (첫 동작하는 결과물)
- [ ] `subscriptions.json` 스키마 정의 + 실제 구독 입력 (예: Claude Pro, ChatGPT Plus…)
- [ ] `src/telegram.py`: sendMessage 래퍼 (환경변수에서 토큰/chat_id 읽기)
- [ ] `src/subscriptions.py`: Asia/Seoul 기준 오늘·내일 결제 예정 계산 (`zoneinfo` 사용, 29/30/31일 → 말일 짧은 달 처리)
- [ ] `src/report.py` 최소 버전: 구독 정보만 담아 로컬에서 1회 발송 성공
- ✅ 완료 기준: 내 텔레그램으로 구독 리포트 수신
- 💡 API 수집 없이도 이 시점부터 매일 쓸 수 있는 도구가 됨

### 세션 2 — Anthropic 비용 수집
- [ ] `src/providers/anthropic_cost.py`
  - `GET https://api.anthropic.com/v1/organizations/cost_report`
  - 헤더: `x-api-key: <Admin키>`, `anthropic-version: 2023-06-01`
  - 전일(KST 기준) 1일치 + 이달 1일~어제 누적, `has_more` 페이지네이션 처리
- [ ] 리포트 메시지에 "Anthropic: 전일 $X / 이달 $Y" 반영
- ✅ 완료 기준: 실제 비용 숫자가 텔레그램에 표시

### 세션 3 — OpenAI 비용 수집
- [ ] `src/providers/openai_cost.py`
  - `GET https://api.openai.com/v1/organization/costs`
  - 헤더: `Authorization: Bearer <Admin Key>`, 파라미터 `start_time`(unix), `bucket_width=1d`
- [ ] 제공사별 + 합계 포맷 완성 (전일 합계, 이달 누적 합계)
- ✅ 완료 기준: 두 제공사 + 합계가 한 메시지에 표시

### 세션 4 — GitHub Actions 자동화
- [ ] `.github/workflows/daily-report.yml`
  - `schedule: cron '0 23 * * *'` (UTC 23:00 = **KST 다음날 08:00**)
  - `workflow_dispatch` 수동 실행 트리거 추가 (테스트용)
  - Python 3.11 셋업 → `pip install requests` → `python src/report.py`
  - Secrets → env 주입
- [ ] 수동 실행(workflow_dispatch)으로 1회 성공 확인
- ✅ 완료 기준: 다음날 아침 8시에 자동 수신

### 세션 5 — 안정화 + 마무리
- [ ] 에러 핸들링 점검: 한 제공사 실패 시 나머지는 정상 발송 + 실패 사실 명시, 전체 실패 시 traceback 요약을 텔레그램으로 발송 (조용히 죽지 않기)
- [ ] API 응답 지연/빈 데이터(전일 사용량 0) 케이스 처리
- [ ] README.md 작성 (셋업 방법, Secrets 목록, 구독 추가 방법)
- ✅ 완료 기준: 일주일 무인 운영 시작

## 4. 메시지 포맷 초안

```
📊 CostPing — 7/27(월)

어제 API 비용
· Anthropic  $1.23
· OpenAI     $0.45
· 합계        $1.68

이번 달 누적: $34.20

💳 결제 예정
· 오늘: Claude Pro $20
· 내일: ChatGPT Plus $20
```

## 5. 기술 메모 (구현 시 주의)

- **Anthropic**: 일반 API 키로는 사용량 조회 불가 → 반드시 Admin 키(`sk-ant-admin...`). `cost_report`의 `bucket`은 UTC 기준 → KST '전일'과 경계가 어긋나므로 MVP에서는 "UTC 기준 전일"로 단순화하고 리포트에 명시 (정밀화는 백로그).
- **OpenAI**: Admin Key는 조직 Owner만 발급 가능. `start_time`은 unix seconds.
- **데이터 신선도**: 두 API 모두 집계 반영에 지연 가능 → 아침 8시 발송이면 전일 데이터는 충분히 안정.
- **cron 주의**: GitHub Actions cron은 UTC이고 정시에서 수 분 지연될 수 있음(정상).
- **의존성**: `requests` 하나만. 시간대는 표준 라이브러리 `zoneinfo("Asia/Seoul")`.

## 6. 2단계 백로그 (지금 하지 않음)

- Next.js + Supabase 웹 대시보드, 구독 CRUD UI
- **Google Gemini API 비용 추가** (2026-07-27 조사): Anthropic/OpenAI처럼 간단한 Admin API 키로 비용 조회 불가. AI Studio 대시보드 전용, 프로그래밍 조회는 GCP Cloud Billing BigQuery Export 구축 필요(별도 GCP 프로젝트 연결 + 서비스 계정 + BigQuery 쿼리) — MVP 아키텍처(서버 없는 GitHub Actions 스크립트)와 무게감이 다름
- **Voyage AI 비용 추가** (2026-07-27 조사): MongoDB Atlas 산하로 통합됨. 공개 REST 비용/사용량 API 없음, Atlas UI(Usage/Cost Explorer/Invoices)로만 확인 가능. 프로그래밍 연동은 MongoDB에 별도 문의 필요
- 기타 제공사 추가
- **"🧪 KST 정밀" 섹션과 UTC 섹션 통합 여부** (2026-10-24 기록): 프로젝트별 비용 섹션을 cost_report와
  정확히 대조하기 위해 UTC 기준으로 정했다(KST로는 cost_report 1d 버킷과 대조 불가). 그 결과 리포트에
  UTC 기준(합계·프로젝트별)과 KST 기준(실험 섹션)이 공존한다. 실험 섹션 유지/제거/통합은 별도 판단
- 프로젝트별 섹션 반올림 표시 차이(예: $0.38+$0.08=$0.46 vs 합계 $0.45, 실제 값은 일치) — 현재는 그대로 둠
- 프로젝트별 비용 2단계: 일일 스냅샷 `data/daily/*.json` 커밋, 웹 대시보드, `/stats` 즉시 조회, 자동 비용 절감 진단 규칙

> ~~월 예산 초과 경고, 주간/월간 요약 리포트~~ → 2단계 세션 1에서 완료 (아래 7번 참고)
> ~~KST 정밀 일 경계~~ → 2단계 세션 2(세션 A~C)에서 완료. "🧪 KST 정밀 어제 비용(실험적)" 섹션으로
> report.py에 통합됨(기존 UTC 섹션과 나란히 공존, 아래 7번 참고).

## 7. 2단계 진행 로그

### 2단계 세션 1 — 월 예산 초과 경고 + 주간/월간 요약 (2026-07-28)
- [x] `budget.json` + `src/budget.py`: 이번 달 API 비용이 `monthly_budget_usd`의
      `warning_threshold_pct`(기본 80%)를 넘으면 ⚠️, 100%를 넘으면 🚨 경고를 리포트에 추가.
      파일이 없거나 한도 0이면 조용히 생략(옵트인 기능).
- [x] `src/weekly_monthly.py` + `src/providers/*.get_cost_for_range()`: 별도 cron/DB 없이 기존
      매일 08:00 실행에 조건부로 "매주 월요일 → 지난주 요약", "매월 1일 → 지난달 요약" 섹션 추가.
- [x] `src/report.py`에 통합, 기존 방어 패턴(제공사 개별 실패해도 나머지 정상 발송)을 새 섹션에도
      동일 적용.
- ✅ 완료 기준: 로컬에서 provider 함수를 mock해 평일/월요일/1일 케이스, budget.json 없음/깨짐/한도 0
      케이스를 모두 검증(정상 동작 확인). 실제 배포는 사용자가 GitHub에 커밋/푸시 + Actions 수동
      실행(workflow_dispatch)으로 최종 확인 필요.
- 📝 다음에 이어갈 것: `budget.json`의 `monthly_budget_usd`(기본값 50) 실제 지출 규모에 맞게 조정.
- [x] (추가 조정, 2026-07-28) 실배포 확인 결과 이번 달 누적이 기본 한도($50)를 이미 넘어서 있어,
      사용자 요청으로 `budget.json`에 `daily_budget_usd`(하루 $5)를 추가하고 `monthly_budget_usd`를
      $60으로 조정. `src/budget.py`가 하루/월 한도를 각각 독립적으로 체크하도록 리팩터링
      (`build_budget_warnings()`가 0~2줄 경고를 반환), `src/report.py`의 `build_cost_section()`이
      어제 하루 비용도 함께 반환하도록 변경. 실제 사용자 리포트 숫자($5.94/$71.27)로 재현 테스트해
      하루·월 두 경고가 모두 정상 표시됨을 확인.

### 2단계 세션 2 — KST 정밀 일 경계 (진행 중, 세션 A 완료: 2026-07-28)

**조사 결과 (착수 전 필수 확인 사항)**:
- Anthropic Cost Report API, OpenAI Costs API 둘 다 `bucket_width`가 **하루(1d)만 지원**한다
  (공식 문서 확인). 그 하루 버킷은 UTC 자정에 고정되는 것으로 보임(Anthropic 공식 쿡북 예제 코드
  주석: "snap to start of day to align with bucket boundaries") — 즉 `starting_at`을 KST 자정으로
  바꿔서 넣어도 진짜 KST 기준으로 하루가 안 끊길 가능성이 높다. 그래서 애초에 **Cost API만으로는
  KST 정밀화가 불가능**하다고 결론.
- 대안은 **Usage API**(`GET /v1/organizations/usage_report/messages` 등, `bucket_width=1h` 지원,
  최대 168버킷=7일치)로 토큰 사용량(모델별 input/output/cache 토큰 개수)을 받아와서, 직접 유지하는
  단가표로 USD를 계산하는 것. 이러면 KST 자정에 정확히 맞는 24시간 구간을 그대로 질의할 수 있다.
- 다만 실제 단가 체계가 훨씬 복잡함을 확인: 모델별 input/output 단가 외에 5분/1시간 캐시 쓰기 요율,
  캐시 읽기 요율, 20만 토큰 초과 컨텍스트 요율, 배치(batch) 50% 할인, 데이터 레지던시 +10% 할증이
  다 따로 있고, 가격 자체도 수시로 바뀜(예: Claude Sonnet 5는 2026-09-01에 프로모션 요율 종료 예정).
  이걸 다 반영한 완전판 단가표는 유지보수 부담이 커서, **사용자 결정으로 "간소화판"**
  (input/output/캐시 읽기 + 5분 캐시 쓰기만 반영, 나머지는 감지 시 경고 후 근사치 처리)으로 범위를
  좁힘. OpenAI/Anthropic 공식 가격 페이지에서 확인한 현재 가격을 `prices.json`에 반영.

**세션 A 완료 (계산 엔진만, 아직 report.py에 연결 안 함 — 안전하게 격리된 상태)**:
- [x] `prices.json` (신규): 모델 접두사별 USD/100만 토큰 단가표. `_readme`에 반영 범위/한계와
      공식 가격 페이지 링크 명시.
- [x] `src/pricing.py` (신규): `calc_anthropic_cost_usd()` / `calc_openai_cost_usd()` — 토큰
      개수를 받아 USD로 환산하는 순수 계산기. 단가표에 없는 모델을 만나면 예외를 던지지 않고
      경고 문구를 반환(그 모델분은 0으로 집계에서 제외 + 리포트에 명시할 수 있게).
- [x] `src/kst_bucket.py` (신규): `kst_day_to_utc_range()` — 주어진 KST 캘린더 날짜에 정확히
      대응하는 UTC 24시간 구간을 계산(한국은 DST 없어 연중 고정 UTC+9). `kst_range_to_utc_range()`로
      여러 날짜 범위도 지원(주간/월간용). `group_records_by_kst_date()`로 넓은 구간에 걸친 시간별
      레코드를 KST 날짜별로 재구성 가능.
- [x] 합성 데이터로 단위 테스트: 가격 계산 정확성(수기 검산 일치), KST 자정 경계(UTC 15:00) 케이스,
      연중 DST 없음 확인, KST 날짜 그룹핑 경계 케이스(UTC 14시/15시/23시) 모두 통과.
- ✅ 완료 기준: 계산 엔진이 report.py와 분리된 상태로 정확성 검증됨. 아직 실제 Anthropic/OpenAI
      Usage API를 호출하는 코드는 없음 — 실제 API 응답 형태(정확한 모델 ID 문자열, 필드명)를
      모르는 채로 어댑터를 짜는 게 더 위험하다고 판단해 다음 세션으로 미룸.

**세션 B 완료 (2026-07-28)**: Anthropic Usage API 실제 연동.
- [x] `scripts/explore_anthropic_usage.py`로 실제 응답 확인. 실제 등장한 모델 ID:
      `claude-sonnet-5`(접미사 없음), `claude-sonnet-4-6`(접미사 없음),
      `claude-haiku-4-5-20251001`(날짜 접미사 있음) — 셋 다 `prices.json`의 기존 접두사
      (`claude-sonnet-5`/`claude-sonnet-4`/`claude-haiku-4`)와 겹치지 않고 정확히 매칭됨,
      단가표 수정 불필요.
- [x] 실제 응답 스키마 확인: `cache_creation`이 `{ephemeral_1h_input_tokens, ephemeral_5m_input_tokens}`
      중첩 객체로 옴(당초 가정한 단일 `cache_creation_input_tokens` 필드가 아니었음). 이번 사용자
      샘플에서는 `service_tier`/`context_window`가 전부 `null`(배치·20만 토큰 초과 없음).
- [x] `src/providers/anthropic_cost.py`에 `get_kst_precise_cost_for_day(kst_date)` 추가:
      `kst_bucket.kst_day_to_utc_range()`로 계산한 UTC 24시간 구간을 `usage_report/messages`에
      `bucket_width=1h`로 질의 → 모델별로 `pricing.calc_anthropic_cost_usd()`로 환산 → 합산.
      1시간 캐시 쓰기·`service_tier`≠standard·`context_window` 존재·웹 검색 도구 사용을 감지하면
      경고 문구를 남기고(그 부분은 비용 계산에서 제외 또는 근사치), 단가표에 없는 모델을 만나도
      죽지 않는다(`pricing.CostAccumulator`).
- [x] 사용자가 붙여넣은 실제 응답 1건 + 1시간 캐시/batch/context_window/웹검색/미등록 모델을 모두
      포함한 합성 케이스로 재현 테스트 — 수기 검산과 정확히 일치, 경고 5종 모두 정상 트리거 확인.
- ✅ 완료 기준: `python src/providers/anthropic_cost.py` 실행 시 기존 UTC 기준 값과 새 KST 정밀
      (실험적) 값이 나란히 출력됨. **아직 report.py에는 연결 안 함** — 다음 세션 전에 사용자가 며칠
      실행해보며 콘솔 실제 청구액과 비교해볼 것.
- 🐛 **버그 발견+수정**: `anthropic_cost.py`를 report.py 경유 없이 단독 실행하면 `.env`를 안 읽어서
  `ANTHROPIC_ADMIN_KEY` 없음 에러가 났음(세션 B에서 처음 단독 실행하며 발견 — 원래부터 있던 결함).
  report.py와 동일한 `_load_dotenv_if_present()`를 추가해 수정, 재실행으로 확인 완료.
- 사용자가 실제로 돌려본 결과(2026-07-28): UTC 어제 $5.94 vs KST 정밀(실험적) $6.35 — 서로 다른
  24시간 창(UTC 자정 vs KST 자정)이라 다른 게 정상. 경고 2건 실제 트리거(1시간 캐시 18,126토큰
  미반영, 웹 검색 10회 미반영) — 이 사용자는 1시간 캐시를 실제로 쓰고 있어 간소화판의 근사치 갭이
  0이 아님을 확인.

**세션 C 완료 (2026-07-28)**: OpenAI Usage API 연동 + report.py 통합.
- [x] `src/providers/openai_cost.py`에 `get_kst_precise_cost_for_day(kst_date)` 추가.
      `GET /v1/organization/usage/completions`를 `bucket_width=1h`로 조회 → 모델별
      `pricing.calc_openai_cost_usd()`로 환산. 오디오 토큰(입력/출력)·batch 처리·기본이 아닌
      `service_tier`를 감지하면 경고를 남기고 그 부분은 계산에서 제외. **batch 감지 시 경고 방향이
      Anthropic 쪽과 반대**라는 점 문서화함 — batch는 실제로 50% 할인이라, 감지되면 이 함수의
      계산값이 오히려 실제보다 **높게**(과대 계상) 나올 수 있음.
  - 이 함수도 단독 실행 시 `.env`가 안 읽히는 문제를 처음부터 방지하도록 `_load_dotenv_if_present()`를
    같이 추가함(세션 B에서 발견한 버그를 미리 반영).
- [x] `report.py`에 `_build_kst_precise_section_safe()` 추가, "🧪 KST 정밀 어제 비용 (실험적)"
      섹션을 기존 "어제 API 비용 (UTC 기준)" 섹션 바로 아래(예산 경고 다음, 구독 알림 이전)에
      **나란히** 추가(교체 아님). 두 제공사 다 조회 실패하면 이 실험적 섹션 자체를 조용히 생략해서,
      실험 기능 하나 때문에 핵심 리포트에 눈에 띄는 에러가 뜨지 않게 함(한쪽만 실패하면 "부분 합계"로
      표시 — 기존 UTC 섹션·주간/월간 요약과 동일한 방어 패턴).
- [x] 합성 데이터로 OpenAI 계산 정확성(수기 검산 일치, 오디오/batch/service_tier/미등록 모델 경고
      트리거) 검증, `report.py` 전체 발송 경로에서 정상 케이스·두 제공사 다 실패·한쪽만 실패 3가지
      시나리오 모두 재현 테스트 통과.
- ✅ 완료 기준: `report.py`가 실행되면 UTC 섹션 바로 아래 KST 정밀(실험적) 섹션이 붙어서 발송됨.
      실제 OpenAI 계정으로는 아직 검증 안 함(사용자가 OpenAI Admin 키로 실행해서 확인 필요).

**2단계 세션 2 전체 완료.** 다음 2단계 작업은 남은 백로그(6번 항목: Next.js+Supabase 대시보드,
Gemini/Voyage AI 비용, ChatGPT 구독 추가) 중에서 고르면 된다. "KST 정밀 어제 비용(실험적)" 섹션이
며칠 지켜봐서 신뢰할 수 있다고 판단되면, 그때 기존 "어제 API 비용 (UTC 기준)" 섹션을 이걸로 대체할지
별도로 결정한다(지금은 나란히 공존하는 상태).

### 2단계 세션 3 — Notion 구독 연동 (`/product-management:brainstorm`으로 시작, 2026-07-28)

**배경**: 사용자가 이미 Notion DB로 OTT/AI 구독 현황을 관리 중인데, Anthropic/OpenAI처럼 API로
사용량이 자동 추적되는 것과 달리 OTT류는 안 쓰는데도 매달 계속 결제되는 문제("좀비 구독")가
있었음. 브레인스토밍으로 다음을 확인:
- OTT 사용량은 공개 API가 없어 자기보고(self-report)가 필수. Notion에는 "마지막 이용일"/"사용
  빈도" 필드가 아직 없음(사용자 확인).
- 웹 대시보드 신설은 Notion이 이미 그 역할을 하고 있어 불필요하다고 판단, Notion 기반으로 방향을
  정함.
- 실제 Notion을 조회해보니(MCP `notion-fetch`/`notion-query-data-sources`) 애초에 예상했던
  "구독 관리" DB(4개 항목, AWS/OpenAI/Cursor/Notion)는 실사용 중인 DB가 아니었고, 사용자가 직접
  링크로 알려준 "구독 중" DB(정기 구독 3 페이지 하위, 14개 항목: Notion/Cursor/OpenAI
  API/Supabase/Claude/Google Gemini/Voyage AI + OTT 3개(디즈니 플러스/티빙/넷플릭스) + 해지된
  쿠팡)가 실제 소스였음. 이 DB는 "다음 결제일" 수식 필드까지 이미 갖추고 있어 예상보다 기반이
  탄탄했음. 기존 `subscriptions.json`("Claude MAX" $100/매월 18일)이 실제 Notion 데이터("Claude"
  $22/5·29 시작)와 어긋나 있는 것도 이때 발견 — 이번 전환으로 자연 해소.
- 원탭 응답을 Notion에 반영하는 방식은 "서버 없음" MVP 원칙을 지키기 위해, 실시간 webhook 대신
  매일 08:00 크론에서 텔레그램 `getUpdates`로 전날 이후 응답(버튼 `callback_query` 포함)을 모아
  처리하는 방식으로 정함(최대 24시간 지연 허용, 사용자 확정). 오프셋을 따로 저장하지 않아도
  된다 — 응답을 확인 처리(offset 전달)하지 않으면 텔레그램이 계속 "미확인" 상태로 들고 있어서,
  다음 크론 때도 그대로 받아올 수 있음(stateless cron과 호환).
- 3세션으로 분할 합의: **세션 1** Notion을 결제 알림의 읽기 전용 소스로 전환 → **세션 2** "마지막
  이용일" 필드 추가 + D-3 알림에 인라인 버튼(✅ 이번 달 씀 / ❌ 안 씀) + 다음날 크론에서 응답 반영
  → **세션 3(선택)** 월간 리포트에 "3개월 이상 미이용" 하이라이트.

**세션 3-1 완료 (2026-07-28)**: Notion → 결제 알림 읽기 전용 전환.
- [x] `src/providers/notion_subscriptions.py` (신규): `결제 시작일` + `결제 주기`(월/연)로 다음
      결제일을 직접 계산(`_next_payment_date`, 말일 보정 포함 — Notion의 "다음 결제일" 수식
      필드는 공개 API로 계산 로직을 못 가져와서 동일 로직을 재구현). 이름/결제 시작일/결제 주기
      중 하나라도 비어 있는 행(빈 줄 등)은 건너뜀. `NOTION_API_KEY` 미설정/`notion_config.json`
      문제 시 `NotionConfigError`.
- [x] `notion_config.json` (신규): `database_id`, `active_status_value`("구독 중"). 시크릿 아님,
      커밋됨.
- [x] `src/report.py`: `subscriptions.py`/`subscriptions.json` 의존 제거, `_build_subscription_section_safe()`가
      `notion_subscriptions.get_due_today_and_tomorrow()`를 호출하도록 교체. 기존 방어 패턴(조회
      실패해도 나머지 리포트는 정상 발송) 그대로 유지.
- [x] `src/subscriptions.py`, `subscriptions.json` 삭제 (완전히 대체됨).
- [x] `.github/workflows/daily-report.yml`에 `NOTION_API_KEY` env 추가(사용자가 직접 반영 —
      워크플로 yml은 원격 도구로 못 씀).
- [x] README.md: 아키텍처/구조도/셋업 순서(Notion Integration 발급+DB 연결 단계 추가)/"구독
      관리 방법(Notion)" 섹션 신설, 기존 "구독 추가/수정 방법(subscriptions.json)" 섹션 제거.
- 🐛 **실제 연동 중 발견+해결 (트러블슈팅 전 과정)**:
  1. 처음엔 Notion의 "데이터소스(data source)" 단위 쿼리 엔드포인트(`POST
     /v1/data_sources/{id}/query`, `Notion-Version: 2025-09-03`)로 구현했는데, 실행 시 404
     (`object_not_found`, 대상 ID: `c8b66ca4-...`)가 남.
  2. 진단 스크립트(`scripts/explore_notion_subscriptions.py`, 신규)로 3단계 확인: 토큰 유효
     (200), DB 자체 접근도 처음엔 200 — 그런데 `data_sources` 필드가 빈 배열. 오래되고 안정적인
     **데이터베이스 단위 쿼리 엔드포인트**(`POST /v1/databases/{database_id}/query`,
     `Notion-Version: 2022-06-28`)로 바꿔봤지만, 이번엔 DB 접근 자체가 400
     (`validation_error`, "does not contain any data sources accessible by this API bot")로
     바뀜 — Notion 공식 업그레이드 가이드(developers.notion.com/docs/upgrade-guide-2025-09-03)
     확인 결과 "데이터베이스 연결만으로는 불충분, 데이터소스별 접근 권한이 별도로 필요"함을 확인.
  3. Integration 연결 해제 후 재연결해도 동일. 완전히 새로 만든 다른 기존 DB("할일 DB")로
     테스트했더니 200 정상 — **Integration 자체는 문제 없고, "구독 중" 이 DB(Notion이 데이터소스
     개념을 도입하기 전에 만들어진 것으로 추정)에만 있는 특이 케이스로 확정**.
  4. "구독 중"을 Duplicate로 복제해서 우회를 시도했으나, 복제본도 원본과 동일한 데이터소스 ID
     (`c8b66ca4-...`)를 그대로 참조해서 실패(Notion의 Duplicate가 데이터를 복사하되 데이터소스
     객체 자체는 새로 안 만드는 것으로 보임).
  5. **최종 해결**: Notion MCP(`notion-create-database`, `notion-create-pages`)로 완전히 새로운
     데이터베이스를 API로 직접 생성(부모: "정기 구독 3" 페이지, 스키마는 원본과 동일하게 이름/
     상태/유형/결제 시작일/결제 주기/결제 금액(KRW,USD)/비고)하고, 원본의 실제 12개 구독 행(활성
     11 + 해지 1)을 그대로 옮겨 넣음. 완전히 새로 생성된 데이터소스라 Integration 연결이 정상
     작동함을 확인. `notion_config.json`의 `database_id`를 이 새 DB(`74ccacf1-eeb8-4878-bfe7-dd7d7416a7a2`)로
     최종 반영. **앞으로 구독 추가/해지/금액 변경은 이 새 DB에서 해야 리포트에 반영된다** — 원본
     "구독 중"은 이 특이 케이스 때문에 더 이상 CostPing의 소스로 쓸 수 없음(사용자가 원본/복제본
     정리 완료).
- [x] 로컬 검증(mock, 실제 Notion API 호출 없이): `_next_payment_date` 말일 보정 케이스,
      `_parse_subscription_page` 파싱(정상/빈 행), `report.build_report()` 전체 조립 3가지 시나리오
      (NOTION_API_KEY 없음 → 건너뜀 경고, 정상 조회 mock, 예상 못한 예외 → 실패 경고) 모두 통과.
      `py_compile`로 문법 확인 완료.
- [x] **실제 Notion API로 최종 검증 완료(2026-07-28)**: `python src/providers/notion_subscriptions.py`
      실행 결과 활성 구독 11개가 정확한 다음 결제일과 함께 출력됨(예: Cursor 7/28=오늘, Claude
      7/29=내일, 넷플릭스 8/23, 티빙 8/12, 디즈니 플러스 9/16 — 이 값들은 Notion 화면에 표시되는
      "다음 결제일" 수식 값과 전부 일치함, 말일 보정 로직 정확성 재확인). 구독 해지 상태인 쿠팡은
      정상적으로 제외됨. "오늘 결제"/"내일 결제" 분류도 정확함.
- ✅ **완료 기준 충족**: 코드/문서 정합성, mock 기반 방어 패턴, 실제 Notion API 연동 세 가지 모두
      검증 완료. 2단계 세션 3-1(Notion 읽기 전용 전환) 전체 완료.
- 📝 다음: 세션 2(마지막 이용일 필드 + D-3 원탭 알림 + 익일 크론 반영) 진행 가능. 단, 이제
      구독 편집은 새 DB(`구독 중` 전용 연동 DB, 위 5번 참고)에서 이뤄진다는 점을 감안해서 설계.

**세션 3-2 완료 (2026-07-28)**: "마지막 이용일" 필드 + D-3 원탭 알림 + 익일 크론 응답 반영.
- [x] Notion 데이터소스(`c872fed8-f123-4b79-96cb-d320e394241a`, `구독 중 v2`)에 `마지막 이용일`
      (DATE) 컬럼을 MCP `notion-update-data-source`로 직접 추가. 사람이 채우는 필드가 아니라
      코드가 텔레그램 버튼 응답으로 자동으로 채우는 필드라, 기존 11개 구독 행은 빈 값으로 시작.
- [x] `src/providers/notion_subscriptions.py`:
  - `_parse_subscription_page`가 이제 `page` 전체(properties 아님)를 받아 `page_id`와
    `마지막 이용일` → `last_used_date`(date | None)까지 함께 반환하도록 확장.
  - `get_due_in_days(days)` 추가: 오늘+days일 결제 예정 구독만 필터링(D-3 알림에 사용). D-day가
    아니라 D-3에 물어보는 이유는 해지 여부를 실제 결제 전에 판단할 시간을 주기 위함.
  - `update_last_used_date(page_id, used_date)` 추가: `PATCH /v1/pages/{page_id}`로 "마지막
    이용일"만 덮어씀. "❌ 안 씀" 응답은 이 함수를 호출하지 않음 — 필드를 그대로 묵혀둬야 세션
    3(선택)의 "N개월째 미사용" 판단이 가능해짐.
- [x] `src/telegram.py`:
  - `send_message_with_keyboard(text, keyboard)` 추가 — `reply_markup.inline_keyboard` 포함
    발송.
  - `get_updates(offset=None)` 추가 — `timeout=0`(즉시 응답, 크론 잡이라 롱폴링 불필요).
  - `confirm_updates(last_update_id)` 추가 — `offset=last_update_id+1`로 한 번 더 호출해
    텔레그램 서버 쪽에서 확인(ack) 처리시킴. 이게 유일한 "상태 저장"이고 텔레그램 서버가 대신
    들고 있어주므로 우리 쪽엔 상태 파일/DB가 전혀 필요 없음(브레인스토밍 때 결정한 설계 그대로
    구현).
- [x] `src/reply_processor.py` (신규): `process_pending_replies()` — `get_updates()`로 미확인
      업데이트를 모아 `callback_data`가 `lu|<page_id>|y`/`lu|<page_id>|n` 형식인 것만 처리.
      `y`는 `update_last_used_date`로 오늘 날짜 기록, `n`은 기록 변경 없이 확인 문구만 생성.
      관계없는 업데이트(일반 메시지 등)는 조용히 무시. 처리 대상이 있었으면 가장 큰
      `update_id`로 `confirm_updates` 호출. 응답이 하나도 없으면 빈 리스트 반환(리포트에 섹션
      자체를 안 붙임 — 매일 "처리할 응답 없음"을 보여주는 건 노이즈).
- [x] `src/report.py`:
  - `_build_reply_processing_section_safe()` 추가 — 헤더 바로 다음 섹션으로 배치("📋 지난 답변
    처리"), 처리 결과가 없으면 섹션 자체 생략.
  - `_build_d3_alert_section_and_keyboard()` 추가 — D-3 대상 구독마다 "✅ {이름} 씀"/"❌ {이름}
    안 씀" 버튼 한 행씩 구성, `callback_data`는 `lu|{page_id}|y`/`lu|{page_id}|n`.
  - `build_report()`가 이제 `(텍스트, 인라인_키보드_행_리스트)` 튜플을 반환하도록 변경(기존
    문자열 단일 반환에서 변경 — breaking change지만 `main()`도 같이 수정해 호환).
  - `main()`: 키보드가 비어 있으면 기존처럼 `telegram.send_message()`, 있으면
    `telegram.send_message_with_keyboard()`를 호출하도록 분기.
  - 다른 섹션들과 동일한 방어 패턴 유지: Notion 조회 실패/미설정이어도 D-3 섹션만 생략되고
    나머지 리포트는 정상 발송.
- [x] 로컬 검증(mock, 실제 텔레그램/Notion 호출 없이 — `scripts/_test_session2.py`, 커밋 전
      삭제 대상): `_parse_subscription_page`의 `page_id`/`last_used_date` 파싱,
      `get_due_in_days`의 D-3 필터링, `update_last_used_date`의 PATCH 본문 형식,
      `process_pending_replies`의 y/n 분기 + 무관 업데이트 무시 + `confirm_updates` 호출,
      `_build_d3_alert_section_and_keyboard`의 키보드 구성, `build_report()`의 튜플 반환까지
      6가지 시나리오 모두 통과. `py_compile`로 문법 확인 완료.
- ⚠️ **실제 텔레그램 버튼 응답으로 아직 검증 안 됨**: 위 항목은 전부 mock 기반 검증이고, 실제로
      리포트를 받아 버튼을 눌러보고 다음날 크론이 Notion에 정확히 반영하는지는 GitHub Actions가
      돌 때(또는 사용자가 로컬에서 `python src/report.py` 두 번 실행 — 한 번은 알림 발송, 버튼
      클릭 후 한 번 더 실행해 반영 확인) 실사용 검증이 필요함.
- 📝 다음: 세션 3(선택) — 월간 리포트에 "마지막 이용일" 기준 3개월 이상 미이용 구독 하이라이트.
      `last_used_date`가 이미 `fetch_active_subscriptions()` 반환값에 포함되어 있어 바로 활용
      가능.

**세션 3-3 완료 (2026-07-28)**: 월간 리포트에 "3개월 이상 미이용 추정 구독" 하이라이트.

- **판단 기준 설계**: `마지막 이용일`은 D-3 알림에서 "✅ 씀"을 눌러야만 채워지고("❌ 안 씀"은
  갱신 안 함 — 세션 3-2 참고), 이 필드가 2026-07-28에 막 추가돼서 기존 구독은 전부 빈 값으로
  시작한다는 점이 문제였다. `last_used_date`가 비어 있다고 무조건 "미이용"으로 몰면, 아직 D-3
  알림을 한 번도 못 받은 최근 시작 구독까지 오탐된다. 그래서 두 갈래로 나눴다:
  1) `last_used_date`가 있으면 그 날짜 기준으로 경과 개월 계산.
  2) 없으면 `결제 시작일`(새로 노출한 `start_date`) 기준으로 경과 개월 계산 — 시작한 지
     3개월이 채 안 된 구독은 "아직 판단할 데이터 없음"으로 보고 제외.
- [x] `src/providers/notion_subscriptions.py`:
  - `_months_between(early, late)` 추가 — 두 날짜 사이 만(滿) 개월 수 계산(일(day) 단위까지
    비교해서 "3개월 딱 채웠는지" 정확히 판정, 말일 케이스도 처리).
  - `_parse_subscription_page`/`fetch_active_subscriptions` 반환값에 `start_date`(결제
    시작일, date 객체) 필드 추가 — 기존 `next_payment_date` 계산에 이미 쓰던 값을 그대로
    노출만 한 것이라 다른 함수에 영향 없음.
  - `get_unused_subscriptions(months=3)` 추가: 활성 구독 중 위 기준으로 `months`개월(기본 3)
    이상 지난 것만 걸러 `unused_reason`("last_used"|"never_confirmed")·`reference_date`·
    `months_since`를 덧붙여 반환. 안 쓴 지 오래된 순(내림차순)으로 정렬.
- [x] `src/report.py`: `_build_unused_subscription_highlight_safe(today)` 추가, "🗓️ 지난달
      요약" 섹션 바로 다음(매월 1일에만)에 "🧟 3개월 이상 미이용 추정 구독"으로 표시.
      다른 `_..._safe` 함수들과 동일한 방어 패턴 — 매월 1일이 아니면 Notion 조회 자체를
      안 함(`get_unused_subscriptions` 호출 안 됨, 목 테스트로 확인), `NotionConfigError`는
      조용히 생략(이미 구독 섹션에서 경고가 뜨므로 중복 표시 안 함), 대상 없으면 생략,
      예상 못 한 에러만 경고 문구로 표시.
- [x] 로컬 검증(mock, 실제 Notion/텔레그램 호출 없이):
  - `_months_between` 경계 케이스(정확히 3개월/하루 모자람/말일 보정/역순) 전부 통과.
  - `get_unused_subscriptions` 시나리오 5종(마지막 이용 4개월 전=포함, 2개월 전=제외,
    이용 기록 없음+시작 5개월 전=포함, 이용 기록 없음+시작 1개월 전=제외, 정확히
    3개월 전=경계값 포함) + 정렬 순서 + 빈 목록 모두 통과.
  - `_build_unused_subscription_highlight_safe` 시나리오 5종(1일 아님→조회 자체 안 함,
    NotionConfigError→조용히 생략, 예상 못 한 에러→경고, 대상 없음→생략, 정상 케이스→포맷
    확인) 모두 통과.
  - `build_report()` 전체 조립 목 테스트(오늘=8/1로 고정, 나머지 섹션 전부 mock)로 "🗓️ 지난달
    요약" 바로 아래에 "🧟 3개월 이상 미이용 추정 구독" 섹션이 정상적으로 나란히 붙는 것까지
    확인. `py_compile`로 문법 확인 완료.
- ✅ 완료 기준 충족: 계산 로직 정확성 + report.py 통합 + 방어 패턴 모두 mock 기반으로 검증.
      **실제 Notion 데이터로는 아직 검증 안 함** — 이 필드가 오늘(2026-07-28) 막 생겼기 때문에
      실사용 검증은 최소 한 번 이상 결제 주기가 돌고 D-3 버튼을 눌러본 뒤, 그리고 실제로 다음
      달 1일(2026-08-01) 리포트가 발송될 때 확인 가능하다.
- 📝 다음: 2단계 백로그(6번 항목: Next.js+Supabase 대시보드, Gemini/Voyage AI 비용 추가, 기타
      제공사 추가) 중 고르거나, 이번에 추가한 하이라이트가 실사용에서 오탐/누락 없이 잘
      동작하는지 몇 달 지켜본 뒤 임계값(현재 3개월 고정)을 조정할지 검토.
