"""`consensus.snapshot` 처리기(묶음 K) — 가짜 KIS(합성 20종목 세계)·메모리 저장소.

호출 수 = 2 × 유니버스, 우선순위 P3, 빈 응답은 커버 없음(실패 아님), 일시 실패는 남은 종목만 재시도,
목표가 컨센서스·리비전 파생, 관심종목 리비전 하루 1통(p4_expect.yaml alerts.alert.revision 과 같은
종목), 알림·파생 실패는 키를 미완으로. 등록부에서는 꺼져 있다(D-P4-10) — 처리기를 직접 부른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.fin import FinConfig, load_fin
from kbj.core.fin_rows import ConsensusRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.private.kis.consensus import TR_ESTIMATE, TR_OPINION
from kbj.data.private.kis.datasets import DATASETS
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.services.collectors import consensus as cs
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fakes.kis_server import FakeKisServer
from tests.fixtures.synthetic.p4_world import TODAY, P4World, build_world
from tests.unit.collectors.p3_fakes import DirectKis, ctx

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 16, 16, 0, tzinfo=KST)
DAY = TODAY.isoformat()
BY_ID = {d.id: d for d in DATASETS}
KEYS = (
    BY_ID["KIS:consensus_estimate"].key(DAY),
    BY_ID["KIS:invest_opinion"].key(DAY),
)
# 유니버스(top_n=0) = 관심종목 중 보통주·코스피/코스닥 — 우선주 #2 는 #1 로, 스팩·리츠·코넥스는 뺀다
UNIVERSE = {
    "994010", "994020", "994030", "994060", "994070", "994080", "994100", "994110", "994120",
    "994130", "994140", "994160", "99K170", "994180", "994190",
}  # fmt: skip
EMPTY = {"994080", "994120", "994180", "994190"}  # 유니버스 안이지만 KIS 빈 응답(커버 없음)


def world() -> P4World:
    return build_world()


def expect_alert_codes() -> list[str]:
    doc = yaml.safe_load(
        (ROOT / "tests/fixtures/synthetic/p4_expect.yaml").read_text(encoding="utf-8")
    )
    return sorted(doc["alerts"]["alert.revision"]["codes"])


def cfg(top_n: int = 0) -> FinConfig:
    base = load_fin()
    return base.model_copy(update={"consensus": base.consensus.model_copy(update={"top_n": top_n})})


@dataclass
class Ticket:
    ok: bool = True
    duplicate: bool = False
    reason: str = ""


class Notify:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, text: str, **kw: Any) -> Ticket:
        self.sent.append((text, kw))
        return Ticket(ok=self.ok, reason="" if self.ok else "대기열 오류")


def seeded(*, history: bool = True) -> MemoryRepos:
    w = world()
    r = memory_repos()
    r.market.upsert_universe(w.universe(), loaded_by="test")
    r.market.upsert_snapshots(w.snaps(w.last_krx_day), loaded_by="test")
    r.journal.replace_watchlist("main", w.watchlist, loaded_by="test", now=NOW)
    r.consensus.put_share_classes(w.share_classes, loaded_by="test", now=NOW)
    if history:  # 2026-09-01 ~ 전 거래일 스냅(하루치 피드로는 안 들어오는 과거 — 미리 넣는다)
        old = [x for x in w.consensus if x.snap_date < TODAY]
        r.consensus.put_snapshot(old, loaded_by="test", now=NOW)
    return r


def run(
    r: MemoryRepos,
    kis: DirectKis,
    *,
    keys: tuple[DataKey, ...] = KEYS,
    notify: Notify | None = None,
    top_n: int = 0,
    **res: Any,
) -> Any:
    extra: dict[str, Any] = {"kis": kis, "fin_config": cfg(top_n), **res}
    if notify is not None:
        extra["notify"] = notify
    c = ctx("consensus.snapshot", DAY, keys, now=NOW, repos=r, **extra)
    return cs.run(c)


def semantic(rows: list[ConsensusRow]) -> set[tuple[Any, ...]]:
    return {(x.code, x.fiscal_ym, x.is_estimate, x.revenue, x.op, x.ni, x.eps, x.ebitda)
            for x in rows}  # fmt: skip


def test_full_run_collects_derives_and_sends_one_bundle() -> None:
    r = seeded()
    kis = DirectKis(FakeKisServer(lambda: NOW, consensus=world()))
    note = Notify()
    res = run(r, kis, notify=note)
    assert res.status == "ok" and set(res.collected) == set(KEYS)
    # 호출 = 2 TR × 유니버스, 우선순위 P3(설계 §1.6)
    assert {c[1].get("SHT_CD") or c[1].get("FID_INPUT_ISCD") for c in kis.calls} == UNIVERSE
    assert kis.count(TR_ESTIMATE.tr_id) == kis.count(TR_OPINION.tr_id) == len(UNIVERSE)
    assert {c[2] for c in kis.calls} == {Priority.P3}
    d = res.detail
    assert set(d["consensus_estimate"]["empty_sample"]) == EMPTY  # 커버 없음 — 실패가 아니다
    assert d["consensus_estimate"]["codes"] == len(UNIVERSE - EMPTY)
    assert set(d["watch_excluded"]) == {"994040", "994050", "994090"}
    # 저장: 오늘 스냅 = 세계 의미값(순이익 기준 미확인 — ni_owner 는 None)
    w = world()
    for code in UNIVERSE - EMPTY:
        got = r.consensus.snapshots(code, TODAY, TODAY)
        assert semantic(got) == semantic(w.consensus_rows(code, TODAY)), code
        assert all(x.ni_owner is None and x.source == "KIS" for x in got)
    for code in EMPTY:
        assert r.consensus.snapshots(code, TODAY, TODAY) == []  # 0 으로 채우지 않는다
    ops = r.consensus.opinions("994030", date(2026, 8, 18), TODAY)
    want = [o for o in w.opinion_rows("994030", upto=TODAY) if o.opinion_date >= date(2026, 8, 18)]
    assert {(o.broker, o.opinion_date, o.target_price) for o in ops} == {
        (o.broker, o.opinion_date, o.target_price) for o in want
    }
    # 목표가 컨센서스: #4 는 미제시 증권사를 빼고 3곳, #3 은 2곳(estimated),
    # #1 은 창 밖 1곳을 빼고 5곳
    t4, t3, t1 = (r.consensus.target(c, TODAY) for c in ("994030", "994020", "994010"))
    assert t4 is not None and t4.n_brokers == 3 and t4.quality is Quality.OK
    assert t3 is not None and t3.n_brokers == 2 and t3.quality is Quality.ESTIMATED
    assert t1 is not None and t1.n_brokers == 5
    # 리비전: #8 FY1 순이익 부호 전환(TURN)
    rev8 = r.consensus.revisions("994070", TODAY)
    assert any(x.metric == "ni" and x.fiscal_ym == "202612" and x.signal == "TURN" for x in rev8)
    # 알림 하루 1통 — 관심종목 |pct| ≥ 5 또는 TURN(p4_expect.yaml 과 같은 종목)
    (text, kw) = note.sent[0]
    assert len(note.sent) == 1 and d["alert"] == "queued"
    assert kw == {"kind": "alert.revision", "source": "consensus.snapshot", "as_of": TODAY,
                  "parse_mode": None}  # fmt: skip
    shown = sorted(c for c in UNIVERSE if f"({c})" in text)
    assert shown == expect_alert_codes()
    # 이름은 "KIS 추정 변화"(§4.7 — 컨센서스라 부르지 않는다), 창은 config 의 revision_windows
    assert text.startswith(f"{cs.LABEL} 변화(리비전) 2026-11-16 — 관심종목 4개")
    assert "기준: 7·30일 전 스냅" in text and "컨센서스" not in text
    assert "nan" not in text.lower() and "None" not in text


def test_same_day_rerun_is_idempotent_and_skips_stored_estimates() -> None:
    r = seeded()
    kis = DirectKis(FakeKisServer(lambda: NOW, consensus=world()))
    run(r, kis, notify=Notify())
    before = (
        len(r.consensus.snapshot_t.all()),
        len(r.consensus.opinion_t.all()),
        len(r.consensus.target_t.all()),
        len(r.consensus.revision_t.all()),
    )
    kis2 = DirectKis(FakeKisServer(lambda: NOW, consensus=world()))
    res = run(r, kis2, notify=Notify())
    assert res.status == "ok"
    assert kis2.count(TR_ESTIMATE.tr_id) == len(EMPTY)  # 오늘 스냅이 있는 종목은 건너뛴다
    after = (
        len(r.consensus.snapshot_t.all()),
        len(r.consensus.opinion_t.all()),
        len(r.consensus.target_t.all()),
        len(r.consensus.revision_t.all()),
    )
    assert after == before


def test_no_history_means_no_baseline_and_no_alert() -> None:
    r = seeded(history=False)
    note = Notify()
    res = run(r, DirectKis(FakeKisServer(lambda: NOW, consensus=world())), notify=note)
    assert res.status == "ok" and note.sent == [] and res.detail["alert"].startswith("대상 없음")
    revs = r.consensus.revisions("994010", TODAY)
    assert revs and all(
        x.baseline is None and x.pct is None for x in revs if x.metric != "target_mean"
    )


def test_watch_code_without_todays_snapshot_is_stored_stale_but_not_alerted() -> None:
    """그날 KIS 빈 응답인 관심종목 — 지난 스냅의 리비전은 stale 로 저장하되 알림에는 없다."""
    r = seeded()

    def empty_994010(tr: str, p: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == TR_ESTIMATE.tr_id and p.get("SHT_CD") == "994010":
            return {"rt_cd": "0", "output1": {}, "output2": [], "output3": [], "output4": []}
        return body

    note = Notify()
    kis = DirectKis(FakeKisServer(lambda: NOW, consensus=world()), hook=empty_994010)
    res = run(r, kis, notify=note)
    assert res.status == "ok" and res.detail["alert_skipped_stale"] == ["994010"]
    assert r.consensus.snapshots("994010", TODAY, TODAY) == []
    revs = [x for x in r.consensus.revisions("994010", TODAY) if x.metric != "target_mean"]
    assert revs and all(x.quality is Quality.STALE for x in revs)
    (text, _kw) = note.sent[0]
    assert "(994010)" not in text
    assert sorted(c for c in UNIVERSE if f"({c})" in text) == [
        c for c in expect_alert_codes() if c != "994010"
    ]


def test_transient_failure_leaves_the_key_and_retry_takes_the_rest() -> None:
    r = seeded()
    server = FakeKisServer(lambda: NOW, consensus=world())
    server.inject("HTTP500", 2, tr_id=TR_ESTIMATE.tr_id)
    note = Notify()
    res = run(r, DirectKis(server), notify=note)
    assert res.status == "failed"
    assert set(res.collected) == {KEYS[1]}  # 의견 키만 끝났다
    assert res.detail["consensus_estimate"]["retry_codes"] == 2
    assert note.sent == [] and "revisions" not in res.detail  # 미완이면 파생·알림 없음
    kis = DirectKis(FakeKisServer(lambda: NOW, consensus=world()))
    again = run(r, kis, keys=(KEYS[0],), notify=note)
    assert again.status == "ok" and again.collected == (KEYS[0],)
    # 남은 둘 + 빈 응답 종목만 다시 부른다
    assert kis.count(TR_ESTIMATE.tr_id) == 2 + len(EMPTY)
    assert len(note.sent) == 1


def test_permanent_failures_over_the_cap_fail_the_key() -> None:
    r = seeded()

    def broken(tr: str, _p: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        return {"rt_cd": "0", "output": "깨짐"} if tr == TR_OPINION.tr_id else body

    server = FakeKisServer(lambda: NOW, consensus=world())
    res = run(r, DirectKis(server, hook=broken), notify=Notify())
    assert res.status == "failed" and set(res.collected) == {KEYS[0]}
    assert res.detail["invest_opinion"]["skipped_codes"] == len(UNIVERSE)
    assert "종목 고유 실패" in res.detail["errors"][KEYS[1].label()]


def test_notify_failure_fails_the_run_without_collecting() -> None:
    r = seeded()
    res = run(r, DirectKis(FakeKisServer(lambda: NOW, consensus=world())), notify=Notify(False))
    assert res.status == "failed" and res.collected == ()
    assert "알림을 넣지 못했다" in res.detail["errors"]["alert"]
    assert r.consensus.revisions("994010", TODAY)  # 받은 값·파생은 남는다 — 재시도가 다시 보낸다


def test_missing_notify_resource_is_recorded_not_failed() -> None:
    r = seeded()
    res = run(r, DirectKis(FakeKisServer(lambda: NOW, consensus=world())))
    assert res.status == "ok" and res.detail["alert"].startswith("notify 자원 없음")


def test_derive_failure_uncollects(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("파생 실패")

    monkeypatch.setattr(cs, "derive", boom)
    res = run(seeded(), DirectKis(FakeKisServer(lambda: NOW, consensus=world())), notify=Notify())
    assert res.status == "failed" and res.collected == ()
    assert "파생 실패" in res.detail["errors"]["derive"]


def test_universe_missing_fails_and_top_n_adds_market_cap_leaders() -> None:
    r = memory_repos()
    kis = DirectKis(FakeKisServer(lambda: NOW, consensus=world()))
    res = run(r, kis)
    assert res.status == "failed" and "유니버스" in res.detail["reason"] and kis.calls == []
    # 관심종목이 없어도 top_n 이 시총 상위 보통주를 채운다(스팩·리츠·코넥스·우선주 제외)
    r = seeded()
    r.journal.replace_watchlist("main", [], loaded_by="test", now=NOW)
    kis = DirectKis(FakeKisServer(lambda: NOW, consensus=world()))
    res = run(r, kis, top_n=3)
    w = world()
    snaps = {s.code: s for s in w.snaps(w.last_krx_day)}
    commons = sorted(
        (s.mktcap or 0, c)
        for c, s in snaps.items()
        if w.stock(c).kind == "common" and w.stock(c).market != "KONEX"
    )
    top3 = {c for _cap, c in commons[-3:]}
    assert {c[1].get("SHT_CD") for c in kis.calls if c[0] == TR_ESTIMATE.tr_id} == top3
    assert res.detail["universe"]["watch"] == 0 and res.detail["alert"].startswith("대상 없음")


def test_unknown_dataset_or_wrong_day_key_fails_only_that_key() -> None:
    r = seeded()
    other = BY_ID["KIS:stock_quote_eod"].key(DAY, BY_ID["KIS:stock_quote_eod"].venues[0])
    wrong = BY_ID["KIS:invest_opinion"].key("2026-11-13")
    res = run(r, DirectKis(FakeKisServer(lambda: NOW, consensus=world())),
              keys=(KEYS[0], other, wrong))  # fmt: skip
    assert res.status == "failed" and res.collected == (KEYS[0],)
    assert set(res.detail["errors"]) == {other.label(), wrong.label()}


def test_skipped_without_keys_and_priority_from_limits() -> None:
    c = ctx("consensus.snapshot", DAY, (), now=NOW, repos=seeded())
    assert cs.run(c).status == "skipped"
    c = ctx("consensus.snapshot", DAY, KEYS, now=NOW, repos=memory_repos())
    res = dict(c.resources)
    res.pop("kis_priority")
    c2 = type(c)(c.job, c.as_of, c.run_id, c.attempt, c.now, c.keys, None, res)
    assert cs._priority(c2) is cs.DEFAULT_PRIORITY  # pyright: ignore[reportPrivateUsage]


def test_revision_text_lines() -> None:
    assert cs.revision_text({}, {}, TODAY, min_abs_pct=5.0) is None
