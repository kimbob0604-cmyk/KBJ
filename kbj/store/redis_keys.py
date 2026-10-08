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
| `web:session:<sha256(sid) 32자>` | api 로그인(P3) | `{user, csrf, created, …}` | 12시간 |
| `web:login_fail:<sha256(ip) 16자>` | api 로그인 실패 카운터 | 실패 수 | 15분 창 |
| `web:login_fail:all`·`web:login_lock` | api 전체 실패 수·잠금 | 수·잠근 시각 | 1시간·30분 |
| `api:data_version:<영역>` | api 캐시(ops.data_claim 최근 done_at) | 버전 문자열 | 15초 |

웹 로그인 키(docs/p3_design.md §5.4)에는 세션 id·IP 원문을 넣지 않는다 — 부르는 쪽이 sha256 해시를
만들어 넘기고(`web_digest`), 이 모듈은 해시 모양(소문자 16진)만 받는다.
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

# ── 웹 로그인·API 캐시(P3 — docs/p3_design.md §5.3·§5.4) ─────────────────────────────────────
WEB_SESSION_PREFIX: Final = "web:session:"
WEB_LOGIN_FAIL_PREFIX: Final = "web:login_fail:"
WEB_LOGIN_FAIL_ALL: Final = "web:login_fail:all"  # 전체 1시간 실패 수
WEB_LOGIN_LOCK: Final = "web:login_lock"  # 전체 잠금(30분)
API_DATA_VERSION_PREFIX: Final = "api:data_version:"
_HEX = re.compile(r"[0-9a-f]+")
_DOMAIN = re.compile(r"[a-z][a-z0-9_]*")


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


def web_digest(value: str, length: int = 32) -> str:
    """세션 id·IP 를 키에 남길 때 쓰는 sha256 앞 `length` 자(원문은 키에 남기지 않는다)."""
    if not value:
        raise ValueError("빈 값의 해시는 만들지 않는다")
    if not 16 <= length <= 64:
        raise ValueError("length 는 16~64")
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def _digest_part(what: str, digest: str, length: int) -> str:
    if len(digest) != length or not _HEX.fullmatch(digest):
        raise ValueError(
            f"{what} 는 sha256 앞 {length}자(소문자 16진)여야 한다 — 원문을 넣지 않는다"
        )
    return digest


def web_session_key(digest: str) -> str:
    """로그인 세션 — `web:session:<sha256(sid) 앞 32자>`. sid 원문은 쿠키에만 있다."""
    return WEB_SESSION_PREFIX + _digest_part("세션 해시", digest, 32)


def web_login_fail_key(ip_digest: str) -> str:
    """IP 별 로그인 실패 수(15분 창) — `web:login_fail:<sha256(ip) 앞 16자>`."""
    return WEB_LOGIN_FAIL_PREFIX + _digest_part("IP 해시", ip_digest, 16)


def api_data_version_key(domain: str) -> str:
    """API 캐시 데이터 버전 — `api:data_version:market|board|flows|etf`."""
    if not _DOMAIN.fullmatch(domain):
        raise ValueError(f"영역 이름은 소문자 식별자: {domain!r}")
    return API_DATA_VERSION_PREFIX + domain
