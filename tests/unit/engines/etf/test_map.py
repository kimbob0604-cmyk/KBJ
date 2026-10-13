"""ETF 맵(kbj.engines.etf.map) — 승격 시험 + 손계산.

원본: ET `monitor/kr/tests/test_etf.py`(이 레포
legacy/etf_traker). 승격한 것은 묶음·기간·유니버스 단언
(`유니버스.test_순자산과_거래대금_하한`,
`조립.test_묶음이_..._탭을_따른다`·`test_국내테마_묶음에_해외ETF가_안_
들어온다`·`test_받은게_없으면_...`·`test_일봉이_모자란_ETF는_뺀다`)이다.
분류 단언(`분류.*` — 이름에서
운용사·레버리지·헤지·합성·액티브·탭 자산군 읽기)은 **폐지 목록**이다 — 분류 정본은
`kbj.engines.etf.types`(P3 `test_types.py`)이고 두 번째
사본을 만들지 않는다(D-P5-13 ①). 괴리율·상대강도·
YTD·화면 계약(`items`·`flow`·`overlap`)은 ET 화면 전용 칸이라 맵에 없다(괴리율은 P3 premium).

kbj 와 다른 점(의도):
- '일봉이 모자란 ETF' 는 ET 처럼 빼지 않고 **신규 상장(회색 — 수익률 None)** 으로 남긴다(설계 §4.4).
- 자산군은 네이버 탭코드가 아니라 metrics §8.1 유형(국내주식 = 국내 대표지수·국내 테마).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfType, SplitEvent
from kbj.engines.etf import map as M


def days(n: int = 80) -> list[date]:
    """연속 영업일 n 개(주말 제외)."""
    out: list[date] = []
    d = date(2026, 11, 16)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def row(code: str, d: date, close: float, **kw: object) -> EtfDay:
    base: dict[str, object] = dict(
        code=code, date=d, name=None, close=close, nav=close, list_shrs=1_000_000,
        net_asset=10**11, turnover=10**10, volume=10**5, mktcap=10**11, base_index=None,
        source="krx", venue="KRX", quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return EtfDay(**base)  # pyright: ignore[reportArgumentType]


def meta(code: str, name: str, t: EtfType | None = None, **kw: object) -> EtfMeta:
    base: dict[str, object] = dict(
        code=code, name=name, issuer=None, brand=None, theme=None, etf_type=t, leverage=None,
        base_index=None, listed_on=None, delisted_on=None, source="krx", quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return EtfMeta(**base)  # pyright: ignore[reportArgumentType]


DATES = days()
LISTING = [
    ("990001", "KODEX 200"),
    ("990002", "TIGER 200"),
    ("990003", "KODEX 반도체"),
    ("990004", "TIGER 반도체TOP10"),
    ("990005", "KODEX 레버리지"),
    ("990006", "KODEX 인버스"),
    ("990007", "TIGER 미국S&P500"),
    ("990008", "KODEX 국고채3년"),
]


def world() -> tuple[dict[str, EtfMeta], dict[str, list[EtfDay]]]:
    m = {c: meta(c, n) for c, n in LISTING}  # 유형·테마 비움 → 정본 함수가 이름에서 읽는다
    d = {
        c: [row(c, x, 10_000.0 + i + j) for j, x in enumerate(DATES)]
        for i, (c, _) in enumerate(LISTING)
    }
    return m, d


# ── 유니버스(ET 유니버스.test_순자산과_거래대금_하한) ──────────────────────────────────


def item(code: str, **kw: object) -> M.Item:
    base: dict[str, object] = dict(
        code=code, name=code, etf_type=EtfType.KR_INDEX, theme=None, net_asset=10**11,
        turnover=10**10, close=1000.0, ret_pct=1.0, leveraged=False, new_listing=False,
        asof=DATES[-1], source="krx", quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return M.Item(**base)  # pyright: ignore[reportArgumentType]


def test_순자산과_거래대금_하한() -> None:
    items = {
        "a": item("a"),
        "b": item("b", net_asset=10**8),  # 순자산 1억 — 미달
        "c": item("c", turnover=10**6),  # 거래대금 100만 — 미달
        "d": item("d", close=None),  # 시세 없음
    }
    keep, ex = M.filter_universe(items)
    assert list(keep) == ["a"]
    assert (ex.no_data, ex.illiquid, ex.small, ex.total) == (1, 1, 1, 3)


def test_경계값은_포함한다() -> None:
    keep, _ = M.filter_universe(
        {"a": item("a", net_asset=M.MIN_NET_ASSET, turnover=float(M.MIN_TURNOVER))}
    )
    assert list(keep) == ["a"]


# ── 묶음(ET 조립.*) ──────────────────────────────────────────────────────────────────


def test_유형_묶음이_정본_유형을_따른다() -> None:
    m, d = world()
    cells, _ = M.cells(m, d, DATES, "1M", lev=True)
    groups = M.build_groups(cells, "type")
    keys = {g.key for g in groups}
    assert {"kr_index", "kr_theme", "overseas", "bond_cash", "leveraged_inverse"} <= keys
    label = {g.key: g.label for g in groups}
    assert label["kr_index"] == "국내 대표지수" and label["bond_cash"] == "채권·현금"
    # 묶음 순서 = 구성 수 내림차순(같으면 키)
    sizes = [(-len(g.cells), g.key) for g in groups]
    assert sizes == sorted(sizes)


def test_국내테마_묶음에_해외ETF가_안_들어온다() -> None:
    m, d = world()
    cells, _ = M.cells(m, d, DATES, "1M", lev=True)
    members = {c.code for g in M.build_groups(cells, "theme") for c in g.cells}
    assert "990007" not in members, "해외 ETF 가 국내테마에 들어왔다"
    assert "990008" not in members, "채권 ETF 가 국내테마에 들어왔다"
    assert "990005" not in members and "990006" not in members  # 레버리지·인버스는 국내주식 아님
    assert {"990001", "990003", "990004"} <= members


def test_받은게_없으면_빈_결과() -> None:
    m, d = world()
    assert M.build_items(m, d, [], "1M") == {}
    assert M.build_items({}, {}, DATES, "1M") == {}
    assert M.build_groups([], "type") == []


def test_일봉이_모자란_ETF는_신규상장_회색() -> None:
    """ET 는 뺐지만 맵은 회색 + 꼬리표로 남긴다(수익률 None —
    상장 뒤 수익률을 기간 수익률로 쓰지 않는다)."""
    m, d = world()
    m["999999"] = meta("999999", "신규상장 테마")
    d["999999"] = [row("999999", x, 100.0) for x in DATES[-5:]]
    items = M.build_items(m, d, DATES, "1M")
    assert items["999999"].new_listing and items["999999"].ret_pct is None
    assert not items["990001"].new_listing
    # 1D 는 이력 5일이면 신규 상장이 아니다
    assert not M.build_items(m, d, DATES, "1D")["999999"].new_listing


def test_메타_상장일이_있으면_그것으로_신규상장을_판단한다() -> None:
    m, d = world()
    m["990001"] = meta("990001", "KODEX 200", listed_on=DATES[-3])
    assert M.build_items(m, d, DATES, "1W")["990001"].new_listing


def test_레버리지_인버스는_기본_숨김() -> None:
    m, d = world()
    hidden, _ = M.cells(m, d, DATES, "1M")
    shown, _ = M.cells(m, d, DATES, "1M", lev=True)
    assert {"990005", "990006"} & {c.code for c in hidden} == set()
    assert {"990005", "990006"} <= {c.code for c in shown}
    assert all(c.leveraged for c in shown if c.code in ("990005", "990006"))


def test_저장된_유형과_테마를_먼저_쓴다() -> None:
    m, d = world()
    m["990001"] = meta("990001", "KODEX 200", EtfType.KR_THEME, theme="반도체")
    it = M.build_items(m, d, DATES, "1M")["990001"]
    assert (it.etf_type, it.theme) == (EtfType.KR_THEME, "반도체")


# ── 기간 수익률(ET engine.period_return 규칙) ─────────────────────────────────────────


def test_기간_수익률_손계산() -> None:
    ds = DATES[-30:]
    closes = {x: 100.0 + i for i, x in enumerate(ds)}
    # 1W = 5 영업일 전: 기준 = ds[-6] = 124, 오늘 = 129
    assert M.period_return(closes, ds, 5) == pytest.approx((129 / 124 - 1) * 100)


def test_기준일이_비면_이전_관측일로_물러난다() -> None:
    ds = DATES[-30:]
    closes = {x: 100.0 + i for i, x in enumerate(ds)}
    del closes[ds[-6]]
    assert M.period_return(closes, ds, 5) == pytest.approx((129 / 123 - 1) * 100)


def test_구간_결측이_한도를_넘으면_None() -> None:
    ds = DATES[-40:]
    closes = {ds[0]: 100.0, ds[-1]: 110.0}
    assert M.period_return(closes, ds, 30) is None  # 사이 결측 > 20
    assert M.period_return({ds[0]: 1.0}, ds, 5) is None  # 오늘 관측 없음
    assert M.period_return(closes, ds[:3], 5) is None  # 창이 짧다


def test_분할_보정_종가로_수익률을_낸다() -> None:
    """1:10 분할 — 효력일 앞 종가를 10 으로 나눠 이어 붙인다(metrics §8.2)."""
    ds = DATES[-10:]
    rows = [row("990001", x, 10_000.0 if i < 7 else 1_010.0) for i, x in enumerate(ds)]
    ev = SplitEvent("990001", ds[7], 10.0, "manual", "", "manual", Quality.OK)
    adj = M.adjusted_closes(rows, [ev])
    assert adj[ds[0]] == pytest.approx(1_000.0) and adj[ds[-1]] == 1_010.0
    items = M.build_items(
        {"990001": meta("990001", "KODEX 200")}, {"990001": rows}, ds, "1W", splits={"990001": [ev]}
    )
    assert items["990001"].ret_pct == pytest.approx(1.0)


def test_셀_손계산() -> None:
    m, d = world()
    cells, ex = M.cells(m, d, DATES, "1M", lev=True)
    c = next(x for x in cells if x.code == "990003")  # i=2: 종가 10002 + j
    j_last = len(DATES) - 1
    want = ((10_002 + j_last) / (10_002 + j_last - 21) - 1) * 100
    assert c.ret_pct == pytest.approx(want, abs=1e-4)
    assert (c.net_asset, c.period, c.asof, c.etf_type) == (10**11, "1M", DATES[-1], "kr_theme")
    assert ex.total == 0 and len(cells) == len(LISTING)


def test_보고_순자산이_없으면_좌수_곱하기_NAV() -> None:
    ds = DATES[-3:]
    rows = [row("990001", x, 100.0, net_asset=None, list_shrs=10**8, nav=100.0) for x in ds]
    it = M.build_items({}, {"990001": rows}, ds, "1D")["990001"]
    assert it.net_asset == 10**10


def test_모르는_기간과_묶음은_거부() -> None:
    with pytest.raises(ValueError):
        M.build_items({}, {}, DATES, "6M")  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError):
        M.build_groups([], "brand")  # pyright: ignore[reportArgumentType]
