# KBJ — 국장 통합 터미널

한국 주식시장을 한 화면에서 보는 개인 터미널이자 포트폴리오 프로젝트.
장중에는 시장·수급·옵션 레벨을 켜 두고 보고, 장후·월초에는 수출입·공시·재무 흐름으로 종목을 고른다.

- **시장 대시보드**: 지수·선물(주야간)·수급·업종 히트맵·트리맵·신고가 보드 *(로그인)*
- **진단**: 코스피200 옵션 GEX 레벨(Flip·콜월·풋월·기대변동폭), 시장폭·신용·수급 지표
- **수출입 분석**: 관세청 품목×국가·시군구·10일 잠정치, 급등 탐색, 단가 대 물량, 종목↔HS 매핑 *(공개)*
- **공시·재무**: DART 실시간 공시·잠정실적·재무·내부자·자사주 *(공개)*
- **매크로**: 한국은행·통계청·금투협(신용잔고·예탁금·신용스프레드) *(공개)*
- **알림**: 규칙 기반 기술적 알림(RSI·MACD·볼린저·수급 조건) → 텔레그램, 알림 후 성과 추적 *(로그인)*

공개 화면에는 재배포가 허용된 공공데이터와 TradingView 위젯만, 로그인하면 전체 데이터를 보여 준다 — [`docs/DATA_TIERS.md`](docs/DATA_TIERS.md).

> 상태: **P2 공용 기반 구현(로컬 통과, CI 확인 대기)** — KIS 토큰 발급은 auth 한 곳·레이트리미터·캘린더·공개/로그인 어댑터·notifier·작업 등록부·DB 마이그레이션, legacy 의 KIS·KRX·DART 직접 호출 0(`scripts/check_canonical.py`), 하루 운영 시뮬레이션(24시간 창 — 토큰 발급 auth 에서만·중복 수집 0, `tests/sim`). 운영 VM 전환(옛 발급자 정지·웹훅 이전)은 남았다([`docs/PLAN.md`](docs/PLAN.md) §8, [`docs/p2_design.md`](docs/p2_design.md)).
>
> 본 프로젝트는 투자 자문이 아니며, 표시되는 데이터·분석은 참고용이다.

## 구조

```
kbj/            신규 코드 (Python 3.12, uv)
  core/         순수 계산 — 값 품질(Sourced·Quality), 마스킹, 거래 캘린더(XKRX·XNYS), 시각, cron
  config/       설정(KBJ_ 환경변수 하나로 — docs/secrets.md)
  data/public/  공개 등급 수집기    data/private/  로그인 등급 수집기
  store/        Postgres+Timescale 스키마 — pub_*(공개)·prv_*(로그인)·ops
  engines/  services/  reports/   도메인 엔진 · 서비스(auth·scheduler·notifier…) · 산출물
config/         작업 등록부 jobs.yaml · 호출 상한 limits.yaml · 발송 규칙 notify.yaml · 휴장 덮어쓰기
legacy/         옮겨 온 원본 3개 프로젝트 — 정본으로 갈아 끼우면 지운다
tests/  scripts/  docs/(계획·등급표·ADR)
```

공개/로그인은 폴더·DB 스키마·import 규칙으로 나눈다 — [`docs/adr/0002-tier-schemas.md`](docs/adr/0002-tier-schemas.md), [`docs/adr/0003-legacy-scope.md`](docs/adr/0003-legacy-scope.md).

P2 공용 기반의 결정: KIS 발급은 auth 한 곳 [`0004`](docs/adr/0004-kis-issuance.md) · 알림 outbox·중복 키 [`0005`](docs/adr/0005-notifier-outbox.md) · 작업 등록부·데이터 키 선점 [`0006`](docs/adr/0006-job-registry-claims.md) · legacy 브리지·직접 호출 금지 기준선 [`0007`](docs/adr/0007-legacy-bridge-baseline.md).

```
uv sync && uv run pytest && uv run lint-imports        # 개발 확인(하루 시뮬레이션 tests/sim 포함 — 빼려면 -m "not sim")
docker compose up -d                                  # 로컬 DB·Redis (.env 에 KBJ_POSTGRES_PASSWORD·KBJ_REDIS_PASSWORD)
docker compose --profile app up -d --build            # + migrate·auth·scheduler·notifier (앱 이미지 Dockerfile)
uv run python scripts/check_public_safety.py          # 커밋 전 공개 안전 검사
uv run python scripts/check_canonical.py              # 직접 호출 금지 검사(정본 경계 — 기준선 scripts/canonical_baseline.txt)
uv sync --all-groups && uv run bash scripts/test_legacy.sh   # legacy 세 프로젝트 기존 시험(부분 이름: gexlab·board·etf-rest·stock_dashboard …)
```
