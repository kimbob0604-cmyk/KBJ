"""처리기 `flows.report`(16:00 — ADR 0018) — kbj 원장(메모리 저장소)에서 대상·수급·일봉을 읽어
보낸다.

새 시험(KBJ P5 묶음 BRF — docs/p5_design.md §1.5·§4.5). 입력은 고정 시드 합성(실데이터 아님):
보드 newhigh 산출(`prv_board.artifact`)·종목 투자자 일별(`prv_flows.stock_investor_daily`)·일봉
(`prv_market.daily_bar`). 원천 KRX 스크랩·KIS 직접 호출은 없다(계약 ⑯).
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import Bar, BoardArtifact, BoardDayRecord, Investor, InvestorDay
from kbj.core.time import KST
from kbj.reports.flows import daily
from kbj.reports.flows import run as R
from kbj.services.scheduler.handlers import JobContext, resolve
from kbj.store.repos.memory import MemoryRepos, memory_repos

DAY = date(2026, 11, 17)
NOW = datetime.combine(DAY, time(16, 2), tzinfo=KST)
CODE, NAME = "997010", "합성전자"
BY = "test.flows"
CAL = TradingCalendar.default()


def trading_days(end: date, n: int) -> list[date]:
    out = [end]
    while len(out) < n:
        out.append(CAL.prev_trading_day(out[-1]))
    return out[::-1]


def board_row(code: str, name: str, kind: str = "w52", vol_mult: float | None = 3.2) -> dict:
    on = {kind: True}
    return {"code": code, "name": name, "vol_mult": vol_mult, "mktcap": 32000.0,
            "turnover": 1840.0, "label": kind, "hits": on,
            "close_basis": {"label": kind, "hits": on},
            "high_basis": {"label": kind, "hits": on}}  # fmt: skip


def seed(
    *, flows_end: date = DAY, achieved: list[dict] | None = None, inst7: bool = True
) -> MemoryRepos:
    rnd = random.Random("flows:test")  # noqa: S311 — 합성 데이터 생성(보안 용도 아님)
    r = memory_repos()
    days = trading_days(DAY, 300)
    px, bars = 10_000.0, []
    for d in days:
        o = px
        px = max(1000.0, px * (1 + rnd.gauss(0.001, 0.02)))
        bars.append(Bar(CODE, d, o, max(o, px) * 1.01, min(o, px) * 0.99, round(px),
                        rnd.randint(100_000, 900_000), None, "krx", "KRX", Quality.OK))  # fmt: skip
    r.market.upsert_daily_bars(bars, loaded_by=BY, received_at=NOW)
    close = {b.date: b.close for b in bars}
    rows: list[InvestorDay] = []
    for d in [x for x in days if x <= flows_end][-70:]:
        q = Quality.ESTIMATED if d == DAY else Quality.OK
        src = "kis" if d == DAY else "krx"
        parts = {Investor.TRUST: rnd.randint(-5000, 9000),
                 Investor.PENSION: rnd.randint(-4000, 4000),
                 Investor.FIN_INVEST: rnd.randint(-3000, 3000)}  # fmt: skip
        inst = sum(parts.values())
        foreign = rnd.randint(-8000, 8000)
        qty = {Investor.INSTITUTION: inst, Investor.FOREIGN: foreign,
               Investor.INDIVIDUAL: -(inst + foreign)}  # fmt: skip
        if inst7:
            qty.update(parts)
        for inv, n in qty.items():
            rows.append(InvestorDay(CODE, d, inv, int(n * close[d]), n, src, "KRX", q))
    r.flows.upsert_investor_days(rows, revise=False, loaded_by=BY, now=NOW, received_at=NOW)
    rows_nh = achieved if achieved is not None else [board_row(CODE, NAME)]
    payload = {"as_of": DAY.isoformat(), "achieved": rows_nh}
    art = BoardArtifact(DAY, "newhigh", payload, "synthetic", "0" * 16, NOW, NOW, "kis",
                        Quality.ESTIMATED)  # fmt: skip
    r.board.put_day(BoardDayRecord(DAY, (), (), (art,), "kis", Quality.ESTIMATED),
                    loaded_by=BY, now=NOW)  # fmt: skip
    return r


class Sink:
    """notify·notify_media 자리 — 받은 것을 모은다(그림은 넣는 순간 읽는다 — notifier 와 같다)."""

    def __init__(self, *, media_ok: bool = True) -> None:
        self.texts: list[tuple[str, dict[str, Any]]] = []
        self.media: list[tuple[list[bytes], str, dict[str, Any]]] = []
        self.media_ok = media_ok

    def notify(self, text: str, **kw: Any) -> SimpleNamespace:
        self.texts.append((text, kw))
        return SimpleNamespace(ok=True, duplicate=False)

    def notify_media(self, paths: list[str], caption: str = "", **kw: Any) -> SimpleNamespace:
        self.media.append(([Path(p).read_bytes() for p in paths], caption, kw))
        return SimpleNamespace(ok=self.media_ok, duplicate=False, reason="합성 거절")


def ctx(repos: Any, sink: Sink, *, media: bool = True) -> JobContext:
    res: dict[str, Any] = {"repos": repos, "notify": sink.notify, "kr": CAL}
    if media:
        res["notify_media"] = sink.notify_media
    return JobContext("flows.report", DAY.isoformat(), "r1", 1, NOW.astimezone(UTC),
                      resources=res)  # fmt: skip


def test_등록부_owner_가_처리기로_풀린다() -> None:
    assert resolve("kbj.reports.flows:daily") is daily


def test_대상_종목마다_차트_4장과_본문_한_통() -> None:
    sink = Sink()
    res = daily(ctx(seed(), sink))
    assert res.status == "ok" and res.rows == 1
    ((pics, caption, kw),) = sink.media
    assert len(pics) == 4 and all(len(p) > 5000 for p in pics)
    assert caption.startswith(f"{NAME} {CODE} · 수급 60거래일")
    assert kw["kind"] == "flows.report" and kw["subject"] == f"{CODE}:charts"
    ((text, tkw),) = sink.texts
    assert tkw["parse_mode"] == "Markdown" and tkw["subject"] == CODE and tkw["as_of"] == DAY
    assert f"*{NAME}* `{CODE}` — 최근 60거래일 수급" in text
    assert "52주 신고가(고가·종가 기준) · 거래량 20일 평균의 3.2배" in text
    assert text.count("```") == 2
    assert res.detail["sent"] == 1 and res.detail["board_asof"] == DAY.isoformat()


def test_수급_기준일_원천_quality_를_적는다() -> None:
    sink = Sink()
    daily(ctx(seed(), sink))
    assert "_수급 기준일 11.17 · 원천 kis · 잠정_" in sink.texts[0][0]


def test_오늘_수급이_없으면_전_거래일_값과_그_차이를_적는다() -> None:
    sink = Sink()
    prev = CAL.prev_trading_day(DAY)
    daily(ctx(seed(flows_end=prev), sink))
    text = sink.texts[0][0]
    assert "기준일이 다르다 — 신고가 11.17 · 수급·가격 11.16" in text
    assert "수급 기준일 11.16 · 원천 krx · 확정" in text


def test_기관_세부가_없으면_차트는_3장() -> None:
    sink = Sink()
    daily(ctx(seed(inst7=False), sink))
    assert len(sink.media[0][0]) == 3


def test_그림이_실패하면_본문도_보내지_않고_작업은_실패() -> None:
    sink = Sink(media_ok=False)
    with pytest.raises(R.FlowReportError):
        daily(ctx(seed(), sink))
    assert sink.texts == []


def test_대상이_없는_날도_한_줄은_간다() -> None:
    sink = Sink()
    repos = seed(achieved=[board_row(CODE, NAME, kind="d120")])
    res = daily(ctx(repos, sink))
    assert res.rows == 0 and res.detail["no_target"] is True
    assert sink.texts[0][0].startswith("📉 수급 리포트 — 대상 없음")
    assert sink.texts[0][1]["subject"] == "none"


def test_원장에_없으면_건너뛰고_그_사실을_알린다() -> None:
    sink = Sink()
    repos = seed(achieved=[board_row("997999", "원장없음")])
    res = daily(ctx(repos, sink))
    assert res.detail["skipped"] == 1 and res.rows == 0
    assert "모두 건너뜀" in sink.texts[0][0] and "수급 원장에 행이 없다" in sink.texts[0][0]


def test_보드_산출이_없으면_실패() -> None:
    with pytest.raises(R.FlowReportError, match=r"board\.daily"):
        daily(ctx(memory_repos(), Sink()))


def test_notify_media_는_notify_의_짝을_찾는다() -> None:
    sink = Sink()
    c = ctx(seed(), sink, media=False)
    # 자원 notify 가 묶인 메서드면 같은 객체의 notify_media 를 쓴다
    assert R._notify_media(c) == sink.notify_media  # pyright: ignore[reportPrivateUsage]


def test_ledger_는_없는_구분을_0으로_채우지_않는다() -> None:
    rows = [InvestorDay(CODE, DAY, Investor.FOREIGN, 100, 1, "kis", "KRX", Quality.OK)]
    amt, qty, meta = R.ledger(rows)
    assert amt == {"20261117": {"외국인": 100.0}} and qty == {"20261117": {"외국인": 1.0}}
    assert meta["20261117"]["quality"] is Quality.OK


def test_자릿수_대조는_값이_모자라면_걸지_않는다() -> None:
    assert R.unit_check({"20261117": {"개인": 1.0}}, {}, {"20261117": 100.0}) is None
    chk = R.unit_check({"d": {"개인": 1e14}}, {"d": {"개인": 1e8}}, {"d": 1.0})
    assert chk is not None and chk[2] is False  # 100만 배 — 단위를 잘못 안 것
