"""연속 순매수 — 0원·순매도·거래정지에서 끊김, 오늘 정지면 0(docs/metrics.md §2·§5 '연속일')."""

from __future__ import annotations

from kbj.core.quality import Quality
from kbj.engines.flows.streak import buy_days, streak
from tests.unit.engines.flows.flow_rows import days, ledger_of, lrow

D = days(6)


def _desc(values: list[int | None], **kw) -> list:
    """오래된 날부터 준 값 → 최신부터(rows_desc 모양)."""
    rows = [lrow("A00001", D[i], foreign=v, **kw) for i, v in enumerate(values)]
    return list(reversed(rows))


def test_순매수_연속() -> None:
    assert streak(_desc([5, 3, 1, 2]), "foreign") == 4


def test_0원에서_끊긴다() -> None:
    assert streak(_desc([5, 0, 1, 2]), "foreign") == 2


def test_순매도에서_끊긴다() -> None:
    assert streak(_desc([5, -1, 1, 2]), "foreign") == 2


def test_값이_없는_날에서_끊긴다() -> None:
    assert streak(_desc([5, None, 1]), "foreign") == 1


def test_거래정지에서_끊긴다() -> None:
    rows = _desc([5, 6, 7])
    rows[1] = lrow("A00001", D[1], foreign=6, traded=False, flags=("halted",))
    assert streak(rows, "foreign") == 1


def test_오늘_거래정지면_0() -> None:
    rows = _desc([5, 6, 7])
    rows[0] = lrow("A00001", D[2], foreign=7, traded=False, flags=("halted",))
    assert streak(rows, "foreign") == 0


def test_invalid_행에서_끊긴다() -> None:
    rows = _desc([5, 6, 7])
    rows[1] = lrow("A00001", D[1], foreign=6, quality=Quality.INVALID)
    assert streak(rows, "foreign") == 1


def test_그날_행이_없으면_끊긴다() -> None:
    led = ledger_of([lrow("A00001", D[0], foreign=1), lrow("A00001", D[2], foreign=1)], D[:3])
    assert streak(led.rows_desc("A00001", D[2]), "foreign") == 1


def test_기관도_같은_규칙() -> None:
    rows = [lrow("A00001", D[i], inst=v) for i, v in enumerate([1, 2, 3])]
    assert streak(list(reversed(rows)), "inst") == 3


def test_순매수_일수는_0과_None_을_세지_않는다() -> None:
    rows = [lrow("A00001", D[i], foreign=v) for i, v in enumerate([10, 0, -5, None, 3])]
    rows.append(None)  # type: ignore[arg-type]
    assert buy_days(rows, "foreign") == 2
