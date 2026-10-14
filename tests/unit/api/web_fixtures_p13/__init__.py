"""P9 페이지 13 '운영' 프런트 시험 픽스처 — `web/test/fixtures/api/ops_*.json`(docs/p9_design.md
§6.2·§7.3, 묶음 OPS).

합성 운영 기록(`tests/unit/ops/ops_world.py` — 이름·상태·시각·개수뿐)을 메모리 원천으로 앱에 넣고
`/api/ops/*` 응답을 그대로 낸다. `uv run python -m tests.unit.api.web_fixtures_p13.ops` 로 다시
쓰고, `tests/unit/api/test_routes_ops.py` 가 최신인지 본다(기존 `make_web_fixtures`·`api_world` 는
고치지 않는다 — DEMO 와 겹침 방지).
"""
