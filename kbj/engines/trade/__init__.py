"""수출입 엔진(순수 — 계약 ⑨): 수출 속보(`flash` — FLASH)·탐색·급등·단가 대 물량·104품목(EXP)·
종목↔HS 매핑·겹쳐 보기(MAP).

docs/p6_design.md §4, metrics §25~§29. 입력은 행 자료형(`kbj.core.trade_rows`)과 설정 모델
(`kbj.config.trade`)뿐 — 어댑터·저장소·서비스를 모른다.
"""
