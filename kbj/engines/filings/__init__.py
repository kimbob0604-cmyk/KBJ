"""공시 엔진 — 중요도·잠정실적 분류·오버행·내부자·실적 일정(docs/p4_design.md §1.4·§4.1~§4.4).

D-P4-3·4·14·18, docs/metrics.md §12~§14, ADR 0005(알림)·0013(공시 수집).

- `importance`: 공시 제목 키워드 점수(공개)·관심종목·시총 가산(로그인 — 읽을 때 계산).
  SD `server.py:_DISCLOSURE_SCORE_TABLE`:9920·`score_disclosure`:9974·`_cap_bonus_for_code`:9946.
- `earnings`: 잠정실적 YoY 분류(공개 규칙)·컨센서스 서프라이즈 분류·신뢰도 3관문(로그인 — 분기
  컨센서스가 있을 때만)·알림 본문 템플릿(LLM 없음)·알림 후 성과 거래일. SD
  `earnings_signal_classifier.py`(THRESHOLDS:166·classify_signal:172·sanity_check_consensus:34),
  `earnings_alert_writer.build_fallback`:299,
  `earnings_telegram_sender.backfill_alert_performance`:217.
- `overhang`: CB·BW·EB·유상증자의 t 시점 상태·요약·추정 보호예수(공개 — 금액·ITM 은 로그인 도구).
  SD `overhang_parser.get_active_overhang`:171·`collect_overhang_for_stock`:266 를 대신한다.
- `insider`: 임원·주요주주 소유보고의 정정 대체와 30·90·180일 창(공개 — 금액은 로그인 도구).
- `schedule`: 실적 일정(결산실적공시예고 예정일·법정 제출기한). SD `_fetch_kr_earnings`:11047 대신.

엔진은 순수 계산이다(import-linter ⑨ — `kbj.data`·`kbj.services`·`kbj.store` 를 모른다. ⑫ —
`kbj.engines.{consensus,valuation}`·`kbj.data.private` 도 모른다). 설정(`kbj.config.fin`)은 인자로
받는다. 금액은 원 단위 정수, 비율은 % 실수(반올림은 화면에서만).
"""
