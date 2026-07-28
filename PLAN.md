# CostPing — AI 비용 텔레그램 리포터 프로젝트 계획

> AI API 비용 + 구독 결제일을 매일 아침 8시(KST) 텔레그램으로 받는 개인 도구.
> 서버·DB 없음: GitHub Actions cron → Python 스크립트 → Telegram Bot API.

작성일: 2026-07-27 · 상태: **1단계 MVP 완료 + 2단계 세션 1(예산 경고/주간·월간 요약)·세션 2(KST 정밀 비용, 실험적) 완료**

---

## 1. 아키텍처 한 장 요약

```
GitHub Actions (cron, 매일 23:00 UTC = 08:00 KST)
  └─ report.py
       ├─ Anthropic Admin API  → 전일/이달 API 비용
       ├─ OpenAI Costs API     → 전일/이달 API 비용
       ├─ subscriptions.json   → 오늘·내일 결제 예정 구독
       └─ Telegram Bot API     → 리포트 발송 (실패 시에도 에러 알림 발송)
```

- 자동 수집: Anthropic + OpenAI (둘 다 **Admin 키** 필요)
- 고정 데이터: `subscriptions.json` (구독명, 월 금액, 결제일, 통화)
- 시크릿 4개는 전부 GitHub Secrets: `ANTHROPIC_ADMIN_KEY`, `OPENAI_ADMIN_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

## 2. repo 구조 (목표)

```
aisaas_costping/
├─ .github/workflows/daily-report.yml
├─ src/
│  ├─ report.py            # 진입점: 수집 → 포맷 → 발송
│  ├─ providers/
│  │  ├─ anthropic_cost.py
│  │  └─ openai_cost.py
│  ├─ subscriptions.py     # 결제일 계산 (오늘/내일, 말일 처리)
│  ├─ budget.py            # 월 예산 초과 경고 (2단계 세션 1)
│  ├─ weekly_monthly.py    # 주간/월간 요약 날짜 범위 계산 (2단계 세션 1)
│  └─ telegram.py          # 발송 + 에러 알림
├─ subscriptions.json
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
