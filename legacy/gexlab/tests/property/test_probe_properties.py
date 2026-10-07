from datetime import UTC, datetime, timedelta, timezone

from hypothesis import given
from hypothesis import strategies as st

from scripts.probe_common import classify_mtrt, session_of


@given(st.text(max_size=10))
def test_classify_mtrt_total(code: str) -> None:
    assert classify_mtrt(code) in ("YYYYMM", "YYMMWW", "unknown")


@given(
    st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2035, 1, 1)),  # noqa: DTZ001
    st.integers(min_value=-12, max_value=14),
)
def test_session_of_is_timezone_invariant(naive: datetime, offset_h: int) -> None:
    ts = naive.replace(tzinfo=UTC)
    other = ts.astimezone(timezone(timedelta(hours=offset_h)))
    assert session_of(ts) == session_of(other)
