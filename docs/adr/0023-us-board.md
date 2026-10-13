# ADR 0023 — 간밤 미국 신고가 승격(Nasdaq·Stooq·Yahoo → 252봉 엔진 → us_summary·us_brief), 금융위 일별 대조 (2026-10-13)

상태: **채택**(2026-10-13 — 초안 P5 묶음 US, 번호·상태는 웨이브 3 WRAP 이 확정 — 아래 'WRAP 확정 메모').
근거: `docs/p5_design.md` §1.6·§1.9·§3.2·§4.6·§5, D-P5-18·19, 부록 A §23, ADR 0001 Q10(미국장 252봉), ADR 0017(국장 3축 — 미국장
제외), ADR 0018(장 마감 뒤 발송 16:00 — 아침 발송은 해당 없음), 사용자 결정 2026-10-13 Q6(간밤 미국 수집을 켠다 — 본인 화면·
텔레그램만, 원자료 비공개). 원본 ET `board/us/{sources,engine,sectors,brief,build,pipeline,demo}.py`·`config/us.yaml`·
`us/knowledge/*.yaml`.
구현: `kbj/data/private/{nasdaq,stooq}/**`, `kbj/engines/us/{config,engine,board,brief,sectors}.py`,
`kbj/services/collectors/{us,fsc_daily}.py`, `kbj/services/api/readers/board_us.py`, `web/src/pages/p2/us.ts`(+ `p2_board.ts` 고리),
`config/us.yaml`·`config/knowledge/us/`(git mv), legacy `board/us/{engine,sectors,brief,sources,build}.py`(shim).

## 1. 맥락

- ET us-board 는 GitHub Actions 에서 Nasdaq 스크리너(유니버스)·Nasdaq history(일봉, 실패 시 설정 한 줄로 Stooq·Yahoo 로 갈아
  끼움)를 sqlite 에 쌓고, 국장과 **같은 신고가 코드**(당시 `board/engine/newhigh.py` — 60·252봉)로 판정해 정적 HTML 과 텔레그램
  브리프(①~⑧)를 냈다. SD 는 따로 `_daily_us_market_build` 로 미국 일봉을 만들었다(두 벌).
- P3 에서 국장 신고가는 3축(ADR 0017 — 120거래일·달력 52주·역사적)으로 바뀌었다. 미국장은 무료 소스가 상장 이후 전 구간을 주지
  않아 '역사적'을 단정할 수 없고(ET us.yaml 주석), ADR 0001 Q10 이 252봉 정의를 유지하기로 했다.

## 2. 결정

1. **엔진은 본문 그대로, 정의는 252봉**: `kbj.engines.us.engine`·`sectors`·`brief` 는 ET 본문 그대로(이름 공개화·타입 주석만),
   `board`(ET `build.py` 의 순수 부분 — 추이·섹터 추이·조립)는 만든 시각을 인자로 받는다(시계 주입). 판정은 국장과 같은
   `kbj.engines.board.newhigh`(옛 설정 키 `newhigh.lookback` = 그 종목 봉 수)를 쓰고, 설정은 `config/board.yaml` 엔진 절 위에
   `config/us.yaml` 이 이긴다(`load_us`). ADR 0017 의 3축은 미국장에 걸지 않는다.
2. **출처 순서·행 출처**: 일봉은 설정이 고른 출처(기본 Nasdaq) → 실패하면 Stooq(`history` — ET 는 하나만 불렀다), 지수는 Yahoo
   (FND 어댑터). 행 `source` 에 실제 출처(nasdaq·stooq·yahoo), 새 표 없이 `prv_market.daily_bar`(market='US'). 같은 (종목, 날짜)에
   출처가 여럿이면 저장소 `US_SOURCE_RANK`(nasdaq > yahoo > stooq)로 하나를 고른다. 수정주가 여부는 실행마다 `adjust_probe`(NVDA
   분할 대조) 사실을 남기고 Nasdaq 행 `adjusted` 에 싣는다(Stooq 행은 None — [확인 필요 #47]). 429 는 폴백하지 않고 감속 신호로
   올린다(남은 종목을 부르지 않는다).
3. **유니버스는 주 1회, 하한은 생성 단계**: `us.universe`(일 04:00)가 스크리너 → 펀드 제외 → 하한(시총·가격·거래대금 추정)을 건 판을
   `prv_market.us_universe` 에 통째로 쓴다(섹터는 원본 열 — 분류는 엔진 단계에서 `config/knowledge/us` 규칙으로). ET 는 매일
   스크리너를 다시 받았다 — KBJ 는 주 1회라 하한 판정도 주 1회고, 일봉 단계에서 `in_universe` 가 그날 값으로 한 번 더 건다(ET
   `build` 와 같다).
4. **기준일은 받은 일봉이 정한다**: `us.eod` 는 가장 많은 종목의 마지막 봉 날짜(`asof_from`)가 키의 미국 거래일과 다르면 공표
   전(`not_ready`). 이력이 짧은 종목(< 252 + 추이 + 10봉)은 전 구간(760일), 나머지는 최근 90일만 겹쳐 받는다(ET `daily`).
5. **산출 두 개**: `prv_board.artifact`(market='US')에 us_summary(기본 기준 보드·행 + 다른 기준(`alt` — API `basis=high`) +
   한눈에 문장(브리프 ① 의 줄) + 라벨 이름)와 us_brief(텔레그램 HTML 조각 `parts`·평문 `text`). 아침 브리핑 ① 은 us_brief 를
   **읽기만** 한다(BRF — 발송은 brief.morning 하나, U2). 보류(이력 부족)·수집 실패·섹터 미분류는 숨기지 않고 보드 `missing` 과
   실행 기록 detail 에 적는다.
6. **API·화면**: `/api/board/us`(로그인)는 DB(산출)만 읽는다 — 미국 거래일(뉴욕 날짜)·출처·보류 수를 반드시 싣는다. 페이지 2
   '간밤 미국 신고가' 패널(로그인 — 공개판 자물쇠)은 날짜를 한국 날짜로 바꾸지 않는다.
7. **금융위 일별은 대조만**(D-P5-19): `market.fsc_daily` 가 금융위 주식·지수 시세를 `daily_bar(source='fsc')` 로 쓴다(원장 순위상
   KRX 를 밀어내지 않는다). 쓰기 전에 같은 날 KRX 종가와 비교해 불일치 비율이 `reconcile.warn_mismatch_pct` 이상이면 health 경고
   (`fsc_mismatch`)만 — 수집 실패로 만들지 않는다. 화면·브리핑 미사용.
8. **조합 탐색(btsearch)은 옮기지 않는다**: ET `board/us/btsearch.py` 는 국장 백테스트 엔진(`board/engine/{backtest,search,signals,
   systems}` — P8)에 기대므로 그 엔진과 함께 P8 에 옮긴다. legacy 에 남고 원본 시험 `test_us_search` 3 도 legacy 에 남는다(설계
   추정 board 1,036 → 실측 1,039).

## 3. 결과

- legacy `board/us/{engine,sectors,brief}` 는 kbj 를 다시 내보내는 shim, `sources` 는 예전 호출 모양을 kbj 클라이언트(프로세스
  안 리미터)로 잇는 얇은 함수, `build` 는 sqlite·state 파일 입출력만(계산은 kbj `board.build`). `run.py cmd_us_*` 는 그대로 돈다
  (설정 경로만 레포 루트 `config/us.yaml`). 다리 시험 `board/tests/test_kbj_us_shim.py`.
- 원본 시험 77(`test_us_{sources 18,engine 20,brief 17,sectors 10,pipeline 9}` — Yahoo 3 은 FND 가 `test_yahoo.py` 로)을 kbj 로
  옮겼다. `test_us_pipeline` 의 sqlite·HTML·발송 단언은 저장소 왕복·산출·발송 조각으로 같은 뜻을 본다(페이지 HTML 은 SPA).
- 공개 레포에는 합성만: `tests/fixtures/synthetic/us/demo.py`(ET 합성 생성기), 가짜 Nasdaq·Stooq(`tests/fakes/nasdaq_server.py`),
  P5 합성 세계(`p5_world`) 응답 픽스처(`web/test/fixtures/api/us/`).

## 4. 남은 일·확인 필요

- [확인 필요 #47] Nasdaq 스크리너 칸·단위(시총·가격 달러, 등락 %), history 수정주가 여부, Stooq 폴백 빈도·빈 본문 조건, 약관(§10 Q6).
- [확인 필요 #46] Yahoo 지수 심볼(`config/us.yaml indexes` — ^GSPC·^IXIC·^DJI).
- [확인 필요 #48] 금융위 지수 idxNm 표기(코스피·코스닥·코스피 200)·KRX 지수 코드 규칙(공백 뺀 이름)과 맞는지.
- 카탈로그: `NASDAQ:screener`·`NASDAQ:daily` 를 `PLANNED` 에서 `kbj/data/private/nasdaq/datasets.py` 로 옮기기(FND 파일 — 요청).
- 작업 켜기(`us.universe`·`us.eod`·`market.fsc_daily`)는 WRAP — 사용자 결정 Q6 으로 미국 수집은 켠다(실측 #47 은 ET 운영으로 확인된
  경로), 금융위는 #48 확인 뒤.

## WRAP 확정 메모(2026-10-13 — 웨이브 3)

1. 상태 **채택**. 등록부: `us.universe`(일요일 04:00)·`us.eod` 켬(사용자 결정 Q6 — 본인 화면·텔레그램만, 원자료 공개 없음).
   `market.fsc_daily` 는 실측 #48 전이라 꺼짐. 카탈로그 `NASDAQ:screener`·`NASDAQ:daily` 를 PLANNED 에서 어댑터로 옮겼다.
2. `scripts/check_canonical.py LEGACY_KBJ_ALLOW` 의 `kbj.engines.us`·`kbj.data.private.{nasdaq,stooq,yahoo}` 4줄 확정. legacy board
   1,115 → 1,039(`test_us_search` 3 은 btsearch 와 함께 legacy — P8), `legacy/etf_traker/board/MIGRATION.md` 'P5'.
3. 하루 운영 시뮬레이션: `us.eod` 06:10 KST 실제 처리기(가짜 Nasdaq 이 합성 P5 세계 미국 일봉을 서빙)가 ok.
4. 남은 확인(#46·#47·#48): Yahoo 지수 심볼, Nasdaq history 수정주가 여부·스크리너 칸 단위·Stooq 폴백 빈도, 금융위 idxNm 표기·
   KRX 지수 코드 일치.
