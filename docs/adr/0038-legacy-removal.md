# ADR 0038 — legacy 비우기: ET·SD 삭제, 다리(브리지·notifier shim) 제거, 직접 호출 검사 목표 0 (2026-10-14)

상태: **확정**(2026-10-14 P9 WRAP — LEGACY 초안 + WRAP 의 `legacy/gexlab` 삭제, 끝 'WRAP 확정 메모').
근거: docs/p9_design.md §1·§7.2 LEGACY·§7.6·§11(Q1·Q9·Q10), D-P9-11, PLAN D8. 대체: ADR 0003(legacy 범위)·ADR 0007(legacy
논리 URL 브리지·기준선 정책) — 두 ADR 에 '대체됨 — 0038' 을 달았다(WRAP).

## 1. 맥락

- P1 은 세 프로젝트를 `legacy/` 로 옮겼고(ADR 0003), P2 는 legacy 가 KIS·KRX·DART 를 직접 부르지 않게 논리 URL
  브리지(`kbj.data.legacy_bridge`)와 notifier shim(`legacy_send*`·`legacy_kinds`)을 두었다(ADR 0007·p2 §5.9).
  P3~P8 이 영역별로 기능을 KBJ 로 옮기며 ET(`legacy/etf_traker`)·SD(`legacy/stock_dashboard`)는 실행되지 않는
  코드가 되었다 — 등록부의 SD·ET 작업은 전부 `absorbs`·`retired`, compose 에도 없다(설계서 §1.1).
- 남은 것은 시험 수만 지키는 옛 코드(ET 276 파일·SD 62 파일 — 파이썬 약 6.9만 줄)와, 그 코드를 위해 KBJ 가 들고
  있는 다리·예외(브리지·shim·`legacy_kinds`·`legacy.other`·기준선 34줄·`check_canonical` 허용 목록)다.

## 2. 결정

1. **ET·SD 를 레포에서 지운다**(사용자 결정 §11 Q1·Q9·Q10 — 페이지 9 테마 코드·대체 없는 옛 기능 포함). 지우기 직전
   상태는 **커밋 `f6aeebd`**(legacy 를 지우기 직전 — 태그 `legacy-final` 은 사용자가 GitHub 에서 달 수 있다. 이
   세션에서는 태그 푸시가 막혔다). 원본 레포 셋도 그대로 남는다. 보관 브랜치는 만들지 않는다.
2. 이식 기록은 남긴다: `MIGRATION.md` 넷을 `docs/migration/{etf_traker,etf_traker_board,etf_traker_dart_report,
   stock_dashboard}.md` 로 `git mv`(머리말에 원래 위치·지운 커밋).
3. **다리를 같이 지운다**: `kbj/data/legacy_bridge.py`(+ `config/limits.yaml` 역할 `legacy_bridge`), notifier
   `legacy_send*`·`legacy_kind` 프레임 찾기·`origin`(legacy 부른 곳) 중복 키 조각, `config/notify.yaml`
   `legacy_kinds`·`legacy_default_kind`·종류 `legacy.other`, import-linter ⑯ 금지 목록의 `kbj.data.legacy_bridge`,
   `check_canonical` 의 legacy 허용 목록 중 ET·SD shim 만 쓰던 줄. 모든 발송은 kbj 작업이 kind 를 넘기는
   `notify*` 하나다(U1·U2 그대로).
4. **직접 호출 검사**: 기준선에서 ET·SD 줄(34)을 지우고 telegram·ecos·datago·kosis·naver·etf_issuers·ta_indicators 를
   목표 0 그룹으로 바꾼다. `krx_scrape` 만 `pyproject.toml` legacy 의존성 그룹 한 줄 때문에 기준선으로 남는다 —
   WRAP 이 legacy 그룹을 지우며 목표 0(기준선은 머리말만).
5. 시험: legacy 파일을 읽던 kbj 시험은 **조용한 skip 이 되지 않게** 처리한다 — 원본 값을 시험 안 상수로 동결
   (ET board DDL·settings 절), kbj 쪽 단언으로 바꿈(board build shim), 소스 대조만 하던 항목은 사유와 함께 삭제
   (SD 휴장 사본 대조). shim·브리지 전용 시험은 폐지, shim 으로 공통 경로를 보던 시험은 `notify*` 로 옮긴다.
   하루 운영 시뮬레이션의 'legacy' 소비자(브리지)는 같은 자리의 KBJ 읽기 프로세스 'reader' 로 바꿔 '발급자는 auth
   하나'·거절 신고 시험을 그대로 둔다. 보드 골든 생성기는 '재생성 불가' 로 표시한다(골든 값·비교 시험은 그대로).
6. D-P9-11: 진단 `bands.pct_rank` 는 정본 `kbj.core.indicators.pct_rank` 호출 + Decimal 양자화로 하나가 된다(창
   ≤ 100,001 에서 옛 Decimal 계산과 같은 값·표현 — 시험으로 고정).

## 3. 결과

- 레포의 legacy 는 `legacy/gexlab` 하나(GXI 이식 뒤 WRAP 이 지운다). CI legacy 매트릭스는 `[gexlab]`, `pytest -m
  legacy` 시험 0.
- 사라지는 기능(대체 없음 — 원본 레포·커밋 f6aeebd 에 남음): 스윙 신호·백테스트·조합 탐색, 구루 X 다이제스트, flowlab
  이벤트 스터디, SD 발굴·추천 생성기·뉴스·옵션·LLM 에이전트, ET 뉴스 수집, 테마 강도·밸류체인(페이지 9 — 사용자 별도).
- 보드 골든을 다시 만들려면 커밋 f6aeebd 를 `git worktree` 로 꺼내 그 안에서 `make_golden.py` 를 돌린다.

## WRAP 확정 메모 (2026-10-14)

1. **`legacy/gexlab` 도 지웠다** — GXI(ADR 0036)가 수집 경로를 옮겼고 GXI 가 '못 끝냄' 을 적지 않았다(§11 R1 대안은 쓰지
   않음). `MIGRATION.md` 는 `docs/migration/gexlab.md` 로 `git mv`. `git ls-files legacy` = 0, `legacy/` 디렉터리 없음.
2. `pyproject.toml`: `legacy` 의존성 그룹(스크랩·flask·pre-commit 등 19줄) 삭제 → `uv lock`(패키지 122 → 73, 남는 패키지의
   버전 변화 0 — pandas 2.3.3·numpy 2.5.3 그대로), ruff `extend-exclude`·pyright `exclude` 의 `legacy`, pytest 마커
   `legacy`, import-linter 계약 ②(대상 모듈 없음 — 번호는 다시 매기지 않는다, kept 19 → 18) 삭제.
3. `check_canonical`: `krx_scrape` 도 목표 0 — **모든 그룹이 목표 0**, `canonical_baseline.txt` 는 머리말만(기준선 장치는
   코드에 남는다 — 그 규칙 시험은 그룹을 시험 안에서만 기준선으로 바꿔 본다).
4. CI: `legacy`·`gexlab-integration` 잡과 `scripts/test_legacy.sh` 삭제, `test (kbj)` 의 `and not legacy` 삭제, canonical
   한 단계, `public-safety` → `public-outputs`, 새 잡 `demo (compose)`. `scripts/ci_junit_annotations.py` 는
   `kbj-integration` 잡의 실패 주석으로 옮겨 살렸다.
5. `.dockerignore`·`.gitignore` 의 legacy 줄 삭제(시험 `test_store_layout`·`test_gitignore` 갱신 — legacy 경로 사례 17개
   삭제, 하위 폴더 `.env` 사례 1개 추가). `test_store_layout` 의 GX 표·열 대조는 GX 마이그레이션 001~004 에서 읽던 값을
   시험 안 상수로 옮겼다(ET board DDL 과 같은 방식).
6. 사라진 시험 수(조용한 skip 0 — 전부 사유와 함께): GX 단위 277·통합 39 → KBJ 통합 9 로 옮김, 나머지 폐지(사유
   `docs/migration/gexlab.md` 'P9 WRAP'). kbj 쪽 −2(계약 ② 사례 1·canonical 기준선 그룹 시험 1 → 모든 그룹 목표 0 시험 +
   실제 기준선 빈 파일 시험으로 바꿈).
7. 원본: **커밋 `f6aeebd`**(legacy 를 지우기 직전 — 태그 `legacy-final` 은 사용자가 GitHub 에서 달 수 있다)와 원본 레포
   셋(GEXLAB·ETF-Traker·stock-dashboard).
