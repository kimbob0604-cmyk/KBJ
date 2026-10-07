"""compose healthcheck — `python -m kbj.services.runtime.healthcheck <서비스> [최대 나이 초]`.

GEXLAB `services/healthcheck.py` 승격. Redis `health:heartbeat:<서비스>` 가 있고 그 시각이 최대 나이
(기본 60초) 안이면 0, 아니면 1. `KBJ_REDIS_URL` 은 환경변수(.env)에서만 읽는다. 아무것도 출력하지
않는다(접속 문자열이 새지 않게) — 실패 사유는 종료 코드 2(사용법·설정 없음·설정 오류)·1(하트비트
없음·오래됨·Redis 오류)로만 가른다.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from kbj.services.runtime.clock import utcnow
from kbj.services.runtime.heartbeat import connect_redis, heartbeat_age

DEFAULT_MAX_AGE_S = 60.0
CLOCK_SKEW_S = 5.0  # 하트비트 시각이 이만큼 미래여도 받아 준다(컨테이너 시계 차)


def check(
    redis: Redis,
    service: str,
    *,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    now: Callable[[], datetime] = utcnow,
) -> bool:
    try:
        age = heartbeat_age(redis, service, now())
    except (RedisError, ValidationError, ValueError):
        return False
    return age is not None and -CLOCK_SKEW_S <= age <= max_age_s


def main(argv: Sequence[str] | None = None, *, redis: Redis | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        return 2
    service = args[0]
    try:
        max_age = float(args[1]) if len(args) == 2 else DEFAULT_MAX_AGE_S
    except ValueError:
        return 2
    if redis is None:
        from kbj.config.settings import Settings

        try:
            url = Settings().redis_url
        except Exception:  # 설정 오류 문구에 값이 실릴 수 있어 싣지 않는다 — 종료 코드 2 로만
            return 2
        if url is None:
            return 2
        redis = connect_redis(url)
    return 0 if check(redis, service, max_age_s=max_age) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
