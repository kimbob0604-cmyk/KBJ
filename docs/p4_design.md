# KBJ P4 설계서 — 공시·재무·컨센서스·종목 상세

- 작성: 2026-10-07. 상태: **설계(구현 전)**. 대상 단계: `docs/PLAN.md` §8 P4 — "DART 피드·잠정실적·오버행·내부자, dart-report 모듈화(엑셀 + 화면), 컨센서스·리비전, 밸류에이션, TAM, 종목 상세 페이지 8".
- 기준 문서: 계획 `docs/PLAN.md` §2·§4·§8, 등급 `docs/DATA_TIERS.md`(정본 — **DART 는 공개 ✅확인, 컨센서스는 로그인**), 지표 `docs/metrics.md`(P4 지표는 부록 A 를 묶음 M 이 옮겨 정본으로 만든다), P2 정본 `docs/p2_design.md`·ADR 0004~0007, P3 설계 `docs/p3_design.md`·ADR 0009~0012, 정본 지정 `docs/conflict_map.md`(§1.5 DB 이관·§1.12 DART·§1.13 네이버·Q4 누적→분기), ADR 0001(U1~U4·Q1~Q13), 인벤토리 `docs/inventory.md`, 알림 `config/notify.yaml`·ADR 0005, 작업 등록부 `config/jobs.yaml`.
- 원본 줄 번호: 이 레포 `legacy/` 를 직접 열어 확인했다(작업 트리 — P3 구현 중). 표기 `ET:` = `legacy/etf_traker/`, `SD:` = `legacy/stock_dashboard/`, `DR:` = `legacy/etf_traker/dart-report/`. kbj 쪽은 레포 경로 그대로.
- 표기: **[추정]** = 코드로 끝까지 확인하지 못함, **[확인 필요]** = 기본값으로 정한 것, **[실측 필요]** = 키를 받은 뒤 실제 응답으로 확인, **[결정 필요]** = 사용자가 정할 것, **[제안]** = 이 설계의 권고, ⚠ = 충돌·위험.
- 키·토큰·계좌 값은 이 문서 어디에도 없다. `.env` 는 열지 않았다. 네트워크 호출은 하지 않았다 — DART 구조화 API·KIS 컨센서스 TR 의 필드 이름은 **레포 코드에 없는 것은 전부 [확인 필요]** 다. P4 의 모든 증명은 합성 데이터·가짜 서버로 하고, 실데이터 검증은 키를 받은 뒤 `docs/probe_results.md` §7 #28~#37 에 남긴다.
- **rsm0kk(제3자) 코드는 보지도 옮기지도 않는다**(U3). P4 대상(SD·ET board·dart-report)에는 rsm0kk 유래 파일이 없다(`grep -rl rsm0kk` — monitor/bok·kr 만).

---

## 0. 요약

### 0.1 결정 요약

| # | 결정 | 이유·근거 |
|---|---|---|
| D-P4-1 | **P3 가 끝난 뒤(묶음 S 커밋) 시작한다.** P4 가 고치는 파일 중 P3 가 지금 고치는 것: `config/jobs.yaml`, `kbj/data/private/kis/datasets.py`, `tests/fakes/kis_server.py`, `kbj/services/api/app.py`·`readers/_common.py`, `kbj/services/public_export/export.py`·`manifest.py`, `kbj/store/repos/__init__.py`·`memory.py`, `web/src/app/{pager,pages}.ts`, `pyproject.toml` | 같은 파일을 두 워크플로가 고치지 않게(작업 트리 공유). §1.0 웨이브 표의 '시작 조건' |
| D-P4-2 | 마이그레이션 번호: **0010 filings · 0011 fin(공개) · 0012 fin_prv(로그인) · 0013 journal_watchlist**. P2 설계 §8.2 의 `0008_filings`·`0009_fin` 계획은 P3 이 번호를 먼저 써서 버린다(D-P3-6 규칙 — 적용 순서대로 다음 번호) | `kbj/store/migrate.py` 는 번호 순서로 미적용분을 고른다. 공개·로그인을 파일 단위로 나눠 권한 시험이 파일마다 단순해진다 |
| D-P4-3 | **DART 수집 = 증분 목록 + 구조화 API 우선 + 원문 파싱 폴백.** `list.json` 을 매분 앞쪽 쪽부터 읽다 이미 본 접수번호를 만나면 멈춘다. 주요사항보고서(CB·BW·EB·유상·무상증자·자기주식)·지분공시(임원·주요주주 소유보고, 5% 대량보유)는 DART 구조화 엔드포인트로 받고 [확인 필요: 엔드포인트·필드], 잠정실적·전환청구·전환가액 조정은 원문(`document.xml`)을 파싱한다(SD 정규식 승격). **원문 자체는 저장하지 않는다**(파싱 결과 + 접수번호 링크만) | 일 예산 18,000(limits.yaml) 안에서 장중 매분 폴링(§3.5). SD `overhang_parser`·`earnings_parser` 의 정규식은 서식 편차에 약하다 — 구조화 응답이 있으면 그것이 정답 |
| D-P4-4 | **이벤트 연쇄는 이벤트 버스가 아니라 DB 대기 상태 폴링**: `pub_filings.disclosure.parse_state` 와 `pub_fin` 적재 표시를 다음 작업이 주기적으로 읽는다(`earnings.alerts` 5분, `fin.quarterly` 30분). 등록부 `fin.quarterly.schedule.triggered_by` 는 **지운다** — 실행기에 구현이 없다(`registry.py`:168 주석 "연결은 그 작업의 단계에서") | 실행기·선점 장부를 바꾸지 않는다. SD `earnings_pipeline_5min`(5분 파이프라인)과 같은 주기 |
| D-P4-5 | **재무 2층**: ① 주요계정(전 상장사 — `fnlttMultiAcnt` 100개 묶음, 매출·영업익·순익·자산·부채·자본) ② 전체 재무제표(상세 대상 = 관심종목 ∪ 컨센서스 유니버스 ∪ 공개 데모 목록 — `fnlttSinglAcntAll`, 원시 행을 `pub_fin.statement_raw` 에 저장). 분기 환산은 **dart-report `statements.to_quarterly` 하나**(ADR 0001 Q4 기본값) — ET board 의 '3개월값 우선' 규칙은 값으로 쓰지 않고 **대조 열**로만 남긴다 | 전 상장사 전체 재무제표는 2,700 × 20보고서 × 1.3 ≈ 7만 호출(5일치 예산). 두 환산식이 남으면 두 벌(CLAUDE §3) |
| D-P4-6 | **dart-report 승격 순서(P3 D-P3-10 과 같은 틀)**: ① legacy 원본으로 합성 골든 G1·G2 를 먼저 캡처해 커밋 → ② 순수 계산(`statements`·`costs`·`bridge`·`orders` 파서)은 `kbj/engines/financials/`, 엑셀 빌더·파이프라인은 `kbj/reports/dart_excel/` 로 함수 본문 그대로 옮긴다 → ③ 골든 비교 → ④ legacy `dartreport/*.py` 를 kbj 를 다시 내보내는 shim 으로, `config/mapping.yaml` 은 `config/dart_report/mapping.yaml` 로 `git mv`. **Streamlit `app.py` 는 지운다**(페이지 8 '리포트' 탭이 대체 — 오류 화면 키 노출 `app.py:106` 도 함께 사라짐) | PLAN 완료 기준 "이식 전과 같다". shim 뒤에 골든을 만들면 자기 자신과 비교 |
| D-P4-7 | **엑셀 골든 = 정규화 비교**(바이트 비교 금지 — CLAUDE §4): 시트 순서·이름·크기, 셀 값(수식 문자열은 정확히, 실수는 `isclose(rel 1e-9, abs 1e-9)`), 스타일(글꼴·채움·테두리·정렬)·서식 문자열, 열 너비·행 높이·병합·눈금선, **차트는 xlsx zip 의 `xl/charts/*.xml`·`xl/drawings/*.xml` 을 정규 XML 로**(openpyxl 은 읽을 때 차트를 버린다). 제외: `docProps/*`(생성 시각), 요약 시트의 '생성 시각' 값 칸(자리표시로 바꿈). 수식은 작은 평가기로 계산해 오류 0·`#DIV/0!` 0 을 단언 | dart-report CLAUDE.md "수식을 건드렸으면 재계산 검증 — 오류 0". openpyxl 버전 차이에도 같은 결과 |
| D-P4-8 | **legacy 동작(버그 포함)은 골든이 고정한다.** 예: `statements._pick` 이 누적값 0 을 `or` 로 당기값으로 바꾸는 것(DR:`dartreport/statements.py`:116), 요약 '생성 시각'의 벽시계(`excel.py`:107 — kbj 는 시각을 주입받되 골든은 자리표시로 정규화). 고치려면 새 골든 세트 + ADR | 이식과 수정을 한 커밋에 섞으면 골든이 무의미해진다 |
| D-P4-9 | **컨센서스 출처 = KIS**(로그인): 종목추정실적(연간 추정 손익·EPS) + 종목투자의견(증권사별 의견·목표가). TR 은 레포 안에서도 서로 어긋난다 — `kbj/data/private/kis/datasets.py`:214 는 "투자의견 FHKST668300C0·추정실적 FHKST663300C0", `docs/conflict_map.md` §1.13 은 그 반대, 가짜 서버(`tests/fakes/kis_server.py`:76)는 FHKST663300C0 을 `estimate-perform` 경로에 붙였다. 이 설계의 기본값은 **추정실적 `HHKST668300C0`(`/uapi/domestic-stock/v1/quotations/estimate-perform`), 투자의견 `FHKST663300C0`(`…/invest-opinion`), 증권사별 투자의견 `FHKST663400C0`(`…/invest-opbysec`)** — 기억에 기댄 값이라 전부 **[확인 필요]**, 실측(#29) 뒤 M 이 카탈로그를 고친다. **목표가 컨센서스는 증권사별 의견에서 우리가 계산**한다(§4.7) | U4: 네이버(main.naver·tb_type1)·FnGuide 스크래핑 금지. DATA_TIERS: 컨센서스는 로그인·합성 fixture |
| D-P4-10 | **컨센서스 교체로 잃는 것을 지어내지 않는다**: 분기 컨센서스(→ 분기 어닝 서프라이즈), 컨센서스 최고·최저(→ 시나리오 Bull EPS), 애널리스트 수(추정실적 쪽), 12개월 선행 EPS 직접값은 KIS 에 없을 가능성이 높다 [확인 필요]. 없으면 화면은 '출처 없음'이고, 서프라이즈는 **전년동기·직전분기 대비**(DART 만 — SD `_process_yoy_only`:411 승격)로 대신한다. 옛 SD SQLite 의 네이버 컨센서스·밴드·서프라이즈 행은 **이관하지 않는다**(§3.8) | 절대 규칙 2·3, conflict_map §1.13 "확인 전에는 '출처 미확보'로 비워 둔다" |
| D-P4-11 | **밸류에이션은 시점 일관(PIT)**: 날짜 t 의 TTM 은 **t 까지 공시된**(정기보고서 접수일 ≤ t) 마지막 4개 분기만 쓴다. 시가총액은 **회사 기준**(상장된 보통주 + 우선주, 같은 날 종가 × 상장주식수). PER 분모는 **지배주주 순이익**(없으면 당기순이익 + `estimated`). 밴드는 5년 일별 PIT 배수의 백분위. **오버행 가치를 EV 에 더하지 않는다** — SD `valuation_calculator.calculate_ev_ebitda_band`:194 는 CB 를 차입금(순차입금)과 오버행 양쪽에 넣어 이중 계산하고, 오늘의 오버행을 5년 전 분기에도 더해 미래 참조가 된다. 결과가 legacy 와 다르다(ADR 0016) | 미래 참조·이중 계산 제거. 우선주 페이지도 회사 배수로 같은 값 |
| D-P4-12 | **'TAM' = 시나리오 적정가(Bear/Base/Bull)** 로 이름을 바꾼다. SD `tam_modeler.py` 는 시장 규모(Total Addressable Market)가 아니라 EPS × PER 목표가 시나리오다(머리말 "EPS × PER → TP 자동 산출"). 피어 평균(밸류체인 세그먼트 — SD `valuechain`)은 분류 사전이 P5 라 P4 에서는 5년 밴드 P75 로 대신하고, 컨센서스 최고값이 없으면 Base × 1.2(legacy 폴백 그대로). 진짜 시장 규모 TAM 은 만들지 않는다 **[결정 필요 — 기본값: 이름 바꿈]** | PLAN §4 페이지 8 "TAM" 의 출처가 이 모듈. 같은 단어로 다른 것을 만들지 않는다 |
| D-P4-13 | **관심종목 원천 = `prv_journal.watchlist`**(로그인·개인 — 0013). P4 는 **읽기 API + 가져오기 CLI**(`python -m kbj.services.watchlist import --from sd-json <파일>` · `--from csv`)만 만든다 — SD `cache/server_watchlist.json`(SD `api_watchlist_sync`:5821 이 브라우저 localStorage 를 받아 쓴 파일)을 사용자가 VM 에 올려 넣는다. 쓰기 API(별 토글)는 P8(D-P3 §0.2 '쓰기 API — P8'). **공개판은 관심종목을 모른다** | 관심종목은 개인 데이터(DATA_TIERS §2 11~13 과 같은 등급). 레포·공개 JSON 에 종목 목록이 남지 않게 |
| D-P4-14 | **알림**: 실시간 push 는 **`alert.earnings` 하나** — 관심종목의 잠정실적 중 우선순위 ≤ 2(§4.2), 대상(subject) = `<corp_code>:<회계기간>:<연결/별도>`(정정공시가 다시 울리지 않게 — 접수번호가 아님). 일반 공시는 push 하지 않는다(SD `poll_dart_disclosures`:10166 의 "사용자 요청으로 텔레 발송 차단"을 승계). `alert.revision` 은 관심종목 리비전 **하루 1통 묶음**. 공시 요약은 P5 아침 브리핑(U2 — 하루 1회) 몫 | ADR 0005(쿨다운·중복 키), U2 |
| D-P4-15 | **페이지 8 = 혼합 등급**: 공개 = DART 재무·dart-report 블록·공시·오버행(주식 수·비율)·내부자(주식 수)·잠정실적·TradingView 차트 / 로그인 = 시세·차트·수급·컨센서스·리비전·밸류에이션·시나리오·오버행 금액·ITM·내부자 금액. `web/src/app/pages.ts` 의 8번 `tier: 'login'` → `'mixed'`. **페이지 5 의 DART 패널도 P4** 가 채운다(등록부 `phase: 'P4'`) — 경제 캘린더·뉴스는 P5 | DATA_TIERS §2 8·5행 |
| D-P4-16 | **API 섹션 격리**: 종목 상세는 섹션마다 라우트 하나(§5), 응답은 P3 `Envelope[T]`. 세 가지를 구분한다 — `200` + `data.applicable=false`·`na_reason`(적용 불가: 스팩 PER·금융업 EV/EBITDA·적자 PER), `404 no_data`(아직 수집 안 됨 — 작업 이름·예정 시각), `404 unknown_code`. **500 은 언제나 실패**(완료 시험이 0 을 단언). API 는 외부를 부르지 않는다(P3 계약 ⑩) → 상세 수집 대상이 아닌 종목은 "수집 대상 아님 — 관심종목에 넣으면 다음 수집(시각)"으로 보인다 | 완료 기준 "20개에서 오류 없이"를 시험 가능한 정의로 |
| D-P4-17 | **공개 내보내기**: 공시 피드·잠정실적·오버행·내부자·실적 일정 JSON + **상장사 전체 `fin/<종목코드>.json`**(DART 만 — 분기·연간·비율·오버행·내부자 요약·최근 공시) + `corp_index.json`. 크기 상한 합계 25 MB [제안], 넘으면 실패(조용히 자르지 않음). 엑셀(xlsx)은 `config/reports.yaml` 의 **공개 데모 목록**만 [제안] | DATA_TIERS §2 8행 "공개: DART 재무·부문 매출·공시". P3 계약 ⑧ 로 내보내기는 `pub_*` 만 읽는다 → 파생값은 작업이 `pub_*` 에 미리 쓴다 |
| D-P4-18 | **보호예수(의무보유)**: 레포 어디에도 구현·출처가 없다(`grep 보호예수` — PLAN 한 줄뿐). KRX KIND 스크랩은 금지(`krx_scrape` 그룹). P4 는 **법정 전매제한에서 계산한 '추정 해제일'만**(제3자배정 유상증자 신주 상장일 + 1년 [확인 필요: 규정]) `quality=estimated` 로 보이고, 예탁결제원 출처는 [확인 필요]·[결정 필요] | 지어내지 않는다. 출처를 찾으면 데이터셋을 더한다 |
| D-P4-19 | **SD `server.py` 함수 단위 삭제**(D-P3-18 이 P4 로 넘긴 것)와 SD 컨센서스·어닝·오버행·밸류·TAM·DART 수집 모듈 삭제를 P4 묶음 S 가 한다(§1.12). legacy dart-report 는 shim | 기준선은 줄어들기만(ADR 0007) |
| D-P4-20 | 신규 런타임 의존성 **`openpyxl`·`beautifulsoup4`**(파서는 표준 `html.parser` — DR 3곳 그대로, lxml 불필요). 백분위는 numpy(이미 pandas 경유로 lock 에 있음)를 쓰지 않고 순수 함수(선형 보간 — numpy 기본 'linear' 와 같은 정의)로 | 엑셀·주석 표 파싱. 엔진 순수성(P3 계약 ⑨) |
| D-P4-21 | 기술적 지표는 **SMA 20·60·120 만**(차트 겹쳐 보기). RSI·MACD·볼린저·볼륨 프로파일은 지표 정본 `kbj.core.indicators`(P8 — ADR 0001 Q3) 이후 | 지표 두 벌 방지 |
| D-P4-22 | **부문 매출**은 사업보고서 원문 표 best-effort(SD `_try_dart_segment_revenue`:10454 승격, `quality=estimated`), 실패는 '표 없음'. 시간이 모자라면 P5 로 미룬다 [제안] | 회사마다 서식이 달라 실패가 잦다(SD 머리말) |

### 0.2 만드는 것 / 만들지 않는 것 (P4 경계)

| 만든다 (P4) | 만들지 않는다 |
|---|---|
| DART 공시 피드(증분·중요도·종류), 잠정실적 파싱·실적 일정, 오버행(CB·BW·EB·유상증자 + 전환가 조정·청구 행사), 내부자(임원·주요주주 소유보고·5% 대량보유), 자기주식 결정, 기업개황(결산월·업종) | 증권사 리포트 목록·종목토론실 감성(네이버 — 대체 없음, 폐지: conflict_map §1.13), 뉴스(P5), 경제 캘린더(P5) |
| 재무 2층 수집·분기 환산·DART 비율(공개), 5년 백필 CLI | 전 상장사 전체 재무제표(상세 대상만), 비12월 결산 외 회계기준 변경·사업결합 보정(표시만) |
| dart-report 승격: 엔진·엑셀 빌더·파이프라인·작업, 골든 G1·G2(합성), G3(실데이터 — 키 뒤) | Streamlit UI(폐지), 엑셀 양식 변경 |
| 컨센서스(KIS 추정실적·투자의견), 목표가 컨센서스·리비전 계산, 관심종목 리비전 알림 | 분기 컨센서스·서프라이즈(출처 없음 — §4.7), 미국 종목 컨센서스(yfinance — 국장 범위 밖) |
| 밸류에이션 PIT 일별 시계열·5년 밴드·현재 백분위, 시나리오 적정가(구 TAM) | 피어 평균 PER(P5 분류 사전 뒤), 검증 시트·갭 분석·분석 일지(P8), 피어 표(P5) |
| 페이지 8(종목 상세 — 검색·관심종목 칩·탭 5개) + 페이지 5 의 DART 패널, 공개판 page 8(재무·공시) | 수출 겹쳐 보기(P6), RSI 등 지표 겹쳐 보기(P8), 쓰기 API(P8) |
| 관심종목 표·읽기 API·가져오기 CLI, 합성 20종목 세계(p4_world) | 관심종목 편집 화면(P8) |
| `alert.earnings`(관심종목 잠정실적)·`alert.revision`(일 1통), 알림 후 3·5·7거래일 성과 기록 | 일반 공시 push, 마감 요약(P5 — U2 결정) |
| SD `server.py` 함수 단위 삭제·SD P4 모듈 삭제·dart-report shim·기준선 축소 | monitor/kr 재무 카드(P5 에 monitor/kr 를 다시 만들 때 `pub_fin` 으로) |

### 0.3 완료 기준과 증명

| PLAN §8 P4 완료 기준 | 증명 | 묶음 |
|---|---|---|
| **dart-report 엑셀이 이식 전과 같다(골든)** | `tests/golden/dart_report/test_builder_golden.py`(G1 — 합성 입력 8벌을 legacy `build_workbook` 으로 만든 정규화 JSON 과 kbj `kbj.reports.dart_excel.excel.build_workbook` 결과 비교), `test_pipeline_golden.py`(G2 — 합성 DART 응답 2개 회사를 legacy `run.main` 전 과정(재무제표→주석 비용→브릿지→수주→엑셀)과 kbj 파이프라인에 같이 흘려 비교), 수식 평가 오류 0, 차트 XML 같음. 실데이터 G3 은 키 뒤(#30 — 공개 등급이라 실응답 fixture 커밋 가능) | R |
| (같은 기준 — legacy 스모크) | `scripts/test_legacy.sh dart-report` 의 `built. quarters= 14 annual= 4 bridge steps= 9` 가 shim 뒤에도 같다 | R |
| **종목 상세가 관심 종목 20개에서 오류 없이 나온다** | `tests/acceptance/test_stock_detail_watchlist20.py`: 합성 세계 `p4_world`(20종목 — §8.4 표: 신규상장·거래정지·우선주·스팩·리츠·비12월 결산·적자·재무 미제출·KONEX 등)에 P4 실제 처리기(가짜 DART·KIS, 가짜 시계)를 돌린 뒤 **페이지 8 의 모든 라우트 × 20종목**: 500 = 0, 봉투 검증 통과, 각 섹션 상태가 기대 행렬(`tests/fixtures/synthetic/p4_expect.yaml`)과 같다(200·`na_reason`·404 `no_data`). 프런트 `web/test/acceptance/p8_watchlist20.test.ts`: 그 응답으로 20종목 렌더 — `console.error` 0, DOM 에 `NaN`·`undefined`·`Infinity`·`null` 문자열 0, 패널 하나 실패가 다른 패널을 멈추지 않음 | S(+A·W2) |
| (같은 기준 — 실데이터) | 키를 받은 뒤 `python -m kbj.services.engine.verify_stock --watchlist main` 이 실 DB 로 같은 단언을 돌려 `docs/probe_results.md` #37 에 결과(종목 수·섹션 상태 분포 — 값은 적지 않음) | S |
| 백엔드 ruff·pyright·pytest·lint-imports·check_canonical(그룹 `dart` 0)·check_public_safety, 프런트 eslint·tsc·vitest, CI 녹색 | §8.7 | S |

### 0.4 등록부(`config/jobs.yaml`)에서 바뀌는 것

| 작업 | 지금 | P4 뒤 |
|---|---|---|
| `filings.dart_feed` | P4, 꺼짐, `when: trading_day`, `collects [list@minute, document@event]`, `writes [pub_filings.disclosure, pub_filings.overhang]`, `notify alert.earnings`, absorbs 에 `SD:earnings_pipeline_5min` | **켜짐**, `when: always`(평일 cron — DART 는 거래소 휴장일(12/31·근로자의 날)에도 접수 [확인 필요]), `collects` + 구조화 데이터셋 9개(§3.3) `@ event`, `writes` §3.2, **`notify` 지움**(알림은 `earnings.alerts`), absorbs 에서 `SD:earnings_pipeline_5min` 을 `earnings.alerts` 로 옮김 |
| `filings.company_profile` | P4, `writes [pub_filings.corp_code]` | **켜짐**, `writes [pub_filings.corp_profile]`(corp_code 표는 `filings.corp_code` 가 매일 갈아 넣는다 — 두 작업이 한 표를 쓰지 않게) |
| `fin.quarterly` | P4, `cron "30 7 * * *"`, `triggered_by [filings.dart_feed]`, collects `fnlttSinglAcntAll@quarter`·`fnlttMultiAcnt@quarter` | **켜짐**, `cron "*/30 7-21 * * *"`, `when: always`, `triggered_by` 지움(D-P4-4), collects `DART:fnlttMultiAcnt @ run_date`·`DART:fnlttSinglAcntAll @ event`(`<corp>:<연도>:<보고서>:<CFS/OFS>`), writes `pub_fin.statement_raw`·`quarterly`·`ratio` |
| `fin.backfill` | (없음) | **신규·켜짐(수동)**: `manual`, `backfill_of [fin.quarterly]`, 예산 `dart.backfill_cap`(§3.5) |
| `reports.dart_excel` | P4, owner `kbj.reports.dart_excel:run`, `cron "0 9 16 2,5,8,11 *"` | **켜짐**, owner `kbj.reports.dart_excel.job:run`, `cron "0 21 * * *"`(대상 = 관심종목 ∪ 공개 데모 중 재무가 바뀐 종목) + 기존 분기 cron 은 지움(같은 작업 하나 — 매일 21:00 이 덮는다), collects `DART:report_inputs @ event`(`<종목>:<실행일>`), writes `pub_fin.report` |
| `consensus.snapshot` | P4, collects `KIS:consensus_estimate`, notify `alert.revision` | **켜짐**, collects `KIS:consensus_estimate`·**`KIS:invest_opinion`**(신규) `@ trade_date`, writes `prv_fin.consensus_snapshot`·`invest_opinion`·`target_consensus`·`revision`, notify 그대로, `cron "0 16 * * 1-5"`(ADR 0018 — 리비전 알림 16:00) |
| `fin.valuation_band` | P4, owner `kbj.engines.valuation:band`, `cron "40 18 * * 1-5"`, `depends_on market.close_collect` | **켜짐**, owner `kbj.services.engine.valuation:band`(D-P3-5 — 엔진은 순수), **`cron "50 8 * * 1-5"`**, `depends_on [krx.daily(hard), fin.quarterly(soft)]`(전 거래일 KRX 확정 시총 — `ok`), writes `prv_fin.valuation_daily`·`valuation_band`·`scenario`. retired 의 `SD:mac-crontab:tam_modeler` 를 absorbs 로 옮김(D-P4-12) |
| `earnings.alerts` | (없음) | **신규·켜짐**: `cron "*/5 7-20 * * 1-5"`, `when: always`, owner `kbj.services.engine.earnings:alerts`, writes `prv_fin.earnings_surprise`, notify `alert.earnings`, absorbs `SD:earnings_pipeline_5min` |
| `earnings.backfill` | P4, owner `kbj.engines.earnings:backfill`, `30 6 * * *` | **켜짐**, owner `kbj.services.engine.earnings:backfill`, `when: trading_day`(알림 후 3·5·7 **거래일** 수익률 — SD 는 달력일) |
| `filings.derive` | (없음) | **신규·켜짐**: `cron "10,40 7-20 * * 1-5"` + `"30 21 * * *"`, owner `kbj.services.engine.filings:derive`, writes `pub_filings.overhang_state`·`overhang_summary`·`insider_window`·`earnings_schedule` |
| `public.export` | P3 | 그대로(파일 목록만 늘어남 — §7) |

등록부 검증의 "단계별 켜진 작업 목록" 단언에 P4 10개를 더한다(M). `absorbs` 는 `tests/unit/scheduler/legacy_jobs.txt` 의 줄마다 정확히 한 번 — `SD:earnings_pipeline_5min`(→ earnings.alerts), `SD:mac-crontab:tam_modeler`(retired → fin.valuation_band)를 옮긴다.

---

## 1. 모듈 묶음·웨이브·파일 소유

### 1.0 웨이브

| 웨이브 | 묶음(동시) | 시작 조건 | 예상 |
|---|---|---|---|
| **1** | **M**(DB·저장소·자료형·설정·카탈로그·등록부·지표 문서·합성 세계), **W**(프런트 셸 확장·부품) | P3 묶음 S 커밋(D-P4-1) | 1.5일 |
| **2** | **F**(DART 수집·파서), **E**(공시 엔진·알림 작업), **R**(재무·dart-report 승격·골든), **K**(컨센서스), **V**(밸류에이션·시나리오), **A**(API·관심종목 CLI), **X**(공개 내보내기) | M 의 `kbj/core/fin_rows.py`·저장소·마이그레이션·`p4_world`·설정. 서로는 이 문서의 시그니처(§4·§5)로 맞춘다 — 엔진이 늦으면 readers 시험은 메모리 가짜로 먼저 | 3일 |
| **3** | **W2**(페이지 5·8 위젯·타입 생성), **S**(완료 시험·시뮬레이션·CI·legacy 정리·enabled 뒤집기·계약·문서) | W2: W + A 의 `openapi.json`·픽스처. S: 웨이브 2 전부 | 2일 |

합계 약 6.5일(PLAN 5~7일). 묶음 11개. 묶음마다 빌드 에이전트 + 검증 에이전트(§1.13 완료 명령으로 검증).

### 1.1 M — DB·저장소·자료형·설정·카탈로그 (웨이브 1)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/core/fin_rows.py`(신규 — P3 `rows.py` 는 건드리지 않는다) | SD 표 정의(`migrations/004·005`, `overhang_parser` INSERT:322, `earnings_parser.save_earnings_actual`:284), DR `statements.Period`:90 | frozen dataclass: `DisclosureRow`, `CorpProfile(corp_code, stock_code, acc_mt, induty_code, corp_cls, est_dt)`, `PrelimRow`, `ScheduleRow`, `OverhangEvent`, `OverhangAdjust`, `OverhangState`, `OverhangSummary`, `InsiderReport`, `MajorHolderReport`, `InsiderWindow`, `BuybackEvent`, `ShareCount`, `StatementRaw`, `FiscalPeriod(year, q, start, end)`, `QuarterRow`, `RatioRow`, `ReportRow`, `SegmentRow`, `ConsensusRow`, `OpinionRow`, `TargetConsensus`, `RevisionRow`, `ValuationDay`, `BandRow`, `ScenarioRow`, `SurpriseRow`, `WatchItem`, `ShareClass` · StrEnum `Instrument`(CB·BW·EB·PAID_IN·BONUS), `PrelimScope`(CONSOLIDATED·STANDALONE·SUBSIDIARY), `NaReason` | `tests/unit/core/test_fin_rows.py`(frozen, 금액 int 원, naive 시각 거부, 기간 검증) |
| `kbj/store/migrations/0010_filings.sql`·`0011_fin.sql`·`0012_fin_prv.sql`·`0013_journal_watchlist.sql` | §3.4 | §3.4 | `tests/test_store_layout.py` 확장, 통합 `tests/integration/test_migrations_pg.py`(0001~0013 두 번, `kbj_public_export` 가 0010·0011 은 읽고 0012·0013 은 못 읽음) |
| `kbj/store/repos/{filings,fin,consensus,journal}.py`(신규), `__init__.py`·`memory.py`(추가) | — | Protocol + `Pg*Repo` + 메모리. 예: `FilingsRepo.upsert_disclosures(rows) -> int`, `.seen(rcept_nos) -> set[str]`, `.pending(kind, limit) -> list[DisclosureRow]`, `.mark_parsed(rcept_no, state, note)`, `.disclosures(code \| None, start, end, kinds=None)`, `.prelims(...)`, `.overhang_events(corp)`, `.put_overhang_state(...)`, `.insider_reports(corp, start, end)`; `FinRepo.put_raw(StatementRaw)`, `.raw(corp, year, reprt, fs_div)`, `.upsert_quarters(rows)`, `.quarters(corp, upto_rcept: date \| None = None)`(PIT 읽기), `.put_report(ReportRow)`, `.report(code)`; `ConsensusRepo.put_snapshot(...)`, `.snapshots(code, start, end)`, `.opinions(code, start, end)`, `.put_valuation_days(rows)`, `.valuation_days(code, start, end)`, `.band(code, metric)`; `JournalRepo.watchlist(list_name='main') -> list[WatchItem]`, `.replace_watchlist(list_name, items, *, source)` | `tests/unit/store/repo_cases_p4.py` + `test_repos_p4_memory.py`, 통합 `tests/integration/test_repos_p4_pg.py`(같은 묶음을 Pg 로) |
| `kbj/store/legacy_import/{mappings,transforms}.py`(추가) | SD `server.py:_load_server_watchlist`:5837 | `MAPPINGS` 에 `watchlist`(SD `cache/server_watchlist.json` → `prv_journal.watchlist`), `LATER` 의 P4 세 줄을 "다시 받는다(DART)·이관 안 함(네이버 출처)"로 바꿈(§3.8) | `tests/unit/store/test_legacy_import*.py` 확장 |
| `kbj/data/public/dart/datasets.py`(추가) | §3.3 | 데이터셋 13개 추가·2개 변경 | `tests/unit/data/public/test_public_datasets.py` 확장 |
| `kbj/data/private/kis/datasets.py`(변경) | D-P4-9 | `consensus_estimate`(TR·노트 수정), `invest_opinion`(신규) | `tests/unit/kis/test_kis_datasets.py` |
| `kbj/config/fin.py`(신규) + `config/fin.yaml`·`config/reports.yaml`(신규) | §3.7 (`kbj/config/markets.py` 방식 — 모르는 키 오류) | `load_fin() -> FinConfig`, `load_reports() -> ReportsConfig` | `tests/unit/config/test_fin_config.py` |
| `config/jobs.yaml`(웨이브 1 — P4 행 확정, **enabled: false**), `config/notify.yaml`(`alert.earnings`·`alert.revision` 노트·subject 규칙), `config/limits.yaml`(`dart.backfill_cap: 8000` [제안]) | §0.4 | — | 등록부 검증·`python -m kbj.services.scheduler validate` |
| `pyproject.toml`·`uv.lock` | D-P4-20 | 런타임 `openpyxl>=3.1`·`beautifulsoup4>=4.12`(legacy 그룹과 같은 하한) | `tests/test_import_contracts.py` 의 `RUNTIME_IMPORTS` 에 `openpyxl`·`bs4` |
| `docs/metrics.md` §9~§15(부록 A 를 옮김), `docs/secrets.md`(새 이름 없음 — `STOCKS`·`DART_STOCKS` 행의 대체를 `config/reports.yaml` 로 확정) | CLAUDE §4 "먼저 쓰고 구현" | — | 문서 |
| `tests/fixtures/synthetic/p4_world.py`, `tests/fixtures/synthetic/p4_expect.yaml` | ET `board/tests/demo.py` 방식(고정 시드) | `build_world(seed=20261007) -> P4World`(20종목 §8.4, DART 응답(공시 목록·구조화·원문 HTML·재무제표·기업개황·주식총수)·KIS 컨센서스·KRX 일별·관심종목) | `tests/fixtures/test_p4_world.py`(결정성, 20종목 모두 `_source: SYNTHETIC`, 코드 형식) |

### 1.2 W — 프런트 셸 확장·부품 (웨이브 1)

| 경로 | 내용 | 시험 |
|---|---|---|
| `web/src/app/pager.ts` | 해시 `#/p/<n>(/<code>)?` — `HASH_RE`(:28) 확장, 종목 코드는 `^[0-9A-Z]{6}$` 만. 종목을 바꾸면 `replaceState`(P3 규칙 그대로) | `web/test/pager.test.ts` 확장 |
| `web/src/app/route.ts`(신규) | 선택 종목 상태(`getCode()`·`onCode(fn)`), 다른 페이지(4 수급 표 행·5 공시 행)에서 `openStock(code)` → `#/p/8/<code>` | `route.test.ts` |
| `web/src/app/pages.ts` | 8번 `tier: 'mixed'`·`plan` 갱신, 5번 `tier: 'mixed'`(경제 캘린더 P5 자리) | `pager.test.ts` |
| `web/src/ui/{search,tabs,band,waterfall,stackbar,timeline,kv}.ts`(신규) | 종목 검색 상자(코드·이름, 키보드), 탭, 밸류 밴드(가격선 + 배수 밴드 5줄), 워터폴(브릿지), 누적 막대(비용 구성 100%), 타임라인(오버행 청구기간), 키-값 표 | `web/test/ui_p4.test.ts`(빈 배열·음수·전부 null 에도 예외·`NaN` 없음) |
| `web/src/ui/lock.ts` | 자물쇠 사유 추가: `consensus`("컨센서스 — 제3자 유료·재배포 금지"), `valuation`("시세 기반") | `ui.test.ts` |
| `web/src/design/components.css` | 위 부품 스타일(스킨 토큰만 — 새 색 없음) | `tokens.test.ts` 그대로 |

### 1.3 F — DART 수집·파서 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/data/public/dart/feed.py` | SD `poll_dart_disclosures`:10166(페이징 10쪽 — 매번 처음부터), ET `board/ingest/dart.py:disclosures`:123 | `scan_new(client, day: date, seen: Callable[[Iterable[str]], set[str]], *, max_pages=30) -> FeedScan(items, pages, stopped_at, complete: bool)` — `list_disclosures`(P2) 를 쪽마다 불러 **이미 본 접수번호가 한 쪽에 나오면 멈춘다**(정렬 기본 = 접수 내림차순 [확인 필요 #28]) · `is_correction(title) -> bool`(`[기재정정]`·`[첨부정정]` 등) · `base_title(title) -> str` | 새로: 가짜 DART(쪽 경계·정정·같은 분 2회) |
| `kbj/data/public/dart/disclosures.py`(변경) | P2 승격본 `KINDS`:68 | `KINDS` 에 `prelim`(잠정실적 — `earnings` 앞), `overhang_exercise`(전환청구권행사·신주인수권행사·교환청구권행사), `price_adjust`(전환가액의조정·행사가액조정), `insider`(임원ㆍ주요주주특정증권등소유상황보고서), `major_holder`(주식등의대량보유상황보고서), `periodic`(사업·반기·분기보고서), `pre_announce`(결산실적공시예고) — **기존 반환 dict 키는 그대로**(legacy 가 다시 내보내 씀) | P2 `test_dart_for.py` 그대로 + 종류 표 |
| `kbj/data/public/dart/structured.py`(신규) | SD `overhang_parser.OVERHANG_REPORT_PATTERNS`:46 (제목 분류만) | `ENDPOINT_OF: Mapping[Instrument \| str, str]` — `cvbdIsDecsn`(CB)·`bdwtIsDecsn`(BW)·`exbdIsDecsn`(EB)·`piicDecsn`(유상)·`fricDecsn`(무상)·`tsstkAqDecsn`(자기주식 취득)·`tsstkDpDecsn`(처분) **[확인 필요 #31: 이름·파라미터(corp_code·bgn_de·end_de)·필드]** · `fetch_decision(client, corp_code, day, rcept_no, instrument) -> OverhangEvent \| BuybackEvent \| None`(같은 접수번호 행을 고른다) · `parse_cb(row)`·`parse_bw`·`parse_eb`·`parse_paid_in`·`parse_buyback` | 새로: 합성 응답(필드 이름은 모듈 상수 하나에 모아 실측 뒤 한 곳만 고침) |
| `kbj/data/public/dart/ownership.py`(신규) | — (KR 내부자 구현 없음 — SD 는 미국 yfinance 비중뿐 :8745) | `fetch_insider(client, corp_code) -> list[InsiderReport]`(`elestock.json` [확인 필요 #32]), `fetch_major(client, corp_code) -> list[MajorHolderReport]`(`majorstock.json`) — 응답은 회사의 보고 전체라 **새 접수번호만** 남긴다 · 보고자 이름은 저장하되 로그·알림에는 싣지 않는다 [제안] | 새로 |
| `kbj/data/public/dart/prelim_doc.py`(신규) | SD `earnings_parser.py`: `EARNINGS_TYPES`:50, `classify_disclosure_type`:58, `_detect_unit_multiplier`:133, `detect_scope_from_body`:155, `parse_preliminary_earnings`:181(전년동기 = 5번째 열, '실적등에대한전망' 제외) | `classify(title) -> PrelimScope \| Literal['PRE_ANNOUNCE'] \| None`, `parse(html) -> PrelimParse`(당기·전기(직전분기)·전년동기 **세 열을 다 남긴다** — SD 는 전기 열을 버렸다), 단위는 **원**으로(SD 는 백만원) · `parse_pre_announce(html) -> date \| None`(예고일 [확인 필요]) · `infer_period(rcept_dt, acc_mt, body) -> FiscalPeriod, inferred: bool` — 본문 기간 표기를 먼저, 없으면 결산월 기준 직전 분기(SD `_infer_quarter`:262 는 달력 분기 고정 — 비12월 결산에서 틀림) | 새로(SD 원본 시험 0): 합성 HTML(단위 억원·천원, 5열 폼·2열 폼, 연결/별도, 적자 '-'), 비12월 결산 기간 |
| `kbj/data/public/dart/overhang_doc.py`(신규) | SD `overhang_parser.py`: `_parse_amount`:176, `_parse_date`:186, `parse_cb_bw_content`:198, `parse_paid_in_content`:242 | 구조화 응답이 빈 칸일 때만 쓰는 폴백 `parse_cb_bw(html)`, `parse_paid_in(html)` + 신규 `parse_exercise(html) -> OverhangAdjust`(청구 금액·발행 주식 수 [확인 필요]), `parse_price_adjust(html) -> OverhangAdjust`(조정 후 전환가) | 새로 |
| `kbj/data/public/dart/company.py`·`shares.py`(신규) | P2 `disclosures.company`(induty_code 만) | `fetch_profile(client, corp_code) -> CorpProfile`(`company.json` 의 `acc_mt`·`induty_code`·`corp_cls`·`est_dt` [확인 필요: acc_mt 형식]), `fetch_share_count(client, corp_code, year, reprt) -> ShareCount`(`stockTotqySttus.json` [확인 필요 #33]) | 새로 |
| `kbj/services/collectors/dart_feed.py` | SD `poll_dart_disclosures`:10166, `earnings_parser.process_disclosure`:396, `overhang_parser.collect_overhang_for_stock`:266 | `run(ctx) -> JobResult`(`filings.dart_feed`): ① 그날 `scan_new` → `pub_filings.disclosure` upsert(키워드 점수는 E 의 `importance.keyword_score` — **F 는 E 를 import 하지 않고**, 점수는 `filings.derive` 가 채운다. F 는 `kind` 만) ② 07:00 첫 실행은 전 영업일 전체를 한 번 더(19:59 뒤 접수분 — §3.2) ③ 새 행 중 `prelim`·`pre_announce`·`overhang_*`·`price_adjust`·`insider`·`major_holder`·`buyback` 은 접수번호마다 `DART:<dataset> @ event` 선점 → 구조화 API 또는 원문 → 각 표, `parse_state` 기록 ④ 한 건 실패는 그 건만 `failed`(다음 실행이 `retry_parse_max` 3회까지) — 다른 건을 멈추지 않는다 | `tests/unit/collectors/test_dart_feed.py`(증분 멈춤, 정정, 선점 중복 0, 한 건 실패 격리, 020 이면 그날 중단·`failed` 사유) |
| `kbj/services/collectors/dart_company.py` | ET `run.py:_dart_ksic`:837 | `run(ctx)`(`filings.company_profile`) — 상장사(corp_code 표 `stock_code` 있음)마다 `company.json`, 주 1회 | 새로 |
| `tests/fakes/dart_server.py`(확장) | P2 승격본 | `p4_world` 응답 서빙(구조화 엔드포인트·원문 zip·`elestock`·`majorstock`·`stockTotqySttus`·`company`) + `inject` | `tests/unit/fakes/test_dart_server_p4.py` |
| `tests/fixtures/synthetic/dart/**` | — | 합성 공시 원문 HTML(잠정실적 폼 2종·CB·BW·유상·전환청구·전환가 조정·결산실적공시예고) — `make_dart.py` 가 `p4_world` 에서 생성, `_source: SYNTHETIC` | `test_dart_fixtures.py`(생성기와 같음) |

### 1.4 E — 공시 엔진·알림 작업 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/engines/filings/importance.py` | SD `_DISCLOSURE_SCORE_TABLE`:9920, `score_disclosure`:9974, `_cap_bonus_for_code`:9946 | `keyword_score(title) -> KeywordScore(score, matched)`(**공개**), `login_score(kw, *, is_watch, mktcap) -> LoginScore(total, importance)`(관심 +3·시총 보너스 — **로그인**, 읽을 때 계산) · 표는 `config/fin.yaml filings.keywords` | 새로: 표 경계(10·6·4), 공백 무시 |
| `kbj/engines/filings/earnings.py` | SD `earnings_signal_classifier.py`: `THRESHOLDS`:166, `classify_signal`:172, `_process_yoy_only`:411, `sanity_check_consensus`:34(3관문) | `classify_yoy(cur, prev_y, prev_q, reported_yoy) -> EarningsSignal`(흑자전환·적자전환·적자축소·BIG·STANDARD·INLINE — 공개), `classify_surprise(actual, consensus) -> EarningsSignal`(분기 컨센서스가 있을 때만 — 로그인), `sanity(consensus, history, mktcap) -> Sanity` | 옮김 0(SD 시험 없음). 새로: legacy 분류 표 전부(경계 값 포함), 단위 원 |
| `kbj/engines/filings/overhang.py` | SD `get_active_overhang`:171(가치 합), `collect_overhang_for_stock`:266(상태·희석률) | `state_at(events, adjusts, t) -> list[OverhangState]`, `summary(states, shares: ShareCount, t) -> OverhangSummary`(§4.3), `lockup_estimates(events) -> list[LockupEstimate]`(D-P4-18) | 새로 + 속성 시험(잔여 ≥ 0, 전환가 조정 뒤 잠재 주식 = 잔여 ÷ 새 가격) |
| `kbj/engines/filings/insider.py` | — | `window(reports, t, days) -> InsiderWindow`(§4.4), `dedupe(reports) -> list`(정정 대체) | 새로 |
| `kbj/engines/filings/schedule.py` | SD `_fetch_kr_earnings`:11047(정기보고서 목록) | `statutory_deadlines(profile, today) -> list[ScheduleRow]`(분기·반기 45일, 사업 90일 [확인 필요: 법정 기한]), `from_pre_announce(...)` | 새로 |
| `kbj/services/engine/filings.py` | — | `derive(ctx)`(`filings.derive`): 그날 바뀐 회사의 오버행 상태·요약·내부자 창·실적 일정·키워드 점수를 `pub_filings.*` 에 — **공개 작업**(계약 ⑬ — 로그인 모듈 import 금지) | 새로 |
| `kbj/services/engine/earnings.py` | SD `earnings_telegram_sender.send_pending_alerts`:161·`backfill_alert_performance`:217, `earnings_alert_writer.build_fallback`:299(템플릿 — **LLM 은 쓰지 않는다**, Ollama 폐기) | `alerts(ctx)`(`earnings.alerts`): 처리 안 된 잠정실적 → 분류 → `prv_fin.earnings_surprise` → 관심종목·우선순위 ≤ 2 면 `notify(text, kind='alert.earnings', subject=…)`; `backfill(ctx)`(`earnings.backfill`): 알림 후 3·5·7 **거래일** 종가 수익률(원장 — krx > kis) | 새로: 같은 subject 두 번 → 한 번(정정), 관심종목 아님 → 기록만, 알림 본문에 계산값만(지어낸 수 없음 — 본문 숫자는 모두 입력에서) |
| `tests/unit/engines/filings/**`, `tests/unit/engine_jobs/test_{filings,earnings}.py`, `tests/property/test_overhang_props.py` | — | — | — |

### 1.5 R — 재무·dart-report 승격·골든 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `tests/golden/dart_report/`(`make_golden.py`, `inputs/`, `expected/*.json`, `META.json`) | DR `tests_smoke.py`(합성 Period·비용·브릿지·수주) | §4.6 | **먼저 커밋**(②보다 앞) |
| `tests/oracles/xlsx_normalize.py`·`formula_eval.py` | — | `normalize(path) -> dict`(D-P4-7), `evaluate(wb_dict) -> dict[cell, float \| str]`(`=SUM`·`IFERROR`·사칙·셀 참조만 — DR 수식 형태 12가지 전부) | 자기 시험 |
| `kbj/engines/financials/statements.py` | DR `statements.py` 전체 232줄: `EOK`:18, `TAGS`:21, `BS_KEYS`:67, `_norm`:71, `_to_num`:75, `Period`:90, `_pick`:105, `fetch_periods`:141, `to_quarterly`:166, `to_annual`:204 | 같은 이름·같은 반환. `fetch_periods(source: StatementSource, …)` — `StatementSource` 는 `financials(corp, year, reprt, fs_div) -> list[dict]` Protocol(`DartClient` 그대로 맞음 + `PgStatementCache`) | 옮김: 원본 시험 없음 → G1·G2 + 새 시험(누적 차분·OFS 폴백·직전 분기 없음 `derived`·1H/9M 라벨) |
| `kbj/engines/financials/costs.py`·`bridge.py`·`orders.py` | DR `costs.py` 394줄(`BUCKET_ORDER`:21·`find_periodic_reports`:65·`_unit_scale`:97·`_tables_with_title`:120·`_parse_cost_table`:145·`fetch_cost_notes`:212·`bucketize`:295·`cost_timeseries`:331·`annual_cost_structure`:364), `bridge.py` 160줄(`BRIDGE_ORDER`:24·`fetch_bridge_details`:60·`build_bridge`:101·`waterfall_layout`:145), `orders.py` 198줄(`CONTRACT_PATTERNS`:19·`FIELD_ALIASES`:25·`fetch_orders`:122·`orders_table`:170) | 같은 이름. 원문을 읽는 함수는 `DocumentSource`(`document_texts`·`disclosures`) Protocol 을 받는다(I/O 는 호출자 — 계약 ⑨). **엔진 안 `print`(verbose)는 `log` 인자로** | 같음 |
| `kbj/engines/financials/period.py`(신규) | — | `fiscal_period(bsns_year, reprt_code, acc_mt) -> FiscalPeriod` [확인 필요 #34: 비12월 결산의 `bsns_year` 뜻], `ttm_window(quarters, t) -> list[QuarterRow] \| None`(PIT — 공시일 ≤ t) | 새로: 3월·6월·9월 결산 |
| `kbj/engines/financials/normalize.py`(신규) | DR `to_quarterly` + ET `board/ingest/financials.py:build_corp`:83(3개월값 대조 — 값으로 쓰지 않음) | `quarter_rows(periods, profile, rcept_dt_of) -> list[QuarterRow]` — 값 = 차분(Q4), `reported_3m`·`diff3m_max_pct` 열, 1% 넘으면 `quality=estimated` + 사유 [확인 필요 — Q4 20종목 대조] · 지배순이익(`ifrs-full_ProfitLossAttributableToOwnersOfParent`)·감가상각(현금흐름표 조정 항목)·차입금 태그 추가 [확인 필요 #35: 태그] | 새로 |
| `kbj/engines/financials/ratios.py`(신규) | SD `dart_collector.calculate_derived_metrics`:362(EBITDA·순부채 — 기능 참고) | `ratios(quarters, shares) -> list[RatioRow]`(DART 만 — §4.5, **공개**) | 새로 |
| `kbj/engines/financials/segments.py`(신규) | SD `_try_dart_segment_revenue`:10454 | `parse_segments(html) -> list[SegmentRow]`(estimated) | 새로(합성 HTML) |
| `config/dart_report/mapping.yaml` | DR `config/mapping.yaml`(`git mv`, 145줄) | (데이터) — 숫자·동의어 한 곳 | 골든이 덮는다 |
| `kbj/reports/dart_excel/excel.py` | DR `excel.py` 606줄 전체(`_labels`:48 … `build_workbook`:588) | `build_workbook(path_or_buffer, *, meta, annual, annual_costs, quarters, qcosts, bridge_steps, bridge_label, orders, mapping, unmapped, warnings, generated_at: datetime) -> …`(벽시계 대신 주입) | G1 |
| `kbj/reports/dart_excel/pipeline.py` | DR `run.py:main`:52~216(순서·경고 문구 그대로), `_diagnose`:263·`_diagnose_bridge`:219 | `collect_inputs(client, stock, years, fs_div, mapping, *, now, log) -> ReportInputs` · `render(inputs, generated_at) -> (xlsx_bytes, payload)` — payload = 화면용 블록 JSON(분기 실적·연간 비용·브릿지·수주·분기 비용·경고) | G2 |
| `kbj/reports/dart_excel/job.py`·`__main__.py` | DR `run.py` CLI·`report.yml` | `run(ctx)`(`reports.dart_excel` — 대상 목록 = `config/reports.yaml`·관심종목, 재무가 바뀐 종목만) · `python -m kbj.reports.dart_excel --stock 006110 [--years A B] [--fs-div OFS] [--out x.xlsx] [--diagnose-costs \| --diagnose-bridge]`(옛 CLI 옵션 그대로) | `tests/unit/reports/test_job.py`·`test_cli.py` |
| `kbj/services/collectors/fin.py` | ET `board/ingest/financials.py:collect`:173·`fetch_batch`:165, SD `dart_collector.fetch_5y_quarterly`:298(대체) | `quarterly(ctx)`(`fin.quarterly`): ① 정기보고서 접수(`pub_filings.disclosure` kind=periodic, `fin_loaded_at` 없음) → 상세 대상이면 `fnlttSinglAcntAll`(CFS→OFS) `@ event`, 아니면 다음 07:30 `fnlttMultiAcnt` 묶음 ② `pub_fin.statement_raw` → `normalize` → `quarterly`·`ratio`. `backfill(ctx)`(`fin.backfill` — `--from-year`, 대상 층, 예산) | `tests/unit/collectors/test_fin.py`(OFS 폴백, 정정 보고서 다시 받기, 예산 닫힘) |
| legacy shim | DR `dartreport/{client(그대로),statements,costs,bridge,orders,excel}.py` → `from kbj… import *` + `__all__`, `run.py` 는 매핑 기본 경로만 `config/dart_report/mapping.yaml`, `app.py` 삭제, `tests_smoke.py` 그대로 통과 | — | `scripts/test_legacy.sh dart-report` 출력 같음 + 다리 시험 1(`dartreport.excel.build_workbook is kbj…build_workbook`) |

### 1.6 K — 컨센서스 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/data/private/kis/consensus.py`(신규) | SD `consensus_collector.py`·`consensus_quarterly_collector.py`(네이버 — **승격하지 않고 대체**), `revision_calculator.METRIC_COLUMN`:41 | `params_estimate(code)`·`parse_estimate(body, code) -> list[ConsensusRow]`(결산년월별 실적/추정 구분, 매출·영업익·순익·EPS·EBITDA [확인 필요 #29: output 이름·단위 억원?]), `params_opinion(code, start, end)`·`parse_opinion(body, code) -> list[OpinionRow]`(증권사·일자·의견·목표가) · 빈 응답 → `KisEmpty`(0 을 사실로 내보내지 않음 — P3 규칙) | 새로(합성 fixture) |
| `kbj/engines/consensus/{target,revision,forward}.py`(신규) | SD `revision_calculator.py`: `classify_signal`:76(±5·±15), `compute_revisions_for_stock`:169(기준선 = t−W 이전 최신 스냅), `_days_between`:252 | `target_consensus(opinions, t, window_days=90) -> TargetConsensus`, `revisions(snaps, t, windows=(7, 30)) -> list[RevisionRow]`, `classify(pct) -> (signal, priority)`, `ntm(rows, t) -> Ntm \| None`(§4.7) | 옮김: SD `_self_test`:296 의 분류 표를 시험으로. 새로: 기준선 없음, 기준값 0·음수 |
| `kbj/services/collectors/consensus.py` | SD `consensus_snapshot_collector.get_extended_universe`:45(시총 상위 + 밸류체인 350), `run_daily_snapshot`:233, `alert_revision_signals`(server.py:5948 — inventory #10) | `run(ctx)`(`consensus.snapshot`): 유니버스 = 관심종목 ∪ 전 거래일 시총 상위 `consensus.top_n`(300 [확인 필요]) 보통주 → TR 2개씩 → 표 4개 → 관심종목 리비전 묶음 `notify(kind='alert.revision')`(하루 1통 — `daily`) | 새로: 가짜 KIS, 호출 수 = 2 × 유니버스, 우선순위 P3 |
| `tests/fakes/kis_server.py`(확장) | P3 C 확장본 | TR 3개 합성 출력(`p4_world` 연동), 경로 표를 D-P4-9 기본값으로 바로잡는다 | `tests/unit/fakes/test_kis_server_p4.py` |
| `tests/fixtures/synthetic/kis/p4_consensus.json` + `make_p4.py` | P3 `make_p3.py` 방식 | — | 생성기와 같음 |

### 1.7 V — 밸류에이션·시나리오 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/engines/valuation/multiples.py` | SD `valuation_calculator.py`: `get_ttm_metrics`:82, `calculate_current_metrics`:283 | `company_mktcap(snaps_by_code, classes, day) -> Sourced[int] \| None`(보통주+우선주), `trailing(mktcap, ttm, balance) -> Multiples`, `forward(mktcap, ntm, ev) -> Multiples` — 분모 ≤ 0 이면 `None` + `NaReason` | 새로: 손계산 1건씩 |
| `kbj/engines/valuation/pit.py` | (SD 는 미래 참조 — D-P4-11) | `daily_series(bars_by_day, quarters, classes, start, end) -> list[ValuationDay]` — 날마다 그날까지 공시된 TTM | 속성: 미래 분기를 지우거나 바꿔도 t 이전 값 불변(미래 참조 0) |
| `kbj/engines/valuation/band.py`·`percentile.py` | SD `calculate_per_band`:122(분기 8개 이상, 0<PER<200), `percentile_rank`:334(조각 선형 — **바꿈**) | `percentile(xs, p)`(선형 보간), `band(series, metric, *, window_days=1240, min_days=250, caps) -> BandRow`, `rank_pct(xs, x) -> float`(경험적 — §4.8) | 새로: numpy `percentile(…, method='linear')` 와 같은 값(시험 안에서만 numpy 로 대조) |
| `kbj/engines/valuation/scenario.py` | SD `tam_modeler.py`: `calc_base_eps`:163, `calc_bear_eps`:184, `calc_bull_eps`:201, `calc_per_scenarios`:222, `calc_pbr_based_tp`:250, `build_auto_tam`:364, `build_auto_tam_with_label`:456 | `scenarios(eps_inputs, band, bps, price) -> ScenarioSet`(§4.9) | 옮김 0(SD 시험 없음) — legacy 폴백 순서 표를 시험으로 |
| `kbj/services/engine/valuation.py` | SD `save_valuation_band`:355·`calculate_all_bands`:448 | `band(ctx)`(`fin.valuation_band`): 전 거래일 행 추가(전 종목 — 주요계정 층), 밴드·시나리오는 상세 대상만 다시 · `rebuild(code)`(백필 뒤 5년 다시) | 새로: 메모리 저장소, 같은 as_of 재실행 멱등 |

### 1.8 A — API·관심종목 CLI (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/services/api/routes/{stock,filings,watchlist}.py`(신규) | SD 라우트 `/api/dart_financial/<code>`:10528, `/api/disclosures`:10273, `/api/revision/<code>`:18641, `/api/valuechain2/stock/<code>/tam`, `/api/financial/<code>`:3869(네이버 — 대체) | §5.3 | `tests/unit/api/test_routes_{stock,filings,watchlist}.py`(세션 없이 401, 봉투, 쿼리 422, `unknown_code`, `na_reason`) |
| `kbj/services/api/readers/{stock,filings,fin,watchlist}.py`(신규), `readers/_common.py`(`ApiRepos` 에 `filings`·`fin`·`consensus`·`journal`) | — | 저장소 → 엔진(순수) → 모델. 예: `stock_valuation(repos, code, now) -> Envelope[Valuation]` | 메모리 저장소 + `p4_world` |
| `kbj/services/api/models/{stock,filings,fin}.py`(신규) | P3 `models/common.py` | §5.2 | `test_models.py`(모든 응답에 source·as_of·quality) |
| `kbj/services/api/app.py`(라우터 등록만), `cache.py`(데이터 버전 키에 P4 데이터셋) | — | — | `test_app.py` |
| `kbj/services/watchlist/{__init__,__main__}.py`(신규) | SD `api_watchlist_sync`:5821(파일 모양 `[{code, name, …}]`) | `python -m kbj.services.watchlist import --from sd-json\|csv <파일> [--list main] [--dry-run]`, `show` — 코드 검증(`^[0-9A-Z]{6}$`), 모르는 코드는 거절 목록으로 출력(값만) | `tests/unit/watchlist/test_cli.py` |
| `web/src/api/openapi.json`, `web/test/fixtures/api/{stock,filings}/**` | — | A 가 `p4_world` 로 만든 합성 응답(20종목 전부) | `openapi --check` |

### 1.9 X — 공개 내보내기 (웨이브 2)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/services/public_export/{filings_json,fin_json,corp_index}.py`(신규) | §7 의 파일 — **`pub_filings`·`pub_fin` SQL 만**(P3 계약 ⑧) | `tests/unit/public_export/test_{filings,fin}_json.py` |
| `kbj/services/public_export/export.py`·`manifest.py`(등록·하위 폴더 `fin/`·크기 상한) | P3 X 모듈 | `test_export.py`·`test_manifest.py` 확장 |
| `web/scripts/merge-public-data.mjs` | `fin/` 하위 폴더·(선택) `reports/*.xlsx` 허용 목록 | `web/test/scripts.test.ts` |
| `web/test/fixtures/public/{filings_feed,earnings_prelim,overhang_recent,insider_recent,earnings_schedule,corp_index}.json`, `fin/Q00001.json` | 합성 | — |

### 1.10 W2 — 페이지 5·8 위젯 (웨이브 3)

| 경로 | 내용 |
|---|---|
| `web/src/pages/p8_stock.ts`, `web/src/pages/p8/{header,overview,financials,report,consensus,valuation,scenario,filings,overhang,insiders}.ts` | §6 |
| `web/src/pages/p5_filings.ts`, `web/src/pages/p5/{feed,prelim,overhang,insiders,schedule}.ts` | §6.6 |
| `web/src/api/types.gen.ts`(`npm run gen:types`) | A 의 openapi |
| `web/test/pages/p5_*.test.ts`·`p8_*.test.ts`, `web/test/acceptance/p8_watchlist20.test.ts` | §6.8 |

### 1.11 S — 완료 시험·시뮬레이션·CI·legacy 정리 (웨이브 3)

| 경로 | 내용 |
|---|---|
| `tests/acceptance/test_stock_detail_watchlist20.py`(+`conftest.py`) | §8.4 |
| `tests/sim/test_one_day_p4.py`(+`harness.py` 확장) | §8.5 |
| `kbj/services/engine/verify_stock.py` | 실데이터 완료 검증 CLI(값을 출력하지 않음) |
| `config/jobs.yaml`(웨이브 3 — enabled 뒤집기), `pyproject.toml`(계약 ⑪~⑬ — §2) | — |
| `.github/workflows/ci.yml` | 골든·수용 시험 포함(§8.7) |
| `scripts/canonical_baseline.txt`, `scripts/check_canonical.py`(그룹 `fnguide` 신설 — 목표 0, `comp.fnguide.com`·`wisereport` 를 kbj 에서 금지 [제안]), `tests/test_check_canonical.py` | §1.12 |
| §1.12 의 legacy 삭제·고침, `scripts/test_legacy.sh`, `legacy/*/MIGRATION.md` | — |
| `docs/PLAN.md` P4 결과, `docs/probe_results.md` §7 #28~#37, `docs/adr/0013~0016`(초안 확정), `docs/conflict_map.md`(§1.12·§1.13 컨센서스 행 갱신) | 문서 |

### 1.12 legacy 이전 범위 (D-P4-19, P3 §1.10 의 'P4 로' 행)

기준: P4 kbj 작업·페이지가 그 기능을 대체했고 떼어낼 수 있는 것만 지운다. SD `server.py` 는 함수 단위 — SD 검사 스크립트(`legacy/stock_dashboard/tests/test_legacy_checks.py` 가 부르는 `scripts/check_*.py` 10개)가 AST 로 꺼내 쓰는 함수는 **지우기 전에 import 그래프로 확인**하고, 걸리면 그 스크립트도 대체됐는지 본다(대체됐으면 함께 퇴역, 아니면 남김 — R14).

| legacy 기능 (파일:함수:줄) | P4 대체 | P4 처리 | 기준선 변화 [추정] |
|---|---|---|---|
| SD `consensus_collector.py`(네이버 2)·`consensus_quarterly_collector.py`(네이버 2)·`consensus_snapshot_collector.py`·`revision_calculator.py` | `consensus.snapshot`(KIS), `kbj.engines.consensus` | **삭제** | naver −4 |
| SD `earnings_parser.py`·`earnings_signal_classifier.py`·`earnings_alert_writer.py`(Ollama — 폐기)·`earnings_telegram_sender.py`(notifier shim 호출 — inventory #16) | `filings.dart_feed`·`earnings.alerts`·`earnings.backfill` | **삭제** | telegram 그룹 0 유지 |
| SD `overhang_parser.py`·`dart_collector.py`(aiohttp·tenacity)·`valuation_calculator.py`·`tam_modeler.py` | `filings.dart_feed`·`fin.quarterly`·`fin.valuation_band` | **삭제**(pyproject legacy 그룹의 `aiohttp`·`tenacity` 는 다른 사용처가 없으면 S 가 지움 [확인 필요]) | — |
| SD `server.py` 종목 상세·공시 함수: `api_financial`:3869(네이버), `api_dart_financial`:10528, `_fetch_dart_quarter`:10425, `_try_dart_segment_revenue`:10454, `poll_dart_disclosures`:10166, `score_disclosure`:9974·`_DISCLOSURE_SCORE_TABLE`:9920·`_cap_bonus_for_code`:9946·`recalc_disclosure_scores`·`api_disclosure_*`·`api_disclosures`:10273, `init_dart_corp_map_db`:10139·`_load_dart_corp_code_map`:10325, `_fetch_kr_earnings`:11047, `/api/revision/*`:18641~18814, `/api/earnings/*`:16327~16377, `/api/valuechain2/stock/<code>/tam`, `api_chart`:4419·`api_price`:4082·`_fetch_naver_minute_candles`:4178·`_append_today_candle_kr`:4367(종목 차트), `api_compare`:3782 | 페이지 8·5, `/api/stock/*`·`/api/filings/*` | **함수 단위 삭제** + 이 함수만 부르는 cron 등록(`_scheduler.add_job` 줄) | naver −6, krx_scrape −6 |
| SD `server.py` 네이버 전용·대체 없음: `_crawl_naver_research`:2366·`_extract_report_html`:2421·`_scrape_naver_research_list`:9102(증권사 리포트), `_fetch_naver_board_titles`:15246(종목토론실) | — (폐지 — conflict_map §1.13 ⚠ 대체 없음) | **삭제**(등록부 retired 에 `SD:tg_reports` 이미 있음) | naver −7 |
| SD `server.py` P3 대체분(D-P3-18 이월): `_fetch_naver_live_prices`:816, `_overlay_live_prices_on_data`:749, `_fetch_kr_indices_live`:877, `_fetch_naver_trend`:2812·`api_flow`:3144, `_naver_json`:3219·`_scrape_naver_sectors`:3232·`_scrape_naver_sector_detail`:3318(+`_legacy_unused` 3개), `_refresh_prices_from_naver`:8045, `_price_broadcaster`:6776 | P3 페이지 1·2·4 | **함수 단위 삭제**. SD 검사 스크립트 `check_new_high_logic`·`check_newhigh_full_list`·`check_newhigh_flow` 는 P3 신고가 골든이 대체 → 퇴역 | naver −15 |
| SD `server.py` 남김: `api_news`:2285(P5 뉴스), `_load_naver_universe`:3626·`api_peers_kr`:9674(피어 — P5 분류 사전), `_scrape_etf_price`:6482(P5 ETF), `_pykrx_*`·`_fetch_night_futures`·`api_kr_options`(P7), `api_sector_rotation`(P5) | — | P5·P7 | — |
| ET `board/ingest/dart.py`·`financials.py`·`triggers.py:_dart` | P2 정본 + `fin.quarterly` | **남김** — board 툴팁·monitor/kr 재무 카드가 쓴다(이미 브리지 경유). monitor/kr 재구축(P5)에서 `pub_fin` 으로 바꿀 때 지움 | — |
| DR `dartreport/*`·`run.py`·`app.py` | `kbj/engines/financials`·`kbj/reports/dart_excel` | shim(§1.5), `app.py` 삭제 | — |
| 합계 | | | naver 약 49 → 약 17, krx_scrape 약 39 → 약 33 [추정 — S 가 실측해 기준선과 PLAN 에 적는다] |

### 1.13 묶음별 파일 소유 (겹치지 않는다)

| 묶음 | 손대는 파일 |
|---|---|
| M | `kbj/core/fin_rows.py`, `kbj/store/migrations/0010_filings.sql`·`0011_fin.sql`·`0012_fin_prv.sql`·`0013_journal_watchlist.sql`, `kbj/store/repos/{filings,fin,consensus,journal}.py`·`__init__.py`·`memory.py`, `kbj/store/legacy_import/{mappings,transforms}.py`, `kbj/data/public/dart/datasets.py`, `kbj/data/private/kis/datasets.py`, `kbj/data/catalog.py`(필요할 때만), `kbj/config/fin.py`, `config/{jobs,notify,limits,fin,reports}.yaml`(웨이브 1), `pyproject.toml`·`uv.lock`(웨이브 1 — 의존성), `docs/metrics.md`, `docs/secrets.md`, `tests/fixtures/synthetic/p4_world.py`·`p4_expect.yaml`, `tests/fixtures/test_p4_world.py`, `tests/test_store_layout.py`, `tests/test_import_contracts.py`(`RUNTIME_IMPORTS` 만), `tests/unit/core/test_fin_rows.py`, `tests/unit/store/repo_cases_p4.py`·`test_repos_p4_memory.py`·`test_legacy_import*.py`, `tests/unit/data/public/test_public_datasets.py`, `tests/unit/kis/test_kis_datasets.py`, `tests/unit/config/test_fin_config.py`, `tests/unit/scheduler/test_registry_validation.py`, `tests/integration/test_migrations_pg.py`·`test_repos_p4_pg.py` |
| W | `web/src/app/{pager,pages,route}.ts`, `web/src/ui/{search,tabs,band,waterfall,stackbar,timeline,kv,lock}.ts`, `web/src/design/components.css`, `web/test/{pager,route,ui_p4,ui}.test.ts` |
| F | `kbj/data/public/dart/{feed,disclosures,structured,ownership,prelim_doc,overhang_doc,company,shares}.py`, `kbj/services/collectors/{dart_feed,dart_company}.py`, `tests/fakes/dart_server.py`, `tests/fixtures/synthetic/dart/**`, `tests/unit/data/public/test_dart_{feed,kinds,structured,ownership,prelim,overhang_doc,company,shares,fixtures}.py`, `tests/unit/collectors/test_dart_{feed,company}.py`, `tests/unit/fakes/test_dart_server_p4.py` |
| E | `kbj/engines/filings/**`, `kbj/services/engine/{filings,earnings}.py`, `tests/unit/engines/filings/**`, `tests/unit/engine_jobs/test_{filings,earnings}.py`, `tests/property/test_overhang_props.py` |
| R | `kbj/engines/financials/**`, `kbj/reports/dart_excel/**`, `kbj/services/collectors/fin.py`, `config/dart_report/mapping.yaml`(이동), `tests/golden/dart_report/**`, `tests/oracles/{xlsx_normalize,formula_eval}.py`, `tests/unit/engines/financials/**`, `tests/unit/reports/**`, `tests/unit/collectors/test_fin.py`, `legacy/etf_traker/dart-report/**`(shim·이동·`app.py` 삭제) |
| K | `kbj/data/private/kis/consensus.py`, `kbj/engines/consensus/**`, `kbj/services/collectors/consensus.py`, `tests/fakes/kis_server.py`, `tests/fixtures/synthetic/kis/{p4_consensus.json,make_p4.py}`, `tests/unit/kis/test_parse_consensus.py`, `tests/unit/engines/consensus/**`, `tests/unit/collectors/test_consensus.py`, `tests/unit/fakes/test_kis_server_p4.py` |
| V | `kbj/engines/valuation/**`, `kbj/services/engine/valuation.py`, `tests/unit/engines/valuation/**`, `tests/property/test_valuation_pit.py`, `tests/unit/engine_jobs/test_valuation.py` |
| A | `kbj/services/api/routes/{stock,filings,watchlist}.py`, `kbj/services/api/readers/{stock,filings,fin,watchlist,_common}.py`, `kbj/services/api/models/{stock,filings,fin}.py`, `kbj/services/api/{app,cache}.py`, `kbj/services/watchlist/**`, `web/src/api/openapi.json`, `web/test/fixtures/api/{stock,filings,watchlist}/**`, `tests/unit/api/test_routes_{stock,filings,watchlist}.py`·`test_models_p4.py`, `tests/unit/watchlist/**` |
| X | `kbj/services/public_export/{filings_json,fin_json,corp_index,export,manifest}.py`, `web/scripts/merge-public-data.mjs`, `web/test/scripts.test.ts`, `web/test/fixtures/public/**`(P4 파일), `tests/unit/public_export/test_{filings_json,fin_json,corp_index,export,manifest}.py` |
| W2 | `web/src/pages/{p5_filings,p8_stock}.ts`, `web/src/pages/{p5,p8}/**`, `web/src/api/types.gen.ts`, `web/test/pages/{p5,p8}_*.test.ts`, `web/test/acceptance/**` |
| S | `tests/acceptance/**`, `tests/sim/**`, `kbj/services/engine/verify_stock.py`, `config/jobs.yaml`(웨이브 3), `pyproject.toml`(웨이브 3 — 계약), `.github/workflows/ci.yml`, `scripts/{canonical_baseline.txt,check_canonical.py,test_legacy.sh}`, `tests/test_check_canonical.py`, §1.12 의 SD 삭제 파일·`server.py`·SD 검사 스크립트, `legacy/*/MIGRATION.md`, `tests/unit/scheduler/legacy_jobs.txt`(바뀌면), `docs/{PLAN,probe_results,conflict_map}.md`, `docs/adr/0013~0016` |

**공유 파일 — 누가 언제**

| 파일 | 웨이브 1 | 웨이브 2 | 웨이브 3 |
|---|---|---|---|
| `config/jobs.yaml` | **M**(P4 행 확정, 전부 `enabled: false`) | 안 고침 — 바꿀 것은 S 에 메모 | **S**(enabled 뒤집기) |
| `pyproject.toml`·`uv.lock` | **M**(openpyxl·bs4) | 안 고침 | **S**(계약 ⑪~⑬) |
| `kbj/store/repos/memory.py`·`__init__.py` | **M** | 안 고침(메모리 구현이 모자라면 M 담당 요청 목록) | — |
| `tests/fakes/kis_server.py` | — | **K** | S 는 쓰기만 |
| `tests/fakes/dart_server.py` | — | **F** | S 는 쓰기만 |
| `tests/fixtures/synthetic/p4_world.py` | **M** | 안 고침(필요한 사례는 M 요청 — 웨이브 2 동안 M 담당 대기) | S(완료 시험 사례 보강 시) |
| `kbj/services/api/app.py`·`readers/_common.py` | — | **A** | — |
| `kbj/services/public_export/export.py`·`manifest.py` | — | **X** | — |
| `web/src/app/*`·`web/src/ui/*`·`web/package.json` | **W** | 안 고침 | W2 는 고치지 않는다(필요한 부품은 W 가 미리 — §1.2) |
| `docs/metrics.md` | **M**(§9~§15) | 안 고침 | S |
| ADR | — | 초안: 0013 F·0014 R·0015 K·0016 V | S 가 번호·상태 확정 |

### 1.14 묶음별 완료 확인 명령 (모두 종료 코드 0)

| 묶음 | 명령 |
|---|---|
| M | `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest tests/test_store_layout.py tests/test_import_contracts.py tests/fixtures/test_p4_world.py tests/unit/core tests/unit/store tests/unit/data tests/unit/kis tests/unit/config tests/unit/scheduler -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run lint-imports` (+ Docker 있으면 `uv run pytest -m integration tests/integration/test_migrations_pg.py tests/integration/test_repos_p4_pg.py`) |
| W | `cd web && npm ci --ignore-scripts && npm run lint && npm run typecheck && npm test && npm run build:public && npm run build:login && node scripts/check-bundle.mjs` |
| F | `uv run pyright && uv run pytest tests/unit/data/public tests/unit/collectors/test_dart_feed.py tests/unit/collectors/test_dart_company.py tests/unit/fakes -q && uv run python scripts/check_canonical.py --groups dart` |
| E | `uv run pyright && uv run pytest tests/unit/engines/filings tests/unit/engine_jobs/test_filings.py tests/unit/engine_jobs/test_earnings.py tests/property/test_overhang_props.py -q` |
| R | `uv run pyright && uv run pytest tests/golden/dart_report tests/unit/engines/financials tests/unit/reports tests/unit/collectors/test_fin.py -q && bash scripts/test_legacy.sh dart-report` |
| K | `uv run pyright && uv run pytest tests/unit/kis/test_parse_consensus.py tests/unit/engines/consensus tests/unit/collectors/test_consensus.py tests/unit/fakes -q` |
| V | `uv run pyright && uv run pytest tests/unit/engines/valuation tests/property/test_valuation_pit.py tests/unit/engine_jobs/test_valuation.py -q` |
| A | `uv run pyright && uv run pytest tests/unit/api tests/unit/watchlist -q && uv run python -m kbj.services.api openapi --check` |
| X | `uv run pytest tests/unit/public_export -q && bash scripts/build_public_site.sh --dry-run && (cd web && npx vitest run test/scripts.test.ts)` |
| W2 | W 의 명령 + `cd web && npm run gen:types -- --check && npx vitest run test/pages test/acceptance` |
| S | `uv run ruff check && uv run ruff format --check && uv run pyright && uv run lint-imports && uv run pytest -q && uv run pytest tests/sim -m sim && uv run pytest tests/acceptance -q && uv run python scripts/check_canonical.py && uv run python scripts/check_public_safety.py && bash scripts/test_legacy.sh all` + W 명령 + CI 녹색 |

### 1.15 옮기는 원본 시험 수 [추정]

| 묶음 | 옮김(kbj 로) | 삭제(legacy — 대체됨) | 남김 |
|---|---|---|---|
| F | 0(SD 파서 시험 없음 — 새로 씀). ET `test_dart_for` 7 은 P2 에 이미 옮김 | — | ET `test_split_actions` 7·`test_financials` 12·`test_triggers*` 78(board 가 계속 씀 — P5) |
| R | 0(DR 은 `tests_smoke.py` 뿐 — 그대로 legacy 에서 shim 통과) | — | `tests_smoke.py` |
| K·V·E | SD `revision_calculator._self_test` 분류 표(1 → 시험 약 8) | SD 검사 스크립트 3개(신고가 — P3 대체) | SD 나머지 검사 스크립트 7 |
| S | — | SD 검사 래퍼 13 → 약 10 [추정] | — |

---

## 2. 공개/로그인 배치

| 층 | 공개(정적 Pages) | 로그인(VM) |
|---|---|---|
| 데이터 | `pub_filings.*`(공시·잠정실적·실적 일정·오버행 사건·상태·요약·내부자 보고·창·자기주식·주식총수·기업개황), `pub_fin.*`(원시 재무·분기·비율·리포트·부문) — **원천이 DART 하나** | 위 + `prv_fin.*`(컨센서스·투자의견·목표가 컨센·리비전·밸류 일별·밴드·시나리오·서프라이즈), `prv_market.share_class`, `prv_journal.watchlist` |
| 계산 | `kbj.services.engine.filings`(오버행·내부자·일정·키워드 점수), `kbj.services.collectors.fin`(분기·비율), `kbj.reports.dart_excel`(리포트) — **로그인 모듈을 import 하지 않는다** | `kbj.services.engine.{valuation,earnings}`, `kbj.services.collectors.consensus`, API readers(관심종목 점수·ITM·금액) |
| 경계 겹 | 계약 ⑪ `kbj.reports` → `kbj.data.private`·`kbj.engines.{consensus,valuation}`·`kbj.services.api` 금지 / 계약 ⑫ `kbj.engines.{filings,financials}` → `kbj.engines.{consensus,valuation}`·`kbj.data.private` 금지 / 계약 ⑬ 공개 작업(`kbj.services.collectors.{dart_feed,dart_company,fin}`·`kbj.services.engine.filings`) → `kbj.data.private`·`kbj.engines.{consensus,valuation}`·`kbj.store.repos.{consensus,journal}` 금지 + 정적 시험 "이 모듈들의 SQL 이 쓰는 스키마는 `pub_` 만"(`tests/unit/test_public_writers.py`) | P3 계약 ⑩(API 외부 호출 없음) 그대로 |
| 혼합 값 | — | **오버행 금액·ITM**(잠재 주식 × 현재가), **내부자 금액**(증감 × 보고일 종가), **관심종목·시총 가산 중요도**, **서프라이즈(컨센 대비)** 는 읽을 때 계산하고 `pub_*` 에 쓰지 않는다(ADR 0002 "가장 높은 등급") |
| 프런트 | 페이지 5(DART 패널 전부)·8(재무·리포트·공시·오버행 주식 수·내부자 주식 수·TradingView), 로그인 패널은 자물쇠 | 전부 |

---

## 3. 데이터 흐름

### 3.1 한눈에

> **2026-10-08 갱신: 장 마감 뒤 텔레그램은 전부 16:00 — [ADR 0018](adr/0018-post-close-1600.md)**(사용자 결정). `consensus.snapshot`(리비전 알림 `alert.revision`)은 18:30 → 16:00 — 마감 수집이 끝난 뒤 약 3분 수집하고 같은 작업이 보낸다(평소 16:00~16:05). `market.close_collect` 굳은 의존은 가격 때문이 아니라 KIS 앱키 버킷 차례(유니버스는 전 거래일 시총). 아래 시각은 고쳤다.

```
(KST)  03:05 filings.corp_code (P2) ─────────────► pub_filings.corp_code
  토   04:00 filings.company_profile ── DART company ─► pub_filings.corp_profile(결산월·업종)
       05:30 public.export ── pub_* ─► public-data(공시·잠정·오버행·내부자·일정·fin/<코드>·corp_index)
       06:30 earnings.backfill ── 원장 ─► prv_fin.earnings_surprise(3·5·7거래일 성과)
 07:00~19:59 filings.dart_feed (매분) ── list.json 증분 ─► pub_filings.disclosure
                   └─ 새 접수번호(@event): 구조화 API·원문 ─► earnings_prelim·overhang_event·overhang_adjust
                                                         ·insider_report·major_holder_report·buyback_event
                   (07:00 첫 실행: 전 영업일 전체 다시 훑기)
 07:05~20:55 earnings.alerts (5분) ── 미처리 잠정실적 ─► classify_yoy(+컨센) ─► prv_fin.earnings_surprise
                                                    └─ 관심종목·우선순위≤2 ─► notify alert.earnings
 07:00~21:30 fin.quarterly (30분) ── 정기보고서 접수 ─► fnlttSinglAcntAll(상세 대상)/MultiAcnt(07:30 묶음)
                   ─► pub_fin.statement_raw ─► normalize(to_quarterly) ─► pub_fin.quarterly·ratio
       08:05 krx.daily (P3) ─► prv_market.stock_snapshot(krx — 시총·상장주식수)
       08:50 fin.valuation_band ── 원장 + pub_fin.quarterly(PIT) + 컨센 ─► prv_fin.valuation_daily·band·scenario
 07:10~20:40 filings.derive (30분) + 21:30 ─► pub_filings.overhang_state·summary·insider_window·earnings_schedule·점수
       16:00 consensus.snapshot ── KIS 추정실적·투자의견 ─► prv_fin.consensus_snapshot·invest_opinion   (ADR 0018 — 원래 18:30)
                   ─► target_consensus·revision ─► 관심종목 리비전 notify alert.revision(일 1통)
       21:00 reports.dart_excel ── 관심종목 ∪ 공개 데모(재무 바뀐 것) ─► pub_fin.report(payload + xlsx)
  (요청 때) api readers ── repos ─► engines(순수) ─► Envelope[...] ─► 페이지 5·8
```

### 3.2 작업 표 (P4 에 켜는 것)

| 작업(등록부) | 트리거·조건 | 수집 데이터 키(source:dataset @ as_of) | 쓰는 표 | 재시도·마감 | 소유 모듈 |
|---|---|---|---|---|---|
| `filings.dart_feed` | `* 7-19 * * 1-5`, always | `DART:list @ minute`, `DART:document @ event`, `DART:{cvbd,bdwt,exbd,piic,fric,tsstkAq,tsstkDp}Decsn @ event`, `DART:elestock @ event`, `DART:majorstock @ event` | `pub_filings.disclosure`·`earnings_prelim`·`overhang_event`·`overhang_adjust`·`insider_report`·`major_holder_report`·`buyback_event` | 0(다음 분), 마감 5분, 건별 파싱 재시도 3회(다음 실행들) | `kbj.services.collectors.dart_feed:run` |
| `filings.company_profile` | `0 4 * * 6`, always | `DART:company @ run_date` | `pub_filings.corp_profile` | 2×1800 s, 마감 360분 | `kbj.services.collectors.dart_company:run` |
| `fin.quarterly` | `*/30 7-21 * * *`, always | `DART:fnlttMultiAcnt @ run_date`, `DART:fnlttSinglAcntAll @ event`, `DART:stockTotqySttus @ event` | `pub_fin.statement_raw`·`quarterly`·`ratio`, `pub_filings.share_count` | 3×1800 s | `kbj.services.collectors.fin:quarterly` |
| `fin.backfill` | manual(`backfill fin.backfill --from-year 2021 --tier main\|full`) | 같은 키 @ 과거 | 같음 | 2×600 s, 예산 `dart.backfill_cap` | `…fin:backfill` |
| `filings.derive` | `10,40 7-20 * * 1-5` + `30 21 * * *`, always | — (DB 만) | `pub_filings.overhang_state`·`overhang_summary`·`insider_window`·`earnings_schedule`, `disclosure.kw_score` | 1×300 s | `kbj.services.engine.filings:derive` |
| `earnings.alerts` | `*/5 7-20 * * 1-5`, always | — | `prv_fin.earnings_surprise` | 0(다음 5분) | `kbj.services.engine.earnings:alerts` |
| `earnings.backfill` | `30 6 * * *`, trading_day | — | `prv_fin.earnings_surprise`(성과 열) | 1×600 s | `kbj.services.engine.earnings:backfill` |
| `consensus.snapshot` | `0 16 * * 1-5`(ADR 0018 — 원래 `30 18`), T, `depends_on market.close_collect(hard)` — 가격이 아니라 KIS 버킷 차례 | `KIS:consensus_estimate @ trade_date`, `KIS:invest_opinion @ trade_date` | `prv_fin.consensus_snapshot`·`invest_opinion`·`target_consensus`·`revision` | 2×1800 s | `kbj.services.collectors.consensus:run` |
| `fin.valuation_band` | `50 8 * * 1-5`, T, `depends_on krx.daily(hard)`, `fin.quarterly(soft)` | — | `prv_fin.valuation_daily`·`valuation_band`·`scenario`, `prv_market.share_class` | 1×600 s | `kbj.services.engine.valuation:band` |
| `reports.dart_excel` | `0 21 * * *`, always | `DART:report_inputs @ event`(`<종목>:<실행일>`) | `pub_fin.report` | 1×1800 s | `kbj.reports.dart_excel.job:run` |

`DART:report_inputs` 는 dart-report 파이프라인 한 번이 부르는 `list.json`·`fnlttSinglAcntAll`·`document.xml` 묶음을 **논리 데이터셋 하나**로 본다(등록부 정적 규칙 — 같은 (source, dataset) 은 한 작업만. `DART:list`·`DART:document` 는 dart_feed 몫이다). 재무제표 원시 행은 `pub_fin.statement_raw` 가 있으면 그것을 읽어(`PgStatementCache`) 호출을 줄인다 — 같은 행이면 legacy 와 결과가 같다(골든이 확인).

### 3.3 새·바뀐 데이터셋 (카탈로그)

| id | 엔드포인트·TR | as_of | 저장 표 | 비고 |
|---|---|---|---|---|
| `DART:list` (바뀜) | `list.json` | minute | `pub_filings.disclosure` | 증분(§4.1). 정렬 기본값 [확인 필요 #28] |
| `DART:document` (바뀜) | `document.xml` | event | `pub_filings.earnings_prelim`·`overhang_adjust`(파싱 결과만) | 노트의 '[확인 필요] 저장 표 P4' 확정 |
| `DART:cvbdIsDecsn`·`bdwtIsDecsn`·`exbdIsDecsn`·`piicDecsn`·`fricDecsn` (신규) | 주요사항보고서 구조화(전환사채·신주인수권부사채·교환사채·유상증자·무상증자 결정) [확인 필요 #31] | event | `pub_filings.overhang_event` | corp_code + 기간으로 묻고 접수번호로 고른다 |
| `DART:tsstkAqDecsn`·`tsstkDpDecsn` (신규) | 자기주식 취득·처분 결정 [확인 필요 #31] | event | `pub_filings.buyback_event` | |
| `DART:elestock` (신규) | 임원·주요주주 소유보고 [확인 필요 #32] | event | `pub_filings.insider_report` | 회사의 보고 전체가 온다 — 새 접수번호만 저장 |
| `DART:majorstock` (신규) | 주식등의 대량보유상황보고 [확인 필요 #32] | event | `pub_filings.major_holder_report` | 같음 |
| `DART:stockTotqySttus` (신규) | 정기보고서 주요정보 — 주식의 총수 현황 [확인 필요 #33] | event | `pub_filings.share_count` | 오버행·내부자 비율 분모(공개) |
| `DART:company` (바뀜) | `company.json` | run_date | `pub_filings.corp_profile` | `acc_mt`(결산월) |
| `DART:fnlttSinglAcntAll` (바뀜) | 단일회사 전체 재무제표 | **event**(`<corp>:<연도>:<보고서>:<CFS/OFS>`) | `pub_fin.statement_raw`·`quarterly` | quarter → event(회사별 수시 적재) |
| `DART:fnlttMultiAcnt` (바뀜) | 다중회사 주요계정(100개 묶음) | **run_date** | 같음(층 `main`) | 시즌 중 매일 07:30 묶음 |
| `DART:report_inputs` (신규) | (위 엔드포인트 묶음 — 논리) | event | `pub_fin.report` | §3.2 끝 |
| `KIS:consensus_estimate` (바뀜) | 종목추정실적 `HHKST668300C0` [확인 필요 #29] | trade_date | `prv_fin.consensus_snapshot` | D-P4-9 |
| `KIS:invest_opinion` (신규) | 종목투자의견 `FHKST663300C0` [확인 필요 #29] (증권사별 `FHKST663400C0` 는 대안) | trade_date | `prv_fin.invest_opinion` | 기간 90일 |

### 3.4 마이그레이션 0010~0013 (DDL 요약)

공통 규칙(0003·0007 과 같다): 값 행마다 `source`·`quality`(ok·stale·estimated·invalid)·`received_at`·`loaded_by`, 종목코드 `text`(`^[0-9A-Z]{6}$`), corp_code `^[0-9]{8}$`, 금액 **원 단위 `bigint`**(DART 원 정수 — 외국 통화 회사는 `currency` 열, 환산하지 않음), 날짜 `date`, `CREATE … IF NOT EXISTS`, 적용 뒤 고치지 않음. `pub_*` 표는 0001 기본 권한으로 `kbj_public_export` 가 읽고(명시 GRANT 도 함께 — 0004 방식), `prv_*` 는 못 읽는다(통합 시험).

| 파일 | 표 | 키 | 주요 열 | 비고 |
|---|---|---|---|---|
| **0010_filings** | `pub_filings.disclosure` | `rcept_no`(14자) | `corp_code`, `stock_code`, `corp_name`, `corp_cls`(Y·K·N·E), `title`, `base_title`, `filer`, `rcept_dt`, `remark`, `kind`, `is_correction`, `kw_score smallint`, `kw_matched text[]`, `first_seen_at timestamptz`, `parse_state`(none·pending·done·failed·na), `parse_tries smallint`, `parse_note`, `fin_loaded_at` | 색인 `(stock_code, rcept_dt DESC)`, `(rcept_dt, kind)`, `(parse_state) WHERE parse_state IN ('pending','failed')` |
| | `pub_filings.corp_profile` | `corp_code` | `stock_code`, `acc_mt smallint`(1~12), `induty_code`, `corp_cls`, `est_dt`, `updated_at` | 대표자 이름 등 개인 칸은 받지 않는다 |
| | `pub_filings.earnings_prelim` | `rcept_no` | `corp_code`, `scope`, `fiscal_year`, `fiscal_q`(1~4, 연간 = 4 + `is_annual`), `period_end`, `period_inferred`, `unit_mult`, `revenue`·`op`·`pretax`·`ni`·`ni_owner` × {`_cur`, `_prev_q`, `_prev_y`}, `eps_cur`·`eps_prev_y`(원/주), `yoy_reported jsonb`, `currency` | 정정은 새 행 + `disclosure.is_correction` |
| | `pub_filings.earnings_schedule` | `(corp_code, period_end, kind)` | `kind`(pre_announce·statutory), `planned_date`, `rcept_no`, `quality`(statutory = estimated) | `filings.derive` 가 쓴다 |
| | `pub_filings.overhang_event` | `rcept_no` | `corp_code`, `instrument`, `face_value`, `price`(전환·행사·교환·발행가), `shares`(발행 결정 시 잠재·신주 수), `refix_floor`, `exercise_start`, `exercise_end`, `maturity`, `listing_date`, `alloc_method`(제3자배정 등), `parse_source`(api·document) | |
| | `pub_filings.overhang_adjust` | `rcept_no` | `target_rcept_no`, `kind`(price_adjust·exercise·redemption), `new_price`, `amount`, `shares`, `effective_date` | 대상 연결 실패면 `quality=invalid` + 사유 |
| | `pub_filings.overhang_state` | `(corp_code, as_of, rcept_no)` | `remaining_face`, `price`, `potential_shares`, `active`, `exercisable`, `lockup_end_est` | 파생(§4.3) |
| | `pub_filings.overhang_summary` | `(corp_code, as_of)` | `potential_new_shares`, `exercisable_shares`, `exchange_shares`, `denominator`, `denom_source`, `ratio_pct numeric`, `exercisable_ratio_pct` | 파생 |
| | `pub_filings.insider_report` | `rcept_no` | `corp_code`, `reporter`, `position`, `registered`, `major_holder`, `shares_after`, `shares_delta`, `ratio_after`, `ratio_delta`, `reason`(원문 파싱 성공 때만) | |
| | `pub_filings.major_holder_report` | `rcept_no` | `corp_code`, `reporter`, `report_type`, `shares`, `shares_delta`, `ratio`, `ratio_delta`, `reason` | |
| | `pub_filings.insider_window` | `(corp_code, as_of, days)` | `net_shares`, `buy_shares`, `sell_shares`, `n_reports`, `n_reporters`, `ratio_pct`, `reason_known_pct` | 파생 — days ∈ 30·90·180 |
| | `pub_filings.buyback_event` | `rcept_no` | `corp_code`, `kind`(acquire·dispose·trust_in·trust_out), `shares_planned`, `amount_planned`, `start`, `end`, `method` | |
| | `pub_filings.share_count` | `(corp_code, period_end, rcept_no)` | `common_issued`, `pref_issued`, `treasury_common`, `treasury_pref` | |
| **0011_fin** | `pub_fin.statement_raw` | `(corp_code, bsns_year, reprt_code, fs_div)` | `rcept_no`, `rows jsonb`(응답 list 그대로), `n_rows`, `currency` | 상세 층만. 크기 약 350사 × 20보고서 × 30KB(TOAST 압축 전) [추정] |
| | `pub_fin.quarterly` | `(corp_code, fiscal_year, fiscal_q, fs_div)` | `period_start`, `period_end`, `rcept_dt`(PIT 기준), `tier`(main·full), `revenue`, `cogs`, `gross_profit`, `sgna`, `op`, `pretax`, `tax`, `ni`, `ni_owner`, `d_and_a`, `ebitda`, `total_assets`, `total_liabilities`, `total_equity`, `equity_owner`, `cash`, `short_inv`, `total_debt`, `net_debt`, `minority`, `eps_basic`, `derived`, `reported_3m jsonb`, `diff3m_max_pct`, `currency` | 손익은 분기 단독, 상태표는 분기말 |
| | `pub_fin.ratio` | `(corp_code, fiscal_year, fiscal_q)` | `ttm_revenue`, `ttm_op`, `ttm_ni_owner`, `opm`, `npm`, `roe`, `roa`, `debt_ratio`, `net_debt_ratio`, `rev_yoy`, `op_yoy`, `eps_ttm`, `bps`, `shares_basis` | §4.5 |
| | `pub_fin.report` | `(stock_code, built_on)` | `corp_code`, `year_from`, `year_to`, `fs_div_used`, `payload jsonb`, `xlsx bytea`(≤ 2 MiB — CHECK), `xlsx_sha256`, `warnings jsonb`, `engine_version`, `input_digest`, `generated_at` | 종목마다 최근 4개만 남김(`ops.nightly`) |
| | `pub_fin.segment` | `(corp_code, bsns_year, segment)` | `amounts jsonb`, `rcept_no`, `quality`(estimated) | |
| **0012_fin_prv** | `prv_fin.consensus_snapshot` | `(code, snap_date, fiscal_ym, source)` | `is_estimate`, `revenue`, `op`, `ni`, `ni_owner`, `eps`, `ebitda`, `extra jsonb`(KIS 가 준 비율 — 계산에 쓰지 않음) | |
| | `prv_fin.invest_opinion` | `(code, broker, opinion_date, source)` | `opinion`, `opinion_prev`, `target_price`, `quality` | 증권사 이름은 원문 그대로 |
| | `prv_fin.target_consensus` | `(code, as_of)` | `mean`, `median`, `high`, `low`, `n_brokers`, `window_days` | |
| | `prv_fin.revision` | `(code, as_of, metric, fiscal_ym, window_days)` | `current`, `baseline`, `baseline_date`, `pct`, `signal` | SD `revision_alerts` 정의 하나로(이중 정의 해소 — inventory 282) |
| | `prv_fin.valuation_daily` | `(code, trade_date)` | `mktcap_company`, `price`, `ttm_ref`(분기 id), `per`, `pbr`, `psr`, `ev`, `ev_ebitda`, `per_fwd`, `psr_fwd`, `ev_ebitda_fwd`, `flags jsonb`, `quality` | hypertable(`trade_date`), 보존 없음, 압축 90일 뒤 [제안] |
| | `prv_fin.valuation_band` | `(code, as_of, metric)` | `p10`·`p25`·`p50`·`p75`·`p90`, `min`, `max`, `n_days`, `n_excluded`, `current`, `current_rank_pct`, `window_from` | |
| | `prv_fin.scenario` | `(code, as_of, case)` | `eps`, `eps_source`, `multiple`, `multiple_source`, `method`(PER·PBR), `tp`, `upside_pct` | |
| | `prv_fin.earnings_surprise` | `rcept_no` | `code`, `period`, `signal`, `priority`, `rev_yoy`, `op_yoy`, `rev_surprise`, `op_surprise`, `consensus_ref`, `alerted_at`, `notify_ticket`, `price_at_alert`, `perf_3d`·`perf_5d`·`perf_7d`, `perf_done` | |
| | `prv_market.share_class` | `code` | `corp_code`, `class`(common·pref), `common_code`, `source` | KRX 기본정보·이름 규칙(로그인 원천이라 prv) |
| **0013_journal_watchlist** | `prv_journal.watchlist` | `(list_name, code)` | `name_at_add`, `added_at`, `sort smallint`, `note`, `source`(import:sd·cli·api), `updated_at` | 개인 데이터 |

### 3.5 DART 호출량 (리미터 8/s, 일 예산 18,000 — `config/limits.yaml` `dart`)

| 작업 | 평일 보통 | 실적 시즌 최대 | 근거 |
|---|---|---|---|
| `filings.dart_feed` 목록(증분 — 분당 평균 1.2쪽 [추정]) | 약 950 | 약 1,300 | 780분 × 쪽 수. SD 는 매분 최대 10쪽을 처음부터 다시 읽었다(최대 7,800) |
| 07:00 전 영업일 다시 훑기 | 약 20 | 약 40 | 하루 1,500~3,000건 / 100 |
| 잠정실적·전환청구·조정 원문 | 약 40 | 약 450 | 시즌 하루 잠정실적 수백 건 [추정] |
| 구조화(CB·BW·EB·유증·무증·자사주) | 약 30 | 약 60 | |
| 임원·주요주주·대량보유 | 약 300 | 약 400 | 회사마다 1건(보고 전체가 온다) |
| `fin.quarterly` 주요계정 묶음 | 0 | 약 60 | 27묶음 × 직전·현재 보고서 |
| `fin.quarterly` 전체 재무제표(상세 층) + 주식총수 | 약 20 | 약 900 | 상세 대상 약 350 × CFS(+OFS 폴백) + 1 |
| `reports.dart_excel` | 약 50 | 약 1,000 | 종목당 약 46호출(DR: 연도 4 × 보고서 4 + 목록 + 원문 약 16 + 수주) × 관심 20 + 데모 — `statement_raw` 재사용 시 절반 |
| `filings.company_profile`(토요일) | (토) 2,700 | — | 상장사 수 |
| **합계** | **약 1,400**(토 +2,700) | **약 4,200** | 예산의 25% |
| `fin.backfill`(한 번) | 주요계정 5년 약 540 + 상세 층 5년 약 9,100 | — | **별도 상한 `dart.backfill_cap` 8,000/일 [제안]**, 우선순위 P4, 이틀 |

020(일 한도 초과)을 받으면 그날 예산을 닫는다(P2 `DartClient._status_error`) — `dart_feed` 는 그날 `failed`(사유)로 남고 다음 날 07:00 다시 훑기가 빠진 것을 채운다.

### 3.6 KIS 호출량 (초당 4건 — 앱키 하나)

| 작업 | 호출 수 | 시간 | 비고 |
|---|---|---|---|
| `consensus.snapshot` 16:00(ADR 0018) | (관심 20 ∪ 시총 상위 300) × 2 TR ≈ **640** | 약 3분(P3 우선순위) | `market.close_collect`(15:35~약 16:00)가 끝난 뒤 — 리비전 알림은 평소 16:00~16:05. 장중 버킷과 겹치지 않는다 |
| 그 밖 P4 | 0 | — | 밸류에이션은 DB(KRX 확정 시총)만. API 는 KIS 를 부르지 않는다(D-P4-16) |

### 3.7 `config/fin.yaml`·`config/reports.yaml` (신규 — 숫자는 한 곳)

| 키 | 기본값 | 근거 |
|---|---|---|
| `filings.keywords` | SD `_DISCLOSURE_SCORE_TABLE`:9920 그대로(10·8·6·4·2점) | 중요도 |
| `filings.importance_levels` | critical ≥ 10, high ≥ 6, medium ≥ 4 | SD `score_disclosure`:9993 |
| `filings.login_bonus` | 관심종목 +3, 시총 10조 +3·1조 +2·1,000억 +1 | SD :9989·`_cap_bonus_for_code`:9946 |
| `filings.feed.max_pages`·`retry_parse_max` | 30 · 3 | §4.1 |
| `earnings.thresholds` | BIG 20%, STANDARD 10%, INLINE 5%, 적자축소 50% | SD `THRESHOLDS`:166 |
| `earnings.alert_max_priority` | 2 | D-P4-14 [확인 필요] |
| `earnings.perf_days` | [3, 5, 7] 거래일 | SD 성과 백필(달력일 → 거래일) |
| `consensus.top_n`·`opinion_window_days`·`min_brokers_ok` | 300 · 90 · 3 [확인 필요] | §4.7 |
| `consensus.revision_windows`·`signal_pct` | [7, 30] · 강한상향 15·상향 5·하향 −5·강한하향 −15 | SD `revision_calculator`:38·:76 |
| `valuation.band_days`·`min_days` | 1240(5년) · 250 | §4.8 |
| `valuation.caps` | PER (0, 200), PBR (0, 50), PSR (0, 100), EV/EBITDA (0, 100) | SD `calculate_*_band` 거름값 그대로 [확인 필요] |
| `scenario.bear_eps_mult`·`bull_eps_mult` | 0.7 · 1.2 | SD `tam_modeler`:184·:201 |
| `overhang.lockup_months_third_party` | 12 [확인 필요: 규정] | D-P4-18 |
| `detail_universe` | 관심종목 ∪ `consensus.top_n` ∪ `reports.public_demo` | D-P4-5 |
| `reports.yaml`: `years_back`·`fs_div`·`public_demo`·`mapping` | 3(올해 포함 4개 연도 — DR `run.py`:69) · CFS · [] [결정 필요 — 공개 데모 종목] · `config/dart_report/mapping.yaml` | DR CLI 기본값, secrets.md `STOCKS`·`DART_STOCKS` 대체 |
| `public_export.fin_max_total_mb` | 25 [제안] | D-P4-17 |

### 3.8 관심종목 원천과 legacy 데이터 이관

| 원본 | P4 처리 | 이유 |
|---|---|---|
| SD `cache/server_watchlist.json`(브라우저 localStorage → `POST /api/watchlist/sync`) | `python -m kbj.services.watchlist import --from sd-json` 또는 legacy 이관(`kbj.store.legacy_import` 매핑 `watchlist`) → `prv_journal.watchlist(list_name='main')` | 사용자 파일(레포 밖)·로그인 등급 |
| SD `disclosure_history`·`earnings_actual`·`dart_disclosure_overhang`·`financial_quarterly` | **이관 안 함 — DART 로 다시 받는다**(`fin.backfill`, 공시는 최근 30일만 다시) | 공개 원천으로 다시 만들 수 있다. SD 30일 보존(:10260)이라 잃는 것 없음 |
| SD `consensus_*`·`valuation_band`·`revision_alerts`·`earnings_surprise`·`earnings_alert_queue` | **이관 안 함** [결정 필요 — 기본값] | 네이버·FnGuide 파생(U4·약관), 밴드는 네이버 재무비율의 주식 수를 썼다(`get_shares_outstanding`:52). p2 설계 §8.4 의 "`source='naver'` 표시만 남기고 이어 쌓기"를 이 결정으로 바꾼다 |
| SD `alert_history_v2`(어닝 알림 성과) | P8(알림 후 성과 통계)로 | P8 범위 |

---

## 4. 엔진 명세

정의의 정본은 부록 A(→ `docs/metrics.md` §9~§15). 여기에는 함수·입출력·예외·품질만 적는다.

### 4.1 공시 피드·종류·중요도

| 단계 | 규칙 |
|---|---|
| 증분 | 1쪽(100건)부터 읽어 그 쪽의 접수번호 중 하나라도 `seen` 이면 그 쪽까지만 넣고 멈춘다. 30쪽을 넘으면 `complete=False` + 경고(다음 분에 이어서). 정렬이 접수 내림차순이 아니면(실측 #28) 전 쪽을 읽고 `seen` 으로 거르는 방식으로 바꾼다 — 호출 수가 늘어 §3.5 를 다시 계산 |
| 접수 시각 | DART 응답에 시각이 없다(rcept_dt 날짜뿐 — P2 `disclosures_for` 머리말). `first_seen_at` = 우리가 처음 본 시각. 화면은 "HH:MM 수집" 으로 적는다 |
| 정정 | 제목 머리 `[기재정정]`·`[첨부정정]`·`[첨부추가]`·`[변경등록]` [확인 필요] → `is_correction`, `base_title` 로 원 공시와 짝. 파싱 결과 표는 새 접수번호 행을 따로 두고, 읽는 쪽(파생·화면)이 같은 대상의 **가장 늦은 접수번호**를 쓴다(DR CLAUDE "정정공시는 rcept_no 가 큰 쪽만") |
| 종류 | P2 `kind_of` + §1.3 의 새 종류(앞에 있는 것이 우선). 잠정실적 판정은 SD `classify_disclosure_type`:58 순서(자회사 > 결산예고 > 연결잠정 > 별도잠정, '실적등에대한전망' 제외) |
| 중요도(공개) | `keyword_score` = 맞은 키워드 점수 중 **최댓값**(합 아님 — SD :9986). 등급 critical·high·medium·low |
| 중요도(로그인) | `login_score` = 키워드 + 관심종목 3 + 시총 보너스(전 거래일 회사 시총) — 읽을 때 계산 |
| 실패 | 목록 실패 = 그 실행 `failed`(다음 분). 한 건 파싱 실패 = 그 건 `parse_state=failed`, `parse_tries`+1, 3회면 `na` + 사유(화면은 "파싱 실패 — 원문 보기") |

### 4.2 잠정실적·실적 일정·알림

| 항목 | 규칙 |
|---|---|
| 파싱 | SD `parse_preliminary_earnings`:181 — 라벨(매출액·영업이익·법인세비용차감전계속사업이익 [추가]·당기순이익·지배기업 소유주지분 순이익 [추가, 확인 필요]) 뒤 '당해실적' 행의 6열(당기·전기·전기대비%·전환·전년동기·전년동기대비%). 2열 폼은 당기·전기만. 단위 표기(억원·백만원·천원·원)를 **원**으로. EPS 는 원/주 그대로 |
| 기간 | `infer_period`: ① 본문의 기간 표기(예 'YYYY.MM.DD ~ YYYY.MM.DD' — 있으면 정답 [확인 필요 — 폼 실측 #30]) ② 없으면 `corp_profile.acc_mt` 로 rcept_dt 직전에 끝난 회계 분기(공시일 − 분기말 ≤ 100일), `period_inferred=True`, quality estimated ③ 결산월이 없으면 달력 분기(SD 방식) + estimated |
| 범위 | 본문의 연결/별도 표기(SD `detect_scope_from_body`:155)가 제목보다 우선 |
| YoY 분류(공개) | `classify_yoy`: 영업이익 흑자전환(전년동기 < 0 < 당기) → TURNAROUND(1), 적자전환 → SHOCK(1), 적자 축소 ≥ 50% → TURNAROUND_PARTIAL(2), YoY ≥ +20% → YOY_BIG_UP(1)·≥ +10% → YOY_UP(2), ≤ −20% → YOY_BIG_DOWN(1)·≤ −10% → YOY_DOWN(2), 그 밖 YOY_INLINE(5). 분모 = |전년동기|, 전년동기 0 이면 비율 없음(전환 판정만). 본문 '전년동기대비%' 와 우리 계산이 0.1%p 넘게 다르면 `notes`(본문 값은 반올림·정정 포함일 수 있음) |
| 서프라이즈(로그인) | 분기 컨센서스가 있을 때만 SD `classify_signal`:172(BEAT_BIG …) + `sanity` 3관문 결과를 같이 저장. **KIS 는 연간 추정뿐일 가능성이 높아(§4.7) P4 의 기본 경로는 YoY 분류** |
| 실적 일정 | 결산실적공시예고(본문 예정일) → `pre_announce`(ok). 법정 제출기한(분기·반기 = 분기말 + 45일, 사업 = 결산일 + 90일 [확인 필요: 휴일 처리]) → `statutory`(estimated). 이미 접수된 정기보고서가 있으면 그 기간 행은 지운다 |
| 알림 | `earnings.alerts`: 미처리 잠정실적(`prv_fin.earnings_surprise` 에 행 없음) → 분류 → 행 기록 → 관심종목 && priority ≤ 2 이면 `notify(kind='alert.earnings', subject=f'{corp}:{period}:{scope}', as_of=rcept_dt)`. 본문은 **템플릿**(SD `build_fallback`:299 문형 — 종목명·기간·매출·영업익·YoY·분류·DART 링크). 숫자는 모두 행에서 — LLM 없음(절대 규칙 3). 정정공시: 같은 subject 라 30일 안에 다시 울리지 않는다(값만 갱신 — 분류가 1단계 이상 바뀌면 `notes` 에만) |
| 성과 | `earnings.backfill`: `alerted_at` 다음 거래일 시가가 아니라 **알림 시각 직전 마감 종가**(`price_at_alert`) 대비 3·5·7거래일 뒤 종가(원장 krx > kis). 거래정지로 종가가 없으면 그 칸 None(지어내지 않음) |

### 4.3 오버행 (CB·BW·EB·유상증자)

| 항목 | 규칙 |
|---|---|
| 사건 | `overhang_event`(결정 공시) + `overhang_adjust`(전환가액·행사가액 조정, 전환·행사·교환 청구, 만기 전 상환 [확인 필요: 공시 제목·원문 형식 #31]) |
| 잔여 권면 | `remaining_face_t` = 권면총액 − Σ(청구·상환 금액, effective_date ≤ t). 청구 공시를 파싱 못 하면 권면총액 그대로(**상한**) + `quality=estimated` + "청구분 미반영" |
| 현재 가격 | 마지막 조정(≤ t)의 새 가격, 없으면 발행가. 리픽싱 하한(`refix_floor`)은 표시만 |
| 잠재 주식 | CB = 잔여 권면 ÷ 전환가, BW = 잔여 행사 금액 ÷ 행사가(분리형도 같은 식), EB = 잔여 ÷ 교환가(→ **교환 대상 주식** — 신주가 아님, 별도 합계), 유상증자 = 신주 수(결정일 ≤ t < 신주 상장일인 동안만 — 상장 뒤엔 상장주식수에 들어감). 소수점 버림 |
| 활성·행사 가능 | active: 결정일 ≤ t ≤ min(청구기간 끝, 만기) 이고 잔여 > 0. exercisable: active 이고 청구기간 시작 ≤ t |
| 오버행 비율(%) | Σ 잠재 신주(CB·BW·유증, active) ÷ 분모 × 100. **분모(공개)** = 최근 `share_count.common_issued`(DART 주식총수 — 자기주식 포함) — 그 뒤의 증자·분할 사건이 있으면 `notes`. **분모(로그인)** = 전 거래일 KRX 상장주식수(보통주). 둘 다 없으면 비율 None |
| 로그인 덧붙임 | ITM = 전 거래일 종가 ≥ 현재 전환·행사가, 오버행 금액 = 잠재 주식 × 종가(estimated — 시가가 아님) |
| 보호예수 | `lockup_end_est` = 제3자배정 유상증자 신주 상장일 + `lockup_months_third_party`(estimated). 그 밖은 '출처 미확보' |
| legacy 와 다른 점 | SD 는 만료만 보고(청구·조정·상환 반영 없음), 오늘의 주식 수(네이버 `financial`)로 희석률을 냈고, EV 에 오버행 가치를 더했다(D-P4-11) |

### 4.4 내부자 (임원·주요주주 소유보고, 5% 대량보유)

| 항목 | 규칙 |
|---|---|
| 입력 | `insider_report`(보고 1건 = 보고자 1명의 보고 후 소유·증감) |
| 정정 | 같은 보고자·같은 원 보고(제목 정정 짝)면 늦은 접수번호만 |
| 창 | t 기준 최근 30·90·180일(달력일 — 공시일 rcept_dt) |
| 순증감(주) | Σ `shares_delta`(창 안). 매수·매도 합은 부호로 나눈다 |
| 사유 | 원문의 변동 사유(장내매수·장내매도·증여·상속·신규선임·전환 등)를 파싱하면 `reason`, 못 하면 None. `reason_known_pct` = 사유를 아는 증감 주식 비율. **화면 이름은 "보고 기준 순증감(사유 미구분 포함)"** — 장내 순매수가 아닌 것을 섞기 때문 [확인 필요 #32: 사유 칸이 구조화 응답에 있는지] |
| 비율(%) | 순증감 ÷ 분모(§4.3 과 같은 분모) × 100 |
| 금액(로그인) | Σ(증감 × 보고일 종가), estimated(실제 거래가 아님) |
| 5% 대량보유 | 목록만(보고자·지분율 변화·사유) — 창 합계는 내지 않는다(같은 지분을 임원 보고와 이중 계산) |

### 4.5 재무 정규화·비율 (공개)

| 항목 | 규칙 |
|---|---|
| 환산 | `to_quarterly`(DR):1Q = 1분기 누적, 2Q = 반기 − 1Q, 3Q = 3분기 − 반기, 4Q = 연간 − 3분기 누적. 손익은 `thstrm_add_amount` 우선, 상태표는 `thstrm_amount`. 직전 누적이 없으면 값 None + `derived=True`(지어내지 않음) |
| 3개월값 대조 | 분기·반기 보고서의 `thstrm_amount`(3개월)와 차분값을 비교해 `reported_3m`·`diff3m_max_pct` 에 남긴다. 1% 넘으면 `quality=estimated` + "정정 반영 차이"(ET 실측: 163종목 중 8종목 — `board/ingest/financials.py`:140) |
| 연결/별도 | CFS 우선, 없으면 OFS(`fs_div` 열). 한 회사 안에서 분기마다 바뀌면 `notes` |
| 결산월 | `fiscal_period(bsns_year, reprt_code, acc_mt)` → 기간 시작·끝 [확인 필요 #34]. 비12월 결산은 화면 라벨 `FY25 1Q (25.04~25.06)` |
| 우선주 | DART corp_code 가 없다 → `share_class.common_code` 의 회사 재무를 쓴다(ET monitor/kr 머리말과 같은 규칙) |
| 금융업 | 매출 자리에 '영업수익'·'보험수익'·'순영업수익'(ET `NAMES`:37 동의어) — 매출총이익·EBITDA·EV/EBITDA 는 `na_reason=financial_sector`(KSIC 64~66 [확인 필요]) |
| TTM | 같은 기준(fs_div) 4개 연속 분기 합. 하나라도 None 이면 None |
| 비율 | OPM = TTM 영업익 ÷ TTM 매출, NPM = TTM 지배순익 ÷ TTM 매출, ROE = TTM 지배순익 ÷ 평균 지배자본(기초·기말 — 4분기 전 분기말과 현재), ROA = TTM 당기순익 ÷ 평균 자산, 부채비율 = 부채총계 ÷ 자본총계, 순차입금비율 = 순차입금 ÷ 자본총계, 매출·영업익 YoY(분기 단독 vs 전년 같은 분기), EPS_TTM = Σ 분기 기본주당이익(4개), BPS = 지배자본 ÷ (보통주 발행 − 보통주 자기주식)(DART 주식총수). 분모 ≤ 0 이면 None + 사유 |
| 품질 | 원천 DART → ok. 3개월 대조 차이·결산월 추정·OFS 폴백 혼재 → estimated |

### 4.6 dart-report 모듈화와 골든

**함수 매핑과 바뀌는 것**

| legacy | kbj | 바뀌는 것 |
|---|---|---|
| DR `statements.*` | `kbj.engines.financials.statements` | `fetch_periods` 가 `StatementSource` Protocol 을 받는다(`DartClient` 그대로 맞음). 본문 그대로 |
| DR `costs.*`·`bridge.*`·`orders.*` | `kbj.engines.financials.{costs,bridge,orders}` | 원문 접근은 `DocumentSource` Protocol. `verbose` `print` → `log: Callable[[str], None]`(기본 아무것도 안 함). 정규식·버킷 순서·표 고르기(기대 총비용 ±5%·파일 하나만) 그대로 |
| DR `excel.build_workbook` | `kbj.reports.dart_excel.excel.build_workbook` | `generated_at` 인자(요약 '생성 시각' :107). 파일 경로 대신 버퍼도 받는다 |
| DR `run.main` | `kbj.reports.dart_excel.pipeline.collect_inputs` + `render` | `date.today()`(:65 — 연도 기본값·수주 기간) → `now` 주입. 경고 문구·순서 그대로. 결과 payload(JSON) 추가 — 엑셀과 **같은 입력**에서 만든다(화면과 엑셀의 숫자가 갈라지지 않게) |
| DR `config/mapping.yaml` | `config/dart_report/mapping.yaml` | 경로만 |
| DR `app.py`(Streamlit) | 페이지 8 '리포트' 탭 | 삭제 |

**골든 3층**

| 층 | 입력 | legacy 쪽 | kbj 쪽 | 커밋 |
|---|---|---|---|---|
| **G1 빌더** | 합성 입력 8벌 — ① DR `tests_smoke.py` 그대로(14분기·4연도·브릿지 9단계) ② OFS 폴백 ③ 직전 분기 없음(`derived`) ④ 수주 없음 ⑤ 미매핑 0(“없음 — 모든 계정이 매핑되었습니다.”) ⑥ 잔차 큰 브릿지(경고) ⑦ 음수·적자 분기 ⑧ 진행 연도 1H/9M 라벨 | `build_workbook`(legacy 루트 venv, `cd DR`) | `kbj…excel.build_workbook(generated_at=고정)` | `tests/golden/dart_report/builder/<사례>.json`(정규화) + `inputs/<사례>.json` |
| **G2 파이프라인** | `p4_world` 의 합성 회사 2개의 DART 응답(재무제표 4년 × 4보고서 + CFS/OFS, 정기보고서 원문 zip — 비용 성격별 표·영업외손익 표(본문·연결·별도 3파일 — 이중 합산 함정), 공급계약 공시 + 정정 1건) | `run.main([...])` + 가짜 전송층의 `DartClient`(캐시 끔) | `collect_inputs` + `render` | `tests/golden/dart_report/pipeline/<회사>.json` |
| **G3 실데이터** [실측 필요 #30] | DART 실응답(공개 등급 — 커밋 가능) 2~3종목(DR CLAUDE 의 006110·005930 실검증 대상 [제안]) — 응답 본문만 저장, 원문 zip 은 필요한 파일만 다시 묶어 크기 상한 5 MB | `make_golden.py --legacy-commit <shim 전 커밋>` 이 `git archive` 로 그 커밋의 DR 을 임시 폴더에 풀어 실행(레포 상태를 바꾸지 않는다) | 같음 | `tests/fixtures/public/dart/report/**`, `tests/golden/dart_report/real/*.json` |

**정규화 표(`tests/oracles/xlsx_normalize.py`)**

| 대상 | 남기는 것 | 비교 |
|---|---|---|
| 통합 문서 | 시트 이름 순서 | 정확히 |
| 시트 | `max_row`·`max_column`, `sheet_view.showGridLines`, 고정 틀, 병합 범위, 열 너비(소수 2자리), 행 높이 | 정확히 |
| 셀(값이나 스타일이 있는 칸) | 좌표, 값 형(`n`·`s`·`f`·`b`·`d`), 값(수식은 문자열 그대로), 글꼴(이름·크기·굵게·기울임·색 rgb), 채움(패턴·전경색), 테두리 네 변(스타일·색), 정렬(가로·세로·줄바꿈), 서식 문자열 | 실수만 `isclose(rel 1e-9, abs 1e-9)`, 나머지 정확히 |
| 차트 | zip 의 `xl/charts/chart*.xml`·`xl/drawings/drawing*.xml`·`_rels` → `defusedxml` 로 읽어 속성 정렬·공백 제거한 정규 문자열(C14N) | 정확히(시리즈 참조 범위·`axId`·`crosses`·데이터 레이블 플래그·앵커 — DR CLAUDE 의 off-by-one·이중축 함정을 잡는다) |
| 제외 | `docProps/core.xml`·`app.xml`(생성·수정 시각), A열이 '생성 시각' 인 행의 B열 값(→ `"<generated_at>"`) | — |
| 수식 평가(`formula_eval.py`) | 모든 `f` 칸을 계산 — 오류 0, IFERROR 밖 `#DIV/0!` 0, 수식 개수 기록(DR CLAUDE "수식 197개") | 개수는 사례마다 정확히 |

**순서 규칙**: G1·G2 캡처와 커밋 → 엔진·빌더 이식 → 비교 녹색 → shim. `META.json` 에 legacy 커밋·시드·openpyxl 버전. shim 뒤 `make_golden.py --check` 는 legacy 가 kbj 를 부르므로 쓰지 않는다 — 골든 파일은 그 뒤 고치지 않는다(바꿀 일은 새 골든 세트 + ADR, P3 §4.1 5번과 같음).

### 4.7 컨센서스·리비전 (로그인 — KIS 교체)

| 항목 | 규칙 |
|---|---|
| 추정실적 | `parse_estimate`: 결산년월마다 실적(A)·추정(E) 구분 [확인 필요 #29]. 금액 단위(억원 추정)를 원으로. FY1 = 추정 중 결산월이 오늘 뒤인 가장 이른 해, FY2 = 그다음 |
| 단일 출처 여부 | KIS 추정실적이 **여러 증권사 평균(시장 컨센서스)인지 한국투자증권 자체 추정인지** [확인 필요 #29] — 자체 추정이면 화면 이름을 "KIS 추정"으로, 리비전은 "KIS 추정 변화"로 적는다(컨센서스라 부르지 않는다) |
| NTM | `ntm(t)` = w·FY1 + (1 − w)·FY2, w = (FY1 결산일 − t) ÷ 365 를 [0, 1] 로 자른 값. FY2 가 없으면 None(FY1 만 쓰는 값은 `fy1` 로 따로) |
| 목표가 컨센서스 | `target_consensus(t)`: 투자의견 행 중 t − 90일 ~ t, 증권사마다 **가장 늦은 행 하나**, 목표가 ≤ 0·의견 '미제시/Not Rated' 제외 → 평균·중앙값·최고·최저·증권사 수. n ≥ 3 → ok, 1~2 → estimated, 0 → None |
| 리비전 | 지표(FY1·FY2 의 매출·영업익·순익·EPS, 목표가 평균) × 창(7·30일): 기준선 = `snap_date ≤ t − W` 중 가장 늦은 스냅(SD 규칙 :169). pct = (현재 − 기준) ÷ |기준| × 100. 기준 0 → None, 부호가 바뀌면 `signal=TURN`(비율 대신). 분류 ±5·±15(SD `classify_signal`:76 — 경계는 위쪽 포함) |
| 투자의견 분포 | 창 안 증권사별 마지막 의견을 매수·중립·매도 [확인 필요 — 의견 코드 매핑] 로 센다 |
| 잃는 것(D-P4-10) | 분기 컨센서스(SD `consensus_quarterly` — 네이버 tb_type1) → 분기 서프라이즈 없음 · 컨센서스 최고·최저(SD `fwd_eps_1y_high`) → Bull = Base × 1.2 · 애널리스트 수 · 12개월 선행 EPS 직접값(→ NTM 계산값) · 네이버 리서치 리포트 목록(폐지). 대안 유료 출처(FnGuide DataGuide 등)는 [결정 필요] |
| 알림 | 관심종목 중 |pct| ≥ 5 인 것만 모아 하루 1통(`alert.revision`, `daily`) — 0건이면 보내지 않는다 |

### 4.8 밸류에이션 (로그인)

| 항목 | 정의 |
|---|---|
| 회사 시가총액 MC(t) | Σ_{회사의 상장 종류주식 c} 종가_c(t) × 상장주식수_c(t). 원천 = 원장(krx > kis — P3 D-P3-7). 우선주가 정지·결측이면 그 종류 빼고 `estimated` + 사유. 자기주식 차감 안 함 [확인 필요] |
| PIT TTM | t 에 공시된(`rcept_dt ≤ t`) 분기만으로 만든 마지막 연속 4분기 합(§4.5). 잠정실적은 쓰지 않는다(정기보고서만 — ok 값) |
| PER | MC ÷ TTM 지배순익. 지배순익 없음 → TTM 당기순익 + estimated. ≤ 0 → None, `na_reason=loss` |
| PBR | MC ÷ 최근 분기말 지배자본(같은 PIT). ≤ 0 → `na_reason=capital_impairment` |
| PSR | MC ÷ TTM 매출. 매출 ≤ 0(스팩 등) → `na_reason=no_revenue` |
| EV | MC + 총차입금(단기·장기 차입금·사채·유동성장기부채 — 리스부채 제외 [확인 필요]) − 현금및현금성자산 − 단기금융상품 + 비지배지분. **오버행 가치는 더하지 않는다**(CB 는 이미 사채) |
| EV/EBITDA | EV ÷ TTM EBITDA(영업이익 + 감가상각비 + 무형자산상각비 — 현금흐름표 조정 항목). D&A 가 없으면 None(영업이익으로 바꾸지 않음), 금융업 `na_reason=financial_sector`, 주요계정 층(전체 재무제표 없음) `na_reason=not_detail_tier` |
| 선행(forward) | PER_fwd = MC ÷ NTM 지배순익 추정(없으면 NTM EPS × 보통주 발행주식수 − 자기주식 [확인 필요]), PSR_fwd = MC ÷ NTM 매출, EV/EBITDA_fwd = EV ÷ FY1 EBITDA 추정(있을 때만). 추정 ≤ 0 → None |
| 일별 시계열 | `valuation_daily`: 상장일 또는 5년 전부터 매 거래일. 값이 거름값(`valuation.caps`) 밖이면 저장은 하되 `flags.out_of_cap` |
| 밴드 | 최근 `band_days`(1,240거래일)의 유효 값(None·거름값 밖 제외 — 수는 `n_excluded`)으로 p10·p25·p50·p75·p90(선형 보간 — x_(k) 를 0부터 센 순서통계량으로 h = (n − 1)·p, x_⌊h⌋ + (h − ⌊h⌋)(x_⌈h⌉ − x_⌊h⌋)), 최소·최대. 유효 일 < `min_days`(250) 이면 밴드 None(`na_reason=short_history` — 신규상장) |
| 현재 백분위 | rank_pct = 100 × #{x ≤ 현재} ÷ n(경험적). SD `percentile_rank`:334(조각 선형 보간)를 바꾼다 — legacy 와 값이 다르다(ADR 0016) |
| 밴드 차트 | 가격 공간으로: 밴드가(t) = 배수_p × 그날 주당 기준값(EPS_TTM·BPS) — 화면만, 저장 안 함 |
| 분할·병합 | 배수는 총액 ÷ 총액이라 주식 수 변화에 영향 없음. 주당 값(EPS·BPS)은 그날의 주식 수로 — 분할 직후 사례를 시험에 넣는다 |

### 4.9 시나리오 적정가 (구 TAM, 로그인)

| 단계 | Bear | Base | Bull |
|---|---|---|---|
| EPS | FY1 지배 EPS 추정 × 0.7 → (없으면) Base × 0.7 | FY1 지배 EPS 추정(> 0) → TTM EPS(DART, > 0) | 컨센서스 최고(없음 — D-P4-10) → Base × 1.2 |
| 배수 | PER 밴드 p25 | PER 밴드 p50 | 피어 평균 × 1.1(P5) → PER 밴드 p75 |
| 적정가 | EPS × 배수(원, 정수 반올림은 화면) | | |
| PBR 폴백 | Base EPS 가 없거나(적자) PER 밴드가 없으면: BPS × PBR 밴드 p25·p50·p75 (`method=PBR`) — SD `calc_pbr_based_tp`:250 |
| 산출 불가 | PBR 밴드도 없으면 `method=INSUFFICIENT`, 사유(신규상장·자본잠식·재무 미제출) |
| 업사이드 | (적정가 ÷ 전 거래일 종가 − 1) × 100 |
| 품질 | EPS 원천이 TTM·×배수 폴백 → estimated, 컨센서스 n < 3 → estimated. 각 칸에 `eps_source`·`multiple_source` 를 그대로(SD 와 같은 문자열 체계: `CONSENSUS`·`TTM_EPS`·`BASE_×0.7`·`5Y_P25` …) |

---

## 5. API

### 5.1 구성

P3 `api` 서비스 그대로(같은 프로세스·로그인·CSRF·보안 헤더·캐시). 새 라우터 3개(`stock`·`filings`·`watchlist`)를 `create_app` 이 붙인다. **외부 호출 없음**(계약 ⑩) — 요청이 DART·KIS 예산을 쓰지 않는다. 모든 `/api/*` 는 세션 필수(공개는 정적 사이트).

### 5.2 상태 규칙과 모델

| 경우 | HTTP | 본문 |
|---|---|---|
| 정상 | 200 | `Envelope[T]`(P3 — `source`·`as_of`·`quality`·`notes`·`generated_at`·`data`) |
| 적용 불가(스팩의 PER, 금융업 EV/EBITDA, 적자 PER, 상세 층 아님 …) | 200 | `data.applicable=false`, `data.na_reason`(`NaReason` — `loss`·`capital_impairment`·`no_revenue`·`financial_sector`·`short_history`·`not_detail_tier`·`not_in_consensus_universe`·`no_filings`·`spac`·`konex_no_consensus`), 값 칸은 null. **0 으로 채우지 않는다** |
| 아직 수집 안 됨 | 404 | `NoDataBody`(P3 — `code: no_data`, 메시지 "아직 없음 — <작업> <예정 시각>") |
| 모르는 코드 | 404 | `{code: "unknown_code"}` — 유니버스(전 거래일)에도 corp_code 표에도 없음 |
| 쿼리 오류 | 422 | P3 그대로 |
| 그 밖 | 500 | **실패**(완료 시험이 0 을 단언). 오류 문구에 원인 값·키를 싣지 않는다 |

| 모델 | 주요 필드 |
|---|---|
| `StockHeader` | `code`, `name`, `market`(KOSPI·KOSDAQ·KONEX), `kind`(common·pref·spac·reit), `common_code`, `corp_code \| None`, `status_flags`(정지·관리·정리매매·투자경고 — KIS·KRX 원천 [실측 필요 — P3 R22]), `listed_on`, `acc_mt`, `induty_code`, `last: SourcedQuote \| None`(종가·등락·시총 — 로그인), `is_watch`, `detail_tier: bool` |
| `PriceChart` | `bars: list[{date, o, h, l, c, v, turnover}]`, `sma: {20, 60, 120}`, `adjusted: false`(KRX 일별은 비수정 — 분할 사건 표시 `events`) |
| `Financials` | `basis`(q·a), `fs_div`, `currency`, `rows: list[{period, label, period_end, rcept_dt, revenue, op, ni, ni_owner, opm, npm, rev_yoy, op_yoy, derived, quality}]`, `ratios: RatioRow`, `checks: {diff3m_max_pct}` |
| `ReportView` | `built_on`, `fs_div_used`, `blocks: {quarterly, annual_costs, bridge, orders, quarterly_costs}`(엑셀과 같은 입력), `warnings`, `xlsx_available` |
| `Segments` | `year`, `rows: list[{segment, amounts}]`, `quality=estimated` |
| `Consensus` | `label`("컨센서스"·"KIS 추정" — §4.7), `fy: list[{fiscal_ym, is_estimate, revenue, op, ni, eps}]`, `ntm`, `target: TargetConsensus`, `opinions: {buy, hold, sell}`, `revisions: list[RevisionRow]`, `recent: list[OpinionRow](≤ 20)` |
| `Valuation` | `current: {per, pbr, psr, ev_ebitda, per_fwd, psr_fwd, ev_ebitda_fwd}`(칸마다 `applicable`·`na_reason`), `bands: list[BandRow]`, `series: list[{date, per, pbr}]`(주 1점으로 줄임), `mktcap_company`, `classes` |
| `Scenario` | `method`, `cases: {bear, base, bull: {eps, eps_source, multiple, multiple_source, tp, upside_pct}}`, `price` |
| `Overhang` | `summary`(공개 칸 + 로그인 칸 `amount`·`itm_shares`), `instruments: list[OverhangState + event 칸]`, `lockups`, `timeline` |
| `Insiders` | `windows: list[InsiderWindow]`(+ 로그인 `amount`), `reports: list[InsiderReport](≤ 50)`, `major_holders` |
| `Disclosures` | `rows: list[{rcept_no, rcept_dt, title, kind, is_correction, kw_score, importance, login_score, link}]` |
| `Earnings` | `prelims: list[PrelimRow + signal]`, `schedule: list[ScheduleRow]`, `surprises: list[SurpriseRow]` |
| `FilingsFeed` 등(페이지 5) | 같은 행 모양 + 필터 메타 |

### 5.3 라우트

| 메서드·경로 | 쿼리 | 응답 | 캐시(서버 TTL) |
|---|---|---|---|
| `GET /api/stock/search` | `q`(1~20자), `limit≤20` | `Envelope[StockSearch]`(유니버스 ∪ corp_code 상장사 — 이름·코드 부분 일치) | 600 s |
| `GET /api/stock/{code}/header` | — | `Envelope[StockHeader]` | 60 s |
| `GET /api/stock/{code}/chart` | `days=250(≤1240)` | `Envelope[PriceChart]` | 300 s |
| `GET /api/stock/{code}/financials` | `basis=q\|a`, `n=12(≤20)` | `Envelope[Financials]` | 데이터 버전 |
| `GET /api/stock/{code}/report` | — | `Envelope[ReportView]` | 데이터 버전 |
| `GET /api/stock/{code}/report.xlsx` | — | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`(`Content-Disposition` 파일명 `<회사>_<코드>_재무리포트.xlsx` — DR 이름 규칙) | `private, no-cache` |
| `GET /api/stock/{code}/segments` | — | `Envelope[Segments]` | 데이터 버전 |
| `GET /api/stock/{code}/consensus` | — | `Envelope[Consensus]` | 데이터 버전 |
| `GET /api/stock/{code}/valuation` | `metric=per\|pbr\|psr\|ev_ebitda` | `Envelope[Valuation]` | 데이터 버전 |
| `GET /api/stock/{code}/scenario` | — | `Envelope[Scenario]` | 데이터 버전 |
| `GET /api/stock/{code}/overhang` | — | `Envelope[Overhang]` | 300 s |
| `GET /api/stock/{code}/insiders` | `days=30\|90\|180` | `Envelope[Insiders]` | 300 s |
| `GET /api/stock/{code}/disclosures` | `days=30(≤365)`, `kind?` | `Envelope[Disclosures]` | 60 s |
| `GET /api/stock/{code}/earnings` | — | `Envelope[Earnings]` | 60 s |
| (P3) `GET /api/flows/stock/{code}` | `days` | 그대로 — 페이지 8 수급 패널 | — |
| `GET /api/watchlist` | `list=main` | `Envelope[Watchlist]`(코드·이름·추가일 + 헤더 요약) | 60 s |
| `GET /api/filings/feed` | `date?`, `kind?`, `importance?`, `market?`, `watch_only=false`, `limit≤200` | `Envelope[FilingsFeed]` | 30 s(장중) |
| `GET /api/filings/earnings` | `from`, `to`(≤ 31일) | `Envelope[EarningsList]` | 60 s |
| `GET /api/filings/overhang` | `days=7(≤90)`, `instrument?` | `Envelope[OverhangRecent]` | 300 s |
| `GET /api/filings/insiders` | `days=7(≤90)`, `min_ratio_pct?` | `Envelope[InsiderRecent]` | 300 s |
| `GET /api/filings/schedule` | `days=14(≤60)` | `Envelope[EarningsSchedule]` | 600 s |

`{code}` 는 `^[0-9A-Z]{6}$`(P3 규칙). 우선주 코드는 `share_class` 로 회사(`common_code`·`corp_code`)를 찾는다 — 재무·공시·오버행·내부자는 회사 것, 시세·수급은 그 코드 것. 데이터 버전 키 = 관련 데이터셋의 `ops.data_claim` 최근 `done_at`(P3 `api_data_version_key`).

---

## 6. 프런트 — 페이지 8 (종목 상세) + 페이지 5 (공시)

### 6.1 진입과 상태

| 진입 | 동작 |
|---|---|
| 해시 `#/p/8/<code>` | 직접 열기·공유. 코드가 없으면 관심종목 첫 종목(로그인) 또는 검색 안내(공개) |
| 검색 상자(페이지 8 머리) | 로그인: `/api/stock/search`, 공개: `data/corp_index.json`(상장사 이름·코드 — DART) |
| 관심종목 칩 줄(로그인) | `/api/watchlist` — 칩을 누르면 종목 전환 |
| 다른 페이지에서 | 페이지 2·4 표 행, 페이지 5 공시 행의 종목 → `openStock(code)` |

### 6.2 탭과 패널

| 탭 | 패널 | API(로그인) | 공개판 |
|---|---|---|---|
| 머리(늘 보임) | 종목명·코드·시장·종류 배지(우선주·스팩·리츠·KONEX)·상태 배지(정지·관리)·결산월·상장일, 종가·등락·시총 | `header` | 이름·코드·결산월(DART), 시세 자리 = TradingView 시세 위젯 |
| **개요** | 가격 차트(1년·SMA 20/60/120) | `chart` | TradingView 차트 위젯 |
| | 핵심 지표 카드(PER·PBR·ROE·OPM·오버행 비율·내부자 90일·목표가 업사이드) | `valuation`·`financials`·`overhang`·`insiders`·`consensus` | ROE·OPM·오버행 비율·내부자 주식 수만, 나머지 자물쇠 |
| | 수급(20일 막대 + 누적선 — P3 위젯 재사용) | `/api/flows/stock/{code}` | 자물쇠 |
| | 최근 공시 5건·잠정실적 | `disclosures`·`earnings` | `fin/<code>.json` |
| **재무** | 분기 12·연간 4 표(매출·영업익·순익·OPM·YoY), 막대 + OPM 선, 3개월값 대조 경고 | `financials` | 같음(`fin/<code>.json`) |
| | 비율 표(ROE·ROA·부채비율·순차입금비율·EPS·BPS) | `financials.ratios` | 같음 |
| | 부문 매출(estimated) | `segments` | 같음 |
| **리포트**(dart-report) | 블록 5개: 분기 실적(막대 + OPM 보조축) · 연간 비용구조(누적 막대) · 이익 브릿지(워터폴 — 잔차 행 강조) · 수주 현황(표 + 합계) · 분기 비용구조(100% 누적) + 경고 줄 + **엑셀 내려받기** | `report`·`report.xlsx` | 공개 데모 종목만 블록·xlsx, 그 밖은 "로그인 또는 데모 종목" 안내 |
| **컨센·밸류** | 컨센서스 표(FY1·FY2·NTM), 목표가(평균·중앙·범위·증권사 수), 의견 분포, 리비전(7·30일 — 신호 색) | `consensus` | 자물쇠("컨센서스 — 제3자 유료·재배포 금지") |
| | 밸류 밴드 차트(가격선 + 배수 밴드 5줄, 지표 선택) + 현재 배수·백분위 표(trailing·forward) | `valuation` | 자물쇠("시세 기반") |
| | 시나리오(Bear·Base·Bull 적정가·업사이드·근거) | `scenario` | 자물쇠 |
| **공시·지분** | 공시 목록(30·90·365일, 종류 필터, 중요도) | `disclosures` | 같음(최근 30일) |
| | 오버행(요약 비율·행사 가능 비율, 종목별 표, 청구기간 타임라인, 추정 보호예수) + 로그인 ITM·금액 | `overhang` | 주식 수·비율·타임라인 |
| | 내부자(30·90·180일 순증감 주식·비율, 보고 목록, 5% 대량보유) + 로그인 금액 | `insiders` | 주식 수·비율 |
| | 실적 일정(예고·법정 기한) | `earnings.schedule` | 같음 |
| (빈 자리) | 피어(P5)·검증 시트(P8)·수출 겹쳐 보기(P6) | — | '준비 중(Pn)' |

### 6.3 상태·오류 표시 (P3 §6.7 + P4)

| 상태 | 표시 |
|---|---|
| `applicable=false` | 패널·칸에 사유 문구(`na_reason` → 한국어 표: "적자 — PER 산출 안 함", "금융업 — EV/EBITDA 해당 없음", "상장 1년 미만 — 5년 밴드 없음", "상세 수집 대상 아님 — 관심종목에 넣으면 다음 수집(16:00·21:00)", "스팩 — 실적 지표 해당 없음") |
| 404 `no_data` | "아직 없음 — <작업> <예정 시각>" |
| `quality=estimated` | '추정' 배지(오버행 상한·보호예수 추정·결산월 추정·KIS 단일 추정·3개월 대조 차이) |
| 거래정지 | 머리 배지 + 시세 칸 `stale`("정지 — 마지막 거래 YYYY-MM-DD") |
| 패널 오류 | 그 패널 안 오류 줄 — 다른 패널은 계속(`Promise.allSettled`) |
| 숫자 서식 | 원 → 억·조는 화면에서만(P3 `ui/fmt.ts`). None·undefined 는 '—'(문자열 `NaN`·`undefined`·`null` 이 DOM 에 나오면 시험 실패) |

### 6.4 페이지 5 (공시·일정 — P4 패널)

| 위젯 | API(로그인) | 공개판 |
|---|---|---|
| 공시 피드(오늘·어제, 종류·중요도 필터, 관심종목만 토글(로그인), 행 → 페이지 8) | `/api/filings/feed` | `filings_feed.json`(키워드 점수만) |
| 잠정실적(오늘·이번 주 — YoY 분류 색, 관심종목 강조) | `/api/filings/earnings` | `earnings_prelim.json` |
| 새 오버행(최근 7일 CB·BW·EB·유증 — 비율) | `/api/filings/overhang` | `overhang_recent.json` |
| 내부자 큰 변동(최근 7일, 비율 하한) | `/api/filings/insiders` | `insider_recent.json` |
| 실적 일정(14일) | `/api/filings/schedule` | `earnings_schedule.json` |
| 경제 캘린더·뉴스 | — | '준비 중(P5)' |

### 6.5 시험 (vitest + jsdom)

| 시험 | 내용 |
|---|---|
| `p8_*.test.ts` | 탭마다 A 의 합성 응답으로 렌더, `na_reason` 문구, 404 no_data 문구, 한 패널 500 → 그 패널만 오류 줄 |
| `p8_public.test.ts` | 공개 모드: `/api` 호출 0, 로그인 패널 자물쇠, `fin/<code>.json` 없는 종목은 "공시 재무 없음" |
| `acceptance/p8_watchlist20.test.ts` | §0.3 — 20종목 × 5탭, `console.error` 0, 금지 문자열 0, axe serious·critical 0 |
| `p5_*.test.ts` | 필터·정렬·행 클릭 → `#/p/8/<code>` |
| `route.test.ts`·`pager.test.ts` | 해시 `#/p/8/005930`·잘못된 코드 무시 |

---

## 7. 공개 정적 사이트 내보내기 영향

| 파일 | 내용 | 원천 표(`pub_*` 만) | 크기 [추정] |
|---|---|---|---|
| `filings_feed.json` | 최근 2영업일 공시(접수번호·종목·제목·종류·키워드 점수·정정·링크) | `pub_filings.disclosure` | 약 0.6 MB |
| `earnings_prelim.json` | 최근 14일 잠정실적(값·YoY 분류) | `earnings_prelim` | 작음 |
| `overhang_recent.json` | 최근 30일 오버행 사건 + 회사 요약 비율 | `overhang_event`·`overhang_summary` | 작음 |
| `insider_recent.json` | 최근 30일 내부자 창 상위(비율) | `insider_window`·`insider_report`(보고자 이름은 넣지 않음 [제안]) | 작음 |
| `earnings_schedule.json` | 앞으로 30일 | `earnings_schedule` | 작음 |
| `corp_index.json` | 상장사 `[stock_code, corp_name, corp_cls, acc_mt]` | `corp_code`·`corp_profile` | 약 0.2 MB |
| `fin/<stock_code>.json` | 분기 12·연간 4·비율·부문·오버행 요약·내부자 창·최근 공시 20·실적 일정 | `pub_fin.*`·`pub_filings.*` | 종목당 약 6 KB × 약 2,600 ≈ 16 MB |
| `reports/<stock_code>.json`(+ `.xlsx` [제안]) | dart-report 블록 — `config/reports.yaml public_demo` 만 | `pub_fin.report` | 작음 |

| 규칙 | 내용 |
|---|---|
| 상한 | 합계 > `fin_max_total_mb`(25 MB) 이면 내보내기 **실패**(조용히 자르지 않음 — 절대 규칙 4). 넘으면 [결정 필요]: 분기 수 줄이기 또는 상위 N |
| 관심종목 | 공개 파일에 관심종목 표식·순서·목록을 넣지 않는다(시험: `prv_journal` 문자열·관심 플래그 0) |
| 내용 검사 | `source` 가 `DART` 가 아닌 값 0(P3 §7.4 '로그인 출처 이름 0' 확장 — `KIS`·`KRX`·`consensus`·`naver`·`fnguide` 0) |
| manifest | 각 파일 `source: "DART"`·`as_of`·`quality`, `fin/` 하위 개수·합계 크기 |
| 배포 | P3 그대로(`public.export` 05:30 → 고아 브랜치 → 수동 `pages.yml`) — Pages 활성은 P3 의 사용자 승인 항목 |

---

## 8. 시험·검증

### 8.1 골든 — dart-report (§4.6)

| 시험 | 내용 |
|---|---|
| `tests/golden/dart_report/test_builder_golden.py` | G1 8사례: kbj `build_workbook` → 정규화 → 커밋된 legacy 정규화와 필드 단위 비교. 실패 메시지 = (사례, 시트, 좌표·차트 파일, 속성, legacy, kbj) |
| `test_pipeline_golden.py` | G2 2회사 — 가짜 DART(`p4_world`) 위 kbj `collect_inputs`·`render` |
| `test_formulas.py` | 모든 사례: 수식 평가 오류 0, IFERROR 밖 `#DIV/0!` 0, 영업이익(역산) = 매출 − 비용합계(평가값과 입력으로 손계산한 값이 같다), 수식 개수 고정 |
| `test_golden_meta.py` | `META.json` legacy 커밋이 shim 전 커밋, 골든 파일 sha256 고정 |
| `test_real_golden.py` | G3 — fixture 가 없으면 skip(사유 "키 수령 후 #30"), 있으면 같은 비교 |
| legacy 다리 | `legacy/etf_traker/dart-report/tests_bridge.py`(1건): `dartreport.excel.build_workbook is kbj.reports.dart_excel.excel.build_workbook`, `tests_smoke.py` 출력 같음 |

### 8.2 파서·수집 (F·K)

| 시험 | 내용 |
|---|---|
| 잠정실적 | 단위 4종, 6열·2열 폼, '-'·괄호 음수, 연결/별도 본문 우선, 자회사·결산예고·전망 제외, 비12월 결산 기간 추정, 정정 |
| 오버행 | 구조화 응답 → 사건, 빈 칸 → 원문 폴백, 전환가 조정·청구 행사 연결 실패 → invalid + 사유 |
| 내부자 | 회사 보고 전체 중 새 접수번호만, 정정 대체, 증감 부호 |
| 피드 | 증분 멈춤(쪽 경계), 같은 분 두 실행 선점 중복 0, 07:00 다시 훑기, 020 → 그날 중단 |
| KIS 컨센서스 | 합성 응답 파싱, 단위, 빈 응답 `KisEmpty`, 증권사 의견 창·중복 |
| 공개 등급 실 fixture | 키 뒤 `tests/fixtures/public/dart/` 에 실응답(공개 등급 — 허용) — **보고자 이름은 합성으로 바꿔 넣는다** [제안] |

### 8.3 엔진 (E·R·K·V)

| 시험 | 내용 |
|---|---|
| `test_quarterly.py` | 누적 차분, OFS 폴백, 직전 없음, 3개월 대조 1% 경계, 비12월 결산 라벨, 금융업 동의어 |
| `test_ratios.py` | 부록 A §9 표 손계산 |
| `test_valuation_pit.py`(속성) | hypothesis: 미래(공시일 > t) 분기를 바꾸거나 지워도 ≤ t 값 불변. 분할(주식 수 × k, 가격 ÷ k) 앞뒤 배수 불변 |
| `test_band.py` | 선형 백분위 = numpy 'linear'(시험 안에서만 numpy), 250일 미만 None, 거름값 밖 제외 수 |
| `test_scenario.py` | 폴백 순서 표(컨센 → TTM → PBR → INSUFFICIENT), 적자·자본잠식·신규상장 |
| `test_revision.py` | SD 분류 표(경계 ±5·±15 포함), 기준선 없음·0·부호 전환 |
| `test_target.py` | 증권사별 마지막 의견, 90일 창, n 에 따른 품질 |
| `test_overhang_props.py` | 잔여 ≥ 0, 활성 구간, 유증 상장 뒤 0, EB 는 신주 합에 없음 |
| `test_importance.py`·`test_earnings_signal.py` | SD 표 그대로 |

### 8.4 완료 시험 — 관심종목 20개 (S)

`tests/acceptance/test_stock_detail_watchlist20.py` — 가짜 시계 2026-11-16(월, 3분기 보고 마감 직후) 기준으로 `p4_world` 를 만들고, 처리기(`filings.corp_code`·`company_profile`·`dart_feed`(하루치)·`fin.backfill`·`fin.quarterly`·`filings.derive`·`krx.daily`(P3)·`consensus.snapshot`·`fin.valuation_band`·`reports.dart_excel`·`earnings.alerts`)를 실제로 돌린 뒤(메모리 저장소 — 통합 판은 Pg) 페이지 8 라우트 14개 × 20종목을 부른다.

| # | 합성 종목(코드는 합성 — 실제 종목과 겹치지 않음을 `corp_code` 표로 확인) | 노리는 함정 | 기대(일부) |
|---|---|---|---|
| 1 | 대형 보통주(12월 결산·연결) | 기준 | 모든 섹션 200·applicable |
| 2 | 1번의 우선주(끝자리 ≠ 0, 이름 '…우') | DART corp_code 없음, 회사 시총 합 | 재무 = 1번 회사, 배수 = 1번과 같음 |
| 3 | 신규상장(20거래일) | 밴드·TTM 없음 | valuation `short_history`, scenario `INSUFFICIENT` 또는 PBR |
| 4 | 거래정지(최근 5거래일) | 시세 stale | header stale 배지, chart 마지막 = 정지 전 |
| 5 | 스팩 | 매출 0 | PER·PSR `spac`, 컨센 `not_in_consensus_universe` |
| 6 | 리츠(6월·12월 반기 결산 [추정]) | 분기 보고 없음 | 재무 반기 행만, `derived` |
| 7 | 3월 결산 | 회계연도 라벨·TTM | `FY25 2Q (25.07~25.09)` |
| 8 | 적자(TTM 순손실) | PER None | PER `loss`, scenario PBR |
| 9 | 재무 미제출(정기보고서 없음·관리종목) | 빈 재무 | financials 404 `no_data` 또는 `no_filings`, 공시 200 |
| 10 | KONEX | KRX `knx`, 컨센 없음 | consensus `konex_no_consensus` |
| 11 | 은행지주(금융업) | 매출 = 영업수익 | EV/EBITDA `financial_sector` |
| 12 | 연결 미작성(OFS) | 폴백 | `fs_div=OFS` |
| 13 | 자본잠식 | PBR None | PBR `capital_impairment` |
| 14 | CB 3·BW 1(리픽싱·일부 청구) | 오버행 잔여·가격 | 비율 = 손계산 |
| 15 | 내부자 대량 매도 + 5% 보고 | 창 합계·정정 | 90일 순증감 = 손계산 |
| 16 | 컨센서스 없는 소형 코스닥(상세 층 아님) | EV/EBITDA 층 | `not_detail_tier` |
| 17 | 액면분할 직후(×5) | 주당 값·밴드 | 배수 연속(속성) |
| 18 | 영숫자 단축코드(`0009K0` 꼴) | 코드 정규식·kind 판정 | 정상 |
| 19 | 외국기업(재무 통화 ≠ KRW [추정]) | 환산 안 함 | `currency` 표시, 배수 `notes` "통화 불일치" → applicable=false [확인 필요] |
| 20 | 정리매매(상장폐지 예정) | 상태 배지 | header 배지, 나머지 정상 |

단언: ① 500 = 0 ② 모든 200 봉투가 모델 검증 통과(source·as_of·quality) ③ 섹션 상태 = `p4_expect.yaml` 행렬 ④ 값이 들어간 칸에 `NaN`·`inf` 0 ⑤ 로그에 traceback 0(caplog) ⑥ 같은 시험을 공개 내보내기로: 20종목 중 DART 가 있는 종목의 `fin/<code>.json` 생성·스키마 통과.

### 8.5 하루 운영 시뮬레이션 확장 (S — `tests/sim`)

| 항목 | 단언 |
|---|---|
| 창 | 2026-11-16(월) 05:00 ~ 11-17 05:00 KST(분기 마감 직후 — 시즌 최대) |
| DART | `dart_feed` 780회 실행, 데이터 키(분·접수번호)마다 `done` 1회·중복 0, 어떤 1초 창에도 DART ≤ 8건, 하루 합 ≤ 예산(§3.5 시즌 값 + 여유), 020 주입 변형에서 그날 중단·다음 날 07:00 다시 훑기로 빠짐 0 |
| KIS | P3 단언 그대로 + `consensus.snapshot` 호출 = 2 × 유니버스, 초당 ≤ 4 |
| 알림 | 관심종목 잠정실적 → `alert.earnings` 1통(정정 1건 주입 → 여전히 1통), 리비전 → 하루 1통, 관심 밖 → 0 |
| 순서 | `fin.valuation_band` 08:50 가 `krx.daily` 뒤, `reports.dart_excel` 21:00 이 `fin.quarterly` 뒤 데이터 사용 |

### 8.6 API·공개 내보내기

| 시험 | 내용 |
|---|---|
| `test_routes_stock.py` 등 | 세션 없음 401, `unknown_code` 404, `na_reason` 200, no_data 404, 쿼리 422, 우선주 → 회사 재무 |
| `test_no_external.py`(P3) | P4 라우트 포함 — 외부 호출 0 |
| `test_public_writers.py` | 공개 작업 모듈의 SQL 스키마가 전부 `pub_`(정적) |
| `tests/unit/public_export/test_fin_json.py` | `prv_` 읽기 0, `source` 전부 DART, 크기 상한 초과 → 실패, 관심종목 흔적 0 |

### 8.7 CI 잡 (`.github/workflows/ci.yml`)

| 잡 | 바뀌는 것 |
|---|---|
| `lint` | 계약 ⑪~⑬ |
| `test-kbj` | 골든 G1·G2·수식·엔진·API·수용 시험(`tests/acceptance`) 포함 |
| `kbj-integration` | 0010~0013 적용·P4 저장소 Pg·수용 시험 Pg 판 |
| `sim` | `test_one_day_p4.py` |
| `canonical` | 줄어든 기준선, 그룹 `fnguide`(목표 0) |
| `web` | 페이지 5·8 시험·수용 시험·타입 신선도 |
| `legacy` | SD 검사 래퍼 줄어듦, dart-report shim 스모크 |

### 8.8 실데이터 검증 (키를 받은 뒤 — `docs/probe_results.md` §7 이어서)

| # | 할 일 |
|---|---|
| 28 | DART `list.json` 기본 정렬·`page_count` 상한·하루 건수 분포(시즌·평시), 정정 제목 머리 종류, 휴장일(12/31·근로자의 날) 접수 여부 |
| 29 | KIS 추정실적·투자의견·증권사별 TR id·경로·파라미터·필드·단위, **시장 컨센서스인지 자체 추정인지**, 분기 추정 유무, 커버리지(시총 상위 300 중 몇 종목) |
| 30 | 잠정실적 공시 원문 폼(기간 표기·열 순서), G3 실데이터 골든 캡처(2~3종목, 공개 fixture) |
| 31 | 주요사항보고서 구조화 엔드포인트 이름·필드(CB·BW·EB·유증·무증·자사주), 전환청구·전환가 조정·상환 공시의 제목·원문 형식 |
| 32 | `elestock`·`majorstock` 필드(증감·사유 칸 유무), 응답 크기(보고 전체가 오는지) |
| 33 | 주식총수 현황 엔드포인트·필드(보통·우선·자기주식) |
| 34 | 비12월 결산 회사의 `bsns_year`·`reprt_code` 뜻, `company.json` `acc_mt` 형식 |
| 35 | `fnlttSinglAcntAll` 의 지배순이익·감가상각·차입금 태그 분포(상세 층 350사), `rcept_no` 칸 유무(PIT 공시일) |
| 36 | Q4 비교(conflict_map §4 Q4 — 20종목 12분기, 차분 대 3개월값 대 SD 방식) — 결과로 ADR 0001 Q4 확정 |
| 37 | `python -m kbj.services.engine.verify_stock --watchlist main` — 실 관심종목 20개 수용(섹션 상태 분포만 기록) |

---

## 9. 위험·미정

| # | 위험·미정 | 영향 | 대응·기본값 |
|---|---|---|---|
| R1 | ⚠ KIS 컨센서스 TR id 가 레포 안에서 서로 어긋난다(datasets.py·conflict_map·가짜 서버) — 실측 전 | 컨센서스 섹션 전부 | D-P4-9 기본값 + 상수 한 곳(`kbj/data/private/kis/consensus.py`), 실측 #29 뒤 M 담당이 카탈로그·가짜 서버를 함께 고친다 |
| R2 | ⚠ KIS 추정실적이 시장 컨센서스가 아니라 한 증권사(KIS) 추정일 수 있다, 분기 추정이 없을 수 있다 | '컨센서스'·리비전 의미, 분기 서프라이즈 불가 | 화면 이름을 "KIS 추정"으로(§4.7), 서프라이즈는 YoY 로(D-P4-10). 유료 출처는 **[결정 필요]** |
| R3 | DART 구조화 엔드포인트(DS005·지분공시·주식총수) 이름·필드 미확인 | 오버행·내부자·분모 | 필드 이름을 모듈 상수 표 하나에, 빈 칸은 원문 폴백(SD 정규식), 실측 #31~#33 |
| R4 | `list.json` 정렬이 접수 내림차순이 아니면 증분 멈춤이 틀린다 | 공시 누락 | 시험이 정렬 가정을 고정, 실측 #28 에서 다르면 전 쪽 읽기 + `seen` 거름(호출 ↑ — §3.5 다시) |
| R5 | 잠정실적 기간 추정(본문 기간 표기 유무) | 분기 오배정 → YoY 오판 | 결산월 기반 추정 + `estimated`, 시즌 실측 #30 |
| R6 | 비12월 결산의 `bsns_year` 뜻 미확인 | 회계연도 라벨·TTM 이 한 해 밀림 | `fiscal_period` 한 함수, 실측 #34, 3월 결산 합성 사례(#7) |
| R7 | DR 의 `_pick` 0 누적값 문제(D-P4-8) 등 legacy 동작을 골든이 고정 | 드물게 틀린 분기값 | 골든 뒤 별도 ADR + 새 골든 세트로만 고친다. P4 안에서는 `pub_fin.quarterly` 의 3개월 대조가 그 경우를 `estimated` 로 드러낸다 |
| R8 | 엑셀 골든이 openpyxl 버전·차트 XML 직렬화에 민감 | 거짓 실패 | 정규화(C14N·속성 정렬), `META.json` 에 openpyxl 버전, 단일 lock 으로 고정. 버전을 올릴 때 골든 다시 캡처는 **legacy 커밋에서**(`--legacy-commit`) |
| R9 | 실데이터 골든 G3 은 키 전에는 없다 | "이식 전과 같다"의 실데이터 증명 늦음 | P4 완료는 G1·G2(합성)로, G3 은 체크리스트 #30 — shim 뒤에도 `git archive` 로 legacy 실행 가능 |
| R10 | 전체 재무제표 원시 행 저장 크기 | DB 디스크 | 상세 층만(약 350사), TOAST 압축, `ops.nightly` 크기 health. 넘으면 오래된 연도 원시 행 정리 [제안] |
| R11 | `fin.backfill` 이틀 동안 DART 예산 경합 | 장중 피드 지연 | `backfill_cap` 별도·우선순위 P4, 장중(07~20시)엔 백필이 리미터를 양보(P2 우선순위) |
| R12 | 밸류 PIT·오버행 EV 제외·백분위 정의 변경으로 legacy 화면과 값이 다르다 | 사용자 혼란 | ADR 0016 에 차이 표, 화면 도움말 한 줄 |
| R13 | 우선주 → 보통주 매핑 규칙(코드 끝자리) 예외(2우B 등) | 회사 시총·재무 오배정 | `share_class` 를 KRX 기본정보 이름 + 코드 규칙 **둘 다 맞을 때만**(P3 `kinds.of` 원칙), 못 맞추면 그 종목만 `na_reason` |
| R14 | SD `server.py` 함수 단위 삭제가 SD 검사 스크립트(AST 추출)를 깬다 | legacy CI 실패 | 삭제 전 스크립트별 추출 함수 목록과 대조(S), 걸리면 그 스크립트가 대체됐는지 보고 퇴역 또는 삭제 보류(R17 of P3 와 같음) |
| R15 | 보호예수 출처 없음 | 오버행의 보호예수 해제 물량 공백 | 추정 해제일만(D-P4-18), 예탁결제원 출처는 **[확인 필요·결정 필요]** |
| R16 | 내부자 '순매수'에 증여·상속·신규선임 등 비거래 증감이 섞임 | 신호 오판 | 이름을 "보고 기준 순증감"으로, 사유 파싱 비율 표시, 실측 #32 |
| R17 | 공개 `fin/*.json` 총량(약 16 MB 추정) | Pages 배포 시간·크기 | 상한 25 MB 에서 실패, 넘으면 **[결정 필요]** |
| R18 | 외국기업 재무 통화(CNY 등) | 배수 왜곡 | 환산하지 않고 `notes`·applicable=false [확인 필요] |
| R19 | 금융업 판정(KSIC) 경계 | EV/EBITDA 표시 오류 | `corp_profile.induty_code` 앞 두 자리 64~66 [확인 필요], 목록을 `config/fin.yaml` 에 |
| R20 | P3 미완료분에 기대는 것(§9.1) | 착수 지연 | D-P4-1 시작 조건, 웨이브 1 이 P3 산출을 검증하는 시험부터 |
| R21 | DART 접수일이 KRX 휴장일인 날(12/31 등) | `when: trading_day` 면 놓침 | `when: always` + 평일 cron(§0.4), 실측 #28 |
| R22 | 관심종목이 20개가 아닐 수 있다(SD 파일 크기 불명) | 완료 기준 해석 | 실데이터 수용(#37)은 "관심종목 전부(20개 이상이면 20개 무작위가 아니라 전부)" [제안] |

### 9.1 P3 에서 기대는 것 (아직 없을 수 있음 — 2026-10-07 작업 트리 기준)

| 기대 | 상태(작업 트리) | P4 영향 |
|---|---|---|
| P3 묶음 S: import-linter 계약 ⑧~⑩, `jobs.yaml` P3 작업 enabled, CI `web` 잡 | 아직(`pyproject.toml` 에 계약 ⑦ 까지) | 계약 ⑪~⑬ 번호·CI 잡 이름이 P3 결과에 맞춰야 함 |
| P3 W2: 페이지 1·2·4·10 위젯, `web/src/api/types.gen.ts`, `npm run gen:types` | 아직(`web/src/pages/` 에 `placeholder.ts`·`tradingview.ts` 뿐) | W2 의 타입 생성 절차를 P3 W2 결과대로 따른다 |
| `prv_market.stock_snapshot` 과거 행(KRX 백필 — 시총·상장주식수) | 처리기는 있음(`krx_daily.py`), 실행은 키 뒤(R11 of P3) | 5년 밴드는 백필 뒤에만(그 전엔 `short_history`) |
| `prv_market.universe` 상태 플래그(정지·관리 — KIS `iscd_stat_cls_code`) | [실측 필요 — P3 R22] | 머리 배지, 시험은 합성 |
| `/api/flows/stock/{code}`·`Envelope`·`NoDataBody`·`ApiRepos`·데이터 버전 캐시 | 있음(`kbj/services/api/*` — P3 작업 중) | 그대로 재사용 |
| `public_export` 등록 방식·`manifest`·`merge-public-data.mjs` 허용 목록 | 있음(작업 중) | X 가 확장 |
| SD `server.py` 함수 삭제(D-P3-18 이월) | 미착수 | P4 S 가 한다 |
| ADR 0008(로그인 API) | 파일 없음(0009~0012 만) | P4 ADR 은 0013 부터 — 번호 겹침 없음 |

### 9.2 이 설계로 남길 ADR (초안 → S 가 확정)

| ADR | 내용 | 초안 |
|---|---|---|
| 0013 | DART 공시 수집 구조 — 증분 피드·구조화 우선·원문 폴백·이벤트 데이터 키·DB 대기 폴링·공시 알림 정책(D-P4-3·4·14) | F |
| 0014 | dart-report 승격과 엑셀 골든(정규화 비교·3층·legacy 동작 고정·Streamlit 폐지) + 재무 2층·Q4 환산 하나(D-P4-5~8) | R |
| 0015 | 컨센서스 출처 교체(KIS) — 잃는 것·목표가 컨센 계산·리비전 정의·옛 네이버 데이터 이관 안 함(D-P4-9·10) | K |
| 0016 | 밸류에이션 PIT·회사 시총·EV 에서 오버행 제외·백분위 정의, 시나리오 적정가(구 TAM), 관심종목 원천과 20종목 완료 기준(D-P4-11~13·16) | V |

---

## 10. 사용자만 할 수 있는 것

| # | 할 일 | 기본값(사용자가 정하지 않으면) |
|---|---|---|
| U-P4-1 | **컨센서스 출처 결정**: KIS(무료·커버리지·분기 없음 가능) 그대로 갈지, 유료 데이터(FnGuide 등 — 약관상 로그인 전용) 계약을 검토할지 | KIS. 분기 서프라이즈는 YoY 로 대체 |
| U-P4-2 | 옛 SD 네이버 컨센서스·밴드·서프라이즈 이력 이관 여부 | 이관 안 함 |
| U-P4-3 | 실시간 텔레그램: 관심종목 잠정실적만(우선순위 ≤ 2) + 리비전 하루 1통 — 일반 공시 push 없음(SD 에서 사용자가 막았던 것 승계)이 맞는지 | 그대로 |
| U-P4-4 | 'TAM' 을 '시나리오 적정가'로 이름 바꾸는 것, 진짜 시장 규모 TAM 이 필요한지 | 이름 바꿈, 시장 규모는 만들지 않음 |
| U-P4-5 | 관심종목 파일 제공: SD 브라우저 localStorage 또는 `cache/server_watchlist.json`(Render·맥)을 VM 으로 — `python -m kbj.services.watchlist import` | 없으면 실데이터 수용(#37) 보류 |
| U-P4-6 | 공개 데모 종목(`config/reports.yaml public_demo` — DART 엑셀·블록을 공개판에 올릴 2~5종목) | 비움(공개판 리포트 탭은 안내만) |
| U-P4-7 | 공개판 `fin/*.json` 범위(전 상장사 vs 상위 N) | 전 상장사, 25 MB 상한 |
| U-P4-8 | 보호예수 출처(예탁결제원 등) 약관 확인·키 신청 | 추정 해제일만 |
| U-P4-9 | 키 수령 후 실측 #28~#37 실행 승인(DART·KIS 실호출, 실응답 공개 fixture 커밋 — DART 만) | — |
| U-P4-10 | (P3 이월) Pages 활성·배포 키·디스패치 토큰 | P3 그대로 |

---

## 부록 A. `docs/metrics.md` 에 넣을 지표 정의 (묶음 M 이 §9~§15 로 옮긴다)

> 공통: 금액은 원 단위 정수로 저장, 화면에서만 억·조. 비율은 % 실수(저장은 반올림 없이). 모든 값에 `source`·`as_of`·`quality`. 분모가 0 이하이거나 입력이 하나라도 없으면 **None + 사유**(0 으로 채우지 않는다). 날짜 기준 계산은 **시점 일관(PIT)**: t 의 값은 t 까지 공시된 자료만 쓴다.

### §9 재무(공개 — DART)

| 지표 | 공식 | 입력 | 단위 | 예외 | 품질 | 시험 |
|---|---|---|---|---|---|---|
| 분기 단독 손익 | 1Q = 1분기 누적, kQ = k분기 누적 − (k−1)분기 누적, 4Q = 연간 − 3분기 누적 | `fnlttSinglAcntAll`(손익은 `thstrm_add_amount` 우선) 또는 `fnlttMultiAcnt` | 원 | 직전 누적 없음 → None·`derived` | ok / 3개월값과 1% 넘게 다르면 estimated | 누적 차분·4Q·OFS |
| TTM X | 같은 `fs_div` 연속 4분기 X 합, 각 분기 rcept_dt ≤ t | 분기 단독 | 원 | 하나라도 None → None | 구성 중 가장 나쁜 것 | PIT 속성 |
| 지배순이익 | `ProfitLossAttributableToOwnersOfParent`, 없으면 당기순이익 | 재무제표 | 원 | 대체 시 estimated + "비지배 포함" | | |
| EBITDA | 영업이익 + 감가상각비 + 무형자산상각비(현금흐름표 조정, 분기 차분) | 전체 재무제표 | 원 | D&A 없음 → None(영업이익으로 대체 금지), 금융업 해당 없음 | | |
| 순차입금 | 총차입금(단기차입금·장기차입금·사채·유동성장기부채) − 현금및현금성자산 − 단기금융상품 | 분기말 상태표 | 원 | 리스부채 제외 [확인 필요] | | |
| OPM·NPM | TTM 영업익 ÷ TTM 매출, TTM 지배순익 ÷ TTM 매출 | | % | 매출 ≤ 0 → None | | |
| ROE | TTM 지배순익 ÷ ((4분기 전 분기말 지배자본 + 현재 지배자본) ÷ 2) | | % | 평균 ≤ 0 → None(자본잠식) | | |
| ROA | TTM 당기순익 ÷ 평균 자산총계 | | % | | | |
| 부채비율 | 부채총계 ÷ 자본총계 | 분기말 | % | 자본 ≤ 0 → None | | |
| EPS_TTM | 최근 4분기 기본주당이익 합(분기 단독) | 재무제표 | 원/주 | 하나라도 없으면 None | | |
| BPS | 지배자본 ÷ (보통주 발행 − 보통주 자기주식) | 재무제표 + 주식총수 | 원/주 | | | |
| YoY | (이번 분기 − 전년 같은 분기) ÷ |전년 같은 분기| | 분기 단독 | % | 전년 0 → None, 부호 바뀌면 '흑자전환'·'적자전환' 문자 | | |

### §10 컨센서스·리비전(로그인 — KIS)

| 지표 | 공식 | 예외·품질 |
|---|---|---|
| FY1·FY2 | 추정(E) 중 결산일이 오늘 뒤인 첫 해·둘째 해 | 출처가 단일 증권사면 이름 "KIS 추정" |
| NTM | w·FY1 + (1−w)·FY2, w = clip((FY1 결산일 − t) ÷ 365, 0, 1) | FY2 없음 → None |
| 목표가 컨센서스 | 최근 90일 증권사별 마지막 목표가(> 0, 미제시 제외)의 평균·중앙값·최고·최저·수 | n ≥ 3 ok, 1~2 estimated, 0 None |
| 리비전(%) | (현재 − 기준) ÷ |기준| × 100, 기준 = t − W 이전 가장 늦은 스냅(W = 7·30일) | 기준 0 → None, 부호 전환 → `TURN` |
| 리비전 신호 | ≥ 15 강한상향, ≥ 5 상향, > −5 중립, > −15 하향, 그 밖 강한하향 | SD 경계 그대로 |

### §11 밸류에이션(로그인)

| 지표 | 공식 | 예외 |
|---|---|---|
| 회사 시총 MC(t) | Σ 종류주식(보통·우선) 종가 × 상장주식수 | 종류 하나 결측 → estimated |
| PER (trailing) | MC ÷ TTM 지배순익 | ≤ 0 → None(`loss`) |
| PBR | MC ÷ 최근 분기말 지배자본 | ≤ 0 → None(`capital_impairment`) |
| PSR | MC ÷ TTM 매출 | ≤ 0 → None |
| EV | MC + 순차입금 + 비지배지분(오버행 가치 더하지 않음) | |
| EV/EBITDA | EV ÷ TTM EBITDA | EBITDA ≤ 0·없음 → None, 금융업 해당 없음 |
| forward PER·PSR·EV/EBITDA | MC ÷ NTM 지배순익 추정, MC ÷ NTM 매출 추정, EV ÷ FY1 EBITDA 추정 | 추정 ≤ 0 → None |
| 밴드 | 최근 1,240거래일 유효 일별 값의 p10·25·50·75·90(선형 보간) | 유효 < 250일 → None(`short_history`), 거름값 밖 제외(수 표시) |
| 현재 백분위 | 100 × #{x ≤ 현재} ÷ n | |

### §12 오버행(공개 — 금액·ITM 은 로그인)

| 지표 | 공식 | 예외·품질 |
|---|---|---|
| 잔여 권면 | 권면총액 − Σ 청구·상환(≤ t) | 청구 공시 파싱 실패 → 권면총액(상한), estimated |
| 잠재 주식 | CB 잔여 ÷ 현재 전환가, BW 잔여 ÷ 행사가, 유증 신주(상장 전까지), EB 는 교환 대상(신주 아님 — 따로) | 버림 |
| 오버행 비율 | Σ 잠재 신주(활성) ÷ 보통주 발행주식수 × 100 | 분모 출처 표시(공개 DART 주식총수 / 로그인 KRX) |
| 행사 가능 비율 | 같은 식, 청구기간 시작 ≤ t 인 것만 | |
| 오버행 금액(로그인) | 잠재 주식 × 전 거래일 종가 | estimated |
| ITM(로그인) | 종가 ≥ 현재 전환·행사가 | |
| 추정 보호예수 해제일 | 제3자배정 신주 상장일 + 12개월 [확인 필요] | estimated |

### §13 내부자(공개 — 금액은 로그인)

| 지표 | 공식 | 예외·품질 |
|---|---|---|
| 보고 기준 순증감(주, W일) | Σ 임원·주요주주 소유보고 증감(공시일 ∈ [t−W, t], 정정은 늦은 것만) | 사유 미구분 포함 — 이름에 적는다 |
| 순증감 비율 | 순증감 ÷ 보통주 발행주식수 × 100 | |
| 사유 파악률 | 사유를 아는 증감 주식 ÷ 전체 증감 주식(절댓값) | |
| 금액(로그인) | Σ 증감 × 보고일 종가 | estimated(실거래가 아님) |

### §14 잠정실적·서프라이즈

| 지표 | 공식 | 등급·예외 |
|---|---|---|
| 잠정 YoY·QoQ | (당기 − 전년동기) ÷ |전년동기|, (당기 − 직전분기) ÷ |직전분기| | 공개. 0 분모 → None |
| YoY 분류 | 흑자전환·적자전환(1) > 적자 50% 이상 축소(2) > ±20%(1) > ±10%(2) > 인라인(5) — 영업이익 기준, 영업이익 없으면 매출 | 공개 |
| 서프라이즈(%) | (실적 − 분기 컨센서스) ÷ |컨센서스| × 100, SD 분류 12종 | 로그인. 분기 컨센서스 없으면 계산 안 함 |
| 알림 후 성과 | 3·5·7거래일 뒤 종가 ÷ 알림 직전 마감 종가 − 1 | 로그인. 정지로 종가 없음 → None |

### §15 시나리오 적정가(구 TAM, 로그인)

| 항목 | 공식 |
|---|---|
| Base | (FY1 지배 EPS 추정 > 0 → 그것, 아니면 TTM EPS > 0) × PER 밴드 p50 |
| Bear | (FY1 × 0.7 → Base EPS × 0.7) × PER p25 |
| Bull | (컨센서스 최고 → Base EPS × 1.2) × (피어 평균 × 1.1 → PER p75) |
| PBR 폴백 | BPS × PBR p25·p50·p75 (EPS 없음·PER 밴드 없음) |
| 업사이드 | 적정가 ÷ 전 거래일 종가 − 1 |
| 품질 | 폴백·n<3 → estimated, 근거 문자열(`eps_source`·`multiple_source`) 필수 |

**시험(metrics §5 에 추가)**: 손계산 1건씩(PER·PBR·EV/EBITDA·밴드·NTM·목표가·오버행 비율·내부자 창·YoY 분류·시나리오), PIT 속성(미래 공시 변경이 과거 값을 바꾸지 않음), 분할 불변, 0·음수 분모 None, `na_reason` 표.

---

## 부록 B. 이 설계가 읽은 근거

| 구분 | 경로 |
|---|---|
| KBJ 문서 | `CLAUDE.md`, `docs/PLAN.md`(§2·§4·§8), `docs/DATA_TIERS.md`(§1·§2·§3·§4), `docs/conflict_map.md`(§0·§1.5·§1.12·§1.13·§1.16·§4 Q4), `docs/inventory.md`(DART 행·cron·텔레그램 #10·#16·기능 표), `docs/secrets.md`, `docs/metrics.md`(구조), `docs/probe_results.md` §7, `docs/p2_design.md`(§1·§6·§8 P4 계획·R20), `docs/p3_design.md`(전체), `docs/adr/0001`·`0005`·`0012` |
| KBJ 코드 | `kbj/data/public/dart/{client,datasets,disclosures,corp_code}.py`, `kbj/data/private/kis/datasets.py`(consensus_estimate), `kbj/data/catalog.py`, `kbj/data/spec.py`(AsOfKind `event`), `kbj/store/migrations/0001·0003·0004·0007~0009`, `kbj/store/repos/*`(이름), `kbj/store/legacy_import/mappings.py`(LATER·NEVER), `kbj/services/scheduler/{registry,handlers,runner}.py`(triggered_by 미구현, JobContext), `kbj/services/notifier/client.py`(notify 시그니처), `kbj/services/api/{app,models/common,readers/_common,routes/flows}.py`, `kbj/engines/board/kinds.py`, `kbj/config/markets.py`, `config/{jobs,notify,limits}.yaml`, `pyproject.toml`(의존성·계약 ①~⑦), `scripts/check_canonical.py`(그룹)·`canonical_baseline.txt`, `tests/fakes/{dart_server,kis_server}.py`, `tests/fixtures/public/README.md`, `tests/fixtures/synthetic/{ledger_gen.py,kis/README.md}`, `tests/unit/scheduler/legacy_jobs.txt`, `web/src/app/{pages,pager,types}.ts` |
| dart-report | `DR:{CLAUDE.md,README.md,인수인계.md,run.py,app.py,tests_smoke.py,requirements.txt,config/mapping.yaml}`, `DR:dartreport/{client,statements,costs,bridge,orders,excel}.py`(함수 목록·수식 형태 12종·벽시계 :107), `legacy/etf_traker/MIGRATION.md`(dart-report 행), `scripts/test_legacy.sh`(스모크 기준) |
| SD | `consensus_collector.py`·`consensus_quarterly_collector.py`·`consensus_snapshot_collector.py`(네이버), `revision_calculator.py`(임계·기준선), `valuation_calculator.py`(밴드·EV 오버행), `tam_modeler.py`(시나리오), `earnings_parser.py`·`earnings_signal_classifier.py`·`earnings_alert_writer.py`·`earnings_telegram_sender.py`, `overhang_parser.py`, `dart_collector.py`, `server.py`(공시 점수 :9920~10010, 폴링 :10166, 관심종목 :5821·:5837, DART 분기·부문 :10425·:10454, 실적 일정 :11047, 라우트 목록, 네이버·스크랩 줄의 함수 — AST 로 집계), `MIGRATION.md`, `static/js/app.js`(관심종목 localStorage) |
| ET board | `board/ingest/{dart,financials,triggers}.py`(3개월값 대조 :83~152), `board/run.py:_dart_ksic`:837, `board/tests/test_{dart_for,financials,split_actions,triggers*}.py`(수), `monitor/kr/financials.py`(우선주·화면 계약) |
