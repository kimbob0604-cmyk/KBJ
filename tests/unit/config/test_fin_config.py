"""`config/fin.yaml`·`config/reports.yaml` 해석기(kbj/config/fin.py) — 실제 파일이 통과하고, 틀린
값은 잡힌다.

근거: docs/p4_design.md §3.7. 값은 SD 원본 상수와 같아야 한다(근거 줄은 yaml 주석).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.fin import load_fin, load_reports, parse_fin, parse_reports
from kbj.config.settings import Settings

S = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def raw_fin() -> dict[str, Any]:
    return copy.deepcopy(load_yaml_mapping("fin.yaml", settings=S))


def raw_reports() -> dict[str, Any]:
    return copy.deepcopy(load_yaml_mapping("reports.yaml", settings=S))


def test_real_files_parse_with_design_defaults() -> None:
    fin = load_fin(settings=S)
    f = fin.filings
    assert f.keywords["상장폐지"] == 10 and f.keywords["공급계약"] == 8 and f.keywords["배당"] == 6
    assert f.keywords["사업보고서"] == 2 and len(f.keywords) == 39  # SD :9920 표 그대로
    assert (f.importance_levels.critical, f.importance_levels.high, f.importance_levels.medium) == (
        10, 6, 4,
    )  # fmt: skip
    assert f.feed.max_pages == 30 and f.feed.retry_parse_max == 3
    e = fin.earnings
    assert (e.thresholds.big_pct, e.thresholds.standard_pct, e.thresholds.inline_pct) == (
        20.0, 10.0, 5.0,
    )  # fmt: skip
    assert e.thresholds.turnaround_partial_ratio == 0.5 and e.alert_max_priority == 2
    assert e.perf_days == (3, 5, 7)
    c = fin.consensus
    assert (c.top_n, c.opinion_window_days, c.min_brokers_ok) == (300, 90, 3)
    assert c.revision_windows == (7, 30)
    v = fin.valuation
    assert (v.band_days, v.min_days) == (1240, 250)
    assert v.caps["per"] == (0.0, 200.0) and v.caps["ev_ebitda"] == (0.0, 100.0)
    assert (fin.scenario.bear_eps_mult, fin.scenario.bull_eps_mult) == (0.7, 1.2)
    assert fin.overhang.lockup_months_third_party == 12
    assert fin.public_export.fin_max_total_mb == 25
    assert (fin.backfill.from_year, fin.backfill.tier) == (2021, "main")
    rep = load_reports(settings=S)
    assert (rep.years_back, rep.fs_div, rep.public_demo) == (3, "CFS", ())
    assert rep.mapping == "dart_report/mapping.yaml" and rep.xlsx_max_bytes == 2 * 1024 * 1024


def test_helpers_follow_sd_rules() -> None:
    fin = load_fin(settings=S)
    lv = fin.filings.importance_levels
    assert [lv.level(x) for x in (10, 9, 6, 5, 4, 3)] == [
        "critical", "high", "high", "medium", "medium", "low",
    ]  # fmt: skip
    bonus = fin.filings.login_bonus
    assert bonus.watchlist == 3
    # SD _cap_bonus_for_code — 경계 '초과'
    assert bonus.cap_bonus(10_000_000_000_001) == 3 and bonus.cap_bonus(10_000_000_000_000) == 2
    assert bonus.cap_bonus(100_000_000_001) == 1 and bonus.cap_bonus(100_000_000_000) == 0
    assert bonus.cap_bonus(None) == 0
    sig = fin.consensus.signal_pct
    cases = [
        (15.0, "STRONG_UP"), (14.99, "UP"), (5.0, "UP"), (4.99, "NEUTRAL"), (-4.99, "NEUTRAL"),
        (-5.0, "DOWN"), (-14.99, "DOWN"), (-15.0, "STRONG_DOWN"),
    ]  # fmt: skip
    assert [sig.classify(p)[0] for p, _ in cases] == [s for _, s in cases]
    assert sig.classify(20.0) == ("STRONG_UP", 1) and sig.classify(0.0) == ("NEUTRAL", 3)
    v = fin.valuation
    assert v.in_cap("per", 199.9) and not v.in_cap("per", 200.0) and not v.in_cap("per", 0.0)


def _fin_err(fn: Any) -> str:
    data = raw_fin()
    fn(data)
    with pytest.raises(ConfigFileError) as e:
        parse_fin(data)
    return str(e.value)


def test_unknown_keys_and_bad_values_fail() -> None:
    assert "fin.yaml" in _fin_err(lambda d: d["filings"].update(keywordz={}))
    _fin_err(lambda d: d["filings"]["keywords"].update({"폭등": 11}))
    _fin_err(lambda d: d["filings"]["importance_levels"].update(high=12))
    _fin_err(lambda d: d["filings"]["login_bonus"].update(mktcap_krw=[[1, 1], [2, 2]]))
    _fin_err(lambda d: d["filings"]["feed"].update(rescan_at="7:00"))
    _fin_err(lambda d: d["earnings"]["thresholds"].update(inline_pct=30.0))
    _fin_err(lambda d: d["earnings"].update(perf_days=[5, 3]))
    # 저장 칸(0012 perf_3d·5d·7d)·CHECK(revision.window_days 7·30) 밖의 값은 설정에서 막는다
    assert "perf_days" in _fin_err(lambda d: d["earnings"].update(perf_days=[3, 5, 10]))
    assert "revision_windows" in _fin_err(
        lambda d: d["consensus"].update(revision_windows=[7, 30, 90])
    )
    _fin_err(lambda d: d["consensus"].update(revision_windows=[30, 7]))
    _fin_err(lambda d: d["consensus"]["signal_pct"].update(up=-1.0))
    _fin_err(lambda d: d["valuation"]["caps"].update(per=[10.0, 1.0]))
    _fin_err(lambda d: d["valuation"]["caps"].pop("psr"))
    _fin_err(lambda d: d["valuation"].update(min_days=5000))
    _fin_err(lambda d: d["scenario"].update(bear_eps_mult=1.5))
    _fin_err(lambda d: d["backfill"].update(tier="all"))
    _fin_err(lambda d: d.update(version=2))


def test_reports_rules() -> None:
    data = raw_reports()
    data["public_demo"] = ["994010", "99K010"]
    assert parse_reports(data).public_demo == ("994010", "99K010")
    for bad in (
        {"public_demo": ["5930"]},
        {"public_demo": ["994010", "994010"]},
        {"mapping": "../secrets.yaml"},
        {"xlsx_max_bytes": 3 * 1024 * 1024},
        {"fs_div": "XFS"},
        {"extra_key": 1},
    ):
        with pytest.raises(ConfigFileError, match=r"reports\.yaml"):
            parse_reports({**raw_reports(), **bad})
