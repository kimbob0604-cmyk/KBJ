import pytest

from services.ws_gateway.backoff import Backoff


def test_doubles_up_to_60s_and_resets() -> None:
    b = Backoff()
    assert [b.next_delay() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    b.reset()
    assert b.next_delay() == 1


def test_jitter_stays_within_band_and_cap() -> None:
    lo = Backoff(jitter=0.2, rand=lambda: 0.0)
    hi = Backoff(jitter=0.2, rand=lambda: 1.0)
    assert lo.next_delay() == pytest.approx(0.8)
    assert hi.next_delay() == pytest.approx(1.2)
    capped = Backoff(jitter=0.2, rand=lambda: 1.0, attempt=10)
    assert capped.next_delay() == 60  # 상한을 넘지 않는다


@pytest.mark.parametrize(
    "kwargs",
    [{"initial": 0}, {"factor": 0.5}, {"initial": 10, "maximum": 5}, {"jitter": 1.0}],
)
def test_rejects_bad_parameters(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        Backoff(**kwargs)  # type: ignore[arg-type]
