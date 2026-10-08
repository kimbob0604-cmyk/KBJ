# ADR 0011 — 공개 정적 사이트: pub_* 내보내기·고아 브랜치 public-data·수동 Pages (2026-10-07)

상태: **확정**(2026-10-07 — 초안 P3 묶음 X, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모', `docs/p3_design.md` §9.3).
근거: `docs/p3_design.md` D-P3-2·15·16, §2·§6.6·§7, `docs/DATA_TIERS.md` §2·§3·§5, ADR 0002 §2.2.
구현: `kbj/services/public_export/`(`export`·`calendar_json`·`events_json`·`manifest`·`push`·`__main__`),
`.github/workflows/pages.yml`, `scripts/build_public_site.sh`, 시험 `tests/unit/public_export/`·`tests/test_workflows.py`.

## 1. 맥락

- 공개판은 서버가 없다(정적 GitHub Pages). 데이터는 VM 이 공개 등급만 JSON 으로 내보내 전달해야 한다(DATA_TIERS §3).
- 공개/로그인은 실행 중 검사가 아니라 구조로 나눈다. 공개 사이트에는 로그인 API 주소조차 넣지 않는다.
- 공개 레포라 Actions 는 무료지만, CI 규칙상 예약(schedule) 실행은 두지 않는다.
- P3 의 새 표는 전부 `prv_*`(KIS·KRX·운용사)라 공개로 낼 표가 아직 없다. 공개 띠의 세션·야간 카운트다운·금통위 D-n 은
  코드 계산(거래 캘린더)과 공개 설정(`config/calendar_events.yaml`)이다.

## 2. 결정

1. **내보내기는 `kbj.services.public_export` 하나**(등록부 `public.export`, 05:30, always). 산출은 `KBJ_DATA_DIR/public/`:
   `calendar.json`(오늘부터 60일 — 거래일·휴장·지연 개장·월물/위클리 만기·야간 세션·세션 경계), `events.json`(금통위),
   `manifest.json`(`{schema_version, generated_at, files:[{name, source, as_of, quality, sha256}]}`).
   파일마다 봉투(§5.2 와 같은 `source`·`as_of`·`quality`·`notes`·`generated_at`·`data`).
2. **DB 는 `kbj_public_export` 구성원 로그인 역할로만**(`KBJ_PUBLIC_EXPORT_DATABASE_URL`, 읽기 전용). 앱 DSN 으로 대신하지
   않는다 — DSN 이 없으면 작업은 실패한다. 연결은 `transaction_read_only = on` 과 역할 구성원 여부를 확인하고, 아니면 실패.
   P3 은 읽는 공개 표가 없어 연결 확인만 한다. 공개 표가 생기면(P5~) 같은 모듈에 파일을 더한다.
3. **섞이지 않게 하는 겹**: DB 권한(0001), import 계약 ⑧(로그인 등급 코드 import 금지 — S 가 pyproject 에, 그 전에는
   `test_import_boundary.py` 가 import 폐포와 임시 설정의 import-linter 로 확인), SQL 정적 시험(`pub_` 스키마만), 내용 검사
   (어느 깊이의 `source` 에도 KIS·KRX·ETF_ISSUERS·YAHOO … 이름 0, 글자 전체에 `/api`·`/telegram`·`auth/login`·`KBJ_` 0 —
   쓰기 전에 검사, 걸리면 아무 파일도 바꾸지 않음), 합치기 검사(`web/scripts/merge-public-data.mjs` — manifest 이름 허용 목록),
   번들 검사(`check-bundle.mjs`). Python 검사와 JS 검사는 같은 규칙이고, 시험이 우리 산출물을 JS 검사에 넣어 확인한다.
4. **전달은 고아 브랜치 `public-data`**: 켜져 있으면 manifest 에 적힌 파일만 임시 저장소에 담아 **커밋 1개로 강제 푸시**
   (이력을 쌓지 않는다). 성공하면 `pages.yml` 을 workflow_dispatch 로 부른다. 둘 다 `KBJ_PUBLIC_PUSH_ENABLED`(기본 false).
5. **Pages 워크플로는 수동만**: `on: workflow_dispatch` 하나, 전체 권한 `contents: read`, `pages: write`·`id-token: write` 는
   배포 잡에만, 배포는 main 에서만, 비밀 없음. 빌드 순서는 `scripts/build_public_site.sh` 한 벌(웹 안에서
   `npm ci --ignore-scripts` → `build:public` → `merge-public-data.mjs` → `check-bundle.mjs dist-public`).
   `--dry-run` 은 웹 빌드 없이 데이터 경로 전부(python 검사 → merge → check-bundle)를 돈다.
6. **금통위 일정 품질**: 설정 날짜가 한국은행 원 일정과 대조되기 전에는 `estimated` + 사유(추정을 단정하지 않는다).
   다음 일정이 없으면 `stale`.

## 3. 사용자 승인 사항 (P3 에서 켜지 않는다)

| 항목 | 이름 | 상태 |
|---|---|---|
| Pages 활성(소스 = GitHub Actions) | 레포 설정 | **[사용자 승인 필요]** — 켜기 전에는 배포 잡이 실패(조용히 넘어가지 않음) |
| public-data 브랜치 쓰기 배포 키 | `KBJ_PUBLIC_DEPLOY_KEY_PATH`(키 파일 경로, VM 에만) | **[사용자 승인 필요]** |
| Pages 디스패치 토큰(`actions:write` 만) | `KBJ_GITHUB_DISPATCH_TOKEN` | **[사용자 승인 필요]** |
| 푸시 켜기 | `KBJ_PUBLIC_PUSH_ENABLED=true` | 위 셋 뒤에 |
| VM TLS(로그인 쪽) | 역방향 프록시 | **[사용자 승인 필요]**(이 ADR 범위 밖 — ADR 0008) |

푸시는 SSH(`git@github.com:<레포>.git`)로, 호스트 키는 VM 의 known_hosts 로 확인한다(`StrictHostKeyChecking=yes` —
처음 한 번 사람이 등록). 레포 이름은 `push.PUBLIC_REPO` 상수 [확인 필요 — 설정으로 뺄지 묶음 M 요청].

## 4. 결과

- 공개판에 나가는 것은 manifest 에 적힌 파일뿐이고, 그 파일은 Python·JS 두 검사를 모두 통과한 것이다.
- `public-data` 브랜치는 늘 커밋 1개라 공개 레포 이력에 데이터가 쌓이지 않는다(대가: 데이터 이력은 VM 에만).
- 승인 전에도 로컬·CI 에서 끝까지 만들어 볼 수 있다: `python -m kbj.services.public_export build --out <dir> --no-db`,
  `bash scripts/build_public_site.sh [--dry-run]`.
- [확인 필요] `public.export` 를 켜는 웨이브 3 에 VM 에 `KBJ_PUBLIC_EXPORT_DATABASE_URL` 이 없으면 매일 실패 알림이 난다 —
  역할 생성(운영 배포)을 켜기 전에 한다.

## S 확정 메모 (웨이브 3, 2026-10-07)

- 번호 0011·상태 **확정**. 계약 ⑧(`kbj.services.public_export` → `kbj.data.private`·`kbj.engines`·`kbj.services.api`·
  `kbj.services.collectors`·`kbj.services.engine`·`kbj.store.repos` 금지, 간접 포함)을 넣었다 — 위반 4건을 심어 잡는 시험.
- `public.export` 를 켰다. compose scheduler 에 `KBJ_PUBLIC_EXPORT_DATABASE_URL`(비면 매일 실패 알림 — 앱 DSN 으로 대신하지
  않는다)·`KBJ_PUBLIC_PUSH_ENABLED`(기본 false)만 넣었다. 배포 키 경로·디스패치 토큰·Pages 활성·VM `known_hosts` 는
  **[사용자 승인 필요]** 그대로 — compose 에도 넣지 않았다. 운영 배포에서 `kbj_public_export` 구성원 로그인 역할을 먼저 만든다.
- CI `web` 잡이 `build_public_site.sh --dry-run` 과 실제 조립(`--skip-install`)을 돈다(배포 없음). `pages.yml` 은 수동 그대로.
