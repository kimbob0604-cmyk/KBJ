"""관심종목 쓰기(P8 묶음 JNL — `add`·`remove`·`reorder`, 출처 api). 메모리 저장소."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from kbj.core.fin_rows import WatchItem
from kbj.core.quality import Quality
from kbj.core.rows import UniverseRow
from kbj.services.watchlist import API_LOADED_BY, WatchWriteError, add, remove, reorder
from kbj.store.repos.memory import MemoryRepos

NOW = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)


def _repos() -> MemoryRepos:
    r = MemoryRepos()
    r.market.upsert_universe(
        [
            UniverseRow(c, date(2026, 10, 13), n, "KOSPI", "common", None, "KRX", Quality.OK)
            for c, n in (("990001", "합성전자"), ("990002", "합성화학"), ("990003", "합성기계"))
        ],
        loaded_by="test",
    )
    r.journal.replace_watchlist(
        "main",
        [WatchItem("main", "990001", "합성전자", None, 0, source="cli")],
        loaded_by="cli.watchlist",
        now=NOW,
    )
    return r


def test_add_idempotent_and_known_only() -> None:
    r = _repos()
    items, added = add(r, "main", "990002", now=NOW)
    assert added and [i.code for i in items] == ["990001", "990002"]
    new = items[1]
    assert (new.source, new.name_at_add, new.sort, new.added_at) == ("api", "합성화학", 1, NOW)
    assert items[0].source == "cli"  # 기존 항목의 출처는 지킨다
    again, added2 = add(r, "main", "990002", now=NOW)
    assert not added2 and again == items
    with pytest.raises(WatchWriteError) as e:
        add(r, "main", "990009", now=NOW)
    assert e.value.reason == "unknown_code"
    with pytest.raises(WatchWriteError) as e:
        add(r, "main", "bad", now=NOW)
    assert e.value.reason == "bad_code"
    assert r.journal.watch_t.rows[("main", "990002")].extra["loaded_by"] == API_LOADED_BY


def test_add_to_new_list_and_remove_reorder() -> None:
    r = _repos()
    items, _ = add(r, "swing", "990003", now=NOW)
    assert [(i.list_name, i.code, i.sort) for i in items] == [("swing", "990003", 0)]
    add(r, "main", "990002", now=NOW)
    add(r, "main", "990003", now=NOW)
    got = reorder(r, "main", ["990003", "990001", "990002"], now=NOW)
    assert [(i.code, i.sort) for i in got] == [("990003", 0), ("990001", 1), ("990002", 2)]
    for bad in (
        ["990003", "990001"],
        ["990003", "990001", "990001"],
        ["990003", "990001", "990009"],
    ):
        with pytest.raises(WatchWriteError) as e:
            reorder(r, "main", bad, now=NOW)
        assert e.value.reason == "bad_order"
    left = remove(r, "main", "990001", now=NOW)
    assert [(i.code, i.sort) for i in left] == [("990003", 0), ("990002", 1)]
    with pytest.raises(WatchWriteError) as e:
        remove(r, "main", "990001", now=NOW)
    assert e.value.reason == "not_in_list"
    assert [i.code for i in r.journal.watchlist("swing")] == ["990003"]  # 다른 목록은 그대로
