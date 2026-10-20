"""스케줄러 자원 `detail_universe`(D-P4-5) — 관심종목 ∪ 시총 상위 N 보통주 ∪ 공개 데모."""

from __future__ import annotations

from kbj.config.fin import load_fin, load_reports
from kbj.services.engine.detail_universe import WATCH_LIST, detail_codes, provider
from tests.unit.api.api_world_p4 import LAST_KRX_DAY, shared


def test_top_n_and_watchlist_as_common_codes() -> None:
    w = shared()
    fin = load_fin()
    fin = fin.model_copy(update={"consensus": fin.consensus.model_copy(update={"top_n": 3})})
    got = detail_codes(w.repos, LAST_KRX_DAY, fin, load_reports())
    snaps, _ = w.repos.market.snapshot(LAST_KRX_DAY)
    caps = sorted(((s.mktcap or 0, c) for c, s in snaps.items()), reverse=True)
    watch = w.repos.journal.watch_codes(WATCH_LIST)
    assert len(got - set(watch)) <= 3
    assert got  # 비지 않는다(연결 전에는 공개 데모뿐 — 빈 목록)
    assert all(c in snaps or c in watch for c in got)
    assert not caps or caps[0][1] in got  # 시총 1위는 늘 들어간다
    assert provider(w.repos, fin, load_reports())(LAST_KRX_DAY) == got
