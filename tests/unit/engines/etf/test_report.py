"""ETF 리포트 엔진(kbj.engines.etf.report) — ET
`etf_tracker_v9/report.py` 섹션 본문 그대로 + 입력 조립.

ET 에는 리포트 원본 시험이 없었다(import 스모크뿐 — 설계 §1.12). 여기서 고정하는 것: 섹션 순서,
HTML 이스케이프(ETF 이름의 & < >), `_pack` 의 3,900자
묶기(4,096자 나눔 전 길이), 섹션을 쪼개지 않음,
변동 없는 날도 머리 한 줄, 입력 D 의 ET 규칙(동시 편입 이벤트·종목 사유 소멸·확대 겹침·비중 %p).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Change, EtfDay, EtfMeta, EtfType, Fund, SplitEvent
from kbj.engines.etf import report as R

BASE = date(2026, 11, 16)


def tdays(n: int) -> list[date]:
    out: list[date] = []
    d = BASE
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def day(code: str, d: date, close: float, shrs: int = 10**7, **kw: Any) -> EtfDay:
    base: dict[str, Any] = dict(
        code=code, date=d, name=None, close=close, nav=close, list_shrs=shrs,
        net_asset=int(shrs * close), turnover=10**10, volume=10**5, mktcap=int(shrs * close),
        base_index=None, source="krx", venue="KRX", quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return EtfDay(**base)


def meta(
    code: str, name: str, t: EtfType = EtfType.KR_THEME, listed: date | None = None
) -> EtfMeta:
    return EtfMeta(code, name, None, None, None, t, None, None, listed, None, "krx", Quality.OK)


def fund(fid: str, name: str, active: bool = False) -> Fund:
    return Fund(fid, "합성운용", fid, None, name, None, active, "full", True, 0, "ETF_ISSUERS:x")


def ch(fid: str, code: str, kind: str, **kw: Any) -> Change:
    base: dict[str, Any] = dict(
        run_date=BASE + timedelta(days=1), fund_id=fid, code=code, kind=kind, name=f"종목{code}",
        asof=BASE, prev_asof=BASE - timedelta(days=3), gap_days=3, prev_qty=None, cur_qty=None,
        prev_wt=None, cur_wt=None, qty_pct=None, qty_pct_adj=None,
    )  # fmt: skip
    base.update(kw)
    return Change(**base)


def make(
    changes: list[Change] | None = None,
    *,
    snaps: dict[tuple[str, date], frozenset[str]] | None = None,
    extra_days: dict[str, list[EtfDay]] | None = None,
    meta_: dict[str, EtfMeta] | None = None,
    splits: dict[str, list[SplitEvent]] | None = None,
    pair_count: int = 3,
) -> R.ReportInput:
    ds = tdays(30)
    days = {
        "990001": [day("990001", d, 10_000.0 + i * 10) for i, d in enumerate(ds)],
        "990002": [day("990002", d, 5_000.0 - i * 5) for i, d in enumerate(ds)],
        "990003": [day("990003", d, 8_000.0 + i, shrs=2 * 10**7 if d == BASE else 10**7)
                   for i, d in enumerate(ds)],
        "990004": [day("990004", d, 3_000.0 + i * 30) for i, d in enumerate(ds)],
    }  # fmt: skip
    days.update(extra_days or {})
    m = {
        "990001": meta("990001", "KODEX 200", EtfType.KR_INDEX),
        "990002": meta("990002", "합성 A&B <테마>"),
        "990003": meta("990003", "합성 유입"),
        "990004": meta("990004", "합성 레버리지", EtfType.LEVERAGED_INVERSE),
    }
    m.update(meta_ or {})
    funds = {
        f: fund(f, f"펀드{f}", active=f.startswith("A")) for f in ("A1", "A2", "P1", "P2", "P3")
    }
    return R.ReportInput(
        base=BASE, days=days, meta=m, run_date=BASE + timedelta(days=1), changes=changes or [],
        funds=funds, snaps=snaps or {}, pair_count=pair_count, splits=splits or {},
    )  # fmt: skip


SECTION_TITLES = [
    "📊 ETF 리포트",
    "🆕 신규 편입",
    "⚖️ 보유비중 변화",
    "🔁 여러 ETF가 같은 방향으로 움직인 종목",
    "❌ 제외된 종목",
    "📊 당일 등락",
    "📈 기간별 수익률",
    "💰 자금 유입·이탈",
    "🔥 거래 급증",
]


def full_changes() -> list[Change]:
    prev = BASE - timedelta(days=3)
    return [
        ch("A1", "X", "NEW", cur_wt=4.0),
        ch("A2", "X", "NEW", cur_wt=6.5),
        ch("P1", "Y", "ADD", prev_wt=2.0, cur_wt=3.5, qty_pct_adj=40.0),
        ch("P2", "Y", "ADD", prev_wt=1.0, cur_wt=1.2, qty_pct_adj=10.0),
        ch("P1", "Z", "DROP", prev_wt=1.0, prev_asof=prev),
        ch("P2", "Z", "DROP", prev_wt=1.0, prev_asof=prev),
        ch("P3", "Z", "DROP", prev_wt=1.0, prev_asof=prev),
    ]


def test_섹션_순서는_ET_그대로() -> None:
    inp = make(full_changes(), extra_days={
        "990005": [day("990005", d, 1_000.0, volume=10**6 if d == BASE else 10**5)
                   for d in tdays(30)],
    })  # fmt: skip
    text = "\n\n".join(R.build(R.assemble(inp)))
    pos = [text.find(t) for t in SECTION_TITLES]
    assert all(p >= 0 for p in pos), list(zip(SECTION_TITLES, pos, strict=True))
    assert pos == sorted(pos)


def test_HTML_이스케이프() -> None:
    msgs = R.build(R.assemble(make()))
    text = "\n".join(msgs)
    assert "합성 A&amp;B &lt;테마&gt;" in text
    assert "A&B <테마>" not in text
    assert R.esc(None) == "" and R.esc("<b>") == "&lt;b&gt;"


def test_변동_없는_날도_머리_한_줄() -> None:
    msgs = R.build(R.assemble(make([])))
    assert msgs and msgs[0].startswith("<b>📊 ETF 리포트</b>  2026-11-16")
    assert "3개 ETF 비교 — 감지된 변동 없음" in msgs[0]
    assert "🆕 신규 편입" not in msgs[0]
    first = R.build(R.assemble(make([], pair_count=0)))[0]
    assert "아직 비교할 이전 스냅샷이 없습니다" in first


def test_묶기는_3900자_안에서_섹션을_쪼개지_않는다() -> None:
    secs = ["가" * 2000, "나" * 2000, "다" * 1000]
    msgs = R._pack(secs)  # pyright: ignore[reportPrivateUsage]
    assert msgs == ["가" * 2000, "나" * 2000 + "\n\n" + "다" * 1000]
    assert all(len(m) <= R.LIMIT for m in msgs)
    long = "\n".join(
        f"줄{i:04d} " + "x" * 50 for i in range(200)
    )  # 한 섹션이 한도를 넘으면 줄 단위
    msgs = R._pack(["머리", long])  # pyright: ignore[reportPrivateUsage]
    assert msgs[0] == "머리" and all(len(m) <= R.LIMIT for m in msgs)
    assert "".join(msgs[1:]).rstrip("\n") == long


def test_큰_리포트도_메시지마다_4096자_안() -> None:
    many = [ch(f"A{i % 2 + 1}", f"S{i:03d}", "NEW", cur_wt=float(i % 9) + 0.5) for i in range(300)]
    msgs = R.build(R.assemble(make(many)), url="https://example.invalid/etf?a=1&b=2")
    assert len(msgs) >= 1 and all(len(m) <= 4096 for m in msgs)
    assert msgs[-1].endswith(
        '<a href="https://example.invalid/etf?a=1&amp;b=2">웹 대시보드에서 보기</a>'
    )


def test_동시편입과_종목사유_소멸_ET_규칙() -> None:
    prev = BASE - timedelta(days=3)
    snaps = {(f, prev): frozenset({"Z"}) for f in ("P1", "P2", "P3")}
    snaps |= {(f, BASE): frozenset() for f in ("P1", "P2", "P3")}
    d = R.assemble(make(full_changes(), snaps=snaps))
    x = d["h_new"][0]
    assert (x["code"], x["n"], x["funds"], x["wt"], x["event"]) == (
        "X", 2, ["펀드A1", "펀드A2"], 6.5, False,
    )  # fmt: skip
    assert d["h_out"] == []
    assert [(e["code"], e["n"], e["prior"]) for e in d["h_events"]] == [("Z", 3, 3)]
    mv = d["h_moves"]
    assert [(m["code"], m["kind"], m["n"]) for m in mv] == [("Y", "ADD", 2)]
    assert mv[0]["avg"] == pytest.approx(25.0)
    # 비중 0.5%p 이상만 — Y@P1 +1.5%p, Y@P2 +0.2%p 는 빠진다
    assert [(w["code"], w["fund"], round(w["dpp"], 2)) for w in d["wt_up"]] == [
        ("Y", "펀드P1", 1.5)
    ]
    assert [n["wt"] for n in d["new_detail"]] == [6.5, 4.0]


def test_다섯_곳_이상이_한꺼번에_담으면_이벤트() -> None:
    changes = [ch(f, "N", "NEW") for f in ("A1", "A2", "P1", "P2", "P3")]
    d = R.assemble(make(changes))
    assert d["h_new"][0]["event"] is True


def test_시장_칸_손계산() -> None:
    d = R.assemble(make())
    k = {lab: v for lab, v, _ in d["kpi"]}
    ds = tdays(30)
    want = ((10_000 + 29 * 10) / (10_000 + 28 * 10) - 1) * 100
    assert k["KODEX 200"] == f"{want:+.2f}%"
    assert d["flow_span"] == (ds[-2].isoformat(), BASE.isoformat())
    # 990003: 좌수 1천만 → 2천만, NAV 8029 — 순유입 = 1천만 × 8029 원 = 802.9억
    assert [(x["code"], round(x["amt"], 1), round(x["pct"], 1)) for x in d["flow_in"]] == [
        ("990003", 802.9, 100.0)
    ]
    # 레버리지(990004)는 당일 등락 '레버리지·인버스 제외' 목록에서 빠진다
    assert "990004" not in {x["code"] for x in d["up_nl"] + d["down_nl"]}
    assert "990004" in {x["code"] for x in d["up"]}
    assert "1개월" in d["periods"] and "1년" not in d["periods"]  # 창 30일 — 1년 기준일 없음


def test_분할일은_보정해_등락과_흐름을_낸다() -> None:
    ds = tdays(30)
    rows = [day("990009", d, 10_000.0 if d < BASE else 1_000.0, shrs=10**7 if d < BASE else 10**8)
            for d in ds]  # fmt: skip
    ev = SplitEvent("990009", BASE, 10.0, "manual", "", "manual", Quality.OK)
    d = R.assemble(make(extra_days={"990009": rows}, meta_={"990009": meta("990009", "분할")},
                        splits={"990009": [ev]}))  # fmt: skip
    mv = {x["code"]: x["chg"] for x in d["up"] + d["down"]}
    assert mv["990009"] == pytest.approx(0.0)
    assert "990009" not in {x["code"] for x in d["flow_in"] + d["flow_out"]}


def test_신규상장과_거래급증() -> None:
    ds = tdays(30)
    extra = {
        "990005": [day("990005", d, 1_000.0, volume=10**6 if d == BASE else 10**5) for d in ds],
        "990006": [day("990006", d, 2_000.0) for d in ds[-5:]],
    }
    d = R.assemble(
        make(extra_days=extra, meta_={"990006": meta("990006", "새 ETF", listed=ds[-5])})
    )
    assert [(s["code"], s["mult"]) for s in d["spikes"]] == [("990005", 10.0)]
    assert [(n["code"], n["first"]) for n in d["new_etf"]] == [("990006", ds[-5].isoformat())]


def test_전일_자료가_없으면_흐름_섹션은_안내() -> None:
    inp = make()
    one = R.ReportInput(
        base=BASE, days={c: [r for r in rs if r.date == BASE] for c, rs in inp.days.items()},
        meta=inp.meta, run_date=inp.run_date, changes=[], funds=inp.funds, snaps={},
        pair_count=0, notes=("KRX ETF 일별 지연",),
    )  # fmt: skip
    text = "\n".join(R.build(R.assemble(one)))
    assert "순자산 스냅샷이 하루치뿐입니다" in text
    assert "KRX ETF 일별 지연" in text
    assert "KODEX 200 –" in text
