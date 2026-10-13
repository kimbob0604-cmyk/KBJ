"""로그인 매크로 수집(kbj/services/collectors/macro_prv.py — D-P5-6·7): Yahoo 12종·FRED 저작권·ECOS
타기관·
ForexFactory(꺼짐이면 skipped, 켜면 예상치) → prv_macro 만. 합성만."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import SecretStr

from kbj.config.macro import MacroConfig
from kbj.core.quality import Quality
from kbj.data.private.forexfactory import FfClient
from kbj.data.private.fred_restricted import FredRestrictedClient
from kbj.data.private.yahoo.client import YahooClient
from kbj.services.collectors import macro_prv as P
from kbj.store.repos.memory import memory_repos
from tests.fakes.ff_server import FakeFf
from tests.fakes.fred_server import FAKE_KEY, FakeFred
from tests.fakes.yahoo_server import FakeYahoo
from tests.unit.collectors.test_macro import RAW, _Ecos, _Lim, cfg_with, ctx, key

NOW = datetime(2026, 11, 17, 21, 20, tzinfo=UTC)  # 16:20 ET — 11-17 세션 끝
US_DAY = "2026-11-17"


def _res(cfg: MacroConfig, repos: Any, *, yahoo: FakeYahoo | None = None) -> dict[str, Any]:
    restricted = [s.item for s in cfg.series if s.dataset == "FRED:restricted_series" and s.item]
    return {
        "macro_config": cfg,
        "repos": repos,
        "yahoo": YahooClient(
            _Lim(), transport=(yahoo or FakeYahoo(lambda: NOW)).transport("t"), base_url="https://y"
        ),
        "fred_restricted": FredRestrictedClient(
            SecretStr(FAKE_KEY),
            limiter=_Lim(),
            restricted=restricted,
            transport=FakeFred(lambda: NOW).transport(),
            base_url="https://f",
        ),
        "ff": FfClient(_Lim(), transport=FakeFf(lambda: NOW).transport(), base_url="https://ff"),
    }


MORNING = [
    key("YAHOO:macro", US_DAY),
    key("FRED:restricted_series", "2026-11-16"),
    key("FF:calendar", "2026-11-18"),
]


def test_morning_prv_writes_only_private_tables_and_ff_is_skipped_while_off() -> None:
    from kbj.config.macro import parse_macro

    # 실 설정은 사용자 결정 Q3 로 켰다(WRAP) — 꺼 두면 부르지 않고 skipped 인지 본다
    assert cfg_with().ff.enabled is True
    cfg = parse_macro({**RAW, "ff": {"enabled": False}})
    assert cfg.ff.enabled is False
    repos = memory_repos()
    res = P.morning(ctx(MORNING, _res(cfg, repos), job="macro.morning_prv"))
    assert (
        res.status == "skipped" and "ff.enabled" in res.detail["skipped"]["FF:calendar@2026-11-18"]
    )
    assert {k.dataset_id for k in res.collected} == {"YAHOO:macro", "FRED:restricted_series"}
    meta = repos.macro_prv.meta()
    assert len(meta) >= 15 and all(m.tier == "private" for m in meta)
    krw = repos.macro_prv.series(["YAHOO:KRW=X"])["YAHOO:KRW=X"]
    assert (
        krw[-1].period == US_DAY
        and krw[-1].source == "YAHOO"
        and isinstance(krw[-1].value, Decimal)
    )
    assert repos.macro.meta() == []  # 공개 표에는 쓰지 않는다
    assert repos.macro_prv.calendar(date(2026, 11, 1), date(2026, 12, 31)) == []


def test_ff_enabled_writes_forecasts_to_private_calendar() -> None:
    raw = {**RAW, "ff": {"enabled": True}}
    from kbj.config.macro import parse_macro

    cfg = parse_macro(raw)
    repos = memory_repos()
    res = P.morning(ctx(MORNING[2:], _res(cfg, repos), job="macro.morning_prv"))
    assert res.status == "ok", res.detail
    cal = repos.macro_prv.calendar(date(2026, 11, 16), date(2026, 11, 22))
    cpi = next(e for e in cal if e.kind == "cpi")
    assert (cpi.source, cpi.forecast, cpi.unit, cpi.country) == ("ff", Decimal("0.3"), "%", "US")
    nfp = next(e for e in cal if e.kind == "payrolls")
    assert nfp.unit == "천" and not nfp.is_public


def test_live_yahoo_bar_is_estimated_and_missing_session_is_not_ready() -> None:
    cfg = cfg_with()
    repos = memory_repos()
    live = FakeYahoo(lambda: datetime(2026, 11, 17, 17, 0, tzinfo=UTC), live=True)
    res = P.morning(ctx(MORNING[:1], _res(cfg, repos, yahoo=live), job="macro.morning_prv"))
    assert res.status == "ok"
    vix = repos.macro_prv.series(["YAHOO:^VIX"])["YAHOO:^VIX"]
    assert vix[-1].quality is Quality.ESTIMATED  # 진행 중인 봉 — 종가 아님
    res2 = P.morning(
        ctx([key("YAHOO:macro", "2026-11-18")], _res(cfg, memory_repos()), job="macro.morning_prv")
    )
    assert res2.status == "not_ready"


def test_one_unknown_symbol_fails_the_key_after_writing_the_rest() -> None:
    cfg = cfg_with()
    repos = memory_repos()
    y = FakeYahoo(lambda: NOW)
    del y.symbols["BTC-USD"]
    res = P.morning(ctx(MORNING[:1], _res(cfg, repos, yahoo=y), job="macro.morning_prv"))
    assert res.status == "failed" and "BTC-USD" in res.detail["reason"]
    assert repos.macro_prv.latest(["YAHOO:GC=F"])  # 받은 심볼은 썼다


def test_monthly_prv_ecos_restricted_tables() -> None:
    cfg = cfg_with(**{"ECOS:901Y056:deposit": "S23A"})
    repos = memory_repos()
    ecos = _Ecos({"901Y056": [("202609", "55000"), ("202610", "56000")]})
    keys = [key("ECOS:901Y056", "2026-10"), key("ECOS:901Y009", "2026-10")]
    res = P.monthly(
        ctx(
            keys,
            {"macro_config": cfg, "repos": repos, "ecos_restricted": ecos},
            job="macro.monthly_prv",
        )
    )
    assert [k.dataset_id for k in res.collected] == ["ECOS:901Y056"] and res.status == "skipped"
    assert (
        repos.macro_prv.latest(["ECOS:901Y056:deposit"])["ECOS:901Y056:deposit"].period == "2026-10"
    )
