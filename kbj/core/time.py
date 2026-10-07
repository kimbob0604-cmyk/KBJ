"""시각 도우미 — 벽시계 한 곳과 KST 변환 (설계 §1.3·§7.2, conflict_map §1.6).

- 벽시계를 읽는 함수는 `utcnow()` 하나다. 서비스 진입점(시계를 주입받지 못한 기본값 자리)에서만
  부르고, 판정·계산 함수는 시각을 인자로 받는다(CLAUDE.md §4 — 시험은 시계를 주입한다).
- naive datetime 은 받지 않는다. 어느 시간대인지 모르는 시각을 KST 로 짐작해 읽으면 UTC 로 도는
  서버(Render·컨테이너)에서 9시간이 조용히 어긋난다 — legacy 에서 실제로 난 일이다
  (SD `kis_api.py:_is_kr_market_hours` 의 naive `datetime.now()`).
- `KST` 는 `ZoneInfo("Asia/Seoul")`. legacy 의 `timezone(timedelta(hours=9))` 와 같은 순간을
  가리킨다(한국은 1988년 뒤 서머타임이 없다) — aware 끼리의 비교·뺄셈은 섞여도 맞다.
- legacy `now_kst` 10벌(ET 6·SD 4)의 대체는 `now_kst()` 다. 문자열을 돌려주던 벌(ET
  `datetime.now(KST).isoformat(timespec="seconds")`)은 부르는 쪽이 `.isoformat(timespec="seconds")`
  를 붙인다(설계 §7.2 — 묶음 H).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

KST: Final = ZoneInfo("Asia/Seoul")


def utcnow() -> datetime:
    """지금(UTC, aware). 벽시계를 읽는 유일한 함수 — 서비스 진입점의 기본 시계로만 쓴다."""
    return datetime.now(tz=UTC)


def _require_aware(ts: datetime) -> datetime:
    if not isinstance(ts, datetime):  # pyright: ignore[reportUnnecessaryIsInstance] — 실행 중 방어
        raise TypeError(f"datetime 을 넘겨야 한다({type(ts).__name__} 받음)")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime 은 받지 않는다: {ts!r}")
    return ts


def to_kst(ts: datetime) -> datetime:
    """같은 순간의 KST 시각(aware). naive 는 `ValueError`."""
    return _require_aware(ts).astimezone(KST)


def kst_date(ts: datetime) -> date:
    """그 순간의 KST 달력 날짜. 예: 2026-10-05 15:30 UTC → 2026-10-06."""
    return to_kst(ts).date()


def now_kst() -> datetime:
    """지금(KST, aware) — `to_kst(utcnow())`. legacy `now_kst` 대체용."""
    return to_kst(utcnow())
