# ADR 0018 — 장 마감 뒤 텔레그램은 전부 16:00 (2026-10-08)

상태: **채택**(2026-10-08 — 사용자 결정).
대체: ADR 0001 Q1 의 "16:40 마감 요약", ADR 0009 의 `board.daily` 16:20, ADR 0015 6 의 컨센서스 18:30, 그리고
`docs/p2_design.md` §5.2·§6.7·`docs/p3_design.md` §3.1·§3.2·`docs/p4_design.md` §3.1·§3.2·§3.6 의 마감 뒤 발송 시각.
같은 결정으로 원본 두 레포(stock-dashboard·ETF-Traker)도 먼저 바꿨다 — 세 레포가 같은 규칙이다.
구현: `config/jobs.yaml`(board.daily·brief.closing·flows.report·consensus.snapshot), `config/notify.yaml`(노트),
`kbj/services/scheduler/{registry,runner}.py`(무른 의존 `wait_min`), `kbj/services/api/readers/board.py`(안내 문구),
`web/src/pages/p2_board.ts`, 시험 `tests/unit/scheduler/{test_registry_validation,test_runner}.py`·`tests/sim/test_one_day.py`·
`test_notify_on.py`·`test_one_day_p3.py`.

## 1. 맥락

사용자 결정(2026-10-08, 강조): 장 끝난 뒤 나가는 텔레그램은 **모두 16:00**. 원본 SD 는 마감 요약·수급 시그널·리비전·AI 추천을
16:00 한 번에 보내도록 이미 바꿨다(그 저장소 커밋 "장 끝난 뒤 텔레그램을 전부 16:00 에 보낸다"). KBJ 등록부가 그것을 따라야 한다.

- 바꾸기 전 등록부의 마감 뒤 발송: `brief.closing` 16:40(캐치업 20:30), `flows.report` 18:20, `consensus.snapshot`
  (`alert.revision`) 18:30. 발송 재료인 `board.daily` 는 16:20.
- 재료는 16:00 에야 생긴다: `market.close_collect` 가 close+5(평소 15:35)에 시작해 KIS 약 5,700건 ≈ **24분**
  (`docs/p3_design.md` §3.7 — 추정)이라 평소 약 16:00 에 끝난다. 그 전에는 보드도, 마감 수급도 없다.
- 실행기에는 '의존이 끝나면 시작' 트리거가 없다(`triggered_by` 미구현 — D-P4-4). 굳은 의존은 기다리지만 의존이 실패하면
  같이 실패하고, 무른 의존은 기다리지 않았다 — 16:00 발송이 같은 16:00 보드보다 먼저 나갈 수 있었다.

## 2. 결정

1. **발송 작업 셋 = `cron "0 16 * * 1-5"`, `when: trading_day`**: `brief.closing`(캐치업 20:30 그대로, U2 하루 1회 그대로),
   `flows.report`, `consensus.snapshot`. 그대로 두는 것: 아침(`brief.morning` 08:10·`krx.daily` 의 `ops.universe` 08:05),
   장중(`rules.intraday`), 사건(`filings.dart_feed` 의 `alert.earnings`), 운영 감시(`ops.watchdog` 30분마다·실행기의
   `ops.job_failed`). 등록부 시험이 "KST 15:30 이후 첫 발화가 있는 발송 작업은 이 셋, 전부 16:00" 을 지킨다.
2. **`board.daily` = 16:00 cron + `market.close_collect` 굳은 의존**: 마감 수집이 16:00 전에 끝났으면 16:00 에 바로, 늦으면
   끝난 뒤 30초(`DEP_POLL_S`) 안에 시작한다. `equity close+5` 로 마감 수집과 같이 발화시키는 안은 버렸다 — equity 트리거는
   캐치업이 없어서, 15:35~16:00 사이에 scheduler 가 재기동하면 그날 보드 발화를 잃는다. 16:00 cron 은 그 사이 재기동에도 돈다.
3. **무른 의존 `wait_min`(실행기 — 새 필드)**: 굳은 의존이 다 준비된 때부터 최대 N분, 그 무른 의존의 같은 as_of 실행이
   실행기에서 **진행 중**(제 의존 대기·실행·재시도 대기)이면 끝나기를 기다린다. 끝나면 결과(ok·failed·timeout·skipped)를
   묻지 않고 시작하고, 시한이 지나면 기다리지 않고 시작한다 — 보드 실패가 발송을 막지 않는다(무른 의존 그대로).
   기준을 발화가 아니라 '굳은 의존 준비' 로 잡은 것은 수능일(마감 수집 16:35~)에도 보드를 기다리게 하려는 것이다.
   `brief.closing`·`flows.report` 는 `{job: board.daily, hard: false, wait_min: 20}` — 보드 재시도 한 번(10분)을 품는다.
   등록부 검증: 무른 의존만, `as_of: same` 만, external 의존 금지(진행 기록이 없다), 의존이 이 작업보다 늦게 발화하면 안 됨
   (아직 발화하지 않은 실행은 기다리지 않는다), `wait_min` < 이 작업의 마감.
4. **`consensus.snapshot` 은 16:00 에 수집하고 바로 보낸다**: 굳은 의존(`market.close_collect`)은 가격 때문이 아니다 — 유니버스는
   관심종목 ∪ **전 거래일** 시총 상위(ADR 0015 6). 같은 KIS 앱키 버킷(초당 4건)을 마감 수집과 겹치지 않게 차례를 잡는 것이라
   그대로 둔다. SD 처럼 14:30 수집·16:00 발송으로 나누지 않는다: 장중 슬롯 수집(`flows.intraday`·`market.intraday`)과 같은
   버킷을 나눠 쓰게 되고(`docs/p4_design.md` §3.6 "장중 버킷과 겹치지 않는다"), 등록부의 '작업 하나 = 알림 하나' 모양을
   깨야 한다. 약 640호출(4/s 로 약 3분)이라 리비전 알림은 평소 16:00~16:05.
5. **16:00 수급의 기준일을 밝힌다**(SD 2026-09-18 결정과 같은 원칙 — "16:00 수급은 오늘 값인 척 나가지 않는다"):
   `flows.report` 의 투자자 수급은 KIS 마감 수집(15:35~약 16:00) 값이라 **잠정일 수 있고**, 당일 행이 없는 종목은 **전 거래일**
   값이다. 리포트(P5)는 수급 섹션에 실제 기준일·원천·quality 를 적는다. 저녁 확정치로 다시 보내지 않는다(하루 1통).
   SD 기록으로 투자자별 확정치는 KRX 약 18:00·네이버 약 18:30 이다. KIS 가 15:35 에 당일 투자자 행을 주는지·저녁 값과
   같은지는 **[확인 필요]** — `docs/probe_results.md` §7 #22 에 더했다.
6. **도착 시각(정직하게)**: 발화는 16:00 이지만 실제 발송은 재료가 준비된 뒤다 — 평소 **16:00~16:05** [추정 — 마감 수집
   24분은 p3_design §3.7 추정, 보드 계산 시간은 실측 전]. 거래소 구분을 둘로 늘리면(마감 수집 약 48분) 16:25 전후, 수능일(정규장 16:30
   마감)은 17:00 전후. 보드가 `wait_min` 안에 끝나지 않으면 마감 요약은 보드를 '미준비'로 적고 나간다. 마감 수집이 끝내
   실패하면 굳은 의존이라 발송 작업도 실패한다(`ops.job_failed` — 이전과 같다).

## 3. 결과

- 시험: 등록부(장 마감 뒤 발송 = 셋·전부 16:00, 보드 굳은 의존·발송 무른 의존 `wait_min` 20, `wait_min` 규칙 위반이 잡힌다),
  실행기(`wait_min` — 보드가 끝날 때까지 잡아 두기·보드 실패여도 시작·시한에서 시작·`wait_min` 없으면 안 잡기·수능일처럼
  굳은 의존이 늦을 때 준비 시점부터 세기), 시뮬레이션(P2 하루 — 발송 셋 16:00·보드 뒤, 발송 켬 — 08:10·16:00·16:00,
  P3 하루 — 실제 처리기로 보드가 16:00 에 시작해 16:05 전에 끝나고 발송이 그 뒤).
- 16:00 에 KIS 를 쓰는 kbj 작업은 `consensus.snapshot`(P4 에 켬)뿐이고 마감 수집 뒤라 겹치지 않는다. legacy GX 주간 분봉
  (`gex.day_minutes` — POST_DAY, P7 까지 external)이 같은 앱키를 쓰면 리미터 우선순위(`config/limits.yaml` — 분봉 P4 < 마감·
  컨센 P3)대로 나눠 쓴다 — 몇 분 늘 수 있다 [확인 필요].
- 화면: 보드가 아직 없으면 "아직 없음 — board.daily 16:00~16:05".
- `docs/inventory.md`·`docs/conflict_map.md`(P0 목록)와 `legacy/` 사본의 옛 시각은 그때의 기록이라 고치지 않는다.
