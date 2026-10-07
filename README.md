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

> 상태: **P1 완료(CI 확인 대기)** — 모노레포 뼈대와 세 프로젝트 legacy 이식, 기존 시험 전부 통과(로컬). GitHub CI 첫 녹색 확인이 남았다([`docs/PLAN.md`](docs/PLAN.md) §8).
>
> 본 프로젝트는 투자 자문이 아니며, 표시되는 데이터·분석은 참고용이다.

## 구조

```
kbj/            신규 코드 (Python 3.12, uv)
  core/         순수 계산 — 값 품질(Sourced·Quality), 마스킹
  config/       설정(KBJ_ 환경변수 하나로 — docs/secrets.md)
  data/public/  공개 등급 수집기    data/private/  로그인 등급 수집기
  store/        Postgres+Timescale 스키마 — pub_*(공개)·prv_*(로그인)·ops
  engines/  services/  reports/   도메인 엔진 · 서비스(auth·scheduler·notifier…) · 산출물
legacy/         옮겨 온 원본 3개 프로젝트 — 정본으로 갈아 끼우면 지운다
tests/  scripts/  docs/(계획·등급표·ADR)
```

공개/로그인은 폴더·DB 스키마·import 규칙으로 나눈다 — [`docs/adr/0002-tier-schemas.md`](docs/adr/0002-tier-schemas.md), [`docs/adr/0003-legacy-scope.md`](docs/adr/0003-legacy-scope.md).

```
uv sync && uv run pytest && uv run lint-imports        # 개발 확인
docker compose up -d                                  # 로컬 DB·Redis (.env 에 KBJ_POSTGRES_PASSWORD·KBJ_REDIS_PASSWORD)
uv run python scripts/check_public_safety.py          # 커밋 전 공개 안전 검사
uv sync --all-groups && uv run bash scripts/test_legacy.sh   # legacy 세 프로젝트 기존 시험(부분 이름: gexlab·board·etf-rest·stock_dashboard …)
```
