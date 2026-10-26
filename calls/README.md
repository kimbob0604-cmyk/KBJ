# calls — 공개 투자 콜 기록

내가 낸 투자 판단을 파일 하나씩 남기는 곳이다. 레포가 공개라서 **커밋 시각이 곧 "그때 그렇게 판단했다"는 기록**이 된다.
맞힌 콜만 남기지 않는다 — 틀린 콜도 닫아서 남긴다. 결정 근거는 [`docs/adr/0042-public-call-log.md`](../docs/adr/0042-public-call-log.md),
성과 공식은 [`docs/metrics.md` §32](../docs/metrics.md).

## 콜 내기

1. [`TEMPLATE.md`](TEMPLATE.md) 를 `calls/<YYYY>/<YYYY-MM-DD>-<코드>-<소문자-슬러그>.md` 로 복사해 채운다.
2. 검사: `uv run python -m kbj.services.personal calls check`
3. 콜 날짜 당일(늦어도 3일 안)에 커밋·푸시한다. 3일 넘게 지난 날짜로 새 콜을 올리면 CI 가 소급 기록으로 막는다.

## 규칙

| 무엇 | 규칙 |
|---|---|
| 시총 | 주가가 아니라 **시총(억 단위)** 으로 쓴다. 통화는 시장 기준(KR 원·HK 홍콩달러·US 달러) |
| 판단 칸 | `opened`·`code`·`direction`·`horizon_months`·`entry_mcap_*`·`target_mcap_eok`·`benchmark`·`kill_criteria` 는 커밋 뒤 고치지 않는다 |
| 본문 | `## 갱신` 위(논리·밸류에이션)는 고치지 않는다. 바뀐 것은 `## 갱신` 아래에 날짜와 함께 덧붙인다 |
| 닫기 | `status: closed` + `closed`·`close_mcap_eok`·`close_reason` 을 함께 쓴다. 닫힌 콜은 다시 열지 않는다(새 콜을 낸다) |
| 지우기 | 낸 콜은 지우지 않는다 |
| 숫자 | 출처를 적고, 추정은 추정이라고 적는다(CLAUDE.md 절대 규칙 1·2) |

CI `calls` 잡이 푸시·PR 마다 직전 버전과 비교해 위 규칙을 검사한다(`calls check --against <기준>`).

## 공개 범위

- 콜 파일(논리·진입/목표 시총·킬 조건)은 **내가 쓴 판단**이라 공개다.
- 시세로 매일 계산하는 성과 시계열은 시세 원천(KRX·KIS)이 로그인 등급이라 레포·공개 사이트에 올리지 않는다 — 로그인판에서만 본다
  (`docs/DATA_TIERS.md`). 콜을 닫을 때 적는 `close_mcap_eok` 한 값은 내 기록이라 공개다.

## 목록

`uv run python -m kbj.services.personal calls list`
