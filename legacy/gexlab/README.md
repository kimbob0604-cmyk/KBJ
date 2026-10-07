# GEXLAB

코스피200 옵션 GEX 실시간 트래커 · 백테스트 · 시스템 트레이딩 — KBJ 모노레포로 옮겨 온 원본 코드(legacy, KBJ `docs/PLAN.md` §2 교살자 패턴: 정본 모듈로 갈아 끼우면 지운다).

- 이식 기록: [`MIGRATION.md`](MIGRATION.md) — 시험 fixture 는 합성 데이터(`scripts/make_synthetic_fixtures.py`)이고 KIS·KRX 실측 응답은 들어 있지 않다

- 계획: [`docs/PLAN.md`](docs/PLAN.md) · 불변 규칙: [`CLAUDE.md`](CLAUDE.md)
- 현재 단계: **Phase 0 (준비)** — repo 골격, CI, probe 실측 → [`docs/probe_results.md`](docs/probe_results.md)
- 로컬 실행: [`docs/runbook.md`](docs/runbook.md)

데이터 출처: 한국투자증권 KIS Open API, 한국거래소 통계정보. 투자 판단 참고용.
