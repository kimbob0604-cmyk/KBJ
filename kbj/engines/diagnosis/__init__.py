"""진단 엔진(순수 — 계약 ⑨): 페이지 3 공공지표 진단·시장 진단·국장 공포탐욕·종합 판정·이벤트 표시.

docs/p7_design.md §1.5·부록 A, docs/metrics.md §D0~§D9. 입력은 관측 점(`bands.Obs`)·행 자료형
(`kbj.core.p7_rows`)·설정 모델(`kbj.config.diag`)뿐 — 어댑터·저장소·서비스를 모른다.

- `bands` 백분위·구간·상태 점수(§D0·§D1), `indicators` 지표 행과 파생(변화율·누적 — §D1~§D6),
  `public` 매크로 위험 게이지(§D4 — 공개 입력만, 계약 가), `fear_greed`(§D7), `verdict`(§D8),
  `events`(§D9).
- 기존 정본 공식(반도체 쏠림 §6.4·시장폭 §6.2·신용스프레드 §16·한국 대 글로벌 §6.5·수출 속보 §25)은
  다시 정의하지 않는다 — 부르는 쪽(`kbj.services.engine.diag*`)이 정본 함수로 값을 만들어 넘긴다.
"""
