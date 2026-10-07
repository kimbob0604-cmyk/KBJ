# GEXLAB — 불변 규칙

전체 계획은 `docs/PLAN.md`. 이 파일은 §2 불변 규칙 발췌다. 여기와 PLAN이 어긋나면 PLAN을 고치고 이 파일을 다시 발췌한다.

## 작업 방식
- 작업 단위는 PLAN §12 Phase. 완료 기준을 통과하기 전 다음 Phase 착수 금지
  - 예외(2026-09-28 사용자 결정): Phase 0 잔여(#19, CI)와 병행해 Phase 1·2 착수. CI 는 Actions 복구 전까지 로컬 전체 검사(`ruff check`·`ruff format --check`·`pyright`·`pytest`)로 대신
  - 예외(2026-09-29 사용자 결정): 남은 Phase 까지 순차 진행, 운영 기간 완료 기준은 병행. Phase 9 실전 주문은 사용자 최종 승인 전 금지 유지
- 지표·로직은 명세(`docs/metrics.md`) → 테스트 → 구현. 명세에 없는 공식 임의 생성 금지
- PLAN §3 `실측 필요` 항목은 추측 금지. `scripts/probe_*.py` 결과를 `docs/probe_results.md`에 기록한 뒤 사용
- `exec/broker_kis_real.py`는 Phase 9 전 작성 금지. `LIVE_TRADING` 기본값 `false`
- 비밀정보는 `.env`(로컬)·GitHub Secrets(CI)에만. 커밋·로그 출력 금지

## 상품 명세 (`core/specs.py` 한 곳에만 정의)
| | 선물 | 미니선물 | 옵션(위클리 포함) |
|---|---|---|---|
| 승수 | 250,000 | 50,000 | 250,000 |
| 호가단위 | 0.05pt | 0.02pt | ≥10pt 0.05 / <10pt 0.01 |
- 주문 가격은 `Decimal`로 틱 반올림 후 전송

## 세션·날짜
- 주간 08:45~15:45 (최종거래일 종목 15:20), 야간 18:00~익일 06:00
- 야간 거래분은 다음 거래일(T+1) 귀속. 금요일 야간 → 다음 거래일(보통 월요일)
- 야간장: 월~목 밤은 다음 날이 거래일일 때만, 금요일 밤은 늘(월요일 휴장이어도 — 실측)
- 모든 레코드에 `session`(day/night)과 `trade_date` 분리 저장
- 휴장일: `exchange_calendars` XKRX + `config/holidays_override.yaml`
- 저장 UTC, 표시 KST. naive datetime 금지

## 만기
- 월물 만기주엔 월요일 위클리만 상장. 만기 목록·`fid_mtrt_cnt`는 KIS 월물리스트 API에서 조회(계산은 교차검증용)
- 만기 시각 15:20 KST. `T = max(남은 분, 5) / (365×24×60)`

## GEX 부호·단위
- 딜러 콜 롱·풋 숏. Black-76, 기초자산 = 동일 만기 합성선물 F
- `GEX_call(K) = +Γ·OI·250,000·F²·0.01`, `GEX_put(K) = −Γ·OI·250,000·F²·0.01` (원/1%, 표시 억원)
- DEX = `Σ OI_c·Δ_c·m·F − Σ OI_p·Δ_p·m·F`. Vanna·Charm 동일 부호
- 부호는 `tests/test_sign_conventions.py`로 고정

## API 제약
- KIS 레이트리미터는 앱키당 하나. 토큰 24h·재발급 1분 1회, 발급은 `auth` 서비스만
- 웹소켓 앱키당 41건, 세션 소유는 `ws-gateway` 하나. 전광판 콜/풋 각 100건·1초 1건 이하
- KRX Open API 10,000회/일, 다음 영업일 08:00 갱신
- KIS 시세 제3자 제공 불가 → 대시보드 비공개. 출처 "한국거래소 통계정보"·KIS 푸터 고정

## 코딩
- Python 3.12, `uv`, `ruff`, `pyright`(strict: core/ backtest/ exec/)
- 외부 입력은 `pydantic` 검증 후 사용. 모든 지표 산출물에 `quality: ok|stale|estimated|invalid`
- 예외는 지표·위젯 단위로 격리. 로깅은 구조화 JSON(거래일·세션·서비스명)
- 커밋 단위: 기능 하나 + 테스트
