# ADR 0003 — legacy/ 의 범위: 린트·타입검사 제외, 프로젝트별 시험 루트 분리 (2026-10-06)

상태: 확정(P1). 근거: `docs/PLAN.md` §2(교살자 패턴)·§8 P1, `docs/conflict_map.md` §3.1·§3.4, ADR 0001 U3.
구현: `pyproject.toml`(`[tool.ruff]`·`[tool.pyright]`·`[tool.pytest.ini_options]`·`[tool.importlinter]`), `.gitignore`.

## 1. 맥락

- P1 은 세 프로젝트(stock-dashboard·ETF-Traker·GEXLAB)를 `legacy/<프로젝트>/` 로 **옮기기만** 하고 기존 시험을 그대로 통과시킨다. 고치는 것은 경로·import 뿐이다(예외: U3 합성 데이터 교체·개인정보 제거, 각 `legacy/<프로젝트>/MIGRATION.md` 에 기록).
- 세 프로젝트는 import 루트가 다르다: GEXLAB 은 `pythonpath=["."]`, ETF-Traker 는 레포 루트에서 `board.*`·`monitor.*`·`flowlab`, stock-dashboard 는 평면 모듈. 한 프로세스에 여러 루트를 올리면 **같은 최상위 이름이 부딪힌다**(conflict_map §3.4): `db`(SD 패키지 대 GX 패키지 — 실제 충돌), `scripts`·`data`·`config`·`tests`(GX 패키지 대 다른 프로젝트 디렉터리), `report`·`verify`·`render` 등 평면 모듈.
- legacy 코드는 각자의 린트 규칙·타입 수준으로 쓰였다(SD·ET 는 린트 설정이 없다). 신규 `kbj/` 규칙(ruff 규칙 세트·pyright)을 걸면 "경로·import 만 고친다" 원칙을 지킬 수 없다.

## 2. 결정

1. **ruff·pyright 는 `kbj/`·`tests/`·`scripts/` 만 본다.** `legacy/` 는 `extend-exclude`·`exclude` 로 뺀다(`force-exclude = true` — 경로를 직접 넘겨도 제외). legacy 를 고치려고 린트를 맞추지 않는다.
2. **legacy 시험은 프로젝트마다 따로 돈다.** 루트 `pytest` 의 `testpaths` 는 `tests` 하나다. legacy 는 각 프로젝트 루트를 작업 디렉터리·`PYTHONPATH` 로 삼아 원래 명령 그대로 돌린다(conflict_map §3.2 표의 명령 — GX `pytest -m "not network"`, ET `python -m unittest discover …`, SD 검사 스크립트). CI 잡도 프로젝트별로 나눈다(conflict_map §3.5 단계 7).
3. **`kbj` 는 `legacy` 를 import 하지 않는다.** import-linter 계약 ②, 그리고 `tests/test_import_contracts.py` 가 `kbj/` 의 모든 import 를 "kbj·표준 라이브러리·선언한 런타임 의존성"으로 제한한다 — legacy 의 최상위 이름(`core`·`db`·`board` …)으로 우회해도 걸린다. 의존 방향은 legacy → kbj 만 허용한다(정본으로 갈아 끼울 때 legacy 가 kbj 를 부르게 바꾼다).
4. **의존성**: 루트 `pyproject.toml` 의 런타임 의존성은 신규 `kbj/` 에 필요한 것만이다. legacy 의존성은 통합 단계가 `legacy` 의존성 그룹으로 넣고 다시 lock 한다(P0 해석: pandas 2.3.3 — conflict_map §3.3).
5. **legacy 의 실행 산출물은 git 에 들어가지 않는다**: `.gitignore` 의 `legacy/**/state/`·`cache/`·`.cache/`·`out/`·`ci-out/`·`probe_out/`·`runs/`·`data_store/`. 루트 산출물 패턴은 `/data/`·`/state/` 처럼 **루트에 앵커**한다 — 앵커 없는 `data/` 는 코드 패키지 `kbj/data/`·`legacy/gexlab/data/` 까지 무시한다. `raw/` 는 루트만(`legacy/gexlab/tests/golden/raw/` 는 시험 골든).
6. **원본의 `.github/workflows` 는 루트 `.github` 에 두지 않는다**(공개 레포에서 실행되면 안 된다). 시험이 읽는 파일만 legacy 안의 원래 상대 위치에 둔다.
7. **삭제 시점**: 한 영역을 정본(`kbj/…`)으로 갈아 끼우면 그 영역의 legacy 코드와 시험을 지운다(PLAN §2·D8). 마지막 단계(P9)에서 `legacy/` 를 비우고, 그때 이 ADR 의 제외 설정과 `legacy` 의존성 그룹도 지운다.

## 3. 결과

- 신규 코드는 엄격하게(ruff 규칙 세트 GEXLAB 과 같음, `kbj/core` 는 pyright strict), legacy 는 원래대로 — 두 기준이 섞이지 않는다.
- 이름 충돌은 실행을 나눠서 피하므로 legacy 파일 이름·패키지 이름을 바꿀 필요가 없다(P1 원칙 유지).
- 대가: CI 잡이 프로젝트 수만큼 늘고, legacy 쪽 린트 품질은 정본으로 갈아 끼울 때까지 그대로다. `scripts/check_public_safety.py` 는 legacy 도 검사한다(제외 옵션은 P1 이식이 진행되는 동안만 쓴다).
