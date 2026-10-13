# KBJ P8 설계서 — 규칙 알림·백테스트·저널 (페이지 11·12)

- 작성: 2026-10-13. 상태: **설계(구현 전)**. 구현은 **IDX(P5 2차)·P6·P7 이 커밋된 뒤** 시작한다(이 문서를 쓰는 동안 IDX 는 빌드 중, P6·P7 은 설계 중). 대상 단계: `docs/PLAN.md` §4 페이지 11·12, §8 P8 — "규칙 DSL(JSON 스키마), 평가 엔진(장중 10분·마감), 쿨다운, 근거 메시지, **알림 후 성과 통계** / 백테스트(같은 지표 함수), 저널·포트폴리오, 페이지 11·12". 출처 요구는 `legacy/stock_dashboard/REBUILD_BRIEF.md` §4(재활용 자산)·§6(규칙 엔진 목표·스키마 초안).
- 기준 문서: 계획 `docs/PLAN.md` §2·§4·§8, 등급 `docs/DATA_TIERS.md`(정본 — **페이지 11·12·13 = 개인 기능, 전부 로그인**, §2:57), 지표 `docs/metrics.md`(P8 지표는 부록 A 를 FND 가 **P7 다음 절 번호**로 옮긴다), P4·P5 설계 `docs/p4_design.md`·`docs/p5_design.md`(구조·관례), ADR 0005(notifier outbox·중복 키·쿨다운), 0006(등록부·선점), 0007(legacy 기준선), 0008(로그인 API·CSRF), 0016(관심종목), 0017(신고가 3축), **0018(마감 뒤 텔레그램은 전부 16:00)**, 0023 §2-8(US btsearch 는 P8 로), 작업 등록부 `config/jobs.yaml`.
- 원본 위치: 이 레포 `legacy/` 를 직접 열어 확인했다(작업 트리 HEAD `138da8f` + IDX 미커밋 변경). 표기 `ET:` = `legacy/etf_traker/`, `SD:` = `legacy/stock_dashboard/`. kbj 쪽은 레포 경로 그대로. 줄 번호는 이 날짜의 작업 트리 기준 — P6·P7 이 SD `server.py` 를 고치면 FND 가 다시 확인한다.
- 표기: **[추정]** = 코드로 끝까지 확인하지 못함, **[확인 필요]** = 기본값으로 정한 것, **[실측 필요]** = 키를 받은 뒤 실제 응답으로 확인, **[결정 필요]** = 사용자가 정할 것, **[제안]** = 이 설계의 권고, ⚠ = 충돌·위험.
- 번호 규칙: P6·P7 이 아직 번호를 잡지 않았으므로 **마이그레이션 = `NNNN` = P7 다음 번호**(IDX 가 `0018_index.sql` 을 쓰고 있다 — 그 뒤 P6·P7 다음), **ADR = P7 다음 번호 A·B·C·D**, **metrics 절 = P7 다음 절 번호**(IDX 가 §24), **import-linter 계약 = P7 다음 번호**, **probe 체크리스트 = P7 다음 번호**. FND 가 시작할 때 실제 번호로 바꾸고 이 문서의 표기를 고친다.
- 키·토큰·계좌 값은 이 문서 어디에도 없다. `.env`·토큰 캐시는 열지 않았다. 네트워크 호출은 하지 않았다. 모든 증명은 **합성 데이터**로 한다(KIS·KRX 시세와 사용자의 실제 규칙·일지·매매 기록은 레포에 넣지 않는다 — 고정 시드 생성기만). 실전 주문 코드는 만들지 않는다(`LIVE_TRADING=false` — CLAUDE §1-6).
- **코드로 확인한 사실 중 계획·요청과 다른 것(이 설계의 출발점)**:
  1. **`alert_quote` 라는 데이터셋은 없다.** 등록부 `rules.intraday`(`config/jobs.yaml`:458~468)가 모으는 것은 `KIS:watch_quotes_intraday`(FHKST01010100 관심종목 현재가 — `kbj/data/private/kis/datasets.py`:229~238, `store=""`, 노트 "저장 여부는 P8 [확인 필요]")다. P8 은 이것을 알림 시세 데이터셋으로 쓰고 **저장한다**(D-P8-9). 파서는 이미 있다(`kbj/data/private/kis/quotes.py:parse_quote`:73·`bar_from_quote`:114 — `quote_on_demand`·마감 수집이 쓰는 것).
  2. 등록부 `rules.intraday` 의 owner `kbj.engines.rules:intraday` 는 계약 ⑨(엔진은 순수 — 저장소·어댑터 금지)와 맞지 않는다(P5 D-P5-7 과 같은 문제). writes 도 비어 있다.
  3. **지표 정본이 없다.** `kbj/services/api/readers/stock.py`:12 가 "SMA 는 지표 정본(`kbj.core.indicators` — P8) 전까지 이 모듈의 `sma`" 라고 예고했다. legacy 정의가 서로 다르다: SD `server.py:_calc_rsi_macd`:383 은 EMA 를 **첫 값**으로 시작하고 RSI 워밍업 칸을 **0.0** 으로 채운다(창이 모자란 종목이 'RSI < 30' 에 걸린다 — 버그), ET `board/engine/systems.py` 의 `ema_arr`:75 는 **SMA 시드**, `rsi_arr`:86 은 와일더 평활·워밍업 `None`, `adx_arr`:106 은 와일더 14. `board/engine/signals.py` 에 `sma`:48·`pstdev`:59·`true_range`:68·`bb_width`:78 이 또 있다.
  4. 연속 순매수는 정본이 **이미 있다**: `kbj/engines/flows/streak.py`(metrics §2 — SD `_flow_streak`·ET 두 벌을 대체). DSL 의 수급 조건은 이것을 그대로 부른다.
  5. ET board 백테스트 엔진 `board/engine/{backtest(380줄),search(203),signals(551),systems(468)}.py` 와 `board/us/btsearch.py`(151)는 legacy 에 있고 원본 시험은 13·6·25·15·3 = **62**(`board/tests/test_{backtest,search,signals,systems,us_search}.py`). ADR 0023 §2-8 이 btsearch 를 "국장 백테스트 엔진과 함께 P8 에" 로 미뤘다. 이 엔진은 sqlite `conn` 을 직접 읽는다(`backtest.py:load`:74) — kbj 로 오면 읽기(저장소)와 계산(엔진)을 나눠야 한다.
  6. 일봉 원장 `prv_market.daily_bar` 는 **비수정 가격**이다(`0003_market_flows.sql`:46 "KRX OpenAPI 일별은 비수정 — adjusted=false"). 액면분할이 지표를 깨뜨린다. board 는 이미 `kbj/engines/board/newhigh.py:split_guard`:193·`prv_board.split_check` 로 막는다 — 같은 장치를 쓴다(D-P8-8).
  7. 저장 스키마 `prv_alerts`(알림 규칙·발송 이력·큐)·`prv_journal`(분석 일지·추천·매매·포트폴리오·관심종목)은 `0001_schemas.sql`:31~32 에 **이미 있다**. 표는 `prv_alerts.{tg_inbox,notify_message}`(0005)·`prv_journal.watchlist`(0013 — "쓰기 API 는 P8")뿐이다. legacy 이관 표 `kbj/store/legacy_import/mappings.py`:290~291 이 SD `alert_rules·alert_history(_v2)` → `prv_alerts.rule·event`, SD `analysis_journal·recommendation_history·trade_journal` → `prv_journal.*` 를 P8 로 적어 두었다.
  8. **개인 데이터 쓰기 API 는 P8 이 처음이다.** 지금 상태를 바꾸는 라우트는 `auth`·`webhook` 뿐(`kbj/services/api/routes/`). CSRF·Origin 검사는 `kbj/services/api/deps.py:require_csrf`:124 가 이미 있다(ADR 0008 §5).
  9. 웹 등록부 `web/src/app/pages.ts`:62~65 가 11·12 **그리고 13(운영)** 을 `phase: 'P8'` 로 두었다(13 은 "[확인 필요]" 주석). 요청 범위는 11·12 뿐이다 — 13 은 §10 Q9. 페이지 모듈 glob 은 `pages.ts`:84 `p{1,2,4,5,6,8,10}_*.ts`(P6·P7 이 넓힌 뒤에 11·12 를 더한다).
  10. 텔레그램 명령 `/시그널`(`kbj/services/notifier/commands.py`:151)이 `phase="P8"` 로 자리만 있다.
  11. ADR 0018 §2-1: "KST 15:30 이후 첫 발화가 있는 발송 작업은 `brief.closing`·`flows.report`·`consensus.snapshot` **셋**, 전부 16:00" — 등록부 시험이 지킨다. 마감 규칙 알림을 더하면 **넷**이 된다(D-P8-4 — ADR 보충).
  12. SD 원본: 규칙 검사 `check_alert_rules`:4285(가격·등락률·RSI 과매수/과매도·MACD 크로스·외국인 연속/5일 누적 — 규칙당 **하루 1번**(`triggered_at`)), 트레일링 스톱 `_check_trailing_stops`:4144, 쿨다운 `_alert_cooldown_ok`:4593(종목×종류 60분), 관심종목 가격 `alert_watchlist_price`:4739, 발굴 신규 `alert_discovery_new_entries`:4634, 백테스트 `api_backtest`:9798(발굴 스냅샷의 태그·점수 기반 — 규칙 백테스트가 아니다), 상관관계 `_compute_correlation`:10001·`api_correlation`:10045, 추천 이력 `_init_recommendation_history`:10101·성과 `api_recommendation_performance`:10236(D+1~D+7), 매매 저널 `_init_trade_journal`:12432~`api_journal_delete`:12655, 분석 일지 라우트 13284~13420 + `analysis_journal_api.py`(756줄)·`analysis_journal_helper.py`(422)·`review_validator.py`(328), 일지 표 `migrations/004_step4_schema.py`:40.

---

## 0. 요약

### 0.1 결정 요약

| # | 결정 | 이유·근거 |
|---|---|---|
| D-P8-1 | **시작 조건 = IDX·P6·P7 커밋 + CI 녹색.** P8 이 고치는 앞 단계 파일은 §1.8 공유 파일 표가 전부다. FND 는 시작할 때 P6·P7 이 바꾼 공유 파일(마이그레이션 번호·`pages.ts` glob·계약 번호·`tests/sim/harness.py`·`config/limits.yaml` KIS 예산)을 다시 읽고 이 문서의 번호를 고친다 | 같은 작업 트리에서 P6·P7 이 설계 중이라 번호·예산이 아직 없다 |
| D-P8-2 | **구현 = FND 1(웨이브 1) + 병렬 묶음 4(EVL·BKT·SWG·JNL, 웨이브 2) + WRAP 1(웨이브 3).** 실시간 평가와 백테스트가 함께 쓰는 **핵심(지표·DSL·프레임·평가기·쿨다운 상태기·체결 비용·통계)은 FND 가 먼저** 만든다 — 둘이 같은 함수를 부르는 것이 완료 기준의 뿌리이기 때문 | 웨이브 2 의 두 묶음(EVL·BKT)이 같은 평가기를 서로 기다리지 않게 |
| D-P8-3 | **지표 정본 = `kbj/core/indicators.py`(새, 순수)**: SMA·EMA(SMA 시드)·RSI(와일더, 워밍업 `None`)·MACD(12·26·9)·볼린저(20·2σ, 모표준편차)·%B·밴드폭·ADX/+DI/−DI(와일더 14)·ATR·true range·거래량 배수·N일 최고/최저·등락률. 정의는 board `systems.py`·`signals.py` 를 따른다(시험이 있는 쪽). SD `_calc_*` 정의는 버린다(EMA 첫 값 시드·RSI 0 채움). 페이지 8 SMA(`readers/stock.py`)도 이것으로 바꾼다 | CLAUDE §3 정본 하나. 실시간·백테스트·차트가 한 함수. 공식은 부록 A |
| D-P8-4 | **평가 시점 둘 = 장중 10분(`rules.intraday`, 사건 시점 발송) + 마감(`rules.close`, 16:00 발송).** 마감 규칙 알림은 **16:00 한 통 묶음**(`alert.rule_close`, `dedup: daily`, 0건이면 보내지 않음). ADR 0018 의 '15:30 이후 발송 셋' 을 넷으로 넓히는 **보충**을 P8 ADR A 에 쓰고 등록부 시험을 고친다 | ADR 0018(마감 뒤는 전부 16:00), 장중은 사건 시점. 규칙이 많아도 마감 뒤 폭주가 없다(§10 Q1) |
| D-P8-5 | **규칙 = JSON DSL v1(§4.1) — JSON 스키마는 pydantic 모델에서 생성**(`config/schemas/rule_v1.schema.json`·`web/src/api/rule.schema.json` — 생성 전용). 논리 `all`(AND)·`any`(OR) 중첩 깊이 ≤ 3, 조건 ≤ 12, 지표·연산자는 닫힌 목록. 평가기는 문자열 식을 **실행하지 않는다**(eval 없음) | REBUILD_BRIEF §6.4 초안을 확장. 서버·브라우저 편집기가 같은 스키마로 검사 |
| D-P8-6 | **같은 신호의 정의**: 신호 = (규칙 판, 종목, 평가 시점)에서 규칙이 참이고 쿨다운 상태기가 허락한 것. 실시간과 백테스트는 **같은 프레임 생성기(`kbj.engines.rules.frame`)·같은 평가기(`evaluate`)·같은 쿨다운 상태기(`cooldown`)** 를 부른다. 백테스트는 시점을 하나씩 넘기며 같은 함수를 부를 뿐 별도 '벡터화 판' 을 두지 않는다 | 완료 기준 "백테스트와 실시간이 같은 신호" 를 구조로 보장 |
| D-P8-7 | **EMA 계열의 시작점 고정(anchor)**: EMA·RSI·MACD·ADX·ATR 은 시작점에 따라 값이 달라진다. 지표는 언제나 `config/rules.yaml anchor_date`(기본 `2020-01-02` [제안]) 또는 그 종목의 첫 봉 중 늦은 날부터 **한 번에** 계산하고, 실시간은 마지막 칸·백테스트는 각 칸을 읽는다 | 창 길이를 실시간·백테스트가 다르게 잡으면 임계값 근처 신호가 갈린다 |
| D-P8-8 | **비수정 가격 방어**: 프레임을 만들 때 board `split_guard`(`kbj/engines/board/newhigh.py`:193)와 `prv_board.split_check` 의 의심 종목이 평가 창 안에 있으면 그 종목은 `skipped: split_suspect`(신호 없음, 근거에 적음). 수정주가는 만들지 않는다 | 신고가와 같은 장치(정본 재사용). 수정주가 원장은 P8 범위 밖 |
| D-P8-9 | **장중 평가 입력 = 전 거래일까지의 확정 일봉 + 그 슬롯의 부분 봉**(`watch_quotes_intraday` 의 시가·고가·저가·현재가·누적 거래량). 이 슬롯 시세를 **`prv_alerts.watch_quote_slot` 에 저장**한다 — 백테스트의 '장중 재생' 이 같은 입력을 다시 읽어 같은 신호를 낸다. 수급 조건은 장중에 **전 거래일 확정값**을 쓰고 근거에 기준일을 적는다 | 장중 이력이 없으면 장중 신호를 재현할 수 없다. 장중 잠정 수급(`flows.intraday`)은 상위 N 종목만이라 관심종목 전부를 덮지 않는다 |
| D-P8-10 | **쿨다운 = 엔진 상태기 + notifier 문지기 두 겹**: 규칙의 `cooldown_min`(기본 1,440 = 24시간 [제안], 하한 60)은 엔진이 신호 표로 판정하고(백테스트도 같은 상태기), notifier `alert.rule`(cooldown 3600, subject `<rule_id>:<code>`)은 안전망. 장중·마감 모드가 같은 쿨다운을 나눠 쓴다 | ADR 0005 §2.2(미끄러지는 창). SD 의 하루 1회·60분 규칙을 둘 다 품는다 |
| D-P8-11 | **근거 메시지 = 템플릿**(LLM 없음 — CLAUDE §1-3): 조건마다 ✔/✖, 지표 이름·파라미터·현재값·기준값·크로스면 전/후 값, 수급은 기준일, 시세는 출처·시각·quality(장중 = `estimated`), 규칙 이름·판·쿨다운, 페이지 8 링크(`KBJ_PUBLIC_BASE_URL` + `#/8/<코드>` — 로그인 화면). 4,096자 초과는 notifier 가 나눈다 | 절대 규칙 1(source·as_of·quality), REBUILD_BRIEF §6.1-4 |
| D-P8-12 | **알림 후 성과 = `rules.outcome`(평일 08:30, KRX 확정 일봉 뒤)**: 신호마다 기간 1·3·5·10·20 거래일 [제안]의 수익률·지수 대비 초과(코스피·코스닥 — 시장별)·최대 유리/불리 폭(MFE/MAE)을 `prv_alerts.signal_outcome` 에 쌓는다. 진입가 = 장중 신호는 그 슬롯 현재가, 마감 신호는 그날 종가. 통계(표본 수·승률·평균·중앙·사분위·초과)는 읽을 때 순수 함수로 | 완료 기준 "성과 표가 자동으로 쌓인다". 확정 일봉(D+1 08:05)으로 계산해 잠정값 성과를 남기지 않는다 |
| D-P8-13 | **백테스트 = 같은 평가기 + 고정 시간 규칙**: 마감 신호는 d 일 종가까지로 판정, **d+1 시가 체결**(board `backtest.py` 머리말의 미래참조 규칙 그대로), 장중 신호는 신호 슬롯 가격 체결. 보유 N일·손절·익절·비용(수수료·거래세 — `config/rules.yaml`, 값 [확인 필요])·생존편향 주의 문구. 체결·비용·통계 원시 함수는 `kbj/engines/backtest/{costs,stats}.py`(FND — board `backtest.stats`:236 승격) 하나를 BKT·SWG 가 같이 쓴다 | 같은 일 두 벌 금지. 원작 board 의 시점 규칙을 그대로 |
| D-P8-14 | **일치 감시(운영 중 증명)**: `rules.backtest_nightly` 가 켜진 규칙을 최근 20 거래일에 다시 백테스트해 실시간 신호와 비교하고 `prv_alerts.match_check` 에 적는다. 차이마다 사유 `data_revised`(입력 지문 `input_fp` 가 다름 — KIS 잠정 → KRX 확정 등) / `logic`(지문이 같은데 결과가 다름 — 버그, health 오류). 같은 실행이 3년 백테스트 요약을 `backtest_run` 에 남겨 페이지 12 '백테스트 대 실제' 가 읽는다 | 완료 기준을 합성 세계에서 한 번이 아니라 운영 내내 지킨다 |
| D-P8-15 | **표 하나에 쓰는 주체 하나**(§3.3 표): 장중·마감 신호를 한 표에 두 작업이 쓰지 않도록 `prv_alerts.signal_intraday`(쓰기 `rules.intraday`)·`signal_close`(쓰기 `rules.close`) 로 나누고 읽기는 뷰 `prv_alerts.signal`. 개인 기록 표(규칙·일지·매매)는 **쓰기 모듈 하나**(`kbj.services.personal.rules`·`.journal`, 기존 `kbj.services.watchlist`)를 API 라우트와 CLI 가 같이 부른다 | 사용자 규칙 '표마다 쓰는 쪽 하나' |
| D-P8-16 | **개인 쓰기 API(처음)**: 상태를 바꾸는 요청은 전부 `require_session` + `require_csrf`(Origin·`X-KBJ-CSRF`), 본문 64 KiB 상한, pydantic 검증(422 는 위치·종류만 — 입력값 없음), 규칙 수정은 **새 판**(`rule_version` 은 바꾸지 않는다 — 과거 신호가 어느 판에서 났는지 남는다), 삭제는 보관(`archived_at`) | ADR 0008 §5·§6. 성과 통계가 판별로 나뉘어야 한다 |
| D-P8-17 | **즉석 백테스트는 API 안에서 계산, 저장하지 않는다**: `POST /api/backtest` 는 저장소에서 읽고 순수 엔진으로 계산(계약 ⑩ 그대로 — 외부 호출 없음), 상한(종목 ≤ 100·기간 ≤ 5년·20초 — [제안]) 넘으면 413/503, 결과는 Redis `rules:bt:<규칙 해시>:<원장 as_of>` 1시간. DB 에 남는 백테스트는 야간 작업 것뿐 | API 쓰기 주체를 늘리지 않는다(D-P8-15) |
| D-P8-18 | **board 스윙 엔진 승격(SWG)**: `board/engine/{signals,backtest,search,systems}` + `board/us/btsearch` → `kbj/engines/swing/**`(순수 — 읽기는 `kbj/services/engine/swing.py`), 원본 시험 62 는 **같은 단언, import 경로만** 바꿔 승격, 지표는 `kbj.core.indicators` 로 갈아 끼운다(같은 값 — 골든). `config/signals.yaml` 은 `git mv`. legacy 는 shim(`board.run --signals/--backtest/--search` 와 `us-search.yml` 이 계속 돈다). 실행은 수동 CLI(`python -m kbj.services.engine.swing …`) — 매일 발송·DB 표·페이지 패널은 P8 에 두지 않는다(§10 Q8) | ADR 0023 §2-8, PLAN §2 정본. 지금 legacy 도 수동(workflow_dispatch) |
| D-P8-19 | **저널·포트폴리오(JNL)**: 분석 일지(`prv_journal.analysis` — SD `analysis_journal` 칸 그대로 + 출처), 매매 기록(`prv_journal.trade` — SD `trade_journal` + `signal_id` 연결), 포지션·실현/평가 손익은 **이동평균 단가** [제안]로 읽을 때 계산(표 없음), 상관관계(관심종목·보유 60·120일 수익률 — 읽을 때 계산), 추천 성과는 SD `recommendation_history` 이관분만(`prv_journal.pick` — 새 추천 생성기 없음). KIS 잔고 연동 없음 — 입력·CSV 가져오기만(§10 Q6) | 주문·계좌 접근 없음(절대 규칙 5·6). SD 분석 일지 prefill 은 P4 밸류에이션·시나리오 적정가 읽기로 바꾼다 |
| D-P8-20 | **장중 대상 = 관심종목 `main` ∪ 보유 종목 ∪ 규칙에 적은 코드, 상한 60** [제안](`config/rules.yaml intraday_max_codes`). 넘으면 규칙 우선순위·관심 순서로 자르고 근거·health 에 '잘림' 을 적는다. 마감 평가는 상한 없음(원장만 읽는다 — KIS 호출 없음) | KIS 초당 4건 버킷(§3.4) |
| D-P8-21 | **legacy 정리 = 옮긴 만큼만, 파일·함수 단위**(D-P5-24 규칙): SD 알림·저널·백테스트·상관·추천 성과 함수와 `analysis_journal_*.py`·`review_validator.py` 는 대체 뒤 삭제(남은 import 없을 때만), `_calc_rsi_macd`·`_calc_bollinger`·`_calc_adx` 는 SD 화면이 아직 부르면 남기고 기준선 그룹 `ta_indicators`(새)에 센다. board 엔진은 shim. `config/notify.yaml legacy_kinds` 의 #6·#12·#13·#14 는 SD 함수가 지워질 때 같이 지운다 | 조용히 줄지 않는다 — 승격 또는 폐지(대체 시험 기록) |
| D-P8-22 | **새 검사(WRAP)**: ① import-linter **P7 다음 번호** — `kbj.engines.rules`·`kbj.engines.backtest`·`kbj.engines.swing`·`kbj.engines.journal` 은 저장소·어댑터·서비스를 모른다(⑨ 확장으로 충분하면 ⑨ 에 모듈만 추가), `kbj.services.personal` 은 로그인 원천·수집기를 모른다 ② `check_canonical` 그룹 **`ta_indicators`**(RSI·MACD·볼린저·ADX·EMA 계산 함수 정의 — kbj 안 허용 위치 `kbj/core/indicators.py` 하나, legacy 는 줄어들기만 하는 기준선) ③ `tests/unit/test_no_orders.py` — kbj 안에 KIS 주문 TR id(`TTTC08*`·`VTTC08*` 등 — 목록은 시험에 [확인 필요])·주문 경로(`/trading/order`) 0, `settings.live_trading` 기본 false 그대로 | 정본 하나를 기계로 지킨다. 주문 코드 0 |
| D-P8-23 | **화면 = MD6 13 페이지 틀 그대로**, 페이지 11·12 는 기존 패널 양식(`web/src/ui/panel.ts`·`table.ts`·`line.ts`·`bars.ts`·`seg.ts`·`tabs.ts`)에 FND 의 새 공용 부품 `form.ts`(터미널식 입력·선택·토글)·`heatmap.ts`(상관 행렬) 둘만 더한다. 둘 다 `lock: 'personal'` — **공개 빌드에 본문이 들어가지 않는다**(공개 JS gzip 예산 61,440 B 중 P5 끝 61,021 B) | 사용자 규칙(13 페이지·패널 양식), 번들 예산 |

### 0.2 만드는 것 / 만들지 않는 것 (P8 경계)

| 만든다 (P8) | 만들지 않는다 |
|---|---|
| 지표 정본(`kbj.core.indicators`)·규칙 DSL v1·JSON 스키마·평가기·쿨다운 상태기·근거 메시지 | 문자열 식·사용자 코드 실행, LLM 해설, 실전 주문·주문 미리보기·KIS 잔고 조회 |
| 장중 10분 평가(`rules.intraday` — 관심종목 현재가 수집·저장·평가·사건 시점 발송), 마감 평가(`rules.close` — 16:00 묶음) | 웹소켓 실시간 체결 평가(P7 웹소켓 이후 — 별도), 분 단위 평가 |
| 알림 후 N일 성과(`rules.outcome`)·통계·백테스트 대 실제 비교·일치 감시(`rules.backtest_nightly`) | 성과 기반 규칙 자동 조정·최적화(SWG 의 기존 조합 탐색 외) |
| DSL 백테스트(즉석 API + 야간), board 스윙 엔진·US btsearch 승격(수동 CLI) | 스윙 시그널 매일 발송·페이지 패널(§10 Q8), 발굴·AI 추천 생성기 |
| 분석 일지·매매 기록·포지션 손익·상관관계·추천 성과(이관분), 관심종목 쓰기 API(별 토글) | 현금·입출금 원장, 세금 신고용 계산, 다계좌 |
| 페이지 11(규칙·편집기·발송 이력·성과)·12(백테스트·대 실제·일지·포트폴리오·상관·추천 성과), `/시그널` 명령 | 페이지 13 운영(§10 Q9), 공개판 화면(자물쇠 그대로) |
| SD 규칙·일지·매매·추천 이력 이관 매핑(사용자가 VM 에 올린 파일 — 레포에 넣지 않음) | SD `alert_history(_v2)` 이관(발송 기록은 `ops.notify_log` 가 정본 — 옛 기록은 버림 [제안]) |

### 0.3 완료 기준과 증명

| PLAN §8 P8 완료 기준 | 증명 | 묶음 |
|---|---|---|
| **규칙 예시 10개가 백테스트와 실시간 평가에서 같은 신호를 낸다** | `tests/acceptance/test_rules_parity.py`(WRAP): 합성 세계 `p8_world`(고정 시드, 상장 80종목 × 320 거래일, 분할 1·거래정지 1·신규상장 1·우선주 1 포함)에 `config/rule_examples.yaml` 10개(마감 7·장중 3 — §4.1.4)를 넣고 ① **마감 7개**: 마지막 60 거래일을 날마다 실제 처리기 `rules.close`(가짜 시계·메모리 저장소·발송 켬)로 돌린 신호 집합 = 같은 원장으로 돌린 `backtest(mode='close')` 신호 집합 — (규칙 판, 종목, 날짜) **완전히 같고**, 근거 값(지표 현재값·기준값)은 허용오차 1e-9 ② **장중 3개**: 마지막 5 거래일 × 40 슬롯을 실제 처리기 `rules.intraday`(가짜 KIS — 합성 현재가)로 돌린 신호 = `watch_quote_slot` 을 재생한 `backtest(mode='intraday_replay')` 신호 ③ 쿨다운으로 막힌 시점도 양쪽이 같다(상태기 공유) ④ 변형: 한 종목의 KIS 마감 종가를 다음 날 KRX 확정이 바꾸면 `match_check` 가 `data_revised` 로 분류하고 `logic` 0 ⑤ 분할 종목은 양쪽 모두 `split_suspect` 로 신호 0 | FND(핵심)·EVL·BKT, WRAP |
| **성과 표가 자동으로 쌓인다** | `tests/sim/test_rules_days.py`(WRAP — 2026-11-02(월) ~ 12-04(금) 25 거래일, 등록부 cron 대로 실제 처리기 `rules.intraday`·`rules.close`·`rules.outcome`·`rules.backtest_nightly` + 재료 작업 `market.close_collect`·`krx.daily`(가짜 KIS·KRX)): ① k 일째 아침 `signal_outcome` 행 수 = (기간 h 마다 D+h ≤ k−1 인 신호 수의 합) — 날마다 **단조 증가**, 수기 계산과 일치 ② 같은 날 재실행·scheduler 재기동 뒤 중복 행 0(멱등) ③ `GET /api/rules/stats` 의 표본 수가 날마다 늘고 손계산 3칸(평균·승률·초과)과 일치 ④ 휴장일(가짜 캘린더 1일)에 실행 0 | BKT, WRAP |
| (규칙) 장중 알림은 사건 시점, 마감 뒤는 16:00 | 등록부 시험(`test_registry_validation.py` — 15:30 이후 첫 발화 발송 작업 = 넷, 전부 16:00), sim ①의 `ops.notify_log`: `alert.rule` 접수 시각 = 신호 슬롯 + ≤ 60초, `alert.rule_close` 는 16:00~16:05 하루 1건(0건인 날은 0) | FND·EVL, WRAP |
| (규칙) 주문 없음·공개판 개인 데이터 0 | `tests/unit/test_no_orders.py`, 공개 안전 검사·`check-bundle.mjs`(공개 번들에 `/api/rules`·`/api/journal` 문자열 0), 공개 내보내기 산출에 P8 파일 0 | WRAP |
| (PLAN 기능) 페이지 11·12 가 합성 데이터로 그려진다 | `web/test/acceptance/p11_p12_pages.test.ts`(WRAP — `console.error` 0, DOM 의 `NaN`·`undefined`·`Infinity` 0, 패널 격리, 공개 빌드는 자물쇠만) | EVL·BKT·JNL, WRAP |
| 백엔드 ruff·pyright·pytest·lint-imports·check_canonical·check_public_safety, 프런트 eslint·tsc·vitest·빌드·번들 검사, CI 녹색 | §1.10 WRAP 명령 | WRAP |

### 0.4 등록부(`config/jobs.yaml`)에서 바뀌는 것 (FND 가 모양을 확정 — 전부 `enabled: false`, 켜기는 WRAP)

| 작업 | 지금 | P8 |
|---|---|---|
| `rules.intraday` | owner `kbj.engines.rules:intraday`(⚠ 계약 ⑨), collects `KIS:watch_quotes_intraday`(slot10m), writes 없음, notify `alert.rule`, `retry {max: 0}`, `deadline_min: 10` | owner **`kbj.services.engine.rules:intraday`**. 시각 그대로(`equity {start: open, end: close, every_min: 10}`). writes **`prv_alerts.watch_quote_slot`·`prv_alerts.signal_intraday`**. absorbs 그대로(SD `tg_custom_alerts`·`tg_watchlist`·`tg_trailing`·`price_sync_intraday`·`stage2_realtime_kr`). 노트: 대상 상한·수급은 전 거래일 확정(D-P8-9·20) |
| **`rules.close`(새)** | — | owner `kbj.services.engine.rules:close`, `cron "0 16 * * 1-5"`, `when: trading_day`, `catch_up_until: "20:30"`, 굳은 의존 `market.close_collect`, collects 없음(원장만), writes `prv_alerts.signal_close`, notify `{kind: alert.rule_close}`. absorbs 없음(SD 에 마감 규칙 평가가 없다) |
| **`rules.outcome`(새)** | — | owner `kbj.services.engine.rules_perf:outcome`, `cron "30 8 * * 1-5"`, `when: trading_day`, 굳은 의존 `{job: krx.daily}`(as_of `prev_trading_day`), writes `prv_alerts.signal_outcome`, 발송 없음 |
| **`rules.backtest_nightly`(새)** | — | owner `kbj.services.engine.rules_perf:nightly`, `cron "45 8 * * 1-5"`, 굳은 의존 `rules.outcome`, writes `prv_alerts.{backtest_run,backtest_signal,match_check}`, 발송 없음(`logic` 차이는 health 오류 → `ops.watchdog` 이 알린다) |
| SD `alert_discovery_new_entries`(#14) | `alert.rule` 로 흡수 표시 | 발굴 기능이 KBJ 에 없다 → **`retired`** 로 옮김(사유 "발굴 신규 진입 — 생성기 없음, 같은 뜻은 사용자 규칙으로") [제안 — §10 Q11] |
| SD `/api/backtest`·`/api/journal/*`·`/api/correlation`·`/api/recommendation/*` | 등록부 밖(HTTP 라우트) | `retired` 행 4(사유 "SPA 페이지 12 + 로그인 API 가 대체") — WRAP 이 SD 함수를 지울 때 |

등록부 시험(FND 몫, `tests/unit/scheduler/test_registry_validation.py`): 위 행의 owner·collects·writes·의존, **15:30 이후 첫 발화 발송 작업 = `brief.closing`·`flows.report`·`consensus.snapshot`·`rules.close`, 전부 16:00**, `rules.intraday` 의 하루 KIS 호출(대상 상한 60 × 40 슬롯 = 2,400) 을 더해도 `kis.daily_soft_cap` 이하, 같은 `(source, dataset)` 은 한 작업만, 표마다 writes 하는 켜진 작업 하나(P8 표 — §3.3).

---

## 1. 모듈 묶음·웨이브·파일 소유

### 1.0 웨이브

| 웨이브 | 묶음(동시) | 시작 조건 | 예상 |
|---|---|---|---|
| **1** | **FND** — 핵심(지표·DSL·스키마 생성·프레임·평가기·쿨다운·체결 비용·통계·예시 10개·지표 문서) + 바탕(마이그레이션·행 자료형·저장소·설정·등록부·notify·KIS 데이터셋 행·API 모양과 빈 리더·쓰기 의존·openapi·TS 타입·웹 셸·공용 부품·합성 세계) | IDX·P6·P7 커밋 | 2일 |
| **2** | **EVL**(실시간 평가·발송·규칙 쓰기·페이지 11 규칙/편집기/이력·`/시그널`), **BKT**(백테스트·성과·일치 감시·페이지 11 성과·페이지 12 백테스트), **SWG**(board 스윙 엔진·US btsearch 승격), **JNL**(일지·매매·포트폴리오·상관·추천 성과·관심종목 쓰기·SD 이관·페이지 12 저널 패널) | FND 커밋. 서로는 FND 가 고정한 표·모델·평가기로만 만난다 | 2.5일 |
| **3** | **WRAP** — 작업 켜기·완료 시험·여러 날 시뮬레이션·CI·검사(계약·`ta_indicators`·주문 0)·legacy 정리·기준선·문서·ADR 확정 | 웨이브 2 전부 | 1.5일 |

합계 약 6일(PLAN 6~8일 안). 묶음 6개(FND 1 + 병렬 4 + WRAP 1). FND 는 파일이 겹치지 않는 두 에이전트(핵심 FND-K·바탕 FND-S)로 나눠도 된다 — §1.1 표의 '에이전트' 칸. 묶음마다 빌드 에이전트 + 검증 에이전트(§1.10 명령). **도커 통합·legacy 전체·sim 전체는 묶음이 돌리지 않는다** — 메인이 WRAP 끝에 한 번.

### 1.1 FND — 공통 기반 (웨이브 1)

| 경로 | 내용 | 에이전트 | 시험 |
|---|---|---|---|
| `kbj/core/indicators.py`(새) | 부록 A 의 함수 전부 — 입력 `Sequence[float \| None]`, 출력 같은 길이 리스트(워밍업·결측은 `None`, 0 으로 채우지 않는다). 순수 Python(numpy 없음 — board 와 같은 부동소수 순서) | K | `tests/unit/core/test_indicators.py`(손계산 표 + board 원본 함수와 같은 입력 같은 값 — legacy 를 import 하지 않고 원본 값 표를 고정), `tests/property/test_indicator_props.py`(창 밖 불변·SMA 선형성·RSI 0~100·볼린저 상하 대칭·EMA 상수열 고정점) |
| `kbj/engines/rules/{__init__,dsl,frame,evaluate,cooldown}.py`(새) | `dsl`: pydantic v1 모델(§4.1)·검증(깊이·개수·지표/연산자 목록·모드별 허용)·`dsl_hash`(정규화 JSON sha256)·JSON 스키마 생성. `frame`: 일봉·수급 원장 + (장중이면) 부분 봉 → 지표 배열 묶음(anchor·split_guard·결측 사유). `evaluate`: (규칙, 프레임, 칸) → `Verdict(ok, conds[…값·기준·전 값], skipped_reason)`. `cooldown`: (이전 신호들, 시점) → 허락/막힘 | K | `tests/unit/engines/rules/test_{dsl,frame,evaluate,cooldown}.py`, `tests/property/test_rules_eval_props.py`(AND/OR 드모르간·조건 순서 무관·미래 칸을 바꿔도 과거 판정 불변 = 미래참조 없음) |
| `kbj/engines/backtest/{__init__,costs,stats}.py`(새) | 체결가·수수료·거래세·슬리피지, 거래 통계(승률·평균·중앙·PF·MDD·연환산) — board `backtest.py:stats`:236 승격(같은 단언 시험 일부) | K | `tests/unit/engines/backtest/test_{costs,stats}.py` |
| `config/rules.yaml`·`kbj/config/rules_cfg.py`(새 — 모르는 키 오류) | anchor_date, horizons `[1,3,5,10,20]`, cooldown 기본·하한, intraday_max_codes 60, DSL 상한, 지수 매핑(KOSPI·KOSDAQ), 비용(fee_bps·tax_pct [확인 필요])·즉석 백테스트 상한, 일치 감시 창 20, 보존(watch_quote_slot 2년 [제안]) | K | `tests/unit/config/test_rules_cfg.py` |
| `config/rule_examples.yaml`(새) | 예시 10개(§4.1.4) — 편집기 '예시 불러오기' 와 완료 시험이 같은 파일 | K | DSL 검증 통과 시험 |
| `config/schemas/rule_v1.schema.json`·`web/src/api/rule.schema.json`(생성 전용) | `python -m kbj.engines.rules.dsl schema --write` | K | `--check` |
| `docs/metrics.md` P7 다음 절(부록 A 를 옮김) | CLAUDE §4 "먼저 쓰고 구현" | K | 문서 |
| `kbj/services/api/readers/stock.py`(한 곳) | 자체 `sma` → `kbj.core.indicators.sma`(값 같음 — P4 시험 그대로) | K | `tests/unit/api/test_routes_stock*.py` 그대로 녹색 |
| `kbj/core/rules_rows.py`(새) | frozen dataclass: `RuleRow`·`RuleVersion`·`QuoteSlot`·`SignalRow(signal_id, mode, rule_id, version, dsl_hash, code, eval_ts, trade_date, entry_price, evidence, input_fp, status, ticket, quality)`·`OutcomeRow`·`BacktestRun`·`MatchRow`·`AnalysisRow`·`TradeRow`·`PickRow` | S | `tests/unit/core/test_rules_rows.py` |
| `kbj/store/migrations/NNNN_rules_journal.sql`(새 — P7 다음 번호) | §3.3 | S | `tests/test_store_layout.py` 확장, 통합 `tests/integration/test_migrations_pg.py` 확장(두 번 적용·`kbj_public_export` 가 새 표를 못 읽음 — Docker 는 메인) |
| `kbj/store/repos/{rules,personal}.py`(새)·`journal.py`(추가 — 일지·매매·추천 읽기)·`__init__.py`·`memory.py` | 표마다 쓰기 함수는 그 표의 쓰기 주체만 부른다(§3.3) — 이름에 주체를 적는다(`put_signals_intraday` 등) | S | `tests/unit/store/test_repos_p8.py`(메모리·Postgres 같은 계약) |
| `config/jobs.yaml`(§0.4 — 전부 false)·`config/notify.yaml`(`alert.rule` 노트 갱신·**`alert.rule_close`** 새 종류 `topic: alert`·`dedup: daily`)·`config/limits.yaml`(`kis.priorities.rules_intraday: P2`) | — | S | `tests/unit/scheduler/test_registry_validation.py`(§0.4 단언 — `ENABLED_BY_PHASE` 는 WRAP), notifier 정책 시험 |
| `kbj/data/private/kis/datasets.py`(한 행) | `watch_quotes_intraday` 의 `store="prv_alerts.watch_quote_slot"`·노트("P8 저장 — 장중 재생용") | S | `tests/unit/data/test_catalog*.py` |
| `kbj/services/api/models/{rules,backtest,journal}.py`·`routes/{rules,backtest,journal,portfolio}.py`·`readers/{rules,rules_stats,backtest,journal,portfolio}.py`(새 — **빈 리더: 언제나 `NoData`**, 쓰기 라우트는 501)·`readers/_common.py`(`ApiRepos` 에 새 저장소)·`app.py`·`cache.py`(라우터·캐시 키) | 모델은 §5 표 그대로 고정. 쓰기 라우트 의존 `require_session`·`require_csrf`·본문 상한 공용 함수 `kbj/services/api/writes.py`(새) | S | `tests/unit/api/test_routes_p8_shape.py`(모든 라우트 200/404/501 모양·쓰기 라우트 CSRF 없으면 403) |
| `web/src/api/openapi.json`·`types.gen.ts`(생성) | `python -m kbj.services.api openapi --write && (cd web && npm run gen:types)` | S | `--check` |
| `web/src/app/pages.ts`(glob 에 11·12, plan 문구), `web/src/pages/{p11_rules,p12_journal}.ts`(새 — 패널 자리와 하위 모듈 import 만, 본문 '준비 중') | 공개 빌드는 자물쇠(`lock: 'personal'`)만 — 본문 모듈은 로그인 빌드에만 | S | `web/test/pager.test.ts`·`tier.test.ts` 확장 |
| `web/src/ui/{form,heatmap}.ts`(새)·`web/src/design/components.css`(부품 클래스) | `form`: 선택·숫자·토글·행 추가/삭제·오류 표시(터미널 글꼴·테마 5종 토큰), `heatmap`: −1~1 발산 색·라벨 줄임·셀 상세. SVG/DOM, 외부 라이브러리 없음, 모바일 폭 | S | `web/test/ui_p8.test.ts` |
| `web/scripts/check-bundle.mjs`(필요할 때만) | 공개 번들 금지 문자열에 `/api/rules`·`/api/journal`·`/api/backtest`·`/api/portfolio` | S | `web/test/scripts.test.ts` |
| `tests/fixtures/synthetic/p8_world.py`·`p8_expect.yaml`(새) | 고정 시드 `build_world(seed=20261102)`: 80종목 × 320 거래일 일봉(분할·정지·신규상장·우선주), 투자자별 일별, 지수 2, 관심종목·보유·매매 30건·일지 5건, 장중 5일 × 40 슬롯 현재가, 규칙 예시 10개의 **기대 신호 수**(생성기가 심은 패턴 — 손으로 확인한 칸) | S | `tests/unit/fixtures/test_p8_world.py`(결정성·합성 표시) |
| `tests/unit/api/make_web_fixtures.py`(틀 — `render_p8()`·`--only`) + 빈 모듈 `tests/unit/api/web_fixtures_p8/{rules,backtest,journal}.py` | 도메인 묶음이 자기 모듈만 채우고 `web/test/fixtures/api/<도메인>/**` 만 쓴다 | S | `tests/unit/api/test_web_fixtures.py` 확장 |
| `docs/probe_results.md`(P7 다음 번호 한 줄) | FHKST01010100 의 시가·고가·저가·누적 거래량 칸 이름·단위 [실측 필요 — 파서는 P3 마감 수집이 쓰는 것과 같음] | S | 문서 |

FND 가 하지 않는 것: 작업 처리기·근거 문구·발송·백테스트 실행기·성과 계산·일지/매매 본문·리더 본문·페이지 본문(웨이브 2), `tests/fakes/kis_server.py`(EVL), sim 시나리오(WRAP).

### 1.2 EVL — 실시간 평가·발송·규칙 쓰기 (웨이브 2, 페이지 11)

| 경로 | 내용 |
|---|---|
| `kbj/engines/rules/{live,evidence}.py`(새) | `live`: 슬롯 시세 → 부분 봉(시가·고가·저가·현재가·누적 거래량 — 정지·상한가 플래그), 대상 목록 자르기(D-P8-20). `evidence`: 근거 메시지 템플릿(§4.4)·마감 묶음 본문·`/시그널` 답 |
| `kbj/services/engine/rules.py`(새) | 처리기 `intraday`(대상 → KIS 현재가(리미터 P2) → `watch_quote_slot` 저장 → 프레임 → 평가 → 쿨다운 → `signal_intraday` 저장 → `notify(kind='alert.rule', subject=f'{rule_id}:{code}')`), `close`(원장 → 평가 → `signal_close` → 16:00 묶음 1통). 한 종목·한 규칙 실패는 그 칸만 `error` 로 적고 나머지 계속(격리) |
| `kbj/services/personal/{__init__,rules}.py`(새) | 규칙 쓰기 주체: 만들기·새 판·켜기/끄기·보관·예시 가져오기 — API·CLI(`python -m kbj.services.personal rules …`)가 같이 부른다 |
| `kbj/services/api/{models,readers,routes}/rules.py`(본문) | 규칙 CRUD·검증(`POST /api/rules/validate` — 오늘 원장으로 시험 평가, 저장 안 함)·신호 이력 |
| `kbj/services/notifier/commands.py`(`/시그널` 처리기 한 줄 + 함수) | 오늘 신호 상위 10(규칙·종목·시각) — 저장소만 읽는다 |
| `tests/fakes/kis_server.py`(FHKST01010100 슬롯 시세 — 합성 세계에서) | WRAP 은 쓰기만 |
| `web/src/pages/p11/{rules,editor,history}.ts`, `tests/unit/api/web_fixtures_p8/rules.py`, `web/test/fixtures/api/rules/**` | 페이지 11 패널 ①②③(§6) |
| `tests/unit/engines/rules/test_{live,evidence}.py`, `tests/unit/services/test_rules_jobs.py`, `tests/unit/services/test_personal_rules.py`, `tests/unit/api/test_routes_rules.py`, `tests/unit/notifier/test_cmd_signals.py`, `web/test/pages/p11_rules.test.ts` | — |
| `docs/adr/` P7 다음 번호 A 초안 — 규칙 알림·평가 시점·ADR 0018 보충 | WRAP 이 확정 |

### 1.3 BKT — 백테스트·성과·일치 감시 (웨이브 2, 페이지 11·12)

| 경로 | 내용 |
|---|---|
| `kbj/engines/rules/{backtest,outcome,stats,match}.py`(새) | `backtest`: 모드 `close`·`intraday_replay`, 시점마다 `frame`·`evaluate`·`cooldown` 을 그대로 부른다(D-P8-6), 거래 모의(보유 N·손절·익절·비용 — `kbj.engines.backtest`). `outcome`: 신호 → 기간별 수익률·초과·MFE/MAE(거래정지·상폐는 사유). `stats`: 표본 수·승률·평균·중앙·사분위·초과·n<20 표시. `match`: 실시간 대 백테스트 차이·사유 분류 |
| `kbj/services/engine/rules_perf.py`(새) | 처리기 `outcome`·`nightly` |
| `kbj/services/api/{models,readers,routes}/backtest.py`·`readers/rules_stats.py`(본문) | 즉석 백테스트(상한·Redis 캐시)·야간 실행·일치 감시·성과 통계 |
| `web/src/pages/p11/perf.ts`, `web/src/pages/p12/{backtest,vs_live}.ts`, `tests/unit/api/web_fixtures_p8/backtest.py`, `web/test/fixtures/api/backtest/**` | 페이지 11 ④, 페이지 12 ①② |
| `tests/unit/engines/rules/test_{backtest,outcome,stats,match}.py`, `tests/property/test_backtest_props.py`(미래참조 없음 — d 일 판정이 d+1 이후 봉에 무관, 비용 0 이면 체결 대칭), `tests/unit/services/test_rules_perf.py`, `tests/unit/api/test_routes_backtest.py`, `web/test/pages/p12_backtest.test.ts` | — |
| `docs/adr/` P7 다음 번호 B 초안 — 백테스트·성과·일치 감시 | — |

### 1.4 SWG — board 스윙 엔진·US btsearch 승격 (웨이브 2)

| 경로 | 내용 |
|---|---|
| `kbj/engines/swing/{__init__,signals,backtest,search,systems,us_search}.py`(새) | ET 원본 본문을 옮기고 ① DB 읽기(`load`·`names`·`fetch`)를 함수 인자(봉·이름 목록)로 바꾸고 ② `sma`·`ema_arr`·`rsi_arr`·`adx_arr`·`pstdev`·`true_range`·`bb_width` 를 `kbj.core.indicators` 로 ③ `stats` 를 `kbj.engines.backtest.stats` 로. 판정·체결 규칙은 바꾸지 않는다 |
| `kbj/services/engine/swing.py`(새) | 읽기(원장 `prv_market.daily_bar` KR·US, 유니버스 PIT) + CLI `python -m kbj.services.engine.swing {signals,backtest,search,systems,us-search} [--asof] [--send]`(`--send` 는 기존 kind `board.signals`·`board.manual`) |
| `config/signals.yaml`(`git mv` ← `ET:board/config/signals.yaml`) | legacy shim 이 kbj 경로를 읽는다 |
| `legacy/etf_traker/board/engine/{backtest,search,signals,systems}.py`·`board/us/btsearch.py`(shim — kbj 다시 내보내기 + DB 읽기 어댑터), `board/tests/test_{backtest,search,signals,systems,us_search}.py`(→ `tests/unit/engines/swing/` 승격, legacy 에는 다리 시험 1), `legacy/etf_traker/board/MIGRATION.md` 'P8' 절 | P3 E1·P5 US 의 shim 방식(계약 ② — kbj 는 legacy 를 모른다) |
| `kbj/engines/us/__init__.py`(머리말 한 줄 — "btsearch 는 `kbj.engines.swing.us_search`") | — |
| `docs/adr/` P7 다음 번호 C 초안 — 스윙 엔진 승격·지표 교체 | — |

### 1.5 JNL — 저널·포트폴리오·관심종목 쓰기·이관 (웨이브 2, 페이지 12)

| 경로 | 내용 |
|---|---|
| `kbj/engines/journal/{__init__,portfolio,correlation,prefill}.py`(새) | `portfolio`: 매매 → 포지션(이동평균 단가)·실현/평가 손익·보유일·월별 수익(최신 원장 종가·quality 표시). `correlation`: 일간 수익률 피어슨(60·120일, 겹치는 날 < 40 이면 `None`). `prefill`: 분석 일지 초안(P4 밸류에이션·시나리오 적정가·52주 수익률 읽기 결과 → 칸, 출처 칸 채움) |
| `kbj/engines/rules/legacy_sd.py`(새) | SD 규칙 종류 → DSL 변환 표(§4.1.5) — 순수 |
| `kbj/services/personal/journal.py`(새) | 일지·매매 쓰기 주체(API·CLI·이관 공용), 매매 CSV 가져오기(`python -m kbj.services.personal trades import --csv`) |
| `kbj/services/watchlist/__init__.py`(P4 — `add`·`remove`·`reorder` 추가, source `api`) | 관심종목 쓰기 주체는 그대로 이 모듈 하나 |
| `kbj/store/legacy_import/mappings.py`(P8 행 셋 → 실제 매핑)·`kbj/store/legacy_import/sd_p8.py`(새) | SD `alert_rules`(→ `kbj.services.personal.rules` 경유 — 변환 실패는 목록), `analysis_journal`, `trade_journal`, `recommendation_history`, `cache/server_portfolio.json`(포지션 → 기초 매수 기록, [확인 필요]) |
| `kbj/services/api/{models,readers,routes}/journal.py`·`readers/portfolio.py`·`routes/portfolio.py`(본문)·`routes/watchlist.py`·`readers/watchlist.py`(P4 — 쓰기 라우트 추가) | §5 |
| `web/src/pages/p12/{journal,portfolio,corr,picks}.ts`, `web/src/pages/p8/header.ts`(P4 — 관심종목 별 토글), `tests/unit/api/web_fixtures_p8/journal.py`, `web/test/fixtures/api/journal/**` | 페이지 12 ③④⑤⑥ |
| `tests/unit/engines/journal/**`, `tests/unit/engines/rules/test_legacy_sd.py`, `tests/unit/services/test_personal_journal.py`, `tests/unit/watchlist/test_write.py`, `tests/unit/store/test_legacy_import_p8.py`(합성 SD sqlite·json — `tests/fixtures/synthetic/sqlite/` 생성기), `tests/unit/api/test_routes_journal.py`, `web/test/pages/p12_journal.test.ts` | — |
| `docs/adr/` P7 다음 번호 D 초안 — 개인 쓰기 API·저널·포트폴리오 | — |

### 1.6 WRAP — 마무리 (웨이브 3)

1. **작업 켜기**: `config/jobs.yaml` 의 `rules.intraday`·`rules.close`·`rules.outcome`·`rules.backtest_nightly` `enabled: true`(FHKST01010100 은 P3 마감 수집에서 운영 확인된 TR — 장중 칸 이름만 [실측 필요]이고 파서가 같다 → 켠다 [제안]), `ENABLED_BY_PHASE["P8"]`. `retired` 행(§0.4).
2. **완료 시험**: `tests/acceptance/test_rules_parity.py`(§0.3 ①~⑤), `tests/sim/test_rules_days.py`(§0.3 ②), `web/test/acceptance/p11_p12_pages.test.ts`, `tests/unit/test_no_orders.py`.
3. **시뮬레이션 공유 파일**: `tests/sim/harness.py`(P8 작업·가짜 KIS 슬롯 시세 등록), 새 `tests/sim/p8.py`. 기존 하루 sim(`test_one_day_p5` 등)의 발송·키 수 상수에 P8 작업이 끼면 고친다(꺼진 채 비교하던 상수 — 근거를 주석에).
4. **CI**(`.github/workflows/ci.yml`): 새 시험 경로·sim 잡 `-k rules_days`.
5. **검사**(D-P8-22): import-linter 계약(P7 다음 번호)·`tests/test_import_contracts.py`·`pyproject.toml`, `scripts/check_canonical.py` 그룹 `ta_indicators` + `scripts/canonical_baseline.txt`(legacy 줄 — 줄이는 방향만), `tests/unit/test_public_writers.py`(새 작업은 `prv_` 만).
6. **legacy 정리**(§1.7): SD 함수·파일 삭제, `config/notify.yaml legacy_kinds` 줄 정리, `legacy/stock_dashboard/MIGRATION.md` 'P8', `scripts/test_legacy.sh` 기준 수(board·stock_dashboard).
7. **문서·ADR**: P8 ADR A~D 상태 확정, **ADR 0018 에 '보충(P8): 16:00 발송 넷 — `rules.close`' 한 줄**, `docs/metrics.md` P8 절 확정, `docs/PLAN.md` §8 P8 '결과' 절(실측 수치), `docs/probe_results.md`, `web/src/app/pages.ts` 13 의 단계 문구(§10 Q9 답대로), 이 문서 상태 '구현 완료'.
8. 메인이 마지막에 도커 통합·legacy 전체·sim 전체를 병렬 스크립트로 한 번.

### 1.7 legacy 이전 범위 (D-P8-21 — 옮긴 만큼만)

| legacy | P8 처리 | 옮김(kbj) | 폐지 | legacy 에 남김 | 묶음 |
|---|---|---|---|---|---|
| `ET:board/engine/{signals,backtest,search,systems}.py` | `kbj/engines/swing` 승격, legacy 는 shim(`run.py cmd_signals`:466·`cmd_backtest`:562·`cmd_search`:622 가 계속 import) | 59(`test_signals` 25·`test_backtest` 13·`test_systems` 15·`test_search` 6) | — | shim + 다리 1 | SWG |
| `ET:board/us/btsearch.py` | `kbj/engines/swing/us_search.py`, legacy shim(`cmd_us_search`:1927) — ADR 0023 §2-8 해소 | 3(`test_us_search`) | — | shim | SWG |
| `SD:server.py` `check_alert_rules`:4285·`_check_trailing_stops`:4144·`_alert_cooldown_ok`:4593·`alert_watchlist_price`:4739·`alert_discovery_new_entries`:4634 | `rules.intraday`·`rules.close`(트레일링은 `trail_from_peak` 조건 — §4.1.2) → **삭제**(SD 스케줄 등록 줄 포함) | 0(원본 시험 없음) | — | — | WRAP |
| `SD:server.py` 매매 저널 12432~12655·분석 일지 라우트 13284~13420·`/journal` 화면 742·`api_portfolio_sync`:4126·`api_backtest`:9798·`_bt_*`·상관 10001~10100·추천 이력 10101~10340 | 페이지 12 + 로그인 API → **삭제**(다른 SD 함수가 부르지 않을 때 — `grep -n` 근거를 MIGRATION 에) | 0 | — | 추천 이력 **저장**(`save_recommendation_snapshot`)은 SD 발굴이 남아 있으면 남김 | WRAP |
| `SD:analysis_journal_api.py`·`analysis_journal_helper.py`·`review_validator.py`·`valuechain.py` | `kbj.engines.journal.prefill`·`kbj.services.personal.journal` → **삭제**(P5 가 `valuechain.py` 를 일지 helper 때문에 남겼다 — 같이 지운다), `migrations/004·006·007` 은 SD DB 이관 근거로 남김 | 0 | — | 마이그레이션 스크립트 | WRAP |
| `SD:server.py _calc_rsi_macd`:383·`_calc_bollinger`:557·`_calc_adx`:501 | SD 차트 API 가 아직 부르면 **남김** — 기준선 `ta_indicators` 에 센다(P9 에 legacy 비우기) | 0 | — | 남김 | WRAP |

### 1.8 묶음별 파일 소유 (겹치지 않는다)

| 묶음 | 손대는 파일 |
|---|---|
| FND | `kbj/core/{indicators,rules_rows}.py`, `kbj/engines/rules/{__init__,dsl,frame,evaluate,cooldown}.py`, `kbj/engines/backtest/**`, `kbj/config/rules_cfg.py`, `config/{rules,rule_examples}.yaml`, `config/schemas/rule_v1.schema.json`, `web/src/api/rule.schema.json`, `kbj/store/migrations/NNNN_rules_journal.sql`, `kbj/store/repos/{rules,personal}.py`·`journal.py`·`__init__.py`·`memory.py`, `config/{jobs,notify,limits}.yaml`(웨이브 1), `kbj/data/private/kis/datasets.py`(한 행), `kbj/services/api/writes.py`·`models/{rules,backtest,journal}.py`·`routes/{rules,backtest,journal,portfolio}.py`·`readers/{rules,rules_stats,backtest,journal,portfolio}.py`(웨이브 1 — 모양·빈 리더)·`readers/_common.py`·`readers/stock.py`(sma 한 곳)·`app.py`·`cache.py`, `web/src/api/{openapi.json,types.gen.ts}`(생성), `web/src/app/pages.ts`, `web/src/pages/{p11_rules,p12_journal}.ts`, `web/src/ui/{form,heatmap}.ts`, `web/src/design/components.css`, `web/scripts/check-bundle.mjs`(필요 시), `tests/fixtures/synthetic/{p8_world.py,p8_expect.yaml}`, `tests/unit/api/make_web_fixtures.py`·`web_fixtures_p8/__init__.py`, `tests/unit/scheduler/test_registry_validation.py`(웨이브 1), `docs/metrics.md`(P8 절), `docs/probe_results.md`(한 줄) + 위 시험들 |
| EVL | `kbj/engines/rules/{live,evidence}.py`, `kbj/services/engine/rules.py`, `kbj/services/personal/{__init__,rules}.py`, `kbj/services/api/{models,readers,routes}/rules.py`(웨이브 2 본문), `kbj/services/notifier/commands.py`, `tests/fakes/kis_server.py`, `web/src/pages/p11/{rules,editor,history}.ts`, `tests/unit/api/web_fixtures_p8/rules.py`, `web/test/fixtures/api/rules/**`, ADR A 초안 + §1.2 시험 |
| BKT | `kbj/engines/rules/{backtest,outcome,stats,match}.py`, `kbj/services/engine/rules_perf.py`, `kbj/services/api/{models,readers,routes}/backtest.py`·`readers/rules_stats.py`(웨이브 2 본문), `web/src/pages/p11/perf.ts`, `web/src/pages/p12/{backtest,vs_live}.ts`, `tests/unit/api/web_fixtures_p8/backtest.py`, `web/test/fixtures/api/backtest/**`, ADR B 초안 + §1.3 시험 |
| SWG | `kbj/engines/swing/**`, `kbj/services/engine/swing.py`, `config/signals.yaml`(`git mv`), `kbj/engines/us/__init__.py`(머리말), `legacy/etf_traker/board/engine/{backtest,search,signals,systems}.py`·`board/us/btsearch.py`(shim)·`board/tests/test_{backtest,search,signals,systems,us_search}.py`(승격·다리)·`board/MIGRATION.md`('P8'), `tests/unit/engines/swing/**`, ADR C 초안 |
| JNL | `kbj/engines/journal/**`, `kbj/engines/rules/legacy_sd.py`, `kbj/services/personal/journal.py`, `kbj/services/watchlist/__init__.py`, `kbj/store/legacy_import/{mappings,sd_p8}.py`, `kbj/services/api/{models,readers,routes}/journal.py`·`readers/portfolio.py`·`routes/portfolio.py`(웨이브 2 본문)·`routes/watchlist.py`·`readers/watchlist.py`, `web/src/pages/p12/{journal,portfolio,corr,picks}.ts`, `web/src/pages/p8/header.ts`, `tests/unit/api/web_fixtures_p8/journal.py`, `web/test/fixtures/api/journal/**`, `tests/fixtures/synthetic/sqlite/sd_p8_gen.py`, ADR D 초안 + §1.5 시험 |
| WRAP | `config/jobs.yaml`(enabled·retired)·`config/notify.yaml`(legacy_kinds), `tests/unit/scheduler/test_registry_validation.py`(`ENABLED_BY_PHASE`), `tests/acceptance/test_rules_parity.py`, `tests/sim/{harness,p8,test_rules_days}.py`·기존 sim 상수, `web/test/acceptance/p11_p12_pages.test.ts`, `tests/unit/test_no_orders.py`, `tests/test_import_contracts.py`, `tests/unit/test_public_writers.py`, `pyproject.toml`(계약), `scripts/{check_canonical.py,canonical_baseline.txt,test_legacy.sh}`, `.github/workflows/ci.yml`, `legacy/stock_dashboard/**`(삭제·MIGRATION), `docs/{PLAN,metrics,probe_results}.md`, `docs/adr/0018-post-close-1600.md`(보충 한 줄)·ADR A~D 확정, `web/src/app/pages.ts`(13 문구 — Q9), `docs/p8_design.md`(상태) |

**공유 파일 — 누가 언제**(한 칸에 한 묶음. P8 이 건드리는 앞 단계 파일은 이 표가 전부다)

| 파일 | 웨이브 1 | 웨이브 2 | 웨이브 3 |
|---|---|---|---|
| `config/jobs.yaml` | **FND**(§0.4 모양, 전부 false) | 안 고침 — 요청 | **WRAP**(enabled·retired) |
| `config/notify.yaml` | **FND**(`alert.rule` 노트·`alert.rule_close`) | 안 고침 | **WRAP**(legacy_kinds #6·#12·#13·#14 정리) |
| `config/limits.yaml` | **FND**(`rules_intraday: P2`) | 안 고침 | — |
| `config/rules.yaml`·`rule_examples.yaml` | **FND** | 안 고침(값은 요청 — 비용 값은 BKT 가 요청) | WRAP |
| `kbj/data/private/kis/datasets.py` | **FND**(한 행) | 안 고침 | — |
| `kbj/store/repos/{__init__,memory,journal}.py` | **FND** | 안 고침(모자라면 FND 담당에게 요청 — 대기) | — |
| `kbj/services/api/{app,cache}.py`·`readers/_common.py` | **FND** | 안 고침 | — |
| `kbj/services/api/readers/stock.py` | **FND**(sma 한 곳) | 안 고침 | — |
| `kbj/services/api/models/*`·`routes/*`·`readers/*`(P8 새 파일) | **FND**(모양) | 도메인 묶음이 **자기 파일만**(EVL rules / BKT backtest·rules_stats / JNL journal·portfolio·watchlist) | — |
| `kbj/services/api/{routes,readers}/watchlist.py`(P4) | — | **JNL** | — |
| `kbj/services/watchlist/__init__.py`(P4) | — | **JNL** | — |
| `kbj/services/notifier/commands.py`(P2) | — | **EVL** | — |
| `kbj/store/legacy_import/mappings.py`(P1) | — | **JNL** | — |
| `kbj/engines/us/__init__.py`(P5) | — | **SWG**(머리말) | — |
| `web/src/api/openapi.json`·`types.gen.ts`·`rule.schema.json` | **FND**(생성) | 생성 전용 — 모델을 바꾼 묶음이 생성 명령만 | **WRAP**(`--check`) |
| `web/src/app/pages.ts` | **FND**(glob·plan) | 안 고침 | **WRAP**(13 문구) |
| `web/src/ui/*`·`web/src/design/*`·`web/scripts/check-bundle.mjs` | **FND** | 안 고침 | — |
| `web/src/pages/p8/header.ts`(P4) | — | **JNL**(별 토글) | — |
| `tests/fakes/kis_server.py` | — | **EVL** | WRAP 은 쓰기만 |
| `tests/sim/harness.py`·기존 sim 파일 | — | 안 고침 | **WRAP** |
| `tests/unit/api/make_web_fixtures.py` | **FND**(틀) | 도메인 묶음은 `web_fixtures_p8/<도메인>.py`·`web/test/fixtures/api/<도메인>/**` 만 | — |
| `tests/unit/scheduler/test_registry_validation.py` | **FND**(P8 행·발송 넷) | 안 고침 | **WRAP**(`ENABLED_BY_PHASE["P8"]`) |
| `pyproject.toml`·`tests/test_import_contracts.py`·`scripts/check_canonical.py`·`canonical_baseline.txt`·`test_legacy.sh` | — | 안 고침 | **WRAP** |
| `legacy/etf_traker/board/**` | — | **SWG** | WRAP 은 기준 수만(`test_legacy.sh`) |
| `legacy/stock_dashboard/**` | — | 안 고침(JNL 은 읽기만) | **WRAP** |
| `docs/metrics.md` | **FND**(P8 절) | 안 고침(정의 변경은 요청) | **WRAP** |
| `docs/probe_results.md` | **FND**(한 줄) | — | **WRAP** |
| `docs/adr/0018-post-close-1600.md` | — | — | **WRAP**(보충 한 줄) |
| `docs/PLAN.md`·`.github/workflows/ci.yml` | — | — | **WRAP** |

### 1.9 옮기는 원본 시험 수 [추정 — WRAP 이 실측]

| 묶음 | 옮김(kbj 로) | 폐지 | legacy 기준 수 변화 |
|---|---|---|---|
| SWG | 62(board `test_signals` 25·`test_backtest` 13·`test_systems` 15·`test_search` 6·`test_us_search` 3) | 0 | board **1,039 → 978**(−62 + 다리 1) [추정 — P6·P7 이 board 를 바꾸면 그 수에서] |
| WRAP(SD) | 0 | 0 | stock_dashboard 기준 수 그대로(지우는 함수에 시험 없음 — 검사 스크립트의 함수 목록 대조만 고침) |

### 1.10 묶음별 완료 확인 명령 (모두 종료 코드 0)

> 같은 작업 트리에서 여러 묶음이 동시에 일한다 — 묶음은 **자기 경로 시험 + 빠른 공통 명령**만 돌리고 실패는 자기 파일 것만 고친다(남의 미완성으로 깨진 공통 시험은 그 묶음에 알린다). **도커 통합(`-m integration`)·legacy 전체(`test_legacy.sh all`)·sim 전체는 돌리지 않는다**. 시험은 `-q`, 실패만 본다. 시험이 읽는 파일이 `.gitignore` 에 걸리지 않았는지 `git status --ignored` 로 본다.

**공통(모든 백엔드 묶음)**: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run lint-imports && uv run python scripts/check_canonical.py && uv run python scripts/check_public_safety.py --exclude legacy && uv run pytest -m "not sim and not integration and not legacy" -q` — 웹을 고친 묶음은 `cd web && npm run lint && npm run typecheck && npx vitest run <내 시험>` 도.

| 묶음 | 묶음 명령(공통에 더해) |
|---|---|
| FND | `uv run pytest tests/test_store_layout.py tests/unit/core/test_indicators.py tests/unit/core/test_rules_rows.py tests/property/test_indicator_props.py tests/property/test_rules_eval_props.py tests/unit/engines/rules tests/unit/engines/backtest tests/unit/config/test_rules_cfg.py tests/unit/store/test_repos_p8.py tests/unit/scheduler tests/unit/fixtures/test_p8_world.py tests/unit/api/test_routes_p8_shape.py tests/unit/api/test_web_fixtures.py -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.engines.rules.dsl schema --check && uv run python -m kbj.services.api openapi --check && (cd web && npm run gen:types -- --check && npx vitest run test/pager.test.ts test/tier.test.ts test/ui_p8.test.ts test/scripts.test.ts)` |
| EVL | `uv run pytest tests/unit/engines/rules/test_live.py tests/unit/engines/rules/test_evidence.py tests/unit/services/test_rules_jobs.py tests/unit/services/test_personal_rules.py tests/unit/api/test_routes_rules.py tests/unit/notifier tests/unit/fakes -q && uv run python -m tests.unit.api.make_web_fixtures --only rules && uv run pytest tests/unit/api/test_web_fixtures.py -q -k rules && (cd web && npx vitest run test/pages/p11_rules.test.ts)` |
| BKT | `uv run pytest tests/unit/engines/rules/test_backtest.py tests/unit/engines/rules/test_outcome.py tests/unit/engines/rules/test_stats.py tests/unit/engines/rules/test_match.py tests/property/test_backtest_props.py tests/unit/services/test_rules_perf.py tests/unit/api/test_routes_backtest.py -q && uv run python -m tests.unit.api.make_web_fixtures --only backtest && uv run pytest tests/unit/api/test_web_fixtures.py -q -k backtest && (cd web && npx vitest run test/pages/p12_backtest.test.ts)` |
| SWG | `uv run pytest tests/unit/engines/swing tests/unit/core/test_indicators.py tests/unit/engines/backtest -q && uv run python -m kbj.services.engine.swing --help && uv run bash scripts/test_legacy.sh board` |
| JNL | `uv run pytest tests/unit/engines/journal tests/unit/engines/rules/test_legacy_sd.py tests/unit/services/test_personal_journal.py tests/unit/watchlist tests/unit/store/test_legacy_import_p8.py tests/unit/api/test_routes_journal.py tests/unit/api -q -k "journal or portfolio or watchlist" && uv run python -m tests.unit.api.make_web_fixtures --only journal && (cd web && npx vitest run test/pages/p12_journal.test.ts test/pages/p8_stock.test.ts)` |
| WRAP | 공통 + `uv run pytest tests/acceptance -q && uv run pytest tests/sim -m "sim and not slow" -q -k "rules_days or one_day_p5 or brief_once" && uv run pytest tests/unit/test_no_orders.py tests/unit/test_public_writers.py tests/test_import_contracts.py -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.services.api openapi --check && uv run python -m kbj.engines.rules.dsl schema --check && uv run python scripts/check_public_safety.py && bash scripts/build_public_site.sh --dry-run` + `cd web && npm run gen:types -- --check && npm run lint && npm run typecheck && npm test && npm run build:public && npm run build:login && node scripts/check-bundle.mjs` + 메인: Docker 통합·`test_legacy.sh all`·sim 전체·CI 녹색(푸시 뒤) |

---

## 2. 공개/로그인 배치

| 대상 | 등급 | 근거 |
|---|---|---|
| 페이지 11·12 전부(규칙·신호·성과·백테스트·일지·매매·포트폴리오·상관·추천 성과) | **로그인(개인)** | DATA_TIERS §2:57 "11·12·13 — 전부(개인 기능)". 입력도 KIS·KRX 시세(로그인) |
| 표 `prv_alerts.*`·`prv_journal.*` | 로그인 스키마 | `kbj_public_export` 는 `prv_*` 를 못 읽는다(통합 시험 확장) |
| 텔레그램 알림 본문 | 로그인(본인 채팅) | 원시세 재배포 아님 — 본인 알림 |
| 지표 정본·DSL·평가기·백테스트 엔진 코드 | 코드(레포 공개) | 코드는 공개 가능, 실데이터 fixture 는 금지 — 합성 세계만 |
| 공개 정적 사이트 | 변화 없음 — 페이지 11·12 는 자물쇠(`personal`) | §7 |

## 3. 데이터 흐름

### 3.1 한눈에

```
장중 09:00~15:30 (10분)                                   16:00 (ADR 0018)
 관심종목 main ∪ 보유 ∪ 규칙 코드(≤60)                     market.close_collect(15:35~) ─┐
   └ KIS FHKST01010100 → watch_quote_slot ─┐                                             ▼
 확정 일봉·수급(전 거래일까지) ────────────┤→ frame → evaluate → cooldown         rules.close ─→ signal_close
                                           └→ signal_intraday ─→ notify alert.rule      └→ notify alert.rule_close(1통)
                                                         (사건 시점)
다음 거래일 08:05 krx.daily(확정) → 08:30 rules.outcome → signal_outcome(1·3·5·10·20일)
                                  → 08:45 rules.backtest_nightly → backtest_run·backtest_signal·match_check
API(읽기) ← 신호·성과·백테스트·일지·포트폴리오 / API(쓰기) → 규칙·일지·매매·관심종목(쓰기 모듈 하나씩)
```

### 3.2 작업 표 (P8 에 켜는 것 — 시각 KST)

| 작업 | 시각 | 입력 | 출력 | 발송 |
|---|---|---|---|---|
| `rules.intraday` | 평일 장중 10분(09:00~15:30, 40슬롯) | KIS 현재가(≤60), `daily_bar`·`stock_investor_daily`(전 거래일까지), `split_check` | `watch_quote_slot`, `signal_intraday` | `alert.rule` 사건 시점(쿨다운) |
| `rules.close` | 16:00(캐치업 20:30), 굳은 의존 마감 수집 | `daily_bar`(오늘 KIS 마감 — `estimated` 가능), 수급 원장 | `signal_close` | `alert.rule_close` 16:00 1통(0건이면 없음) |
| `rules.outcome` | 08:30, 굳은 의존 `krx.daily` | 신호 뷰, 확정 일봉·지수 | `signal_outcome` | 없음 |
| `rules.backtest_nightly` | 08:45, 굳은 의존 `rules.outcome` | 켜진 규칙 판, 원장 3년 + anchor, 신호 뷰 | `backtest_run`·`backtest_signal`·`match_check` | 없음(`logic` 차이 → health 오류) |

### 3.3 마이그레이션 `NNNN_rules_journal.sql` (P7 다음 번호 — DDL 요약, FND)

| 표 | 열(요약) | 키 | 쓰는 주체(하나) |
|---|---|---|---|
| `prv_alerts.rule` | `rule_id`(`r_` + 12자), `name`, `current_version`, `enabled`, `archived_at`, `source`(`api`·`cli`·`import:sd`·`example`), `created_at`, `updated_at`, `loaded_by` | `rule_id` | `kbj.services.personal.rules` |
| `prv_alerts.rule_version` | `rule_id`, `version`, `dsl jsonb`, `dsl_hash`, `created_at` — **바꾸지 않는다** | (`rule_id`, `version`) | 같음 |
| `prv_alerts.watch_quote_slot`(hypertable) | `slot_ts`, `code`, `open`·`high`·`low`·`price`, `volume`, `turnover`, `change_pct`, `flags`, `quality`, `source`, `received_at` | (`slot_ts`, `code`) | `rules.intraday` |
| `prv_alerts.signal_intraday`·`signal_close` | `signal_id`(`si_`/`sc_` + ulid), `rule_id`, `version`, `dsl_hash`, `code`, `eval_ts`, `trade_date`, `entry_price`, `evidence jsonb`, `input_fp`, `status`(`sent`·`cooldown`·`suppressed`·`dry`·`skipped`), `skip_reason`, `ticket`, `quality` | `signal_id`, UNIQUE(`rule_id`,`version`,`code`,`eval_ts`) | `rules.intraday` / `rules.close` |
| 뷰 `prv_alerts.signal` | 두 표 UNION ALL + `mode` | — | (읽기 전용) |
| `prv_alerts.signal_outcome` | `signal_id`, `horizon`, `due_date`, `close_at`, `ret`, `index_code`, `excess`, `mfe`, `mae`, `reason`(정지·상폐·미도래), `quality`, `computed_at` | (`signal_id`, `horizon`) | `rules.outcome` |
| `prv_alerts.backtest_run`·`backtest_signal`·`match_check` | 실행(규칙 판·기간·파라미터·원장 as_of·요약), 신호(종목·날짜·모드·진입가·기간 수익), 감시(날짜·규칙·실시간 n·백테스트 n·차이 목록·사유) | 각 `run_id`·(`run_id`,`code`,`trade_date`)·(`trade_date`,`rule_id`) | `rules.backtest_nightly` |
| `prv_journal.analysis` | SD `analysis_journal` 칸(종목·분석일·밸류체인 id 3(빈칸 허용 — 사전은 사용자 별도)·현재가·52주 수익률·fwd PER·밴드 위치·EV/EBITDA·시총·OPM·bear/base/bull EPS·PER·TP 와 출처·반영도·메모·태그 `text[]`·결론) + `source`, `updated_at` | `id bigint identity` | `kbj.services.personal.journal` |
| `prv_journal.trade` | `id`, `code`, `name_at_trade`, `side`(`buy`·`sell`), `trade_date`, `price numeric`, `qty`, `fee`, `tax`, `strategy`, `memo`, `signal_id`(선택 — 알림과 연결), `source`, `created_at` | `id` | 같음 |
| `prv_journal.pick` | SD `recommendation_history` 그대로(`date`, `source`, `rank`, `code`, `name`, `market`, `score`, `price_at_rec`, `tags jsonb`) — **이관 전용** | (`date`,`source`,`code`) | `kbj.store.legacy_import`(한 번) |

멱등(`IF NOT EXISTS`), 적용된 번호는 고치지 않는다(체크섬). 권한: 새 표는 `kbj_app` 만, `kbj_public_export` 없음.

### 3.4 KIS 호출량 (초당 4건 — `config/limits.yaml kis.rate`)

| 작업 | 호출 | 하루 |
|---|---|---|
| `rules.intraday` | 대상 ≤ 60 × 40 슬롯(FHKST01010100, 우선순위 P2) — 슬롯당 약 15초 | ≤ 2,400 |
| `rules.close`·`outcome`·`nightly` | 0(원장만) | 0 |

같은 :00·:10 슬롯에 `flows.intraday`(P3)·P7 GEX 폴링이 겹친다 — P2 우선순위·슬롯 마감 10분 안. 겹침이 측정상 문제면 `rules.intraday` 를 +1분 엇갈림으로 [제안 — WRAP 이 sim 으로 판단]. P6·P7 이 늘린 예산을 FND 가 다시 더해 `daily_soft_cap` 안인지 시험한다.

### 3.5 설정 파일 (FND — 숫자·목록은 한 곳)

`config/rules.yaml`: `anchor_date`, `horizons`, `cooldown_min {default: 1440, floor: 60}`, `intraday_max_codes: 60`, `dsl {max_depth: 3, max_conditions: 12}`, `index {KOSPI: "1001", KOSDAQ: "2001"}` [확인 필요 — 원장 지수 코드], `costs {fee_bps: 1.5, sell_tax_pct: 0.20, slippage_bps: 0}` [확인 필요 — 거래세율은 시행 연도 기준], `adhoc {max_codes: 100, max_years: 5, timeout_s: 20, cache_ttl_s: 3600}`, `match {window_days: 20}`, `retention {watch_quote_slot_days: 730}`.

---

## 4. 엔진 명세

### 4.1 규칙 DSL v1

#### 4.1.1 모양

```json
{
  "v": 1,
  "name": "반도체 과매도 반등",
  "universe": {"watchlist": "main"},
  "mode": "close",
  "when": {"all": [
    {"ind": "rsi", "p": {"n": 14}, "op": "<", "value": 30},
    {"ind": "sma", "p": {"n": 5}, "op": "cross_above", "ref": {"ind": "sma", "p": {"n": 20}}},
    {"ind": "foreign_streak", "op": ">=", "value": 3}
  ]},
  "alert": {"cooldown_min": 1440, "priority": 2, "silent": false},
  "track": true
}
```

- `universe`: `{"codes": [...]}` · `{"watchlist": "<list>"}` · `{"portfolio": true}` · `{"market": "KOSPI"|"KOSDAQ"|"ALL"}`(**마감 전용** — 장중은 거절). 한 규칙에 하나.
- `mode`: `close` · `intraday` · `both`(장중과 마감이 같은 쿨다운을 나눈다).
- `when`: `{"all": [...]}`·`{"any": [...]}` 중첩(깊이 ≤ 3), 잎은 조건. `not` 은 두지 않는다(연산자 반대로 표현).
- 조건: `ind`(지표) + `p`(파라미터 — 지표마다 허용 키·범위) + `field`(여러 값 지표의 칸 — 예 `macd.hist`·`bb.lower`) + `op` + (`value` 또는 `ref` 다른 지표). `ref` 의 지표도 같은 목록.
- `op`: `<` `<=` `>` `>=` `==`(정수 지표만) `between`(`value: [lo, hi]`) `cross_above` `cross_below`(직전 칸 대 이번 칸 — 마감 모드는 d−1 대 d, 장중은 d−1 확정 대 부분 봉) `turns_positive` `turns_negative`(부호 전환).
- 값이 `None`(워밍업·결측·정지)이면 그 조건은 **거짓 + 사유**(`insufficient`·`missing`·`halted`) — 참으로 처리하지 않는다.

#### 4.1.2 지표 목록 (정의는 부록 A — 모두 `kbj.core.indicators` 또는 기존 정본)

| `ind` | 칸 | 파라미터(기본·범위) | 장중 | 정본 |
|---|---|---|---|---|
| `close`·`open`·`high`·`low` | 값 | — | 부분 봉 | 원장·슬롯 |
| `change_pct` | 전일 대비 % | — | ○ | indicators |
| `sma`·`ema` | 값 | `n` 5(2~250) | ○ | indicators |
| `rsi` | 값 | `n` 14(2~50) | ○ | indicators |
| `macd` | `line`·`signal`·`hist` | `fast` 12·`slow` 26·`signal` 9 | ○ | indicators |
| `bb` | `upper`·`mid`·`lower`·`pctb`·`width` | `n` 20·`k` 2.0 | ○ | indicators |
| `bb_width_rank` | 밴드폭의 N일 백분위(스퀴즈) | `n` 20·`k` 2·`lookback` 120 | ○ | indicators |
| `adx` | `adx`·`pdi`·`mdi` | `n` 14 | ○ | indicators |
| `atr` | 값 | `n` 14 | ○ | indicators |
| `vol_ratio` | 거래량 ÷ 직전 N일 평균(오늘 제외) | `n` 20 | ○(누적 — §4.3) | indicators |
| `high_n`·`low_n` | 직전 N일 최고·최저(오늘 제외) | `n` 20(2~250) | ○ | indicators |
| `new_high` | 신고가 여부(축 `d120`·`52w`·`ath`) | `axis` | ✕(마감) | board 엔진 3축(ADR 0017) |
| `foreign_streak`·`inst_streak` | 연속 순매수 일수(매도는 `side: sell`) | `side` | 전 거래일 확정 | `kbj.engines.flows.streak` |
| `foreign_sum`·`inst_sum` | 최근 N일 순매수 합(원) | `n` 5 | 전 거래일 확정 | 수급 원장 |
| `trail_from_peak` | 보유 시작 뒤 최고가 대비 하락 % | — | ○ | indicators + 포지션(`universe: portfolio` 전용) |

#### 4.1.3 검증

스키마(JSON) → 의미 검사(지표·칸·파라미터 범위, `ref` 의 단위 일치 — 가격 대 가격·비율 대 비율, `mode` 별 허용, `market` 우니버스는 마감만, `trail_from_peak` 는 포트폴리오만, 쿨다운 하한) → `dsl_hash`. 거절은 위치(JSON 포인터)·사유만 돌려준다.

#### 4.1.4 예시 10개 (`config/rule_examples.yaml` — 완료 기준의 대상)

| # | 이름 | 모드 | 조건 |
|---|---|---|---|
| E1 | RSI 과매도 | close | `rsi(14) < 30` |
| E2 | MACD 골든크로스 | close | `macd.line cross_above macd.signal` |
| E3 | 볼린저 하단 터치 | close | `low <= bb(20,2).lower` |
| E4 | 20일선 돌파 | close | `close cross_above sma(20)` |
| E5 | 외국인 3일 연속 | close | `foreign_streak >= 3` |
| E6 | 반도체 과매도 반등(REBUILD_BRIEF §6.4) | close | `all(rsi<30, sma5 cross_above sma20, foreign_streak>=3)` |
| E7 | 추세 강함 | close | `all(adx>25, adx.pdi > adx.mdi, close > sma(60))` |
| E8 | 거래량 급증 양봉 | intraday | `all(vol_ratio(20) >= 2, change_pct > 0)` |
| E9 | 장중 급등 | intraday | `all(change_pct >= 5, vol_ratio >= 1.5)` |
| E10 | 신고가 근접 쌍끌이 | intraday | `all(close >= high_n(250) × 0.97 — ref 배수 `scale: 0.97`, any(foreign_streak>=1, inst_streak>=1))` |

합성 세계가 각 규칙이 참이 되는 패턴을 심어 신호가 0 이 아니게 하고(`p8_expect.yaml` 기대 수), OR·AND 중첩·크로스·워밍업·정지·분할 사례를 모두 지나게 한다.

#### 4.1.5 SD 규칙 → DSL (이관 — JNL)

| SD `rule_type` | DSL |
|---|---|
| `price_above`·`price_below` | `close >= v`·`close <= v`(mode both) |
| `change_up`·`change_down` | `change_pct >= v`·`change_pct <= v` |
| `rsi_oversold`·`rsi_overbought` | `rsi(14) <= v(기본 30)`·`>= v(70)` |
| `macd_golden`·`macd_dead` | `macd.line cross_above/below macd.signal` |
| `foreign_strong_buy` | `foreign_streak >= v(3)` |
| `foreign_cum_buy` | `foreign_sum(5) >= v × 1e8`(SD 는 억원) |
| 미국 종목(`market: us`) | 변환하지 않음 — 목록으로 보고(장중 미국 평가 없음) |

SD 의 '하루 1번(`triggered_at`)' 은 `cooldown_min: 1440` 으로 옮긴다.

### 4.2 프레임·평가·쿨다운 (FND — 실시간·백테스트 공용)

1. **프레임** `build_frame(bars, flows, *, upto, partial=None, cfg)`: 원장 우선순위로 고른 봉(`kbj.store.repos.market.series` 와 같은 순위 — 저장소가 고른 결과를 받는다), anchor 부터 `upto` 까지, `partial` 이 있으면 마지막 칸에 붙인다. 정지일은 칸을 두되 가격 `None`. `split_guard` 의심이 창 안이면 `skipped='split_suspect'`. **입력 지문** `input_fp` = 마지막 260칸 OHLCV·수급 값의 sha256(앞 16자) — 일치 감시용.
2. **평가** `evaluate(rule, frame, i)`: 순수, 같은 입력이면 같은 출력. 조건마다 `CondResult(ok, value, ref_value, prev_value, prev_ref, reason)`. `all` 은 모든 잎을 평가해(단락하지 않는다 — 근거에 전부 싣기 위해) 결과를 낸다.
3. **쿨다운** `allow(prior_signals, rule, code, ts)`: 같은 (규칙, 종목)의 마지막 `sent`·`suppressed` 신호로부터 `cooldown_min` 이 지나야 허락(미끄러지는 창). `both` 모드는 장중·마감 신호를 함께 본다. 막힌 시점도 `status='cooldown'` 으로 **기록**한다(이력 화면·일치 감시).

### 4.3 장중 부분 봉의 뜻 (D-P8-9)

- 부분 봉 = (오늘 시가, 지금까지 고가·저가, 현재가를 종가 칸에, 누적 거래량). 모든 지표는 이 봉을 오늘 칸으로 넣고 같은 식을 쓴다 — 예: 장중 RSI 는 '지금 가격으로 마감한다면' 의 값.
- `vol_ratio` 는 누적 거래량 ÷ 직전 20일 평균(시간 보정 없음) — 장 초반에는 작게 나온다. 시간 보정은 하지 않는다 [제안 — 단순·재현 가능].
- `cross_*` 는 d−1 확정 칸 대 부분 봉 — 같은 날 여러 슬롯에서 참일 수 있으므로 쿨다운이 첫 슬롯만 보낸다.
- 수급 지표는 전 거래일 확정(기준일을 근거에). 장중 잠정 수급은 쓰지 않는다.
- 상·하한가·거래정지·VI 플래그(`status_flags` — `quotes.py`:51)는 근거에 적고, 정지면 그 종목 평가는 `halted`.

### 4.4 근거 메시지 (EVL — 템플릿)

```
[규칙] 반도체 과매도 반등 · 삼성전자 005930
10:20 장중(잠정) · 현재가 71,200 (−2.3%)
✔ RSI(14) 28.4 < 30
✔ SMA5 상향 돌파 SMA20 — 70,880 > 70,610 (전일 70,100 ≤ 70,300)
✔ 외국인 연속 순매수 3일 (10-12 확정)
모두(AND) · 쿨다운 24시간 · 규칙 3판
KIS 현재가 10:20 estimated · 일봉 KRX 10-12 ok · 상세 <로그인 주소>/#/8/005930
```

마감 묶음(`alert.rule_close`): 규칙별로 종목 줄(이름·코드·종가·핵심 값 하나) — 30종목 넘으면 '외 N' + 페이지 11 링크. 머리에 "16:00 기준 KIS 마감 값(잠정일 수 있음)"(ADR 0018 §2-5 원칙).

### 4.5 알림 후 성과 (BKT — 부록 A)

- 진입가 `P0` = 장중 신호의 슬롯 현재가 / 마감 신호의 그날 종가. 기간 h 의 `ret = close(D+h) / P0 − 1`(확정 일봉), `excess = ret − 지수 수익(같은 창)`, `mfe = max(high[D+1..D+h]) / P0 − 1`, `mae = min(low[…]) / P0 − 1`.
- 정지·상폐로 D+h 종가가 없으면 `reason` 을 적고 통계에서 뺀다(뺀 수를 같이 보인다). 분할 의심이 창 안이면 `split_suspect` 로 뺀다.
- 통계: 규칙(판 합침/판별)·기간별 n·승률(ret > 0)·평균·중앙·25/75%·평균 초과·MFE/MAE 중앙. **n < 20 이면 '표본 적음'** 흐림, 수익을 약속하는 문구 없음("과거 신호의 사후 수익 — 비용·체결 미반영").

### 4.6 백테스트 (BKT)

- 모드 `close`: 시점 d 마다 `evaluate(rule, frame, d)` → 신호 → 거래(d+1 시가 체결, 보유 N일 뒤 종가 청산 또는 손절·익절 — 같은 날 둘 다 닿으면 손절 먼저(보수적)), 비용 반영. 모드 `intraday_replay`: 저장된 슬롯마다 부분 봉으로 같은 함수, 체결은 슬롯 가격.
- 우니버스: 기간의 각 날 `prv_market.universe`(PIT) — 상장폐지 종목도 원장에 있으면 포함(legacy 의 생존편향 경고를 줄인다). 결과에 '표본 = 원장 보유 구간' 문구.
- 출력: 신호 목록·거래 목록·요약(`kbj.engines.backtest.stats`)·누적 곡선·신호 성과(§4.5 와 **같은 함수**) — 페이지 12 가 '백테스트 기대' 와 '실제 알림 성과' 를 같은 칸으로 나란히 보인다.

### 4.7 저널·포트폴리오 (JNL)

- 포지션: 매수는 평균 단가 갱신, 매도는 실현 손익 `(가격 − 평균단가) × 수량 − 수수료 − 세금`, 보유 수량 음수 금지(422). 평가 손익은 최신 원장 종가(quality·기준일 표시). 월별 수익률 = 실현 + 평가 변화 ÷ 월초 투입 원가 [확인 필요 — 현금 원장 없음].
- 분석 일지 prefill: P4 `prv_val`·시나리오 적정가·컨센서스 읽기(저장소) → 칸 + 출처 칸(`kbj` 계산·컨센서스·수기). 밸류체인 칸은 사전 통합(사용자 별도) 전까지 빈칸.
- 상관: 관심종목 또는 보유의 일간 수익률 피어슨 60·120일, 50종목 상한.

---

## 5. API (로그인 — DB·Redis 만 읽는다, 계약 ⑩)

| 메서드·경로 | 응답·동작 | 묶음 |
|---|---|---|
| `GET /api/rules` · `GET /api/rules/{id}` | 규칙 목록(현재 판·켜짐·최근 30일 신호 수·마지막 신호) / 판 이력 | EVL |
| `POST /api/rules` · `PUT /api/rules/{id}` · `POST /api/rules/{id}/{enable,disable,archive}` | 만들기·새 판·상태 — CSRF, 검증 오류 422(위치·사유) | EVL |
| `POST /api/rules/validate` | 검증 + 오늘 원장으로 시험 평가(저장 없음) | EVL |
| `GET /api/rules/examples` | 예시 10개 | EVL |
| `GET /api/rules/signals?from&to&rule&code&mode` | 신호 이력(상태·근거·발송 결과 — `ops.notify_log` 결합) | EVL |
| `GET /api/rules/stats?rule&version&horizon` | 성과 통계·분포 | BKT |
| `POST /api/backtest` | 즉석 백테스트(상한·캐시 — D-P8-17) | BKT |
| `GET /api/backtest/runs?rule` · `GET /api/backtest/match?from&to` | 야간 실행 요약 / 일치 감시 | BKT |
| `GET·POST /api/journal/analysis` · `GET·PATCH·DELETE /api/journal/analysis/{id}` · `GET /api/journal/analysis/prefill/{code}` · `GET /api/journal/analysis/{id}/markdown` | 분석 일지 | JNL |
| `GET·POST /api/journal/trades` · `DELETE /api/journal/trades/{id}` | 매매 기록 | JNL |
| `GET /api/portfolio` · `GET /api/portfolio/correlation?set=watchlist\|holdings&n=60` · `GET /api/journal/picks` | 포지션·손익 / 상관 / 추천 성과(이관분) | JNL |
| `PUT·DELETE /api/watchlist/{list}/{code}` · `POST /api/watchlist/{list}/order` | 관심종목 쓰기(P4 읽기 라우트 옆) | JNL |

모든 응답은 `Envelope[T]`(source·as_of·quality·notes). 데이터 없음은 404 `no_data`(작업·예정 시각).

## 6. 프런트 (MD6 13 페이지 틀·기존 패널 양식 그대로)

| 페이지 | 패널 | 내용 | 묶음 |
|---|---|---|---|
| 11 알림·규칙 | ① 규칙 | 표: 이름·모드·켜짐 토글·쿨다운·최근 신호·30일 신호 수, 보관 | EVL |
| | ② 편집기 | 조건 행(지표 선택 → 파라미터·칸·연산자·값/참조), 묶음 AND/OR, 우니버스·모드·쿨다운, JSON 보기(같은 스키마 검사), '예시 불러오기', '오늘 데이터로 시험' 결과(조건별 ✔/✖ 값) | EVL |
| | ③ 발송 이력 | 시각·규칙·종목·모드·상태(보냄·쿨다운·꺼짐·건너뜀)·근거 펼치기 | EVL |
| | ④ 알림 후 성과 | 규칙 × 기간 표(n·승률·평균·중앙·초과), 분포 막대, n<20 흐림 | BKT |
| 12 백테스트·저널 | ① 백테스트 | 규칙 선택·기간·보유·손절/익절 → 요약·누적 선·거래 표 | BKT |
| | ② 백테스트 대 실제 | 같은 규칙의 백테스트 성과 대 실제 알림 성과, 일치 감시(최근 20일 — 차이·사유) | BKT |
| | ③ 분석 일지 | 목록·작성(prefill)·마크다운 내보내기 | JNL |
| | ④ 포트폴리오·수익률 저널 | 매매 입력, 포지션·실현/평가 손익·월별 막대, 알림 연결 표시 | JNL |
| | ⑤ 상관관계 | `heatmap` — 관심종목/보유 토글·60/120일 | JNL |
| | ⑥ 추천 성과 | SD 이관분이 있으면 D+1~D+7 표, 없으면 숨김 | JNL |
| 8 종목 상세 | 헤더 별 토글 | 관심종목 추가·삭제 | JNL |

상태·오류 표시는 P3 §6.7 + P4 규칙 그대로(패널 격리, `quality` 칩, 빈/실측 전/휴장). 공개 빌드는 11·12 자물쇠만.

## 7. 공개 정적 사이트 내보내기 영향

없음. `kbj/services/public_export` 는 P8 표를 읽지 않는다(계약 ⑧·통합 시험). WRAP 이 공개 안전 검사·번들 검사·`build_public_site.sh --dry-run` 으로 P8 흔적 0 을 확인한다.

## 8. 시험·검증

| 층 | 무엇 | 위치 |
|---|---|---|
| 지표 | 손계산 표·board 원본 값 일치·속성 | `tests/unit/core/test_indicators.py`, `tests/property/test_indicator_props.py` |
| DSL·평가 | 스키마·의미 검사·드모르간·미래참조 없음·`None` 은 거짓 | `tests/unit/engines/rules/*`, `tests/property/test_rules_eval_props.py` |
| 실시간 | 슬롯 처리(정지·상한가·잘림·한 종목 실패 격리)·쿨다운·발송 모양·16:00 묶음 | `tests/unit/services/test_rules_jobs.py` |
| 성과·백테스트 | 기간 경계·정지·지수 초과·d+1 체결·손절 우선·비용·캐시 | `tests/unit/engines/rules/test_{backtest,outcome,stats,match}.py`, `tests/property/test_backtest_props.py` |
| 스윙 | 원본 62 승격 + 지표 교체 골든 | `tests/unit/engines/swing/**` |
| 저널 | 평균 단가·실현 손익·음수 보유 거절·상관 결측·prefill 출처·SD 이관(합성 sqlite) | `tests/unit/engines/journal/**`, `tests/unit/store/test_legacy_import_p8.py` |
| API | 모양·CSRF·422 에 입력값 없음·외부 호출 없음(`test_no_external.py` 확장) | `tests/unit/api/test_routes_{rules,backtest,journal}.py` |
| 완료 | 일치 10규칙·여러 날 성과 누적·주문 0 | `tests/acceptance/test_rules_parity.py`, `tests/sim/test_rules_days.py`, `tests/unit/test_no_orders.py` |
| 웹 | 패널·편집기 상호작용·자물쇠 | `web/test/pages/p11_rules.test.ts`·`p12_*.test.ts`, `web/test/acceptance/p11_p12_pages.test.ts` |

실데이터 검증(키를 받은 뒤): FHKST01010100 장중 칸 이름·단위(probe P7 다음 번호), 관심종목 5종목으로 하루 장중 평가를 돌려 건수만 출력(`python -m kbj.services.engine.rules verify --watchlist main` — 값·종목코드 없음).

## 9. 위험·미정

| # | 위험 | 영향 | 대응 |
|---|---|---|---|
| R1 | 비수정 가격 — 액면분할·병합이 가짜 크로스·RSI 급변 | 오알림 | `split_guard`·`split_check` 로 건너뜀(D-P8-8), 근거에 사유. 수정주가 원장은 별도 과제 |
| R2 | KIS 마감(16:00) 값과 KRX 확정(D+1) 값이 다름 | 실시간 신호 ≠ 다음 날 백테스트 | 입력 지문으로 `data_revised` 분류(D-P8-14), 완료 시험은 같은 원장 상태로 비교 |
| R3 | EMA 계열 시작점 의존 | 임계 근처 신호 불일치 | anchor 고정(D-P8-7). backfill 로 anchor 이전이 생겨도 anchor 가 같으면 값 같음 |
| R4 | 장중 부분 봉 의미 오해(누적 거래량·장중 크로스) | 사용자 혼동 | 근거에 '장중(잠정)'·기준 시각, 편집기 도움말, metrics 정의 |
| R5 | 같은 슬롯의 KIS 버킷 경합(`flows.intraday`·P7 폴링) | 슬롯 지연·`estimated` | P2·상한 60·필요하면 +1분 엇갈림(§3.4), sim 이 슬롯 지연을 단언 |
| R6 | 백테스트 미래참조·생존편향 | 과대평가 | 속성 시험(미래 칸 변경 불변), PIT 우니버스, 결과 문구 |
| R7 | 처음 여는 개인 쓰기 API | 보안 | `require_csrf`·본문 상한·검증·로그에 본문 없음, 쓰기 모듈 하나, 시험 |
| R8 | 공개 번들 예산 거의 소진(61,021/61,440 B) | 공개 빌드 실패 | 11·12 본문은 로그인 빌드만, `check-bundle.mjs` 금지 문자열 |
| R9 | ADR 0018 '셋' 을 넷으로 | 기존 시험 실패 | FND 가 등록부 시험을 같이 고치고 ADR A·0018 보충 |
| R10 | P6·P7 이 공유 파일(번호·glob·계약·harness·예산)을 먼저 바꿈 | 번호 충돌 | D-P8-1 — FND 시작 때 재확인, 이 문서 번호 갱신 |
| R11 | 백테스트 속도(순수 Python, anchor 부터 전체 계산) | 즉석 API 지연 | 종목별 배열 한 번 계산 후 칸 읽기, 상한·캐시, 야간 작업 |
| R12 | 스윙 엔진 지표 교체로 원본 값이 바뀜 | legacy 시험·골든 실패 | 정본 정의를 board 쪽으로 정했다(D-P8-3) — 바뀌면 정본이 board 와 다르다는 뜻이므로 정본을 고친다 |
| R13 | board shim 순환 import | legacy 실패 | P3·P5 shim 방식(계약 ②) |
| R14 | 표본 작은 성과 통계가 과신을 부름 | 판단 오류 | n<20 흐림·약속 문구 금지·판별 분리 |
| R15 | 백테스트 이력 깊이 부족(`market.backfill` 은 수동) | 3년 창 못 채움 | 있는 구간만 + 경고, §10 Q10 |

### 9.1 앞 단계에서 기대는 것 — 실제 코드 확인(2026-10-13, HEAD `138da8f` + IDX 작업 트리)

| 기대 | 확인 |
|---|---|
| `KIS:watch_quotes_intraday`·파서 | `datasets.py`:229 / `quotes.py:parse_quote`:73 — 있음 |
| 원장 읽기 `MarketRepo.series`(우선순위 한 행) | `kbj/store/repos/market.py`:75 — 있음 |
| 연속 순매수 정본 | `kbj/engines/flows/streak.py:streak`:20 — 있음 |
| 분할 의심 | `kbj/engines/board/newhigh.py:split_guard`:193 — 있음 |
| notifier `notify(kind, subject, …)`·쿨다운 정책 | `kbj/services/notifier/client.py`:189, `policy.py`:15 — 있음 |
| CSRF 의존 | `kbj/services/api/deps.py:require_csrf`:124 — 있음 |
| 관심종목 표·쓰기 모듈 | `0013_journal_watchlist.sql`, `kbj/services/watchlist/__init__.py` — 있음(쓰기 API 없음) |
| P4 밸류에이션·시나리오 읽기(prefill) | P4 저장소 — 있음 [추정: 함수 이름은 FND 가 확인] |
| P7 웹소켓·GEX 폴링의 슬롯 시각 | **P7 설계 전** — FND 가 확인 |

### 9.2 이 설계로 남길 ADR (초안 → WRAP 이 확정, 번호는 P7 다음)

| ADR | 내용 | 초안 |
|---|---|---|
| A | 규칙 알림: DSL v1·평가 시점 둘·쿨다운 두 겹·근거 템플릿·장중 시세 저장, **ADR 0018 보충(16:00 발송 넷)** | EVL |
| B | 백테스트·성과·일치 감시: 같은 함수·anchor·d+1 체결·`data_revised`/`logic` | BKT |
| C | board 스윙 엔진·US btsearch 승격, 지표 정본 교체(ADR 0023 §2-8 해소) | SWG |
| D | 개인 쓰기 API·저널·포트폴리오(평균 단가·주문/잔고 연동 없음) | JNL |

## 10. 사용자만 할 수 있는 것

| # | 질문·작업 | 기본값(답이 없을 때) |
|---|---|---|
| Q1 | 마감 규칙 알림을 16:00 **한 통 묶음**으로 받을지, 규칙마다 따로(16:00 여러 통) 받을지 | 한 통 묶음(0건이면 없음) |
| Q2 | 장중 평가 대상 상한(KIS 호출 = 상한 × 40/일) | 60종목(관심 main ∪ 보유 ∪ 규칙 코드) |
| Q3 | 규칙 기본 쿨다운 | 24시간(하한 60분) |
| Q4 | 성과 기간과 비교 지수 | 1·3·5·10·20 거래일, 시장별 코스피·코스닥 |
| Q5 | 백테스트 비용(수수료·거래세·슬리피지) | 수수료 0.015% 양쪽, 매도 거래세 0.20% [확인 필요], 슬리피지 0 |
| Q6 | 포트폴리오를 KIS 잔고와 연동할지(계좌번호·잔고 조회 — 주문은 아님) | 연동 안 함 — 직접 입력·CSV, 평균 단가 |
| Q7 | SD 개인 데이터(알림 규칙·분석 일지·매매 저널·추천 이력·`server_portfolio.json`)를 VM 에 올려 이관할지 | 올리면 이관(레포에는 넣지 않음), `alert_history` 는 이관 안 함 |
| Q8 | board 스윙 시그널을 매일 16:00 텔레그램·페이지 12 패널로 낼지 | P8 은 수동 CLI 만(지금 legacy 와 같음) |
| Q9 | 페이지 13 운영을 P8 에 넣을지(`pages.ts` 가 P8 로 표시) | 넣지 않음 — P9 또는 별도 단계, 자리표시 그대로 |
| Q10 | 백테스트용 일봉 백필 깊이(`market.backfill` 수동 실행 — KRX 예산) | 3년 + anchor(2020-01-02) 이후 |
| Q11 | SD 발굴 신규 진입 알림(#14)을 폐지할지 | 폐지(`retired`) — 같은 뜻은 사용자 규칙으로 |
| Q12 | 장중 수급 조건에 장중 잠정 수급을 쓸지 | 쓰지 않음 — 전 거래일 확정 |
| — | KIS 키로 FHKST01010100 장중 칸 실측(probe), VM 에 SD 파일 올리기, `market.backfill` 실행, 운영 VM 반영 | — |

---

## 부록 A. `docs/metrics.md` 에 넣을 지표 정의 (FND 가 P7 다음 절 번호로 옮긴다)

공통: 입력 = 원장 우선순위로 고른 일봉(KRW, 비수정), 결측·정지 칸은 `None`, 워밍업 칸은 `None`(0 으로 채우지 않음), 계산은 float64 순서 고정(왼쪽→오른쪽 누적), anchor = `max(rules.anchor_date, 종목 첫 봉)`. 품질 = 입력 중 가장 나쁜 quality(장중 부분 봉은 `estimated`). 출처 = 입력 데이터셋.

| 지표 | 공식 | 예외·주 | 시험 |
|---|---|---|---|
| SMA(n) | 최근 n 칸 산술 평균 | 창 안 `None` 이 하나라도 있으면 `None` | 손계산·선형성 |
| EMA(n) | 시드 = 첫 n 칸 SMA(칸 n−1), 이후 `e += α(x − e)`, α = 2/(n+1) | board `ema_arr` 와 같음 | board 값 표 |
| RSI(n) | 와일더: 첫 n 변화의 평균 이득·손실 → `(g(n−1)+x)/n`, `100 − 100/(1+g/l)`, l = 0 이면 100 | 워밍업 `None`(SD 의 0.0 채움 폐지) | board `rsi_arr` 값, 0~100 |
| MACD(f,s,sig) | line = EMA(f) − EMA(s), signal = line 의 EMA(sig)(line 의 첫 유효 칸부터 시드), hist = line − signal | — | 손계산 |
| 볼린저(n,k) | mid = SMA(n), σ = 모표준편차(n), upper/lower = mid ± kσ, %B = (close − lower)/(upper − lower), width = (upper − lower)/mid | σ = 0 이면 %B `None` | board `pstdev`·`bb_width` 값 |
| 밴드폭 백분위 | width 의 직전 lookback 칸 중 순위 백분위 | lookback 미달 `None` | 손계산 |
| TR·ATR(n) | TR = max(h−l, |h−c₋₁|, |l−c₋₁|), ATR = TR 의 와일더 평균 | 첫 칸은 h−l | board `true_range` 값 |
| ADX(n) | +DM/−DM → 와일더 평활 → +DI/−DI = 100·DM/ATR, DX = 100·|+DI−−DI|/(+DI+−DI), ADX = DX 의 와일더 평균 | board `adx_arr` 와 같음 | board 값 표 |
| 거래량 배수(n) | 오늘 거래량 ÷ 직전 n 칸 평균(오늘 제외) | 평균 0 이면 `None`. 장중은 누적 거래량 | 손계산 |
| N일 최고·최저 | 직전 n 칸 high 최대·low 최소(오늘 제외) | — | 손계산 |
| 등락률 | close/close₋₁ − 1 (%) | 전일 정지면 직전 유효 종가 | 손계산 |
| 연속 순매수·N일 합 | metrics §2 정본(`kbj.engines.flows.streak`), 합은 원 단위 | 장중은 전 거래일 확정 | 기존 시험 |
| 고점 대비 하락 | 보유 시작일 이후 최고 high 대비 (close/peak − 1) | 포지션 없으면 `None` | 손계산 |
| 신호 성과(h) | §4.5 — ret·excess·MFE·MAE, 진입가 규칙 | 정지·상폐·분할 의심 제외(수 표시) | 손계산·sim 누적 |
| 일치 감시 | 같은 (규칙 판, 종목, 시점) 집합 비교, 차이 사유 = 입력 지문 같음 → `logic` / 다름 → `data_revised` | `logic` ≥ 1 이면 health 오류 | acceptance ④ |
| 백테스트 체결 | 마감 신호 d → d+1 시가, 보유 N 거래일 종가 청산, 손절·익절은 장중 low/high 기준(같은 날 둘 다면 손절), 비용 = 수수료 양쪽 + 매도 거래세 + 슬리피지 | 정지일 체결 없음(다음 유효일) | 속성·손계산 |

---

## 사용자 결정 (2026-10-13 — §10 열린 질문 답, 이 절이 위 본문보다 우선)

- **장중 수급 조건은 라이브로 받는다**(Q12 기본값 '전일 확정치' 를 버린다). 장중 평가(`rules.intraday`)의 수급 조건은 그날 장중 값을 쓴다:
  종목은 KIS 가집계 `KIS:inst_foreign_intraday`(P3 — 10분 회차, quality=estimated), 시장은 F2 의 1분 누적(`prv_gex.investor_flow` — 쓰기 주인 `flows.market_minute`, ADR 0019).
  잠정치라 알림 근거 문구에 '장중 잠정' 을 붙이고, 마감 평가는 확정치로 다시 판정한다. 일치 감시(BKT)는 장중 잠정 → 확정 차이를 '데이터 수정' 으로 분류한다.
  가집계가 없는 종목·회차는 조건을 '판단 불가' 로 두고 그 규칙을 발송하지 않는다(지어내지 않음).
- **스윙 신호는 없앤다**: 묶음 **SWG 를 하지 않는다**. board 스윙 엔진(`board/engine/{signals,backtest,search,systems}`)·US btsearch 를 KBJ 로 옮기지 않고,
  `kbj/engines/swing/**`·`kbj/services/engine/swing.py`·스윙 CLI·`config/signals.yaml` 이동·관련 시험 승격도 만들지 않는다. legacy 의 해당 코드·시험은 P9 'legacy 비우기' 에서 정리한다.
  DSL 백테스트(BKT)는 그대로 — 지표는 `kbj.core.indicators` 하나. 웨이브 2 묶음은 EVL·BKT·JNL 셋.
- 나머지 열린 질문(마감 알림 16:00 한 통·장중 상한 60종목·쿨다운 24시간·성과 1/3/5/10/20일·비용 수수료 0.015%·매도세 0.20%[확인 필요]·KIS 잔고 동기화 안 함·SD 자료는 올라오면 이관·페이지 13 제외·3년 백필·SD 발굴 알림 폐지)은 기본값 그대로.
