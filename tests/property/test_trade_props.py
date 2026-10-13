"""탐색·단가 대 물량 속성(metrics §26·§27 — hypothesis, 묶음 EXP):

- 로그 분해 항등식: `ln_value = ln_price + ln_volume`(오차 < 1e-9).
- 접두 합 = 상위 합: 일관된 계층(상위 = 하위 합)에서 한 층 아래 접두 값의 합이 상위 값과 같다.
- 이중 합산 금지: 상위 행을 지우거나 남겨도 접두 값이 같다(상위·하위가 함께 와도 더하지 않는다).
- 0 중량: 단가 None, 분해 None(0 으로 채우지 않는다).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import ItemMonthRow
from kbj.engines.trade.explore import item_index
from kbj.engines.trade.price_volume import decompose, unit_price

T = datetime(2026, 12, 11, 11, 30, tzinfo=KST)
YM = "2026-10"
POS = st.integers(min_value=1, max_value=10**12)

# 잎(HS10) 몇 개 — 같은 장·HS4 를 나눠 갖게 작은 알파벳에서 고른다
LEAF = st.builds(
    lambda a, b, c, d: f"{a}{b}{c}{d}000000"[:10],
    st.sampled_from(["84", "85"]),
    st.sampled_from(["01", "02"]),
    st.sampled_from(["10", "20"]),
    st.sampled_from(["10", "20"]),
)


def _row(hs: str, usd: int, kg: int) -> ItemMonthRow:
    return ItemMonthRow(
        ym=YM, hs=hs, hs_len=len(hs), exp_usd=Decimal(usd), exp_kg=Decimal(kg), imp_usd=None,
        imp_kg=None, bal_usd=None, source="DATAGO:15101609", as_of=T, quality=Quality.OK,
    )  # fmt: skip


def _tree(leaves: dict[str, tuple[int, int]], keep_upper: set[int]) -> list[ItemMonthRow]:
    """잎 값으로 일관된 상위 행을 만든다(keep_upper 의 자릿수만 상위 행으로 남긴다)."""
    acc: dict[str, list[int]] = {}
    for leaf, (u, k) in leaves.items():
        for n in (2, 4, 6, 8):
            if n in keep_upper:
                a = acc.setdefault(leaf[:n], [0, 0])
                a[0] += u
                a[1] += k
    rows = [_row(h, u, k) for h, (u, k) in leaves.items()]
    rows += [_row(h, a[0], a[1]) for h, a in acc.items()]
    return rows


@settings(max_examples=200, deadline=None)
@given(POS, POS, POS, POS)
def test_로그_분해_항등식(v: int, q: int, v0: int, q0: int) -> None:
    d = decompose(Decimal(v), Decimal(q), Decimal(v0), Decimal(q0))
    assert d is not None
    assert abs(d.ln_price + d.ln_volume - d.ln_value) < 1e-9


@settings(max_examples=150, deadline=None)
@given(
    st.dictionaries(LEAF, st.tuples(POS, POS), min_size=1, max_size=12),
    st.sets(st.sampled_from([2, 4, 6, 8])),
)
def test_접두_합은_상위_합·이중_합산_없음(
    leaves: dict[str, tuple[int, int]], keep: set[int]
) -> None:
    full = item_index(_tree(leaves, {2, 4, 6, 8}), "exp")
    part = item_index(_tree(leaves, keep), "exp")
    for p in {lf[:n] for lf in leaves for n in (2, 4, 6)}:
        whole = full.value(p, YM)
        assert whole is not None
        assert part.value(p, YM) == whole  # 상위 행이 있든 없든 같은 값
        kids = {lf[: len(p) + 2] for lf in leaves if lf.startswith(p)}
        total = sum((full.usd(k, YM) or Decimal(0)) for k in kids)
        assert total == whole.usd
    assert full.usd("84", YM) is None or full.usd("84", YM) == sum(
        Decimal(u) for lf, (u, _) in leaves.items() if lf.startswith("84")
    )


@settings(max_examples=100, deadline=None)
@given(POS, POS, POS)
def test_0_중량(v: int, v0: int, q0: int) -> None:
    assert unit_price(Decimal(v), Decimal(0)) is None
    assert decompose(Decimal(v), Decimal(0), Decimal(v0), Decimal(q0)) is None
    assert decompose(Decimal(v), Decimal(q0), Decimal(v0), Decimal(0)) is None
