"""P5 설정 파일(config/{macro,briefs,markets,limits}.yaml — docs/p5_design.md §3.6·§1.1).

- 모르는 키는 오류(철자 틀린 값이 조용히 기본값으로 돌지 않게).
- **시리즈 tier ↔ 데이터셋 tier 대조**(D-P5-6): config/macro.yaml 의 시리즈마다 적은 tier 가
  카탈로그
  데이터셋 tier 와 같다 — 한 데이터셋에 공개·로그인 시리즈를 섞지 않는다(공개판에 로그인 값 0 —
  R10).
- 브리핑 섹션의 재료 작업은 등록부에 있고, 표 시리즈는 macro.yaml 의 brief 시리즈와 같다.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.briefs import load_briefs, parse_briefs
from kbj.config.files import ConfigFileError
from kbj.config.macro import load_macro, parse_macro
from kbj.config.markets import load_markets, parse_markets
from kbj.core.p5_rows import PUBLIC_MACRO_SOURCES
from kbj.data.catalog import all_datasets
from kbj.data.limits import load_limits
from kbj.services.scheduler.registry import Registry

ROOT = Path(__file__).resolve().parents[3]
CFG = ROOT / "config"


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((CFG / name).read_text(encoding="utf-8"))


# ── macro.yaml ─────────────────────────────────────────────────────────────────────────────


def test_series_tier_matches_dataset_tier() -> None:
    cat = all_datasets()
    errs = load_macro().check_tiers(lambda ds: cat[ds].tier.value)
    assert errs == []


def test_tier_mismatch_is_caught() -> None:
    cat = all_datasets()
    data = _yaml("macro.yaml")
    bad = copy.deepcopy(data)
    for s in bad["series"]:
        if s["id"] == "ECOS:802Y001:kospi":  # 한국거래소 작성 표(로그인)를 공개라고 적으면
            s["tier"] = "public"
            s["tab"] = "growth"
    errs = parse_macro(bad).check_tiers(lambda ds: cat[ds].tier.value)
    assert errs and "ECOS:802Y001" in errs[0]


def test_public_series_use_public_sources_only() -> None:
    m = load_macro()
    assert {s.source for s in m.public_series()} <= set(PUBLIC_MACRO_SOURCES)
    assert all(s.dataset != "FRED:restricted_series" for s in m.public_series())
    assert {s.source for s in m.private_series()} >= {"YAHOO", "FRED", "ECOS"}
    yahoo = [s for s in m.series if s.source == "YAHOO"]
    assert len(yahoo) == 12 and {s.tab for s in yahoo} == {"market"}  # SD _MACRO_TICKERS 12종
    assert [t.id for t in m.tabs][-1] == "market" and not m.tabs[-1].public
    assert len(m.tabs) == 7 and m.ff.enabled is True  # 사용자 결정 2026-10-13 Q3(예상치를 쓴다)


def test_unknown_items_carry_a_probe_number_instead_of_a_guess() -> None:
    for s in load_macro().series:
        if s.item is None:
            assert s.probe in {"#43", "#44", "#45", "#46"}, s.id
            assert not s.ready


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d.update(extra=1), "extra"),
        (lambda d: d["series"][0].update(colour="red"), "colour"),
        (lambda d: d["series"][0].update(item=None, probe=None), "probe"),
        (lambda d: d["series"].append(dict(d["series"][0])), "겹친다"),
        (lambda d: d["derived"][0].update(tier="private"), "입력 등급"),
        (lambda d: d["series"][-1].update(tab="us"), "공개 탭"),  # 로그인 시리즈를 공개 탭에
        (lambda d: d["tabs"].append({"id": "Bad", "title": "x", "public": True}), "소문자"),
    ],
)
def test_macro_rejects_bad_files(mutate: Any, match: str) -> None:
    data = copy.deepcopy(_yaml("macro.yaml"))
    mutate(data)
    with pytest.raises(ConfigFileError, match=match):
        parse_macro(data)


# ── briefs.yaml ────────────────────────────────────────────────────────────────────────────


def test_brief_sections_point_at_registered_jobs_and_macro_series() -> None:
    b = load_briefs()
    jobs = {j.name for j in Registry.load(CFG / "jobs.yaml").jobs}
    assert b.jobs() <= jobs
    assert set(b.table_series) == set(load_macro().brief_series())
    assert b.morning.send_at == "08:10" and b.closing.send_at == "16:00"  # ADR 0018
    omitted = {s.key: s.phase for k in (b.morning, b.closing) for s in k.sections
               if s.status == "omitted"}  # fmt: skip
    # 구루 X 다이제스트는 사용자 결정(2026-10-13 Q4)으로 완전히 뺐다 — 자리표시도 없다
    # P7 FND-B 가 GEX·진단(P7)과 시그널·초안·추천(P8 — LLM)을 나눴고, OUT 이 P7 둘을 켰다(D-P7-16)
    assert omitted == {"signals": "P8"}
    assert all(s.key != "guru" for k in (b.morning, b.closing) for s in k.sections)
    # 간밤 미국 지수는 config/us.yaml indexes(us.eod 가 받는 것)와 같다
    assert [{"symbol": s, "name": n} for s, n in b.us_index_pairs()] == _yaml("us.yaml")["indexes"]
    from kbj.reports.briefs.morning_brief import US_INDEXES  # 설정 없는 호출의 기본값 — 같아야 한다

    assert b.us_index_pairs() == US_INDEXES
    # 섹션별 '없음' 문구(BRF 요청) — 공시는 SD 문구, 일정은 공용 none_line
    texts = {s.key: s.none_text for s in b.closing.sections if s.empty == "none_line"}
    assert texts == {"filings": "오늘 중요 공시 없음"}


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d["morning"]["sections"][0].update(status="on"), "include"),
        (lambda d: d["morning"]["sections"].pop(), "quality"),
        (lambda d: d["texts"].update(not_ready="미준비"), "job"),
        (lambda d: d["closing"]["sections"][6].update(phase=None), "phase"),  # signals(P8)
        (lambda d: d["closing"]["sections"][0].update(none_text="없음"), "none_line"),
        (lambda d: d.update(us_indexes=[]), "us_indexes"),
    ],
)
def test_briefs_reject_bad_files(mutate: Any, match: str) -> None:
    data = copy.deepcopy(_yaml("briefs.yaml"))
    mutate(data)
    with pytest.raises(ConfigFileError, match=match):
        parse_briefs(data)


# ── markets.yaml(F2)·limits.yaml(P5) ─────────────────────────────────────────────────────────


def test_minute_pairs_start_with_the_three_gx_measured_k2i_pairs() -> None:
    m = load_markets()
    assert m.flows.minute.enabled_pairs()[:3] == (("K2I", "F001"), ("K2I", "OC01"), ("K2I", "OP01"))
    # 현물·주식선물은 KIS 공식 Postman 샘플 v2.6 의 코드 표(open-trading-api — 주석에 URL).
    # 위클리 4조합은 P7 WRAP 이 켰다(D-P7-6 — GX 실측 #13). 야간은 꺼진 채(#P7-2)
    assert m.flows.minute.enabled_pairs()[3:] == (
        ("KSP", "0001"), ("KSQ", "1001"), ("999", "S001"),
        ("WKM", "OC05"), ("WKM", "OP05"), ("WKI", "OC04"), ("WKI", "OP04"),
    )  # fmt: skip
    assert m.flows.minute.enabled_pairs("night") == ()
    # 차트에 겹칠 지수 — 현물은 그 시장 지수, K2I·위클리는 코스피200, 주식선물은 없음
    idx = {p.id: p.index for p in m.flows.minute.pairs if p.session == "day"}
    assert (idx["kospi"], idx["kosdaq"], idx["k200_fut"], idx["stock_fut"]) == (
        "0001", "1001", "2001", None,
    )  # fmt: skip
    assert (m.program.scan_top_n, m.program.daily_days, m.program.top_n) == (30, 60, 20)


def test_unknown_pair_codes_cannot_be_enabled() -> None:
    data = copy.deepcopy(_yaml("markets.yaml"))
    for p in data["flows"]["minute"]["pairs"]:
        if p["id"] == "kospi":
            p.update(market=None, sector=None, enabled=True)
    with pytest.raises(ConfigFileError, match="켤 수 없다"):
        parse_markets(data)


def test_minute_index_must_be_a_configured_intraday_index() -> None:
    data = copy.deepcopy(_yaml("markets.yaml"))
    for p in data["flows"]["minute"]["pairs"]:
        if p["id"] == "kospi":
            p["index"] = "9999"
    with pytest.raises(ConfigFileError, match="intraday_indices"):
        parse_markets(data)


def test_p5_limiters_and_kis_budget() -> None:
    lim = load_limits()
    for name in ("fred", "treasury", "nyfed", "yahoo", "ff", "nasdaq", "stooq"):
        assert lim.source(name).rate_config().capacity == 1, name
    kis = lim.source("kis")
    assert kis.daily_soft_cap == 30_000 and kis.daily_cap is None
    assert [kis.priority(r).name for r in
            ("market_minute", "program_intraday", "program_scan", "program_sweep")] == [
        "P2", "P2", "P3", "P4",
    ]  # fmt: skip
    cat = all_datasets()
    used = {s.limiter for s in cat.values()}
    assert used <= set(lim.sources) | {
        "anthropic"
    }  # 카탈로그 리미터는 limits.yaml 에 있다(구루는 P8)
