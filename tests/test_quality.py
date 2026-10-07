"""kbj.core.quality — 값에 source·as_of·quality 를 붙이는 모델 (CLAUDE.md 절대 규칙 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from kbj.core.quality import Quality, Sourced

KST = timezone(timedelta(hours=9))
AS_OF = datetime(2026, 10, 6, 15, 30, tzinfo=KST)


def make(value: float = 1.5, quality: Quality = Quality.OK) -> Sourced[float]:
    return Sourced[float](value=value, source="ECOS:817Y002", as_of=AS_OF, quality=quality)


def test_quality_has_exactly_four_values() -> None:
    assert [q.value for q in Quality] == ["ok", "stale", "estimated", "invalid"]


def test_quality_from_string_and_usable() -> None:
    assert Quality("stale") is Quality.STALE
    assert [q.usable for q in Quality] == [True, True, True, False]
    with pytest.raises(ValueError):
        Quality("unknown")


def test_sourced_carries_all_fields() -> None:
    s = make()
    assert (s.value, s.source, s.as_of, s.quality) == (1.5, "ECOS:817Y002", AS_OF, Quality.OK)
    assert s.usable


def test_quality_accepts_string_from_db_row() -> None:
    row = {"value": 2.0, "source": "DART", "as_of": AS_OF, "quality": "estimated"}
    s = Sourced[float].model_validate(row)
    assert s.quality is Quality.ESTIMATED


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Sourced[float](value=1.0, source="DART", as_of=datetime(2026, 10, 6), quality=Quality.OK)  # noqa: DTZ001


@pytest.mark.parametrize("source", ["", "   "])
def test_blank_source_is_rejected(source: str) -> None:
    with pytest.raises(ValidationError):
        Sourced[float](value=1.0, source=source, as_of=AS_OF, quality=Quality.OK)


def test_missing_field_and_extra_field_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Sourced[float].model_validate({"value": 1.0, "source": "DART", "as_of": AS_OF})
    with pytest.raises(ValidationError):
        Sourced[float].model_validate(
            {"value": 1.0, "source": "DART", "as_of": AS_OF, "quality": "ok", "note": "x"}
        )


def test_value_type_is_validated() -> None:
    with pytest.raises(ValidationError):
        Sourced[int](value="열", source="DART", as_of=AS_OF, quality=Quality.OK)  # type: ignore[arg-type]


def test_frozen() -> None:
    s = make()
    with pytest.raises(ValidationError):
        s.quality = Quality.INVALID


def test_with_quality_returns_new_value() -> None:
    s = make()
    t = s.with_quality(Quality.INVALID)
    assert s.quality is Quality.OK
    assert t.quality is Quality.INVALID and not t.usable
    assert (t.value, t.source, t.as_of) == (s.value, s.source, s.as_of)


def test_json_round_trip_keeps_timezone() -> None:
    s = make()
    back = Sourced[float].model_validate_json(s.model_dump_json())
    assert back == s
    assert back.as_of.utcoffset() == timedelta(hours=9)
    assert '"quality":"ok"' in s.model_dump_json()


class TestAged:
    def test_ok_becomes_stale_when_older_than_max_age(self) -> None:
        now = AS_OF + timedelta(hours=2)
        assert make().aged(now, timedelta(hours=1)).quality is Quality.STALE

    def test_boundary_is_not_stale(self) -> None:
        now = AS_OF + timedelta(hours=1)
        assert make().aged(now, timedelta(hours=1)).quality is Quality.OK

    def test_compares_instants_across_timezones(self) -> None:
        # 15:30 KST == 06:30 UTC. 07:00 UTC 는 30분 뒤
        now = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
        assert make().aged(now, timedelta(minutes=29)).quality is Quality.STALE
        assert make().aged(now, timedelta(minutes=30)).quality is Quality.OK

    @pytest.mark.parametrize("q", [Quality.STALE, Quality.ESTIMATED, Quality.INVALID])
    def test_other_qualities_are_kept(self, q: Quality) -> None:
        now = AS_OF + timedelta(days=30)
        assert make(quality=q).aged(now, timedelta(hours=1)).quality is q

    def test_naive_now_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            make().aged(datetime(2026, 10, 7), timedelta(hours=1))  # noqa: DTZ001

    def test_negative_max_age_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            make().aged(AS_OF, timedelta(seconds=-1))

    @given(
        offset_s=st.integers(min_value=-(10**6), max_value=10**6),
        max_age_s=st.integers(min_value=0, max_value=10**6),
    )
    def test_property_stale_iff_older(self, offset_s: int, max_age_s: int) -> None:
        now = AS_OF + timedelta(seconds=offset_s)
        got = make().aged(now, timedelta(seconds=max_age_s)).quality
        assert (got is Quality.STALE) == (offset_s > max_age_s)
