# ADR 0015 — 컨센서스 출처 교체(네이버 → KIS), 잃는 것, 목표가 컨센서스·리비전 정의 (2026-10-07)

상태: **제안**(P4 설계 초안 — 묶음 K 가 구현하며 고치고, 묶음 S 가 확정한다). 출처 선택은 **[결정 필요 — U-P4-1]**.
근거: `docs/p4_design.md` D-P4-9·10, §4.7, 부록 A §10, ADR 0001 U4(네이버 스크래핑 금지), `docs/DATA_TIERS.md` §1(컨센서스 = 로그인),
`docs/conflict_map.md` §1.13(컨센서스 행), `docs/p2_design.md` R20, SD `consensus_collector.py`·`consensus_quarterly_collector.py`·
`consensus_snapshot_collector.py`·`revision_calculator.py`·`tam_modeler.py`.
구현 예정: `kbj/data/private/kis/consensus.py`, `kbj/engines/consensus/**`, `kbj/services/collectors/consensus.py`,
`kbj/data/private/kis/datasets.py`(`consensus_estimate`·`invest_opinion`), 마이그레이션 `0012_fin_prv.sql`.

## 1. 맥락

- legacy 컨센서스는 전부 네이버 스크래핑이다: 선행 EPS(`main.naver` 인라인), 분기 컨센서스(`tb_type1` 표 — `(E)` 열), 그 위의
  스냅샷·리비전·서프라이즈·TAM. U4 로 쓸 수 없고 FnGuide 스크래핑도 같은 이유로 안 된다.
- 대체 후보는 KIS 시세분석 TR(종목추정실적·종목투자의견·증권사별 투자의견). **레포 안에서도 TR id 가 어긋난다**:
  `kbj/data/private/kis/datasets.py`:214(투자의견 FHKST668300C0·추정실적 FHKST663300C0), `docs/conflict_map.md` §1.13(그 반대),
  `tests/fakes/kis_server.py`:76(FHKST663300C0 → `estimate-perform`). 아무것도 실측되지 않았다.
- KIS 추정실적이 여러 증권사 평균인지 한국투자증권 자체 추정인지, 분기 추정이 있는지 모른다.

## 2. 결정

1. **출처 = KIS, 로그인 등급, 합성 fixture 만.** 이 ADR 의 기본 TR 은 추정실적 `HHKST668300C0`(`…/quotations/estimate-perform`),
   투자의견 `FHKST663300C0`(`…/quotations/invest-opinion`), 대안 증권사별 `FHKST663400C0`(`…/quotations/invest-opbysec`) —
   **전부 [확인 필요]**(기억에 기댄 값). 실측(#29) 뒤 카탈로그·가짜 서버·이 ADR 을 한 커밋에 고친다.
2. **목표가 컨센서스는 우리가 계산**한다: 최근 90일 증권사별 마지막 목표가(> 0, 미제시 제외)의 평균·중앙값·최고·최저·수.
   증권사 3곳 이상 ok, 1~2곳 estimated.
3. **잃는 것을 지어내지 않는다**: 분기 컨센서스(→ 분기 어닝 서프라이즈는 계산하지 않고 전년동기·직전분기 대비 분류로 대신),
   컨센서스 최고·최저(→ 시나리오 Bull 은 Base × 1.2 폴백), 애널리스트 수, 12개월 선행 EPS 직접값(→ FY1·FY2 시간 가중 NTM 계산값),
   네이버 리서치 리포트 목록(폐지). 없는 칸은 '출처 없음'.
4. KIS 추정이 단일 증권사 추정으로 확인되면 화면·알림에서 **"컨센서스"라 부르지 않고 "KIS 추정"** 으로 적는다.
5. **리비전**: 지표 × 창(7·30일), 기준선 = t − W 이전 가장 늦은 스냅샷, pct = (현재 − 기준) ÷ |기준| × 100, 기준 0 → None,
   부호 전환 → `TURN`. 신호 경계 ±5·±15(SD 그대로). 관심종목 리비전만 하루 1통(`alert.revision`, `daily`).
6. **유니버스** = 관심종목 ∪ 전 거래일 시총 상위 300 보통주(18:30, 약 640호출 — KIS 4/s 로 약 3분). API 는 KIS 를 부르지 않으므로
   유니버스 밖 종목은 "수집 대상 아님".
7. 옛 SD SQLite 의 네이버 컨센서스·밴드·서프라이즈 행은 **이관하지 않는다**(p2 설계 §8.4 의 '표시만 남기고 이어 쌓기'를 바꾼다)
   [결정 필요 — U-P4-2].

## 3. 결과

- 네이버·FnGuide 호출 0(`check_canonical` 그룹 `naver` 축소, 신규 `fnguide` 목표 0).
- 분기 서프라이즈·Bull 컨센 최고값 등은 기능이 줄어든다 — 화면에 사유를 적는다. 유료 출처 계약 여부는 사용자가 정한다.
- 남은 [확인 필요]: TR id·경로·파라미터·필드·단위(억원?), 시장 컨센서스 여부, 분기 추정 유무, 커버리지(#29), 투자의견 코드 매핑.
