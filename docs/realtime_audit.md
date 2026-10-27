# 실시간 갱신 점검 — 페이지·패널별 '왜 안 바뀌나'(2026-10-27)

> 사용자 보고: "몇몇 패널이 실시간으로 안 바뀐다". 맥 화면·DB 는 이 작업에서 볼 수 없어 **코드만으로**
> 쓰는 쪽(작업·서비스) → API 캐시(영역 버전·TTL) → reader(어느 날을 고르나) → 웹(언제 다시 읽나)을
> 패널마다 따라갔다. 맥 기록으로만 가릴 수 있는 것은 **맥 확인 필요**와 명령을 적었다.

## 0. 갱신 경로(전제)

| 단계 | 규칙 | 코드 |
|---|---|---|
| 쓰기 | 장중 수집은 슬롯 작업(`equity: every_min`) — 10분: `market.intraday`(개장~마감+10)·`flows.intraday`·`rules.intraday`·`program.stock_scan`(개장+5~마감-5), 1분: `flows.market_minute`·`program.intraday`(개장~마감+10)·`gex.alert`. 상시 서비스 `gex.poller`·`gex.ws_gateway`·`gex.engine` 은 작업 기록 없이 쓴다 | `config/jobs.yaml` |
| API 캐시 | 응답 본문은 키 (경로, 쿼리, **영역 데이터 버전**)로 프로세스 메모리에. 버전 = `DOMAIN_JOBS[영역]` 작업들의 마지막 완료 시각(Redis 15초) — GEX 는 `prv_gex.levels max(ts)` 도. 버전이 안 바뀌어도 TTL(장중 30초·그 밖 300초, 종목 재무 3600초)이 지나면 다시 계산 | `kbj/services/api/cache.py`·`deps.py` |
| 원장 캐시 | 수급·시장 요약이 쓰는 전 종목 원장은 (끝 날, `market`\|`flows` 버전)으로 캐시 — **TTL 없음**(버전이 바뀌어야 다시 만든다) | `readers/_common.py LedgerCache` |
| 웹 | 페이지는 처음 보일 때 **한 번만 마운트**된다(다른 페이지로 갔다 와도 다시 읽지 않는다). 다시 읽는 것은 페이지가 건 `ctx.every` 뿐 — 탭이 숨으면 멈추고 다시 보이면 바로 한 번 | `web/src/app/app.ts`·`refresh.ts` |
| 브라우저 캐시 | API 는 `Cache-Control: private, no-cache` + ETag, 웹은 `cache: 'no-cache'`(늘 재검증), 서비스 워커는 API 를 캐시하지 않는다 — 막힘 없음 | `deps.PRIVATE`·`web/src/data/api.ts`·`pwa/routes.ts` |
| 맥 점검 | `kbj.sh audit` 는 캐시 없는 앱(버전 원천 없음)으로 모든 GET 을 한 번씩 불러 상태·기준 시각 나이(`age_h`)·품질·빈 칸을 찍는다 — **DB 의 사실**. 화면이 audit 보다 늦으면 캐시·웹 문제, audit 도 늦으면 수집 문제 | `kbj/services/api/audit.py` |

## 1. 찾은 원인(영향 순)과 고친 것

| # | 원인 | 증상(장중) | 고친 것 | 시험 |
|---|---|---|---|---|
| R1 | **장중 잠정 스냅이 '전 종목 스냅'을 가렸다.** `flows.intraday` 가 10분마다 거래대금 순위 목록 종목(수십 개)의 오늘 잠정 스냅(`kis.prelim`, 시총 없음)을 `prv_market.stock_snapshot` 에 쓰는데, 저장소 `snapshot(오늘)` 은 원천을 보지 않고 가장 최근 날을 골랐다 | 09:00 첫 슬롯 ~ 15:35 마감 수집까지: ① 시장 요약 원장 마지막 날 = 오늘(목록 종목뿐) → 거래대금이 '장중(지수 기준)'(10분마다 바뀜)이 아니라 목록 종목 합 '마감', 시장폭이 수십 종목 ② 목록 밖 종목 헤더(페이지 8) '시세 없음' ③ 장중 수급 '시총 대비' 순위 빈 표(페이지 4) ④ 스크리너(페이지 4)가 목록 종목만 ⑤ `fin.quarterly` 상세 대상의 시총 상위가 장중엔 비었다(`detail_universe`) | 저장소 `snapshot()` 이 **잠정만 있는 날을 건너뛴다**(Pg·메모리 같게, 고른 날의 잠정 행은 우선순위 맨 뒤로 남는다). 장중 화면은 설계대로 마지막 확정 원장 + 지수 장중값 | `tests/unit/store/repo_cases.py case_snapshot_skips_prelim_only_day`, `tests/unit/api/test_realtime_prelim_snaps.py`(고치기 전 4건 모두 실패) |
| R2 | **슬롯과 어긋난 웹 새로 고침.** 페이지 1·4·10 의 장중 위젯이 마운트 시각부터 630초마다 다시 읽었다(벽시계와 무관) | 새 슬롯 값이 들어와도 화면은 최대 10분 30초 늦게(평균 5분) — "실시간이 아니다" | 벽시계 5분 경계(슬롯 시작 + 2분 30초·+ 7분 30초)에서만 다시 읽는다(`app/refresh.ts onSlot` — 30초마다 경계만 확인, 같은 구간이면 요청 없음, 숨은 사이 넘은 경계는 다시 보일 때 한 번). 페이지 2(하루 단위 보드)는 630초 그대로 | `web/test/refresh_slot.test.ts`, `p1_market`·`p10_etf_p5` 시험 |
| R3 | **페이지 11 발송 이력은 한 번만 읽었다.** 신호는 장중 10분(`rules.intraday`)·16:00(`rules.close`)에 늘어나는데 `ctx.every` 가 없었다 | 아침에 연 탭은 그날 장중 신호를 새로 고침 전까지 못 본다 | 발송 이력만 벽시계 10분 슬롯 + 2분 30초에 다시 읽는다(다른 패널은 하루 한 번·사용자 편집) | `p11_rules.test.ts '장중 새로 고침'` |
| R4 | **영역 버전에 빠진 쓰기 작업.** 빠진 작업이 쓴 값은 다른 작업이 돌거나 TTL 이 지날 때까지 옛 본문, 원장 캐시는 다른 작업이 돌 때까지 옛 원장 | `market`: 금융위 일봉·VKOSPI 타일(08:10)·원/달러·신용스프레드·신용잔고·한국 대 글로벌 칩 ≤ 5분 늦음. `gex`: `/api/gex/flow`(1분 투자자)가 마감 뒤 15:30~15:40 값 ≤ 5분. `flows_minute`: 지수 겹침. `flows`·`stock`·`screen`: `market.fsc_daily`(13:10 일봉) 뒤 원장 | `DOMAIN_JOBS` 에 더함 — market(+`market.fsc_daily`·`krx.vkospi`·`market_stats.kofia`·`us.eod`·`macro.*` 6), flows·stock·screen(+`market.fsc_daily`), flows_minute(+`market.intraday`), gex(+`flows.market_minute`) | `test_cache.py test_domain_jobs_cover_table_writers`(영역이 읽는 표 ↔ 그 표를 쓰는 켜진 작업 — 다음에 작업을 더해도 잡는다) |

## 2. 페이지·패널 표

TTL: 'live' = 장중 30초·그 밖 300초. 웹 '슬롯' = 이번에 바꾼 벽시계 슬롯 맞춤(위 R2·R3).

| 페이지 · 패널 | API | 쓰는 쪽(일정) | 캐시 영역 · TTL | 웹 새로 고침 | 판정 · 근거 |
|---|---|---|---|---|---|
| 상단 띠(모든 페이지) | `market/ribbon` | market.intraday·flows.intraday(10분), board.daily 16:00, kofia 13:30, 매크로, gex.engine(서비스) | market · 30초 | 60초 | 정상(R4 로 버전도 맞춤). GEX 칩은 서비스가 쓰지만 TTL 30초 |
| 1 시장 · 지수 타일 | `market/summary` | market.intraday 10분(개장~마감+10) | market · live | 슬롯 5분(고침 R2) | **고침**(R1 — 장중 거래대금·시장폭이 목록 종목만이던 것, R2) |
| 1 · 보조 타일(원/달러·VKOSPI·야간선물) | 〃 | macro.evening(_prv) 17:00, krx.vkospi 08:10, gex.poller(서비스) | market · live | 슬롯 5분 | 고침(R4). 야간선물은 서비스 — 야간은 TTL 300초 + 5분 새로 고침 |
| 1 · 업종 히트맵 | `market/sectors` | market.intraday 10분 | market · live | 슬롯 5분 | 고침(R2) |
| 1 · 시장 투자자 | `flows/investors` | market.close_collect 15:35 | flows · 300초 | 슬롯 5분 | 정상 — **설계상 전 거래일 확정**(장중 투자자는 아래 1분 패널) |
| 1 · 신용잔고·예탁금 | `market/credit` | market_stats.kofia 13:30 | macro · 300초 | 슬롯 5분 | 정상 |
| 1 · 장중 투자자 1분 | `flows/market-minute` | flows.market_minute 1분(개장~마감+10) | flows_minute · 30초 | 60초 | 정상(R4 로 지수 겹침 버전) |
| 1 · 프로그램 일별·상위 | `program/daily`·`program/top` | program.intraday 1분·stock_scan 10분·daily 15:50·sweep 18:10 | program · 300초/live | 60초 | 정상 |
| 2 신고가 보드 전부 | `board/*` | board.daily 16:00·board.confirm 08:40 | board · 300초 | 630초 | 정상 — 하루 단위(장중 보드 없음) |
| 2 · 미국 보드 | `board/us` | us.eod 22:10·05:10 KST | board_us · 300초 | 630초 | 정상 |
| 3 진단 | `diag/*` | krx.vkospi 08:10·diag.public 08:40·diag.daily 16:00 | diag · 300초 | **없음** | 맥 확인 필요 — 하루 세 번 바뀌는데 한 번만 읽는다. 아침에 연 탭은 16:00 판정을 새로 고침(⌘R) 전까지 못 본다(장중 값은 없음) |
| 3 · GEX | `gex/*` | gex.engine(서비스, 10초 따라잡기)·gex.alert 1분·flows.market_minute | gex(+`levels max(ts)`) · live | 30초 | 정상(R4 — flow) |
| 4 · 스크리너·종목 상세 | `flows/screen`·`flows/stock/{code}` | close_collect 15:35·krx.daily 08:05 | flows · 300초 | 슬롯 5분 | **고침**(R1 — 장중엔 목록 종목만이던 것). 설계상 장중엔 마지막 확정 원장 |
| 4 · 장중 잠정 | `flows/intraday` | flows.intraday 10분 | flows · 30초 | 슬롯 5분 | **고침**(R1 시총 대비 빈 표, R2) |
| 5 공시 피드·잠정실적 | `filings/feed`·`filings/earnings` | filings.dart_feed 매분 07~19·earnings.alerts 5분 | filings · live/60초 | 60초 | 정상 |
| 5 · 오버행·내부자·일정·캘린더 | `filings/overhang`·`insiders`·`schedule`, `macro/calendar` | filings.derive 30분, macro.morning 06:20 | filings · 300·600초 | 없음 | 정상에 가깝다 — 30분 단위 파생, 오래 열어 두면 새로 고침 |
| 6 매크로 | `macro/*` | macro.morning 06:20·monthly 09:10·evening 17:00 | macro · 300초 | 없음 | 정상(하루 단위) — 오래 연 탭은 새로 고침 |
| 7 수출입 | `trade/*` | customs_tenday 공표일 20분 폴링·monthly·derive | trade · 300초 | 없음 | 정상(공표일 하루 몇 번) |
| 8 종목 헤더 | `stock/{code}/header` | close_collect 15:35·krx.daily 08:05 | stock · 60초 | 없음(종목을 고를 때) | **고침**(R1 — 목록 밖 종목 '시세 없음'). 설계상 **전 거래일 종가** — 장중 종목 시세 수집은 없다(순위 목록 수십 종목만) |
| 8 · 공시·잠정실적 | `stock/{code}/disclosures`·`earnings` | dart_feed 매분 | stock · 60초 | 없음 | 맥 확인 필요(낮음) — 종목을 다시 고르면 새로 읽는다. 장중 공시는 페이지 5 피드(60초)에서 |
| 8 · 재무·컨센서스·밸류 | `stock/{code}/financials` 등 | fin.quarterly 30분·consensus 16:00·valuation 08:50 | stock · 3600초(버전) | 없음 | 정상 |
| 9 테마 강도 | `themes/strength` | board.daily 16:00·테마 동기화 일요일 | themes · 300초 | 없음 | 정상 — **설계상 보드 하루**(ADR 0039) |
| 10 ETF 순유입·유형·괴리율·변동 | `etf/*` | flows.intraday 10분(감시 ETF 시세)·krx.daily·etf.collect 08:00 | etf · 300초/live | 슬롯 5분 | 고침(R2) |
| 11 · 발송 이력 | `rules/signals` | rules.intraday 10분·rules.close 16:00 | rules · 300초 | 슬롯 10분(고침 R3) | **고침**(R3) |
| 11 · 규칙·성과·스크리너 | `rules`·`rules/stats`·`screen/cis` | 쓰기 모듈·rules.outcome 08:30·krx.daily | 없음/rules/screen | 없음 | 정상(하루 단위·사용자 편집) |
| 12 백테스트·저널·포트폴리오 | `backtest/*`·`journal/*`·`portfolio` | rules.backtest_nightly 08:45·쓰기 모듈·일봉 | backtest/없음 | 없음 | 정상(일봉 기준 — 장중 평가 없음) |
| 13 운영 | `ops/*` | 스케줄러·하트비트 | 버전 없음 · 15초 | 60초 | 정상 |

## 3. 맥 확인 필요

1. **수집 자체가 멈췄는지**(코드로 못 가림 — 가장 흔한 '안 바뀜'): 장중에
   `bash scripts/local/kbj.sh errors --since 1d` — `market.intraday`·`flows.intraday`·`flows.market_minute`·
   `program.intraday`·`rules.intraday` 실패·시간 초과가 있으면 그 패널은 마지막 성공 슬롯에 멈춘다.
   맥이 잠들면 슬롯이 통째로 빈다: `bash scripts/local/kbj.sh awake status`·`auto status`.
   GEX 는 서비스 하트비트: `bash scripts/local/kbj.sh status`.
2. **R1 이 맥에서도 있었는지**(고친 뒤 확인): 장중(예: 10:15)에
   - `bash scripts/local/kbj.sh audit --show /api/market/summary` → `data.date` 가 **전 거래일**, `data.turnover.basis`
     가 `intraday_index`, 태그 '장중(지수 기준)'. 고치기 전이면 `data.date` = 오늘, basis `close`.
   - `bash scripts/local/kbj.sh audit --show /api/stock/<거래대금 상위가 아닌 종목>/header` → `data.last` 가 null 이 아니다.
   - `bash scripts/local/kbj.sh audit --show /api/flows/intraday` → `top_foreign_by_cap` 가 비지 않는다.
3. **나이 비교**: 장중 `bash scripts/local/kbj.sh audit` 에서 `/api/market/summary`·`/api/market/sectors`·
   `/api/flows/intraday`·`/api/flows/market-minute`·`/api/rules/signals`
   의 `age_h` 가 0.3 이하여야 한다(10분 슬롯 + 수집 시간). 쿼리가 든 경로는 목록에 없어 `--show` 로 —
   `bash scripts/local/kbj.sh audit --show '/api/etf/premium?basis=inav'`·`--show '/api/gex/levels?scope=all'` 의 `as_of`.
   화면의 '기준' 시각이 audit 보다 늦으면 캐시·웹, audit 도 늦으면 수집(1번).
4. **업데이트 뒤 화면**: 웹 새 빌드는 `index.html` 을 다시 받아야 바뀐다 — `kbj.sh update` 뒤 브라우저 새로 고침 한 번.
   페이지 3·6·7·9·12 는 한 번만 읽으므로 아침에 연 탭은 16:00 이후 새로 고침이 필요하다(1번 표 '없음').
