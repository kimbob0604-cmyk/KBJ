# 지표 명세

PLAN §5 를 구현 전에 여기 옮겨 적는다. 명세 → 테스트 → 구현 순서. 여기 없는 공식은 쓰지 않는다.
PLAN 에 없어서 기본값을 정한 곳은 **[확인 필요]** 로 표시한다 — 기본값으로 구현하되 사용자 확인 전까지 바꿀 수 있게 설정·인자로 둔다.

> 2026-09-28 사용자 결정: 이 날짜까지 적힌 **[확인 필요]** 기본값은 모두 승인한다. 단 §3.8 Δt 는 달력 기준으로 바꿨다. 이후 새로 붙는 [확인 필요]는 따로 확인받는다.

> 골든(PLAN §6.3): §1~§3 파이프라인 출력은 `tests/golden/core/chain_synthetic_20260928_small.json` 에 고정돼 있다 — 입력 `tests/fixtures/validation/chain_snapshot_synthetic_20260928_{1427,1452}_small.json`(+ 14:52 의 시각만 야간으로 옮긴 파생 입력 `NIGHT_DERIVED` — 실측 아님, `docs/phase1_design.md` §10), 비교 `tests/golden/test_core_golden.py`(허용오차 `scripts/make_golden.py` `TOLERANCES`). 여기 공식·기본값을 바꾸면 골든이 깨진다 — 의도한 변경이면 `uv run python -m scripts.make_golden core --write` 로 다시 만들고 diff 를 같은 커밋에 넣는다.

각 지표 형식:

```
## <이름>
- 정의:
- 공식:
- 입력:
- 단위:
- 예외:
- 품질 판정 (ok | stale | estimated | invalid):
- 테스트:
```

## 0. 공통 규약

- 가격·행사가·F 단위는 pt. 옵션·선물 승수는 `core/specs.py`(§2.1): 코스피200 옵션 m = 250,000원
- 가격 모델 Black-76, 기초자산 = 같은 만기 합성선물 F(§1.3). **할인율 r = 0** — PLAN §2.4 "배당·금리 추정 불필요"를 r = 0 으로 해석 **[확인 필요]** (함수 인자 `r` 기본값 0.0 으로 둔다)
- IV 역산 라이브러리: `vollib`(Let's Be Rational). PLAN §5.1 의 `py_vollib` 은 1.0.12 에서 deprecated 경고를 내며 `vollib` 로 옮기라고 안내한다 — 같은 저자·같은 API 의 후속 패키지
- 그릭스 단위: 델타 무차원, 감마 1/pt(기초 1pt 당 델타 변화), 베가 IV 1%p 당 가격(pt), 세타 1일(365일 기준) 당 가격(pt)
- 시각은 aware datetime(UTC 저장, KST 계산). naive 금지
- 품질 합성: 한 산출물의 품질은 입력 품질 중 가장 나쁜 것(`invalid` > `estimated` > `stale` > `ok`) 이상
- `stale`: 입력 스냅샷이 §6.1 기준보다 오래됨 — 웹소켓 10초, REST 90초 미갱신

## 1. 입력 전처리 (PLAN §5.1)

### 1.1 옵션 가격 선택
- 정의: IV 역산에 쓸 옵션 한 종목의 가격
- 공식: 매수·매도 호가가 모두 있고 `(ask − bid) ≤ 3 × tick(bid)` 이면 `mid = (bid + ask) / 2`, 아니면 `last`. 둘 다 없으면 제외
  - `tick(p)` 는 §2.1 옵션 호가단위(p ≥ 10pt 이면 0.05, 미만 0.01). 호가가 10pt 경계를 걸칠 때 **bid 기준 틱**으로 센다 **[확인 필요]**
- 입력: bid, ask, last (전광판 행), 당일 누적 거래량 `volume`(KIS `acml_vol`, 모르면 없음). 단건 현재가 보강 행(`source=fill`)은 호가가 없어 `last` 만
- 단위: pt
- 예외: bid 또는 ask 가 0·빈 값이면 호가 없음으로 본다. last 가 0·빈 값이면 last 없음
  - ask < bid(역전 호가 — 단일가 접수 중 스냅샷 등)도 호가 없음으로 보고 last **[확인 필요]** — 식을 글자 그대로 따르면 음수 스프레드라 mid 가 된다
  - **전 세션 가격**: 고른 가격이 `last` 인데 `volume = 0` 이면 그 last 는 전 세션 값으로 본다(`PriceChoice.prev_session`) **[확인 필요]**. 근거: `docs/validation_greeks.md` §5.4 — 2026-09-28 위클리 ATM±5 22행 중 10~19행이 당일 거래 없이 전 세션 last 로 역산돼 합성 F 가 +28~31pt(WKM 261001) 어긋났다. `volume` 을 모르면(없음) 표시하지 않는다(지금 동작). mid 는 살아 있는 호가라 거래량과 무관하다. 야간 세션의 `acml_vol`·last 가 어느 세션 기준인지는 실측 전이다
  - 전 세션 가격의 쓰임 **[확인 필요]**: §1.3 F 에는 쓰지 않는다(§1.3). IV·그릭스·GEX 에는 그대로 쓰되 그 종목 품질(`OptionEval.quality`)을 `estimated` 이상으로 둔다 — 사유 `prev_session_last`(`OptionEval.quality_reasons`). 종목 품질은 §2.1 만기 품질·§3.7 ATM IV 품질에 합성된다
- 품질: 선택된 가격이 있으면 ok, 없으면 해당 종목 제외(품질 아님). 전 세션 가격이면 위 규칙
- 테스트: 스프레드 3틱 경계(=3틱 mid, 3틱+ last), 10pt 경계 틱, 호가 한쪽만 있음, 둘 다 없음, 역전 호가, 거래량 0 인 last(전 세션)·mid·거래량 모름

### 1.2 프리미엄 하한
- 정의: 수치 불안정 종목 제외
- 공식: 선택 가격 < 0.02pt 이면 IV·그릭스 계산 제외. 그 종목 OI 는 GEX 에서 **기본 제외**, `excluded_oi_ratio = 제외된 OI 합 / 전체 OI 합` (범위별)
  - 판정은 `IvResult.below_min_premium`(§1.5 결과의 명시 플래그)으로 한다. KIS IV 가 있으면 §1.5 폴백으로 `sigma` 가 `estimated` 로 채워지지만(IV 표시용), 자체 그릭스는 계산하지 않고 GEX 는 IV 품질·`sigma` 유무와 무관하게 이 플래그로 제외한다 **[확인 필요]** — §1.2 "계산 제외"와 §1.5 "1.2 로 제외 → KIS 폴백"이 겹치는 부분의 해석. KIS 그릭스는 어떤 경우에도 GEX 폴백으로 쓰지 않는다(2026-09-28 사용자 결정, PLAN §5.1) — 이 행들은 GEX 에서 빠진 채로 제외 OI 비율에만 든다
- 입력: 1.1 의 가격, OI
- 단위: 비율(0~1)
  - 분자 "제외된 OI" 는 §1.2 제외뿐 아니라 GEX 에 못 들어간 **모든** 종목의 OI — 가격 없음(§1.1), IV `invalid`(§1.5), 그 만기 F 없음(§1.3) **[확인 필요]**. 비율이 GEX 가 덮지 못한 OI 몫을 뜻하게 한다. KIS 폴백 `sigma`(`estimated`)로 그릭스를 계산한 종목은 포함이라 분자에 없다
- 예외: 전체 OI 합 0 이면 `excluded_oi_ratio = 0`
- 품질: 비율 자체는 ok. 순GEX 품질에 연동(§2.2)
- 테스트: 0.019/0.02 경계, 비율 계산, OI 0, KIS IV 가 있어도 `below_min_premium` 이 서고 GEX 에서 빠짐

### 1.3 합성선물 F
- 정의: 만기별 Black-76 기초자산
- 공식: 기준가 S_ref(선물 근월물 현재가)에 가장 가까운 행사가를 ATM 으로, ATM±2(최대 5개) 행사가 중 콜·풋 가격(1.1)이 모두 있는 행사가마다 `C − P + K`, 그 **중앙값**
  - ATM 을 고를 기준가는 선물 근월물 현재가 — 전광판 `atm_cls_name` 은 쓰지 않는다(probe_results #11a)
  - **S_ref 출처는 세션으로 고른다** (2026-09-29 사용자 결정, `core.forward.select_s_ref`): 주간 = 선물 전광판 `F`(`fut_board` 의 day·`F`·board 행), **야간 = 단건 `CM` 시세(`fut_board` 의 night·`CM`·single 행) — 전광판 `F` 는 야간에 주간 종가로 고정된다(probe_results #19, 분기 B)**. 다른 출처로 대체하지 않는다: 야간에 전광판 `F` 행이 더 최근이어도 쓰지 않고, `CM` 시세가 없으면 S_ref 없음(그 사이클 F 없음). 같은 세션 태그·근월물 코드·값 있음·invalid 아님·now 이전 행 중 가장 최근. 나이 90초 초과면 `stale`(PLAN §6.1 과 같은 기준), 행 품질이 더 나쁘면 그것. 이 S_ref 가 ATM 선정·근월물 + 베이시스 기준가(아래)·확정 베이시스(`confirm_basis`)에 같이 쓰인다
  - 콜·풋 중 하나라도 **전 세션 가격**(§1.1, 당일 거래량 0 인 last)인 행사가는 쓰지 않는다 **[확인 필요]** — 설정 `prev_session_in_forward`(기본 false, `core.gex.PREV_SESSION_IN_FORWARD`). 창은 넓히지 않는다(ATM±2 그대로) — 남은 행사가 수로 아래 품질·선물 대체 규칙을 그대로 적용(3 개 미만 `estimated`, 2 개 미만 선물 대체, 기준가 없이 0 개면 F 없음). 뺀 행사가는 `ForwardResult.prev_session_skipped`. 근거: `docs/validation_greeks.md` §5.4(위클리 F +28~31pt), 적용 뒤 결과는 같은 문서 §5.6(위클리 F 어긋남 사라짐, ATM±2 가 전부 전 세션 가격인 위클리는 F 없음)
- 입력: 만기 한 개의 행사가별 콜·풋 가격(전 세션 표시 포함), S_ref(= 근월물 선물가 — 주간 전광판 `F`·야간 단건 `CM`, 위), (분기 월물이면) 같은 결제월 선물 현재가, 그 만기의 확정 베이시스(아래 선물 교차 확인 — 호출 쪽이 들고 있는 값)
- 단위: pt
- 예외: 쓸 수 있는 행사가가 0 개면 — 선물 기준가가 있으면 선물 대체(아래), 없으면 F 없음 → 그 만기 전 지표 `invalid`. 중앙값이 0 이하여도 F 없음(방어선 — 기준가가 있어도 대체하지 않는다)
- 품질: 쓸 수 있는 행사가가 3 개 미만이면 `estimated` **[확인 필요]**. 만기가 분기 월물(3·6·9·12월 월물)이면 같은 결제월 선물가와 비교해 |F − 선물가| > 0.5pt 면 `estimated`(PLAN §5.1, 0.5 정확히는 ok, 사유 `futures_gap`). 여기에 아래 선물 교차 확인(2pt·선물 대체)이 더해진다
  - 분기 월물 판정: 만기 코드가 만기일의 YYYYMM 과 같고(월물 — 위클리는 YYMMWW, probe_results #12) 월이 선물 결제월(`core.specs`). 분기 월물인데 같은 결제월 선물가가 없으면 0.5pt 비교는 건너뛰고(품질 유지) **[확인 필요]**, 선물 교차 확인은 근월물 + 베이시스 기준가로 한다
  - ATM 선정용 행사가는 만기의 전 행사가(마스터)를 넘긴다 — 없으면 가격이 온 행사가로 고른다(전광판 잘림 시 ATM 이 밀릴 수 있다)
- **선물 교차 확인** (2026-09-28 사용자 결정 — 검증 수정 2, PLAN §6.1 "선물가와 합성선물 차이 > 2pt"). 여기 **[확인 필요]** 는 2026-09-29 새로 붙은 것이라 위 "모두 승인" 에 들지 않는다
  - 기준가(`core.forward.futures_reference` → `FuturesRef`, `ForwardResult.reference`):
    1. 같은 결제월 선물가(`same_month`) — 분기 월물만. 코스피200 선물은 분기물(3·6·9·12월, `core.specs`)이라 월물 202610·202611 은 같은 결제월 선물이 없다. 위클리는 분기월이라도 만기일이 선물 최종거래일(같은 결제월 월물 만기일)과 다르다(월물 만기주엔 월요일 위클리만 상장) — 여기 넣지 않고 2 로 간다 **[확인 필요]**
    2. 없으면 근월물 선물가(S_ref) + 확정 베이시스(`near_basis`). 확정 베이시스 = 그 만기의 마지막 품질 `ok` F − 그 스냅샷의 근월물 선물가(`core.forward.confirm_basis` — `ok` 가 아닌 F 로는 갱신하지 않는다, 선물 대체 F 도). 호출 쪽이 만기별로 들고 있다가 다음 스냅샷에 넘긴다(`evaluate_expiry(forward_basis=…)`). 근월물이 바뀌면(분기 만기일 15:20 뒤 차월물) 옛 근월물 기준이라 버린다. 선물가 그대로와 비교하지 않는 까닭: 같은 결제월이 아닌 만기는 선물과 만기가 달라 보유비용·배당만큼 늘 차이가 있다 — 2026-09-28 월물 202610 은 F − 근월물 선물가가 −5.80 / −4.62pt(급락일 선도 할인, `docs/validation_greeks.md` §5.2·§6 권고 4)라 2pt 로는 늘 걸린다
    3. 둘 다 없으면(그 만기의 첫 ok F 전 등) 교차 확인을 건너뛴다 — 품질 그대로, 기록 코드 `no_futures_ref`(`ForwardResult.notes` — 품질 사유가 아니다) **[확인 필요]**
  - 판정: |F − 기준가| > **2.0pt** 면 `estimated`, 사유 `futures_ref_gap` **[확인 필요]** — 2.0 정확히는 ok. 인자 `ref_tolerance`·`futures_ref_tolerance`(`core.forward.FUTURES_REF_TOLERANCE`). `ForwardResult.futures_gap = F − 기준가`
  - 선물 대체: 쓸 수 있는 행사가(전 세션 규칙 뒤)가 **2 개** 미만이고 기준가가 있으면 F = 기준가, `estimated`, 사유 `futures_fallback` **[확인 필요]** — 인자 `min_parity_strikes`(`core.forward.MIN_PARITY_STRIKES`). 행사가 1 개의 패리티 값은 버리고(`strikes` 비움) 기준가와 비교하지 않는다(`futures_gap` 없음). 기준가가 없으면 지금 규칙 그대로(1 개 → 그 행사가로 `few_strikes`, 0 개 → F 없음 `invalid`). 대체 F 로 IV·그릭스·GEX 를 내고 만기 품질은 `estimated` 다
  - 규칙 합성(사유가 하나라도 있으면 `estimated`, 사유는 모두 적는다):

    | 쓸 행사가 n(전 세션 규칙 뒤) | 기준가 | F | 사유 |
    |---|---|---|---|
    | ≥ 3 | 같은 결제월 선물가 | 중앙값 | \|gap\| > 0.5 `futures_gap`(PLAN §5.1), > 2.0 이면 `futures_ref_gap` 도 — 0.5 가 더 엄격해 2pt 규칙은 사유만 더한다 |
    | ≥ 3 | 근월물 + 베이시스 | 중앙값 | \|gap\| > 2.0 `futures_ref_gap`(0.5 규칙은 같은 결제월 선물가에만) |
    | ≥ 3 | 없음 | 중앙값 | 없음(`ok`) — `no_futures_ref` 기록 |
    | 2 | 있음 | 중앙값 | `few_strikes` + 위 gap 규칙 |
    | 2 | 없음 | 중앙값 | `few_strikes` |
    | 0·1 | 있음 | 기준가 | `futures_fallback` 하나(비교 없음) |
    | 1 | 없음 | 그 행사가 하나 | `few_strikes` |
    | 0 | 없음 | 없음 | `no_parity_strikes`(`invalid`) |
    | 0·1 | 근월물 + 이어 받은 베이시스(이월 1~2거래일) | 기준가 | `futures_fallback` + `basis_carried` |
    | 0·1 | 근월물 + 베이시스, 이월 상한 초과 | 없음 | `basis_carry_expired`(`invalid`) — 선물 대체 안 함 |
    | ≥ 2 | 근월물 + 베이시스, 이월 상한 초과 | 중앙값 | 교차 확인 없음(`few_strikes` 규칙만) — `basis_carry_expired` 기록 |

  - 실측(2026-09-28 두 스냅샷 — 14:27 의 ok F 로 베이시스를 확정하고 14:52 에 기준가로 씀): 202610 베이시스 −5.80 → 14:52 기준가 1086.70, F 1087.875(gap +1.175pt, ok) · 0DTE WKM 260904 −0.54 → gap +0.025pt · WKI 261001 −5.55 → gap +0.975pt(행사가 2 개라 `few_strikes`). 25분 사이 베이시스 움직임이 1.2pt 이하라 2pt 안이다. WKM 261001·202611 은 ok F 가 한 번도 없어 베이시스가 없다 → 기준가 없음, 전처럼 F 없음. 같은 결제월 선물이 있는 만기(202612 옵션)는 스냅샷에 없다
  - **베이시스 이월 상한** (2026-09-29 사용자 결정): 확정 베이시스는 확정한 거래일에서 **최대 2거래일**까지만 이어 쓴다 **[확인 필요]** — `core.forward.MAX_BASIS_CARRY_DAYS`, 인자 `max_basis_carry`. 나이 = `core.forward.basis_age` — 두 세션을 `TradingCalendar.session_day`(주간 = 귀속 거래일, 야간 = 그 밤이 이어지는 주간의 거래일, 즉 귀속 거래일의 앞 거래일)로 옮긴 뒤 그 사이 거래일 수(`trading_days_between`, (a, b]). **주간에 확정한 베이시스를 바로 이어지는 야간에 쓰면 0 — 같은 저녁의 야간은 이월로 보지 않는다, 다음 주간부터 1**(2026-09-29 사용자 결정). 예: 월 주간 확정 → 월 야간 0 · 화 주간 1 · 수 주간 2 · 목 주간 3(만료). 야간에 확정하면 그 밤의 주간과 한 단위라 다음 주간이 1(같은 규칙에서 따라 나온다). engine 은 시리즈별로 (베이시스, 확정 귀속 거래일, 확정 세션)을 들고 `futures_reference(basis_age=…)`·`evaluate_expiry(forward_basis_age=…)` 로 넘긴다
    - 상한 이내이고 나이 ≥ 1(이어 받은 베이시스)로 만든 선물 대체 F 는 사유에 `basis_carried` 를 더한다(`futures_fallback` 과 함께, `estimated`)
    - 상한을 넘으면(`FuturesRef.expired`) 선물 대체 F 를 쓰지 않는다 — 쓸 행사가 2 개 미만이면 F 없음 `invalid`(사유 `basis_carry_expired`; 기준가 없을 때의 1 개 → `few_strikes` 규칙도 쓰지 않는다), 2 개 이상이면 F 는 중앙값으로 내되 교차 확인을 건너뛴다(기록 코드 `basis_carry_expired`)
    - 교차 확인에만 이어 받은 베이시스를 쓴 경우(F 는 패리티 중앙값)는 F 가 그 베이시스를 쓰지 않았으니 품질 사유가 아니라 기록 코드 `basis_carried`(`notes`) — 2026-09-29 사용자 승인
    - 남은 점: 실제 베이시스가 2pt 넘게 바뀌면 그 뒤 F 는 `estimated` 라 베이시스가 다시 확정되지 않는다 — 이월 상한이 지나면 교차 확인이 꺼지고(`basis_carry_expired`) 다음 ok F 로 다시 확정된다
- 테스트: S_ref 출처(주간 전광판 F·야간 단건 CM·야간 전광판 무시·90초 stale), 베이시스 이월(나이 0·1·2 대체, 1·2 에서 `basis_carried`, 3 → 0·1 개 invalid·2 개 이상 교차 확인 건너뜀, 나이 — 월 주간 확정 → 월 야간 0·화 주간 1·수 주간 2·목 주간 3 만료, 금 야간(월 귀속)은 금요일, 야간 확정 → 다음 주간 1, 주말·추석·대체휴일), 중앙값(홀수·짝수 개), 한쪽 가격 없는 행사가 건너뜀, 0 개 → invalid, 분기 월물 ±0.5pt 경계, 전 세션 가격 행사가 건너뜀(창 안 넓힘·전부 전 세션 → invalid). 선물 교차 확인: 기준가 2.0pt 경계(2.0 ok·2.01 estimated), 같은 결제월 0.5·2.0 합성, 쓸 행사가 0·1 개 → 대체·2 개는 대체 안 함(기준가 없으면 전과 같음), 기준가 만들기(분기 월물·월물·위클리·분기월 위클리), 근월물 + 베이시스 경로·확정 베이시스 갱신(ok F 만)·왕복, 기준가 없음 → `no_futures_ref`, 전 세션 행 뺀 뒤 대체(전부 전 세션이면 기준가 있을 때만 F) — 속성 테스트 `tests/property/test_forward_properties.py`

### 1.4 잔존기간 T
- 정의: 만기까지 연 단위 시간
- 공식: `T = max(만기까지 남은 분, T_FLOOR) / (365 × 24 × 60)`, `T_FLOOR = 5분`, 만기 시각 = 만기일 15:20 KST (PLAN §2.3)
  - 만기일은 KIS 옵션 단건 현재가 `futs_last_tr_date`(2026-09-28 실측 필드)에서 얻는다. 계산(둘째 목요일 등)은 교차검증용
- 입력: 현재 시각(aware), 만기일
- 단위: 년
- 예외: 만기 뒤(음수)면 T_FLOOR 적용 — 만기 지난 종목은 상위에서 대상 만기 전환으로 빠진다
- 품질: ok
- 테스트: 15:20 정각·직전·직후, 5분 하한, 달력 분(야간 포함) 기준

### 1.5 IV
- 정의: 종목별 내재변동성
- 공식: Black-76 역산 `vollib.black.implied_volatility(price, F, K, r, T, flag)`
- 입력: 1.1 가격, 1.3 F, K, 1.4 T, (폴백) 같은 종목 KIS `hts_ints_vltl`(%)와 그 만기 T_KIS(§1.7)
- 단위: 연율(소수, 0.25 = 25%)
- 예외: 역산 실패(내재가치 아래, 수렴 실패, 1.2 로 제외) → KIS `hts_ints_vltl`(% 단위 → /100) 폴백 + `estimated` — 폴백 σ 는 아래 **T 환산** 으로 자체 T 에 옮긴다. KIS 값도 없거나 0 이면 `invalid`. 사유 코드 `IvResult.reason`:
  - `invalid_price`: 가격 nan·inf → 폴백 (1.1 이 먼저 걸러야 하는 방어선)
  - `below_min_premium`: 1.2 로 제외(가격 < 0.02pt, 0.02 는 계산) → 폴백
  - `below_intrinsic`: 가격 ≤ 할인 내재가치 `e^(−rT)·max(±(F − K), 0)` → 폴백. 내재가치와 **같은** 가격도 여기 넣는다 **[확인 필요]** — vollib 은 같으면 σ = 0 을 내므로 그대로 두면 아래 이상치(`invalid`)가 된다. 대안: 같으면 `invalid`
  - `above_max`: 가격 ≥ 무차익 상한(σ → ∞ 극한, 콜 `e^(−rT)·F`, 풋 `e^(−rT)·K`) → 폴백 **[확인 필요]** — 대안: §6.1 가격 이상치로 보고 `invalid`
  - `model_error`: vollib 예외 또는 nan 결과(수렴 실패) → 폴백
  - 폴백 KIS 값이 없음·0·nan 이면 `<사유>/kis_missing`, KIS 값 자체가 음수·inf·> 300% 면(T 환산 뒤에도 > 300%) `<사유>/kis_out_of_range`, KIS 값은 0 < σ ≤ 300% 인데 T 환산 뒤 > 300% 면 `<사유>/kis_out_of_range_rescaled`(§6.1 — 아래 T 환산) → `invalid`
- **KIS IV 폴백의 T 환산** (2026-09-28 사용자 결정 — 검증 수정 3. 여기 **[확인 필요]** 는 2026-09-29 새로 붙은 것이라 위 "모두 승인" 에 들지 않는다)
  - 공식: `σ = σ_KIS · √(T_KIS / T)` — 총분산 보존(`σ²·T = σ_KIS²·T_KIS`, `core.iv.rescale_sigma`). T = §1.4 자체 T(하한 5분 포함), T_KIS = §1.7 KIS 관례 `max(D, 0.5)/365`(`core.forward.kis_time_to_expiry` — `core.gex.evaluate_expiry` 가 만기마다 넘긴다, `ExpiryEval.T_kis`)
  - 까닭: KIS IV 는 T_KIS 로 역산된 값이다(§1.7). 자체 T 에 그대로 쓰면 σ√T 가 √(T/T_KIS) 배가 되어, 0DTE(T_KIS 0.5일 vs 자체 27~53분)는 폴백 행 감마가 사실상 0(KIS 감마 대비 −100%)이었다(`docs/validation_greeks.md` §5.3). 옮기면 r = 0 Black 가격·델타·감마(σ√T 로만 정해진다)가 (σ_KIS, T_KIS) 로 낸 값과 같다 — 베가·세타는 다르다. 그릭스·GEX 의 T 는 여전히 자체 T 다(T_KIS 는 폴백 σ 를 옮기는 데만 쓴다)
  - 모든 폴백 σ 에 적용한다 — §1.2 제외 종목의 표시용 σ, §3.4 Flip·§3.7 ATM IV 가 쓰는 sigma 도 옮긴 값이다
  - 이상치(§6.1, ≤ 0·> 300%)는 **옮긴 뒤 σ**(실제로 쓰는 값)로 판정한다 **[확인 필요]** — 대안: 옮기기 전 KIS 값으로 판정. 음수·inf KIS 값은 옮기기 전에 `kis_out_of_range`. 옮긴 뒤 > 300% 면 옮기기 전 KIS 값이 범위 안이었을 때만 `kis_out_of_range_rescaled`(옮긴 탓), 아니면 `kis_out_of_range`. 결과: 배율 √(T_KIS/T) 가 1 보다 크면(장중 만기일·15:20 뒤) 더 엄격하다 — T 하한 5분이면 √(720/5) = 12 배라 σ_KIS > 25% 인 폴백 행은 `invalid` 로 GEX 에서 빠진다. 자체 T 가 T_KIS 보다 길면(만기일 00:00~03:20 야간 — 15:20 까지 12시간 넘게 남음) 배율 < 1 이라 KIS 300% 초과도 옮겨서 쓸 수 있다
  - T_KIS 가 없으면 옮기지 않고 σ_KIS 그대로 쓴다 **[확인 필요]** — 만기일이 지난 만기(now 의 KST 날짜 > 만기일 — §1.4 대로 상위에서 빠질 만기, §1.7 은 ValueError)와 `core.iv.implied_vol` 에 `t_kis` 를 주지 않은 호출. 대안: `invalid`
  - 기록(`IvResult`): 폴백 = `source` `kis`(품질 `estimated`), `t_kis` = 옮길 때 쓴 T_KIS(없으면 None), `rescaled` = 옮기며 σ 가 바뀌었나(T_KIS ≠ T). 만기 D 일 전 15:20 KST 정각엔 두 T 가 같은 값(D/365)이라 `t_kis` 는 있고 `rescaled` 는 False(σ_KIS 그대로). 옮긴 σ 가 이상치라 `invalid` 가 된 폴백(사유 `…/kis_out_of_range`·`…/kis_out_of_range_rescaled`)도 `t_kis`·`rescaled` 를 남긴다 — 폴백을 시도했다는 것은 사유의 `/kis_…`, 옮겼다는 것은 `t_kis`·`rescaled`, 옮긴 탓에 빠졌다는 것은 `…_rescaled`(이때 `rescaled` 는 늘 True)로 읽는다. 자체 역산(`ok`)·`model_out_of_range`·`kis_missing`·옮기기 전 음수·inf 엔 둘 다 없다(None·False)
  - 실측(2026-09-28 두 스냅샷 14:27 / 14:52, 옮기기 전 → 뒤): 0DTE WKM 260904 배율 3.70 / 5.14. GEX 에 든 폴백 행 29 → 25 / 32 → 18 — 빠진 4 / 14 행은 옮긴 σ 가 300% 를 넘은 깊은 ITM 풋(K − F 35~133pt, σ_KIS 59~139%, OI 12 / 250). 0DTE 순GEX −245.49 → −888.65억 / −1,876.17 → −3,317.77억(14:52 1100 P OI 1,040 한 행이 0 → −905.91억). 월물 202610·위클리 WKI 261001 은 배율 0.994~0.999 라 순GEX 차 0.02억 이하. §1.2 제외 폴백 행 중 11 / 32 행은 표시용 σ 가 300% 를 넘어 `invalid` 가 됐다(원래 GEX 제외라 순GEX 와 무관). 이렇게 `invalid` 가 된 행(GEX 4 / 14 · 표시용 11 / 32)은 모두 사유 `…/kis_out_of_range_rescaled`, `t_kis` 0.5일·`rescaled` True 로 남는다(KIS 값 자체 > 300% 인 행은 없었다). 전후 비교 전체는 `docs/validation_greeks.md`(검증 수정 4)
- 품질: 자체 역산 ok / 폴백 estimated / 없음 invalid. **역산 결과** IV ≤ 0 또는 > 300% 는 §6.1 이상치 → KIS 폴백 없이 `invalid`(`model_out_of_range`) — 역산 실패가 아니라 가격·F 이상의 신호라 KIS 값으로 덮지 않는다
- 테스트: 문헌·알려진 값 왕복(가격 → IV → 가격), 내재가치 아래·같음 → 폴백, 상한 이상 → 폴백, 폴백도 없음 → invalid, 역산 결과 이상치 → KIS 가 있어도 invalid, KIS 값 0·음수·> 300% 경계. T 환산(`tests/unit/test_iv.py`·`test_gex.py`): 0DTE(T_KIS 0.5일 vs 53분 — 옮긴 σ 의 델타·감마 = (σ_KIS, T_KIS) 값, 옮기기 전 감마 ≈ 0), 자체 T → 0(53·27·10·6·5분에서 σ√T 일정·감마 유지), T 하한 5분의 이상치 판정(σ_KIS 24% → 288% 사용, 26% → 312% invalid `kis_out_of_range_rescaled` — 기록 유지, GEX 에서 빠지고 OI 는 제외 비율에), KIS 값 자체 > 300% 는 옮겨도 `kis_out_of_range`, 자체 T 가 더 길 때(배율 < 1 — KIS 310% 사용), T_KIS = 자체 T 항등(15:20 KST 정각), 역산 성공이면 옮기지 않음, T_KIS 없음(만기 지남) → 그대로, `IvResult` 기록 불변식(invalid 폴백의 기록 포함). 속성(`tests/property/test_pricing_properties.py`): 옮긴 폴백 σ 가 KIS 총분산(σ²T)을 보존하고 델타·감마·가격이 (σ_KIS, T_KIS) 값과 같다(> 300% 면 `…_rescaled` 와 기록), 임의 T_KIS 에도 쓰는 σ 는 0 < σ ≤ 300%, 옮긴 기록은 KIS 값을 옮긴 폴백(쓴 것·이상치로 버린 것)에만

### 1.6 그릭스
- 정의: 자체 IV 로 계산한 Black-76 해석적 그릭스
- 공식: `vollib.black.greeks.analytical` 의 delta, gamma, vega, theta (r = 0)
- 입력: F, K, T, IV, 콜/풋
- 단위: §0 그릭스 단위
- 예외: IV 가 invalid 이거나 1.2 로 제외(`IvResult.below_min_premium`)면 계산 안 함 — `sigma` 가 있다는 것만으로 부르지 않는다
- 품질: IV 품질을 그대로
- 테스트: 감마 ≥ 0, 같은 K·T·σ 에서 콜·풋 감마 동일, 콜 델타 − 풋 델타 = 1(r=0), 풋-콜 패리티 `C − P = F − K`(r=0) — hypothesis 속성 테스트

### 1.7 KIS 관례 (재현 검사·IV 폴백용)

2026-09-29 작성(2026-09-28 사용자 결정 — PLAN §6.2 재현 검사). 여기 **[확인 필요]** 는 새로 붙은 것이라 위 "모두 승인" 에 들지 않는다.

- 정의: KIS 가 옵션 시세에 붙여 주는 그릭스(`delta_val`·`gama`)와 IV(`hts_ints_vltl`)를 매긴 입력(σ·S·T). 추측하지 않고 2026-09-28 주간 스냅샷 두 개(14:27·14:52)로 정했다 — 근거·잔차는 `docs/validation_greeks.md` §0
- 쓰임: ① PLAN §6.2 재현 검사 — 이 입력을 자체 `core.greeks` 에 넣어 KIS 값을 다시 내 자체 Black-76 구현을 검증한다 ② §1.5 KIS IV 폴백 σ 를 자체 T 로 옮길 때의 T_KIS(`σ_KIS·√(T_KIS/T)` — §1.5 T 환산)
- **KIS 그릭스는 어떤 경우에도 GEX·DEX·레벨의 입력이나 폴백으로 쓰지 않는다**(2026-09-28 사용자 결정). 저장(`chain_snapshots`·`opt_ticks`)·표시와 재현 검사에만 쓴다. GEX 는 자체 IV(§1.5)와 자체 T(§1.4)만 쓴다 — `tests/unit/test_gex.py` 가 core 에 KIS 그릭스 필드가 없음을 고정한다
- 모형: Black(r = 0) — `core.greeks` 와 같은 식. 콜 Δ − 풋 Δ = 1(202611 전광판 같은 행사가 쌍이 모두 1.0000 — 그릭스에는 할인이 없다). KIS 이론가 `hts_thpr` 는 할인이 있다(패리티 할인 D ≈ 0.99941, 10일 기준 r ≈ 2.2%) — 그릭스와 따로다

#### T 관례 — 함수 명세 `kis_time_to_expiry(now, expiry_date) -> float` (년)

- 구현: `core.forward.kis_time_to_expiry`(§1.4 `time_to_expiry` 옆) 하나 — 재현 검사 스크립트(`scripts/validate_greeks.py`)도 이 함수를 쓴다(따로 두지 않는다). §1.5 폴백 σ 를 옮길 때와 재현 검사에만 쓰고 그릭스·GEX 의 T 는 §1.4 자체 T 다
- 공식: `D = (expiry_date − now 의 KST 달력 날짜).days`, `T_KIS = max(D, 0.5) / 365`
- 입력: `now` aware datetime(naive 는 ValueError), `expiry_date` 는 §1.4 와 같은 만기일(KIS `futs_last_tr_date`, 시각 없는 date — datetime 은 TypeError)
- 성질: 시각을 보지 않는다 — 만기일(D = 0)은 장중 어느 시각이든 0.5일. 주말·휴일도 센다(달력일). `D = hts_rmnn_dynu − 1`(KIS 잔존일수는 오늘·만기일을 다 센다 — 202610 11, 선물 202612 74·202703 165 가 모두 D + 1). 만기 D 일 전 15:20 KST 정각엔 §1.4 자체 T(D × 1440분)와 같은 float 다 — D ≥ 1 이면 그날 15:20 전엔 자체 T 가 길고 15:20 뒤(야간 자정 전 포함)엔 짧다. 만기일(D = 0)은 03:20 전(15:20 까지 12시간 넘게 남은 야간)만 자체 T 가 길다
- 예외: D < 0(만기일이 지남) → ValueError. `core.gex.evaluate_expiry` 는 만기일이 지난 만기엔 부르지 않고 T_KIS 없음(None — §1.5 폴백 σ 를 옮기지 않음)으로 둔다 **[확인 필요]**
- 근거(2026-09-28 14:27 / 14:52 — 수치는 모두 `scripts/validate_greeks.py` 출력, validation §7): 202610 D = 10 — 델타 곡면 T 10.0008 / 10.0002일(붓스트랩 95% 구간 안 후보는 달력일뿐, 달력 분 10.0366 은 14:27 구간 밖), 재현 ATM±5 전 행(전체 행은 반올림 보정 기준 전 행), T = 11일(`hts_rmnn_dynu`)이면 ATM±5 감마 0/22 통과(전체 행 평이 2/266 · 4/264, 반올림 보정 113/266 · 112/264) · 202611 D = 45(만기일 11-12 계산값) — 델타 곡면 T 44.9955 / 44.9941일(구간 폭 ±0.008일, 달력 분 45.0366·45.0189 는 둘 다 밖), T = 46일이면 델타 절반 탈락 · 0DTE(D = 0) — KIS Δ·Γ 가 함의하는 T 중앙 0.51 / 0.51일(15:20 까지 53 / 27분과 무관), KIS IV 가 함의하는 T 0.53 / 0.52일, T = 달력 분이면 감마 0건 통과, T 만 0.10~1.00일로 훑으면 ATM±5 감마 |오차| 중앙이 0.50일에서 최소(5.43 / 4.84%, 0.45일 8.94 / 6.11%, 0.55일 7.09 / 7.21%) · WKI 261001 D = 3 → 3.05 / 3.06일 · WKM 261001 D = 8(10-03~05 주말·대체공휴일 포함) → 8.19 / 8.20일
- KIS IV(`hts_ints_vltl`)도 같은 T 로 역산된 값이다(validation §5.2~§5.4: 월물 IV 가 함의하는 T 중앙 9.98 / 10.01일, 0DTE 0.53 / 0.52일, 위클리 무거래 행 8일)
- **[확인 필요]** (실측 전 기본값): ① 야간 세션은 `now` 의 KST 달력 날짜로 센다(귀속 거래일 T+1 이 아니다) — 야간 스냅샷에 그릭스가 없다 ② 만기일 오전·만기 전날(D = 1)도 같은 식 — 두 스냅샷 모두 만기일 오후다
- 테스트: 만기일 여러 시각 0.5일, 주말 포함 날수, `hts_rmnn_dynu − 1`, UTC 입력의 KST 날짜, 만기 지남·naive → ValueError — `tests/unit/test_validate_greeks.py`(실측 `hts_rmnn_dynu` 대조·재현 검사 T 가 이 함수 값)와 `tests/unit/test_forward.py`(core — 만기 전날·야간 자정 전후 [확인 필요 기본값], 15:20 KST 정각 자체 T 와 같음, datetime 만기일 거부 포함)

#### σ·S 관례

- σ: 월물은 행 `hist_vltl`(기초자산 역사적 변동성 %, 장중 소수 셋째 자리가 바뀐다 — 75.38~75.41). 위클리(0DTE 포함)는 행 `hist_vltl` 이 0 이라 — 당일 거래가 있는 행(`acml_vol` > 0, 모르면 이쪽)은 그 행 KIS IV, 무거래 행은 기초 HV(같은 스냅샷 월물 `hist_vltl` 중앙값)
  - 근거: 월물 σ = hist 면 ATM±5 전 행 재현(전체 행은 반올림 보정 기준 전 행), KIS IV·자체 IV 면 감마 중앙 ≈ 71% 어긋남(validation §5.1). 위클리 무거래 행 Δ·Γ 가 함의하는 σ*(T_KIS) 중앙 75.8~75.9%, 당일 거래 행 σ* 와 KIS IV 차 중앙 0.5~1.4%p(0DTE·WKI)
- S: 월물은 KIS 이론가 패리티 선도 — `thpr_C − thpr_P = A − D·K` 회귀의 A/D(만기별, 행사가 3개 이상·최대 잔차 < 1pt). 202610 1095.16 / 1092.94(선물 1095.10 / 1092.50), 202611 1097.98 / 1095.79(선물보다 +2.9 / +3.3pt — S = 선물가면 202611 델타 절반 탈락). 현물 + 보유비용 − 배당으로 보인다(스냅샷에 현물·금리가 없어 미확인)
  - 위클리: KIS 가 행마다 계산한 시점의 기초자산이라 스냅샷에서 알 수 없다 — 당일 거래 행은 현재 수준 근처(0DTE ATM±5 에서 Δ·Γ 가 함의하는 S* 중앙 1094.80 / 1092.59, 행별 1093.7~1099.7 / 1092.2~1093.1), 무거래 행은 ≈ 1126(전 세션 수준, 25분 사이 그대로). 위클리 이론가는 대부분 0 이라 패리티가 안 서(잔차 38~62pt) 재현 검사는 선물 근월물 S_ref 로 두고 실패 원인을 S 로 적는다
  - 참고: 전광판 `invl_val`(내재가치)이 함의하는 S 는 전광판마다 하나이고 조회 시점 현물이다(14:27 1094.61~1094.99, 14:52 1091.99~1092.50) — 그릭스 S 가 아니다(월물 S 로 쓰면 202611 델타 절반 탈락)

#### 재현 검사 판정 (PLAN §6.2)

- 오차(판정, 2026-09-28 사용자 결정): 평이 상대오차 `e = (자체 − KIS) / |KIS|` — 크기는 `|자체/KIS − 1|` 그대로, 부호는 자체 − KIS(풋도 반올림 보정 오차와 같은 부호). KIS 0(값 없음)은 뺀다
- 허용: |e| ≤ 1% **[확인 필요]**(사용자 결정 기본값). 범위: ATM±5(S_ref 기준 — 받은 행사가가 못 덮으면 없음)와 KIS 값이 있는 전체 행을 따로 센다
- 반올림 보정 오차(제안 — 판정 아님, **[확인 필요]**): `e_r = sign(자체 − KIS) × max(|자체 − KIS| − 0.00005, 0) / |KIS|` — KIS 값이 소수 4자리라 반올림 구간(±0.00005) 안이면 0. 판정 오차와 나란히 센다(같은 허용 1%). 작은 KIS 값은 반올림 폭만으로 1% 를 넘을 수 있다 — 감마 0.0029 ±1.7%, 0.0011 ±4.5%, 0.0002 ±25%, 델타 0.0016 ±3.1%
  - 실측(2026-09-28 두 스냅샷, `docs/validation_greeks.md` §0.2): 월물 202610 ATM±5 는 **두 기준 모두** 델타·감마 22/22(평이 감마 |e| 최대 0.67 / 0.85%). 전체 행은 반올림 보정에서만 전 행이다 — 평이 1% 로는 202610 감마 70/266 · 74/264, 델타 271/282 · 267/282, 202611 감마 32/200 · 34/200. 평이 1% 를 넘는 행은 KIS 값이 작아 반올림 폭이 1% 에 가깝거나 넘는 행이다: 202610 날개 감마 0.0001~0.0008, ATM±5 바로 밖 감마 0.0027~0.0029, 깊은 콜 델타 0.0016~0.0047, 202611 감마 0.0006~0.0011. 반올림 보정 |e_r| 는 월물 전 행 0.35% 이하
- 실패 원인: 판정(평이 1%) 밖 행마다 가린다 — 같은 입력이 반올림 보정 오차로는 1% 안이면 `반올림`(1% 를 넘은 몫이 KIS 4자리 반올림 폭 안 — 입력은 맞다). 아니면 KIS Δ·Γ 한 쌍이 함의하는 (S*, x* = σ√T)로 입력을 하나씩 바꿔 반올림 보정 1% 안에 드는지 본다 — S 만 S* 로 바꿔 들면 `S`, σ 만 x*/√T_KIS 로 바꿔 들면 `σ√T`, 둘 다면 `S+σ√T`, 한 쌍을 못 풀면(Δ 0.02~0.98 밖·Γ 0) `풀이 없음`. T 는 시리즈 공통이라 행별 T* = (x*/σ)² 중앙값과 델타 곡면 T 로 따로 본다
- 품질: 판정이 아니라 경고·기록. 월물 ATM±5 통과율이 자체 공식 구현의 검증이다(전체 행의 `반올림` 은 공식 문제가 아니다). 위클리는 KIS 입력(행별 S)을 스냅샷에서 알 수 없어 통과를 기대하지 않는다 — 원인 분포를 기록한다
- 테스트: `tests/unit/test_validate_greeks.py` — 평이 상대오차·반올림 구간 오차, 날개 감마 `반올림` 원인(합성), σ·S 관례, 원인 가르기(합성), fixture(KBJ P1 부터 합성 — 원래 실측 발췌와 같은 자리) 월물 ATM±5 전 행·전체 행 반올림 보정 전 행·202611 S=선물 탈락·0DTE 0.5일·위클리 무거래 행 S ≈ 1126

## 2. GEX (PLAN §2.4)

### 2.1 행사가별 GEX
- 정의: 딜러 1% 기초자산 변동당 감마 익스포저(naive 딜러: 콜 롱·풋 숏)
- 공식: `GEX_call(K) = + Γ_c(K) × OI_c(K) × m × F² × 0.01`, `GEX_put(K) = − Γ_p(K) × OI_p(K) × m × F² × 0.01`, `GEX(K) = GEX_call(K) + GEX_put(K)`
- 입력: 1.6 감마, OI(KIS `hts_otst_stpl_qty`), m, 그 만기 F
- 단위: 원 / 기초자산 1%. 표시 억원(÷ 1e8)
- 예외: 1.2 로 제외된 종목(`IvResult.below_min_premium`, IV 품질과 무관)은 0 으로 두고 `excluded_oi_ratio` 에 반영. 가격 없음·IV `invalid` 종목도 같다(§1.2 분자). 그 외 종목은 IV `sigma`(자체 또는 KIS 폴백)로 §1.6 그릭스를 계산해 포함
- 품질: 입력 품질 합성 — 그 만기 F 품질과 포함 종목 IV 품질 중 가장 나쁜 것(제외 종목의 IV 품질은 비율로만 반영)
  - 종목 품질 = IV 품질, 전 세션 가격(§1.1)이면 `estimated` 이상 **[확인 필요]** — 포함 종목 하나라도 전 세션 가격이면 그 만기는 `estimated`(OI 몫과 무관 — §1.2 비율 같은 OI 가중은 명세에 없다)
  - 행사가별 표는 행과 함께 품질·`excluded_oi_ratio` 를 낸다 — 같은 종목의 §2.2 합산과 같은 규칙(제외 비율 > 10% estimated, F 없음·OI 전부 제외 invalid). 여러 만기를 합친 표(§3.1)는 범위 전체 비율로 다시 잰다
- 테스트: `tests/test_sign_conventions.py` — 콜 OI 만 있으면 양수, 풋 OI 만 있으면 음수, 같은 감마·OI 면 콜+풋 = 0, 단위 계산 예시 값 고정

### 2.2 순GEX
- 정의: 범위 안 모든 행사가·만기의 GEX 합. 양수 롱감마, 음수 숏감마
- 공식: `Σ GEX(K)` — 범위: 전체 / 최근접 만기 / 0DTE(당일 만기) 각각
- 단위: 원/1% (표시 억원)
- 예외: 범위에 종목 없으면 null
  - 최근접 = 범위 후보 중 가장 이른 만기일(만기 지난 만기는 상위에서 뺀다, §1.4). 0DTE = 만기일이 그 거래일인 만기
  - 범위가 비면(만기일이 아닌 날의 0DTE 등) 값 null·품질 ok — 해당 없음이지 품질 문제가 아니다 **[확인 필요]**
  - F 없는 만기가 범위에 있으면 품질 `invalid`(§1.3 "그 만기 전 지표 invalid"), 값은 F 있는 만기만의 합. F 있는 만기가 하나도 없으면 null
  - 범위에 OI 가 있는데 전부 제외됐으면(전체 OI > 0, 포함 OI = 0 — `excluded_oi_ratio = 1`) 값 null·품질 `invalid` **[확인 필요]** — 합이 0 이 아니라 덮은 OI 가 없어 모른다. F 는 있어도 모든 종목이 §1.2·§1.5 로 빠진 경우. 전체 OI 가 0 이면(포지션 없음) 값 0·제외 비율 0 그대로
- 품질: `excluded_oi_ratio > 10%` 면 `estimated`(PLAN, 10% 정확히는 아님), 입력 품질 합성
- 테스트: 범위별 합, 10% 경계, 전부 제외 → null·invalid, OI 전부 0 → 0

### 2.3 DEX
- 정의: 딜러 델타 익스포저
- 공식: `DEX = Σ OI_c·Δ_c·m·F − Σ OI_p·Δ_p·m·F` (풋 델타 음수라 딜러 풋 숏 델타는 양수)
- 단위: 원(기초 가치 기준). 억원 표시
- 예외·품질: §2.2 와 같게 — 같은 제외 규칙·범위, `excluded_oi_ratio > 10%` 면 `estimated`, 입력 품질 합성 **[확인 필요]**
- 테스트: 부호(콜만 → 양수, 풋만 → 양수 — 딜러 풋 숏의 델타는 양수)

## 3. 핵심 레벨 (PLAN §5.2)

### 3.1 콜월
- 정의·공식: 콜 GEX(`GEX_call`) 최대 행사가
- 예외: 동률이면 현재가(F)에 가까운 쪽. 콜 OI 전부 0 이면 null
  - 범위(만기)는 호출 쪽이 고른다(§2.2 all·nearest 등). 행사가별 GEX 는 범위 안 만기들의 합이고 F 없는 만기는 빠진다
  - "현재가(F)" 는 §3.4 기준 F(F 있는 만기 중 가장 이른 만기의 F). 거리도 같으면 낮은 행사가(`core.chain` ATM 동률 규칙과 같게) **[확인 필요]**
  - null 은 콜 GEX 가 0 보다 큰 행사가가 없을 때 — 콜 OI 전부 0 과 콜이 전부 GEX 에서 제외된 경우(§2.1) **[확인 필요]**
- 품질: 범위 순GEX 품질(§2.2 — F 없는 만기 invalid, 제외 OI 비율 > 10% estimated, 입력 품질 합성) **[확인 필요]**. §3.2·§3.3 도 같다
- 테스트: 최대·동률 규칙

### 3.2 풋월
- 정의·공식: 풋 GEX 절대값(`|GEX_put|`) 최대 행사가
- 예외: 동률 규칙 3.1 과 같음
- 테스트: 최대·동률

### 3.3 절대감마 행사가
- 공식: `|GEX_call(K)| + |GEX_put(K)|` 최대 행사가. 동률 규칙 3.1

### 3.4 감마 전환점(Flip)
- 정의: 총 GEX 가 부호를 바꾸는 가상 기초자산 가격
- 공식: 격자 `F′ ∈ [0.95F, 1.05F]`, 0.25pt 간격. 각 F′ 에서 sticky-strike IV(종목별 IV 고정)로 감마를 다시 계산해 총 GEX(F′)(§2.1 식의 F 자리에 F′) 재계산 → 이웃 격자점 사이 부호가 바뀌는 구간마다 선형보간으로 교차점
  - 만기 여러 개를 합칠 때 만기별 F 가 다르다 → 각 만기 F 를 같은 비율 `F′/F_기준`으로 옮긴다(기준 = 최근접 만기 F) **[확인 필요]**
  - 최근접 만기에 F 가 없으면 기준은 F 있는 만기 중 가장 이른 만기의 F **[확인 필요]** — 그 범위 품질은 §2.2 로 invalid
  - 격자: `0.95F + 0.25·i` 로 1.05F 를 넘지 않게, 1.05F 가 격자에 없으면(1e-9pt 넘게 떨어짐) 끝점으로 덧붙인다 — 마지막 간격만 0.25pt 보다 짧다 **[확인 필요]**
  - 재계산 대상은 §2.1 GEX 에 든 종목(제외 종목은 0). 종목 IV 는 그 종목 sigma(자체 역산 또는 KIS 폴백), T 는 그대로
- 단위: pt
- 예외: 교차점 0 개 → `none`(범위 전체 같은 부호), 2 개 이상 → F 에 가장 가까운 것 + `multi_cross = true`. 격자점에서 정확히 0 이고 0 이 아닌 양옆 가장 가까운 값의 부호가 다르면 그 점이 교차점(0 이 여럿 이어지면 그 구간 가운데) **[확인 필요]**
  - 같은 부호 사이의 0(접함)·격자 끝의 0·격자 전부 0 은 부호가 바뀌지 않으니 교차가 아니다(정의·PLAN §5.2 "부호 변화 지점"). 0DTE 막판(T 하한 5분)엔 행사가에서 먼 F′ 의 감마가 언더플로로 정확히 0 이 되어 0 구간이 생기는데, "0 인 격자점은 전부 교차점" 으로 읽으면 그 구간 격자점마다 가짜 교차점이 된다. 그 해석은 인자 `zero_rule="every_zero"` 로 고를 수 있다(기본 `"sign_change"`, `core.levels.FLIP_ZERO_RULE`)
  - F 와의 거리가 같은 교차점이 둘이면 낮은 쪽 **[확인 필요]**
- 품질: 입력 품질 합성 — 범위 순GEX 품질(§3.1 과 같다)
- 테스트: 한 번 교차(선형 체인 합성 예제), 0 번(전부 콜) → none, 두 번 → 가장 가까운 것·multi_cross, 격자 끝, 정확히 0 인 격자점(양옆 부호·접함·끝·0 구간, `every_zero` 설정)

### 3.5 전환점 거리
- 공식: `(F − Flip) / F × 100` (%). Flip 없으면 null
  - F 는 §3.4 기준 F

### 3.6 0DTE 레벨
- 정의: 당일 만기만으로 3.1·3.2·3.4. 만기일에만 활성(그 외 null)
  - "당일" 은 귀속 거래일(§2.2 `trade_date`) — 야간은 T+1 귀속이라 금요일 야간엔 월요일 만기 위클리가 0DTE(§2.2 0DTE 정의 그대로). 기준 F 는 그 만기 F

### 3.7 ATM IV (PLAN §5.4 — 기대변동폭 입력)
- 공식: F 를 사이에 두는 두 행사가 K1 ≤ F ≤ K2 에서 각각 `(IV_call + IV_put) / 2`, F 로 선형보간. F 가 행사가와 같으면 그 행사가 값
- 예외: 둘 중 한쪽 행사가의 콜·풋 IV 가 모두 없으면 있는 한쪽 값만 쓰고 `estimated` **[확인 필요]**, 둘 다 없으면 `invalid`
  - 한 행사가에 콜·풋 IV 중 하나만 있으면 그 값을 그 행사가 값으로 쓰고 `estimated` **[확인 필요]**
  - F 가 행사가 범위 밖이면 없는 쪽 행사가를 "IV 없음" 으로 보고 위 규칙(있는 쪽만, `estimated`) **[확인 필요]**. F 가 행사가와 같은데 그 행사가 IV 가 없으면 `invalid`
  - IV 는 sigma 가 있는 종목 — 자체 역산(ok)·KIS 폴백(estimated). §1.2 제외 종목의 표시용 sigma 도 쓴다. 품질은 종목 품질(§2.1 — 전 세션 가격이면 `estimated`)
- 품질: 만기 F 품질과 쓴 IV 품질 합성, 위 예외면 `estimated` 이상

### 3.8 기대변동폭 ±1σ
- 공식: `F × IV_ATM × √Δt`
  - Δt(년): 세션 중이면 현재 세션 종료까지, 세션 밖이면 다음 세션 전체의 남은 분을 **달력 기준으로 연환산 — ÷ (365 × 24 × 60)**. 2026-09-28 사용자 결정: IV 가 달력 분 T(§1.4)로 역산되므로 Δt 도 같은 기준. PLAN 의 "거래시간 기준 연환산"(÷ 252 × 420분, 주간 한 번 = 1/252년)은 옵션 인자(`basis="trading"`)로만 둔다. 두 값의 차이는 `docs/validation_greeks.md` 에 나란히 적는다
  - 세션 경계는 `core.calendar.session_bounds`(주간 15:45, 야간 익일 06:00). 만기일 종목의 15:20 종료는 반영하지 않는다(기초자산 기준) **[확인 필요]**. 세션 밖이면 지금 뒤에 시작하는 첫 세션(휴장·야간 미개장 건너뜀) 전체 — 주간 420분, 야간 720분
  - F·IV_ATM 은 같은 만기(보통 최근접)
- 품질: IV_ATM 품질 불량(invalid)이면 `invalid`. 아니면 IV_ATM 품질(§3.7 — F 품질 포함)
- 테스트: 세션 중·세션 밖, 식 값

### 3.9 기대범위 내 상위 GEX 레벨
- 공식: `[F − 1σ, F + 1σ]` 안 행사가를 `|GEX(K)|` 내림차순 상위 N(기본 5)
- 예외: 범위 안 행사가가 N 개보다 적으면 있는 만큼
  - F·1σ 는 §3.8 결과, 범위 양 끝 포함. GEX(K) 는 호출 쪽 범위(만기)의 행사가별 합(§3.1), GEX 0 인 행사가도 후보
  - `|GEX(K)|` 동률이면 F 에 가까운 쪽, 거리도 같으면 낮은 행사가 **[확인 필요]**
- 품질: §3.8 품질과 범위 순GEX 품질 합성. §3.8 이 invalid 면 빈 목록·`invalid`

### 3.10 만기별 감마 소멸액
- 공식: 만기별 순GEX 합(§2.2 를 만기 하나 범위로)
- 품질: 만기 목록 품질 연동 — 만기 목록(KIS 월물리스트) 품질을 인자로 받아 만기마다 순GEX 품질(§2.2)에 합성 **[확인 필요]**

---

# Phase 3 확장 지표 (PLAN §5.3~§5.6, §6.4)

2026-09-29 작성. 아래 **[확인 필요]** 는 이 날짜에 새로 붙은 것이라 위의 "모두 승인" 에 들지 않는다 — 기본값으로 구현하고 사용자 확인을 받는다.
공통: 그릭스·IV 는 §1.5·§1.6 의 자체 값만 쓴다(KIS 그릭은 어떤 경우에도 폴백으로 쓰지 않는다 — 2026-09-28 사용자 결정). 부호는 §2.3 DEX 와 같다: 딜러 콜 롱 `+`, 풋 숏 `−`(CLAUDE.md "Vanna·Charm 동일 부호").

## 4. 익스포저 (PLAN §5.3)

### 4.1 Vanna 익스포저
- 정의: IV 1%p 변화당 딜러 델타 변화(원)
- 공식: `VEX = Σ OI_c·Vanna_c·m·F·0.01 − Σ OI_p·Vanna_p·m·F·0.01`, `Vanna = ∂Δ/∂σ`. Black-76(r = 0)에서 `Vanna = −φ(d1)·d2/σ`(콜·풋 같음)
- 입력: §1.6 그릭스에 쓰는 F·K·T·σ, OI, m
- 단위: 원 / IV 1%p (표시 억원)
- 예외·품질: §2.2 와 같다(제외 OI 비율 > 10% 면 estimated, 범위 OI 전부 제외면 null·invalid)
- 테스트: 해석식 대 수치 미분(σ ± h), 콜만 있을 때 부호, 콜·풋 같은 OI 면 상쇄
- 구현(2026-09-29): `core.greeks.vanna`(해석식)·`core.metrics.exposure.vex`(범위 all·nearest·0dte — 값·null·품질·제외 비율은 `core.gex.net_gex` 것을 그대로) — 테스트 `tests/unit/test_greeks.py`(σ ± h 중앙 차분·콜·풋 같음)·`tests/unit/test_exposure.py`(단위·부호·상쇄·DEX 의 σ 미분 × 0.01·§2.2 예외)·`tests/property/test_exposure_properties.py`

### 4.2 Charm 익스포저
- 정의: 달력 1일 경과당 딜러 델타 변화(원)
- 공식: `CEX = Σ OI_c·Charm_c·m·F − Σ OI_p·Charm_p·m·F`, `Charm = −∂Δ/∂T` 를 달력 1일로 환산: Black-76(r = 0)에서 `Charm = φ(d1)·d2 / (2T) / 365`(콜·풋 같음). 계산 주기 2분(PLAN)
- 예외: T 는 §1.4 하한(5분) 그대로. 품질 §4.1 과 같음
- 테스트: 해석식 대 수치 미분(T ± h), 부호, 만기 가까울수록 크기 증가
- 구현(2026-09-29): `core.greeks.charm`·`core.metrics.exposure.cex`(T 는 만기의 자체 T — §1.4 하한 5분 그대로) — 테스트는 §4.1 과 같은 파일(T ± h 중앙 차분·−DEX 의 T 미분 ÷ 365·만기 가까울수록 |CEX| 증가·T 하한)
- engine(2026-09-29): 사이클 지표 등록 `services/engine/extended.py`(vex·cex·gex_pc·iv_term·skew_25d — 새 지표 기본 shadow, 품질에 범위·시리즈 입력 품질 합성). cex 2분 주기는 사이클 시각(as_of)으로 센다 — 같은 세션에서 마지막 계산 뒤 120초 이상인 사이클, 세션이 바뀌면 바로 **[확인 필요]** — 테스트 `tests/unit/test_engine_extended.py`·`tests/unit/test_engine_service.py`, 하루 시험 `tests/integration/test_full_day.py`

### 4.3 GEX P/C 비율
- 공식: `|Σ GEX_put| ÷ Σ GEX_call` (범위: 전체·최근접·0DTE)
- 예외: `Σ GEX_call = 0` 이면 null
- 품질: §2.2 품질
- 구현(2026-09-29): `core.metrics.exposure.gex_put_call_ratio`(콜·풋 GEX 합도 낸다, 범위 순GEX 가 null 이면 null) — 테스트 `tests/unit/test_exposure.py`(값·풋만/콜 OI 0 → null·콜만 0·빈 범위·전부 제외·제외 비율 estimated)·`tests/property/test_exposure_properties.py`(0 이상)

## 5. 변동성 (PLAN §5.4)

### 5.1 ATM IV — §3.7 그대로

### 5.2 IV 기간구조
- 공식: 0DTE(당일 만기)·차기 위클리·월물(가장 가까운 월물) 각각의 §3.7 ATM IV
- 예외: 해당 만기가 없으면 그 칸 null
- 구현(2026-09-29): `core.metrics.vol.term_structure`(칸마다 `core.levels.atm_iv`). 기본값 **[확인 필요]**: 차기 위클리 = 만기일이 귀속 거래일보다 뒤인 가장 이른 위클리(오늘 만기 위클리는 0DTE 칸), 월물 = 만기일이 귀속 거래일 이후(같은 날 포함)인 가장 이른 월물, 같은 칸 후보가 여럿이면 만기일·코드가 이른 것, 없는 칸은 null·품질 ok(해당 없음) — 테스트 `tests/unit/test_vol.py`. engine(`services/engine/extended.py`)은 그 사이클에 평가하지 못한 시리즈도 본다(2026-09-29 검토 E1): 위 규칙으로 그 칸에 들 수 있었던(고른 만기보다 늦지 않은, 칸이 비었으면 늘) 실패한 시리즈가 있으면 그 칸 invalid(`series_failed`), 최종거래일을 모르는 같은 종류 시리즈가 있으면 estimated(`series_no_expiry`) — 빈 칸이어도 같고, 범위 지표(§2.2 engine 범위 입력 품질)와 같은 규칙 **[확인 필요]** — 테스트 `tests/unit/test_engine_extended.py`

### 5.3 25Δ 스큐
- 공식: `IV(Δ_put = −0.25) − IV(Δ_call = +0.25)`. 만기별로 자체 델타·IV(§1.6·§1.5, sigma 가 있는 종목만)를 델타 순으로 놓고 선형보간
- 예외: 보간 구간 밖(격자가 ±0.25 를 못 덮음)이면 null(PLAN)
- 품질: 보간에 쓴 두 점 IV 품질 합성
- 테스트: 알려진 스마일 합성 체인에서 값, 구간 밖 null
- 구현(2026-09-29): `core.metrics.vol.skew_25d`(격자 = 자체 그릭스가 있는 종목 — GEX 에 든 종목, 풋·콜 따로 델타 순 `interpolate_at_delta`, 같은 델타의 점이 있으면 그 IV 평균). 기본값 **[확인 필요]**: 구간 밖·한쪽 종목 없음 null 은 품질 ok(사유 `put_out_of_range`·`call_out_of_range`·`no_put_points`·`no_call_points`), F 없음 invalid(§1.3). 품질은 쓴 점의 IV(종목) 품질에 만기 F 품질도 합성한다 — 델타·IV 를 F 로 구했으므로 §0 합성 규칙(§3.7 ATM IV·§2.1 GEX 와 같게, 2026-09-29 검토 E3). 테스트 `tests/unit/test_vol.py` — 평평한 스마일 0·풋/콜 따로 평평 σ_p − σ_c·델타에 선형인 스마일(σ = a + b·Δ 를 행사가마다 풀어 만든 체인)에서 정확한 값·구간 밖 null·쓴 점의 품질·F 품질(stale·estimated)

### 5.4 IV 랭크 / 퍼센타일
- 공식: 최근 252거래일 월물 ATM IV 일별 값 x₁…x_n, 오늘 x 에 대해 랭크 `(x − min)/(max − min)`, 퍼센타일 `#{xᵢ < x} / n`
- 입력: 일별 값 = 그날 주간 마감 기준 월물 ATM IV. 과거는 KRX 일별 `IMP_VOLT`(정규 행·당일 거래 있는 ATM 두 행사가, §15 참고) 백필, 라이브는 자체 §3.7
- 예외: n < 252 면 있는 만큼으로 계산하고 `estimated` **[확인 필요]**, n < 20 이면 null **[확인 필요]**. max = min 이면 랭크 null
- 품질: KRX `IMP_VOLT` 와 자체 IV 는 산출 기준이 다를 수 있어(#18 보류) 두 원천을 섞은 창이면 `estimated` **[확인 필요]**
- 구현(2026-09-29): `core.metrics.vol.iv_rank`·`krx_atm_iv`(KRX 일별 값)·`atm_iv_from_points`(§3.7 을 표 입력으로 — `core.levels.atm_iv` 와 같은 규칙). 기본값 **[확인 필요]**: 창은 오늘을 포함한 252거래일(캘린더 거래일, 빠진 날은 n 이 준다), 품질은 오늘 값 품질에 위 두 규칙만(지난 값의 품질은 합성하지 않고 invalid·값 없는 날만 뺀다). KRX 일별 값 = 당일 거래 있는 정규 행만으로 F 는 §1.3(종가를 가격으로, ATM 기준 = 그날 근월물 선물 정산가, 교차 확인 기준가 없음), ATM IV 는 §3.7(`IMP_VOLT`/100, 거래 있는 행사가 중 F 를 사이에 두는 두 행사가) — 테스트 `tests/unit/test_vol.py`(손계산 값·창 규칙·n < 20 null·정확히 20·n < 252 estimated·섞인 원천 estimated·max = min·KRX ATM·§3.7 표 입력 = `atm_iv`)

### 5.5 IV − HV
- 공식: `ATM IV − HV20`, `HV20 = stdev(ln(Pₜ/Pₜ₋₁), 최근 20거래일) × √252` **[확인 필요: 연환산 √252]**, P = KRX 선물 정산가(근월물 연결 — 롤오버 규칙은 PLAN §9.3 에 따른다)
- 예외: 20일이 안 되면 null
- 품질: ATM IV 품질
- 구현(2026-09-29): `core.metrics.vol.realized_vol`·`held_contract`·`iv_minus_hv`. 기본값 **[확인 필요]**: stdev 는 표본표준편차(n − 1), 창 = 정산가가 있는 가장 늦은 거래일(as-of 이하 — `POST_DAY` 엔 KRX 가 다음 날 08:00 에 내므로 보통 전 거래일)까지 거래일 20개의 수익률, 날 t 의 수익률은 앞 거래일 장 마감에 들고 있던 결제월(롤 = 최종거래일 직전 거래일 장 마감, PLAN §9.3)의 ln(P(t)/P(t−1)) — 같은 결제월 안에서만(가격 조정 연결선물), 한 날이라도 두 정산가가 없으면 null. 결제월 최종거래일은 같은 결제월 월물 옵션의 KIS 최종거래일, 없으면 캘린더(둘째 목요일) — 테스트 `tests/unit/test_vol.py`(표본표준편차 × √252·20개 안 됨·빠진 날·as-of·롤)
- engine(2026-09-29, §5.4·§5.5 일별): `services/engine/daily.py` — `POST_DAY` 첫 곁일에 그 거래일 한 번(`metrics` 행 ts = 그날 15:45 KST, scope all: `atm_iv_daily`·`iv_rank`·`iv_percentile`·`iv_hv`, 새 지표 기본 shadow). 기본값 **[확인 필요]**: 오늘 값 = 그날 주간 마지막 사이클의 월물 `atm_iv` 중 그날 가장 가까운 월물(최종거래일 — KIS 먼저, 없으면 캘린더 — 이 그날보다 뒤인 첫 결제월: 15:20 뒤 만기 지난 월물은 빠진다)의 행, 그 행이 없으면(마지막 사이클에서 그 시리즈 실패 등) 다음 월물로 넘어가지 않고 invalid(`nearest_monthly_missing`, 2026-09-29 검토 E2), 그 사이클이 15:45 보다 90초(§0 REST) 넘게 앞이면 stale. 창 안에서 `atm_iv_daily` 가 없거나 자체 값이 invalid 였던 날(그날 KRX 는 다음 날 08:00 에 난다 — 2026-09-29 검토 E4)은 KRX 일별로 계산해 `atm_iv_daily`(source krx, 같은 키라 자체 invalid 행을 덮는다)로 저장하고 다시 계산하지 않는다(KRX 로 못 낸 날도 source krx invalid 행으로 남긴다 — 그날 월물 = 최종거래일이 그날보다 뒤인 첫 결제월, ATM 기준 = 최종거래일이 그날 이후인 가장 이른 선물 정산가). 읽기·계산이 통째로 실패하면 health `engine_daily_failed`, 1분 뒤 다시 — 테스트 `tests/unit/test_engine_daily.py`·`tests/unit/test_engine_service.py`, 읽기 `tests/unit/test_store_engine.py`·`tests/integration/test_engine_store.py`, 하루 시험 `tests/integration/test_full_day.py`

## 6. 플로우 (PLAN §5.5)

### 6.1 HIRO-lite (딜러 헤지 흐름 추정)
- 공식: 옵션 체결 틱마다 `Δbuy = cum_buy_qty − 직전 cum_buy_qty`, `Δsell = cum_sell_qty − 직전 cum_sell_qty`, `signed_qty = Δbuy − Δsell`(매수 주도 +). 고객 델타 흐름 `Σ signed_qty × Δ × m × F`, 딜러 헤지 수요 = 부호 반전
- 입력: 틱(cum_buy_qty·cum_sell_qty — 설계 §8), Δ 는 그 종목의 최근 자체 델타(§1.6) **[확인 필요: 틱 시점이 아니라 최근 체인 평가 값]**
- 예외: 웹소켓 끊김·시퀀스 공백·세션 전환이면 누적을 0 으로 리셋하고 표시. 누적 수량이 줄면(역행) 그 틱은 버리고 health
- 품질: 항상 `estimated`(PLAN)
- 구현(2026-09-29): `core.metrics.flow.hiro_step`(틱 하나 → 새 `HiroState`·무엇을 했나 — applied·baseline·no_price·reversal·no_cum)·`hiro_reset`(누적·종목별 직전 누적을 비우고 사유 `ws_disconnect`·`seq_gap`·`session_change`·시각을 남긴다 — 표시)·`is_seq_gap`(수신 순번이 직전 + 1 이 아니면). 세션(귀속 거래일·세션)이 바뀐 틱은 먼저 리셋. `HiroState.dealer_hedge` = −고객 델타 흐름(원), 품질은 늘 estimated. 기본값 **[확인 필요]**: 종목의 첫 틱(리셋 뒤 포함)은 기준만 잡고 흐름에 넣지 않는다(직전 누적이 없다 — 세션 누적 전체를 한 틱에 넣지 않게), 역행 틱은 기준을 옮기지 않는다, 누적 필드가 없는 틱은 건너뛴다, Δ·F 를 모르는 틱은 기준은 옮기고 그 |signed| 를 `unpriced_qty` 로만 센다 — 테스트 `tests/unit/test_flow.py`(흐름 값·콜/풋 매수의 딜러 부호·역행·누적 없음·Δ 없음·세션 전환·리셋 두 사유·입력 검사·시퀀스 공백)·`tests/property/test_flow_properties.py`(역행이 끼어도 흐름 = 순 signed × Δ × m × F)
- engine(2026-09-29): `services/engine/flow.py` `TickFlow`·`services/engine/service.py` — `ticks.opt`·`ticks.fut`(ws-gateway 체결 행) 구독, `hiro` 행(scope all, 플래그 `hiro`, 새 지표 기본 shadow, 값 = 딜러 헤지 수요 원, payload 에 고객 델타 흐름·signed·반영 틱·`unpriced_qty`·역행 수·`reset_reason`·`reset_at`)을 10초마다 바뀐 것이 있을 때, 리셋·세션 전환 직전 상태도 한 행. 리셋: 수신 순번 공백(ws-gateway seq — 선물·옵션 공통이라 두 채널을 다 받는다. ws-gateway 는 재연결마다 순번 하나를 건너뛴다 — 끊긴 동안의 체결이 몰린 재연결 뒤 첫 틱이 반영 전에 리셋된다, 검토 F2), 웹소켓 끊김(ws-gateway 연결 사건 `ws_connected`·`ws_disconnected`·`ws_connect_failed` 를 health_events 에서 5초마다 — 새 사건이면 `ws_disconnect`. 그 사건 뒤에 이미 순번 공백·끊김으로 리셋했으면 다시 비우지 않고 사유만 `ws_disconnect` 로 — 재연결 뒤 흐름을 잃지 않게), 세션 전환. 역행 틱은 health `engine_hiro_reversal`(종목마다 10분에 한 번). 기본값 **[확인 필요]**: Δ·F 는 마지막 사이클의 `option_iv` 자체 델타·그 만기 F 로 그 사이클과 같은 세션(귀속 거래일·세션)의 틱에만(세션 첫 사이클 전 틱은 Δ 모름 → `unpriced_qty`), 시리즈를 모르는 틱(마스터 밖)은 순번만 보고 건너뜀, 시퀀스 공백 리셋 시각 = 그 틱 수신 시각. 틱 하나·곁일 하나의 예외는 그것만 health `engine_flow_failed` — 테스트 `tests/unit/test_engine_ticks.py`, 재연결 순번 `tests/unit/test_ws_client.py`, 하루 시험 `tests/integration/test_full_day.py`(ws-gateway 체결을 다 받아 세션마다 hiro 행, 야간 첫 행은 세션 전환 리셋)

### 6.2 투자자별 선물·옵션 순매수(장중)
- 정의: `investor_flow`(설계 §8) 시계열 그대로 — 선물·콜·풋·위클리별 외국인·개인·기관계·증권 순매수 수량/대금
- 표시: "합계 데이터"(행사가별 아님), 증권 = "딜러 프록시"(PLAN)
- engine(2026-09-29): `services/engine/flow.py` `InvestorFeed` — 30초마다 지금 세션의 (시장, 업종, 투자자)별 최신 `investor_flow` 행(`data.store.PostgresSink.investor_latest` — 검증 실패 행은 뺀다) 중 외국인·개인·기관계·증권의 새 행(직전에 낸 것보다 늦은 ts)을 그대로 `investor_flow` 지표 행으로 — scope all, key `시장:업종:투자자`(예 `WKM:OC05:scrt`), 값 = 순매수 수량(계약), payload 에 상품(futures·call·put)·시리즈(K2I·WKM·WKI)·순매수 대금(백만원)·매수·매도, `aggregate`(합계 데이터 표시)·`dealer_proxy`(증권), 플래그 `investor_flow`(새 지표 기본 shadow). ts·거래일·세션·품질은 그 행 그대로. 기본값 **[확인 필요]**: 주기 30초(poller 60초), 순매수 수량이 없는 행은 null·invalid(`field_missing`), 세션이 바뀌면 처음부터 — 테스트 `tests/unit/test_engine_investor.py`, 하루 시험 `tests/integration/test_full_day.py`(지표 행이 모두 investor_flow 표의 한 행 그대로)

### 6.3 딜러 가정 점검
- 공식: 거래일마다 증권(`scrt`) 계정의 콜 순매수 수량 합과 풋 순매수 수량 합(월물·위클리 합산). naive 가정(딜러 콜 롱·풋 숏)과 일치 = `콜 순매수 > 0` 그리고 `풋 순매수 < 0`
- 경고: 불일치가 연속 5거래일이면 대시보드 경고 배지 **[확인 필요: 5일]**
- 표시: 플로우(거래)이지 포지션(OI)이 아님을 명시(PLAN)
- 구현(2026-09-29): `core.metrics.flow.dealer_check`(조합마다 증권 계정 콜·풋 순매수 수량 → 합·일치 여부)·`mismatch_streak`(끝에서부터 이어진 불일치 날 수)·`dealer_warning`(`DEALER_WARN_DAYS` 5 이상). 기본값 **[확인 필요]**: 모르는 조합(None)은 빼고 estimated(`pairs_missing`), 한쪽을 통째로 모르면 판정 없음(None)·invalid(`no_call_flow`·`no_put_flow`), 판정 없는 날은 연속을 끊는다(모르는 날을 불일치로 세지 않는다) — 테스트 `tests/unit/test_flow.py`(일치·불일치 경계 0·모르는 조합·한쪽 없음·연속·5일 경고)
- engine(2026-09-29): `services/engine/flow.py` `dealer_record`·`services/engine/service.py` `run_dealer` — `POST_DAY` 첫 곁일에 그 거래일 한 번(일별 지표와 따로 — 실패도 따로 health `engine_daily_failed`·1분 뒤 다시). 입력은 그날 주간 (시장, 업종)별 마지막 증권(`scrt`) `investor_flow` 행 — 콜 K2I OC01·WKM OC05·WKI OC04, 풋 K2I OP01·WKM OP05·WKI OP04. `dealer_check` 행(scope all, ts = 그날 15:45 KST, 플래그 `dealer_check` shadow, 값 = 일치 1·불일치 0·판정 없음 null, payload 에 콜·풋 합·조합별 값·`mismatch_streak`·`warning`(대시보드 배지)·`basis` flow(플로우이지 포지션이 아님)). 연속은 앞 거래일의 이 지표 행(`metric_history`)으로 센다. 기본값 **[확인 필요]**: 주간 행만(야간 투자자별은 미실측 #13 — 거래일 = 주간), 앞 20거래일까지 보고 행이 없는 날은 판정 없는 날로(연속을 끊는다), 품질에 쓴 행 품질 합성 — 테스트 `tests/unit/test_engine_investor.py`, 하루 시험 `tests/integration/test_full_day.py`(그 거래일 15:45 한 행, 조합 여섯)

### 6.4 대량 체결
- 공식: 1틱 체결량 ≥ 최근 20거래일 같은 머니니스 구간의 1틱 체결량 p99
- 머니니스 구간: `K/F − 1` 을 1% 단위로 자른 구간, 콜·풋 따로 **[확인 필요]**
- 예외: 기록이 20거래일 미만인 동안 비활성(PLAN)
- 구현(2026-09-29): `core.metrics.flow.moneyness_bucket`(`floor((K/F − 1) / 1%)` — 경계에 딱 걸친 값이 부동소수 오차로 밀리지 않게 1e-9 자리에서 반올림)·`nearest_rank`·`block_thresholds`(표본 (거래일, 콜풋, 구간, 1틱 체결량) → (콜풋, 구간)별 p99, 거래일이 20 개 미만이면 비활성)·`is_block`(체결량 ≥ p99, 비활성·기록 없는 구간은 None). 기본값 **[확인 필요]**: p99 는 nearest-rank(오름차순 ⌈0.99·n⌉ 번째 — 관측된 체결량), 창 = 기록이 있는 가장 최근 20거래일(이어진 달력 거래일이 아니다), 구간 1% 는 내림([n%, n+1%)), 기록이 없는 구간은 판정하지 않는다 — 테스트 `tests/unit/test_flow.py`(구간 경계·p99·20일 전 비활성·창·콜풋 구간 따로·경계 포함·입력 검사)·`tests/property/test_flow_properties.py`(p99 이하 몫 ≥ 99%)
- engine(2026-09-29): 기준은 지금 세션 거래일마다 한 번 — `opt_ticks` 가 있는 그 거래일 앞 가장 최근 20거래일(`data.store.PostgresSink.opt_tick_days`)의 틱(1틱 체결량 > 0·시리즈 있는 틱)을 머니니스 구간으로(`opt_tick_history` — F = 그 틱 앞 10분 안의 같은 종목 engine F(`option_iv`), 못 찾은 틱은 뺀다 **[확인 필요]**). 20거래일 미만이면 비활성. `block_trades` 행(scope all, 플래그 `block_trades`, 값 = 기록 거래일 수, payload 에 active·창·구간 수·표본 수), 읽기 실패는 1분 뒤 다시. 판정은 옵션 틱마다 — 구간 F 는 마지막 사이클(같은 세션)의 그 종목 F, 모르면 판정 안 함 → `block_trade` 행(scope series, key `라벨:행사가:콜풋:수신 순번`, 값 = 1틱 체결량, payload 에 기준·구간·F, 품질 ok **[확인 필요]**) — 테스트 `tests/unit/test_engine_ticks.py`, 읽기 `tests/unit/test_store_engine.py`·`tests/integration/test_engine_store.py`, 하루 시험 `tests/integration/test_full_day.py`(세션 거래일마다 비활성 한 행)

### 6.5 PCR
- 공식: OI 기준 `Σ OI_put / Σ OI_call`, 거래량 기준 `Σ 거래량_put / Σ 거래량_call` (만기별·전체)
- 예외: 분모 0 이면 null
- 구현(2026-09-29): `core.metrics.flow.pcr`(종목마다 콜풋·OI·당일 누적 거래량). 기본값 **[확인 필요]**: 값을 모르는 종목(OI·거래량 None)은 합에서 빼고 그 비율을 estimated(`oi_missing`·`volume_missing` — 0 으로 세지 않는다), 분모 0 의 null 은 품질 ok(해당 없음 — 모르는 값 때문에 분모가 0 이면 estimated) — 테스트 `tests/unit/test_flow.py`(값·분모 0·모르는 값·입력 검사)·`tests/property/test_flow_properties.py`(0 이상·배수 불변·콜풋 바꾸면 역수)
- engine(2026-09-29): 사이클 등록부 `services/engine/flow.py` `FLOW_REGISTRY`(engine 기본 등록부 `ENGINE_REGISTRY` 에 든다) — `pcr_oi`·`pcr_volume`(플래그 `pcr`, 새 지표 기본 shadow) 시리즈마다(key 시리즈 라벨)·전체(all), 입력은 그 사이클 만기 평가 종목의 OI·당일 누적 거래량. 기본값 **[확인 필요]**: F 를 쓰지 않아 품질에 S_ref 를 합성하지 않고 그 시리즈 체인 행 입력 품질(옛 행 stale·OI 없음 estimated)만, 평가하지 못한 시리즈(실패·최종거래일 모름)가 있으면 전체 invalid(`series_failed`·`series_no_expiry` — 범위 all 과 같게) — 테스트 `tests/unit/test_engine_flow.py`, 하루 시험 `tests/integration/test_full_day.py`

### 6.6 맥스페인
- 공식: 만기별로 상장 행사가 K 중 `Σ_{K'} [OI_c(K')·max(K − K', 0) + OI_p(K')·max(K' − K, 0)]` 가 최소인 K
- 예외: 동률이면 F 에 가까운 쪽, 그다음 낮은 쪽. OI 가 전부 0 이면 null
- 구현(2026-09-29): `core.metrics.flow.max_pain`(Decimal 로 정확히 — 동률이 부동소수 오차로 갈리지 않게, 누적합으로 후보·종목 수에 선형, `pain_at` 이 식 그대로). 동률 거리는 `core.chain.by_distance`(F 와의 거리, 같으면 낮은 행사가). 기본값 **[확인 필요]**: 후보는 넘긴 상장 행사가(마스터 전 행사가 — 없으면 OI 가 온 행사가, OI 행이 없는 상장 행사가도 후보), F 를 모르는데 동률이면 낮은 행사가 + estimated(`tie_without_forward`), 후보가 없으면 null — 테스트 `tests/unit/test_flow.py`(손계산·원 환산·동률 세 갈래·F 없는 동률·OI 0·상장 행사가 후보·입력 검사)·`tests/property/test_flow_properties.py`(고른 값 = 식의 최소, 행사가·F 평행 이동)
- engine(2026-09-29): 사이클 등록부 `max_pain`(플래그 `max_pain`, shadow) 시리즈마다 — 후보는 마스터 상장 행사가(`CycleView.strikes`), OI 는 그 사이클 만기 평가 종목, 동률의 F 는 그 만기 F. 기본값 **[확인 필요]**: 상장 행사가 중 체인 행이 한 번도 오지 않은 행사가가 있으면(세션 첫머리 — 보강 2 순환 전) 그 OI 를 몰라 estimated(`strikes_unquoted`), 품질은 PCR 과 같게 S_ref 없이 그 시리즈 입력 품질 — 테스트 `tests/unit/test_engine_flow.py`

### 6.7 OI 증감 히트맵
- 공식: 행사가 × 스냅샷 시각별 `OI(t) − OI(t−1)`
- 이상치: 한 스냅샷에서 줄었다가 다음 스냅샷에 줄어든 양의 90% 이상 복구되면 두 칸 모두 이상치로 격리(표시 제외, health) **[확인 필요: 90%]**
- 구현(2026-09-29): `core.metrics.flow.oi_step`(새 스냅샷 하나 → 새 칸 + 이상치로 바뀐 직전 칸)·`oi_change_cells`(그 접기), 비율 `OI_RECOVERY`(Decimal 0.9 — 정확한 비교). 기본값 **[확인 필요]**: 90% 는 경계 포함(넘친 복구도 이상치), 증감 0 인 스냅샷도 '다음 스냅샷'(복구 아님으로 확정), 첫 스냅샷은 증감 없음(None) — 테스트 `tests/unit/test_flow.py`(증감·짝 여러 갈래·경계 90/89%·한 칸씩 = 접기·시각 검사)·`tests/property/test_flow_properties.py`(증감 합 = 마지막 − 처음, 이상치 = 줄어듦·복구 짝 전부)
- engine(2026-09-29): `services/engine/flow.py` `OiTracker` → `oi_changes` 표(플래그 `oi_changes`) — 사이클마다 만기 지나지 않은 시리즈의 종목별 쓴 체인 행(`choose_rows` — 전광판 먼저)이 새 스냅샷(행 ts 가 직전보다 늦다)이면 한 칸. 기본값 **[확인 필요]**: 행 ts = 스냅샷 시각(체인 행 수신 시각 — 사이클 as_of 가 아니다: 히트맵의 시각 축이고, 이상치로 앞 칸을 고칠 때 같은 키), 세션(귀속 거래일·세션)이 바뀌면 비운다(주간·야간 OI 를 잇지 않는다 — 재기동도 첫 스냅샷부터: 재기동 직전 칸과 다음 칸에 걸친 이상치 짝은 보지 못한다), 쓰는 행은 첫 스냅샷·증감 ≠ 0·이상치로 바뀐 칸뿐(0 증감 스냅샷도 이상치 판정의 '다음 스냅샷'), OI 없는 행은 스냅샷이 아니다, 품질 = 이번·직전 두 스냅샷 체인 행 품질 중 나쁜 것(증감이 기대는 입력만 — 앞 칸 품질을 잇지 않는다, 검토 F3; 첫 스냅샷은 그 행 품질, 이상치로 고친 앞 칸은 그 칸 품질). 이상치면 두 칸(앞 칸은 같은 키로 DO UPDATE) + health `engine_oi_outlier`(시리즈마다 10분에 한 번). 첫 스냅샷 칸(증감 없음)은 같은 키의 저장된 칸을 덮지 않는다(`DO UPDATE … WHERE EXCLUDED.change IS NOT NULL` — 세션 중간에 재기동한 engine 이 같은 최신 체인 행을 첫 스냅샷으로 다시 써도 증감·이상치 격리가 남는다, 검토 F1). 예외는 그것만 health `engine_metric_failed` — 테스트 `tests/unit/test_engine_flow.py`(재기동해도 저장된 칸 그대로·칸 품질은 두 스냅샷만), 쓰기 `tests/unit/test_store_engine.py`·`tests/integration/test_engine_store.py`, 하루 시험 `tests/integration/test_full_day.py`

## 7. 선물 (PLAN §5.6)

- 시장 베이시스 `선물가 − KOSPI200 지수`: **자체 계산**(`futs_prpr − kospi200_nmix`) — KIS `basis` 는 실측으로 이론 베이시스라 시장 베이시스로 쓰지 않는다(아래 2026-09-30). 괴리율·선물 OI 증감·체결강도·KIS `basis`: KIS 선물 필드(`dprt`, `otst_stpl_qty_icdc`, `tday_rltv`, `basis`) 그대로 표시
- 교차검증(2026-09-30 실측 반영): 이론 베이시스 `hts_thpr − kospi200_nmix` 와 KIS `basis` 차이가 0.05pt 를 넘으면 health — KIS 필드 뜻이 바뀌었거나 한 응답 안의 값이 서로 맞지 않는다는 신호 **[확인 필요: 0.05pt]**(비교 대상을 이론 베이시스로 바꾼 것도 **[확인 필요]**). 처음 명세는 자체 시장 베이시스와 비교했으나 실측 여섯 응답 모두에서 KIS `basis` = 이론 베이시스(±0.01)라 늘 실패했다
- 이론 베이시스: KIS `hts_thpr`(이론가) − 지수를 표시. 자체 이론 베이시스는 금리·배당 추정이 필요해 계산하지 않는다(PLAN §2.4 "배당·금리 추정 불필요" 와 같은 입장) **[확인 필요]**
- 구현(2026-09-29): `core.metrics.futures.futures_metrics`(입력 `FuturesQuote` — 위 KIS 필드는 선물옵션 분봉 조회 `inquire-time-fuopchartprice`(FHKIF03020200) output1 의 이름이다, 위 필드가 다 있는 실측 응답은 이것뿐: `tests/fixtures/kis/minute_day.json`. KIS 이름에서 옮기는 것은 호출 쪽 — core 는 KIS 필드 이름을 쓰지 않는다, `tests/unit/test_gex.py`). 교차검증 `|(futs_prpr − kospi200_nmix) − basis| ≤ 0.05`(`BASIS_CHECK_TOLERANCE`, Decimal — 0.05 정확히는 통과). 기본값 **[확인 필요]**: 빈 필드는 그 값만 null·invalid(`field_missing`), 지수·선물가·이론가가 0 이하면 없는 것으로(야간 응답에 `prdy_nmix` `0.00` 실측), 교차검증은 선물가·지수·KIS basis 가 다 있을 때만(아니면 판정 없음) — 테스트 `tests/unit/test_futures_metrics.py`(그대로 표시·이론 베이시스·0.05 경계·허용 인자·빈 값·입력 검사·실측 응답)·`tests/property/test_futures_properties.py`(KIS 값 그대로·두 베이시스 식·교차검증 = |자체 − KIS| ≤ 허용, 허용을 넓히면 통과가 실패로 바뀌지 않음)
  - **실측으로 드러난 점 → 2026-09-30 명세 고침**: 세 응답(2026-09-28 14:01·09-29 15:52·16:07, A01612)에서 KIS `basis` 는 `hts_thpr − kospi200_nmix`(이론 베이시스 — 5.97·6.37·−0.55)와 같고 `futs_prpr − kospi200_nmix`(시장 베이시스 — 0.60·7.95·−5.73)와 다르다. 명세대로 비교하면 교차검증이 늘 실패한다(health 가 10분마다). 대안: `basis` 를 이론 베이시스로 표시하고 교차검증을 `hts_thpr − kospi200_nmix` 와 하거나, 시장 베이시스를 자체 `선물가 − 지수` 로 표시
- engine(2026-09-29): `services/engine/futures.py`·`services/engine/service.py` — 위 필드가 있는 응답은 분봉 조회 원문뿐이라 scheduler 가 세션 뒤(주간 16:00~·야간 06:10~) 녹화한 `raw_messages`(kis_rest, FHKIF03020200) output1 을 1분마다 마지막으로 본 시각 뒤만 읽는다(`data.store.PostgresSink.kis_rest_outputs`, 검증 `data.kis.models.MinuteQuote`). 응답마다 다섯 행 — `futures_basis`(KIS `basis`, payload 에 자체 선물가 − 지수·차·교차검증 결과)·`futures_theory_basis`·`futures_divergence`·`futures_oi_change`·`futures_strength`(scope all, key = 선물 종목코드, ts = 응답 수신 시각, 거래일·세션 = 원문 태그, 플래그 `futures` shadow, 품질은 위 core 규칙). 교차검증 실패는 health `engine_futures_basis_check`(종목마다 10분에 한 번). 기본값 **[확인 필요]**: 주기 60초, 기동하면 하루 앞 응답부터(같은 키라 다시 써도 행이 늘지 않는다), 태그가 없거나 검증에 실패한 응답은 그것만 건너뛰고 센다(로그). 장중 값은 없다 — 분봉을 세션 뒤에만 부르므로 행은 세션마다 그 조회 수만큼(주간 최대 5·야간 8, 같은 마감 값) — 장중 선물 필드 수집은 PLAN §4.4 에 없다 **[확인 필요]** — 테스트 `tests/unit/test_engine_futures.py`, 읽기 `tests/unit/test_store_engine.py`·`tests/integration/test_engine_store.py`

## 8. 기능 플래그·섀도 모드 (PLAN §6.4)

- `config/features.yaml`: 지표·위젯·알림·전략 이름마다 `off | shadow | visible`
- `shadow`: 계산·저장만 하고 화면·알림에 내보내지 않는다. 새 지표의 기본값은 `shadow`, 교차검증·육안 검토를 통과하면 `visible`(PLAN)
- 검증: 지표별 검증 리포트(Phase 3 완료 기준) — 명세 테스트 결과 + 녹화 데이터 재생 결과
- 구현(2026-09-29): 로더 `core.features`(pydantic — 구역 metrics·widgets·alerts·strategies, 이름은 카탈로그 `CATALOG` 에 있는 것만, 값은 문자열로만 읽는다 — YAML 1.1 의 따옴표 없는 `off` 가 false 가 되지 않게). 모르는 구역·이름·값이나 같은 매핑 안의 중복 키(같은 이름·구역 두 번 — 뒤 값이 조용히 이기지 않게, 검토 F3)면 기동 실패(engine 종료 코드 2), 파일에 없는 이름은 카탈로그 기본값(Phase 2 핵심 visible·새 지표 shadow), 30초마다 파일 서명(mtime·크기)이 바뀌었으면 다시 읽는다. 한 플래그가 산출 여럿을 정한다(`pcr` → pcr_oi·pcr_volume, `futures` → 선물 다섯, `expected_move` → 두 기준, `block_trades` → 기준·체결). engine: off 는 계산하지 않는다(`hiro` off 면 틱은 순번과 — `block_trades` 가 켜져 있으면 — 대량 체결 판정만 보고 HIRO 누적·역행 health 를 하지 않으며 누적을 버린다. 다시 켜면 처음부터 `start` — 재기동과 같아 끈 동안의 흐름이 첫 행에 들지 않는다, 검토 F2), shadow 는 계산·저장(행의 계산 당시 `flag` — `metrics`·`levels`·`oi_changes`, 004_flags)만 하고 `engine.levels`·`engine.metrics`·`engine:latest` 에 싣지 않는다, visible 은 발행. 기본값 **[확인 필요]**: Phase 2 핵심은 off 로 둘 수 없다(shadow 까지 — 사이클 품질·레벨 사이 의존의 바탕), 다시 읽다 틀리면 직전 플래그를 그대로 쓰고 health `engine_flags_invalid`(같은 파일은 한 번), compose 는 플래그 파일을 읽기 전용 마운트(제자리 편집만 보인다) — 테스트 `tests/unit/test_features.py`·`tests/unit/test_engine_service.py`·`tests/unit/test_engine_evaluate.py`·`tests/unit/test_compose_file.py`
- 섀도 운영 점검(2026-09-30, 설계 §4): `scripts/shadow_report.py` — 최근 거래일(기본 5 = 1주, 귀속 거래일)의 engine 산출을 플래그·행 플래그별로 계산 횟수(행·사이클)·invalid 비율·예외(`error` 칸 행 + engine 예외 health, 같은 대상 10분 묶음)·null 비율·명세 밖 null 비율로. 명세 null = 각 절 '예외'가 허용한 null(빈 범위·Σ콜 GEX 0·분모 0·보간 구간 밖·20일 안 됨·교차 없음·Flip 없음·첫 스냅샷·빈 필드 — `NULL_RULES`, 카탈로그 산출마다 규칙이 있다)과 품질 invalid 인 null. 기본값 **[확인 필요]**: 플래그 무오류 = 행이 있고 예외 0·명세 밖 null 0, 완료 기준 충족 = 고른 플래그(기본 shadow — 행 플래그와 지금 플래그 파일) 모두 무오류이고 지표에 묶이지 않는 예외(`engine_cycle_failed`·`engine_series_failed`·`engine_rows_failed`) 0·대상 플래그를 못 가린 지표 예외 health(`?이름` — engine 새 문구·이름 바뀜, 버리지 않고 센다, 검토 F5) 0, 행이 없는 플래그는 '계산 없음'(미충족) — 단 지금 off 인 플래그는 '꺼짐'(정상 — off 는 계산하지 않는다, `--flag off` 는 off 플래그가 계산·예외 없이 꺼져 있었나를 본다), off 로 계산된 행은 오류(검토 F4) — 테스트 `tests/unit/test_shadow_report.py`(engine 이 실측 스냅샷으로 쓰는 null 행이 모두 명세 null)·`tests/integration/test_shadow_report.py`·읽기 `tests/unit/test_store_engine.py`·`tests/integration/test_engine_store.py`
- 검증 리포트(2026-09-30, 설계 §5): `scripts/metric_report.py` → `docs/metric_validation.md` — 지표마다 명세 테스트 결과(pytest — 파일 수집 실패는 그 파일을 쓰는 지표의 실패, 시험을 돌렸는데 0 건이면 '명세 테스트 없음'으로 통과하지 않는다), 2026-09-28 체인 스냅샷 두 장의 engine 값·품질, 따로 짠 계산과의 교차 확인, 그 절에 남은 확인 필요 표시. 새 지표를 visible 로 올리는 것은 이 리포트 통과 + 1주 섀도 무오류 뒤 사용자 결정 — 테스트 `tests/unit/test_metric_report.py`
