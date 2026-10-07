"""Redis 키·채널 이름 — 서비스끼리의 계약은 여기 한 곳에만 둔다(설계 §1.1·§3.2·§4.2·§5).

키 이름은 GEXLAB 과 **같게** 둔다(`kis:token`·`rl:kis:<해시>`·`krx:calls:<날짜>`·`session:state`·
`health:heartbeat:<서비스>`). P2~P7 동안 legacy GX(poller·ws-gateway·scheduler 분봉)가 같은
Redis 에서 같은 토큰·버킷·상태를 읽고 쓴다(설계 §3.2 끝).

- 비밀(앱키·봇 토큰·API 키)은 키 이름에 원문으로 넣지 않는다 — `rate_limit_key` 는 scope 를
  sha256 앞 16자로만 남긴다.
- 날짜가 든 키는 **KST 날짜**(`YYYYMMDD`)를 받는다. 시각 → KST 날짜 변환은 부르는 쪽(budget
  등)이 한다.
- 이 모듈은 아무것도 import 하지 않는다(저장소 계층의 바닥 — 계약 ⑦).

| 키·채널 | 쓰는 곳 → 읽는 곳 | 값 | 수명 |
|---|---|---|---|
| `kis:token` | auth → 모든 KIS REST 호출자 | `TokenRecord` JSON | 남은 수명 |
| `kis:token:issue_blocked_until` | auth(발급 간격 Lua) | 에포크 µs | 61초 |
| `kis:token:rejected` | KIS 호출자(`invalidate`) → auth | `{token_sha16, at, by}` | 1시간 |
| `kis:ws_key`·`…:issue_blocked_until` | auth → ws-gateway | 접속키·간격 | 12시간 가정·61초 |
| `rl:<출처>:<해시>`·`…:wait` | 레이트리미터(모든 호출자) | GCRA 상태 hash·대기 zset | 900초~ |
| `krx:calls:<YYYYMMDD>` | KRX 호출자 | 그날 호출 수 | 3일 |
| `budget:<출처>[:<범위>]:<날짜>`·`…:closed` | DART·DATAGO 등 | 호출 수·닫힌 사유 | 3일 |
| `session:state` / 채널 `session.events` | scheduler → 모두 | `SessionState` JSON | 없음 |
| `health:heartbeat:<서비스>` | 각 서비스 → healthcheck·watchdog | `Heartbeat` JSON | 서비스별 |
| `notify:dedup:<중복 키>` | notifier `Outbox.put`(SET NX) | 처음 넣은 시각 | 정책별 |
| 스트림 `notify:outbox` | 모든 발송자 → notifier | 보낼 메시지 | 소비 후 정리 |
| 스트림 `notify:inbound` | 웹훅 처리 → notifier 작업 스레드 | 받은 업데이트 | 소비 후 정리 |
| `notify:webhook_info` | notifier(10분 점검) → 운영 화면 | getWebhookInfo 요약 | 없음 |
| `tg:update:<update_id>` | 웹훅 처리(SET NX) | 1 | 48시간 |
| `sched:run:<작업>:<as_of>` | scheduler 실행기 | 실행 id | 작업 마감 |
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Final

# ── KIS 토큰·접속키 (auth 가 쓰고 나머지는 읽기만 — ADR 0004) ─────────────────────────────────
KIS_TOKEN: Final = "kis:token"  # noqa: S105 — Redis 키 이름일 뿐 비밀이 아니다
KIS_WS_KEY: Final = "kis:ws_key"
ISSUE_BLOCKED_SUFFIX: Final = ":issue_blocked_until"
KIS_TOKEN_REJECTED: Final = "kis:token:rejected"  # noqa: S105 — 거절 신고 키(§3.4 가드)

# ── 레이트리미터 ──────────────────────────────────────────────────────────────────────────────
RATE_LIMIT_PREFIX: Final = "rl:"
RATE_LIMIT_WAIT_SUFFIX: Final = ":wait"
_SOURCE = re.compile(r"[a-z][a-z0-9_]*")

# ── 일 예산 ─────────────────────────────────────────────────────────────────────────────────
KRX_CALLS_PREFIX: Final = "krx:calls:"
BUDGET_PREFIX: Final = "budget:"
BUDGET_CLOSED_SUFFIX: Final = ":closed"

# ── 세션 상태 (scheduler → 모두, GX services/bus.py 와 같은 이름) ─────────────────────────────
SESSION_STATE: Final = "session:state"
SESSION_EVENTS: Final = "session.events"  # pub/sub 채널

# ── 하트비트 ───────────────────────────────────────────────────────────────────────────────
HEARTBEAT_PREFIX: Final = "health:heartbeat:"

# ── 알림(notifier) ─────────────────────────────────────────────────────────────────────────
NOTIFY_OUTBOX: Final = "notify:outbox"  # Redis Stream
NOTIFY_INBOUND: Final = "notify:inbound"  # Redis Stream — 웹훅이 받은 업데이트
NOTIFY_DEDUP_PREFIX: Final = "notify:dedup:"
NOTIFY_WEBHOOK_INFO: Final = "notify:webhook_info"
TG_UPDATE_PREFIX: Final = "tg:update:"

# ── 스케줄러 ───────────────────────────────────────────────────────────────────────────────
SCHED_RUN_PREFIX: Final = "sched:run:"


def _ymd(day: date) -> str:
    return f"{day:%Y%m%d}"


def _part(what: str, value: str) -> str:
    """키 한 조각 — 비면 안 되고 공백을 담지 않는다(키 이름을 사람이 읽고 grep 할 수 있게)."""
    if not value or any(c.isspace() for c in value):
        raise ValueError(f"{what} 는 공백 없는 비지 않은 문자열이어야 한다: {value!r}")
    return value


def issue_blocked(key: str) -> str:
    """발급 간격 키 — `kis:token` → `kis:token:issue_blocked_until`(GX
    `RedisTokenCache.issue_key`)."""
    return _part("key", key) + ISSUE_BLOCKED_SUFFIX


def secret_digest(value: str) -> str:
    """비밀값을 키에 남길 때 쓰는 sha256 앞 16자(원문은 키에 남기지 않는다)."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def rate_limit_key(source: str, scope: str) -> str:
    """`rl:<출처>:<sha256(scope) 16자>` — KIS 는 scope = 앱키(GX `limiter_key` 와 같은 값).

    scope 에 키 원문(앱키·API 키·봇 토큰)을 넣어도 된다 — 해시만 남는다.
    """
    if not _SOURCE.fullmatch(source):
        raise ValueError(f"출처 이름은 소문자 식별자여야 한다: {source!r}")
    if not scope:
        raise ValueError("scope 가 비었다")
    return f"{RATE_LIMIT_PREFIX}{source}:{secret_digest(scope)}"


def rate_limit_wait_key(limiter_key: str) -> str:
    """대기열 zset — `rl:kis:<해시>:wait`."""
    return limiter_key + RATE_LIMIT_WAIT_SUFFIX


def krx_calls_key(day: date) -> str:
    """KST 날짜 하루의 KRX 호출 수(GX `services/bus.py:krx_calls_key` 와 같다)."""
    return KRX_CALLS_PREFIX + _ymd(day)


def budget_key(source: str, scope: str | None, day: date) -> str:
    """일 예산 카운터 — `budget:dart:20261006`, `budget:datago:15100475:20261006`."""
    if not _SOURCE.fullmatch(source):
        raise ValueError(f"출처 이름은 소문자 식별자여야 한다: {source!r}")
    mid = "" if scope is None else _part("scope", scope) + ":"
    return f"{BUDGET_PREFIX}{source}:{mid}{_ymd(day)}"


def budget_closed_key(counter_key: str) -> str:
    """그날 닫힌 예산의 사유 키 — 카운터 키 + `:closed`(공공데이터포털 GW `22`·DART `020`)."""
    return _part("counter_key", counter_key) + BUDGET_CLOSED_SUFFIX


def heartbeat_key(service: str) -> str:
    return HEARTBEAT_PREFIX + _part("service", service)


def notify_dedup_key(key: str) -> str:
    """발송 중복 키(정책이 만든 `{kind}:{as_of}…`)의 Redis 키."""
    return NOTIFY_DEDUP_PREFIX + _part("key", key)


def tg_update_key(update_id: int) -> str:
    """텔레그램 `update_id` 중복 제거 키(SET NX, 48시간)."""
    if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
        raise ValueError(f"update_id 는 0 이상 정수여야 한다: {update_id!r}")
    return f"{TG_UPDATE_PREFIX}{update_id}"


def sched_run_lock(job: str, as_of: str) -> str:
    """같은 (작업, as_of) 를 두 번 돌리지 않게 하는 실행 잠금 키."""
    return f"{SCHED_RUN_PREFIX}{_part('job', job)}:{_part('as_of', as_of)}"
