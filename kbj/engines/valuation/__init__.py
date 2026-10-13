"""밸류에이션 엔진 — 시점 일관(PIT) 배수·5년 밴드·시나리오 적정가(구 TAM). 로그인 등급.

docs/p4_design.md §1.7·§4.8·§4.9·D-P4-11·12, docs/metrics.md §11·§15, ADR 0016.

- `multiples`: 회사 시가총액(보통 + 우선)·trailing(PER·PBR·PSR·EV/EBITDA)·forward·적용 불가 사유
  순위 (`NA_PRIORITY`)·저장 행 → 칸(`cells_of` — API 가 같이 쓴다)
- `pit`: PIT 재무(`PitBook` — 접수일 ≤ t 인 분기만, TTM 창은 재무 엔진 `ttm_window`)·NTM
  (`ConsensusBook` — 컨센서스 엔진 `ntm`)·하루 배수 행(`value_day`·`daily_series`)
- `percentile`: 선형 보간 백분위(numpy 'linear' 와 같다)·경험적 순위
- `band`: 최근 1,240거래일 유효 값의 p10~p90·현재 백분위, 짧으면 사유(`short_history`
  등)·밴드가(화면)
- `scenario`: Bear·Base·Bull 적정가(EPS × PER 밴드 → PBR 폴백 → INSUFFICIENT)
- `classes`: 종류주식 → 회사(`prv_market.share_class` — 코드·이름 규칙 둘 다, R13)

순수 계산이다(계약 ⑨ — `kbj.data`·`kbj.services`·`kbj.store` 를 모른다). 튜닝값(`config/fin.yaml
valuation`·`scenario`)은 처리기(`kbj.services.engine.valuation`)가 읽어 인자로 넘긴다. 다른 엔진은
정본을 가져다 쓴다(두 벌 금지): 재무
`kbj.engines.financials.period.ttm_window`·`normalize.rcept_date`, 컨센서스
`kbj.engines.consensus.forward.ntm`·`universe.consensus_na`, 종류 판정 `kbj.engines.board.kinds`.
"""
