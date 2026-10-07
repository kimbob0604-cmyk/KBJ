# ADR 0005 — 알림 발송: outbox·중복 키·발송 기록 분리·발송 꺼짐 기본 (2026-10-07)

상태: **확정**(2026-10-07 — P2 묶음 E, `docs/p2_design.md` §5, ADR 0001 U1·U2, 메인 결정 D4). §2.2 '설계 표와
달라진 점'(쿨다운 미끄러지는 창·legacy 는 부른 곳마다 하루 1회)은 설계서 §5.2(끝 '전환 기간 legacy 규칙')·§5.3(문지기
키 / 발송 기록 키)·§5.9 에 고쳐 적었다.
구현: `kbj/services/notifier/{client,policy,outbox,service,telegram_api,format,webhook,commands,inbox,store,__main__}.py`,
`config/notify.yaml`, 시험 `tests/unit/notifier/`. 표는 묶음 G 의 `0002_ops_core.sql`(`ops.notify_log`)·
`0005_alerts_inbox.sql`(`prv_alerts.notify_message`·`prv_alerts.tg_inbox`).

## 1. 맥락

- 텔레그램 발송이 7벌(SD 3·ET 3·bok 1), 중복 방지 표식이 6군데(SD `ops_state`·`_alert_cooldown_ok`, ET
  `sent.json` 3종, bok `telegram-sent.json`, Actions 캐시)였다. 봇은 하나를 같이 쓴다(U1).
- U2: 아침 브리핑 1회·마감 요약 1회. 수신은 웹훅 하나(getUpdates 폐지).
- D4: FastAPI 는 P3. P2 웹훅은 순수 처리 함수로 만들고 시험한다.

## 2. 결정

### 2.1 넣는 쪽과 보내는 쪽을 나눈다 (outbox)

- 모든 발송자(kbj 작업·legacy shim)는 `client.notify*`/`legacy_send*` 로 Redis 스트림 `notify:outbox` 에
  **넣기만** 한다. 텔레그램(`api.telegram.org`)을 부르는 것은 notifier 서비스의 `telegram_api.py` 하나다
  (설계 §11.4 grep 검사). 봇 토큰은 notifier 컨테이너에만 둔다.
- 첨부는 경로가 아니라 바이트로 싣는다(넣은 순간의 내용, 컨테이너 간 파일 공유 없음). 상한 20 MiB [제안].
- 스트림에는 MAXLEN 을 두지 않는다 — 소비 후 XDEL 하므로 남은 것은 안 보낸 것뿐이고, 자르면 조용히 잃는다.

### 2.2 중복 방지 두 겹

| 겹 | 어디 | 규칙 |
|---|---|---|
| 1차 | Redis `notify:dedup:<키>` — `put` 의 Lua 한 번(문지기 확인 + XADD 원자적) | 부른 쪽이 그 자리에서 `duplicate`·`cooldown` 을 안다 |
| 2차 | `ops.notify_log.dedup_key` UNIQUE — notifier 가 보내기 전에 `begin` | 문지기 키가 만료된 뒤 다시 온 같은 메시지 |

키 규칙(`config/notify.yaml` `dedup`): `daily` `{kind}:{as_of}`(36시간) · `cooldown` `{kind}:{subject}`(cooldown_s) ·
`subject` `{kind}:{subject}`(30일) · `body` `{kind}:{as_of}:{sha256 16자}`(24시간).

설계 표와 달라진 점:
- **쿨다운은 미끄러지는 창**: 설계의 `{kind}:{subject}:{floor(now/cooldown_s)}` 를 문지기로 쓰면 창 경계
  (10:59·11:01)에서 2분 만에 두 번 나간다. 문지기는 `{kind}:{subject}`(마지막 발송부터 cooldown_s), `floor`
  붙은 키는 발송 기록의 유일 키로만 쓴다.
- **문지기 만료는 주입한 시계로 판정**한다(값 = '언제까지' µs, PX 수명은 청소용). 가짜 시계 시험·하루
  시뮬레이션에서도 쿨다운이 풀린다.
- **subject 가 없으면 본문 해시가 대상**이다 — legacy 처럼 대상을 모르는 알림이 한 쿨다운에 묶이지 않게.
- **전환 기간 legacy 는 '부른 곳(origin)'마다 하루 1회**: SD 의 아침 함수 3개(#3·#4·#5)가 모두 `brief.morning`
  으로 매겨져도 함수마다 따로 센다(본문 합치기는 P5·P3 — 그전에 legacy 내용을 조용히 버리지 않는다). kind 를
  넘긴 legacy 호출(ET ETF `send_report(msgs)` 여러 통·ET flow 종목별 차트 — `etf.report`·`flows.report` 는
  `daily`)은 한 번에 여러 통을 보내므로 origin 에 내용 해시를 붙여 같은 내용의 되풀이(재실행)만 막는다 — 안 그러면
  둘째 통부터 duplicate 로 빠지는데 legacy 는 `(True, '중복')` 을 받아 보낸 줄 안다. kbj 작업은 origin 없이
  부르므로 종류당 하루 1회(U2)가 그대로 선다.
- **거절 기록도 한 줄씩**: `dedup_key` 열이 `NOT NULL UNIQUE` 라 `<원래 키>#<상태>#<대기열 항목 id>` 로 적는다.

### 2.3 발송 기록 분리

- `ops.notify_log`(운영 — 메타만: 종류·토픽·기준일·대상·키·본문 해시·글자 수·조각 수·상태·사유·메시지 id·
  부른 곳·시각)와 `prv_alerts.notify_message`(로그인·개인 — 본문)로 나눈다(ADR 0002 "가장 높은 등급").
- 기록을 못 쓰면 **보내지 않고** 미룬다(기록 없는 발송·2차 검사 없는 발송 금지). 이미 보낸 뒤 기록에 실패하면
  다시 보내지 않고 기록만 나중에 쓴다.

### 2.4 발송 꺼짐이 기본 (`KBJ_NOTIFY_ENABLED=false`)

- notifier 는 대기열을 소비하고 정책·중복 판정까지 다 한 뒤 텔레그램을 부르지 않는다 → `suppressed`.
- 꺼짐 모드의 **외부 HTTP 는 0** 이다: 발송·getWebhookInfo·인박스 oEmbed 모두 부르지 않는다(oEmbed 를 못 채운
  인박스 항목은 ET 계약의 '못 채움' `text_via=null` 그대로 — 지어내지 않는다). 시험
  `tests/unit/notifier/test_disabled_mode.py` 가 고정한다.
- shim 반환: legacy `(False, "발송 꺼짐(KBJ_NOTIFY_ENABLED=false) — 기록만")`, kbj `NotifyTicket(ok=True,
  reason="suppressed")`.

### 2.5 수신 — 순수 처리 함수 (D4)

- `WebhookHandler.handle(요청 dict) -> WebhookResult` — 시크릿 `hmac.compare_digest`(비밀 미설정이면 모두 503),
  `update_id` 중복은 `tg:update:<id>`(48시간)와 `notify:inbound` XADD 를 Lua 한 번에. Redis 장애는 503(텔레그램이
  다시 보낸다 — 받았다고 하고 잃지 않는다). 갈래(명령/인박스/버림)는 notifier 가 스트림을 읽어 처리한다.
- HTTP 서버는 P3 API 가 이 함수를 감싼다. 그래서 `setup-webhook` 은 `--confirm` 없이는 걸지 않는다(받을 서버가
  없을 때 텔레그램이 24시간 재전송하다 버리지 않게). 전환 순서는 설계 §5.11.

### 2.6 실패 규칙

- 네트워크·5xx·429 초과는 `delivery.max_attempts`(3)까지 두 배 간격 재시도 — 나눠 보낸 조각은 이어서(이미 간
  조각은 다시 보내지 않는다. 이어 보낼 자리는 앞에서부터 센 절대 위치다). 보냈거나 실패로 정한 뒤 대기열 확인
  (XACK)이 Redis 장애로 실패해도 다시 읽은 항목을 또 보내지 않고 기록만 마친다. 그 밖 4xx·조각 상한·캐치업 시한(`brief.closing` 20:30)은 바로 `failed`.
  끝내 실패하면 문지기 키를 풀어 부른 쪽이 다시 넣을 수 있게 한다.
- 텔레그램이 서식 오류(`can't parse entities`)로 거절하면 같은 본문을 평문으로 한 번 더(SD earnings 발송기의 처리).

## 3. 결과

- legacy 발송 7벌은 묶음 H 가 shim(`legacy_send*`)으로 바꿨다. 7곳 정적 검사
  (`tests/unit/notifier/test_client_shim.py::test_legacy_shim_targets_call_legacy_send`)는 이제 표시 없이 통과한다.
  `check_canonical` 그룹 `telegram`(api.telegram.org)은 legacy 0건.
- `kind` 를 안 넘긴 legacy 호출은 호출 스택의 함수 이름 → `legacy_kinds`(전환 기간 한정 — SD 14개 + ET `cmd_us_send`
  → `board.us`·flow `send_text` → `flows.report`). 표에 없는 호출은 `legacy.other`(운영 토픽). 메시지를 kbj 작업으로
  옮길 때(P3~P5) 함수와 함께 지운다.
- 남은 [확인 필요]: 토픽 thread id(슈퍼그룹 생성 뒤), 첨부 상한 20 MiB, 대화당 1/s(그룹 20/분) 한도.
