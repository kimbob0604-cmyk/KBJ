# KBJ P6 설계서 — 수출입(페이지 7)·종목↔HS 매핑 겹쳐 보기(페이지 8)

- 작성: 2026-10-13. 상태: **설계(구현 전)**. 대상 단계: `docs/PLAN.md` §8 P6 — "수출입·종목 매핑 (6~8일) — `docs/reference/plan_kr_v0.2.md` 의 P5·P6". 완료 기준: **수출 속보일 당일 자동으로 반영된다. 관심 종목 50개의 매핑과 겹쳐 보기 차트가 나온다.**
- 기준 문서: 계획 `docs/PLAN.md` §4(페이지 7 "관세청 월간·10일·20일 속보, 품목·국가·지역 탐색, 급등, 단가 대 물량, 트리맵, CSV, 수출 104품목", 페이지 8 "종목↔HS 매핑 수출 겹쳐 보기")·§5·§8, `docs/reference/plan_kr_v0.2.md` §3(수출입 표)·§7 P5·P6, 등급 `docs/DATA_TIERS.md` §1(관세청 5종 = **공개** ✅, 이용허락 "제한 없음")·§2(페이지 7 = 공개 전부, 페이지 8 = 매핑 겹쳐 보기 공개), 명세 `docs/probe_results.md` §0 F2~F4·§2(관세청 8개 ID — 키 없이 문서로 조사)·§7 #1~#6·#17, P4·P5 설계 `docs/p4_design.md`·`docs/p5_design.md`(구조·관례), ADR 0001(U3·U4)·0011(공개 정적 사이트)·0018(16:00 발송).
- 코드 기준: 이 레포 HEAD `138da8f`(P5 커밋 `ceba880` + IDX 계획) + **작업 트리의 IDX 진행분**(`git status` — 커밋 전). kbj 쪽은 레포 경로 그대로, `ET:` = `legacy/etf_traker/`, `SD:` = `legacy/stock_dashboard/`.
- 표기: **[추정]** = 코드로 끝까지 확인하지 못함, **[확인 필요]** = 기본값으로 정한 것, **[실측 필요]** = 키를 받은 뒤 실제 응답으로 확인, **[결정 필요]** = 사용자가 정할 것(§10), **[제안]** = 이 설계의 권고, ⚠ = 충돌·위험.
- 키·토큰 값은 이 문서 어디에도 없다. `.env`·토큰 캐시는 열지 않았다. 네트워크 호출은 하지 않았다. 관세청 응답 칸·단위·반영 시각은 `docs/probe_results.md` §2(공식 문서·swagger)와 P2 어댑터 `kbj/data/public/customs/**` 에 적힌 것만 쓰고 나머지는 [실측 필요] — 그 수집 작업은 **`enabled: false`** 로 둔다.
- **레포에 넣는 수출입 자료는 합성만**(사용자 규칙 2026-10-13): 관세청은 공개 등급이라 ADR 0001 U3·DATA_TIERS "공개 등급 실데이터 fixture 허용"이 있지만, P6 은 **관세청·KIS·KRX 원자료와 그것으로 만든 픽스처를 레포에 넣지 않는다** — 고정 시드 생성기(`p6_world`)만. 관세청 조회코드표 xlsx(HS 17,523행)도 넣지 않는다(§4.6).
- **코드로 확인한 사실 중 계획과 다른 것(이 설계의 출발점)**:
  1. **`monitor/bok` 은 레포에 없다**(제3자 `rsm0kk/bok-monitor` — U3, `ET:MIGRATION.md`:28). "수출 104품목"은 bok `extract.py` 가 **출처 불명확 xlsx**(`docs/conflict_map.md`:269, `docs/inventory.md`:401)에서 뽑던 목록이라 옮길 원본이 없다 → **월간 HS 자료로 다시 정의한다**(D-P6-9, §10 Q2).
  2. **SD 에는 수출입·HS 코드가 없다**(`grep -i 'hs_code\|unipass\|관세청' legacy` → 0, ET 문서 2곳뿐). SD `valuechain.py`·`data/valuechain_map.json`(5 테마 × 52 세그먼트, `stocks_kr` 목록·키워드)은 HS 를 모른다. 테마·밸류체인 사전 통합은 사용자가 따로 진행 중(P5 Q1) → **P6 은 밸류체인 맵을 입력으로 쓰지 않는다**(매핑표 후보를 고를 때 사람이 참고만).
  3. **10일·20일 속보는 HS 단위가 아니다**(probe F2): 응답이 고정 11열(`itemUsdAmt00`=전체, `01~10` = 10대 품목 또는 10대 국가)뿐. 그래서 "속보" = 전체·10대 품목·10대 국가(수출·수입 4종). HS 단위·104품목·종목 매핑은 **월간(매월 15일경)** 만 된다.
  4. **P2 가 어댑터를 이미 만들었다**: `kbj/data/public/customs/{client,models,datasets}.py`(5종 + 10일 4종, 1년 한도 검사, `hsCode` 앞자리 0 복원 `normalize_hs`, 10일 열 대응 `TEN_DAY_COLUMNS`·`verify_ten_day_columns`, 시도 코드 개편 `SIDO_CODES`, 잘림 `CustomsTruncated`), 전송층 `kbj/data/datago.py`(`DatagoTransport` — 키 두 형태, 리미터 `limits.yaml datago` 25/s·데이터셋당 9,500/일), 카탈로그 등록(`kbj/data/catalog.py`:32·92), 합성 XML 5개(`tests/fixtures/synthetic/datago/` — 생성기 `make_fixtures.py`), 가짜 포털(`tests/fakes/datago_server.py` — 관세청은 **월간 3종 XML 을 파라미터와 무관하게 그대로** 돌려준다, 시군구·10일은 경로 없음).
  5. **등록부에 P6 작업 두 줄이 이미 있다**(`config/jobs.yaml`:337·352 — `trade.customs_tenday` 1·11·21일 10:00 재시도 6회, `trade.customs_monthly` 15~17일 10:00, owner `kbj.services.collectors.customs:{tenday,monthly}` — 처리기 없음, `enabled` 없음 = 꺼짐). 데이터 키 계산도 있다(`kbj/services/scheduler/conditions.py`:139 `ten_day` = `YYYYMM-1|2|3`, `month` = 지난달).
  6. **`pub_trade` 스키마는 있고 표는 없다**(`0001_schemas.sql`:17). `customs/datasets.py` 머리말의 "마이그레이션 0012, P6"은 낡은 번호 — 0012 는 `0012_fin_prv`. P6 표는 **0019**(D-P6-3).
  7. **상단 띠 '수출 속보' 칩 자리가 있다**: 엔진 `kbj/engines/market/ribbon.py`:68 `PENDING["export_flash"] = ("수출 속보", "public", "P6")`, 웹 `web/src/app/ribbon.ts`:33. 공개 칩 파일은 `credit.json`(`kbj/services/public_export/credit_json.py`).
  8. ⚠ **공개 JS 예산이 거의 찼다**: P5 결과 공개 JS gzip **61,021 B / 61,440 B**(`web/scripts/check-bundle.mjs`:32 `60 * 1024`) — 남은 419 B. 페이지 7 은 **공개 페이지 전체**라 로그인 분리로 줄일 수 없다(D-P6-17, §10 Q1).

---

## 0. 요약

### 0.1 결정 요약

| # | 결정 | 이유·근거 |
|---|---|---|
| D-P6-1 | **IDX 커밋 뒤 시작한다.** 시작 조건: ① `git log` 에 IDX 커밋 ② `ls kbj/store/migrations` 마지막이 `0018_index.sql` ③ `git status --short` 깨끗 ④ `uv run python -m kbj.services.api openapi --check` 통과. IDX 가 지금 고치는 공유 파일(§1.8)을 P6 FND 가 이어서 고치므로 겹쳐 시작하면 충돌한다 | 같은 작업 트리에서 IDX 가 진행 중(0018_index·페이지 8 배지·페이지 10·5·`jobs.yaml`·openapi·`types.gen.ts`) |
| D-P6-2 | **구현 = 기반 1(FND, 웨이브 1) + 병렬 묶음 4(COL·FLASH·EXP·MAP, 웨이브 2) + 마무리 1(WRAP, 웨이브 3)**. 공유 파일은 FND·WRAP 만 고친다. 웨이브 2 묶음은 서로 겹치지 않는 자기 파일만(§1.7) | P4·P5 와 같은 방식(D-P5-2) |
| D-P6-3 | 마이그레이션 **`0019_trade.sql` 하나**(pub_trade 표 11개 — §3.3). 0001~0018 은 고치지 않는다(체크섬). IDX 가 번호를 더 쓰면 그다음 번호로 [IDX 커밋 뒤 확인] | 사용자 규칙 "P6 마이그레이션은 0019 이상" |
| D-P6-4 | **등급: 페이지 7 전부 공개, 페이지 8 겹쳐 보기 = 공개(수출 지수 대 DART 분기 매출) + 로그인 한 줄(월말 종가)**. 매핑표는 자체 큐레이션이라 공개. 로그인 전용 줄은 별도 모듈로 공개 번들에서 뺀다(P4 `LOGIN_VALUE` 방식) | DATA_TIERS §1·§2(관세청·DART 공개, 시세 로그인) |
| D-P6-5 | **API 모델·라우트는 FND 가 고정**(빈 리더는 `NoData`), 리더·모델 본문은 도메인 묶음. `openapi.json`·`types.gen.ts` 는 생성 전용 — 모델을 바꾼 묶음이 생성 명령만 돌린다 | D-P5-4 와 같다 |
| D-P6-6 | **수출 속보 '당일 반영' = 공표일(1·11·21일)과 그 뒤 이틀(2·3·12·13·22·23일) 09:00~17:40 KST 20분마다 폴링**, 새 10일 구간이 들어오면 데이터 키(`YYYYMM-n`) 선점이 done 이 되어 나머지 폴링은 건너뛴다. 주말·공휴일 공표 규칙은 모른다 → 사흘 창으로 덮는다 [실측 필요 #55] | 반영 시각이 문서에 없다(probe §2.6 ①, #6). 1회 4건 × 최대 27회 = 108건/일 ≪ 9,500 |
| D-P6-7 | **로그인판은 수집 즉시, 공개판은 `public.export` 를 하루 4번으로**(05:30 → `30 5,11,14,17 * * *`). 강제 푸시 커밋 1개(ADR 0011)라 횟수가 늘어도 브랜치는 커지지 않는다 | 공개 정적 사이트의 '당일 반영'. 실행기에 이벤트 트리거가 없다(`jobs.yaml`:442 `triggered_by` 삭제 주석) |
| D-P6-8 | **텔레그램: 새 발송 종류를 만들지 않는다.** 속보는 **16:00 마감 요약(`brief.closing`) 한 줄**(그 거래일 16:00 직전 마감 요약 이후 처음 본 구간만)로 보낸다. 공표일이 휴장이면 다음 거래일 마감 요약 | ADR 0018(장 마감 뒤 발송은 16:00), 하루 1회 증명(D-P5-17) 유지. 즉시 알림은 §10 Q4 |
| D-P6-9 | **'수출 104품목' = HS4 기준 자체 묶음표**(`config/knowledge/trade_items.yaml` — 품목 이름·HS 접두 목록). 첫 판은 **최근 12개월 수출액 상위 HS4 104개를 엔진이 고른 결과를 사람이 고정**하는 방식이고, 파일이 비면 엔진이 상위 104 를 매달 자동 선택(`mode: top_n`) | bok 원본 없음·출처 불명(위 1), 10일 단위 104품목은 API 로 불가(F2) — §10 Q2 |
| D-P6-10 | **HS 저장 단위 = 응답 그대로(`hs_len` 열) + 엔진이 접두로 합산**. 월간 품목 수집은 `config/trade.yaml monthly.items_mode`(`omit` = `hsSgn` 생략 한 번, `by_chapter` = HS2 97회) — 기본 `by_chapter` [실측 필요 #3]. 품목×국가는 국가별(약 269회/월) `hsSgn` 생략 [실측 필요 #2] | 생략·자릿수 의미가 문서에 없다 |
| D-P6-11 | **금액 단위 = 달러 정수**(10일 잠정치는 응답 천 달러 × 1,000 을 Decimal 로 정확히), 중량 kg, 건수 건. 원화 환산은 하지 않는다(P6 범위 밖 — 겹쳐 보기는 '환율 미조정' 꼬리표) | 공개 원화 환율 시리즈가 ECOS 타기관 표(731Y001 — 로그인, F6)라 공개판에서 못 쓴다 [확인 필요: 한은 작성 환율 표] |
| D-P6-12 | **10일 잠정치는 처음 본 값과 최신 값을 둘 다 저장**(`first_usd`·`usd`·`first_seen_at`·`revised_at`). 화면 기본은 최신, 속보 칩·마감 요약은 '발표 당시' | 잠정 → 정정 덮어쓰기 여부 [실측 필요 #6 ②] |
| D-P6-13 | **10일 열 대응이 바뀌면 그 종류 수집 실패**(`TenDayColumnsChanged` — 이미 어댑터에 있음), 대응 기록은 `pub_trade.ten_day_columns` 에 남긴다. 실패 = health 경고, 옛 값 유지 | probe §2.6 [주의] |
| D-P6-14 | **종목↔HS 매핑표 = `config/knowledge/trade_map.yaml`(공개·자체 큐레이션, 첫 판 ≥ 60종목)** + VM 전용 덧붙임 파일(`KBJ_TRADE_MAP_LOCAL` 경로 — 레포 밖). 덧붙임 행은 `origin: local` 로 **로그인 전용**(공개 내보내기에서 뺀다) | 관심종목은 개인 데이터(`prv_journal.watchlist`, U-P4-5) — 공개 산출물에 '관심종목 흔적 0'(P4 수용 시험 ⑥). 공개 매핑 우주는 관심종목과 무관하게 고른다 |
| D-P6-15 | **매핑 행 = (종목, HS 접두 2~10자리, 흐름 exp\|imp, 가중치 0~1, 선택: 국가 목록·시도/시군구, 근거)**. 근거 칸 필수(사업보고서 접수번호·매출 구성 문장 요약 — plan_kr §8 "매핑 품질"). 사람이 검토하기 전에는 `reviewed: false` → 화면 꼬리표 '검토 전' | plan_kr §3 "직접 큐레이션 — 핵심 자산" |
| D-P6-16 | **겹쳐 보기 지표**(metrics §28): 종목 수출 지수 `E_t = Σ wᵢ·Xᵢ,t`(월, 달러) → 분기 합 YoY 대 DART 분기 매출 YoY(`pub_fin.quarterly`), 시차 상관(0~2분기, 겹침 ≥ 8분기), 3개월 YoY 랭킹. 지역 매핑 행은 시군구×HS6 값을 쓴다 | plan_kr P6 완료 기준 "품목 수출 YoY 대 분기 매출 YoY" |
| D-P6-17 | ⚠ **공개 JS 예산**: 페이지 7 모듈 목표 gzip ≤ 9 KB, 페이지 8 겹쳐 보기 ≤ 2 KB. 남은 예산 419 B 로는 불가 → **공개 예산 60 → 72 KB 로 올린다**(ADR 0025 — 기본값, §10 Q1). 새 차트 부품은 만들지 않는다(`ui/{line,bars,treemap,table,tabs,seg}.ts` 재사용, CSV 만 `ui/csv.ts` 신설) | `check-bundle.mjs` 는 모든 청크 합 — 탭별 청크로 나눠도 줄지 않는다(`p8_stock.ts`:89 주석) |
| D-P6-18 | **공개 정적 파일은 HS2 장(章)별로 쪼갠다**(`trade/hs/<2자리>.json` — 그 장의 HS4·HS6 24개월). HS10·품목×국가 전체는 로그인 API 에서만(공개 등급이지만 크기 때문) | 크기 상한(§7). 폴더 상한 20 MB·파일 1 MB [제안] |
| D-P6-19 | **CSV 는 브라우저에서 만든다**(지금 보이는 표 → UTF-8 BOM CSV, `= + - @` 로 시작하는 칸은 앞에 `'`). 서버 CSV 경로를 만들지 않는다 — 공개·로그인 같은 코드 | 정적 사이트에 서버가 없다 |
| D-P6-20 | **켜기(WRAP)**: 외부 호출이 없는 `trade.map_load`·`trade.derive`·`trade.stock_index` 는 켠다. `trade.customs_tenday`·`trade.customs_monthly` 는 **꺼진 채**(노트에 막는 실측 번호 #1~#6·#17·#55). 완료 기준은 합성 세계·가짜 포털·하루 운영 시뮬레이션으로 증명 | 사용자 규칙 "검증 안 된 수집은 enabled=false", P5 R7 과 같은 관례 |
| D-P6-21 | **쓰기 주인 하나**: 표마다 쓰는 작업 1개(§3.3 표의 '쓰는 작업'). 등록부 시험이 `writes` 겹침 0 을 본다 | 사용자 규칙 |
| D-P6-22 | **페이지·양식**: 새 페이지 없음. 페이지 7 = 자리표시 → 탭 6개, 페이지 8 = '재무' 탭에 패널 1개 추가(IDX 배지 코드와 별도 모듈). 패널·상태·품질 칩은 기존 `ui/panel.ts`·`ui/quality.ts` 그대로 | MD6 13페이지 유지 |
| D-P6-23 | **legacy 정리 없음**: 옮기는 원본이 없다(위 1·2). 기준선(`check_canonical`) 변화 0 | — |

### 0.2 만드는 것 / 만들지 않는 것 (P6 경계)

| 만든다 (P6) | 만들지 않는다 |
|---|---|
| 관세청 수집 처리기 2개(10일 잠정 4종·월간 4종) + 백필 CLI(5년) + 합성 세계 기반 가짜 포털 | 대만·일본·중국·미국 무역(plan_kr P7), 수입 시군구 탐색 화면(수집은 같이 됨 — 화면은 수출만) |
| 수출 속보: 10일·20일·월 누계 YoY(4종), 상단 띠 칩, 마감 요약 한 줄 | 조업일수·일평균 수출(조업일수 정의 [확인 필요] — §10 Q5), 진단 '수출 모멘텀'(P7) |
| 페이지 7 탭 6개: 속보·품목·국가·지역·급등·104품목(트리맵·단가 대 물량·CSV 포함) | 월간 수출 리뷰 초안 자동 생성(LLM — plan_kr P8), 경제 캘린더에 속보일 줄(페이지 5 는 IDX 가 고치는 중 — 손대지 않음) |
| 종목↔HS·지역 매핑표(공개 ≥ 60 + VM 덧붙임) + 검증기 + 종목 수출 지수 + 페이지 8 겹쳐 보기 패널 + 매핑 종목 랭킹(페이지 7 '품목' 탭 아래 표) | 대만 피어 월매출, 부문 매출과의 자동 매칭(부문 매출은 참고 줄만), 원화 환산 |
| 수용 시험(합성 관심종목 50)·하루 운영 시뮬레이션(속보일 3종)·웹 수용 시험 | 뉴스 RSS(D-P5-22 가 P6 로 제안 — 이번 범위 밖, §10 Q6) |

### 0.3 완료 기준과 증명

| PLAN §8 P6 완료 기준 | 증명 | 묶음 |
|---|---|---|
| **수출 속보일 당일 자동으로 반영된다** | `tests/sim/test_flash_day.py`(WRAP — 가짜 시계·가짜 포털, 작업 켬): ① 2026-11-11(수) 가짜 포털이 1~10일 구간을 **10:47** 에 내놓음 → 11:00 폴링이 받아 `pub_trade.ten_day`·`/api/trade/flash`·상단 띠 칩·공개 `trade/flash.json`(11:30 공개 내보내기)·16:00 마감 요약 한 줄 ② 2026-11-21(토) 공표 → 그날 받고, 발송은 11-23(월) 16:00 ③ 2026-12-01(화) 월 전체(`01~말일`) ④ 공표가 하루 늦은 변형(11-12 09:20) → 12일에 받음 ⑤ 열 구성 변경 주입 → 그 종류만 실패·옛 값 유지·health 경고 ⑥ 같은 구간 재폴링 0(선점 done) | COL·FLASH·WRAP |
| **관심 종목 50개의 매핑과 겹쳐 보기 차트가 나온다** | `tests/acceptance/test_trade_map_watchlist50.py`(WRAP): `p6_world`(합성 50종목 관심목록 — 수출주·내수주·지역 매핑·HS 접두 2~10자리·재무 미제출·신규상장·우선주 섞음)에 실제 처리기(`trade.map_load` → `trade.customs_monthly`(가짜 포털) → `trade.derive` → `trade.stock_index`)를 돌린 뒤 `/api/trade/stock/{code}` × 50: ① 500 = 0 ② 매핑 50/50 ③ 섹션 상태 = `p6_expect.yaml` 행렬(겹쳐 보기 ok·매출 없음·겹침 부족) ④ `NaN`·`Infinity` 0 ⑤ 공개 내보내기 `trade/stock/<code>.json` 은 `origin: public` 종목만·관심종목 흔적 0. 웹: `web/test/acceptance/p6_pages.test.ts` 가 페이지 8 패널을 50종목 픽스처로 그린다(`console.error` 0) | MAP·WRAP |
| (실데이터 판 — 키 수령 뒤) | VM: `python -m kbj.services.engine.verify_trade --watchlist main --json` — 매핑된 관심종목 수·종목코드 존재(DART 상장사)·HS 존재(수집된 `pub_trade.hs_dim`)·겹쳐 보기 상태 건수(값 없이) [실측 필요 #56] | MAP |
| (PLAN 기능) 페이지 7 이 합성 데이터로 그려진다 | `web/test/acceptance/p6_pages.test.ts`: 탭 6개 × 공개·로그인, DOM 의 `NaN`·`undefined` 0, CSV 버튼이 BOM·이스케이프된 본문을 만든다 | FLASH·EXP·WRAP |
| 백엔드 ruff·pyright·pytest·lint-imports·check_canonical·check_public_safety, 프런트 eslint·tsc·vitest·빌드·번들 검사, CI 녹색 | §1.9 | WRAP |

### 0.4 등록부(`config/jobs.yaml`)에서 바뀌는 것 (FND 가 웨이브 1 에 모양을 확정 — 전부 `enabled: false`, 켜기는 WRAP)

| 작업 | 지금(HEAD `138da8f`) | P6 |
|---|---|---|
| `trade.customs_tenday` | `cron "0 10 * * *"`, `month_days [1,11,21]`, 재시도 6 × 3600 s, writes `pub_trade.ten_day` | `cron "*/20 9-17 * * *"`(09:00~17:40), `month_days [1,2,3,11,12,13,21,22,23]`, 재시도 0(폴링이 재시도), `as_of: ten_day`(그대로), writes `pub_trade.ten_day`·**`pub_trade.ten_day_columns`**. 노트에 막는 실측 #6·#17·#55 |
| `trade.customs_monthly` | `cron "0 10 * * *"`, `month_days [15,16,17]`, writes 4표 | `cron "10 10,15 * * *"`, `month_days [14..20]`, `as_of: month`, writes 4표 + **`pub_trade.hs_dim`**. 굳은 의존 없음. 시군구는 매핑표의 HS6 만(설정 파일에서 읽음 — DB 의존 없음). 노트 #1~#5 |
| **`trade.map_load`(새)** | — | owner `kbj.services.engine.trade_map:load`, `cron "35 7 * * *"`, `when: always`, `as_of: run_date`, writes `pub_trade.stock_map`. 검증 실패면 실패(옛 판 유지) |
| **`trade.derive`(새)** | — | owner `kbj.services.engine.trade:derive`, `cron "40 10,15 * * *"`, `month_days [14..20]`, `as_of: month`, 굳은 의존 `{job: trade.customs_monthly}`, writes `pub_trade.surge`·`pub_trade.item_group_monthly` |
| **`trade.stock_index`(새)** | — | owner `kbj.services.engine.trade_map:stock_index`, `cron "0 21 * * *"`, `when: always`, `as_of: run_date`, 무른 의존 `trade.derive`·`trade.map_load`·`fin.quarterly`(`hard: false`), writes `pub_trade.stock_export` |
| `public.export` | `cron "30 5 * * *"` | `cron "30 5,11,14,17 * * *"`(D-P6-7). 처리기 그대로(새 파일은 `_p6_files` 고리) |
| `brief.closing` | 16:00 | **등록부 변경 없음** — 본문에 속보 한 줄(FLASH, D-P6-8) |

등록부 시험(FND 웨이브 1 몫, `tests/unit/scheduler/test_registry_validation.py` — IDX 가 고치는 중인 파일, §1.8): 위 행의 owner·collects·writes·as_of, `pub_trade.*` 쓰기 주인 각 1, 관세청 수집 하루 호출 상한(10일 108·월간 §3.4) ≤ `limits.yaml datago.daily_cap_per_dataset`, P6 새 작업 중 텔레그램 `notify` 0(ADR 0018). `ENABLED_BY_PHASE["P6"]` 는 WRAP.

---

## 1. 모듈 묶음·웨이브·파일 소유

### 1.0 웨이브

| 웨이브 | 묶음(동시) | 시작 조건 | 예상 |
|---|---|---|---|
| **0** | (대기) IDX 커밋 | D-P6-1 네 조건 | — |
| **1** | **FND** — 마이그레이션 0019·행 자료형·저장소·설정 해석기·등록부 행·API 모양·openapi·TS 타입·공개 내보내기 고리·웹 셸(페이지 7 자리 모듈·하위 모듈 빈 껍데기·`ui/csv.ts`)·합성 세계·지표 문서·실측 번호 | 웨이브 0 | 1.5일 |
| **2** | **COL**(관세청 수집·백필·가짜 포털), **FLASH**(속보 엔진·띠 칩·속보 탭·마감 요약 줄·공개 flash), **EXP**(탐색기·급등·단가 대 물량·트리맵·104품목·CSV), **MAP**(매핑표·검증기·종목 수출 지수·겹쳐 보기·랭킹·검증 CLI) | FND 완료(§1.9 FND 명령 녹색) | 3일 |
| **3** | **WRAP** — 페이지 8 고리·작업 켜기·완료 시험·하루 운영 시뮬레이션·계약·CI·예산·ADR·문서 | 웨이브 2 전부 | 1.5일 |

합계 약 6일(PLAN 6~8일). 묶음 6개(FND 1 + 병렬 4 + WRAP 1). 묶음마다 빌드 에이전트 + 검증 에이전트(§1.9). 도커 통합·legacy 전체·sim 전체는 묶음이 돌리지 않는다 — 메인이 WRAP 끝에 돌린다.

### 1.1 FND — 공통 기반 (웨이브 1)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/core/trade_rows.py`(새) | frozen dataclass: `TenDayRow(kind, ym, seg 1\|2\|3, col_no, name, usd, first_usd, first_seen_at, revised_at, source, as_of, quality)`, `ItemMonthRow(ym, hs, hs_len, exp_usd, exp_kg, imp_usd, imp_kg, …)`, `CountryMonthRow`, `ItemCountryMonthRow`, `SigunguMonthRow(ym, sido_cd, sgg_nm, region_id, hs6, exp_usd, exp_cnt, imp_usd, imp_cnt)`, `HsDim`, `SurgeRow`, `ItemGroupMonthRow`, `StockMapRow`, `StockExportRow`. 금액 Decimal(달러 정수) | `tests/unit/core/test_trade_rows.py` |
| `kbj/store/migrations/0019_trade.sql` | §3.3 | `tests/test_store_layout.py`·`tests/integration/test_migrations_pg.py` 확장 |
| `kbj/store/repos/trade.py`(새 — `TradeRepo` 프로토콜·Pg·메모리 구현), `kbj/store/repos/__init__.py`·`memory.py`(등록 한 줄씩) | 쓰기 함수는 표마다 하나(주인 작업만 부른다), 읽기: 구간·접두·국가·지역·종목 | `tests/unit/store/test_trade_repo.py`(새 — IDX 가 고치는 `test_repos_memory.py` 는 건드리지 않는다) |
| `kbj/config/trade.py`(새) + `config/trade.yaml`(새) | 숫자·목록 한 곳(모르는 키 오류 — `kbj/config/macro.py` 방식): `tenday.poll`, `monthly.items_mode`, `backfill.years 5`, `surge`(문턱 — metrics §26), `public_export`(개월 수·상한), `overlay`(최소 겹침 8분기·시차 0~2) | `tests/unit/config/test_trade_config.py` |
| `config/knowledge/{trade_map,trade_items,trade_regions,hs_chapters}.yaml`(새 — **틀과 합성 아닌 자체 항목 0~2개 예시만**, 본문은 EXP·MAP) | 스키마(§4.5·§4.6) | 위와 같음 |
| `config/jobs.yaml`(§0.4 — 전부 false) | — | 등록부 시험 |
| `kbj/services/api/models/trade.py`·`routes/trade.py`(새 — §5 경로 고정)·`readers/{trade_flash,trade_explore,trade_map}.py`(새 — `NoData` 빈 함수), `app.py`(라우터 한 줄)·`cache.py`(도메인 `trade` → 작업 5개)·`readers/_common.py`(Repos 프로토콜 `trade` 한 줄) | 응답 봉투(source·as_of·quality) | `tests/unit/api/test_routes_trade_shape.py` |
| `web/src/api/openapi.json`·`types.gen.ts`(생성) | `uv run python -m kbj.services.api openapi --write && (cd web && npm run gen:types)` | `--check` |
| `kbj/services/public_export/{export,manifest}.py`(고리 `_p6_files`·`SUBDIRS` 에 `trade`·`SUBDIR_JSON_RE`·`DIR_CAPS`·`FILE_CAPS`) + 빈 모듈 `trade_flash_json.py`·`trade_explore_json.py`·`trade_stock_json.py` | §7 | `tests/unit/public_export/test_manifest.py` 확장 |
| `web/scripts/merge-public-data.{mjs,d.mts}`(같은 이름 규칙·상한), `web/src/data/static.ts`(`PUBLIC_SUBDIR` 에 `trade/…`) | — | `web/test/scripts.test.ts`·`source.test.ts` 확장 |
| `web/src/app/pages.ts`(glob 에 7 — `p{1,2,4,5,6,7,8,10}_*.ts`, 7 의 plan 문구), `web/src/pages/p7_trade.ts`(새 — 탭 6개 틀, 하위 모듈 import), `web/src/pages/p7/{common,flash,items,countries,regions,surge,items104}.ts`(빈 껍데기 — '준비 중' 패널), `web/src/pages/p8/trade_overlay.ts`·`trade_overlay_price.ts`(빈 껍데기 — **고리는 WRAP**) | — | `web/test/pager.test.ts`·`tier.test.ts` 확장 |
| `web/src/ui/csv.ts`(새 — `toCsv(rows, cols)`·`downloadCsv(name, text)`, BOM·이스케이프) | D-P6-19 | `web/test/ui_p6.test.ts`(새) |
| `tests/fixtures/synthetic/p6_world.py`(새) + `p6_expect.yaml` | 고정 시드 `build_world(seed=20261211)`: HS 계층(2·4·6·10, 가상 이름) × 60개월 × 수출·수입 금액·중량(급등·급락·중량 0·결측 심음), 국가 40, 시도 18·시군구(개편 전후 이름), 10일 잠정 4종 10년(정정 1건 심음), 합성 상장사 80 + **합성 관심목록 50**·합성 매핑표(접두 2~10자리·국가·지역·`origin: local` 3건) + 분기 매출(P4 `p4_world` 생성기 재사용 — 수정 없이 import) | `tests/unit/fixtures/test_p6_world.py` |
| `tests/unit/api/make_web_fixtures.py`(`render_p6()`·`--only` 에 `flash`·`explore`·`map`) + 빈 `tests/unit/api/web_fixtures_p6/{flash,explore,map}.py` | 도메인 묶음은 자기 모듈·`web/test/fixtures/api/trade_<도메인>/**` 만 | `tests/unit/api/test_web_fixtures.py` |
| `tests/unit/scheduler/test_registry_validation.py`(§0.4 단언) | — | 등록부 검증 |
| `docs/metrics.md` §25~§29(부록 A), `docs/probe_results.md` §7 #55~#57(부록 B), `kbj/data/public/customs/datasets.py` 머리말 번호 정정(0012 → 0019) | — | — |

FND 가 하지 않는 것: 처리기·엔진·리더 본문·페이지 본문(웨이브 2), 가짜 포털 확장(COL), sim 시나리오(WRAP), `p8_stock.ts`(WRAP).

### 1.2 COL — 관세청 수집 (웨이브 2)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/services/collectors/customs.py`(새) | `tenday(ctx)`: 4종을 `strtYymm = endYymm − 2` 개월로 받아 새 구간·정정만 `put_ten_day`(처음 본 값 보존 — D-P6-12), 열 대응 기록, 그 데이터 키 구간이 아직 없으면 `not_ready`(재폴링). `monthly(ctx)`: 지난달 품목(`items_mode`)·국가·품목×국가(국가 목록 = 코드표 국가 중 `config/trade.yaml countries` [제안: 전부])·시군구(매핑 HS6 × 18시도) → 4표 + `hs_dim`. 아직 그 달이 없으면 `not_ready`. `backfill(from_ym, to_ym)`(모듈 CLI `python -m kbj.services.collectors.customs backfill` — 등록부 작업 아님): 12개월 창, 데이터셋당 하루 상한 90% 에서 멈추고 다음 날 이어서(선점 키 = 월) | `tests/unit/collectors/test_customs.py` |
| `kbj/data/public/customs/{client,models}.py`(필요한 것만 — 예: 시군구 `HsSgn` 10자리 거절, 응답 잘림 감지 보강) | 어댑터 계약은 그대로 | `tests/unit/data/public/test_customs.py`(기존 + 추가) |
| `tests/fakes/datago_server.py`(관세청 가지만) + `tests/fakes/customs_world.py`(새) | 경로별 응답을 `p6_world` 에서 **파라미터대로** 만든다(기간·`hsSgn` 접두·`cntyCd`·`sidoCd`), 10일 구간 **공표 시각**(`publish_at` 조절 — 늦은 공표·주말 공표), 열 구성 변경 주입, 잘림 주입. 기존 금투협·금융위 가지는 손대지 않는다 | `tests/unit/fakes/test_customs_world.py` |
| `tests/fixtures/synthetic/datago/make_fixtures.py`·XML(필요하면 10일 3종 추가 — 생성기만) | 합성 | `test_synthetic_fixtures_match_the_generator` |

### 1.3 FLASH — 수출 속보 (웨이브 2, 페이지 7 '속보' 탭·상단 띠·마감 요약)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/engines/trade/flash.py`(새 — 순수) | 구간 누계·전년 같은 구간 YoY·전월 같은 구간 대비·10대 품목·국가 기여도(metrics §25), '발표 당시' 대 '최신', 칩 값(가장 최근 수출 전체 YoY) | `tests/unit/engines/trade/test_flash.py`, `tests/property/test_trade_flash_props.py`(구간 단조·합 = 전체 오차 0) |
| `kbj/engines/market/ribbon.py`(`export_flash` 칩만 — `PENDING` 에서 빼고 값 칩), `kbj/services/api/readers/market.py`(띠 칩 입력만), `kbj/services/public_export/credit_json.py`(공개 칩 목록에 `export_flash` — 파일 이름 `credit.json` 그대로 [제안]) | 칩: `1~20일 +12.3%` 꼴, 기준 구간·`estimated` | `tests/unit/engines/market/test_ribbon.py`·`tests/unit/public_export/test_credit_json.py`(있으면 확장, 없으면 새) |
| `kbj/services/api/readers/trade_flash.py`, `kbj/services/public_export/trade_flash_json.py`(`trade/flash.json`) | — | `tests/unit/api/test_routes_trade_flash.py`, `tests/unit/public_export/test_trade_flash_json.py` |
| `kbj/reports/briefs/closing_brief.py`(속보 한 줄 — 직전 마감 요약 이후 처음 본 구간만, 없으면 줄 없음) | D-P6-8 | `tests/unit/reports/briefs/test_closing.py` 확장 |
| `web/src/pages/p7/flash.ts`, `web/src/app/ribbon.ts`(`export_flash` 의 `phase` 제거·값 표시만) | 4종 × 구간 막대(`ui/bars.ts`)·YoY 선(`ui/line.ts`)·표·CSV | `web/test/pages/p7_flash.test.ts`, `web/test/pages/ribbon.test.ts` 확장 |
| `tests/unit/api/web_fixtures_p6/flash.py`, `web/test/fixtures/api/trade_flash/**` | — | — |

### 1.4 EXP — 탐색기 (웨이브 2, 페이지 7 '품목'·'국가'·'지역'·'급등'·'104품목')

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/engines/trade/{explore,surge,price_volume,items104}.py`(새 — 순수) | 접두 합산·YoY·3개월 평균·비중(§26), 급등(§26), 단가 대 물량 분해(§27), 104품목 묶음·자동 선택(D-P6-9), 트리맵 입력(크기·색) | `tests/unit/engines/trade/test_{explore,surge,price_volume,items104}.py`, `tests/property/test_trade_props.py`(로그 분해 항등식·접두 합 = 상위 합·0 중량 처리) |
| `kbj/services/engine/trade.py`(새 — `derive`) | `pub_trade.surge`·`item_group_monthly` 쓰기 | `tests/unit/engines/trade/test_derive_job.py` |
| `config/knowledge/trade_items.yaml`(첫 판 104 — 합성 세계가 아니라 **자체 이름 + HS4 접두**만, 값 없음), `config/knowledge/trade_regions.yaml`(시도·시군구 이름 → 통합 지역 ID, 2026-07 개편 대응 — probe F4), `config/knowledge/hs_chapters.yaml`(HS2 97장 자체 한글 이름) | 값(무역액)이 아닌 분류표 | `tests/unit/config/test_trade_items.py` |
| `kbj/services/api/readers/trade_explore.py`, `kbj/services/public_export/trade_explore_json.py` | §5·§7 | `tests/unit/api/test_routes_trade_explore.py`, `tests/unit/public_export/test_trade_explore_json.py` |
| `web/src/pages/p7/{common,items,countries,regions,surge,items104}.ts` | 품목 트리(HS2 → 4 → 6, 로그인은 10)·트리맵(`ui/treemap.ts`)·단가 대 물량 겹친 선·국가 표·품목×국가·지역 표·급등 표·CSV | `web/test/pages/p7_explore.test.ts` |
| `tests/unit/api/web_fixtures_p6/explore.py`, `web/test/fixtures/api/trade_explore/**` | — | — |

### 1.5 MAP — 종목↔HS 매핑·겹쳐 보기 (웨이브 2, 페이지 8 패널·페이지 7 랭킹 표)

| 경로 | 내용 | 시험 |
|---|---|---|
| `config/knowledge/trade_map.yaml`(첫 판 ≥ 60종목 — 반도체·자동차·조선·전력기기·화장품·라면·방산·이차전지·석유화학·철강 등 **수출 비중이 사업보고서에 적힌 종목**, 근거 칸 필수, `reviewed: false`) | 자체 큐레이션(§4.5). 관심종목 목록을 보고 고르지 않는다(D-P6-14) | `tests/unit/config/test_trade_map.py`(형식·접두 짝수 자리·HS2 장 존재·가중치 합 ≤ 1·중복 0·근거 칸·시도 코드·지역 ID) |
| `kbj/engines/trade/{mapping,overlay}.py`(새 — 순수) | 매핑 해석(공개 + local 병합, 충돌 = local 우선·경고), 종목 수출 지수·분기 합·YoY·시차 상관·랭킹(§28) | `tests/unit/engines/trade/test_{mapping,overlay}.py`, `tests/property/test_trade_overlay_props.py`(가중치 선형성·분기 합 = 월 합) |
| `kbj/services/engine/trade_map.py`(새 — `load`·`stock_index`), `kbj/services/engine/verify_trade.py`(새 — VM CLI, 건수만) | — | `tests/unit/engines/trade/test_map_jobs.py`, `tests/unit/engines/trade/test_verify_trade.py` |
| `kbj/services/api/readers/trade_map.py`, `kbj/services/public_export/trade_stock_json.py`(`trade/stocks.json`·`trade/stock/<code>.json` — `origin: public` 만) | — | `tests/unit/api/test_routes_trade_map.py`, `tests/unit/public_export/test_trade_stock_json.py`(관심종목 흔적 0) |
| `web/src/pages/p8/trade_overlay.ts`(공개 — 수출 지수 YoY 대 분기 매출 YoY 두 선·매핑 품목 표·근거·'검토 전'·'환율 미조정' 꼬리표), `web/src/pages/p8/trade_overlay_price.ts`(로그인 — 기존 `/api/stock/{code}/chart` 의 월말 종가 한 줄을 겹친다), `web/src/pages/p7/stocks.ts`(매핑 종목 랭킹 표 — `p7_trade.ts` 가 '품목' 탭 아래에 붙임) | **`p8_stock.ts` 는 고치지 않는다**(고리는 WRAP) | `web/test/pages/p8_trade_overlay.test.ts`, `web/test/pages/p7_stocks.test.ts` |
| `tests/unit/api/web_fixtures_p6/map.py`, `web/test/fixtures/api/trade_map/**` | — | — |

### 1.6 WRAP — 마무리 (웨이브 3)

1. **페이지 8 고리**: `web/src/pages/p8_stock.ts` 에 import 2줄 + `tabModules('fin')` 를 `[mountFinancials, mountTradeOverlay]` 로(로그인 줄은 `LOGIN_TRADE` 상수 — `MODE === 'public'` 이면 null). IDX 배지 코드(헤더 쪽)는 건드리지 않는다.
2. 등록부 켜기(D-P6-20): `ENABLED_BY_PHASE["P6"] = {trade.map_load, trade.derive, trade.stock_index}`, 관세청 두 작업은 꺼진 채 노트.
3. `tests/sim/test_flash_day.py`(§0.3 ①~⑥)·`tests/sim/harness.py`(가짜 포털을 `customs_world` 로·`OFFLINE_SOURCES` 에서 DATAGO 관세청 제외)·`tests/sim/p6.py`.
4. `tests/acceptance/test_trade_map_watchlist50.py`·`tests/acceptance/p6_pipeline.py`, `web/test/acceptance/p6_pages.test.ts`.
5. import-linter 계약 **⑰ [IDX 가 계약을 더하면 그다음 번호]**: "수출입 엔진(`kbj.engines.trade`)·작업(`collectors.customs`·`engine.trade`·`engine.trade_map`)·공개 내보내기 trade 모듈은 로그인 원천(`kbj.data.private`)·로그인 저장소를 import 하지 않는다". `pyproject.toml`·`tests/test_import_contracts.py`.
6. 공개 JS 예산(§10 Q1 답에 따라 `web/scripts/check-bundle.mjs` `BUDGET_GZIP.public.js`) + ADR 0025.
7. `.github/workflows/ci.yml`(새 시험 경로가 기존 잡 패턴에 안 걸릴 때만), `docs/PLAN.md` P6 결과 줄, `docs/DATA_TIERS.md`(매핑표 공개·local 로그인 한 줄), ADR 확정(§9.2), `docs/probe_results.md` #55~#57 상태.

### 1.7 묶음별 파일 소유 (겹치지 않는다)

| 묶음 | 손대는 파일 |
|---|---|
| FND | `kbj/core/trade_rows.py`, `kbj/store/migrations/0019_trade.sql`, `kbj/store/repos/trade.py`·`__init__.py`·`memory.py`, `kbj/config/trade.py`, `config/trade.yaml`, `config/knowledge/{trade_map,trade_items,trade_regions,hs_chapters}.yaml`(웨이브 1 틀만), `config/jobs.yaml`(웨이브 1), `kbj/services/api/{app,cache}.py`·`models/trade.py`·`routes/trade.py`·`readers/_common.py`·`readers/trade_{flash,explore,map}.py`(웨이브 1 빈 함수), `web/src/api/{openapi.json,types.gen.ts}`(웨이브 1), `kbj/services/public_export/{export,manifest}.py`·`trade_{flash,explore,stock}_json.py`(웨이브 1 빈 함수), `web/scripts/merge-public-data.{mjs,d.mts}`, `web/src/data/static.ts`, `web/src/app/pages.ts`, `web/src/pages/p7_trade.ts`·`p7/*.ts`·`p8/trade_overlay*.ts`(웨이브 1 껍데기), `web/src/ui/csv.ts`, `tests/fixtures/synthetic/p6_world.py`·`p6_expect.yaml`, `tests/unit/api/make_web_fixtures.py`·`web_fixtures_p6/*.py`(빈 모듈), `tests/unit/scheduler/test_registry_validation.py`(웨이브 1), `tests/test_store_layout.py`, `tests/integration/test_migrations_pg.py`, `tests/unit/{core,store,config,fixtures,api}/` 의 새 FND 시험, `web/test/{pager,tier,scripts,source,ui_p6}.test.ts`, `docs/metrics.md`·`docs/probe_results.md`(웨이브 1), `kbj/data/public/customs/datasets.py`(머리말 한 줄) |
| COL | `kbj/services/collectors/customs.py`, `kbj/data/public/customs/{client,models}.py`, `tests/fakes/datago_server.py`(관세청 가지)·`tests/fakes/customs_world.py`, `tests/fixtures/synthetic/datago/**`, `tests/unit/collectors/test_customs.py`, `tests/unit/data/public/test_customs.py`, `tests/unit/fakes/test_customs_world.py` |
| FLASH | `kbj/engines/trade/flash.py`, `kbj/engines/market/ribbon.py`(칩 하나), `kbj/services/api/readers/market.py`(칩 입력)·`readers/trade_flash.py`(웨이브 2), `kbj/services/public_export/credit_json.py`·`trade_flash_json.py`(웨이브 2), `kbj/reports/briefs/closing_brief.py`, `web/src/pages/p7/flash.ts`, `web/src/app/ribbon.ts`, `tests/unit/engines/trade/test_flash.py`, `tests/property/test_trade_flash_props.py`, `tests/unit/engines/market/test_ribbon.py`, `tests/unit/public_export/test_{credit,trade_flash}_json.py`, `tests/unit/api/test_routes_trade_flash.py`, `tests/unit/reports/briefs/test_closing.py`, `tests/unit/api/web_fixtures_p6/flash.py`, `web/test/fixtures/api/trade_flash/**`, `web/test/pages/{p7_flash,ribbon}.test.ts`, ADR 0026 초안 |
| EXP | `kbj/engines/trade/{explore,surge,price_volume,items104}.py`, `kbj/services/engine/trade.py`, `config/knowledge/{trade_items,trade_regions,hs_chapters}.yaml`(웨이브 2 본문), `kbj/services/api/readers/trade_explore.py`(웨이브 2), `kbj/services/public_export/trade_explore_json.py`(웨이브 2), `web/src/pages/p7/{common,items,countries,regions,surge,items104}.ts`, `tests/unit/engines/trade/test_{explore,surge,price_volume,items104,derive_job}.py`, `tests/property/test_trade_props.py`, `tests/unit/config/test_trade_items.py`, `tests/unit/api/test_routes_trade_explore.py`, `tests/unit/public_export/test_trade_explore_json.py`, `tests/unit/api/web_fixtures_p6/explore.py`, `web/test/fixtures/api/trade_explore/**`, `web/test/pages/p7_explore.test.ts` |
| MAP | `config/knowledge/trade_map.yaml`(웨이브 2 본문), `kbj/engines/trade/{mapping,overlay}.py`, `kbj/services/engine/{trade_map,verify_trade}.py`, `kbj/services/api/readers/trade_map.py`(웨이브 2), `kbj/services/public_export/trade_stock_json.py`(웨이브 2), `web/src/pages/p8/trade_overlay{,_price}.ts`(웨이브 2), `web/src/pages/p7/stocks.ts`, `tests/unit/config/test_trade_map.py`, `tests/unit/engines/trade/test_{mapping,overlay,map_jobs,verify_trade}.py`, `tests/property/test_trade_overlay_props.py`, `tests/unit/api/test_routes_trade_map.py`, `tests/unit/public_export/test_trade_stock_json.py`, `tests/unit/api/web_fixtures_p6/map.py`, `web/test/fixtures/api/trade_map/**`, `web/test/pages/{p8_trade_overlay,p7_stocks}.test.ts`, ADR 0027 초안 |
| WRAP | `web/src/pages/p8_stock.ts`(고리), `config/jobs.yaml`(웨이브 3 — enabled), `tests/unit/scheduler/test_registry_validation.py`(웨이브 3 — `ENABLED_BY_PHASE`), `tests/sim/{test_flash_day,p6}.py`·`harness.py`, `tests/acceptance/{test_trade_map_watchlist50,p6_pipeline}.py`, `web/test/acceptance/p6_pages.test.ts`, `pyproject.toml`·`tests/test_import_contracts.py`(계약 ⑰), `web/scripts/check-bundle.mjs`(예산), `.github/workflows/ci.yml`(필요할 때만), `docs/{PLAN,DATA_TIERS,probe_results,metrics}.md`(웨이브 3), `docs/adr/0025~0027` |

`p7_trade.ts` 는 FND 가 탭 틀을 쓰고 웨이브 2 에서는 아무도 고치지 않는다 — 하위 모듈의 `mount` 이름·서명을 FND 가 고정한다(`(t: TabCtx, el: HTMLElement) => Promise<void>`). `models/trade.py` 를 웨이브 2 에 바꿔야 하면 FND 담당에게 요청(D-P6-5).

### 1.8 공유 파일 — IDX 와의 경계·누가 언제

**IDX 가 지금 고치는 파일(작업 트리 `git status`, 2026-10-13)**: `config/jobs.yaml`, `docs/{DATA_TIERS,metrics,probe_results}.md`, `kbj/services/api/{app,cache}.py`, `kbj/services/api/readers/_common.py`, `kbj/store/repos/{__init__,memory}.py`, `tests/integration/test_migrations_pg.py`, `tests/unit/api/test_cache.py`, `tests/unit/scheduler/test_registry_validation.py`, `tests/unit/store/{test_repos_memory,test_repos_p4_memory,test_repos_p5}.py`, `web/src/api/{openapi.json,types.gen.ts}` + 새 파일(`0018_index.sql`, `kbj/**/index_review*`, `config/index_reviews.yaml` 등). 계획상 앞으로: 페이지 8 배지·페이지 10·페이지 5(`docs/p5_index_rebalance_prompt.md`:18·74).

**P6 가 손대는 공유 파일 전부**(IDX 커밋 뒤에만):

| 파일 | IDX 도 고침 | P6 웨이브 1 (FND) | 웨이브 2 | 웨이브 3 (WRAP) |
|---|---|---|---|---|
| `config/jobs.yaml` | ✓ | trade.* 행·`public.export` cron | 안 고침(요청) | enabled |
| `kbj/store/repos/__init__.py`·`memory.py` | ✓ | `trade` 등록 | 안 고침 | — |
| `kbj/services/api/app.py`·`cache.py`·`readers/_common.py` | ✓ | 라우터·도메인·프로토콜 한 줄씩 | 안 고침 | — |
| `web/src/api/openapi.json`·`types.gen.ts` | ✓ | 생성 | 생성 명령만 | `--check` |
| `tests/integration/test_migrations_pg.py`·`tests/test_store_layout.py` | ✓(앞 파일) | 0019 | — | — |
| `tests/unit/scheduler/test_registry_validation.py` | ✓ | P6 행 단언 | — | `ENABLED_BY_PHASE` |
| `docs/metrics.md`·`docs/probe_results.md`·`docs/DATA_TIERS.md` | ✓ | §25~§29·#55~#57 | — | 상태·한 줄 |
| `web/src/pages/p8_stock.ts` | ✓(배지 예정) | — | — | **고리 2줄**(배지와 다른 줄) |
| `web/src/app/pages.ts` | ✗ [추정] | glob·7 문구 | — | — |
| `web/src/data/static.ts`·`web/scripts/merge-public-data.{mjs,d.mts}` | ✗ | trade 규칙 | — | — |
| `kbj/services/public_export/{export,manifest}.py` | ✗ | 고리·상한 | — | — |
| `kbj/engines/market/ribbon.py`·`readers/market.py`·`public_export/credit_json.py`·`web/src/app/ribbon.ts` | ✗ | — | **FLASH** | — |
| `kbj/reports/briefs/closing_brief.py` | ✗ | — | **FLASH** | — |
| `tests/fakes/datago_server.py`·`tests/sim/harness.py` | ✗ | — | COL(관세청 가지) | WRAP(harness) |
| `tests/unit/api/make_web_fixtures.py` | ✗ | `render_p6` | 안 고침 | — |
| `pyproject.toml`·`tests/test_import_contracts.py` | ? [IDX 커밋 뒤 확인] | — | — | 계약 ⑰ |
| `web/scripts/check-bundle.mjs` | ✗ | — | — | 예산(§10 Q1) |
| `kbj/data/public/customs/datasets.py` | ✗ | 머리말 | — | — |
| `docs/PLAN.md` | ✗ | — | — | 결과 줄 |

IDX 의 페이지 5·10 파일(`web/src/pages/p5*`·`p10*`), `0018_index.sql`, `kbj/**/index_review*` 는 P6 이 **열지도 고치지도 않는다**.

### 1.9 묶음별 완료 확인 명령 (모두 종료 코드 0)

> 같은 작업 트리에서 여러 묶음이 동시에 일한다 — 묶음은 **자기 경로 시험 + 빠른 공통 명령**만 돌리고 실패는 자기 파일 것만 고친다. 도커 통합(`-m integration`)·legacy 전체·sim 전체는 메인이 WRAP 끝에 돌린다. 포맷터는 자기 파일에만(`uv run ruff format <자기 경로>`).

**공통(모든 백엔드 묶음)**: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run lint-imports && uv run python scripts/check_canonical.py && uv run python scripts/check_public_safety.py --exclude legacy && uv run pytest -m "not sim and not integration and not legacy" -q` — 웹을 고친 묶음은 `(cd web && npx eslint . && npx tsc --noEmit)` 를 더한다.

| 묶음 | 묶음 명령(공통에 더해) |
|---|---|
| FND | `uv run pytest tests/test_store_layout.py tests/unit/core/test_trade_rows.py tests/unit/store/test_trade_repo.py tests/unit/config/test_trade_config.py tests/unit/fixtures/test_p6_world.py tests/unit/api/test_routes_trade_shape.py tests/unit/api/test_web_fixtures.py tests/unit/public_export/test_manifest.py tests/unit/scheduler -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.services.api openapi --check && (cd web && npx vitest run test/pager.test.ts test/tier.test.ts test/scripts.test.ts test/source.test.ts test/ui_p6.test.ts)` |
| COL | `uv run pytest tests/unit/collectors/test_customs.py tests/unit/data/public/test_customs.py tests/unit/fakes/test_customs_world.py -q && uv run python tests/fixtures/synthetic/datago/make_fixtures.py --check` |
| FLASH | `uv run pytest tests/unit/engines/trade/test_flash.py tests/property/test_trade_flash_props.py tests/unit/engines/market/test_ribbon.py tests/unit/public_export/test_credit_json.py tests/unit/public_export/test_trade_flash_json.py tests/unit/api/test_routes_trade_flash.py tests/unit/reports/briefs -q && uv run python -m tests.unit.api.make_web_fixtures --only flash && uv run pytest tests/unit/api/test_web_fixtures.py -q -k flash && (cd web && npx vitest run test/pages/p7_flash.test.ts test/pages/ribbon.test.ts)` |
| EXP | `uv run pytest tests/unit/engines/trade/test_explore.py tests/unit/engines/trade/test_surge.py tests/unit/engines/trade/test_price_volume.py tests/unit/engines/trade/test_items104.py tests/unit/engines/trade/test_derive_job.py tests/property/test_trade_props.py tests/unit/config/test_trade_items.py tests/unit/api/test_routes_trade_explore.py tests/unit/public_export/test_trade_explore_json.py -q && uv run python -m tests.unit.api.make_web_fixtures --only explore && uv run pytest tests/unit/api/test_web_fixtures.py -q -k explore && (cd web && npx vitest run test/pages/p7_explore.test.ts)` |
| MAP | `uv run pytest tests/unit/config/test_trade_map.py tests/unit/engines/trade/test_mapping.py tests/unit/engines/trade/test_overlay.py tests/unit/engines/trade/test_map_jobs.py tests/unit/engines/trade/test_verify_trade.py tests/property/test_trade_overlay_props.py tests/unit/api/test_routes_trade_map.py tests/unit/public_export/test_trade_stock_json.py -q && uv run python -m kbj.services.engine.verify_trade --map-only config/knowledge/trade_map.yaml && uv run python -m tests.unit.api.make_web_fixtures --only map && uv run pytest tests/unit/api/test_web_fixtures.py -q -k map && (cd web && npx vitest run test/pages/p8_trade_overlay.test.ts test/pages/p7_stocks.test.ts)` |
| WRAP | 공통 + `uv run pytest tests/acceptance -q && uv run pytest tests/sim -m "sim and not slow" -q -k "flash_day" && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.services.api openapi --check && uv run python scripts/check_public_safety.py && (cd web && npx vitest run && npm run build:public && npm run build:login && node scripts/check-bundle.mjs dist-public && node scripts/check-bundle.mjs dist-login)` — 그 뒤 메인이 `-m integration`·sim 전체·legacy 전체 |

`--collect-only` 로 경로가 잡히는지 먼저 본다: `uv run pytest --collect-only -q <경로들>`. 웹 빌드 스크립트 이름은 `web/package.json`:18·19(`build:public`·`build:login`).

---

## 2. 공개/로그인 배치

| 페이지·기능 | 공개(정적 사이트 — `pub_*` 만) | 로그인(VM API) | 구조로 막는 곳 |
|---|---|---|---|
| 7 속보 | 10일·20일·월 4종 전부 | 같음 | 원천 공개(DATA_TIERS §1) |
| 7 품목·트리맵·단가 대 물량 | HS2·4·6, 24개월(장별 파일) | + HS10, 60개월 | 크기만의 차이(D-P6-18) |
| 7 국가·품목×국가 | 국가 총괄 60개월, 104품목의 상위 국가 | + 임의 품목×국가 | 같음 |
| 7 지역(시군구) | 매핑 HS6 의 시군구 | 같음 | 수집 범위가 매핑 HS6 뿐(F3) |
| 7 급등·104품목·매핑 종목 랭킹 | 전부 | 같음(+ local 매핑 종목) | `origin: local` 제외 시험 |
| 8 겹쳐 보기 | 수출 지수 YoY 대 DART 분기 매출 YoY·매핑 표·근거 | + 월말 종가 한 줄 | 종가 모듈은 공개 번들에서 제외(번들 검사), 공개 `trade/stock/*.json` 에 시세 칸 0(시험) |
| 상단 띠 '수출 속보' | 칩 | 칩 | `credit.json` 칩 목록 |
| 텔레그램(마감 요약 한 줄) | — | 본인 채팅 | 계약 ⑯(리포트는 어댑터를 모른다) |

공개 산출물에 관세청 집계가 실리는 곳 = 공개 레포의 고아 브랜치 `public-data`(ADR 0011, `push.py`:47·48). 레포 **본 브랜치·픽스처에는 합성만**. ⚠ 이 구분이 사용자 규칙과 맞는지 §10 Q3.

---

## 3. 데이터 흐름

### 3.1 한눈에

```
관세청(공공데이터포털, XML)                         config/knowledge/trade_map.yaml (+ VM local)
  │ 10일 4종 ── trade.customs_tenday(1·11·21±2일, 20분 폴링) ─▶ pub_trade.ten_day·ten_day_columns
  │ 월간 4종 ── trade.customs_monthly(14~20일)  ─▶ pub_trade.{item,country,item_country,sigungu}_monthly·hs_dim
  │                                                          │                         │
  │                                     trade.derive(14~20일) ▼        trade.map_load(07:35) ▼
  │                                     pub_trade.surge·item_group_monthly   pub_trade.stock_map
  │                                                          │                         │
  │                         pub_fin.quarterly(P4) ──▶ trade.stock_index(21:00) ─▶ pub_trade.stock_export
  ▼
API /api/trade/*(로그인 VM) ── 페이지 7·8(로그인 빌드)         상단 띠 칩(/api/market/ribbon)
public.export(05:30·11:30·14:30·17:30) ── trade/*.json·credit.json ── 공개 정적 사이트
brief.closing(16:00) ── 속보 한 줄 ── notifier ── 텔레그램
```

### 3.2 작업 표 (P6 에 만드는 것·바꾸는 것 — 시각 KST)

| 작업 | 시각 | 입력 | 쓰기 | 켬(WRAP) |
|---|---|---|---|---|
| `trade.customs_tenday` | 1·2·3·11·12·13·21·22·23일 09:00~17:40, 20분 | DATAGO 15157908·941·901·909 | `pub_trade.ten_day`·`ten_day_columns` | **꺼짐**(#6·#17·#55) |
| `trade.customs_monthly` | 14~20일 10:10·15:10 | DATAGO 15101609·15101612·15100475·15134343 | `pub_trade.item_monthly`·`country_monthly`·`item_country_monthly`·`sigungu_monthly`·`hs_dim` | **꺼짐**(#1~#5) |
| (백필) | `python -m kbj.services.collectors.customs backfill --from 2021-10 --to 2026-09`(스케줄러 `backfill` 은 `backfill_of` 작업을 **거래일마다** 도는 P3 KRX 용이라 월 단위에 맞지 않는다 — `kbj/services/scheduler/__main__.py`:2·10) | 같음 | 같음 | 사람이 실행 |
| `trade.map_load` | 매일 07:35 | `trade_map.yaml` + local | `pub_trade.stock_map` | 켬 |
| `trade.derive` | 14~20일 10:40·15:40(굳은 의존 월간) | pub_trade 월간 | `pub_trade.surge`·`item_group_monthly` | 켬(입력이 없으면 `not_ready`) |
| `trade.stock_index` | 매일 21:00 | stock_map·월간·시군구·`pub_fin.quarterly` | `pub_trade.stock_export` | 켬 |
| `public.export` | 05:30·11:30·14:30·17:30 | pub_* | 공개 브랜치 | 이미 켬 |
| `brief.closing` | 16:00 | + `pub_trade.ten_day` | — | 이미 켬 |

### 3.3 마이그레이션 `0019_trade.sql` (DDL 요약 — FND)

| 표 | 키(UNIQUE) | 칸 | 쓰는 작업 |
|---|---|---|---|
| `pub_trade.ten_day` | (kind, ym, seg, col_no) | name, usd numeric(20,0), first_usd, first_seen_at, revised_at, span_text(`01~31`), source, as_of, quality | `trade.customs_tenday` |
| `pub_trade.ten_day_columns` | (kind, col_no, valid_from) | name, seen_at | `trade.customs_tenday` |
| `pub_trade.item_monthly` | (ym, hs) | hs_len, exp_usd, exp_kg, imp_usd, imp_kg, bal_usd, source, as_of, quality, fetched_at | `trade.customs_monthly` |
| `pub_trade.country_monthly` | (ym, country) | country_name, exp_cnt, exp_usd, imp_cnt, imp_usd, bal_usd, … | 같음 |
| `pub_trade.item_country_monthly` | (ym, country, hs) | hs_len, exp_usd, exp_kg, imp_usd, imp_kg, … | 같음 |
| `pub_trade.sigungu_monthly` | (ym, sido_cd, sgg_nm, hs6) | region_id, exp_usd, exp_cnt, imp_usd, imp_cnt, … | 같음 |
| `pub_trade.hs_dim` | (hs) | hs_len, name_ko, first_ym, last_ym | 같음 |
| `pub_trade.surge` | (ym, level, key, flow) | yoy, yoy_3m, usd, rank, rule | `trade.derive` |
| `pub_trade.item_group_monthly` | (ym, group_id) | exp_usd, exp_kg, imp_usd, imp_kg, yoy, unit_price, mode(`fixed`\|`top_n`) | `trade.derive` |
| `pub_trade.stock_map` | (code, hs, flow, region_id) | name, weight, countries text[], origin(`public`\|`local`), reviewed, evidence jsonb, map_version | `trade.map_load` |
| `pub_trade.stock_export` | (code, period) — period = `YYYY-MM`·`YYYYQn` | export_usd, yoy, revenue_yoy, lag_corr jsonb, status, map_version | `trade.stock_index` |

공통: `ym` 은 `YYYY-MM` text(기존 데이터 키와 같은 모양), 모든 행 `source`·`as_of`·`quality`. hypertable 여부는 `0014_macro.sql`·`0016_market_stats.sql` 관례를 FND 가 따른다 [확인 필요]. 권한은 `0001_schemas.sql`:58~72 의 pub 기본 권한. ⚠ `stock_map` 의 `origin: local` 행이 pub 스키마에 있어도 공개 내보내기는 `origin = 'public'` 만 읽는다(시험) — 아예 `prv_trade` 로 나눌지는 §10 Q3 의 답을 따른다 [제안: 지금은 한 표, 필터 시험].

예상 행 수 [실측 필요 #57]: item_monthly(HS10 기준 약 1.2만 × 60개월 ≈ 72만), item_country_monthly(국가 269 × 응답 행 — 생략 시 자릿수에 따라 수백만), sigungu(매핑 HS6 약 100 × 시군구 약 250 × 60 ≈ 150만 상한).

### 3.4 공공데이터포털 호출량 (`limits.yaml datago` — 25/s, 데이터셋당 9,500/일, 변경 없음)

| 작업 | 데이터셋 | 1회 | 하루 최대 |
|---|---|---|---|
| 10일 폴링 | 4종 × 1 | 4 | 27회 × 4 = 108(데이터셋당 27) |
| 월간 정기 | 15101609 `by_chapter` 97 · 15101612 1 · 15100475 국가 약 269 · 15134343 매핑 HS6 약 100 × 18시도 = 1,800 | 약 2,170 | 2회 실행이지만 두 번째는 선점 done → 1회분 |
| 백필 5년 | 15101609 97 × 5 = 485 · 15100475 269 × 5 = 1,345 · 15134343 1,800 × 5 = 9,000 | — | 15134343 만 이틀로 나눔(90% 에서 멈춤) |

ECOS 한도표(`limits.yaml ecos` — 2/s·602 반감·하루 3회면 닫기)는 **P6 영향 없음** — P6 은 ECOS 를 부르지 않는다. 단가 대 물량의 참고선 '수출물가지수'는 P5 가 이미 쌓는 `pub_macro.series` `ECOS:402Y014:export_px`(`config/macro.yaml`:36)를 읽기만 한다(그 작업 `macro.monthly` 는 실측 #43 전이라 꺼짐 — 없으면 참고선 생략).

### 3.5 설정 파일 (FND — 숫자·목록은 한 곳)

| 파일 | 내용 |
|---|---|
| `config/trade.yaml` | `tenday: {lookback_months: 2}`, `monthly: {items_mode: by_chapter, countries: all, sido: all}`, `backfill: {years: 5, stop_at_ratio: 0.9}`, `surge`(§26 문턱), `public_export: {months: 24, dir_cap_mb: 20, file_cap_kb: 1024}`, `overlay: {min_quarters: 8, lags: [0,1,2]}`, `items104: {mode: fixed, n: 104}` |
| `config/knowledge/trade_map.yaml` | §4.5 |
| `config/knowledge/trade_items.yaml` | `groups: [{id, name, hs: [접두…]}]` — 104 |
| `config/knowledge/trade_regions.yaml` | `regions: [{id, sido_cd, sgg_nm, valid_from, valid_to}]` — 개편 대응 |
| `config/knowledge/hs_chapters.yaml` | `chapters: {"01": "산 동물", …}` 자체 한글 이름(HS 체계는 WCO 공개 분류) [확인 필요: 77장 결번] |

---

## 4. 엔진 명세 (정의 본문은 부록 A — FND 가 `docs/metrics.md` §25~§29 로 옮긴다)

### 4.1 속보 (FLASH — §25)
구간 seg 1 = 1~10일, 2 = 1~20일, 3 = 1~말일(누계). YoY = 같은 kind·col·seg 의 전년 값 대비. 칸 이름은 응답 열 번호가 아니라 `ten_day_columns` 의 그 시점 이름. 상단 띠 칩 = 수출 품목(15157908) `col 00` 최신 구간 YoY, 기준 `YYYY-MM 1~20일`, quality `estimated`. 열 대응이 바뀐 구간은 YoY 를 내지 않는다(이름이 다르면 비교 불가 — 사유 칸).

### 4.2 탐색 (EXP — §26)
접두 합산은 응답의 가장 깊은 자릿수에서 위로 더한다(같은 달 같은 접두에 상위·하위 행이 함께 오면 하위만 — 이중 합산 금지, 시험). 단가 = 금액 ÷ 중량(중량 0·None 이면 None). 3개월 평균 = 직전 3개월 합 ÷ 3. 급등: 수준 문턱(월 1천만 달러 [제안]) 이상이면서 YoY ≥ +50% 이고 3개월 YoY ≥ +30%(급락은 부호 반대) — 수준 미달은 목록에서 뺀다.

### 4.3 단가 대 물량 (EXP — §27)
`Δln(금액) = Δln(단가) + Δln(중량)`(YoY 기준, 항등식 — 속성 시험). 화면은 금액 YoY 막대를 단가·물량 기여로 나눠 쌓는다(`ui/bars.ts` 양·음). 중량 단위가 의미 없는 품목(선박 등)은 '중량 기준 부적합' 꼬리표 [확인 필요: 목록 — `config/trade.yaml price_volume.weight_unfit`].

### 4.4 104품목 (EXP — D-P6-9)
`fixed`: `trade_items.yaml` 묶음 그대로 합산. `top_n`: 지난 12개월 수출액 상위 HS4 n 개(동률은 코드 순) — 매달 바뀔 수 있어 화면에 '자동 선택' 꼬리표. 묶음 접두가 겹치면 설정 오류(시험).

### 4.5 매핑표 (MAP — §28)
```yaml
version: 1
stocks:
  - code: "000000"          # 6자리 영숫자(P3 R 영숫자 코드 허용)
    name: 예시전자
    reviewed: false
    items:
      - {hs: "854232", flow: exp, weight: 0.55, countries: [CN, US]}   # 접두 2~10 짝수 자리
    regions:
      - {region_id: "KR-41-xxx", hs6: "330499", weight: 0.3}
    evidence:
      - {kind: dart_business_report, rcept_no: "<접수번호>", note: "매출 구성 문단 요약(자체 문장)"}
```
검증(CI — `test_trade_map.py`): 형식·짝수 자리·HS2 장이 `hs_chapters.yaml` 에 있음·가중치 0~1·종목별 합 ≤ 1·중복 0·근거 1개 이상·지역 ID 가 `trade_regions.yaml` 에 있음. 검증(VM — `verify_trade`): 종목코드가 DART 상장사 목록(`pub_filings` 회사 표 [확인 필요: 표 이름])에 있음, HS 접두가 `pub_trade.hs_dim` 에 있음 [실측 필요 #56]. 관세청 조회코드표 원본은 레포에 넣지 않는다.

### 4.6 겹쳐 보기 (MAP — §28)
`E_t = Σᵢ wᵢ·Xᵢ,t`, `Xᵢ,t` = 품목 행이면 그 접두의 수출(국가 목록이 있으면 그 국가 합 — 품목×국가 표), 지역 행이면 시군구×HS6. 분기 합 → YoY. 매출 YoY = `pub_fin.quarterly` 매출(보고 기준 — P4 `to_quarterly`). 상태: `ok`·`no_revenue`(재무 미제출)·`short_overlap`(< 8분기 — 선은 그리되 상관 없음)·`no_trade`(매핑 접두 자료 없음). 시차 상관 = 피어슨, 시차 0·1·2분기(수출이 앞섬). 랭킹 = 최근 3개월 `E` YoY. 로그인 종가 줄은 `/api/stock/{code}/chart` 의 월말 값(새 경로 없음).

---

## 5. API (로그인 VM — DB 만 읽는다, 계약 ⑩)

| 경로 | 응답 | 리더(묶음) |
|---|---|---|
| `GET /api/trade/flash?kind=&months=` | 구간 표·YoY·열 이름·발표 당시/최신 | `trade_flash`(FLASH) |
| `GET /api/trade/items?level=2\|4\|6\|10&parent=&flow=&from=&to=` | 접두 트리 한 층·트리맵 입력 | `trade_explore`(EXP) |
| `GET /api/trade/items/{hs}/series?flow=` | 월 금액·중량·단가·YoY·분해 | 같음 |
| `GET /api/trade/items/{hs}/countries?ym=` | 품목×국가 | 같음 |
| `GET /api/trade/countries?ym=`·`/api/trade/countries/{cc}/items?ym=` | 국가 총괄·국가별 품목 | 같음 |
| `GET /api/trade/regions?hs6=&sido=` | 시군구 표 | 같음 |
| `GET /api/trade/surges?ym=&level=` | 급등·급락 | 같음 |
| `GET /api/trade/items104?ym=` | 104 묶음 | 같음 |
| `GET /api/trade/stocks` | 매핑 종목 랭킹 | `trade_map`(MAP) |
| `GET /api/trade/stock/{code}` | 매핑·지수·YoY·상관·상태 | 같음 |

캐시 도메인 `trade`(`cache.py`) — 무효화 작업: `trade.customs_tenday`·`trade.customs_monthly`·`trade.derive`·`trade.map_load`·`trade.stock_index`. 상단 띠는 기존 `/api/market/ribbon`(도메인 `market`)에 `trade.customs_tenday` 를 무효화 작업으로 더한다(FND).

## 6. 프런트

- 페이지 7 `p7_trade.ts`: 탭 `속보 | 품목 | 국가 | 지역 | 급등 | 104품목`(`ui/tabs.ts`), 탭은 처음 보일 때 그린다(P8 방식). 패널마다 출처 '관세청(공공데이터포털)'·기준 월·품질 칩·CSV 버튼. '품목' 탭 아래에 매핑 종목 랭킹 표(MAP 모듈).
- 공개 빌드: `ctx.source.get('trade/…')`(정적 JSON), 로그인 빌드: 같은 키 → `/api/trade/…`(`web/src/data/api.ts` 매핑 — FND). HS10 탭 단계는 로그인만(공개는 자물쇠가 아니라 '로그인판에서 HS10' 안내 한 줄 — 공개 등급 자료라 자물쇠 문구를 쓰지 않는다).
- 페이지 8: '재무' 탭 `[재무, 수출 겹쳐 보기]`. 매핑이 없는 종목은 패널 하나 '매핑 없음 — 매핑표에 없는 종목' (오류 아님).
- 새 차트 부품 없음. 새 CSS 클래스는 `web/src/design/components.css` 에 필요할 때만(FND 에 요청).

## 7. 공개 정적 사이트 내보내기 영향

| 파일 | 내용 | 상한 [제안] |
|---|---|---|
| `trade/flash.json` | 4종 × 10년 구간 | 200 KB |
| `trade/hs2.json`·`trade/hs/<2자리>.json` | HS2 총괄 / 장별 HS4·HS6 24개월 | hs2 1 MB · 장별 2 MB(2026-10-21 — 84장 기계 1.11 MB) |
| `trade/countries.json`·`trade/country/<ISO2>.json` | 국가 총괄 60개월 / 국가별 상위 품목 | 파일 1 MB |
| `trade/region/<hs6>.json` | 매핑 HS6 의 시군구 | 파일 200 KB |
| `trade/surge.json`·`trade/items104.json`·`trade/stocks.json` | — | 파일 1 MB |
| `trade/monthly.json`(월별 추이 — metrics §29a) | — | 파일 256 KB |
| `trade/stock/<code>.json` | `origin: public` 매핑 종목의 겹쳐 보기(시세 칸 없음) | 파일 100 KB |
| `credit.json` | 칩 목록에 `export_flash` | 기존 100 KB |

폴더 `trade` 합계 20 MB [제안 — 실측 #57]. 이름 규칙 `trade/(hs|country|region|stock)/[0-9A-Z]{2,10}\.json` 과 평평한 `trade/[a-z0-9_]+\.json` — `manifest.py`·`merge-public-data.mjs`·`static.ts` 세 곳이 같은 정규식(시험). 공개 안전 검사: 로그인 스키마 이름·관심종목 흔적·`origin: local` 종목 0.

---

## 8. 시험·검증

| 층 | 무엇 | 묶음 |
|---|---|---|
| 파서·수집 | 가짜 포털(파라미터 반영·공표 시각·잘림·열 변경·GW 오류 22/23/30) × 처리기, 백필 이어 받기, 선점 done 뒤 재호출 0 | COL |
| 엔진 | 속보 YoY·접두 합산 이중 합산 금지·급등 문턱 경계·로그 분해 항등식·104 겹침 오류·매핑 검증·지수 선형성·상관 겹침 부족 | FLASH·EXP·MAP |
| 속성(hypothesis) | `tests/property/test_trade_{flash_,,overlay_}props.py` | 각 묶음 |
| API·공개 | 봉투 모델·`NoData`·캐시 무효화, 공개 파일 이름 규칙·상한·관심종목 흔적 0·`local` 0·시세 칸 0 | FND·각 묶음 |
| 웹 | 페이지 7 탭 6 × 등급 2, 페이지 8 패널(매핑 있음/없음/겹침 부족/로그인 종가), CSV BOM·이스케이프, 공개 번들에 `trade_overlay_price` 문자열 없음 | 각 묶음·WRAP |
| 수용 | `test_trade_map_watchlist50.py`, `web/test/acceptance/p6_pages.test.ts` | WRAP |
| 시뮬레이션 | `test_flash_day.py`(§0.3 ①~⑥) | WRAP |
| 실데이터(키 뒤) | 부록 B #55~#57 + 기존 #1~#6·#17 | 사용자·메인 |

---

## 9. 위험·미정

| # | 위험 | 영향 | 대응 |
|---|---|---|---|
| R1 | 공개 JS 예산 419 B 남음 | 페이지 7 이 들어가면 번들 검사 실패 | §10 Q1(기본: 72 KB 로 올림·ADR 0025). 답이 '안 올림'이면 페이지 7 을 공개판에서 표만(트리맵·단가 대 물량 차트는 로그인)으로 줄인다 |
| R2 | IDX 가 커밋 전에 공유 파일을 더 바꿈 | FND 충돌 | D-P6-1 시작 조건, FND 첫 작업으로 §1.8 표를 `git diff --stat HEAD~1` 과 대조 |
| R3 | IDX 가 공개 JS 를 더 씀 | R1 악화 | WRAP 이 IDX 커밋 뒤 예산을 다시 잰다 |
| R4 | 관세청 공표 시각·주말 규칙 미상 | 당일 반영이 늦거나 빈 폴링 | 사흘 창 20분 폴링(D-P6-6), #55 로 확정 뒤 창 축소 |
| R5 | `hsSgn` 생략·자릿수 의미 미상(#2·#3) | 이중 합산·누락 | 응답 `hs_len` 저장 + 하위 우선 합산 + 장별 수집 기본 |
| R6 | 응답 잘림(페이징 없음, #1) | 조용한 누락 | 어댑터 `CustomsTruncated` + 장·국가 단위로 쪼갠 호출, 합계 대조(국가 합 = 15101612) 경고 |
| R7 | 품목×국가·시군구 행 수 | DB·공개 크기 | 공개는 24개월·장별, HS10·임의 조합은 로그인 API, #57 |
| R8 | 매핑 품질(가중치·근거) | 겹쳐 보기 오해 | `reviewed: false` 꼬리표, 근거 필수, 환율 미조정 꼬리표 |
| R9 | 매핑 우주 = 관심종목이면 공개판이 관심종목을 드러냄 | 개인 데이터 노출 | 공개 매핑은 관심종목과 무관하게 고르고 관심종목 전용은 VM local(D-P6-14), 시험 |
| R10 | 10일 열 구성이 바뀜 | 이름이 바뀐 채 저장 | `TenDayColumnsChanged` 실패·`ten_day_columns` 기록 |
| R11 | 시도 코드 개편(2026-07) | 시군구 시계열 끊김 | `trade_regions.yaml` 통합 ID(F4) |
| R12 | 104품목 정의가 사용자 기대와 다름 | 화면 재작업 | §10 Q2, `mode` 전환만으로 바꿀 수 있게 |
| R13 | 공개 내보내기 하루 4회 | VM 부하·푸시 횟수 | 강제 푸시 커밋 1개, 실패해도 다음 회차가 덮음 |

### 9.1 P3~P5·IDX 에서 기대는 것 — 실제 코드 확인(2026-10-13, HEAD `138da8f` + 작업 트리)

| 기대 | 확인 |
|---|---|
| 마지막 마이그레이션 | 작업 트리 `0018_index.sql`(IDX, 미커밋) — P6 은 0019~ |
| `pub_trade` 스키마 있음·표 없음 | ✓ `0001_schemas.sql`:17 |
| 관세청 어댑터·카탈로그·데이터셋 8개 | ✓ `kbj/data/public/customs/*`, `kbj/data/catalog.py`:32·92 |
| 공공데이터포털 리미터 | ✓ `config/limits.yaml`:65(25/s, 9,500/일/데이터셋) |
| 등록부 P6 두 줄(처리기 없음) | ✓ `config/jobs.yaml`:337·352 |
| 데이터 키 `ten_day`·`month`·조건 `month_days` | ✓ `kbj/services/scheduler/conditions.py`:51·88·139 |
| 가짜 포털·합성 XML | ✓ `tests/fakes/datago_server.py`(관세청 월간 3종만, 파라미터 무시), `tests/fixtures/synthetic/datago/*` |
| 시뮬레이션이 가짜 포털을 씀 | ✓ `tests/sim/harness.py`:138·187·542 |
| 상단 띠 '수출 속보' 자리 | ✓ `kbj/engines/market/ribbon.py`:68·77, `web/src/app/ribbon.ts`:33 |
| 공개 내보내기 P6 자리 | ✓ `kbj/services/public_export/export.py`:399 주석 |
| 페이지 7 자리표시·glob | ✓ `web/src/app/pages.ts`:42·84(glob 에 7 없음) |
| 페이지 8 탭 모듈 구조 | ✓ `web/src/pages/p8_stock.ts`:44·91(`tabModules`) |
| 차트 부품 | ✓ `web/src/ui/{line,bars,treemap,table,tabs,seg}.ts` — CSV 부품 없음 |
| 로그인 종가 경로 | ✓ `kbj/services/api/routes/stock.py`:93 `/{code}/chart` |
| 분기 매출 | ✓ `pub_fin.quarterly`(`0011_fin.sql`:34) |
| P4 관심종목 수용 시험 틀 | ✓ `tests/acceptance/test_stock_detail_watchlist20.py` |
| 관심종목은 개인 데이터 | ✓ `0013_journal_watchlist.sql`:6, `config/reports.yaml`:8 |
| 수출물가지수 시리즈 | ✓ `config/macro.yaml`:36(`ECOS:402Y014`, 실측 #43) |
| 지표 마지막 § | 작업 트리 §24(IDX) — P6 은 §25~ |
| 실측 마지막 번호 | 작업 트리 #54(IDX) — P6 은 #55~ [IDX 커밋 뒤 확인] |
| ADR 마지막 | 0024(0020 비어 있음) — P6 은 0025~ [IDX 가 쓰면 그다음] |
| import-linter | ①~⑭·⑯(⑮ 비움) — P6 은 ⑰ |
| bok·104품목 원본 | ✗ 레포에 없음(U3) |
| SD 수출·HS | ✗ 없음 |

### 9.2 이 설계로 남길 ADR (초안 → WRAP 이 확정)

| ADR | 내용 | 초안 |
|---|---|---|
| 0025 | 공개 JS 예산 조정(페이지 7 공개 전체) | WRAP |
| 0026 | 수출 속보 반영(폴링 창·데이터 키·발표 당시/최신·열 변경 실패·16:00 한 줄·공개 내보내기 4회) | FLASH |
| 0027 | 종목↔HS 매핑표(자체 큐레이션·근거 필수·공개/local 분리·검증 두 층·겹쳐 보기 정의·104품목 재정의) | MAP |

---

## 10. 사용자만 할 수 있는 것

| # | 질문·작업 | 기본값(답이 없을 때) |
|---|---|---|
| Q1 | 공개 JS gzip 예산을 60 KB → **72 KB** 로 올려도 되는지(페이지 7 은 공개 전체라 로그인 분리로 못 줄인다) | 올린다(72 KB, ADR 0025) |
| Q2 | "수출 104품목"을 무엇으로 정의할지 — bok 원본 목록(출처 불명 xlsx, 레포에 없음)을 주실 수 있는지 | 원본 없이 **HS4 자체 묶음 104**(최근 12개월 수출 상위 HS4 를 고정한 첫 판). 원본을 주시면 이름·HS 접두로 옮겨 `fixed` 로 |
| Q3 | 관세청 **가공 집계**(장별 JSON)를 공개 레포의 고아 브랜치 `public-data` 에 실어도 되는지(본 브랜치·픽스처는 합성만) | 싣는다(DATA_TIERS §1 공개·ADR 0011). 안 된다면 페이지 7 은 로그인판만 |
| Q4 | 수출 속보를 공표 직후(장중) 텔레그램 한 통으로 따로 받을지 | 따로 보내지 않는다 — 16:00 마감 요약 한 줄(ADR 0018) |
| Q5 | 조업일수·일평균 수출을 넣을지(관세청 조업일수 정의 필요) | 넣지 않는다(누계 YoY 만) |
| Q6 | 뉴스 RSS(P5 가 P6 로 미룬 제안 D-P5-22)를 P6 에 넣을지 | 넣지 않는다(P8 이후) |
| Q7 | 공개 매핑표 첫 판 60종목을 검토해 줄지(가중치·근거) / 관심종목 중 매핑할 종목을 VM local 파일로 줄지 | 초안은 `reviewed: false` 로 커밋, 관심종목 전용은 local 파일이 비어 있는 채 시작 |
| Q8 | 겹쳐 보기에 원화 환산을 넣을지(공개 원화 환율 시리즈 확인 필요) | 넣지 않는다(달러, '환율 미조정') |
| — | `KBJ_DATAGO_KEY` 로 관세청 8개 ID 활용신청(운영 심의승인: 15100475·15101609·15134343), 실측 #1~#6·#17·#55~#57 뒤 `trade.customs_*` 켜기, 5년 백필 실행, VM local 매핑 파일 경로 설정 | — |

---

## 부록 A. `docs/metrics.md` 에 넣을 지표 정의 (FND 가 §25~§29 로 옮긴다 — 공식·입력·단위·예외·품질·시험)

### §25 수출 속보 (공개 — 관세청 10일 잠정치)

| 지표 | 공식 | 입력·단위 | 예외·품질 | 시험 |
|---|---|---|---|---|
| 구간 누계 | 응답 값 × 1,000 | 15157908·941·901·909, 천 달러 → 달러 | `estimated`. 쉼표·앞 공백 문자열(`parse_amount`) | `test_flash.py` |
| 구간 YoY | `v(kind,col,ym,seg) / v(kind,col,ym−12,seg) − 1` | 같음 | 전년 값 0·None → None(사유). 열 이름이 다르면 None | 같음·속성 |
| 칩 | 수출 품목 col 00 최신 seg YoY | 같음 | 공표 후 처음 본 값(`first_usd`) | `test_ribbon.py` |
| 10대 기여도 | `Δvᵢ / Δv₀₀` | 같음 | 분모 0 → None | 같음 |

### §26 탐색·급등 (공개 — 관세청 월간)

| 지표 | 공식 | 입력·단위 | 예외·품질 | 시험 |
|---|---|---|---|---|
| 접두 합 | 같은 달 접두 아래 **가장 깊은 행만** 합 | item_monthly, 달러·kg | 상위·하위 동시 행 → 하위만 | 속성(접두 합 = 상위 합) |
| YoY·3개월 YoY | 월 / 전년 같은 달 − 1, 3개월 합 / 전년 3개월 합 − 1 | 같음 | 0·None → None | `test_explore.py` |
| 비중 | 품목 / 전체 | 같음 | — | 같음 |
| 급등 | 수준 ≥ `surge.min_usd`(1천만 달러 [제안]) ∧ YoY ≥ 0.5 ∧ 3개월 YoY ≥ 0.3 | 같음 | 급락은 −0.3·−0.2 [제안] | `test_surge.py` 경계 |

### §27 단가 대 물량 (공개)

| 지표 | 공식 | 예외 | 시험 |
|---|---|---|---|
| 단가 | 금액 ÷ 중량(달러/kg) | 중량 0·None → None, `weight_unfit` 꼬리표 | `test_price_volume.py` |
| 분해 | `ln(1+YoY금액) = ln(1+YoY단가) + ln(1+YoY중량)` | 어느 하나 None → 분해 없음 | 속성(항등식 오차 < 1e-9) |

### §28 종목 수출 지수·겹쳐 보기 (공개 매핑 → 공개, local 매핑 → 로그인)

| 지표 | 공식 | 입력·단위 | 예외·품질 | 시험 |
|---|---|---|---|---|
| 수출 지수 | `E_t = Σ wᵢ·Xᵢ,t` | stock_map·월간·품목×국가·시군구, 달러 | 입력 하나라도 `estimated` → `estimated` | `test_overlay.py`·속성(선형성) |
| 분기 YoY | 분기 합 / 전년 분기 합 − 1 | 같음 | 3개월 중 결측 → None | 같음 |
| 매출 YoY | `pub_fin.quarterly` 매출 | 원 | 미제출 → `no_revenue` | 같음 |
| 시차 상관 | 피어슨(E YoY 의 k 분기 전, 매출 YoY), k ∈ {0,1,2} | — | 겹침 < 8 → None(`short_overlap`) | 같음 |
| 랭킹 | 최근 3개월 E YoY 내림차순 | — | 동률 코드 순 | `test_routes_trade_map.py` |

### §29 104품목 (공개)
`fixed` = 묶음 접두 합(§26 접두 합 규칙), `top_n` = 직전 12개월 수출 상위 HS4 n. 묶음 사이 접두 겹침 = 설정 오류.

## 부록 B. `docs/probe_results.md` §7 에 더할 줄 (#55~#57 — 값은 적지 않고 칸·단위·코드·반영 시각만)

| # | 출처 | 확인할 것 |
|---|---|---|
| 55 | 관세청 10일 잠정치(P6) | 1·11·21일이 주말·공휴일일 때 공표일(당일·다음 영업일), API 반영 시각 분포(보도 시각 대비 지연), 같은 구간 재공표 여부 — 막는 작업: `trade.customs_tenday`(폴링 창 축소) |
| 56 | 매핑표 실데이터 검증(P6) | `python -m kbj.services.engine.verify_trade --watchlist main --json` 건수: 매핑된 관심종목 수·없는 종목코드·없는 HS 접두·겹쳐 보기 상태별 수(값 없음) |
| 57 | 관세청 월간 크기(P6) | 품목·품목×국가·시군구 응답 행 수(달마다), 저장 행 증가, 공개 `trade/` 폴더 크기 — `config/trade.yaml public_export` 상한 확정 |

기존 #1~#6(관세청 5종·10일)·#17(10일 swagger 열 설명)은 그대로 P6 작업을 막는 번호로 노트에 적는다.

## 부록 C. 이 설계가 읽은 근거

`docs/PLAN.md` §4·§5·§8(P5 2차 IDX·P6), `docs/reference/plan_kr_v0.2.md` §3·§7·§8, `docs/DATA_TIERS.md` §1·§2·§3, `docs/probe_results.md` §0·§1·§2·§7, `docs/inventory.md`:19·21·48·399~402, `docs/conflict_map.md`:269, `docs/adr/0001`(U3)·`0011`·`0018`, `docs/p4_design.md`·`docs/p5_design.md`(구조), `docs/p5_index_rebalance_prompt.md`:18·60·74·83, `kbj/data/public/customs/{client,models,datasets}.py`, `kbj/data/catalog.py`, `config/{jobs,limits,macro,reports}.yaml`, `kbj/services/scheduler/conditions.py`, `kbj/store/migrations/0001_schemas.sql`·`0011_fin.sql`·`0013_journal_watchlist.sql`, `kbj/engines/market/ribbon.py`, `kbj/services/public_export/{export,manifest,push,credit_json}.py`, `web/src/app/{pages,ribbon}.ts`, `web/src/pages/p8_stock.ts`, `web/src/data/static.ts`, `web/scripts/{check-bundle,merge-public-data}.mjs`, `tests/fakes/datago_server.py`, `tests/fixtures/synthetic/datago/README.md`, `tests/sim/harness.py`, `tests/acceptance/test_stock_detail_watchlist20.py`, `legacy/stock_dashboard/{valuechain.py,data/valuechain_map.json}`(구조만), `legacy/etf_traker/MIGRATION.md`:28, `pyproject.toml`(import-linter), `git status`(IDX 진행분).
