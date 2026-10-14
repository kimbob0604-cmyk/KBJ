# GEX 합성 fixture (로그인 등급 모양 — 실데이터 아님)

KIS 옵션 전광판·단건 응답은 로그인 등급이라 실측 원본과 그것으로 만든 fixture 를 레포에 넣지 않는다
(CLAUDE.md §2, DATA_TIERS §4). 이 폴더의 JSON 은 모두 **고정 시드 생성기가 만든 합성 데이터**다.

| 파일 | 내용 | 쓰는 곳 |
|---|---|---|
| `validation/chain_snapshot_synthetic_20260928_1427_small.json` | 작은 체인 스냅샷(전광판 4시리즈 일부 행·단건 보강 ATM±5) — 2026-09-28 14:27 | 코어 골든 입력(`kbj.services.engine.gex_golden` `CORE_FIXTURES`), `tests/golden/gex/test_chain_subset.py`(부분집합 규칙의 고정점) |
| `validation/chain_snapshot_synthetic_20260928_1452_small.json` | 같은 모양 — 14:52(야간 파생 입력 `NIGHT_DERIVED` 의 원본) | 같음 |

- 생성기: `tests/fixtures/synthetic/gex/make_synthetic_fixtures.py`(P9 GXI — GX `scripts/make_synthetic_fixtures.py`
  를 옮김, 시드 `kbj-p1-gexlab-synthetic-v1`·값 그대로) — 전체 합성 스냅샷을 만든 뒤
  `kbj.engines.gex.golden.chain_subset` 규칙으로 자른다. KIS 출력은 `tests/fixtures/synthetic/kis/`, KRX 는
  `tests/fixtures/synthetic/krx/`, 이 폴더는 `validation/`. 다시 만든 값이 지금 파일과 같은지:
  `uv run python tests/fixtures/synthetic/gex/make_synthetic_fixtures.py`(기본 — 비교만, 쓰지 않는다).
  `tests/unit/kis/test_kis_master.py`·`tests/unit/data/private/test_private_synthetic_fixtures.py` 가 생성기 출력과 대조한다.
  GX 사본(`legacy/gexlab/scripts/`)은 P9 WRAP 에 legacy/gexlab 과 함께 지웠다(커밋 f6aeebd 에 남는다).
- 이 폴더의 파일은 옛 GX 사본(`legacy/gexlab/tests/fixtures/validation/`)과 바이트까지 같은 복사본이었다
  (P7 묶음 FND-C, 2026-10-13). P9 WRAP 에 GX 사본을 지운 뒤로는 위 생성기 비교가 같은지 본다.
- 파일 sha256 은 코어 골든(`tests/golden/gex/core/chain_synthetic_20260928_small.json` 의 `inputs`)에 고정돼
  있다 — 바뀌면 `tests/golden/gex/test_core_golden.py` 가 실패한다.
- 이 두 파일은 `chain_subset` 의 고정점이라 머리 칸(`_source`)을 달지 않는다(달면 고정점이 깨진다) —
  합성 표시는 파일 이름(`synthetic`)과 이 README 다.
