# Phase 4 설계안 — 대시보드·알림

작성 2026-09-29. PLAN §7(대시보드)·§8(알림·브리핑)·§12 Phase 4 를 구현 단위로 옮긴다. 완료 기준(PLAN §12): **리플레이 모드에서 전 탭·전 위젯 동작, 알림 오탐 테스트**.

전제: engine 서비스(Phase 3에서 구현 — 체인 스냅샷을 받아 `core.gex`·`core.levels`·Phase 3 지표를 계산해 `metrics`·`levels` 표와 Redis 로 발행)가 있다. 대시보드·알림은 engine 산출물만 읽는다(계산하지 않는다).

## 1. 구성

| 구성요소 | 위치 | 하는 일 |
|---|---|---|
| `api` | `api/main.py`, `api/routes/`, `api/ws.py` | FastAPI. REST(현재 값·시계열·설정) + WebSocket push(engine → Redis pub/sub → 브라우저). 읽기 전용(킬스위치 명령만 예외, §4) |
| `ui` | `ui/index.html`, `ui/js/`, `ui/css/` | 정적 파일, `api` 가 서빙. 빌드 도구 없이 ES 모듈. 차트 라이브러리는 **로컬 vendor 파일**(`ui/vendor/`)로 둔다 — 외부 CDN 호출 없이 VPN 안에서만 동작 |
| `notifier` | `services/notifier/` | 알림 규칙 평가(엔진 발행 구독), 텔레그램 발송·명령 수신 |
| 리플레이 | `live/replay.py` | `raw_messages` 녹화를 시각 순서로 재생해 엔진·api 를 라이브처럼 구동(완료 기준의 "리플레이 모드") |

- 인증·노출: 공개 포트 없음(PLAN §11.2, Tailscale). `api` 는 `127.0.0.1`/Tailscale 인터페이스에만 바인딩. KIS 시세 제3자 제공 불가(§2.6)
- 의존성 추가(Phase 4 착수 커밋): `fastapi`, `uvicorn[standard]`, 테스트용 `httpx`(있음). 차트: TradingView lightweight-charts(Apache-2.0, 저작자 표기), Plotly(MIT) — vendor 파일 + `ui/vendor/LICENSES.md`

## 2. API

| 경로 | 내용 |
|---|---|
| `GET /api/state` | 세션 상태·trade_date·session, 서비스 하트비트, 데이터 품질 요약 |
| `GET /api/levels?scope=all\|nearest\|0dte` | 콜월·풋월·Flip(multi_cross)·절대감마·전환점 거리·기대범위·상위 레벨, 각 `quality`·`as_of` |
| `GET /api/gex/strikes?expiry=` | 행사가별 GEX 막대(콜·풋·순), `StrikeGexTable.quality` |
| `GET /api/series/{name}?from=&to=` | 지표 시계열(`metrics` 표) — 장중흐름 오버레이·IV 기간구조 등 |
| `GET /api/vol/smile?expiry=` | IV 스마일(자체 IV, 품질별 표시) |
| `GET /api/flow/*` | HIRO-lite, 투자자별, 딜러 가정, 대량 체결, PCR, 맥스페인 |
| `GET /api/health` | 서비스 상태, 피드 지연, 마지막 수신, 웹소켓 구독 목록(41건 사용량), 레이트리미터 상태, 격리 레코드 수, 교차검증 결과(§6.2 재현 검사) |
| `WS /ws` | 구독 채널: `levels`, `metrics`, `health`, `session` — engine 발행을 그대로 전달, 메시지마다 `quality`·`as_of` |
| `POST /api/kill`, `POST /api/resume` | Phase 8 trader 의 킬스위치(§10). Phase 4 에서는 자리만(trader 없으면 409) |

- 모든 응답은 pydantic 모델. 시각은 UTC ISO(표시는 UI 가 KST 로)
- `quality` 가 `stale`/`invalid` 면 값과 함께 그대로 보낸다 — 숨기지 않고 UI 가 회색 처리(PLAN §7.2)

## 3. UI (탭 — PLAN §7.1)

| 탭 | Phase 4 범위 |
|---|---|
| Live | 레짐 카드, 레벨 테이블, 행사가별 GEX 바(만기 선택), 장중흐름(선물 + 레벨 오버레이), OI 증감 히트맵 |
| Volatility | 스마일, 기간구조, 25Δ 스큐, IV 랭크·퍼센타일, IV−HV |
| Flow | HIRO-lite, 투자자별, 딜러 가정 배지, 대량 체결, PCR, 맥스페인 |
| Night | 야간 Live(주간 종료 레벨 대비 변화) — 분기 B(단건 EU·CM) 데이터 |
| Stats | Phase 6 산출물이 생기면 채움 — 그 전엔 "데이터 없음" 표시 |
| Backtest | Phase 7 산출물 뷰어 — 그 전엔 "데이터 없음" |
| Trading | Phase 8 — 그 전엔 "비활성" |
| Health | §2 `/api/health` 전 항목 |

- 위젯 공통: `quality` 배지 + 마지막 갱신 시각. `stale`/`invalid` 회색, 신선한 값처럼 보이지 않게(PLAN §7.2)
- 기능 플래그(metrics.md §8): `visible` 만 표시, `shadow` 는 Health 탭 "섀도 지표" 목록에서만
- 모바일 반응형, 푸터: 데이터 출처(KIS, "한국거래소 통계정보"), "투자 판단 참고용"(CLAUDE.md)

## 4. 알림 (PLAN §8)

- 정기 브리핑: 08:30·12:00·15:50·06:05 KST(scheduler 가 트리거, notifier 가 작성). 내용 PLAN §8 그대로
- 이벤트: Flip 돌파, 순GEX 부호 전환, 콜월·풋월 이동, 기대범위 이탈, 대량 체결, 시스템 장애(health critical)
- 오탐 방지(PLAN §8): 돌파는 레벨 ±0.1% 버퍼 + 연속 2스냅샷 확인(히스테리시스), 같은 알림 쿨다운 10분, 입력 품질이 `ok` 가 아니면 이벤트 알림 보류하고 품질 경고만
- 규칙은 순수 함수(`services/notifier/rules.py`: 이전 상태 + 새 스냅샷 → 알림 목록)로 두고 테스트
- 텔레그램: Bot API(`TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`, SecretStr). 명령 `/status`·`/levels`·`/kill`·`/resume` — `/kill` 은 확인 단계 후 trader 로 전달(Phase 8 전엔 "trader 없음" 응답). 허용 chat_id 외 명령은 무시
- 발송 실패는 재시도(지수 백오프) 후 health, 알림 내용에 비밀정보·원문 시세 대량 포함 금지

## 5. 테스트

- API 계약: 각 경로의 pydantic 응답 스키마, `quality` 전달, stale 값 회색 플래그
- 리플레이 E2E(integration 마크): 녹화 fixture(Phase 1 녹화 전엔 2026-09-28 체인 스냅샷에서 합성) → replay → engine → api → WebSocket 클라이언트가 모든 탭 채널을 받는지, 모든 위젯 데이터 경로가 채워지는지
- UI 스모크: 정적 파일 로드·JS 모듈 import 오류 없음(headless 브라우저는 Phase 4 범위에서 선택 — Playwright MCP 로 수동 확인 가능)
- 알림 오탐: 레벨 근처 진동 시계열(버퍼 안 흔들림) → 알림 0, 버퍼 밖 2스냅샷 → 1, 쿨다운 10분 안 반복 → 1, 품질 estimated → 이벤트 보류·품질 경고 1
- 텔레그램: 가짜 Bot API 서버(httpx.MockTransport), 허용 chat_id 필터, `/kill` 확인 흐름

## 6. 구현 순서 (커밋 = 기능 하나 + 테스트)

1. 의존성(fastapi·uvicorn) + api 골격(`/api/state`, `/api/health`) + 응답 모델
2. levels·gex·series·vol·flow 경로 + WebSocket push
3. ui 정적 골격(탭·품질 배지·푸터) + vendor 차트 + Live 탭
4. Volatility·Flow·Night·Health 탭(Stats·Backtest·Trading 은 자리)
5. notifier 규칙(순수) + 오탐 테스트
6. 텔레그램 발송·명령(가짜 서버)
7. live/replay.py + 리플레이 E2E + compose 에 api·notifier 추가
