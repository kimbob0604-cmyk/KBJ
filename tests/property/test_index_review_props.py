"""지수 정기변경 엔진 속성 시험(docs/p5_index_rebalance_prompt.md §4, metrics §24).

- 반올림: 결과는 그 띠 단위의 배수, raw 와의 차이는 반 단위 이하(올림은 한 단위 미만), 완충 안이면
  직전 값. 완충 경계는 '<'(GIMI 2026-08판 §3.1.7): 변화 = 단위면 반영, 단위 바로 아래면 유지.
- 배수(GIMI §3.1.5·§3.1.6): 시총과 무관하고, 구성·세그먼트·유동비율 하한·Light Rebalancing 으로만
  정해진다.
- FOL foreign room 계수: room 이 늘면 편입 가능을 잃지 않고, 계수도 줄지 않는다(신규·기존 모두).
- 필요 상승률(역산): r 만큼 오르면 조건을 넘고, r − 1bp 면 넘지 못한다.
- 배수 경계: 같음은 충족, 1원 모자라면 미충족.
- 판정 단조: 컷오프 고정에서 시총이 오르면 판정이 나빠지지 않는다(구성·비구성 모두).
- 컷오프 추정: 동차(모두 k 배 → k 배), 커버리지가 오르면 컷오프는 오르지 않는다, 한 종목 시총이
  오르면 그
  종목의 '컷오프 이상' 여부는 나빠지지 않는다. KRX 순위: 시총이 오르면 순위가 나빠지지 않는다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.config.index_reviews import load_index_reviews
from kbj.engines.index_review.cutoff import coverage_cutoff
from kbj.engines.index_review.fif import band_of, room_factor, round_fif
from kbj.engines.index_review.krx import predict_krx
from kbj.engines.index_review.msci import evaluate, multipliers
from kbj.engines.index_review.required import BP, meets, required_rise, threshold
from kbj.engines.index_review.types import OUTCOME_ORDER, Cutoff, StockIn

CFG = load_index_reviews()
RULES = CFG.msci_rules("msci_korea")
RULE_NEW = CFG.fif_rule_for(date(2026, 11, 11))
RULE_OLD = CFG.fif_rule_for(date(2026, 2, 11))

ratio = st.integers(min_value=1, max_value=10_000).map(lambda i: i / 10_000)  # 0.0001~1
won = st.integers(min_value=10**9, max_value=10**14)


@settings(max_examples=400, deadline=None)
@given(raw=ratio, old=st.booleans())
def test_rounding_is_a_multiple_of_the_band_step(raw: float, old: bool) -> None:
    rule = RULE_OLD if old else RULE_NEW
    got, how = round_fif(raw, rule)
    b = band_of(raw, rule)
    step = Decimal(str(b.step))
    g, r = Decimal(str(got)), Decimal(str(raw))
    assert how == "rounded"
    assert 0 < g <= 1
    if g < 1 and g != step:
        assert g % step == 0
    if b.mode == "nearest":
        assert abs(g - r) <= step / 2 or g == step
    else:
        assert Decimal(0) <= g - r < step or g == 1


@settings(max_examples=300, deadline=None)
@given(raw=ratio, prev=ratio)
def test_buffer_keeps_previous_value_inside_the_band(raw: float, prev: float) -> None:
    got, how = round_fif(raw, RULE_NEW, prev)
    buf = band_of(raw, RULE_NEW).buffer
    assert buf is not None
    inside = abs(Decimal(str(raw)) - Decimal(str(prev))) < Decimal(str(buf))
    assert (how == "buffer") is inside
    if inside:
        assert got == prev


# 띠마다 (직전 값 하한·상한(단위 배수 칸), 단위) — 직전·raw 가 같은 띠에 있게
BANDS = ((Decimal("0.3"), Decimal("0.95"), Decimal("0.025")),
         (Decimal("0.06"), Decimal("0.24"), Decimal("0.005")),
         (Decimal("0.002"), Decimal("0.048"), Decimal("0.001")))  # fmt: skip


@settings(max_examples=300, deadline=None)
@given(band=st.integers(0, 2), k=st.integers(0, 10_000), up=st.booleans())
def test_buffer_edge_is_strict(band: int, k: int, up: bool) -> None:
    """변화 = 단위면 반영(rounded), 단위 − 0.0001 이면 직전 값 유지(buffer)."""
    lo, hi, step = BANDS[band]
    n = int((hi - lo) / step)
    prev = lo + step * (k % (n + 1))
    sign = 1 if up else -1
    at = prev + sign * step
    below = prev + sign * (step - Decimal("0.0001"))
    got, how = round_fif(float(at), RULE_NEW, float(prev))
    assert how == "rounded" and Decimal(str(got)) == at
    got, how = round_fif(float(below), RULE_NEW, float(prev))
    assert how == "buffer" and Decimal(str(got)) == prev


@settings(max_examples=300, deadline=None)
@given(mcap=won, k=st.integers(101, 400), fif=ratio, member=st.booleans(),
       seg=st.sampled_from([None, True, False]))  # fmt: skip
def test_multipliers_follow_gimi_and_ignore_mcap(
    mcap: int, k: int, fif: float, member: bool, seg: bool | None
) -> None:
    a = _stock("000001", mcap, fif, member, lower_segment=seg)
    b = _stock("000001", mcap * k // 100, fif, member, lower_segment=seg)
    got = multipliers(a, fif, RULES)
    assert got == multipliers(b, fif, RULES)
    low = fif < RULES.val("min_fif_add")
    if member:
        assert got == (RULES.val("keep_mcap_mult"),
                       RULES.val("low_fif_ff_mult_keep" if low else "keep_ff_mult"))  # fmt: skip
    else:
        migrate = seg is None or seg  # 모르면 segment_default = migrate(1.5×)
        assert got[0] == RULES.val("add_mcap_mult_migrate" if migrate else "add_mcap_mult_new")
        assert got[1] == RULES.val("low_fif_ff_mult_add" if low else "add_ff_mult")
    lr = RULES.light_rebalance
    assert lr is not None and not lr.enabled  # 기본 꺼짐
    on = RULES.model_copy(update={"light_rebalance": lr.model_copy(update={"enabled": True})})
    if not member:
        assert multipliers(a, fif, on) == (1.8, 0.9)


@settings(max_examples=300, deadline=None)
@given(lim=st.integers(50, 1000), h1=st.integers(0, 10_000), h2=st.integers(0, 10_000),
       member=st.booleans())  # fmt: skip
def test_more_foreign_room_never_lowers_factor(lim: int, h1: int, h2: int, member: bool) -> None:
    limit = lim / 1000
    a, b = sorted((h1, h2))
    held_lo, held_hi = limit * a / 10_000, limit * b / 10_000  # held_lo → room 이 더 크다
    f_big, ok_big, _ = room_factor(CFG.fol, limit, held_lo, member=member)
    f_small, ok_small, _ = room_factor(CFG.fol, limit, held_hi, member=member)
    assert ok_big or not ok_small  # 여유가 늘면 편입 가능을 잃지 않는다
    if ok_small:  # 편입 불가(신규 room < 15%)면 계수는 쓰이지 않는다
        assert f_big >= f_small
    assert 0 < f_small <= 1 and 0 < f_big <= 1


@settings(max_examples=500, deadline=None)
@given(mcap=won, fif=ratio, cutoff=won, member=st.booleans())
def test_required_rise_inverts_exactly(mcap: int, fif: float, cutoff: int, member: bool) -> None:
    m_mult, f_mult = multipliers(_stock("000001", mcap, fif, member), fif, RULES)
    thr_m, thr_f = threshold(m_mult, cutoff), threshold(f_mult, cutoff)
    r, basis = required_rise(mcap, fif, thr_m, thr_f)
    assert meets(mcap, fif, thr_m, thr_f, r)  # 그만큼 오르면 넘는다
    assert not meets(mcap, fif, thr_m, thr_f, r - BP)  # 1bp 모자라면 못 넘는다
    assert meets(mcap, fif, thr_m, thr_f) is (r <= 0)
    assert basis in ("mcap", "ff")


@settings(max_examples=200, deadline=None)
@given(cutoff=won)
def test_multiple_boundary_equal_meets_one_won_less_fails(cutoff: int) -> None:
    thr = threshold(1.0, cutoff)
    assert meets(cutoff, None, thr, None)
    assert not meets(cutoff - 1, None, thr, None)


def _stock(
    code: str, mcap: int, fif: float, member: bool, *, lower_segment: bool | None = None,
    sector: str | None = None,
) -> StockIn:  # fmt: skip
    return StockIn(code=code, name=None, market="KOSPI", kind="common", sector=sector,
                   listed_on=None, mktcap=mcap, adv_won=1e10, fif_raw=fif, fif_prev=None,
                   fif_source=None, flags=(), member=member,
                   lower_segment=lower_segment)  # fmt: skip


@settings(max_examples=400, deadline=None)
@given(mcap=won, k=st.integers(min_value=101, max_value=400), fif=ratio, cutoff=won,
       member=st.booleans())  # fmt: skip
def test_verdict_never_worsens_when_mcap_rises(
    mcap: int, k: int, fif: float, cutoff: int, member: bool
) -> None:
    c = Cutoff(cutoff, None, None, "user", "user")
    lo = evaluate(_stock("000001", mcap, fif, member), fif, "rounded", c, RULES, 1)
    hi = evaluate(_stock("000001", mcap * k // 100, fif, member), fif, "rounded", c, RULES, 1)
    assert OUTCOME_ORDER[hi.outcome] >= OUTCOME_ORDER[lo.outcome]
    assert (hi.required_rise or 0) <= (lo.required_rise or 0)


items_st = st.lists(
    st.tuples(st.integers(min_value=1, max_value=10**6), ratio), min_size=1, max_size=60
).map(lambda xs: [(f"{i:06d}", m, Decimal(m) * Decimal(str(f))) for i, (m, f) in enumerate(xs)])


@settings(max_examples=300, deadline=None)
@given(items=items_st, k=st.integers(min_value=2, max_value=50))
def test_cutoff_is_homogeneous(items: list[tuple[str, int, Decimal]], k: int) -> None:
    a = coverage_cutoff(items, 0.85)
    b = coverage_cutoff([(c, m * k, f * k) for c, m, f in items], 0.85)
    assert a is not None and b == a * k


@settings(max_examples=300, deadline=None)
@given(items=items_st, lo=st.integers(1, 98), hi=st.integers(1, 98))
def test_more_coverage_never_raises_cutoff(
    items: list[tuple[str, int, Decimal]], lo: int, hi: int
) -> None:
    lo, hi = min(lo, hi), max(lo, hi)
    a, b = coverage_cutoff(items, lo / 100), coverage_cutoff(items, hi / 100)
    assert a is not None and b is not None and b <= a


@settings(max_examples=300, deadline=None)
@given(items=items_st, pick=st.integers(min_value=0), bump=st.integers(1, 10**6))
def test_raising_one_stock_never_drops_it_below_cutoff(
    items: list[tuple[str, int, Decimal]], pick: int, bump: int
) -> None:
    i = pick % len(items)
    code, m, f = items[i]
    before = coverage_cutoff(items, 0.85)
    fif = f / m
    moved = [*items[:i], (code, m + bump, Decimal(m + bump) * fif), *items[i + 1 :]]
    after = coverage_cutoff(moved, 0.85)
    assert before is not None and after is not None
    if m >= before:
        assert m + bump >= after


@settings(max_examples=60, deadline=None)
@given(caps=st.lists(st.integers(1, 10**4), min_size=12, max_size=30, unique=True),
       pick=st.integers(min_value=0), bump=st.integers(1, 10**4))  # fmt: skip
def test_krx_rank_and_condition_never_worsen(caps: list[int], pick: int, bump: int) -> None:
    rules = CFG.krx_rules("kospi200")
    rules = rules.model_copy(update={"size": rules.size.model_copy(update={"v": 8})})
    i = pick % len(caps)

    def run(cs: list[int]) -> dict[str, tuple[int | None, bool | None]]:
        # 두 산업군(짝·홀) — 산업군 안 순위·누적 시총 비중 규칙도 단조여야 한다
        stocks = [_stock(f"{j:06d}", c * 10**9, 0.5, j < 8, sector="AB"[j % 2])
                  for j, c in enumerate(cs)]  # fmt: skip
        res = predict_krx(stocks, rules, ("KOSPI",), date(2026, 10, 30))
        return {o.code: (o.rank, o.condition_met) for o in res.stocks}

    a = run(caps)
    b = run([*caps[:i], caps[i] + bump, *caps[i + 1 :]])
    code = f"{i:06d}"
    ra, ma = a[code]
    rb, mb = b[code]
    assert ra is not None and rb is not None and rb <= ra
    assert not (ma and mb is False)
