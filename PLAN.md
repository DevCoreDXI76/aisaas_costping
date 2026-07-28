# CostPing — AI 비용 텔레그램 리포터 프로젝트 계획

> AI API 비용 + 구독 결제일을 매일 아침 8시(KST) 텔레그램으로 받는 개인 도구.
> 서버·DB 없음: GitHub Actions cron → Python 스크립트 → Telegram Bot API.

작성일: 2026-07-27 · 상태: **세션 0 시작 전** (준비물 없음, 완전 초기)

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
│  └─ telegram.py          # 발송 + 에러 알림
├─ subscriptions.json
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
- KST 정밀 일 경계 (시간 단위 버킷 집계)
- 월 예산 초과 경고, 주간/월간 요약 리포트
- **Google Gemini API 비용 추가** (2026-07-27 조사): Anthropic/OpenAI처럼 간단한 Admin API 키로 비용 조회 불가. AI Studio 대시보드 전용, 프로그래밍 조회는 GCP Cloud Billing BigQuery Export 구축 필요(별도 GCP 프로젝트 연결 + 서비스 계정 + BigQuery 쿼리) — MVP 아키텍처(서버 없는 GitHub Actions 스크립트)와 무게감이 다름
- **Voyage AI 비용 추가** (2026-07-27 조사): MongoDB Atlas 산하로 통합됨. 공개 REST 비용/사용량 API 없음, Atlas UI(Usage/Cost Explorer/Invoices)로만 확인 가능. 프로그래밍 연동은 MongoDB에 별도 문의 필요
- 기타 제공사 추가
