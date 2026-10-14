"""GEX 운영 점검 도구 — GX `scripts/nogap_report.py`·`scripts/shadow_report.py` 이식
(P9 GXI, ADR 0036).

    python -m kbj.services.engine.gex_report nogap  [--end D] [--days 20] [--need 3] [--recompute]
    python -m kbj.services.engine.gex_report shadow [--end D] [--days 5] [--flag shadow]

`nogap`: 'N거래일 연속 무결측' 판정(probe #61), `shadow`: '1주 섀도 운영 무오류' 판정(probe #62).
종료 코드 0 충족 · 1 미충족 · 2 설정·DB 오류. 읽기만 한다(DB 에 쓰지 않는다).
"""
