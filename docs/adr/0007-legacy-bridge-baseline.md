# ADR 0007 — legacy 논리 URL 브리지와 직접 호출 금지 검사의 기준선 정책 (2026-10-07)

상태: 확정(P2 — 묶음 H·I 구현이 근거, `docs/p2_design.md` §3.7·§9, D-P2-6, 메인 결정 D2·D6, PLAN §8 P2 완료 기준
"legacy 가 KIS·KRX·DART 를 직접 부르면 실패").
구현: `kbj/data/legacy_bridge.py`(+ `tests/unit/data/test_legacy_bridge.py`, 프로젝트별 다리 시험 3개) ·
`scripts/check_canonical.py`·`scripts/canonical_baseline.txt`(+ `tests/test_check_canonical.py`) · import-linter
계약 ④~⑦(`pyproject.toml`, `tests/test_import_contracts.py`) · CI `.github/workflows/ci.yml`.

## 1. 맥락

- legacy 세 프로젝트는 KIS·KRX·DART 를 "주소 상수 + `requests`/세션"으로 직접 불렀다. 토큰도 각자 발급했다(K1~K10).
  P2 는 이 직접 호출을 0 으로 만들어야 하지만, legacy 를 다시 쓰는 것은 P3~P7(영역별 이전)의 일이다.
- 정본 경계는 import-linter 로 kbj 안에서만 강제할 수 있다(legacy 는 import-linter 의 루트가 아니다). 주소 문자열은
  import 가 아니라서 계약으로 잡히지 않는다.
- 텔레그램·ECOS·공공데이터포털·KOSIS·네이버·KRX 웹 스크랩·KIS 웹소켓 같은 남은 직접 호출은 한 번에 지울 수 없다
  (네이버 어댑터는 만들지 않는다 — D6, 웹소켓은 P7 — D2).

## 2. 결정

### 2.1 논리 URL 브리지 — legacy 가 KBJ 어댑터를 쓰는 유일한 길

- legacy 의 주소 상수를 **논리 URL** 로 바꾸고 `requests`·세션 자리에 `kbj.data.legacy_bridge` 를 끼운다. 나머지
  legacy 코드(파라미터 채우기·파서·재시도)는 그대로 돈다.

| 스킴 | 가는 곳 | 넣는 비밀(호출자 값은 버린다) | 한도 |
|---|---|---|---|
| `kis:/uapi/…` | `KisRestClient.get` | auth 가 둔 토큰(읽기만)·앱키·시크릿 | `rl:kis` — 기본 P3, 헤더 `x-kbj-priority` |
| `kis-master:<파일>` | `master.download_fo_master()` | — | — |
| `krx:/<엔드포인트>` | `KrxClient.daily` | `AUTH_KEY` | `rl:krx` + `krx:calls:<날짜>` |
| `dart:/<경로>` | `DartClient.get_json·get_raw` | `crtfc_key` | `rl:dart` + `budget:dart:<날짜>` |
| `http(s)://…` | **`ValueError`** — 남은 직접 호출이 조용히 새지 않게 | — | — |

- 응답은 `requests.Response` 모양이다. **발급하지 않는다**(ADR 0004) — 토큰이 없으면 HTTP 503 +
  `msg_cd=KBJ_TOKEN_UNAVAILABLE` 로 legacy 의 기존 오류 경로가 사유를 남긴다. 오류 문구는 한 번 더 가린다.
- 브리지는 legacy 전용이다. kbj 코드는 어댑터를 바로 쓴다. P9(legacy 삭제)에 브리지도 지운다.
- 텔레그램 발송·수신은 브리지가 아니라 notifier shim(`kbj.services.notifier.client.legacy_send*`, ADR 0005)이다.
  휴장·장중 판정은 `kbj.core.calendar_compat` 다시 내보내기다.

### 2.2 직접 호출 금지 검사 — 목표 0 그룹과 줄어들기만 하는 기준선

- `scripts/check_canonical.py` 가 레포 전체(`git ls-files --cached --others --exclude-standard`, 문서·시험·fixture
  제외)에서 그룹별 호스트·경로 문자열을 찾는다.
- **목표 0 그룹**(`kis_oauth`·`kis_rest`·`kis_master`·`krx_api`·`dart`): 허용 위치(kbj 어댑터 한 곳씩, 발급 경로는
  `kbj/services/auth/**`) 밖이면 한 줄이라도 실패. 기준선에 둘 수 없다. 이것이 PLAN §8 P2 완료 기준의 증명이다.
- **기준선 그룹**(`telegram`·`ecos`·`datago`·`kosis`·`kis_ws`·`naver`·`krx_scrape`): `kbj/` 안에서는 허용 위치 밖이면
  실패, 그 밖(legacy·설정)은 `scripts/canonical_baseline.txt`(그룹 | 경로 | 건수 | 정리 단계·사유)와 견준다.
  **줄어들기만 한다**: 기준선에 없는 파일의 위반, 건수 증가, 건수가 줄었는데 기준선을 안 줄임 — 모두 실패.
  `--update-baseline` 은 줄이는 방향으로만 다시 쓴다. 같은 커밋에서 코드와 기준선을 함께 줄인다.
- **AST 규칙**(legacy 의 .py, 시험 제외): `kbj.services.auth`(발급자) import 금지, kbj 의 `_` 로 시작하는 이름
  import·속성 접근(`getattr`·`hasattr` 문자열 포함) 금지 — 비공개 주소 상수로 문자열 검사를 피하는 것을 막는다,
  kbj 모듈은 허용 목록(설계 §9.5 + 묶음 H 요청 `notifier.format`·`notifier.store`·`store.db`)만, 파싱하지 못한
  파일은 건너뛰지 않고 실패.
- 출력은 `경로:줄: 그룹 — 설명` 뿐이다. 찾은 문자열은 출력하지 않는다(주소에 키가 섞일 수 있다 — 절대 규칙 5).
  종료 코드 0 통과·1 위반·2 사용법·기준선 형식 오류. CI 가 돌린다.
- kbj 안은 import-linter 계약 ①~⑦ 이 같은 경계를 구조로 지킨다: ④ 발급 모듈(auth)은 다른 모듈이 import 하지 않는다,
  ⑤ 텔레그램 HTTP 는 notifier 안에서만(간접 import 도 — `Attachment` 형 두 줄만 예외), ⑥ 어댑터는 서비스·엔진·리포트를
  모른다, ⑦ 저장소는 어댑터·서비스·엔진을 모른다.

## 3. 결과 (2026-10-07)

| 그룹 | legacy·기타 | 정리 단계 |
|---|---|---|
| kis_oauth·kis_rest·kis_master·krx_api·dart | **0**(목표 0) | P2 — 끝 |
| telegram·ecos·kosis | 0 | 기준선 0 |
| datago | 2 | P3~P6 — ET board ingest·flowlab 진단 |
| kis_ws | 4(`legacy/gexlab/config/kis_ws.yaml`) | P7 — ws-gateway 승격(D2) |
| naver | 176 | P3~P5 — 기능별로 KRX·KIS 로(D6) |
| krx_scrape | 81 | P3 — KRX OPEN API·KIS 로(설정 1 파일 2줄은 legacy 의존성 그룹을 지울 때) |

- legacy 시험은 루트 venv(kbj 설치)로 돈다 — `scripts/test_legacy.sh`. 브리지로 바꾼 legacy 의 수는 P1 기준에서
  kbj 로 승격한 원본 시험만큼만 줄었다(설계 §11.5).
- 남은 일: ET board ingest 의 옛 환경변수 관문(`creds.has('KRX_API_KEY')`·`'KIS_APP_KEY'`·`'DART_API_KEY'`)은 KBJ
  환경에서 그 단계를 조용히 건너뛴다(브리지로 부를 수 있는데도) — board ingest 이전(P3) 때 KBJ 설정 확인으로 바꾼다.
  notifier 에 기본 저장소를 여는 helper 가 생기면 허용 목록에서 `notifier.store`·`store.db` 를 뺀다.
