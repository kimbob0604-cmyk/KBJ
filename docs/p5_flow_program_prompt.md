# P5-F 장중 투자자 수급·프로그램매매 — 작업 프롬프트

> 2026-10-13 사용자 요청: "투자자 수급(장중 1분 누적 순매수·시장/거래소 선택)과 일별 프로그램 순매수·프로그램 순매수/순매도 상위
> 같은 기능을 어느 단계에 자연스럽게 오류 없이 넣을 수 있는지 검증해서 프롬프트로 넣어 달라."
> 이 문서는 P5 설계(`docs/p5_design.md`)를 만들 때 **묶음 F2** 로 그대로 넣는 지시다. 검증 근거는 §1, 넣는 자리는 §2.

---

## 0. 만들 것 (화면 기준)

페이지 1(시장)의 **투자자 수급** 위젯과 **프로그램매매** 위젯. PLAN §4 표의 페이지 1 '투자자 수급·프로그램' 칸을 채운다.

1. **장중 투자자 누적 순매수 — 1분 단위 선 그래프**
   - 시장 탭: 코스피 · 코스닥 · 코스피200 선물 · 콜옵션 · 풋옵션 · (주식선물 — §1.2 실측 뒤).
   - 카드 7개(억원, 오늘 누적 순매수): 개인 · 외국인 · 기관 합계 · 금융투자 · 투신(사모 포함 여부는 metrics 에 정의) · 연기금 · 기타법인.
   - 선 토글: 개인·외국인·기관(기본 켬), 기관 세부(금융투자·투신·사모·은행·보험·종금·연기금·기타단체)와 기타법인(기본 끔).
   - 끝점 이름표, 0 기준선, 시간축은 수집 범위 그대로(장전·정규장). 빈 분은 **잇지 않고 끊어** 보인다(보간 금지).
   - 머리에 `수집 HH:MM · 60초 갱신 · 잠정` 꼬리표와 거래소 표시(§1.3 — 분 단위 선은 KRX 하나일 수 있다).
2. **일별 프로그램 순매수 — 최근 60거래일 막대**
   - 코스피·코스닥 각각. 막대는 전체 순매수, 막대에 올리면 차익·비차익(위탁·자기) 자세히.
   - 아래 줄: `YYYY-MM-DD · 합계 · 비차익 · 차익 · 지수`.
3. **프로그램 순매수 상위 20 / 순매도 상위 20** (코스피·코스닥 각각)
   - 열: 순위 · 종목 · 현재가 · 등락 · 순매수(억) · 매수 · 매도. 종목을 누르면 페이지 8(종목 상세).
   - **범위를 숨기지 않는다**(§1.4): 장중 표는 `범위: 거래대금 상위 N + 관심종목`, 마감 뒤 표는 `전 종목(마감 확정)`.

## 1. 검증 — 데이터 출처와 한계 (2026-10-13, KIS 공식 예제 저장소 `koreainvestment/open-trading-api` `examples_llm/domestic_stock` 대조)

### 1.1 장중 시장별 투자자 — `FHPTJ04030000` 시장별 투자자매매동향(시세성)
- 경로 `/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market`, 입력 `FID_INPUT_ISCD`(시장)·`FID_INPUT_ISCD_2`(업종).
- **HTS [0403] 시장별 시간동향 상단 표**를 주는 TR 이다 — 호출 시점의 **오늘 누적값 한 줄**이지 분 단위 이력이 아니다.
  그래서 1분 선은 **1분마다 불러 쌓아야** 생긴다. 못 부른 분은 영영 빈다(뒤에서 채울 수 없다) → 빈 분은 끊어 보이고 quality 에 남긴다.
- 투자자 12구분: `frgn·prsn·orgn·scrt·ivtr·pe_fund·bank·insu·mrbn·fund·etc_orgt·etc_corp`
  (매도·매수·순매수 × 수량·대금. `pe_fund·etc_orgt·etc_corp` 순매수 수량 칸 이름은 `_ntby_vol`, 나머지는 `_ntby_qty`).
- **이미 KBJ 에 있는 것:** 데이터셋 `KIS:market_investor_intraday`(`kbj/data/private/kis/datasets.py`), 저장 표 `prv_gex.investor_flow`
  (0006 — `market_code`·`sector_code` 에 값 제한 없음), 읽는 뷰 `prv_flows.market_investor_intraday`(0003).
  GEXLAB 이 `K2I` + `F001`(선물)·`OC01`(콜)·`OP01`(풋)으로 실제로 부르고 있다(`legacy/gexlab/scripts/probe_investor.py`, 모델 `legacy/gexlab/data/kis/models.py`).
- **[실측 필요 — 체크리스트에 새 줄]** 코스피·코스닥 현물의 코드 쌍(예제는 `999`·`S001`), 주식선물 코드, 금액 단위,
  장전(08:00~) 값이 오는지, 응답이 KRX 만인지 통합인지. 실측 전에는 **GEXLAB 이 검증한 K2I 3개만 켜고** 나머지는 꺼 둔다.

### 1.2 일별 시장 투자자 — `FHPTJ04040000` (P3 에 있음, `KIS:market_investor_daily`)
- 마감 뒤 1분 선의 마지막 값과 대조하는 확정치로 쓴다(§4 검산 ③).

### 1.3 거래소(KRX·NXT·통합)
- `FHPTJ04030000` 에는 **거래소 구분 입력이 없다** → 분 단위 선은 KIS 기본값 하나다. 실측 전엔 화면 거래소 선택을 **잠그고** `KRX` 꼬리표를 단다
  (스크린샷처럼 선물에서 거래소 버튼이 흐린 것과 같은 처리). 통합 수치를 지어내지 않는다.
- 프로그램매매 TR 은 `FID_COND_MRKT_DIV_CODE` = `J`(KRX)·`NX`(NXT)·`UN`(통합)을 받는다 → 거래소 선택은 **프로그램 위젯만**,
  `config/markets.yaml kis.venues`(기본 `[KRX]`, 체크리스트 #25)로 켠다. venue 를 늘리면 호출 수가 배로 는다(§3.7 예산).

### 1.4 프로그램매매 TR (전부 공식 예제에 있음 — 응답 칸 이름은 예제 `chk_*.py` 기준, 단위·반영 시각은 [실측 필요])
| 쓰임 | TR | 경로 | 입력 |
|---|---|---|---|
| 일별 60거래일 막대 | `FHPPG04600001` 프로그램매매 종합현황(일별) | `/uapi/domestic-stock/v1/quotations/comp-program-trade-daily` | `FID_COND_MRKT_DIV_CODE`(J·NX·UN), `FID_MRKT_CLS_CODE`(K 코스피·Q 코스닥), 시작·끝 날짜 |
| 장중 시장 프로그램 | `FHPPG04600101` 프로그램매매 종합현황(시간) | `…/comp-program-trade-today` | 거래소·시장·구간·시각 |
| 투자자별 프로그램(당일) | `HHPPG046600C1` | `…/investor-program-trade-today` | 시장 1 코스피·4 코스닥 |
| 종목별 장중 | `FHPPG04650101` 종목별 프로그램매매추이(체결) | `…/program-trade-by-stock` | 거래소, 종목코드 |
| 종목별 일별 | `FHPPG04650201` 종목별 프로그램매매추이(일별) | `…/program-trade-by-stock-daily` | 거래소, 종목코드, 날짜 |
| (웹소켓, 이번엔 안 씀) | `H0STPGM0`·`H0NXPGM0`·`H0UNPGM0`·`H0UPPGM0` | — | P7 ws-gateway(ADR 0004 D2) 이후 |
- 일별 응답 칸: 차익(`arbt_*`)·비차익(`nabt_*`)·전체(`whol_*`) × 위탁(`entm`)·자기(`onsl`)·합계(`smtn`) × 매도·매수2·순매수 × 수량·대금.
- **프로그램 순매수 '상위' 순위 TR 은 KIS 에 없다**(공식 예제의 순위 계열은 `volume_rank` 등 — 프로그램 정렬 없음).
  스크린샷의 순위는 다른 증권사 회원 집계다. 그래서 상위 표는 **종목별 TR 로 범위를 훑어 만든다**:
  - 장중: `거래대금 상위 N`(기존 `KIS:turnover_rank_intraday` — 한 번에 몇 개 오는지 [실측 필요]) + 관심종목, 10분마다 `FHPPG04650101`.
  - 마감 뒤: 전 종목 `FHPPG04650201` 1회 훑기(코스피+코스닥 약 2,700회) → 확정 순위. 리미터 아래에서 저녁 한가한 시각에(§3).
  - 화면 꼬리표로 범위를 반드시 보인다. 다른 증권사 API(새 키·새 계정)는 이 묶음에서 쓰지 않는다 — 필요하면 사용자 결정(PLAN §7).

### 1.5 지켜야 할 경계 (이미 정해진 것)
- 네이버 스크래핑 금지(KBJ). KIS·KRX 원자료와 그걸로 만든 픽스처는 공개 레포·공개 빌드에 넣지 않는다 → **합성 픽스처만**,
  이 위젯들은 **로그인 빌드 전용**(DATA_TIERS — KIS 파생값은 private). 공개 내보내기(`public.export`)에 넣지 않는다.
- 직접 호출은 어댑터(`kbj/data/private/kis`)만(`check_canonical`). 표의 쓰기 주인은 하나(import-linter 계약·`test_public_writers` 관례).

## 2. 넣는 자리 — P5 묶음 F2 (이유)

- **P4 에 넣지 않는다:** P4 는 마무리 검사 중이다. 범위를 늘리면 P4 완료가 밀리고 오류 위험이 커진다.
- **P7(GEX)까지 미루지 않는다:** 1분 수집 표(`prv_gex.investor_flow`)·뷰·KIS 리미터·등록부·페이지 1·차트 부품이 이미 있어
  지금 단계(P5)에서 기대는 것이 전부 갖춰졌다. 남은 의존은 실측(§1)뿐이다.
- **P5 묶음 F2 로 넣고, P7 과의 겹침은 '쓰는 주인 한 명' 규칙으로 푼다:**
  P5 의 새 수집 작업이 `prv_gex.investor_flow` 의 **유일한 쓰기 주인**이 된다(현물·K2I 모두).
  P7 에서 GEXLAB poller 를 옮길 때 poller 는 이 TR 을 따로 부르지 않고 이 표를 읽는다(또는 주인을 넘기는 마이그레이션·ADR).
  두 곳이 같은 TR 을 1분마다 부르는 일을 막는다(호출 낭비·같은 행 경합). ADR(다음 번호 — 지금 마지막은 0018)에 적는다.

## 3. 만드는 순서 (묶음 F2 안 — 각 단계는 시험이 녹색이어야 다음)

1. **실측 체크리스트 줄 추가**(`docs/probe_results.md`): §1.1·§1.3·§1.4 의 [실측 필요] 항목. 키가 있으면 `kbj` 프로브로 채우고, 없으면 칸을 남긴다.
   값은 적지 않고 **칸 이름·단위·코드·반영 시각만** 적는다(공개 레포).
2. **데이터셋·파서**: `datasets.py` 에 `KIS:program_market_daily`·`KIS:program_market_intraday`·`KIS:program_investor_today`·
   `KIS:program_stock_intraday`·`KIS:program_stock_daily` 를 TR 표 그대로(확인 안 된 칸은 `[확인 필요 #NN]`). 파서는 기존 investors
   파서 관례(`kbj/data/private/kis` — 금액은 원 단위 정수로 바꿔 저장, metrics §0). 12구분 → 화면 7카드 매핑은 `docs/metrics.md` 새 절에.
3. **저장**: 마이그레이션 새 번호(현재 마지막 다음) — `prv_flows.program_market_daily`(날짜·시장·거래소·차익/비차익·위탁/자기·매수/매도/순매수·quality·source),
   `prv_flows.program_market_intraday`(hypertable), `prv_flows.program_stock_daily`·`program_stock_intraday`. 현물 1분 수급은 **새 표를 만들지 않고**
   `prv_gex.investor_flow` 에 쓴다(§2). 적용된 마이그레이션은 고치지 않는다(체크섬).
4. **수집 작업**(`config/jobs.yaml`, 처음엔 `enabled: false` — 실측 확인 뒤 켬, ENABLED_BY_PHASE P5):
   - `flows.market_minute` — 평일 장중 1분(정규장 09:00~15:30 + 장 마감 단일가 15:40까지, 장전은 실측 뒤), 켜진 시장 코드만. 같은 분 재실행은 멱등(UNIQUE).
   - `program.intraday` — 1분(시장 2개 × 켜진 거래소).
   - `program.stock_scan` — 10분, 거래대금 상위 N + 관심종목(`config/markets.yaml program.scan_top_n`, 기본값 [확인 필요]).
   - `program.daily` — 장 마감 뒤 1회 + 처음 1회 60거래일 백필.
   - `program.stock_sweep` — 마감 뒤 전 종목 1회(시각은 16:00 발송과 겹치지 않게 — ADR 0018 의 16:00 묶음 뒤, 예 18:10). 중간에 끊기면 이어서(커서 저장).
   - 휴장일은 등록부의 기존 거래일 조건(P3 작업들과 같은 판정)으로 건너뛴다. 호출 예산은 `config/limits.yaml` 에 더하고 **등록부 시험이 예산 초과를 막는다**.
5. **API**: `/api/flows/market-minute?market=&venue=`(분 시계열·카드·quality·수집 시각), `/api/program/daily?market=&days=60`,
   `/api/program/top?market=&side=buy|sell&scope=intraday|close`. openapi.json·`types.gen.ts` 재생성(`--check` 녹색).
6. **웹(페이지 1)**: 기존 P3 페이지 1 디자인·상태 표시(quality·잠정·자물쇠)를 그대로 쓴다. 60초 폴링(웹소켓 아님), 탭/토글 상태는 URL 쿼리.
   빈 분은 선을 끊고, 데이터 없음·실측 전(꺼진 시장)·장 시작 전·휴장일 화면을 각각 따로 보인다. 모바일 폭에서 가로 넘침 없음.
7. **텔레그램은 이번 묶음에 넣지 않는다**(16:00 수급 리포트에 프로그램 줄을 더하는 건 사용자 요청이 있을 때 — ADR 0018 규칙 유지).

## 4. 시험 (오류가 안 나게 하는 장치)

- **파서**: 합성 응답(실제 칸 이름, 가짜 값)으로 12구분·`_ntby_vol`/`_ntby_qty` 차이·빈 문자열·음수·단위 변환.
- **가짜 KIS**(`tests/fakes`): 위 TR 전부 + 분마다 누적이 늘어나는 장중 시나리오, 일부 분 실패(429·타임아웃), 장전/휴장일.
- **검산(metrics 새 절, 합성 원장에서 강제)**:
  ① 매분 `개인 + 외국인 + 기관 + 기타법인(+기타단체)` 순매수 대금 ≈ 0(허용 오차는 metrics 에 — 현물만, 선물·옵션은 별도 정의),
  ② `기관 합계 = 금융투자 + 투신 + 사모 + 은행 + 보험 + 종금 + 연기금(+기타단체 정의에 따라)`,
  ③ 마감 뒤 1분 선의 마지막 값 = `KIS:market_investor_daily` 확정치(차이는 기록, quality 로 표시),
  ④ 프로그램 전체 = 차익 + 비차익, 위탁 + 자기 = 합계,
  ⑤ 마감 순위(전 종목)의 상위 20 은 장중 범위 순위와 다를 수 있다 — 화면 꼬리표가 범위를 말하는지 시험.
- **등록부·리미터**: 새 작업의 호출 수 합이 예산 안, 16:00 발송 묶음과 겹치지 않음, 휴장일 미실행.
- **하루 운영 시뮬레이션**(`tests/sim`): 장중 1분 수집이 정규장 동안 **빈 분 0**(가짜 KIS 정상 시나리오), 실패 시나리오에선 빈 분이 정확히 실패한 분만큼.
- **API·웹**: 라우트 시험, vitest 로 카드·선 토글·끊긴 선·빈/실측 전/휴장 상태·순위 꼬리표, 로그인 빌드에만 들어가는지(공개 번들 검사).
- **공통 완료 명령**: ruff·format·pyright·lint-imports·check_canonical·check_public_safety·pytest(단위·sim·integration)·web lint/tsc/vitest/build/check-bundle/gen:types --check.

## 5. 완료 기준 (P5 완료 기준에 더한다)

- 합성 데이터로 페이지 1 투자자 수급·프로그램 위젯이 시장별로 그려지고(스크린샷 4장과 같은 구성), 빈 분·실측 전·휴장 화면이 각각 맞게 나온다.
- 하루 운영 시뮬레이션에서 정규장 1분 수집 빈 분 0, 검산 ①~④ 합성 원장 0 차이, 순위 화면이 범위를 표시한다.
- 실데이터는 키로 실측(§1 체크리스트)한 항목만 켠다 — 켜지 않은 시장·거래소는 화면에 '확인 전'으로 남는다.
- 공개 빌드·공개 내보내기·레포에 KIS 값 0(check_public_safety·번들 검사).
