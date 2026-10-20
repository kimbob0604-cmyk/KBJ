"""`kbj.services.engine.index_review` —
작업(index.calendar·index.predict)·CLI(목록·컷오프·백테스트)를
합성 세계 + 메모리 저장소로(외부 호출 없음)."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.services.engine import index_review as svc
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.index_world import (
    BASE,
    CLOCK,
    build_index_world,
    golden_doc,
    load_into,
)


def world_repos(*, members: bool = True) -> MemoryRepos:
    repos = memory_repos()
    load_into(repos, build_index_world(), members=members)
    return repos


def ctx(job: str, repos: MemoryRepos, as_of: str = BASE.isoformat()) -> JobContext:
    res = {"repos": repos, "index_reviews": build_index_world().config}
    return JobContext(job=job, as_of=as_of, run_id="r1", attempt=1, now=CLOCK, resources=res)


def test_calendar_job_writes_estimated_unconfirmed_rows() -> None:
    repos = world_repos()
    res = svc.calendar(ctx(svc.JOB_CALENDAR, repos))
    assert res.status == "ok" and res.rows == 4
    cal = repos.index_review.calendar()
    assert [(e.index_id, e.review_id) for e in cal][:2] == [("msci_korea", "2026-05"),
                                                             ("msci_korea", "2026-11")]  # fmt: skip
    assert {e.quality for e in cal if not e.confirmed} == {Quality.ESTIMATED}


def test_predict_job_matches_engine_golden() -> None:
    repos = world_repos()
    svc.calendar(ctx(svc.JOB_CALENDAR, repos))
    res = svc.predict(ctx(svc.JOB_PREDICT, repos))
    assert res.status == "ok", res.detail
    assert res.detail["by_index"] == {"msci_korea": "ok", "kospi200": "ok", "kosdaq150": "ok"}
    gold = golden_doc()["reviews"]
    for key, want in gold.items():
        idx, review = key.split(":")
        runs = repos.index_review.latest_runs(idx, review)
        assert [r.cutoff_kind for r in runs] == ["model"] and runs[0].base_date == BASE
        assert runs[0].cutoff_won == want["cutoff"] and runs[0].quality is Quality.ESTIMATED
        rows = repos.index_review.predictions(idx, review, BASE, "model")
        assert sorted((p.code, p.verdict) for p in rows) == sorted(
            (r["code"], r["outcome"]) for r in want["rows"]
        ), key
        for p in rows:
            w = next(r for r in want["rows"] if r["code"] == p.code)
            assert p.evidence["required_rise"] == pytest.approx(w["required_rise"])


def test_base_date_is_frozen_after_review_base() -> None:
    repos = world_repos()
    rep = svc.run_predict(repos, build_index_world().config, date(2026, 11, 3), now=CLOCK,
                          loaded_by="t")  # fmt: skip
    assert rep.status == "ok"
    run = repos.index_review.latest_runs("msci_korea", "2026-11")[0]
    assert run.base_date == BASE  # 기준일(10-30) 값으로 고정


def test_no_constituents_writes_status_only() -> None:
    repos = world_repos(members=False)
    rep = svc.run_predict(repos, build_index_world().config, BASE, now=CLOCK, loaded_by="t")
    assert rep.status == "ok"
    for idx, review in (("msci_korea", "2026-11"), ("kospi200", "2026-12")):
        runs = repos.index_review.latest_runs(idx, review)
        assert runs[0].status == "no_constituents" and runs[0].n_add == 0
        assert runs[0].quality is not Quality.OK
        assert repos.index_review.predictions(idx, review, BASE, "model") == []


def test_user_cutoff_adds_a_second_run() -> None:
    repos = world_repos()
    cfg = build_index_world().config
    svc.sync_calendar(repos, cfg, now=CLOCK, loaded_by="t")
    repos.index_review.set_user_cutoff("msci_korea", "2026-11", 2 * 10**12, loaded_by="t",
                                       now=CLOCK)  # fmt: skip
    svc.run_predict(repos, cfg, BASE, now=CLOCK, loaded_by="t")
    runs = repos.index_review.latest_runs("msci_korea", "2026-11")
    assert [r.cutoff_kind for r in runs] == ["model", "user"]
    assert runs[1].cutoff_won == 2 * 10**12 and runs[1].cutoff_method == "user"


def test_one_index_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = world_repos()

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("합성 실패 token=abc")

    monkeypatch.setattr(svc, "predict_krx", boom)
    res = svc.predict(ctx(svc.JOB_PREDICT, repos))
    assert res.status == "failed"
    assert res.detail["by_index"]["msci_korea"] == "ok"
    assert set(res.detail["failed"]) == {"kospi200", "kosdaq150"}
    assert repos.index_review.latest_runs("msci_korea", "2026-11")


def test_krx_passive_uses_config_non_etf_estimate() -> None:
    repos = world_repos()
    svc.run_predict(repos, build_index_world().config, BASE, now=CLOCK, loaded_by="t")
    aum = repos.index_review.passive_aum("kospi200", BASE)
    assert [(a.kind, a.aum_won) for a in aum] == [("non_etf", 5 * 10**12)]
    run = repos.index_review.latest_runs("kospi200", "2026-12")[0]
    assert run.aum_won == 5 * 10**12


def _cli(argv: list[str], repos: MemoryRepos) -> tuple[int, list[dict[str, Any]]]:
    out = io.StringIO()
    code = svc.main(argv, repos=repos, cfg=build_index_world().config, now=CLOCK, out=out)
    return code, [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


def test_cli_load_constituents_closes_and_opens(tmp_path: Path) -> None:
    repos = world_repos(members=False)
    f = tmp_path / "list.csv"
    f.write_text("code,name,fif\n993001,가,0.5\n993002,나,\n", encoding="utf-8")
    code, out = _cli(["load-constituents", "--index", "msci_korea", "--file", str(f),
                      "--as-of", "2026-06-01", "--source", "user"], repos)  # fmt: skip
    assert code == 0 and out[0]["opened"] == 2
    f.write_text("code,fif\n993001,55\n993003,0.3\n", encoding="utf-8")  # 퍼센트 FIF·993002 빠짐
    code, out = _cli(["load-constituents", "--index", "msci_korea", "--file", str(f),
                      "--as-of", "2026-12-01", "--source", "press"], repos)  # fmt: skip
    assert code == 0 and out[0]["closed"] == 2 and out[0]["opened"] == 2
    now = {c.code: c.fif for c in repos.index_review.constituents("msci_korea", date(2026, 12, 1))}
    assert now == {"993001": 0.55, "993003": 0.3}
    old = {c.code for c in repos.index_review.constituents("msci_korea", date(2026, 11, 30))}
    assert old == {"993001", "993002"}
    f.write_text("name\nx\n", encoding="utf-8")
    code, out = _cli(["load-constituents", "--index", "msci_korea", "--file", str(f),
                      "--as-of", "2026-12-01", "--source", "user"], repos)  # fmt: skip
    assert code == 1 and out[0]["error"] == "input"
    code, out = _cli(["load-constituents", "--index", "nope", "--file", str(f),
                      "--as-of", "2026-12-01", "--source", "user"], repos)  # fmt: skip
    assert code == 1


def test_cli_set_cutoff() -> None:
    repos = world_repos()
    svc.sync_calendar(repos, build_index_world().config, now=CLOCK, loaded_by="t")
    code, out = _cli(["set-cutoff", "--index", "msci_korea", "--review", "2026-11",
                      "--won", "3000000000000"], repos)  # fmt: skip
    assert code == 0 and out == [{"updated": True}]
    code, _ = _cli(["set-cutoff", "--index", "msci_korea", "--review", "2026-11", "--clear"],
                   repos)  # fmt: skip
    assert code == 0 and repos.index_review.calendar("msci_korea")[1].user_cutoff_won is None


def test_cli_backtest_scores_past_review() -> None:
    repos = world_repos()
    code, out = _cli(["backtest", "--index", "msci_korea"], repos)
    assert code == 0
    assert {(r["review"], r["side"]) for r in out} == {("2026-05", "add"), ("2026-05", "delete")}
    w = build_index_world()
    before, after = w.past_members["msci_korea"]
    add = next(r for r in out if r["side"] == "add")
    assert set(add["hits"]) | set(add["misses"]) == after - before
    # 기준일 뒤(효력 전) 리뷰는 백테스트하지 않는다 — 기록이 없으면 no_history
    code, out = _cli(["backtest", "--index", "kospi200"], repos)
    assert code == 1 and out == []


def _proxy_cfg(size: int | None) -> Any:
    cfg = build_index_world().config
    idxs = [
        i.model_copy(update={"proxy_etf": "990200", "size": size}) if i.id == "kospi200" else i
        for i in cfg.indexes
    ]
    return cfg.model_copy(update={"indexes": tuple(idxs)})


def _put_proxy(repos: MemoryRepos, codes: list[str]) -> None:
    from kbj.core.rows import Fund, HoldingRow

    repos.etf.upsert_funds(
        [Fund("rise:PX", "합성운용", "PX", "990200", "합성 200", "시장대표", True, "full", True, 0,
              "ETF_ISSUERS:합성운용")],
        loaded_by="t", now=CLOCK,
    )  # fmt: skip
    rows = [HoldingRow(c, f"합성{c}", 100.0, 0.5, 1e6, "ETF_ISSUERS:합성운용", Quality.OK)
            for c in codes]  # fmt: skip
    repos.etf.put_holdings("rise:PX", BASE, rows, loaded_by="t", received_at=CLOCK)


def test_proxy_etf_fills_constituents_and_predicts() -> None:
    """목록을 올리지 않아도 완전 복제 ETF 의 구성종목 공시로 구성종목을 맞추고 예측한다
    (2026-10-20 — 늘 '구성종목 목록 없음'이던 것)."""
    members = sorted(r.code for r in world_repos().index_review.constituents("kospi200", BASE))
    repos = world_repos(members=False)
    _put_proxy(repos, members)
    cfg = _proxy_cfg(len(members))
    rep = svc.run_predict(repos, cfg, BASE, now=CLOCK, loaded_by="t")
    assert rep.by_index["kospi200"] == "ok", rep.detail()
    got = repos.index_review.constituents("kospi200", BASE)
    assert sorted(r.code for r in got) == members
    assert {r.source for r in got} == {"etf"}
    # 다시 돌려도 같은 집합이면 쓰지 않는다(멱등)
    assert (
        svc.sync_from_proxy(
            repos,
            cfg.indexes[1]
            if cfg.indexes[1].id == "kospi200"
            else next(i for i in cfg.indexes if i.id == "kospi200"),
            BASE,
            now=CLOCK,
            loaded_by="t",
        )
        is None
    )


def test_proxy_size_mismatch_is_not_used() -> None:
    members = sorted(r.code for r in world_repos().index_review.constituents("kospi200", BASE))
    repos = world_repos(members=False)
    _put_proxy(repos, members[:-5])  # 표본 복제처럼 5종목 모자람
    rep = svc.run_predict(repos, _proxy_cfg(len(members)), BASE, now=CLOCK, loaded_by="t")
    assert "지수" in rep.proxy_notes["kospi200"]
    assert repos.index_review.constituents("kospi200", BASE) == []


def test_user_list_is_not_overwritten_by_proxy() -> None:
    repos = world_repos()  # 사용자 목록(합성 세계 source)이 있다
    before = {(r.code, r.source) for r in repos.index_review.constituents("kospi200", BASE)}
    _put_proxy(repos, sorted(c for c, _ in before)[:-1])
    svc.run_predict(repos, _proxy_cfg(None), BASE, now=CLOCK, loaded_by="t")
    after = {(r.code, r.source) for r in repos.index_review.constituents("kospi200", BASE)}
    assert after == before
