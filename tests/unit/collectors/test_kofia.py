"""금투협 종합통계 수집(kbj/services/collectors/kofia.py — D-P5-20): 항목 이름·결측·공표 전·fund
실측 전."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from kbj.core.quality import Quality
from kbj.data.public.fsc_kofia_stats.client import NUMERIC, KofiaRow, Op
from kbj.data.spec import DataKey
from kbj.services.collectors import kofia as K
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.public import memory_public_repos

NOW = datetime(2026, 11, 17, 4, 30, tzinfo=UTC)  # 13:30 KST
D = date(2026, 11, 16)


def _row(op: Op, d: date, **vals: str | None) -> KofiaRow:
    values = {
        c: (None if vals.get(c, "1") is None else Decimal(vals.get(c) or "1")) for c in NUMERIC[op]
    }  # type: ignore[arg-type]
    labels = {"mngInvTgt": "개인", "invrCtg": "전체"} if op == "cma" else {}
    return KofiaRow(source=f"DATAGO:15094809/{op}", op=op, bas_dt=d, values=values, labels=labels)  # type: ignore[arg-type]


class _Kofia:
    def __init__(self, days: dict[str, list[KofiaRow]]) -> None:
        self.days = days

    def credit_balance(self, b: date, e: date) -> list[KofiaRow]:
        return self.days.get("credit", [])

    def market_capital(self, b: date, e: date) -> list[KofiaRow]:
        return self.days.get("capital", [])

    def cma(self, b: date, e: date) -> list[KofiaRow]:
        return self.days.get("cma", [])


def _ctx(ops: list[str], kofia: _Kofia, repos: object) -> JobContext:
    keys = tuple(DataKey("DATAGO", f"15094809/{op}", D.isoformat()) for op in ops)
    return JobContext("market_stats.kofia", D.isoformat(), "r", 1, NOW, keys, None,
                      {"public_repos": repos, "kofia": kofia})  # fmt: skip


def test_credit_and_capital_items_with_missing_cell_as_invalid() -> None:
    repos = memory_public_repos()
    k = _Kofia({"credit": [_row("credit", D, crdTrFingWhl="20000000000000", crdTrLndrWhl=None)],
                "capital": [_row("capital", D, invrDpsgAmt="55000000000000")]})  # fmt: skip
    res = K.run(_ctx(["credit", "capital"], k, repos))
    assert res.status == "ok" and len(res.collected) == 2
    got = {r.item: r for r in repos.market_stats.series("credit_balance")}
    assert got["loan_total"].value == Decimal("20000000000000") and got["loan_total"].unit == "원"
    assert got["short_total"].value is None and got["short_total"].quality is Quality.INVALID
    cap = {r.item: r for r in repos.market_stats.series("market_capital")}
    assert cap["investor_deposit"].source == "DATAGO" and set(cap) == set(
        K.ITEMS["capital"].values()
    )


def test_not_ready_when_the_day_has_no_rows_and_fund_is_skipped() -> None:
    repos = memory_public_repos()
    res = K.run(_ctx(["credit", "fund", "cma"], _Kofia({"cma": [_row("cma", D)]}), repos))
    assert res.status == "not_ready" and [k.dataset for k in res.collected] == ["15094809/cma"]
    assert "#48" in res.detail["skipped"]["DATAGO:15094809/fund@2026-11-16"]
    items = {r.item for r in repos.market_stats.series("cma")}
    assert all(i.startswith(("companies_g", "accounts_g", "balance_g")) for i in items)  # 구분 해시


def test_rows_of_drops_other_days() -> None:
    assert K.rows_of("credit", [_row("credit", date(2026, 11, 13))], D) == []
