# CLAUDE.md — KBJ

국장 통합 터미널 · 포트폴리오 완성본. 계획은 `docs/PLAN.md`, 공개/로그인 등급은 `docs/DATA_TIERS.md`(정본).

## 1. 절대 규칙 (모든 모듈)
1. 모든 수치에 `source`·`as_of`·`quality`(ok·stale·estimated·invalid)를 붙인다.
2. 추정을 단정하지 않는다. 기본값으로 정한 것은 문서에 `[확인 필요]`로 표시한다.
3. LLM 에 계산을 시키지 않는다. LLM 이 지어낸 값은 실패로 처리한다. LLM 은 요약·문장화에만 쓴다.
4. 에러를 삼키지 않는다. 한 모듈의 실패가 다른 모듈을 멈추지 않는다(격리).
5. 키·토큰·계좌번호는 어떤 로그·화면·커밋·응답에도 남기지 않는다(공용 마스킹 함수).
6. 실전 주문 코드를 만들지 않는다. `LIVE_TRADING=false` 가 기본이고, 사용자 승인 전엔 바꾸지 않는다.

## 2. 공개 레포 규칙 (이 레포는 Public)
- 레포에 넣지 않는다: `.env`·키, 로그인 등급 출처(KIS·KRX·금융위 주식/지수 시세·Yahoo·컨센서스·ETF 운용사)의 원본 응답과 그것으로 만든 fixture. 그런 테스트는 `fixtures/synthetic/`(합성 데이터)로 만든다.
- 넣어도 된다: 코드 전부, 문서, 공개 등급 출처(DART·ECOS·KOSIS·관세청·금융위 금투협 종합통계·소매채권)의 실데이터 fixture.
- 공개/로그인은 실행 중 검사가 아니라 **구조로** 나눈다: `kbj/data/public/` 대 `kbj/data/private/`, DB `public` 대 `private` 스키마, 공개는 정적 사이트(Pages)·로그인은 VM API. 공개 내보내기 코드는 public 쪽만 import 한다.
- 네이버 금융 스크래핑은 쓰지 않는다.
- MD6 디자인은 보고 직접 다시 만든다. MD6 의 CSS·JS·로고·이미지 파일은 복사하지 않는다.

## 3. 정본 (중복 금지 — docs/PLAN.md §2)
KIS 토큰은 auth 서비스 한 곳에서만 발급(1분 1회 제한)하고 나머지는 읽기만 한다. 앱키당 레이트리미터 하나(초당 4건).
거래 캘린더는 GEXLAB `core.calendar`, 신고가는 board 엔진, 텔레그램은 notifier 하나, 스케줄은 작업 등록부 하나, DB 는 Postgres+Timescale 하나.
같은 일을 하는 코드를 두 벌 만들지 않는다. 옮겨 온 원본은 `kbj/legacy/<프로젝트>/` 에 두고 정본으로 갈아 끼운 뒤 지운다.

## 4. 작업 방식
- 단계(Phase)마다 완료 기준을 채운 뒤 다음으로 넘어간다.
- 커밋 전 통과: 백엔드 `ruff check`·`ruff format --check`·`pyright`·`pytest`, 프런트 `eslint`·`tsc`·`vitest`.
- 지표는 `docs/metrics.md` 에 공식·입력·단위·예외·품질·테스트를 먼저 쓰고 구현한다.
- 실측 결과는 `docs/probe_results.md`, 결정은 `docs/adr/` 에 남긴다.
- 테스트는 시계·날짜를 주입한다(벽시계 의존 금지). 골든 비교는 허용오차로(바이트 비교 금지 — 플랫폼 간 float 끝자리 차이).
