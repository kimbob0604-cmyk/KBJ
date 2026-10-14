# ADR 목록

결정 기록(Architecture Decision Record). 번호 순서 — 상태는 각 파일의 `상태:` 줄과 같다(확정·채택 = 지금 유효,
대체됨 = 뒤 ADR 이 대신한다). 0020 은 번호를 건너뛰었다(결번). 새 ADR 은 0039 부터.

| 번호 | 제목 | 상태 |
|---|---|---|
| [0001](0001-p0-decisions.md) | P0 결정 | 확정 |
| [0002](0002-tier-schemas.md) | 등급별 DB 스키마(pub_·prv_)와 공개 내보내기 역할 | 확정 |
| [0003](0003-legacy-scope.md) | legacy/ 의 범위: 린트·타입검사 제외, 프로젝트별 시험 루트 분리 | 대체됨 — 0038 |
| [0004](0004-kis-issuance.md) | KIS 앱키 주입 범위와 발급 차단 세 겹 | 확정 |
| [0005](0005-notifier-outbox.md) | 알림 발송: outbox·중복 키·발송 기록 분리·발송 꺼짐 기본 | 확정 |
| [0006](0006-job-registry-claims.md) | 작업 등록부·논리 데이터셋·데이터 키 선점으로 중복 수집 금지, `runner: external` | 확정 |
| [0007](0007-legacy-bridge-baseline.md) | legacy 논리 URL 브리지와 직접 호출 금지 검사의 기준선 정책 | 대체됨 — 0038 |
| [0008](0008-login-api.md) | 로그인 API·단일 사용자 로그인·웹훅 서버·API 는 외부 호출 없음 | 확정 |
| [0009](0009-board-engine-promotion.md) | 신고가 board 엔진 승격: 골든 먼저, 순수/IO 분리, legacy shim, 역사적 신고가 깊이 | 확정 |
| [0010](0010-etf-flows-issuers.md) | ETF 수급 엔진·운용사 어댑터 승격, 유형 규칙, 분할 감지 | 확정 |
| [0011](0011-public-static-site.md) | 공개 정적 사이트: pub_* 내보내기·고아 브랜치 public-data·수동 Pages | 확정 |
| [0012](0012-provisional-ledger.md) | 잠정 → 확정 원장 우선순위·차이 기록·KRX D+1 대조·거래소 구분 기본값 | 확정 |
| [0013](0013-dart-filings-collection.md) | DART 공시 수집: 증분 피드·구조화 API 우선·원문 폴백·DB 대기 폴링·공시 알림 정책 | 확정 |
| [0014](0014-dart-report-promotion-golden.md) | dart-report 승격과 엑셀 골든(정규화 비교), 재무 2층·분기 환산 하나 | 확정 |
| [0015](0015-consensus-source-kis.md) | 컨센서스 출처 교체(네이버 → KIS), 잃는 것, 목표가 컨센서스·리비전 정의 | 확정 |
| [0016](0016-valuation-pit-watchlist.md) | 밸류에이션 시점 일관(PIT)·회사 시총·EV 정의, 시나리오 적정가(구 TAM), 관심종목 원천과 20종목 완료 기준 | 확정 |
| [0017](0017-newhigh-three-axes.md) | 신고가 3축: 역사적 · 52주 · 120일 (당일 기준 창) | 채택 |
| [0018](0018-post-close-1600.md) | 장 마감 뒤 텔레그램은 전부 16:00 | 채택 |
| [0019](0019-investor-flow-single-writer.md) | 장중 투자자 수급의 쓰기 주인 하나·프로그램 상위 표의 범위 표시 | 채택 |
| 0020 | (결번 — 번호 건너뜀) | — |
| [0021](0021-macro-sources-tiers.md) | 매크로 출처·등급·수집 모듈 분리, 경제 캘린더(공식 일정 + 발표 결과 + 예상치), 상단 띠 P5 칩 | 채택 |
| [0022](0022-briefs-unified.md) | 브리핑 통합: 아침 브리핑·마감 요약 섹션, 미준비·생략, LLM 없음, 하루 1회, 수급 리포트 승격 | 채택 |
| [0023](0023-us-board.md) | 간밤 미국 신고가 승격(Nasdaq·Stooq·Yahoo → 252봉 엔진 → us_summary·us_brief), 금융위 일별 대조 | 채택 |
| [0024](0024-etf-map-report.md) | ETF 맵·보유 탐색·액티브 동시 편입·ETF 리포트(분류 정본 하나·리포트 08:20·SD ETF 맵 폐기) | 채택 |
| [0025](0025-public-js-budget.md) | 공개 JS 예산 60 → 72 KB(페이지 7 수출입은 공개 전체) | 채택 |
| [0026](0026-export-flash.md) | 수출 속보 반영(관세청 10일 잠정치 — 폴링 창·발표 당시/최신·열 변경·16:00 한 줄·공개 파일) | 채택 |
| [0027](0027-trade-stock-map.md) | 종목↔HS 매핑표와 수출 겹쳐 보기(자체 큐레이션·근거 필수·공개/local 분리·검증 두 층) | 채택 |
| [0028](0028-index-rebalance-prediction.md) | 패시브 지수 정기변경(편입·편출) 예측: 지수별 규칙 모듈·컷오프 추정·로그인 전용 화면 | 초안 |
| [0029](0029-gex-port-boundary.md) | GEX 이식 경계: 옮긴 것·남긴 것·DB 연결·쓰기 주인·웹소켓 D2 마감·알림 시각 | 확정 |
| [0030](0030-diag-indicators.md) | 페이지 3 진단 지표: 등급 배치·국장 공포탐욕·종합 판정·VKOSPI 대체·작업 둘 | 확정 |
| [0031](0031-rule-alerts.md) | 규칙 알림: DSL v1·평가 시점 둘·쿨다운 두 겹·근거 템플릿·장중 시세 저장·장중 수급 라이브 | 확정 |
| [0032](0032-rules-backtest-outcome-match.md) | 규칙 백테스트·알림 후 성과·일치 감시: 같은 함수·anchor·d+1 체결·사유 분류 | 확정 |
| [0033](0033-journal-portfolio-personal-writes.md) | 저널·포트폴리오·관심종목 쓰기: 쓰기 모듈 하나·이동평균 단가·읽을 때 계산·SD 이관 경로 | 확정 |
| [0034](0034-demo-mode.md) | 데모 모드: 별도 compose·합성 세계·고정 데모 계정·외부 호출 0 | 확정 |
| [0035](0035-public-outputs-check.md) | 공개 산출물 검사: 우산 스크립트·원시세 정의·fixture 출처 표식 | 확정 |
| [0036](0036-gex-ingest-port.md) | GX 수집 경로 KBJ 이식: poller 서비스·분봉/KRX 파생/마스터/개장 확인 작업·쓰기 스풀·전환 순서 | 확정 |
| [0037](0037-ops-check-week-page13.md) | 운영 점검·1주 무중단 기준·페이지 13 최소판 | 확정 |
| [0038](0038-legacy-removal.md) | legacy 비우기: ET·SD 삭제, 다리(브리지·notifier shim) 제거, 직접 호출 검사 목표 0 | 확정 |

대체 관계: 0003(legacy 범위)·0007(legacy 브리지·기준선) → **0038**(legacy 비우기, P9). 보충: 0035 → 0011 §2-3, 0036 → 0029(D-P7-4 예외 종료),
0037 → 0006(등록부), 0018 → 0031(마감 규칙 알림도 16:00).
