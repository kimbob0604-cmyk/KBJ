"""브리핑 시험 입력 — 합성 P5 세계(`p5_world`)를 메모리 저장소에 싣고, 마감 재료는 손으로 만든다.

실데이터 아님(합성 — CLAUDE.md 공개 레포 규칙). 시계는 주입한다(아침 2026-11-17 08:10,
마감 16:00 KST). US `us_brief` 산출의 payload 는 US 묶음이 정하는 모양 중 html(이미 막은
텔레그램 HTML)을 쓴다 [합성].
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from kbj.config.briefs import BriefsConfig, load_briefs
from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.fin_rows import DisclosureRow
from kbj.core.p5_rows import UsArtifact
from kbj.core.quality import Quality
from kbj.core.rows import (
    BoardArtifact,
    BoardDayRecord,
    IndexQuote,
    Investor,
    InvestorDay,
)
from kbj.core.time import KST
from kbj.reports.briefs.sections import BriefCtx
from kbj.store.repos.memory import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p5_world import TODAY, US_DAY, build_world

MORNING = datetime.combine(TODAY, time(8, 10), tzinfo=KST)
CLOSING = datetime.combine(TODAY, time(16, 0), tzinfo=KST)
SYN_INDEXES = (("^SYN1", "합성지수1"), ("^SYN2", "합성지수2"))
INDICES = {"0001": "코스피", "1001": "코스닥"}
BY = "test.briefs"


def cfg() -> BriefsConfig:
    return load_briefs()


def ctx(repos: Any, now: datetime, *, cfg_: BriefsConfig | None = None, **kw: Any) -> BriefCtx:
    base: dict[str, Any] = dict(
        repos=repos,
        now=now,
        day=now.astimezone(KST).date(),
        cfg=cfg_ or cfg(),
        kr=TradingCalendar.default(),
        us=us_calendar(),
        indices=dict(INDICES),
        bok_meetings=((date(2026, 11, 27), "금통위 통화정책방향 결정"),),
        importance_high=6,
        us_indexes=SYN_INDEXES,
    )
    base.update(kw)
    return BriefCtx(**base)


# ── 아침 ────────────────────────────────────────────────────────────────────────────────


def us_brief_artifact(day: date = US_DAY, *, q: Quality = Quality.OK) -> UsArtifact:
    at = datetime.combine(TODAY, time(6, 20), tzinfo=KST)
    return UsArtifact(
        trade_date=day,
        name="us_brief",
        payload={"html": "🇺🇸 <b>간밤 미국장 신고가</b> · 합성\n① 한눈에\n신고가 3종목"},
        engine_version="synthetic",
        input_digest="0" * 16,
        as_of=at,
        generated_at=at,
        source="nasdaq",
        quality=q,
    )


def morning_repos(*, us: bool = True, macro: bool = True, brief: bool = True) -> MemoryRepos:
    """p5_world 의 매크로(공개·로그인)·캘린더·미국 일봉 + US 산출을 실은 저장소."""
    w = build_world()
    r = memory_repos()
    now = MORNING
    if macro:
        pub = [m for m in w.macro_meta if m.tier == "public"]
        prv = [m for m in w.macro_meta if m.tier == "private"]
        r.macro.upsert_meta(pub, loaded_by=BY, now=now)
        r.macro_prv.upsert_meta(prv, loaded_by=BY, now=now)
        r.macro.upsert_series(w.macro_rows("public"), loaded_by=BY, received_at=now)
        r.macro_prv.upsert_series(w.macro_rows("private"), loaded_by=BY, received_at=now)
        r.macro.upsert_calendar(
            [e for e in w.calendar if e.is_public], loaded_by=BY, received_at=now
        )
        r.macro_prv.upsert_calendar(
            [e for e in w.calendar if not e.is_public], loaded_by=BY, received_at=now
        )
    if us:
        rows = [b for sym, _ in SYN_INDEXES for b in w.us_bars(sym)]
        r.us.upsert_daily(rows, loaded_by=BY, received_at=now)
    if brief:
        r.board.put_us_artifacts([us_brief_artifact()], loaded_by=BY)
    return r


# ── 마감 ────────────────────────────────────────────────────────────────────────────────


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=KST)


def newhigh_payload(day: date) -> dict[str, Any]:
    def row(code: str, name: str, label: str, chg: float) -> dict[str, Any]:
        return {
            "code": code, "name": name, "chg_pct": chg, "sector": "합성업종",
            "vol_mult": 3.0, "mktcap": 5000.0, "turnover": 300.0,
            "label": label, "hits": {label: True},
            "close_basis": {"label": label, "hits": {label: True}},
            "high_basis": {"label": label, "hits": {label: True}},
        }  # fmt: skip

    achieved = [
        row("997010", "합성역사", "hist", 4.1),
        row("997020", "합성오이", "w52", 2.5),
        row("997030", "합성오이B", "w52", 1.5),
        *[row(f"9971{i:02d}", f"합성백이십{i}", "d120", 1.0) for i in range(7)],
    ]
    return {
        "as_of": day.isoformat(),
        "counts_close": {"hist": 1, "w52": 3, "d120": 7},
        "achieved": achieved,
    }


def board_record(day: date, *, newhigh: bool = True) -> BoardDayRecord:
    def art(name: str, payload: dict[str, Any]) -> BoardArtifact:
        at = _at(day, 16, 3)
        return BoardArtifact(day, name, payload, "synthetic", "0" * 16,  # type: ignore[arg-type]
                             at, at, "kis", Quality.ESTIMATED)  # fmt: skip

    sectors = {
        "as_of": day.isoformat(),
        "sectors": [{"name": f"업종{i}", "chg_pct": round(3.0 - i * 0.5, 2)} for i in range(10)],
    }
    events = {
        "as_of": day.isoformat(),
        "events": [
            {"type": "volume_anomaly", "tickers": ["997010"], "severity": 5.5},
            {"type": "volume_anomaly", "tickers": ["997020"], "severity": 3.2},
            {"type": "material_giveback", "tickers": ["997030"], "severity": 2.0},
        ],
    }
    arts = [art("sectors", sectors), art("events", events)]
    if newhigh:
        arts.append(art("newhigh", newhigh_payload(day)))
    return BoardDayRecord(day, (), (), tuple(arts), "kis", Quality.ESTIMATED)


def closing_repos(
    *, flows_day: date = TODAY, board: bool = True, index: bool = True
) -> MemoryRepos:
    r = memory_repos()
    now = CLOSING
    if index:
        r.market.put_index_quotes(
            [
                IndexQuote("0001", _at(TODAY, 15, 20), "코스피", 2600.0, 0.21, None, None, "kis",
                           Quality.OK),
                IndexQuote("0001", _at(TODAY, 15, 30), "코스피", 2650.12, 0.53, None, None, "kis",
                           Quality.OK),
                IndexQuote("1001", _at(TODAY, 15, 30), "코스닥", 850.5, -0.4, None, None, "kis",
                           Quality.OK),
            ],
            loaded_by=BY, received_at=now,
        )  # fmt: skip
    mk = [
        InvestorDay(code, flows_day, inv, val, None, "kis", "KRX", Quality.ESTIMATED)
        for code in ("0001", "1001")
        for inv, val in (
            (Investor.INDIVIDUAL, -120_000_000_000),
            (Investor.FOREIGN, 80_000_000_000),
            (Investor.INSTITUTION, 40_000_000_000),
        )
    ]
    r.flows.upsert_market_days(mk, loaded_by=BY, received_at=now)
    days = [flows_day - timedelta(days=k) for k in (6, 5, 2, 1, 0)]
    stock: list[InvestorDay] = []
    for i, code in enumerate(("997010", "997020", "997030")):
        for j, d in enumerate(days):
            sign = 1 if (code != "997030" or j == len(days) - 1) else -1
            stock.append(InvestorDay(code, d, Investor.FOREIGN, sign * (i + 1) * 1_000_000_000,
                                     None, "kis", "KRX", Quality.ESTIMATED))  # fmt: skip
            stock.append(InvestorDay(code, d, Investor.INSTITUTION, -(i + 1) * 500_000_000,
                                     None, "kis", "KRX", Quality.ESTIMATED))  # fmt: skip
    r.flows.upsert_investor_days(stock, revise=False, loaded_by=BY, now=now, received_at=now)
    if board:
        r.board.put_day(board_record(TODAY), loaded_by=BY, now=now)
    r.filings.upsert_disclosures(
        [
            disclosure("20261117000001", "합성전자", "최대주주변경", 10),
            disclosure("20261117000002", "합성화학", "단일판매·공급계약체결", 6),
            disclosure("20261117000003", "합성바이오", "기업설명회(IR)개최", 1),
        ],
        loaded_by=BY,
        now=now,
    )
    return r


def disclosure(rcept_no: str, corp: str, title: str, score: int) -> DisclosureRow:
    return DisclosureRow(
        rcept_no=rcept_no, corp_code="00" + rcept_no[-6:], stock_code=None, corp_name=corp,
        corp_cls="Y", title=title, base_title=title, filer=None, rcept_dt=TODAY, remark=None,
        kind="other", is_correction=False, first_seen_at=_at(TODAY, 9), kw_score=score,
    )  # fmt: skip


__all__ = [
    "CLOSING",
    "MORNING",
    "SYN_INDEXES",
    "board_record",
    "cfg",
    "closing_repos",
    "ctx",
    "disclosure",
    "morning_repos",
    "us_brief_artifact",
]
