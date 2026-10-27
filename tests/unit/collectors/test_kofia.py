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


def test_late_portal_still_fills_earlier_days_and_skips_the_day() -> None:
    """포털이 2영업일쯤 늦다(실측 2026-10-20 — 15시에 최신이 전전 거래일). 그날 하루만 부르면 표가
    한 번도 차지 않았다 — 창 안의 지난 날은 넣고, 그날은 허용 지연(3거래일) 안이라 실패가 아니라
    건너뜀(날마다 '공표 전' 실패가 쌓이던 것 — 늦은 날은 다음 실행의 창이 채운다)."""
    repos = memory_public_repos()
    older = date(2026, 11, 12)  # 목 — 그날(11-16 월)까지 거래일 2(금·월)
    k = _Kofia({"credit": [_row("credit", older, crdTrFingWhl="19000000000000")]})
    res = K.run(_ctx(["credit"], k, repos))
    assert res.status == "skipped" and res.collected == ()
    assert "공표 지연" in res.detail["reason"] and "2026-11-12" in res.detail["reason"]
    got = {r.item: r for r in repos.market_stats.series("credit_balance")}
    assert got["loan_total"].value == Decimal("19000000000000")


def test_portal_too_far_behind_is_not_ready() -> None:
    """허용 지연(3거래일)보다 늦으면 공표 전 — 재시도 뒤 실패로 드러난다(포털이 멈춘 것)."""
    repos = memory_public_repos()
    k = _Kofia({"credit": [_row("credit", date(2026, 11, 6))]})  # 금 — 그날까지 거래일 6
    res = K.run(_ctx(["credit"], k, repos))
    assert res.status == "not_ready" and "공표 전" in res.detail["reason"]
