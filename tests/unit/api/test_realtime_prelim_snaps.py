"""장중 잠정 스냅(`flows.intraday` 거래대금 순위 — 목록 종목만, 시총 없음)이 있어도 화면이 깨지지
않는다(2026-10-27 실시간 점검 — docs/realtime_audit.md).

운영 `flows.intraday` 는 매 10분 슬롯마다 순위 목록 종목(수십 개)의 오늘 잠정 스냅(`kis.prelim`,
estimated, mktcap None)을 `prv_market.stock_snapshot` 에 쓴다. 저장소 `snapshot(오늘)` 이 그 날을
고르면 장중 내내
- 원장 마지막 날이 오늘(목록 종목뿐)이 되어 시장 요약의 거래대금이 '장중(지수 기준)'이 아니라
  목록 종목 합, 시장폭이 목록 종목만,
- 목록 밖 종목의 헤더가 '시세 없음',
- 장중 수급의 시총 대비 순위가 비었다(잠정 스냅에 시총이 없다).
합성 세계(`api_world`)는 잠정 스냅을 쓰지 않아 이 길이 시험되지 않았다 — 여기서 넣는다.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from kbj.core.quality import Quality
from tests.unit.api.api_world import LOADED_BY, World

PRELIM_N = 5


def _put_prelim_snaps(world: World) -> list[str]:
    """다음 거래일(장중)의 잠정 스냅 — 운영 `parse_rank_snaps` 와 같은 모양(시총 None)."""
    snaps, day = world.repos.market.snapshot(world.market.last_day)
    assert day == world.market.last_day
    codes = sorted(c for c, s in snaps.items() if s.kind == "common")[:PRELIM_N]
    rows = [
        replace(
            snaps[c],
            date=world.next_day,
            close=(snaps[c].close or 1.0) * 1.01,
            mktcap=None,
            status_flags=None,
            source="kis.prelim",
            quality=Quality.ESTIMATED,
            turnover_is_estimate=True,
        )
        for c in codes
    ]
    world.repos.market.upsert_snapshots(rows, loaded_by=LOADED_BY, received_at=world.slot)
    return codes


def test_summary_intraday_ignores_prelim_snaps(world: World, authed: TestClient) -> None:
    after_close = authed.get("/api/market/summary").json()["data"]
    _put_prelim_snaps(world)
    world.clock.set(world.slot.replace(minute=5))
    d = authed.get("/api/market/summary").json()["data"]
    assert d["date"] == world.market.last_day.isoformat()  # 원장 마지막 날 = 마지막 확정일
    t = d["turnover"]
    assert t["basis"] == "intraday_index" and "장중(지수 기준)" in t["tags"]
    assert t["today"]["value"] == 4_100_000_000_000 + 3_200_000_000_000
    assert d["breadth"]["total"] == after_close["breadth"]["total"]  # 목록 종목만이 아니다


def test_stock_header_outside_rank_list_keeps_last_close(world: World, authed: TestClient) -> None:
    codes = _put_prelim_snaps(world)
    snaps, _ = world.repos.market.snapshot(world.market.last_day)
    other = next(c for c, s in sorted(snaps.items()) if c not in codes and s.kind == "common")
    world.clock.set(world.slot.replace(minute=5))
    for code in (other, codes[0]):
        last = authed.get(f"/api/stock/{code}/header").json()["data"]["last"]
        assert last is not None, code  # '시세 없음' 이 아니다
        assert last["date"] == world.market.last_day.isoformat()
        assert last["mktcap"] is not None


def test_intraday_flows_by_cap_uses_last_close_mktcap(world: World, authed: TestClient) -> None:
    world.clock.set(world.slot.replace(minute=5))
    want = authed.get("/api/flows/intraday").json()["data"]["top_foreign_by_cap"]
    assert want  # 잠정 스냅이 없을 때(합성 세계 기본)는 전일 시총으로 순위가 있다
    _put_prelim_snaps(world)
    world.clock.advance(minutes=1)  # 응답 캐시 없음(버전 원천 없음) — 다시 계산
    d = authed.get("/api/flows/intraday").json()["data"]
    assert d["top_foreign_by_cap"] == want
    assert all(x["mktcap"] for x in d["top_foreign"])


def test_screen_keeps_last_confirmed_ledger(world: World, authed: TestClient) -> None:
    before = authed.get("/api/flows/screen?mode=value&period=1").json()
    _put_prelim_snaps(world)
    world.clock.set(world.slot.replace(minute=5))
    after = authed.get("/api/flows/screen?mode=value&period=1").json()
    assert after["data"]["date"] == world.market.last_day.isoformat()
    assert after["data"]["n_total"] == before["data"]["n_total"]
