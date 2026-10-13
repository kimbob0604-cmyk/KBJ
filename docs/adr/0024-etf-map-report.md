# ADR 0024 — ETF 맵·보유 탐색·액티브 동시 편입·ETF 리포트(분류 정본 하나·리포트 08:20·SD ETF 맵 폐기) (2026-10-13)

상태: **채택**(2026-10-13 — 초안 P5 묶음 ETF, 번호·상태는 웨이브 3 WRAP 이 확정 — 아래 'WRAP 확정 메모').
근거: `docs/p5_design.md` D-P5-13, §1.4·§4.4·§5·§6, 부록 A §19(→ `docs/metrics.md` §19), 사용자 결정 2026-10-13 Q5(ETF
리포트는 아침 브리핑과 따로 08:20 한 통), ADR 0001 U4(네이버 스크래핑 금지)·Q8(ETF 분류 정본), ADR 0010(ETF 수급·운용사
구성종목 — P3), ADR 0018(장 마감 뒤 발송 16:00 — 이 리포트는 아침이라 해당 없음).
구현(묶음 ETF): `kbj/engines/etf/{map,holders,report}.py`, `kbj/reports/etf.py`(작업 `etf.report`),
`kbj/services/api/readers/etf_p5.py`, `web/src/pages/p10/{index,map,holdings,consensus}.ts`·`p10_etf.ts`(고리),
`web/src/pages/p8/etf_holders.ts`(고리는 WRAP).

## 1. 맥락

- P3 가 ETF 수급(자금 흐름·유형별·괴리율)과 운용사 구성종목 변동(`prv_etf.change_log`)을 이미 만들었다. 페이지 10 에
  남은 것은 ETF 맵·보유 탐색·액티브 동시 편입과, ET `etf_tracker_v9` 가 보내던 텔레그램 ETF 리포트다.
- 분류 사본이 둘 있었다: 정본 `kbj.engines.etf.types`(ET `themes.py` 승격 — P3)와 ET `monitor/kr/etf.py:classify`(네이버
  탭코드·이름 키워드). SD 의 ETF 맵(`server.py:api_etf_map`·`_scrape_etf_price`)은 네이버를 긁는다(U4 위반).
- ET 리포트(`etf_tracker_v9/report.py`)는 SQLite(`etf_px`·`etf_aum`·`change_log`·`holding`)에서 입력 D 를 만들던
  `dash.py:collect` 위에 섰다. 원본 시험은 import 스모크뿐이다.

## 2. 결정

1. **분류 정본은 하나** — 맵의 묶음(유형 7종·테마)은 `types.etf_type`·`types.classify` 만 쓴다. 메타에 저장된 값(수집 때
   `typed_meta`)이 있으면 그것, 없으면 같은 정본 함수. ET `monitor/kr/etf.py` 에서는 **묶음·기간 계산만** 승격했다
   (`build_items`·`filter_universe`·`build_groups` → `kbj/engines/etf/map.py`). 그 파일의 분류 단언(원본 시험 `분류.*`)은
   폐지 목록이다(정본 시험은 P3 `test_types.py`).
2. **ETF 맵**(metrics §19): 칸 = ETF, 크기 = 순자산(마지막 확정일 — 보고 순자산이 없으면 좌수 × NAV), 색 = 기간 수익률
   (1D·1W·1M·3M = 5·21·63 영업일 — ET `engine.period_return` 규칙: 기준일이 비면 이전 관측일, 사이 결측 > 20 이면 없음),
   종가는 분할·병합 보정(metrics §8.2). 유니버스 = 순자산 50억↑·20일 거래대금(중앙값) 1억↑(ET 화면 문구), 밖은 수만 notes 에.
   테마 묶음은 국내주식(국내 대표지수·국내 테마)만. 레버리지·인버스는 기본 숨김(토글). **ET 와 다른 점**: 이력이 기간보다
   짧은 ETF 는 빼지 않고 신규 상장(회색 — 수익률 없음 + 꼬리표)으로 남긴다.
3. **보유 탐색**: ETF(펀드 id 또는 상장 코드) → 마지막 PDF 스냅 구성종목 + 마지막 변동 실행의 종류(NEW·IN10·ADD·CUT),
   종목 → 담은 ETF(펀드마다 마지막 스냅 — 비중 내림차순, 같으면 액티브 먼저). 운용사 PDF 기준일이 달라도 섞지 않는다.
   스냅이 하나도 없으면 404 `no_data`, 있는데 담은 ETF 가 없으면 빈 목록(200).
4. **액티브 동시 편입**: 같은 날(run_date) 액티브 ETF 2개 이상이 NEW·IN10·ADD 한 종목(1개는 제외 — ET `theme_moves` 의
   `HAVING n_etf>=2` 를 액티브·편입 방향으로 좁힘). `days=N` 은 마지막 변동 실행 N 회 — 날마다 따로 세고(다른 날을 합쳐
   '동시'로 세지 않는다) 조건을 채운 날의 펀드·종류를 종목별로 합친다.
5. **ETF 리포트(`etf.report` 08:20 거래일, notify `etf.report`)**: 섹션 본문은 ET `report.py` **그대로**(머리·신규 편입·
   보유비중 변화·여러 ETF 같은 방향·제외·당일 등락·기간별 수익률·자금 유입·이탈·거래 급증/신규 상장 — 문구·이모지·HTML
   이스케이프·3,900자 섹션 묶기). 바뀐 것은 입력 D 를 만드는 쪽(`report.assemble` — kbj 행 자료형에서, 순수):
   - 기준일 = 실행일의 직전 완료 영업일 이하 마지막 ETF 일별(장중 값을 그날 수치로 쓰지 않는다). 전 영업일이 아직 없으면
     있는 마지막 날 + 머리 줄 안내.
   - 수익률·등락은 분할 보정 종가, 자금 흐름은 P3 metrics §4(`flows.daily_flow` — 좌수 변화 × NAV, 분할 보정), 금액은 억원.
   - 레버리지·인버스 판정은 정본 유형(ET `dash.is_lev` 의 탭코드·낱말 대신).
   - 신규 상장 = 메타 상장일(없으면 창 안에서 시작한 첫 시세) 60일 안.
   - **변동 없는 날도 한 통**(머리 줄 '감지된 변동 없음') — 조용히 끝내지 않는다.
   - 묶은 메시지들을 이어 붙여 **대기열 한 건(한 통)** 으로 넣는다(dedup daily + subject = 기준일). 4,096자 나눔은
     notifier 가 HTML 태그를 지키며 한다. 대기열 거절·notify 없음·ETF 일별 없음은 실패로 끝낸다.
   - 아침 브리핑(`brief.morning` 08:10)과 따로다(사용자 Q5) — 브리핑 하루 1회 규칙에 세지 않는다.
6. **SD ETF 맵(`api_etf_map`·`_scrape_etf_price` — 네이버)은 버린다.** `etf_tracker_v9` 의 `tracker`·`report`·`market`·
   `render`·`dash`·`live_update` 는 이 묶음(+ P3 `etf.collect`)이 대체한다 — 삭제와 import 스모크 13 → 7 은 WRAP.

## 3. 결과

- 화면: 페이지 10 에 'ETF 맵'(트리맵 — 묶음마다, 색 범위 공통, 칸 누르면 보유 탐색)·'보유 탐색'·'액티브 동시 편입' 세
  패널(전부 로그인 — 공개판 자물쇠, 요청 없음). 페이지 8 '담은 ETF' 모듈은 만들어 두고 고리는 WRAP.
- API: `/api/etf/map`·`/fund/{code}/holdings`·`/holders/{code}`·`/active-consensus`(모델·라우트는 FND 그대로).
- 공개 산출물 영향 없음(페이지 10 은 로그인 전부 — DATA_TIERS).

## 4. 잃는 것·남은 것

- ET 화면의 상대강도·YTD·운용사 묶음·괴리율 중앙값(맵 칸)은 옮기지 않았다(괴리율은 P3 패널).
- ET 리포트의 '장중 갱신'(`live_update` — 15분 네이버 목록)은 없다(장중 iNAV 는 P3 감시 그대로).
- 리포트 기준 'KODEX 200' 은 이름(없으면 코드 069500)으로 찾는다 — 유니버스에 없으면 '–'.
- 시험: `tests/unit/engines/etf/test_{map,holders,report}.py`, `tests/unit/reports/test_etf_report.py`,
  `tests/unit/api/test_routes_etf_p5.py`, `web/test/pages/{p10_etf_p5,p8_etf_holders}.test.ts`(합성 p5_world 만).

## WRAP 확정 메모(2026-10-13 — 웨이브 3)

1. 상태 **채택**. 등록부 `etf.report` 켬(평일 08:20 — 아침 브리핑과 따로, 사용자 결정 Q5). 처리기는 저장소만 읽는다(계약 ⑯).
2. 페이지 8 '개요' 탭에 '담은 ETF'(`web/src/pages/p8/etf_holders.ts`) 고리를 달았다 — 로그인 전용, 공개 빌드는 모듈을 싣지 않고
   같은 제목 자물쇠만(공개 번들 예산).
3. legacy `etf_tracker_v9/{tracker,report,render,dash,live_update}.py`·`run.sh` 삭제(import 스모크 13 → 8). `market.py` 는
   monitor/kr 이 아직 import 해서(테마 — 사용자 별도 진행) 남긴다. SD `api_etf_map`·`_scrape_etf_price` 삭제.
4. 하루 운영 시뮬레이션: `etf.report` 08:20 1통(합성 P5 세계 ETF 구성·변동을 창 앞에 실었다).
