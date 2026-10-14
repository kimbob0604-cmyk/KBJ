"""규칙 알림 엔진(P8) — 순수 계산. DSL(`dsl`)·프레임(`frame`)·평가(`evaluate`)·쿨다운(`cooldown`)은
실시간 평가(`rules.intraday`·`rules.close`)와 백테스트가 **같은 함수**를 부른다(D-P8-6).

근거: docs/p8_design.md §4, docs/metrics.md §30. 계약 ⑨ — 저장소·어댑터·서비스를 모른다.
"""
