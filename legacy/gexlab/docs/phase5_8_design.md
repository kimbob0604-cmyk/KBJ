# Phase 5~8 설계안 — 배포·백필·백테스트·페이퍼

작성 2026-09-29. PLAN §9~§12 를 구현 단위로 옮긴다. 2026-09-29 사용자 결정으로 남은 Phase 를 순차 진행하되, **운영 기간이 필요한 완료 기준은 병행**하고 **Phase 9 실전 주문은 사용자 최종 승인 전 금지**다(PLAN §12, CLAUDE.md).

표기: 🧑 = 사용자 준비가 필요(코드로 대신할 수 없음), ⏱ = 운영 기간이 필요한 완료 기준.

## Phase 5 — 24시간 배포 (PLAN §11)

완료 기준: ⏱🧑 VM 에서 2주 연속 무중단, 장애 주입 테스트 자동 복구 확인.

| 작업 | 구현 | 비고 |
|---|---|---|
| 배포 | `scripts/deploy.sh`: `git pull && docker compose pull && docker compose up -d`. **세션 상태가 `IDLE`·`POST_DAY` 일 때만**(Redis `session:state` 확인, 아니면 거부) | PLAN §11.5 |
| 롤백 | 직전 이미지 태그로 재기동(`deploy.sh --rollback`) — 배포 때 태그를 `state/deploy_history` 에 남김 | |
| 호스트 기동 | `deploy/systemd/gexlab.service`(compose up/down), `gexlab-heal.timer`: 5분마다 `docker compose ps` 의 unhealthy 컨테이너 재시작 | `restart: always` 는 unhealthy 를 재시작하지 않는다(서비스 단계 open question) — 외부 이미지 없이 timer 로 |
| 외부 하트비트 | engine·ws-gateway 가 1분마다 `HEALTHCHECK_URL`(Healthchecks.io 등, SecretStr)로 핑 — 🧑 URL 발급 | PLAN §11.3 |
| 로그 | compose `logging: json-file, max-size 20m, max-file 10` | 30일 보관 |
| 디스크 | health: 디스크 사용률·DB 크기 80% 경고 | |
| 백업 | `scripts/export_parquet.py`(`POST_DAY` 에 전일 표 → Parquet), 주 1회 `pg_dump`, 외부 업로드 대상은 설정 — 🧑 저장소 선택 | PLAN §11.4 |
| 복구 | `docs/runbook.md` 복구 절차: DB 복구, 토큰 만료, 웹소켓 강제 종료 | |
| 장애 주입 | integration 시험: ws-gateway 컨테이너 강제 종료 → 재연결·복구, db 재시작 → 스풀 재적재, Redis 의 토큰 삭제·만료 → auth 재발급·서비스 재사용 | 로컬 Docker 로 자동화 |
| VM | 🧑 서울 리전 VM(2 vCPU/8GB/SSD 100GB+), Tailscale, `.env` 수동 배치(권한 600), chrony | PLAN §11.1·§11.2 |

## Phase 6 — 백필·신호 검증 (PLAN §9.1, §9.6-1, §5.7)

완료 기준: 시대별 레짐 통계 리포트, ⏱ KIS 마감 스냅샷 vs KRX 일치 검증(녹화 필요).

- `scripts/backfill_krx.py`: 2010-01-04 ~ 전 거래일, 날짜당 옵션·선물 일별 1회씩(≈ 4,000일 × 2). **재개 가능**(완료 날짜 기록), 초당 1건 이하, **하루 9,000건 상한**(KRX 10,000/일, #16), 응답 원본은 `data_store/krx/{opt,fut}/YYYY/YYYYMMDD.parquet`, 파싱은 `data/krx/models.py` → `krx_*_daily` 표. 휴장일은 빈 응답으로 건너뜀
- 위클리 시대 경계: KRX 데이터에서 위클리 상품(`PROD_NM`) 최초 등장일로 자동 판정(PLAN §9.6-2)
- 일봉 GEX 재구성(`backtest/daily_gex.py`): 날짜별 정규 행만(#15 — 야간 행 IV 0.00 은 결측), OI = `ACC_OPNINT_QTY`, F = 같은 결제월 선물 정산가 또는 합성 F(정산 이론가 기준), T = 그날 15:45 기준 달력 분(§1.4), IV = `IMP_VOLT` 와 종가 자체 역산 병행 저장(PLAN §9.1). 레벨은 `core.gex`·`core.levels` 그대로
- 신호 유효성 검증(§9.6-1) 리포트: 순GEX 부호별 다음 세션 실현변동폭 분포, Flip 이탈 뒤 자기상관, 롱감마 레짐의 콜월·풋월 제한 빈도 vs 무작위 대조군, 딜러 가정 점검과의 관계 — 시대 구분별
- §5.7 레벨 준수 통계: 표본 기간·표본 수 항상 표시
- KIS 분봉 백필: 설계 §8 의 페이징 방식이 미실측이라 **probe 먼저**(23:59:59·05:59:59 외 시각 조회) → 결과 기록 뒤 적재
- ⏱ KIS 마감 스냅샷 vs KRX `ACC_OPNINT_QTY` 일치(§6.2): Phase 1 녹화가 쌓이면 매일 자동 비교

## Phase 7 — 전략 백테스트 (PLAN §9.2~§9.7)

완료 기준: 전략별 표본 외 성과 리포트(슬리피지 민감도 포함).

- `backtest/engine.py`: 이벤트 기반, 바 단위(일봉·분봉) 공통 인터페이스. 시점 정합성(§9.2): T일 KRX 로 만든 신호는 T+1 주간부터, 장중은 수신 다음 바 체결, 야간 T+1 귀속
- `backtest/roll.py`(§9.3): 최종거래일 직전 거래일 장 마감 롤(기본)·OI 역전일(대안), 가격조정·원가격 두 계열, 레벨-선물 베이시스 보정 규칙 명시
- `backtest/fills.py`·`costs.py`(§9.4): 다음 바 시가 또는 지정가 도달, 가격제한 도달 시 미체결, 수수료(설정 — 🧑 계좌 요율), 슬리피지 주간 1틱·야간 2틱 + 0~3틱 민감도, 만기일·서킷브레이커·사이드카 플래그
- `strategy/base.py`: `on_bar(state) -> list[Order]` — **라이브 trader 와 같은 클래스**. `strategy/rules/s1~s5`, `config/strategies/*.yaml`(파라미터 ≤ 3)
- `backtest/walkforward.py`: 학습 3년 / 검증 1년 롤링, **최종 1년 홀드아웃 봉인**(열람 시 기록), 사전 등록 격자만 탐색
- `backtest/metrics.py`·`report.py`: 수익곡선·CAGR·MDD·샤프·소르티노·PF·승률·손익비·거래 수·보유시간, 레짐·세션·요일·만기주 분해
- 장중 전략(S3·S4)은 장중 GEX 녹화가 쌓여야 한다 ⏱ — 그 전엔 일봉 근사만

## Phase 8 — 페이퍼 트레이딩 (PLAN §10.1~§10.3)

완료 기준: ⏱ 최소 4주 운영, 백테스트 대비 괴리 분석, 리스크 한도 테스트 통과.

- `exec/paper_broker.py`: 라이브 호가 기반 가상 체결(주간·야간), §9.4 체결 모델과 같은 규칙
- `exec/broker_kis_demo.py`: KIS 모의 주문(주간만, `VTTO1101U` — PLAN §3 #14), 🧑 모의 앱키·계좌. 모의 앱키는 시세 수집에 쓰지 않는다(§4.2)
- `exec/risk.py`(§10.2): 최대 계약 수·세션당 신규 주문 수·일 손실 한도·연속 손실 정지·데이터 품질 게이트·개장 직후/만기 마감 전 N분·가격제한 근접 금지·킬스위치(텔레그램 `/kill`, 파일 플래그, 대시보드) — 주문 전 필수 통과
- 주문 무결성(§10.3): 멱등 키, `Decimal` 틱 반올림(`core.specs`), 기본 지정가 IOC(시장가는 청산·킬스위치만), 60초 잔고 대사 불일치 시 신규 주문 차단 + 알림
- `services/trader`: 전략 실행(Phase 7 과 같은 전략 클래스), 브로커는 설정으로 paper | kis_demo. **`broker_kis_real.py` 는 만들지 않는다**(Phase 9, 사용자 승인 전 금지). `LIVE_TRADING` 기본 false 유지
- 괴리 분석: 페이퍼 체결 vs 같은 기간 백테스트 체결 비교 리포트

## Phase 9 — 장중 전략·실전 (여기서 멈춘다)

- 🧑 실전 앱키·파생 계좌·사전교육 요건(PLAN §10.4), 🧑 사용자 최종 승인이 완료 기준. 이 설계 범위에서 실전 브로커·실전 주문 코드는 작성하지 않는다

## 공통 순서

Phase 3(확장 지표·engine 서비스·기능 플래그) → Phase 4 → Phase 5(로컬 가능분) → Phase 6 → Phase 7 → Phase 8. 각 Phase: 명세/설계 → 테스트 → 구현 → 적대적 검토 → 수정 → 로컬 전체 검사(ruff·format·pyright·pytest + integration) → `claude/phase1` 병합·push.
