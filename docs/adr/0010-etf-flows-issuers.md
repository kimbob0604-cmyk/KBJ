# ADR 0010 — ETF 수급 엔진·운용사 어댑터 승격, 유형 규칙, 분할 감지 (2026-10-07)

상태: **확정**(2026-10-07 — 초안 P3 묶음 E3, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모', `docs/p3_design.md` §9.3).
근거: `docs/p3_design.md` D-P3-12·13, §1.5·§3.3·§4.3·§8.2, `docs/metrics.md` §4·§8, ADR 0001 Q8·U4,
메인 결정 R2(2026-10-07 — 가능한 검산은 0 차이, 불가한 검산은 사유 기록).
구현: `kbj/engines/etf/{types,splits,flows,premium,holdings}.py`,
`kbj/data/private/etf_issuers/{base,kodex,tiger,timefolio,sol,ace,hanaro,koact,plus,rise,registry}.py`,
`kbj/services/collectors/etf_holdings.py`(`etf.collect`), legacy shim
`legacy/etf_traker/etf_tracker_v9/{themes,tracker,collectors}.py`·`adapters/*.py`,
시험 `tests/unit/engines/etf/`·`tests/unit/etf_issuers/`·`tests/unit/collectors/test_etf_holdings.py`,
가짜 운용사 `tests/fakes/etf_issuer_server.py`, 합성 견본 `tests/fixtures/synthetic/etf_issuers/`.

## 1. 맥락

- 사용자 요청(2026-10-07)으로 페이지 10 'ETF 수급'(순유입·투자자별·유형별·괴리율·구성종목 변동)이 P3 에 들어왔다.
  ET `etf_tracker_v9` 는 구성종목 변동만 하고(운용사 어댑터 9곳 + 네이버 TOP10 폴백, sqlite), 순유입·가격효과·
  검산 ③ 은 어디에도 없다(metrics §4 는 P0 에 정의만 했다).
- `themes.classify` 는 레버리지·채권을 `None` 으로 빼고 해외·원자재 구분이 없다 — metrics §4 의 유형 7개와 1:1 이
  아니다(D-P3-13).
- 분할·병합일에 좌수 공식을 그대로 쓰면 가짜 대규모 순유입이 생긴다(metrics §4 오류 1번).
- ET 원본 시험은 0개다(§1.11 — E3). 운용사 실응답은 로그인 등급이라 레포에 둘 수 없다.

## 2. 결정

1. **순수 엔진**(`kbj.engines.etf` — `kbj.core` 만 import, 계약 ⑨ 대상):
   - `flows.daily_flow(prev, cur, split)` — 순유입 = ΔS × NAVₜ, 가격효과 = Sₜ₋₁ × ΔNAV, 검산 ③ 잔차 = 순자산 변화 −
     (순유입 + 가격효과). **보고 순자산**이 두 날 다 있으면 그것으로 대조(허용오차 0.005원 × (Sₜ + Sₜ₋₁) + 공표 단위
     `unit`, 기본 1원 [실측 필요 — 원·백만원])하고 넘으면 그날 흐름 `invalid`(+ `c3_checks` → `ledger_check(c3)`
     행). 없으면 계산 순자산(항등식 — 부동소수 오차만). 검산할 수 없는 흐름(신규·입력 결측)은 `check3` 가 `None`
     — 0 으로 바꾸지 않는다(R2).
   - `window_flows(days, n, trading_days=…, splits=…, listed_on=…, delisted_on=…)` — 전 거래일 행이 없고 앞에도
     없으면 `new`(순유입 None, 첫날 순자산 `new_net_asset`), 앞에 행이 있는데 전 거래일만 빠지면 `invalid`(공백 —
     여러 날 흐름을 하루로 세지 않는다), `delisted_on` 이상 행은 쓰지 않는다. 분배금은 좌수 공식이라 자동(시험 고정).
   - `by_type` — 7 유형이 늘 다 나온다(수 0 이어도), `period_totals` — 기간 합 = 일별 합.
   - 금액은 부동소수(원)로 계산하고 화면·API 는 반올림 정수(`EtfFlow.won`).
2. **분할·병합 감지**(`splits.detect_split`): Sₜ/Sₜ₋₁ ≈ r **이고** NAVₜ₋₁/NAVₜ ≈ r(r = k 또는 1/k,
   k ∈ `etf.split_ratios`, 상대오차 `etf.split_tol` 2%). 두 조건을 함께 봐서 큰 설정(좌수만 10배)을 분할로 보지 않는다.
   감지 결과는 `SplitEvent(origin=detected, quality=estimated)` 이고 그날 흐름 품질도 estimated. **같은 날 수동 표
   (origin=manual)가 있으면 그것이 우선**(엔진 `_events_by_date` + 저장소 `put_split_event` 가 manual 을 덮지 않음).
   그날 계산은 Sₜ₋₁ × ratio·NAVₜ₋₁ ÷ ratio 로 맞추되 **보정 좌수를 정수로 반올림하지 않는다**(병합에서 Sₜ₋₁ 이 k 로
   나누어떨어지지 않으면 반올림 좌수 × NAV 만큼 가짜 순유입·잔차가 생긴다 — 독립 검증에서 고침, 시험 고정).
3. **유형 7분류**(`types.etf_type(name, base_index)` — metrics §8.1 순서): 레버리지·인버스 → 채권·현금 → 원자재 →
   해외주식 → 국내 대표지수(`classify` ∈ 시장대표·코스닥·팩터) → 국내 테마 → 기타. 이 묶음이 정한 세부:
   - 해외주식은 **기초지수 이름 우선**: 해외 낱말이면 해외, 국내 표시(코스피·KOSPI·코스닥·KRX·FnGuide·WISE…)면
     해외 아님, 둘 다 아니면 ETF 이름. `코스닥글로벌` 은 국내 지수라 지우고 본다.
   - 원자재 `은` 은 낱말 앞이고 뒤가 `행` 이 아닐 때만(은행주 ETF 오분류 방지). 영문 낱말은 영문자에 붙지 않을 때만
     (`CD` 가 `ABCD` 에 걸리지 않게).
   - metrics §8.1 목록에 더한 낱말 [확인 필요]: 채권 `국채`·`단기채`·`특수채`·`크레딧`(미국채 ETF), 원자재 `금은`·
     `천연가스`·`브렌트`·`팔라듐`·`니켈`·`비철금속`, 해외 `선진국`·`신흥국`·`이머징`·`대만`·`홍콩`·`항셍`·`니케이`·
     `독일`·`아시아`·`라틴`·`브라질`·`다우존스` 와 영문 지수 낱말(NASDAQ·NYSE·CSI·STOXX·MSCI WORLD/EM…).
     **metrics §8.1 표를 이 목록으로 맞춰야 한다**(묶음 M/S 요청).
   - 레버리지 배수 `leverage_of`(ET `monitor/kr/etf.py:_leverage` 규칙 + 기초지수 배수 표시 + `곱버스` −2).
   - `typed_meta(meta)` 가 운용사·브랜드·테마·유형·배수를 채운다 — `etf.collect` 가 부르고, KRX 메타 갱신
     (`krx.daily`)도 같은 함수를 쓰면 신규 상장이 그날 분류된다(묶음 C 요청).
   - `themes.py` 는 규칙·순서 그대로 옮겼다. 고친 것 하나: `ISSUER_OF_BRAND['UNICORN']` 첫 글자가 한자 '现' 으로
     들어가 있던 오타 → '현대차증권'.
4. **운용사 어댑터 9곳 승격**(`kbj.data.private.etf_issuers`): ET `collectors.py`(KODEX·TIGER·TIMEFOLIO·SOL)·
   `adapters/`(ACE·HANARO·KoAct·PLUS·RISE)의 파싱 규칙과 실측 주의(우선주 ISIN 보정, KR7 만, 휴장일 폴백, HANARO
   미래일 클램프, RISE 기준일 역추적, ACE `_STOCK` 형태 거르기·중복 코드 합산, KoAct Cloudflare)를 그대로 옮겼다.
   바뀐 것:
   - HTTP 는 requests 세션 → `IssuerHttp`(httpx, 넘겨주기 안 따름, 조각마다 크기 16MiB·전체 시간 상한). 부르기 전
     **호스트별 scoped 리미터** `etf_issuers`(`rl:etf_issuers:<호스트 해시>`, config/limits.yaml 1/s [제안]),
     KoAct 호스트는 0.5/s 로 더 낮춘다(실측 한계 25요청/35초의 7할). 어댑터마다 흩어져 있던 재시도(ACE·HANARO·
     KoAct·RISE)는 이 한 곳으로: 일시 오류(전송 오류·408·425·429·5xx)만 3번까지, 429 는 리미터 감속, KoAct 는
     챌린지 HTML(200 + JSON 아님)도 일시 오류로 보고 35초 × n 쉰다. 400·404 등은 바로 실패(ACE 404 는 '미존재 펀드'
     로 빈 결과). 사유 문구는 `kbj.data.http.scrub` 로 가린다.
   - 행은 `HoldingRow`(source `ETF_ISSUERS:<운용사>`, quality ok), 기준일은 `date`. 응답에 기준일이 없을 때만
     요청일(ET 그대로).
   - RISE 의 '오늘'(KST)은 주입한다(벽시계 의존 금지). 인스턴스 캐시(HANARO `_latest`·RISE `_asof`)는 한 스레드
     전용 — 수집 처리기가 운용사마다 스레드 하나만 쓴다.
   - ET 의 `adapters/` 자동 등록(import 실패를 출력만 하고 넘어감)은 두지 않는다 — 목록은 `registry.ADAPTERS` 하나.
   - **네이버 TOP10 폴백은 옮기지 않는다**(U4). 전용 어댑터가 없는 운용사(키움·하나·우리 등)의 구성종목은 P3 에서
     비고, 대체 후보는 KIS `FHKST121600C0` [실측 필요].
   - kodex·tiger·timefolio·sol·koact 는 ET 처럼 `_is_kr`(6자리 국내 꼴)로만 거른다 — 선물·채권 분류코드가 국내 꼴로
     섞여 오는지는 [실측 필요](체크리스트 #27). 섞이면 ACE 의 `_STOCK` 거르기를 공통으로 올린다.
5. **구성종목 변동**(`holdings.fund_pairs`·`analyze` — ET `tracker.py`:251·274 그대로): 펀드 단위 최근 두 스냅
   (간격 ≤ 14일), NEW·DROP(TOP10 원천은 IN10·OUT10), 수량 ≥ `qty_floor` 종목 변동률의 중앙값으로 CU 재산정 보정
   (`min_base_for_cu` 5 미만이면 0, TOP10 은 보정 없음) 뒤 ±`action_pp`%p 이상이면 ADD·CUT, ETF 가 담은 ETF 제외.
   기준값은 옛 환경변수(`QTY_FLOOR`·`ACTION_PP`) 대신 `config/markets.yaml` `etf.holdings`. 결과 순서는 결정적
   (ET 는 집합 순회 순서였다 — 값은 같다). 수량을 모르는(None) 종목은 변동률을 내지 않는다(0 으로 바꿔 −100%
   가짜 CUT 을 만들지 않음 — ET 는 수량이 늘 숫자였다).
6. **수집 처리기 `etf.collect`**(`kbj.services.collectors.etf_holdings:run`): ① 메타 분류(`typed_meta`, 바뀐 행만)
   ② 운용사마다 스레드 하나로 격리 — 목록 → 추적 대상(유형이 국내 대표지수·국내 테마. ET 의 '네이버 탭 1·2 +
   classify' 대체) → PDF. 목록이 실패하면 저장된 추적 펀드로 PDF 만. ③ 저장은 주 스레드만 — 실제 기준일 스냅을
   통째로 바꾸고(멱등), 빈 응답 3회 연속(`EMPTY_LIMIT` — ET 그대로)이면 추적 제외, 네트워크 오류는 세지 않는다,
   기준일이 요청일보다 뒤면 받지 않는다. ④ 변동은 실행일 행을 **한 번에 통째로**(`put_changes` — 펀드마다 부르면
   앞 펀드 것이 지워진다). 운용사 하나라도 통째로 실패하면 `failed`(등록부 재시도 3×900초 — 다시 돌려도 같은
   결과), 아니면 `ok`. 텔레그램 리포트는 P5.
7. **legacy shim**: `themes.py` 는 kbj 를 다시 내보내고, `tracker.py` 의 `fund_pairs`·`analyze` 는 sqlite 읽기·쓰기만
   남기고 kbj 엔진을 부르며 기준값은 markets.yaml 에서 읽는다. `collectors.py`·`adapters/*.py` 의 운용사 클래스는
   kbj 어댑터를 옛 인터페이스(dict 행·문자열 날짜)로 감싼다(`LegacyAdapter`, `IssuerHttp.local()`). `NaverTop10`
   은 legacy tracker 가 아직 부르므로 원본 그대로 남긴다(정리는 S — 기준선 `naver` 건수 그대로).
   `scripts/check_canonical.py` 의 legacy 허용 목록에 `kbj.engines.etf`·`kbj.data.private.etf_issuers`·
   `kbj.config.markets` 를 더했다(S 확정).

## 3. 결과

- 검산 ③ 은 합성 원장(`tests/fixtures/synthetic/ledger_gen.py` — 묶음 E2 생성기, 1:10 분할·5:1 병합·분배금·
  신규 상장·상장폐지)의 참값과 순유입·가격효과가 같고 잔차가 허용오차 안이다(고정 시드 + hypothesis). 보고
  순자산 하나를 깨면 그 날과 다음 날 흐름만 invalid 이고 c3 기록 2건.
- 운용사 실응답은 레포에 없다. 가짜 서버와 견본 파일은 ET 머리말의 실측 기록을 따른 합성이다 — 키 이름·HTML
  구조 자체가 [실측 필요](체크리스트 #27, 키·약관 확인 뒤 `docs/probe_results.md`).

## 4. 남은 것 [확인 필요]·요청

- 순자산 공표 단위(원·백만원)와 반올림 → 검산 ③ `unit`(R10).
- 분할 감지 오탐·누락(R9) — 실데이터 감지 목록 검토(#20). 수동 표 입력 경로(현재 저장소 `put_split_event` 만).
- `EMPTY_LIMIT`(3)을 `config/markets.yaml` `etf.holdings.empty_limit` 로(M).
- `etf.collect` 등록부 `writes` 에 `prv_etf.meta` 추가(메타 분류를 쓴다 — S).
- 검산 ③ 기록·분할 감지 기록을 저장하는 단계(엔진 `c3_checks`·`SplitCandidate.to_event` 는 있다 — 실행 위치는
  `krx.daily` 끝 단계 또는 실데이터 검산 CLI, S·C·E2 와 정할 것).

## S 확정 메모 (웨이브 3, 2026-10-07)

- 번호 0010·상태 **확정**. `etf.collect` 를 켰다(owner `kbj.services.collectors.etf_holdings:run`, writes 에 `prv_etf.meta`).
- 열린 항목을 닫았다: `record_flow_checks`(검산 ③ 실패 `prv_flows.ledger_check` c3·감지 분할 `prv_etf.split_event`)를
  **`krx.daily` 끝 단계에 연결**했다(ETF 일별 키를 받은 실행만, 백필 제외, 실패는 그 실행의 사유로 — 삼키지 않음). 등록부
  `krx.daily` writes 에 두 표를 더했다. 처음 보는 ETF 는 그날 `typed_meta` 로 분류한다(있던 분류는 지킨다). 시험:
  `tests/unit/collectors/test_krx_daily.py`(1:10 분할 감지·멱등·백필 제외), 하루 운영 확장 시뮬레이션.
- 새 canonical 그룹 `etf_issuers`(운용사 9곳 호스트 — kbj 안은 `kbj/data/private/etf_issuers/**` 만, legacy 0건).
- legacy: `collectors.py:NaverTop10`·`tracker.py:naver_names`·`verify.py` 를 지웠다(D-P3-12·U4). legacy `build_universe` 는
  운용사 어댑터 목록 + `etf_type` 판정만 쓴다. `market.py`(네이버 ETF 목록·시세)는 tracker·live_update 가 써서 P5 로 이월.
