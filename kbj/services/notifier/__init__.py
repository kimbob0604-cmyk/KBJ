"""notifier — 텔레그램 발송·수신(웹훅)의 유일한 곳(ADR 0001 U1·U2, 설계 §5).

| 모듈 | 하는 일 |
|---|---|
| `client` | 넣기 — 작업용 `notify*`, legacy 7벌 대신 `legacy_send*`, `webhook_status` |
| `policy` | `config/notify.yaml` 해석 — 토픽·종류·중복 키·쿨다운·캐치업 |
| `outbox` | Redis 대기열 `notify:outbox` + 중복 문지기 `notify:dedup:<키>` |
| `service`·`__main__` | 소비·발송(꺼짐 기본 → `suppressed`)·기록·받은 업데이트·웹훅 점검 |
| `telegram_api` | 텔레그램 Bot API 를 부르는 유일한 파일(호스트 이름도 여기에만) |
| `format` | 4,096자 분할·`(i/n)` 머리·캡션 상한 |
| `webhook` | 웹훅 순수 처리(요청 dict → 응답)·명령/인박스 갈래 — HTTP 서버는 P3(D4) |
| `commands` | 명령 분배(`/도움` 만 P2 동작) |
| `inbox`·`store` | 인박스 파싱(ET `tg_inbox` 계약)·발송 기록·인박스 저장소 |

하위 모듈을 여기서 import 하지 않는다 — legacy 가 `kbj.services.notifier.client` 만 가볍게 쓰도록.
"""
