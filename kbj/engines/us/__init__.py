"""미국장 신고가 엔진(로그인 산출 — Nasdaq·Stooq·Yahoo). docs/p5_design.md §1.6·§4.6·metrics §23.

승격 원본 `legacy/etf_traker/board/us/{engine,sectors,brief,build}.py`(본문 그대로 — 252봉, ADR 0001
Q10). 모듈:

- `engine` — 종목 판정(신고가·신저가·연속·첫 진입)과 집계(섹터·시총 구간·대형주·대장·요약)
- `board`  — 조립(②·③ 추이 포함) → us_summary 한 덩어리
- `brief`  — 문장·텔레그램 HTML(계산하지 않는다) → us_brief
- `sectors` — 스크리너 sector·industry → 자체 섹터(규칙 `config/knowledge/us/`)
- `config` — `config/board.yaml` 엔진 절 + `config/us.yaml`

ET `board/us/btsearch.py`(조합 탐색)는 옮기지 않았다 — 국장 백테스트 엔진(`board/engine/{backtest,
search,signals,systems}` — P8)에 기대므로 그 엔진과 함께 P8 에 옮긴다(legacy 에 남음).

순수 계산이다: 네트워크·DB 를 모른다(설정·사전 파일만 읽는다).
"""

from __future__ import annotations

from typing import Final

__all__ = ["ENGINE_VERSION"]

# 산출(us_summary·us_brief)의 engine_version — 정의·문장 규칙을 바꾸면 올린다.
ENGINE_VERSION: Final = "us-0.1"
