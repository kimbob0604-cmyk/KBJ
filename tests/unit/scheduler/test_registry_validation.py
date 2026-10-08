"""작업 등록부 검증(설계 §6.6) — 실제 `config/jobs.yaml` 이 통과하고, 규칙을 어기면 잡힌다.

1 형식(모르는 키·이름) · 2 cron·tz·when·as_of · 3 의존 존재·비순환 · 4 같은 데이터셋은 한 곳만 ·
5 카탈로그·등급↔스키마 · 6 enabled ⇒ 처리기 import, external ⇒ 꺼짐 · 7 U2 · 8 inventory (c) 전부 ·
9 예산 · 10 단계별 켜진 작업 목록(P2 셋 + P3 아홉 — docs/p3_design.md §0.4) · 11 장 마감 뒤 발송은
16:00 과 무른 의존 wait_min(ADR 0018).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.markets import load_markets
from kbj.data.catalog import all_datasets, claimable
from kbj.data.limits import load_limits
from kbj.services.notifier.policy import load_notify_config
from kbj.services.scheduler.__main__ import main as cli_main
from kbj.services.scheduler.__main__ import validate_file
from kbj.services.scheduler.registry import (
    Registry,
    RegistryError,
    budgets_from_limits,
    migration_tables,
    read_legacy_jobs,
)

ROOT = Path(__file__).resolve().parents[3]
JOBS = ROOT / "config" / "jobs.yaml"
LEGACY = Path(__file__).with_name("legacy_jobs.txt")
CATALOG = all_datasets()
BUDGETS = budgets_from_limits(load_limits())
POLICIES = {k: p.dedup for k, p in load_notify_config().kinds.items()}
TABLES = migration_tables()
P2_ENABLED = {"ops.nightly", "filings.corp_code", "ops.watchdog"}
P3_ENABLED = {
    "krx.daily",
    "market.backfill",
    "market.close_collect",
    "flows.intraday",
    "market.intraday",
    "board.daily",
    "board.confirm",
    "etf.collect",
    "public.export",
}
# 단계별 켜진 작업(웨이브 3 묶음 S 가 P3 를 켰다). 새 단계가 작업을 켜면 이 표에 한 줄 더한다.
ENABLED_BY_PHASE = {"P2": P2_ENABLED, "P3": P3_ENABLED}


def raw() -> dict[str, Any]:
    return yaml.safe_load(JOBS.read_text(encoding="utf-8"))


def errors_of(data: dict[str, Any]) -> list[str]:
    return Registry.parse(data).validate(
        CATALOG, budgets=BUDGETS, notify_policies=POLICIES, tables=TABLES
    )


def job(data: dict[str, Any], name: str) -> dict[str, Any]:
    for j in data["jobs"]:
        if j["name"] == name:
            return j
    raise KeyError(name)


def mutated(fn: Callable[[dict[str, Any]], None]) -> list[str]:
    data = copy.deepcopy(raw())
    fn(data)
    return errors_of(data)


REG = Registry.load(JOBS)


# ── 실제 등록부 ───────────────────────────────────────────────────────────────────────────


def test_real_registry_passes_every_check() -> None:
    assert errors_of(raw()) == []
    assert validate_file(JOBS) == []


def test_cli_validate_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["validate", str(JOBS)]) == 0
    bad = tmp_path / "jobs.yaml"
    data = raw()
    job(data, "krx.daily")["collects"].append(
        {"source": "KRX", "dataset": "drv/fut_bydd_trd", "as_of": "prev_trading_day"}
    )
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    for name in ("limits.yaml", "notify.yaml"):
        (tmp_path / name).write_text((ROOT / "config" / name).read_text("utf-8"), "utf-8")
    assert cli_main(["validate", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "gex.krx_derivatives" in out and "krx.daily" in out


def test_enabled_jobs_by_phase_and_core_services() -> None:  # §6.6-10, §6.9, P3 §0.4
    enabled = {j.name for j in REG.enabled_jobs()}
    assert enabled == set().union(*ENABLED_BY_PHASE.values())
    for phase, names in ENABLED_BY_PHASE.items():
        assert {j.name for j in REG.enabled_jobs() if j.phase == phase} == names, phase
    assert {s.name for s in REG.services if s.enabled} == {"auth", "notifier", "scheduler"}
    assert all(not j.enabled for j in REG.jobs if j.external)
    # P4 이후 단계는 아직 하나도 켜지 않는다(처리기 없음)
    assert not [j.name for j in REG.enabled_jobs() if j.phase not in ENABLED_BY_PHASE]


def test_every_claimable_catalog_dataset_has_exactly_one_owner() -> None:
    owners = REG.owners_of()
    for ds_id, spec in CATALOG.items():
        if claimable(spec):
            assert len(owners.get(ds_id, [])) == 1, ds_id
        else:  # 즉석 조회(KIS:quote_on_demand)는 등록부 수집 대상이 아니다
            assert ds_id not in owners


def test_inventory_c_lines_appear_exactly_once() -> None:  # §6.6-8
    lines = read_legacy_jobs(LEGACY)
    assert sum(1 for x in lines if x.startswith("SD:") and ":" not in x[3:]) == 43  # add_job 43개
    assert REG.legacy_coverage(lines) == []


def test_u2_one_morning_and_one_closing_brief() -> None:  # §6.6-7
    for kind, time_ in (("brief.morning", "10 8 * * *"), ("brief.closing", "0 16 * * 1-5")):
        senders = [j for j in REG.jobs if j.notify is not None and j.notify.kind == kind]
        assert len(senders) == 1 and senders[0].schedule.cron == time_
        assert POLICIES[kind] == "daily"


# 장 마감 뒤 발송 작업(ADR 0018 — 사용자 결정 2026-10-08). 아침·장중·사건·운영 감시 발송은 그대로.
POST_CLOSE_SENDERS = {
    "brief.closing": "brief.closing",
    "flows.report": "flows.report",
    "consensus.snapshot": "alert.revision",
}


def test_every_post_close_telegram_fires_at_1600() -> None:  # ADR 0018
    close = time(15, 30)
    senders = {
        j.name: j.notify.kind
        for j in REG.jobs
        if j.notify is not None
        and (first := j.schedule.first_time()) is not None
        and first >= close
        and j.schedule.tz == "Asia/Seoul"
    }
    assert senders == POST_CLOSE_SENDERS
    for name in senders:
        spec = REG.by_name(name)
        assert spec.schedule.cron == "0 16 * * 1-5" and spec.schedule.when == "trading_day", name
    # 그대로 두는 것: 아침 브리핑·장중 규칙·공시 사건 알림·운영 감시(장중부터 30분마다)
    assert REG.by_name("brief.morning").schedule.cron == "10 8 * * *"
    assert REG.by_name("filings.dart_feed").schedule.cron == "* 7-19 * * 1-5"
    assert REG.by_name("ops.watchdog").schedule.cron == "0,30 8-20 * * 1-5"
    assert REG.by_name("brief.closing").schedule.catch_up_until == "20:30"


def test_post_close_data_is_ready_for_the_1600_sends() -> None:  # ADR 0018
    """보드는 16:00 에 발화해 마감 수집(굳은)을 기다리고, 발송은 같은 16:00 의 보드를 무른 의존
    wait_min 으로 기다린다(보드 실패가 발송을 막지 않는다)."""
    board = REG.by_name("board.daily")
    assert board.schedule.cron == "0 16 * * 1-5"
    assert [(d.job, d.hard) for d in board.depends_on] == [("market.close_collect", True)]
    for name in ("brief.closing", "flows.report"):
        deps = {d.job: d for d in REG.by_name(name).depends_on}
        assert deps["market.close_collect"].hard, name
        assert not deps["board.daily"].hard and deps["board.daily"].wait_min == 20, name
    consensus = REG.by_name("consensus.snapshot")
    assert [(d.job, d.hard) for d in consensus.depends_on] == [("market.close_collect", True)]


def test_d7_flow_datasets_are_registered_with_venues() -> None:
    must = [
        "KRX:sto/stk_bydd_trd",
        "KRX:sto/ksq_bydd_trd",
        "KRX:etp/etf_bydd_trd",
        "KIS:stock_investor_daily",
        "KIS:inst_foreign_top",
        "KIS:inst_foreign_intraday",
        "KIS:turnover_rank_intraday",
        "KIS:etf_quote_intraday",
    ]
    owners = REG.owners_of()
    for ds in must:
        assert ds in CATALOG and len(owners[ds]) == 1
    assert {v.value for v in CATALOG["KIS:stock_investor_daily"].venues} == {"KRX", "NXT", "TOTAL"}


def test_budget_estimates_fit_caps() -> None:  # §6.6-9
    calls = REG.daily_calls(CATALOG)
    assert 0 < calls["krx"] <= BUDGETS["krx"][0]  # type: ignore[operator]
    assert calls["dart"] <= BUDGETS["dart"][0]  # type: ignore[operator]
    assert calls["krx"] == 10 * 12 + 2 * 12  # krx.daily 10키 × 12회 + GX 파생 2 × 12


# ── 규칙을 어기면 잡힌다 ──────────────────────────────────────────────────────────────────


def test_unknown_key_and_bad_name_are_format_errors() -> None:  # §6.6-1
    data = raw()
    job(data, "ops.nightly")["shedule"] = {}
    with pytest.raises(RegistryError, match="shedule"):
        Registry.parse(data)
    data = raw()
    job(data, "ops.nightly")["name"] = "Ops-Nightly"
    with pytest.raises(RegistryError, match="이름"):
        Registry.parse(data)


def test_duplicate_names() -> None:
    errs = mutated(lambda d: d["jobs"].append(copy.deepcopy(job(d, "ops.nightly"))))
    assert any("이름이 겹친다: ops.nightly" in e for e in errs)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("cron", "61 3 * * *", "cron"),
        ("tz", "Asia/Seol", "tz"),
        ("when", "holiday", "when"),
    ],
)
def test_schedule_values(field: str, value: str, match: str) -> None:  # §6.6-2
    data = raw()
    job(data, "ops.nightly")["schedule"][field] = value
    with pytest.raises(RegistryError, match=match):
        Registry.parse(data)


def test_two_triggers_and_bad_as_of() -> None:
    data = raw()
    job(data, "ops.nightly")["schedule"]["manual"] = True
    with pytest.raises(RegistryError, match="트리거"):
        Registry.parse(data)
    data = raw()
    job(data, "krx.daily")["collects"][0]["as_of"] = "yesterday"
    with pytest.raises(RegistryError):
        Registry.parse(data)


def test_depends_missing_and_cycle() -> None:  # §6.6-3
    errs = mutated(lambda d: job(d, "board.daily")["depends_on"].append({"job": "nope.job"}))
    assert any("대상이 없다: nope.job" in e for e in errs)

    def cycle(d: dict[str, Any]) -> None:
        job(d, "market.close_collect")["depends_on"] = [{"job": "brief.closing", "hard": False}]

    errs = mutated(cycle)
    assert any("순환" in e for e in errs)


def soft_board(d: dict[str, Any], name: str = "brief.closing") -> dict[str, Any]:
    for dep in job(d, name)["depends_on"]:
        if dep["job"] == "board.daily":
            return dep
    raise KeyError("board.daily")


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"hard": True}, "무른 의존"),
        ({"as_of": "prev"}, "as_of same"),
        ({"wait_min": 0}, "wait_min"),
    ],
)
def test_soft_wait_shape(change: dict[str, Any], match: str) -> None:  # ADR 0018
    data = raw()
    soft_board(data).update(change)
    with pytest.raises(RegistryError, match=match):
        Registry.parse(data)


def test_soft_wait_rules() -> None:  # ADR 0018 — 실행기는 진행 중인 실행만 기다린다
    errs = mutated(lambda d: job(d, "board.daily")["schedule"].update(cron="10 16 * * 1-5"))
    assert any("brief.closing" in e and "늦다" in e for e in errs)
    assert any("flows.report" in e and "늦다" in e for e in errs)
    errs = mutated(lambda d: soft_board(d).update(wait_min=120))
    assert any("brief.closing" in e and "마감 120분" in e for e in errs)

    def external(d: dict[str, Any]) -> None:
        job(d, "brief.closing")["depends_on"].append(
            {"job": "gex.day_minutes", "hard": False, "wait_min": 10}
        )

    errs = mutated(external)
    assert any("gex.day_minutes" in e and "external" in e for e in errs)
    assert any("gex.day_minutes" in e and "발화 시각" in e for e in errs)


def test_hard_dependency_needs_same_as_of_kind() -> None:
    errs = mutated(lambda d: job(d, "brief.closing")["depends_on"].append({"job": "krx.daily"}))
    assert any("as_of 종류가 다르다" in e for e in errs)


def test_same_dataset_in_two_jobs_names_both() -> None:  # §6.6-4
    def dup(d: dict[str, Any]) -> None:
        job(d, "krx.daily")["collects"].append(
            {"source": "KRX", "dataset": "drv/fut_bydd_trd", "as_of": "prev_trading_day"}
        )

    errs = mutated(dup)
    assert any(
        "KRX:drv/fut_bydd_trd" in e and "krx.daily" in e and "gex.krx_derivatives" in e
        for e in errs
    )


def test_same_dataset_in_service_and_job() -> None:
    def dup(d: dict[str, Any]) -> None:
        job(d, "rules.intraday")["collects"].append(
            {"source": "KIS", "dataset": "ws_ticks", "as_of": "minute"}
        )

    errs = mutated(dup)
    assert any("KIS:ws_ticks" in e and "gex.ws_gateway" in e for e in errs)


def test_unknown_dataset_and_wrong_as_of_and_venue() -> None:  # §6.6-5
    def bad(d: dict[str, Any]) -> None:
        job(d, "ops.nightly")["collects"] = [
            {"source": "NAVER", "dataset": "daily", "as_of": "trade_date"}
        ]
        job(d, "macro.evening")["collects"][0]["as_of"] = "run_date"
        job(d, "market.fsc_daily")["collects"][1]["venues"] = ["NXT"]

    errs = mutated(bad)
    assert any("카탈로그에 없는 데이터셋 NAVER:daily" in e for e in errs)
    assert any("ECOS:817Y002 as_of run_date" in e for e in errs)
    assert any("DATAGO:15094807 에 없는 거래소" in e for e in errs)


def test_private_source_cannot_write_public_schema() -> None:
    def leak(d: dict[str, Any]) -> None:
        job(d, "market.close_collect")["writes"].append("pub_market_stats.cma")

    errs = mutated(leak)
    assert any("로그인 등급" in e and "pub_market_stats.cma" in e for e in errs)


def test_store_must_be_in_writes_and_enabled_tables_must_exist() -> None:
    errs = mutated(lambda d: job(d, "filings.corp_code").update(writes=[]))
    assert any("pub_filings.corp_code 가 writes 에 없다" in e for e in errs)
    errs = mutated(lambda d: job(d, "ops.nightly")["writes"].append("prv_alerts.nothing"))
    assert any("prv_alerts.nothing" in e and "마이그레이션" in e for e in errs)
    # 꺼진 작업의 표는 그 단계에서 생긴다 — 검사하지 않는다(묶음 D 요청)
    assert "prv_board.daily_state" not in TABLES


def test_enabled_owner_must_import_and_external_must_be_off() -> None:  # §6.6-6
    errs = mutated(lambda d: job(d, "us.eod").update(enabled=True))
    assert any("us.eod" in e and "처리기를 부를 수 없다" in e for e in errs)
    errs = mutated(lambda d: job(d, "kis.master").update(enabled=True))
    assert any("kis.master" in e and "enabled: false" in e for e in errs)
    errs = mutated(lambda d: job(d, "kis.master").update(owner="kbj.x:y"))
    assert any("legacy:" in e for e in errs)


def test_u2_violations() -> None:  # §6.6-7
    errs = mutated(lambda d: job(d, "flows.report").update(notify={"kind": "brief.closing"}))
    assert any("U2: brief.closing" in e for e in errs)
    errs = Registry.parse(raw()).validate(
        CATALOG, notify_policies={**POLICIES, "brief.morning": "body"}
    )
    assert any("U2: brief.morning" in e and "하루 1회" in e for e in errs)
    errs = mutated(lambda d: job(d, "board.daily").update(notify={"kind": "board.nope"}))
    assert any("board.nope" in e for e in errs)


def test_inventory_line_missing_or_twice() -> None:  # §6.6-8
    lines = read_legacy_jobs(LEGACY)
    data = raw()
    job(data, "krx.daily")["absorbs"].remove("SD:mark_etf_stocks")
    job(data, "brief.closing")["absorbs"].append("SD:tg_morning")
    errs = Registry.parse(data).legacy_coverage(lines)
    assert any("SD:mark_etf_stocks" in e and "0번" in e for e in errs)
    assert any("SD:tg_morning" in e and "2번" in e for e in errs)


def test_budget_missing_or_over_cap() -> None:  # §6.6-9
    errs = mutated(lambda d: job(d, "krx.daily").update(budget=None))
    assert any("krx.daily" in e and "예산 krx" in e for e in errs)
    errs = Registry.parse(raw()).validate(CATALOG, budgets={**BUDGETS, "krx": (100, None)})
    assert any("예산 krx: 하루 추정 144 > 상한 100" in e for e in errs)
    errs = Registry.parse(raw()).validate(CATALOG, budgets={**BUDGETS, "datago": (None, 3)})
    assert any("데이터셋별 상한 3" in e for e in errs)


def test_retry_until_and_catch_up_must_be_after_first_run() -> None:
    errs = mutated(lambda d: job(d, "krx.daily")["retry"].update(until="08:00"))
    assert any("retry.until" in e for e in errs)
    errs = mutated(lambda d: job(d, "brief.closing")["schedule"].update(catch_up_until="15:50"))
    assert any("catch_up_until" in e for e in errs)
    errs = mutated(lambda d: job(d, "brief.closing")["schedule"].update(catch_up_until="16:00"))
    assert any("catch_up_until" in e for e in errs)  # 첫 실행과 같아도 안 된다


def test_defaults_fill_jobs() -> None:
    data = raw()
    j = job(data, "board.daily")
    j.pop("retry")
    reg = Registry.parse(data)
    assert reg.by_name("board.daily").retry.backoff_s == (60, 300, 900)
    assert reg.by_name("board.daily").deadline_min == 120
    assert reg.by_name("board.daily").schedule.tz == "Asia/Seoul"
    assert reg.by_name("us.eod").schedule.tz == "America/New_York"


# ── P3 작업 행(docs/p3_design.md §0.4·§3.2 — 웨이브 1 묶음 M: 확정하되 꺼 둔다) ──────────────────

P3_JOBS = {
    "krx.daily": "kbj.services.collectors.krx_daily:run",
    "market.backfill": "kbj.services.collectors.krx_daily:backfill",
    "market.close_collect": "kbj.services.collectors.market_close:run",
    "flows.intraday": "kbj.services.collectors.market_intraday:flows",
    "market.intraday": "kbj.services.collectors.market_intraday:market",
    "board.daily": "kbj.services.engine.board:daily",
    "board.confirm": "kbj.services.engine.board:confirm",
    "etf.collect": "kbj.services.collectors.etf_holdings:run",
    "public.export": "kbj.services.public_export:run",
}
MOVED_TO_P5 = {"brief.closing", "flows.report", "us.universe", "us.eod", "market.fsc_daily"}


def test_p3_jobs_are_registered_and_on_after_wave_3() -> None:
    """웨이브 3(묶음 S)이 처리기를 붙인 뒤 enabled 를 뒤집었다 — owner 는 설계 그대로."""
    p3 = {j.name: j for j in REG.jobs if j.phase == "P3"}
    assert set(p3) == set(P3_JOBS) == P3_ENABLED
    for name, owner in P3_JOBS.items():
        assert p3[name].owner == owner, name
        assert p3[name].enabled, name
    assert {j.name for j in REG.jobs if j.phase == "P5"} >= MOVED_TO_P5
    assert not any(j.enabled for j in REG.jobs if j.name in MOVED_TO_P5)


def test_kis_venues_match_markets_config() -> None:
    """D-P3-9 — KIS 데이터셋의 collects.venues 는 config/markets.yaml kis.venues 와 같다.

    비우면 실행기가 카탈로그의 KRX·NXT·TOTAL 키를 모두 잡고 처리기는 부르지 않은 키를
    done 으로 돌려준다(묶음 C 요청 — 선점 장부에 '받지 않은 것을 done' 으로 남기지 않는다).
    """
    want = tuple(str(v) for v in load_markets().kis.venues)
    checked = 0
    for j in REG.jobs:
        if not j.enabled:
            continue
        for c in j.collects:
            spec = CATALOG[c.dataset_id]
            if c.source != "KIS" or not spec.venues:
                continue
            checked += 1
            assert tuple(v.value for v in c.venues) == want, (j.name, c.dataset_id)
    assert checked == 7  # close_collect 5 + flows.intraday 2


def test_p3_writes_and_collects_follow_the_design() -> None:
    w = {j.name: set(j.writes) for j in REG.jobs}
    board = {"prv_board.alltime", "prv_board.label", "prv_board.split_check",
             "prv_board.stock_day", "prv_board.artifact"}  # fmt: skip
    assert w["board.daily"] == board == w["board.confirm"]
    assert {"prv_market.stock_snapshot", "prv_etf.meta", "prv_market.eod_reconcile"} <= w[
        "krx.daily"
    ]
    # krx.daily 끝 단계가 검산 ③·분할 감지를 기록한다
    # (etf_holdings.record_flow_checks — 묶음 E3 요청, 묶음 S 연결)
    assert {"prv_etf.split_event", "prv_flows.ledger_check"} <= w["krx.daily"]
    assert {"prv_flows.investor_intraday", "prv_market.turnover_rank_intraday"} <= w[
        "flows.intraday"
    ]
    # etf.collect 는 메타 분류(운용사·테마·유형)도 쓴다(묶음 E3 요청)
    assert w["etf.collect"] == {
        "prv_etf.fund",
        "prv_etf.holding",
        "prv_etf.change_log",
        "prv_etf.meta",
    }
    # close_collect 는 당일 KIS 일봉(board 고가 기준 OHLC)도 쓴다(묶음 C 요청)
    assert {"prv_flows.investor_revision", "prv_market.daily_bar"} <= w["market.close_collect"]
    assert w["public.export"] == set()  # 파일만 쓴다 — 표 쓰기·수집 없음
    for name in P3_JOBS:  # 켤 때 표가 있어야 한다(등록부 검증이 enabled 작업에만 보는 것을 미리)
        assert w[name] <= TABLES, (name, w[name] - TABLES)
    collects = {j.name: {c.dataset_id for c in j.collects} for j in REG.jobs}
    assert collects["market.intraday"] == {"KIS:index_quote_intraday", "KIS:sector_quote_intraday"}
    assert "KIS:etf_investor_daily" in collects["market.close_collect"]
    assert collects["public.export"] == set() and collects["board.confirm"] == set()


def test_p3_intraday_retry_fits_the_slot() -> None:
    """D-P3-14 — 슬롯 안 재시도(20·40초), 마감 9분 → 다음 슬롯(10분) 전에 끝난다."""
    for name in ("flows.intraday", "market.intraday"):
        j = REG.by_name(name)
        assert j.schedule.equity is not None and j.schedule.equity.every_min == 10
        assert j.retry.max == 2 and j.retry.backoff_s == (20, 40) and j.deadline_min == 9
    confirm = REG.by_name("board.confirm")
    assert confirm.schedule.cron == "40 8 * * 1-5" and confirm.run_as_of() == "prev_trading_day"
    assert [(d.job, d.hard) for d in confirm.depends_on] == [("krx.daily", True)]
    assert REG.by_name("etf.collect").notify is None  # ETF 리포트 발송은 P5


def test_p3_handlers_import_and_a_missing_one_is_caught() -> None:
    """켜진 P3 작업은 처리기를 import 할 수 있어야 하고(실제 등록부 통과), 처리기가 없는
    owner 로 바꾸면 검증이 잡는다(웨이브 3 의 import 확인과 같은 겹)."""
    assert errors_of(raw()) == []
    errs = mutated(
        lambda d: job(d, "market.intraday").update(owner="kbj.services.collectors.nope:x")
    )
    assert any("market.intraday" in e and "처리기를 부를 수 없다" in e for e in errs)
