"""공개 매크로 수집(kbj/services/collectors/macro.py — docs/p5_design.md §1.2): 가짜 서버·시계 주입·
공표 지연·부분 실패 격리·실측 전 skipped·처음 본 시각 보존. 합성만."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from kbj.config.macro import MacroConfig, parse_macro
from kbj.core.p5_rows import MacroSeriesRow
from kbj.core.quality import Quality
from kbj.data.public.ecos.client import EcosRow
from kbj.data.public.fred.client import FredClient
from kbj.data.public.kosis.client import KosisRow
from kbj.data.public.nyfed.client import NyfedClient
from kbj.data.public.treasury.client import TreasuryClient
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.services.collectors import macro as M
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.public import MemoryPublicRepos, memory_public_repos
from tests.fakes.fred_server import FAKE_KEY, FakeFred
from tests.fakes.nyfed_server import FakeNyfed
from tests.fakes.treasury_server import FakeTreasury

NOW = datetime(2026, 11, 17, 21, 20, tzinfo=UTC)  # 11-18 06:20 KST · 16:20 ET(11-17)
US_DAY = "2026-11-16"  # 끝난 미국 세션(가짜 서버 끝 = 뉴욕 전날 11-16)
RAW: dict[str, Any] = yaml.safe_load(open("config/macro.yaml", encoding="utf-8"))


class _Lim:
    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.5


def cfg_with(**items: str | None) -> MacroConfig:
    """item 을 바꾼 설정(시험 전용 — config/macro.yaml 은 그대로). None = 실측 전처럼 비움."""
    raw = copy.deepcopy(RAW)
    for s in raw["series"]:
        if s["id"] in items:
            s["item"] = items[s["id"]]
    return parse_macro(raw)


def key(ds: str, as_of: str) -> DataKey:
    src, name = ds.split(":", 1)
    return DataKey(src, name, as_of)


def ctx(
    keys: Sequence[DataKey], res: dict[str, Any], *, now: datetime = NOW, job: str = "macro.morning"
) -> JobContext:
    return JobContext(job, keys[0].as_of if keys else "", "run-1", 1, now, tuple(keys), None, res)


def fred(fake: FakeFred, cfg: MacroConfig) -> FredClient:
    gov = [s.item for s in cfg.series if s.dataset == "FRED:gov_series" and s.item]
    return FredClient(
        SecretStr(FAKE_KEY),
        limiter=_Lim(),
        gov_series=gov,
        transport=fake.transport(),
        base_url="https://f",
    )


MORNING = [
    key("FRED:gov_series", US_DAY),
    key("FRED:release_dates", "2026-11-18"),
    key("TREASURY:yield_curve", US_DAY),
    key("NYFED:rates", US_DAY),
]


def test_morning_writes_public_rows_and_skips_unprobed_sources() -> None:
    cfg = cfg_with()  # 실 설정 그대로 — 재무부·뉴욕연은 item null(#45)
    repos = memory_public_repos()
    fake = FakeFred(lambda: NOW, missing={("DGS2", "2026-11-12")})
    res = M.morning(
        ctx(MORNING, {"macro_config": cfg, "public_repos": repos, "fred": fred(fake, cfg)})
    )
    assert res.status == "skipped"  # 실측 전 키가 있으니 ok 가 아니다(받은 척하지 않는다)
    assert {k.dataset_id for k in res.collected} == {"FRED:gov_series", "FRED:release_dates"}
    assert set(res.detail["skipped"]) == {
        "TREASURY:yield_curve@2026-11-16",
        "NYFED:rates@2026-11-16",
    }
    meta = {m.series_id: m for m in repos.macro.meta()}
    assert "FRED:DGS10" in meta and all(m.tier == "public" for m in meta.values())
    rows = repos.macro.series(["FRED:DGS2"])["FRED:DGS2"]
    gap = next(r for r in rows if r.period == "2026-11-12")
    assert gap.value is None and gap.quality is Quality.INVALID  # 0 으로 채우지 않는다
    assert all(r.released_at == NOW for r in rows if r.value is not None)
    cpi = repos.macro.series(["FRED:CPIAUCSL"])["FRED:CPIAUCSL"]
    assert cpi and cpi[-1].period.count("-") == 1  # 월 기간 YYYY-MM
    cal = repos.macro.calendar(date(2026, 11, 1), date(2026, 12, 31))
    assert cal and all(e.source in ("fred", "bok") and e.forecast is None for e in cal)
    assert {e.kind for e in cal} <= {
        "cpi",
        "payrolls",
        "gdp",
        "pce",
        "bok_mpc",
    }  # 대응 없는 발표는 싣지 않는다
    # 금통위(config/calendar_events.yaml)도 경제 캘린더에 — 빠져 있었다(2026-10-20)
    assert any(e.kind == "bok_mpc" and e.date == date(2026, 11, 26) for e in cal)


def test_all_sources_when_probed_and_rerun_keeps_first_seen_time() -> None:
    cfg = cfg_with(**{"TREASURY:10y": "10 Yr", "NYFED:SOFR": "sofr", "NYFED:EFFR": "effr"})
    repos = memory_public_repos()
    res_ = {
        "macro_config": cfg,
        "public_repos": repos,
        "fred": fred(FakeFred(lambda: NOW), cfg),
        "treasury": TreasuryClient(
            _Lim(), transport=FakeTreasury(lambda: NOW).transport(), base_url="https://t"
        ),
        "nyfed": NyfedClient(
            _Lim(), transport=FakeNyfed(lambda: NOW).transport(), base_url="https://n"
        ),
    }
    res = M.morning(ctx(MORNING, res_))
    assert res.status == "ok" and len(res.collected) == 4, res.detail
    assert repos.macro.latest(["TREASURY:10y"])["TREASURY:10y"].period == US_DAY
    assert repos.macro.latest(["NYFED:SOFR"])["NYFED:SOFR"].source == "NYFED"
    later = datetime(2026, 11, 18, 21, 20, tzinfo=UTC)
    M.morning(ctx(MORNING[:1], {**res_, "fred": fred(FakeFred(lambda: NOW), cfg)}, now=later))
    first = repos.macro.series(["FRED:DGS10"], "2026-11-16")["FRED:DGS10"][0]
    assert first.released_at == NOW and first.as_of == later  # 같은 값 — 처음 본 시각을 지킨다


def test_not_ready_when_the_session_value_is_missing_but_rows_are_written() -> None:
    cfg = cfg_with()
    repos = memory_public_repos()
    # FRED 일별은 하루 늦게 올라와 06:20 KST 에는 미국 거래일의 **전** 거래일(11-13 금)을 요구한다 —
    # 가짜 서버 끝 = 11-12(목)이면 그 값이 없다
    early = datetime(2026, 11, 13, 21, 20, tzinfo=UTC)
    res = M.morning(
        ctx(
            MORNING[:1],
            {
                "macro_config": cfg,
                "public_repos": repos,
                "fred": fred(FakeFred(lambda: early), cfg),
            },
            now=early,
        )
    )
    assert res.status == "not_ready" and res.collected == ()
    assert "공표 전" in res.detail["reason"] and repos.macro.latest(["FRED:DGS10"])


def test_one_failing_key_does_not_stop_the_others() -> None:
    cfg = cfg_with()
    repos = memory_public_repos()
    fake = FakeFred(lambda: NOW)
    fake.inject(500)  # 첫 키(gov_series)의 첫 시리즈 호출만 — 그 키는 실패
    res = M.morning(
        ctx(MORNING[:2], {"macro_config": cfg, "public_repos": repos, "fred": fred(fake, cfg)})
    )
    assert res.status == "failed" and "HTTP 500" in res.detail["reason"]
    assert [k.dataset_id for k in res.collected] == ["FRED:release_dates"]
    assert FAKE_KEY not in str(res.detail)


class _Ecos:
    def __init__(self, rows: dict[str, list[tuple[str, str | None]]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def search(
        self, stat: str, cycle: str, start: str, end: str, items: tuple[str, ...] = ()
    ) -> list[EcosRow]:
        self.calls.append((stat, cycle, items))
        return [
            EcosRow(
                source=f"ECOS:{stat}",
                stat_code=stat,
                stat_name="합성",
                cycle=cycle,
                item_codes=items,
                item_names=(),
                unit="%",
                weight="",
                time=t,
                value=None if v is None else Decimal(v),
                raw_value=v or "",
            )
            for t, v in self.rows.get(stat, [])
        ]


def test_evening_ecos_uses_items_and_period_formats() -> None:
    cfg = cfg_with(
        **{
            "ECOS:817Y002:ktb3y": "010200000",
            "ECOS:817Y002:corp_aa3y": "010300000",
            "ECOS:722Y001:base": "0101000",
            "ECOS:731Y003:usdkrw": None,  # 항목 코드를 모르는 시리즈는 건너뛴다(#43)
        }
    )
    repos = memory_public_repos()
    ecos = _Ecos(
        {"817Y002": [("20261116", "2.61"), ("20261117", "2.62")], "722Y001": [("20261117", "2.50")]}
    )
    keys = [
        key("ECOS:817Y002", "2026-11-17"),
        key("ECOS:722Y001", "2026-11-17"),
        key("ECOS:731Y003", "2026-11-17"),
    ]
    res = M.evening(
        ctx(keys, {"macro_config": cfg, "public_repos": repos, "ecos": ecos}, job="macro.evening")
    )
    assert {k.dataset_id for k in res.collected} == {"ECOS:817Y002", "ECOS:722Y001"}
    assert res.status == "skipped" and "#43" in res.detail["skipped"]["ECOS:731Y003@2026-11-17"]
    assert ("817Y002", "D", ("010200000",)) in ecos.calls
    got = repos.macro.series(["ECOS:817Y002:ktb3y"])["ECOS:817Y002:ktb3y"]
    assert [r.period for r in got] == ["2026-11-16", "2026-11-17"]


def test_monthly_not_ready_until_the_month_is_published_and_kosis_items() -> None:
    cfg = cfg_with(**{"ECOS:721Y001:cd91": "5", "KOSIS:DT_1J22003:cpi": "T10/T"})
    repos = memory_public_repos()
    ecos = _Ecos({"721Y001": [("202609", "2.9")]})

    class _Kosis:
        def parameter_data(
            self, org: str, tbl: str, obj: str, itm: str, prd: str, **kw: Any
        ) -> list[KosisRow]:
            assert (org, tbl, obj, itm, prd) == ("101", "DT_1J22003", "T10", "T", "M")
            return [
                KosisRow(
                    source=f"KOSIS:{tbl}",
                    org_id=org,
                    tbl_id=tbl,
                    tbl_nm="x",
                    itm_id=itm,
                    itm_nm="x",
                    unit="2020=100",
                    prd_se="M",
                    prd_de="202610",
                    classes=(),
                    value=Decimal("116.9"),
                    raw_value="116.9",
                    last_changed="",
                )
            ]

    keys = [key("ECOS:721Y001", "2026-10"), key("KOSIS:DT_1J22003", "2026-10")]
    res = M.monthly(
        ctx(
            keys,
            {"macro_config": cfg, "public_repos": repos, "ecos": ecos, "kosis": _Kosis()},
            job="macro.monthly",
        )
    )
    assert res.status == "not_ready" and [k.dataset_id for k in res.collected] == [
        "KOSIS:DT_1J22003"
    ]
    assert "2026-10 공표 전" in res.detail["not_ready"]["ECOS:721Y001@2026-10"]
    assert repos.macro.latest(["ECOS:721Y001:cd91"])["ECOS:721Y001:cd91"].period == "2026-09"


def test_period_helpers() -> None:
    assert (
        M.period_of("Q", date(2026, 11, 1)) == "2026-Q4"
        and M.period_of("A", date(2026, 1, 1)) == "2026"
    )
    assert (
        M.source_period("Q", "2026Q3") == "2026-Q3" and M.source_period("Q", "202603") == "2026-Q3"
    )
    assert M.as_of_period("2026Q3") == "2026-Q3" and M.as_of_period("2026-10") == "2026-10"
    with pytest.raises(ValueError):
        M.source_period("M", "2026")


def test_unknown_dataset_key_fails_without_touching_repos() -> None:
    repos: MemoryPublicRepos = memory_public_repos()
    res = M.morning(
        ctx([key("FRED:other", US_DAY)], {"macro_config": cfg_with(), "public_repos": repos})
    )
    assert res.status == "failed" and repos.macro.meta() == []


def test_fred_daily_wants_the_previous_us_trading_day_and_blank_counts() -> None:
    """FRED 일별은 다음 미국 영업일에 올라온다 — 06:20 KST 의 미국 거래일 D 에는 D 의 전 거래일을
    요구한다. 채권시장 휴장일 값 '.'(빈 값)도 공표로 센다(2026-10-20: 날마다 '공표 전' 4회 실패)."""
    c = ctx(MORNING[:1], {}, now=NOW)
    assert M.fred_daily_want(c, date(2026, 11, 16)) == "2026-11-13"  # 월 → 금
    s = cfg_with().series
    daily = [x for x in s if x.dataset == "FRED:gov_series" and x.freq == "D"][:1]
    blank = [MacroSeriesRow(daily[0].id, "2026-11-13", None, "%", "FRED", NOW, Quality.INVALID)]
    M.require_periods(daily, blank, "2026-11-13", count_blank=True)
    with pytest.raises(M.NotReady):
        M.require_periods(daily, blank, "2026-11-13")
