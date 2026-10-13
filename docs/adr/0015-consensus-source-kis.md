# ADR 0015 — 컨센서스 출처 교체(네이버 → KIS), 잃는 것, 목표가 컨센서스·리비전 정의 (2026-10-07)

상태: **확정**(2026-10-13 — 초안·구현 P4 묶음 K, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모'). **2026-10-08 메인 결정으로 채택**: 출처 = KIS(U-P4-1), 옛 네이버 데이터 이관 안 함(U-P4-2), TR id 한 곳(아래 2-1), 실데이터 확인 전 수집 작업 꺼짐(2-8).
근거: `docs/p4_design.md` D-P4-9·10, §4.7, 부록 A §10, ADR 0001 U4(네이버 스크래핑 금지), `docs/DATA_TIERS.md` §1(컨센서스 = 로그인),
`docs/conflict_map.md` §1.13(컨센서스 행), `docs/p2_design.md` R20, SD `consensus_collector.py`·`consensus_quarterly_collector.py`·
`consensus_snapshot_collector.py`·`revision_calculator.py`·`tam_modeler.py`.
구현(묶음 K, 2026-10-08 — 아래 §4): `kbj/data/private/kis/consensus.py`, `kbj/engines/consensus/{target,revision,forward}.py`,
`kbj/services/collectors/consensus.py`, `kbj/data/private/kis/datasets.py`(`consensus_estimate`·`invest_opinion`·상수 `CONSENSUS_TRS`),
마이그레이션 `0012_fin_prv.sql`(묶음 M).

## 1. 맥락

- legacy 컨센서스는 전부 네이버 스크래핑이다: 선행 EPS(`main.naver` 인라인), 분기 컨센서스(`tb_type1` 표 — `(E)` 열), 그 위의
  스냅샷·리비전·서프라이즈·TAM. U4 로 쓸 수 없고 FnGuide 스크래핑도 같은 이유로 안 된다.
- 대체 후보는 KIS 시세분석 TR(종목추정실적·종목투자의견·증권사별 투자의견). **레포 안에서도 TR id 가 어긋난다**:
  `kbj/data/private/kis/datasets.py`:214(투자의견 FHKST668300C0·추정실적 FHKST663300C0), `docs/conflict_map.md` §1.13(그 반대),
  `tests/fakes/kis_server.py`:76(FHKST663300C0 → `estimate-perform`). 아무것도 실측되지 않았다.
- KIS 추정실적이 여러 증권사 평균인지 한국투자증권 자체 추정인지, 분기 추정이 있는지 모른다.

## 2. 결정

1. **출처 = KIS, 로그인 등급, 합성 fixture 만.** 이 ADR 의 기본 TR 은 추정실적 `HHKST668300C0`(`…/quotations/estimate-perform`),
   투자의견 `FHKST663300C0`(`…/quotations/invest-opinion`), 대안 증권사별 `FHKST663400C0`(`…/quotations/invest-opbysec`) —
   **전부 [확인 필요]**(기억에 기댄 값). **TR id·경로는 `kbj/data/private/kis/datasets.py` 의 상수 `CONSENSUS_TRS` 한 곳에만 둔다** —
   카탈로그 노트·`kbj/data/private/kis/consensus.py`·가짜 서버(`tests/fakes/kis_server.py`)·시뮬레이션(`tests/sim/harness.py`)은 그 상수를
   import 하고 문자열을 다시 쓰지 않는다(묶음 K 가 모은다 — 위치 표는 `docs/p4_design.md` §9.3, 시험 `tests/unit/kis/test_consensus_tr_single.py`).
   실측(#29) 뒤에는 그 상수 하나와 이 ADR 을 한 커밋에 고친다.
2. **목표가 컨센서스는 우리가 계산**한다: 최근 90일 증권사별 마지막 목표가(> 0, 미제시 제외)의 평균·중앙값·최고·최저·수.
   증권사 3곳 이상 ok, 1~2곳 estimated.
3. **잃는 것을 지어내지 않는다**: 분기 컨센서스(→ 분기 어닝 서프라이즈는 계산하지 않고 전년동기·직전분기 대비 분류로 대신),
   컨센서스 최고·최저(→ 시나리오 Bull 은 Base × 1.2 폴백), 애널리스트 수, 12개월 선행 EPS 직접값(→ FY1·FY2 시간 가중 NTM 계산값),
   네이버 리서치 리포트 목록(폐지). 없는 칸은 '출처 없음'.
4. KIS 추정이 단일 증권사 추정으로 확인되면 화면·알림에서 **"컨센서스"라 부르지 않고 "KIS 추정"** 으로 적는다.
5. **리비전**: 지표 × 창(7·30일), 기준선 = t − W 이전 가장 늦은 스냅샷, pct = (현재 − 기준) ÷ |기준| × 100, 기준 0 → None,
   부호 전환 → `TURN`. 신호 경계 ±5·±15(SD 그대로). 관심종목 리비전만 하루 1통(`alert.revision`, `daily`).
6. **유니버스** = 관심종목 ∪ 전 거래일 시총 상위 300 보통주(16:00 — ADR 0018, 원래 18:30. 마감 수집 뒤 약 640호출 — KIS 4/s 로 약 3분, 리비전 알림 평소 16:00~16:05). API 는 KIS 를 부르지 않으므로
   유니버스 밖 종목은 "수집 대상 아님".
7. 옛 SD SQLite 의 네이버 컨센서스·밴드·서프라이즈 행은 **이관하지 않는다**(p2 설계 §8.4 의 '표시만 남기고 이어 쌓기'를 바꾼다)
   — **채택(2026-10-08, U-P4-2)**. 네이버 스크래핑은 하지 않는다(U4).
8. **실데이터 확인 전에는 수집 작업 `consensus.snapshot` 을 `enabled: false` 로 둔다**(P4 웨이브 3 에도 켜지 않는다). 처리기·파서·엔진·
   API·화면은 합성 fixture 로 완성하고, 켜기 전까지 화면은 404 `no_data`("실데이터 확인 전 꺼짐"). 켜는 것은 #29 결과를 본 사용자 확인 뒤.
9. 잃는 것(3)은 문서(이 ADR·`docs/metrics.md` §10)와 **화면**(페이지 8 컨센·밸류 탭의 '출처 없음 — KIS 교체로 잃음' 줄)에 적는다.

## 3. 결과

- 네이버·FnGuide 호출 0(`check_canonical` 그룹 `naver` 축소, 신규 `fnguide` 목표 0).
- 분기 서프라이즈·Bull 컨센 최고값 등은 기능이 줄어든다 — 화면에 사유를 적는다. 유료 출처 계약 여부는 사용자가 정한다.
- 남은 [확인 필요]: TR id·경로·파라미터·필드·단위(억원?), 시장 컨센서스 여부, 분기 추정 유무, 커버리지(#29), 투자의견 코드 매핑.

## 4. 구현에서 정한 것 (묶음 K, 2026-10-08 — S 가 확정할 때 함께 본다)

1. **TR 한 곳**: `CONSENSUS_TRS`(datasets.py)만 TR id·경로를 가진다. 파서는 `TrSpec(*CONSENSUS_TRS[...])`, 가짜 서버 `TRS`·
   시뮬레이션 `KIS_SPECS` 는 상수(또는 파서의 `TR_ESTIMATE`·`TR_OPINION`)를 import 한다. `tests/unit/kis/test_consensus_tr_single.py` 가
   `kbj/`·`tests/`·`scripts/` 의 `.py` 에서 TR id 꼴 문자열이 datasets.py 밖에 0 건인지 본다(문서는 기록이라 제외). 카탈로그
   `consensus_estimate` 노트의 엇갈린 문구는 지우고 상수로 만든 문구로 바꿨다. 증권사별 투자의견(대안)은 상수에만 있고 부르지 않는다.
2. **응답 모양 = 파서 모듈 머리의 상수 표 하나**(`kbj.data.private.kis.consensus` — 출력 이름 `output1~4`·`output`, 행 순서
   `PL_ROWS`·`IND_ROWS`, 열 `data1~5`, 결산년월 `dt` 의 `E` 표지, 금액 단위 억원 `AMOUNT_UNIT_KRW`, 의견 칸 이름). **전부 [확인 필요
   #29]** — 실측 뒤 그 표와 합성 fixture(`tests/fixtures/synthetic/kis/make_p4.py --write`)만 고친다. 가짜 서버의 인코더가 같은 표를 따른다.
3. **순이익 기준 미확인 → `ni_owner` 는 채우지 않는다**(None). KIS '순이익' 은 `ni` 에만. 선행 PER 은 NTM EPS 경로(묶음 V).
   추정·실적 표지가 응답에 없으면 결산일 > 스냅일 인 열을 추정으로 보고 그 행은 `quality=estimated`(표지가 일부 열에만 있을 때도 표지 없는
   미래 결산 열은 실적일 수 없으므로 같은 규칙 — 검증 2026-10-08). 모든 행 `extra.period="annual"` —
   분기 추정 행은 만들지 않는다(묶음 E 의 분기 서프라이즈 길은 쓰이지 않는다 — D-P4-10).
4. **빈 응답 = 커버 없음**(`KisEmpty`) — 실패가 아니고 행을 만들지 않는다. 유니버스 안인데 커버가 없는 종목은 API 가 404 `no_data`.
5. **목표가 컨센서스**: 창 [t − 90, t] 에서 증권사마다 **가장 늦은 행 하나를 먼저 고르고**, 그 행이 미제시('Not Rated'·'미제시'·'NR' …)거나
   목표가 ≤ 0·없음이면 그 증권사를 뺀다 — 의견을 거둔 증권사의 옛 목표가로 돌아가지 않는다. 의견 문구 → 매수·중립·매도 표는
   [확인 필요 — 의견 코드 매핑], 모르는 문구는 `unknown` 으로 따로 센다.
6. **리비전**: 현재값 = t 이하 마지막 스냅, 기준선 = snap_date ≤ t − W 이고 그 칸에 값이 있는 가장 늦은 스냅(metrics §10 — SD 는 '현재
   스냅일 − W', 매일 도는 운영에서는 같다). 기준선 없음·기준 0 → pct·신호 None(행은 남김), 부호 전환 → `TURN`. 현재 스냅이 t 보다 이르면
   stale. 목표가 평균의 기준선은 저장된 의견 이력으로 **그 날짜의 목표가 컨센서스를 다시 계산**한다(저장소에 범위 조회가 없어도 PIT).
   신호 경계 수치는 `config/fin.yaml consensus.signal_pct` 하나(엔진은 `SignalPct.classify` 를 부른다 — 두 벌 금지).
7. **알림**: 관심종목의 추정치 리비전(FY1·FY2 매출·영업이익·순이익·EPS — 목표가 평균은 제외, SD `alert_revision_signals` 처럼) 중
   |pct| ≥ `consensus.alert_min_abs_pct`(5) 또는 `TURN` 을 하루 1통(`alert.revision`, daily dedup, 16:00 — ADR 0018). 제목은 **"KIS 추정
   변화(리비전)"**(시장 컨센서스인지 모른다 — 2-4 의 기본값·§4.7 "KIS 추정 변화"), 본문은 템플릿·평문(parse_mode 없음). 0 건이면 보내지 않는다. 모든 키를 받고
   파생이 끝났을 때만 보내며, 알림을 넣지 못하거나 파생이 실패하면 실행 실패 + 키 미완(재시도가 다시 받고 다시 보낸다 — daily dedup).
   **그날 스냅이 없는 관심종목**(KIS 빈 응답·종목 고유 실패 — 지난 스냅으로 계산한 stale 리비전)은 저장만 하고 알리지 않는다
   (같은 지난 값이 매일 다시 울리지 않게 — 검증 2026-10-08, detail `alert_skipped_stale`).
8. **유니버스**: 관심종목(main) ∪ 전 거래일 시총 상위 `consensus.top_n` — 둘 다 코스피·코스닥 보통주만(KRX 기본정보 `kind=common`).
   관심 우선주는 `prv_market.share_class` 로 보통주로 바꾸고(우선주 컨센서스 = 보통주 것), 스팩·리츠·ETF·코넥스는 빼고 사유를 detail 에.
   유니버스(KRX 기본정보)가 없으면 실패. KIS 우선순위는 `config/limits.yaml kis.priorities.consensus`, 없으면 P3(설계 §1.6).
9. 합성 세계(p4_world) 위 처리기 결과: 호출 = 2 × 유니버스(관심 19 → 보통주 15), 커버 없음 4, 리비전 알림 종목 = 994010·994060·
   994070(TURN)·994140 — `p4_expect.yaml alerts.alert.revision` 의 [제안] 과 같다(`tests/unit/collectors/test_consensus.py`).

## S 확정 메모 (웨이브 3, 2026-10-13)

- 번호 0015·상태 **확정**. 수집 작업 `consensus.snapshot` 은 **꺼진 채**다(2-8 — 등록부 시험 `ENABLED_BY_PHASE["P4"]` 에 없고
  `test_p4_jobs_are_registered_with_design_owners` 가 꺼짐을 단언). 켜는 것은 실측 #29 결과를 본 사용자 확인 뒤 — 등록부 한 줄과 그
  단언을 같은 변경에서.
- **하루 운영 증명**(`tests/sim/test_one_day_p4.py` — 운영 등록부는 그대로 두고 **시뮬레이션의 등록부 사본에서만** 켰다): 16:00
  실제 처리기 1회, KIS 호출 = 추정실적 15 + 투자의견 15 = 2 × 유니버스(관심 19 → 코스피·코스닥 보통주 15), KIS 어떤 1초에도 ≤ 4,
  발급은 auth 만, `alert.revision` 하루 1통(16시 — ADR 0018)·관심 밖 종목 0.
- 정본 경계: `scripts/check_canonical.py` 에 그룹 `fnguide`(FnGuide·WiseReport 호스트 — 목표 0, kbj·legacy 모두)를 신설했고 CI
  `canonical` 잡의 목표 0 목록에 넣었다. SD 의 네이버 컨센서스·리비전 모듈 4개를 지웠다(naver 기준선 −4, `legacy/stock_dashboard/
  MIGRATION.md` 'P4'). `docs/conflict_map.md`·`docs/probe_results.md` 의 옛 TR 값 옆에 정본 위치(`CONSENSUS_TRS`)를 적었다.
- `docs/metrics.md` §10 '리비전 알림' 을 구현(4-7)에 맞췄다 — `|pct| ≥ 5` **또는 TURN**, **그날 스냅이 있는 종목만**, 목표가 평균 제외.
- **[중요 — 결함, 요청] 실제 KRX 유니버스에서 컨센서스 대상이 0**: P3 `krx.daily` 는 주식 유니버스의 종류를 넣지 않는다(`kind=None` —
  분류 정본은 board 엔진 `kbj.engines.board.kinds.of`). 4-8 의 유니버스 규칙(`kbj.engines.consensus.universe.consensus_na` 의
  `kind != "common"`)·수집기의 우선주 판정은 종류가 채워져 있다고 보아, 처리기를 거친 세계에서는 관심종목 전부가
  `not_in_consensus_universe` 가 된다(완료 시험 `tests/acceptance/test_stock_detail_watchlist20.py` 가 이 결함을 xfail(strict)로 고정 —
  `S-R1`). 단위 시험·하루 시뮬레이션은 유니버스에 참값 종류를 넣어 가렸다. 고칠 곳(K·V·A 소유): `kind` 가 None 이면
  `kinds.of(code, name)` 으로 판정하는 공용 함수 하나를 소비자들이 쓰게(밸류 `classes`·API `readers.stock.ref` 도 같은 결함).
- `config/limits.yaml kis.priorities` 에 `consensus: P3` 한 줄은 아직 없다(코드 기본값 P3 로 같다 — M 소유 파일, 요청).
- 남은 [확인 필요 #29]: TR id·경로·파라미터·출력 이름·행 순서·억원 단위·순이익 기준·추정 표지·의견 문구 매핑·연결/별도 구분,
  시장 컨센서스인지 단일 증권사 추정인지, 커버리지.

