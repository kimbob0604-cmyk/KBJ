"""GEX 수집 경로 — GX `services/poller`(옵션 체인·선물 전광판·최종거래일 REST 수집) 이식(P9 GXI,
ADR 0036).

상시 서비스 `gex.poller`(`python -m kbj.services.gex_poller`). 쓰기는 `store.GexIngestStore`
(디스크 스풀),
분봉·KRX 파생·마스터·무결측은 등록부 작업(`kbj.services.collectors.gex_*`·`kis_master`)이다.
"""
