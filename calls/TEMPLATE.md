---
# 이 파일을 calls/<YYYY>/<YYYY-MM-DD>-<코드>-<소문자-슬러그>.md 로 복사해 채운다(예: calls/2026/2026-10-27-000000-example-thesis.md).
# 시총은 '억' 단위 숫자(통화는 시장 기준 — KR 원·HK 홍콩달러·US 달러). 주가가 아니라 시총으로 쓴다.
# ★ 표시 칸은 커밋한 뒤 고치지 않는다(CI 가 막는다). 틀렸으면 '## 갱신' 에 적고, 판단이 바뀌면 콜을 닫고 새로 낸다.
id: 2026-10-27-000000-example-thesis   # ★ 파일 이름(확장자 뺀 것)과 같게
opened: 2026-10-27                      # ★ 콜을 낸 날(KST)
market: KR                              # ★ KR · HK · US
code: "000000"                          # ★ 국장 6자리는 따옴표로(앞자리 0)
name: 예시종목                           # 아래 값은 전부 예시
direction: long                         # ★ long · short
horizon_months: 12                      # ★ 판단 기간(개월)
sector: 반도체
valuation_method: PER+EV/EBITDA+DCF     # 선택 — 고성장 소재주 PER+EV/EBITDA+DCF · 자산형 PBR+DCF · 스토리형 EV/EBITDA+시나리오 · 기술주 PER+PBR+EV/EBITDA
entry_mcap_eok: 10000                   # ★ 진입 시총(억)
entry_mcap_as_of: 2026-10-27            # ★ 진입 시총 기준일(콜 날짜 이하, 7일 이내)
entry_mcap_source: KRX 종가 기준 시총(HTS 확인)  # 어디서 본 값인지
target_mcap_eok: 13000                  # ★ 목표 시총(억) — 롱은 진입보다 크게, 숏은 작게
benchmark: KOSPI                        # ★ KR: KOSPI·KOSDAQ·KOSPI200 / HK: HSI·HSTECH / US: SPX·NDX
kill_criteria:                          # ★ 이 중 하나라도 확인되면 틀린 것으로 보고 닫는다
  - 다음 분기 핵심 제품 출하가 회사 가이던스를 20% 넘게 밑돈다
  - 주요 고객사 수주가 취소·연기됐다는 공시가 나온다
catalysts:                              # 선택 — 확인할 일정
  - 다음 분기 실적 발표
status: open                            # open · closed(닫을 때 아래 세 칸을 함께 쓴다)
# closed: 2027-03-02
# close_mcap_eok: 12500
# close_reason: 목표 근접 + 킬 조건 1번 근접 — 절반 이상 반영으로 판단
---

## 논리

- 핵심 주장 한 줄
- 근거(숫자는 출처와 함께 — 추정이면 추정이라고 적는다)

## 밸류에이션

- 방법·가정·목표 시총 산출(억 단위)

## 갱신

<!-- 이 아래만 덧붙인다. 위(논리·밸류에이션)는 고치지 않는다. 형식: - 2026-11-15: 무엇이 바뀌었고 판단에 어떤 영향인지 -->
