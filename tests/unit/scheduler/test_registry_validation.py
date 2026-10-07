"""작업 등록부 검증(설계 §6.6) — 실제 `config/jobs.yaml` 이 통과하고, 규칙을 어기면 잡힌다.

1 형식(모르는 키·이름) · 2 cron·tz·when·as_of · 3 의존 존재·비순환 · 4 같은 데이터셋은 한 곳만 ·
5 카탈로그·등급↔스키마 · 6 enabled ⇒ 처리기 import, external ⇒ 꺼짐 · 7 U2 · 8 inventory (c) 전부 ·
9 예산 · 10 P2 에 켜진 작업 목록.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

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


def test_p2_enables_only_three_jobs_and_core_services() -> None:  # §6.6-10, §6.9
    assert {j.name for j in REG.enabled_jobs()} == P2_ENABLED
    assert {s.name for s in REG.services if s.enabled} == {"auth", "notifier", "scheduler"}
    assert all(not j.enabled for j in REG.jobs if j.external)


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
    for kind, time_ in (("brief.morning", "10 8 * * *"), ("brief.closing", "40 16 * * 1-5")):
        senders = [j for j in REG.jobs if j.notify is not None and j.notify.kind == kind]
        assert len(senders) == 1 and senders[0].schedule.cron == time_
        assert POLICIES[kind] == "daily"


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
    errs = mutated(lambda d: job(d, "brief.closing")["schedule"].update(catch_up_until="16:00"))
    assert any("catch_up_until" in e for e in errs)


def test_defaults_fill_jobs() -> None:
    data = raw()
    j = job(data, "board.daily")
    j.pop("retry")
    reg = Registry.parse(data)
    assert reg.by_name("board.daily").retry.backoff_s == (60, 300, 900)
    assert reg.by_name("board.daily").deadline_min == 120
    assert reg.by_name("board.daily").schedule.tz == "Asia/Seoul"
    assert reg.by_name("us.eod").schedule.tz == "America/New_York"
