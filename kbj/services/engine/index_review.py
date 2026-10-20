"""지수 정기변경 예측 작업 — `index.calendar`·`index.predict` + 수동 CLI(P5 2차 묶음 IDX).

docs/p5_index_rebalance_prompt.md §3-4·§3-6, metrics §24. 순수 계산은 `kbj.engines.index_review`,
SQL 은
`kbj.store.repos`, 이 모듈은 둘을 잇는 실행만 한다(P3·P4 관례 — `kbj.services.engine.valuation`).
로그인
작업이다(시세 prv_market + 공개 지분 pub_filings + 구성종목 prv_index → 결과 prv_index).

`index.calendar`: 설정 일정표(`config/index_reviews.yaml reviews`) → `prv_index.review_calendar`
(confirmed=False 면 estimated). 사용자 컷오프는 덮지 않는다.

`index.predict`(장 마감 뒤 — as_of = 그날): 켜진 지수마다 아직 효력이 나지 않은 첫 리뷰를 고르고
- 계산 날짜 = min(as_of, 리뷰 기준일) — 기준일이 지나면 기준일 값으로 고정한다.
- 그날 구성종목이 없으면 `no_constituents` 실행 한 줄만 쓴다(예측을 내지 않는다 — 지어내지 않음).
- 그날 이하 마지막 스냅(원장 우선순위)이 없으면 `no_data`.
- 입력: 시총·이름·시장·종류·상태(스냅), 상장일(유니버스), 일평균 거래대금(일봉 N일), KRX 형은
  심사기간 평균 시총(일봉 평균 종가 × 기준일 주식 수 — 설정 mcap_avg_days, 없으면 엔진이 기준일
  시총으로 대체), 업종(DART
  기업개황
  업종코드 → 설정 산업군), 유동비율(구성종목 목록 FIF = 직전 값, DART 자기주식·5% 대량보유 추정 =
  raw —
  시총 상위 `fif_estimate.top_n`(설정) + 구성종목만: 종목마다 공시 저장소를 두 번 읽으므로 상한을
  둔다. 밖의 종목은
  유동비율
  없음 → 컷오프 추정에서 빠진다 — 노트에 수).
- 추종 자금: `prv_index.passive_aum` 의 user 행이 있으면 그것, 아니면 etf(P3 ETF 순자산 — 기초지수
  이름
  패턴, 레버리지·인버스 제외) + non_etf(설정) 합, 그것도 없으면 설정 aum(MSCI). 없으면 None.
- 모델 컷오프 결과(model) + 일정 표에 사용자 컷오프가 있으면 그것으로도(user) 같이 쓴다(MSCI 형만).
- 지수 하나의 실패는 그 지수만 건너뛰고(사유) 나머지를 마친 뒤 `failed`(격리 — 삼키지 않는다).

수동 CLI(로그인 VM — 출력에 값·코드를 싣는 것은 백테스트 표뿐):
- `load-constituents --index ID --file CSV --as-of D --source user|press` — 목록(열 code[,name,fif])
  반영.
  빠진 종목은 D 전날로 닫고, FIF 가 바뀐 종목은 닫고 다시 연다.
- `set-cutoff --index ID --review YYYY-MM --won N|--clear` — 증권사 컷오프 추정치.
- `backtest --index ID [--review YYYY-MM …]` — 지난 리뷰를 다시 계산해 적중·놓침·오탐 표(JSON).
종료 코드 0 = ok, 1 = 입력·데이터 문제, 2 = 저장소 오류(문구는 접속 정보를 가린 것).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import IO, Any, Final, Protocol, cast

from kbj.config.index_reviews import IndexCfg, IndexReviewsConfig, ReviewCfg, load_index_reviews
from kbj.core.index_rows import (
    ConstituentRow,
    PassiveAumRow,
    PredictionRow,
    PredictionRun,
    ReviewEvent,
)
from kbj.core.masking import safe_snippet
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.board import kinds as board_kinds
from kbj.engines.index_review.backtest import score
from kbj.engines.index_review.fif import estimate_fif_raw
from kbj.engines.index_review.krx import predict_krx
from kbj.engines.index_review.msci import predict_msci
from kbj.engines.index_review.types import IndexResult, StockIn, StockOut
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import EtfRepo, FilingsRepo, IndexReviewRepo, MarketRepo, pg_repos

__all__ = [
    "JOB_CALENDAR",
    "JOB_PREDICT",
    "IndexRepos",
    "calendar",
    "calendar_rows",
    "compute",
    "load_constituents",
    "main",
    "predict",
    "run_predict",
    "sync_calendar",
]

JOB_CALENDAR: Final = "index.calendar"
JOB_PREDICT: Final = "index.predict"
SERVICE: Final = "index_review"
SOURCE: Final = "KBJ(KRX·KIS 시세·DART 지분 — 추정)"
CAL_SOURCE: Final = "config/index_reviews.yaml"
_CODE: Final = re.compile(r"[0-9A-Z]{6}")
_CLOSE: Final = time(15, 30)


class IndexRepos(Protocol):
    @property
    def market(self) -> MarketRepo: ...

    @property
    def etf(self) -> EtfRepo: ...

    @property
    def filings(self) -> FilingsRepo: ...

    @property
    def index_review(self) -> IndexReviewRepo: ...


def _reason(e: BaseException) -> str:
    return f"{type(e).__name__}: {safe_snippet(str(e), 200)}"


# ── 일정 ─────────────────────────────────────────────────────────────────────────────────


def calendar_rows(cfg: IndexReviewsConfig) -> list[ReviewEvent]:
    out: list[ReviewEvent] = []
    for idx in cfg.indexes:
        for r in cfg.reviews_of(idx.id):
            out.append(ReviewEvent(
                index_id=idx.id, review_id=r.id, index_title=idx.title, display_order=idx.order,
                base_date=r.base, announce_date=r.announce, trade_date=r.trade,
                effective_date=r.effective, confirmed=r.confirmed, source=CAL_SOURCE,
                quality=Quality.OK if r.confirmed else Quality.ESTIMATED,
            ))  # fmt: skip
    return out


def sync_calendar(
    repos: IndexRepos, cfg: IndexReviewsConfig, *, now: datetime, loaded_by: str
) -> int:
    return repos.index_review.put_calendar(calendar_rows(cfg), loaded_by=loaded_by, now=now)


# ── 입력 모으기 ───────────────────────────────────────────────────────────────────────────


@dataclass
class Inputs:
    stocks: list[StockIn]
    snap_day: date | None
    notes: list[str] = field(default_factory=list[str])


def _adv(repos: IndexRepos, day: date, n: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for code, bars in repos.market.series(None, day, n).items():
        vals = [b.turnover for b in bars if b.turnover is not None and b.date <= day]
        if vals:
            out[code] = sum(vals) / len(vals)
    return out


def _avg_mcap(
    repos: IndexRepos, day: date, n: int, shares: Mapping[str, int | None]
) -> dict[str, int]:
    """KRX 심사기간 일평균 시총 근사 = 최근 n 거래일 평균 종가 × 기준일 상장주식 수(주식 수 변화는
    무시 — 추정). 창의 절반 미만만 종가가 있으면 빼고(엔진이 기준일 시총으로 대체) 넘긴다."""
    out: dict[str, int] = {}
    for code, bars in repos.market.series(None, day, n).items():
        sh = shares.get(code)
        closes = [b.close for b in bars if b.close is not None and b.date <= day]
        if sh and len(closes) * 2 >= n:
            out[code] = round(sum(closes) / len(closes) * sh)
    return out


# 상장 투자회사(인프라·선박 투융자 — 지수 대상 아님). 보드 분류(`board.kinds`)는 리츠만 따로 둔다
_FUND_NAME: Final = re.compile(r"(인프라|투융자|선박투자|해운투자)$")
FUND: Final = "fund"


def stock_kind(code: str, name: str | None, kind: str | None) -> str:
    """원천이 준 종류, 없으면 보드 정본 분류(이름·코드 — 우선주·스팩·리츠) + 상장 투자회사는 fund.

    KRX 기본정보는 종류를 비워(kind None) `kinds: [common]` 이 아무것도 거르지 못해 KOSPI200 편입
    후보에 우선주·인프라 투자회사가 올랐다(2026-10-20)."""
    if kind is not None:
        return kind
    if name and _FUND_NAME.search(name):
        return FUND
    return board_kinds.of(code, name or "")


def _fif_raw(
    repos: IndexRepos, corp: str | None, day: date, cfg: IndexReviewsConfig
) -> float | None:
    if corp is None:
        return None
    sc = repos.filings.latest_share_count(corp, upto=day)
    if sc is None:
        return None
    last: dict[str, tuple[date, str, int]] = {}
    for r in repos.filings.major_holder_reports(corp, end=day):
        if r.shares is None:
            continue
        cur = last.get(r.reporter)
        if cur is None or (r.rcept_dt, r.rcept_no) > (cur[0], cur[1]):
            last[r.reporter] = (r.rcept_dt, r.rcept_no, r.shares)
    holders = [(k, v[2]) for k, v in last.items()]
    return estimate_fif_raw(
        sc.common_issued, sc.treasury_common, holders, cfg.fif_estimate.financial_holder_patterns
    )


def gather(
    repos: IndexRepos,
    cfg: IndexReviewsConfig,
    idx: IndexCfg,
    day: date,
    members: Sequence[ConstituentRow],
    adv_days: int,
) -> Inputs:
    snaps, snap_day = repos.market.snapshot(day)
    if snap_day is None or not snaps:
        return Inputs([], None, ["시세 스냅 없음"])
    notes: list[str] = []
    if snap_day != day:
        notes.append(f"{day} 스냅 없음 — {snap_day} 값으로 계산")
    universe = {u.code: u for u in repos.market.universe(snap_day)}
    adv = _adv(repos, snap_day, adv_days)
    avg_days = cfg.krx_rules(idx.id).opt("mcap_avg_days") if idx.family == "krx" else None
    avg_cap = (
        _avg_mcap(repos, snap_day, int(avg_days), {c: s.shares for c, s in snaps.items()})
        if avg_days else {}
    )  # fmt: skip
    corp_index = repos.filings.corp_index()
    member_fif = {m.code: m.fif for m in members}
    corp_of = {code: v[0] for code, v in corp_index.items()}
    wanted = [s for s in snaps.values() if s.market in idx.markets or s.code in member_fif]
    by_cap = sorted(wanted, key=lambda s: (-(s.mktcap or 0), s.code))
    top_n = cfg.fif_estimate.top_n
    estimate_codes = {s.code for s in by_cap[:top_n]} | set(member_fif)
    profiles = repos.filings.profiles(
        [corp_of[c] for c in estimate_codes if c in corp_of]
    )  # 업종코드
    stocks: list[StockIn] = []
    n_skipped = 0
    for s in by_cap:
        corp = corp_of.get(s.code)
        raw = _fif_raw(repos, corp, snap_day, cfg) if s.code in estimate_codes else None
        if s.code not in estimate_codes:
            n_skipped += 1
        prof = profiles.get(corp) if corp else None
        u = universe.get(s.code)
        stocks.append(StockIn(
            code=s.code, name=s.name, market=s.market, kind=stock_kind(s.code, s.name, s.kind),
            sector=cfg.sectors.sector_of(prof.induty_code if prof else None),
            listed_on=u.listed_on if u else None, mktcap=s.mktcap, adv_won=adv.get(s.code),
            fif_raw=raw, fif_prev=member_fif.get(s.code), fif_source="DART" if raw else None,
            flags=s.status_flags, member=s.code in member_fif, mktcap_avg=avg_cap.get(s.code),
        ))  # fmt: skip
    missing = sorted(set(member_fif) - {s.code for s in stocks})
    for code in missing:  # 구성종목인데 스냅이 없다(거래정지·상장폐지?) — 시세 없음으로 넘긴다
        stocks.append(StockIn(
            code=code, name=None, market=None, kind=None, sector=None, listed_on=None,
            mktcap=None, adv_won=None, fif_raw=None, fif_prev=member_fif[code], fif_source=None,
            flags=None, member=True,
        ))  # fmt: skip
    if n_skipped:
        notes.append(f"유동비율 추정 대상 밖 {n_skipped}종목(시총 상위 {top_n} 밖)")
    if missing:
        notes.append(f"구성종목 {len(missing)}종목 시세 없음")
    return Inputs(stocks, snap_day, notes)


# ── 추종 자금 ────────────────────────────────────────────────────────────────────────────


def etf_aum(
    repos: IndexRepos, patterns: Sequence[str], index_id: str, day: date
) -> PassiveAumRow | None:
    """P3 ETF 중 기초지수 이름이 패턴에 맞는(레버리지·인버스 제외) 것의 마지막 순자산 합."""
    if not patterns:
        return None
    codes = [
        m.code for m in repos.etf.meta().values()
        if m.base_index and any(p in m.base_index for p in patterns)
        and m.etf_type != "leveraged_inverse" and (m.leverage is None or m.leverage == 1)
        and (m.delisted_on is None or m.delisted_on > day)
    ]  # fmt: skip
    if not codes:
        return None
    days = repos.etf.etf_days(day, 5, codes)
    total, n, last_day = 0, 0, None
    for rows in days.values():
        got = [r for r in rows if r.net_asset is not None and r.date <= day]
        if got:
            r = max(got, key=lambda x: x.date)
            total += r.net_asset or 0
            n += 1
            last_day = r.date if last_day is None else max(last_day, r.date)
    if not n or last_day is None:
        return None
    return PassiveAumRow(
        index_id=index_id, as_of=last_day, kind="etf", aum_won=total, n_funds=n, source="KRX",
        quality=Quality.ESTIMATED,
        detail={"match": "기초지수 이름 패턴", "patterns": list(patterns)},
    )  # fmt: skip


def resolve_aum(
    repos: IndexRepos, cfg: IndexReviewsConfig, idx: IndexCfg, day: date, *, write: bool,
    now: datetime, loaded_by: str,
) -> tuple[int | None, list[str]]:  # fmt: skip
    notes: list[str] = []
    if idx.family == "krx":
        rules = cfg.krx_rules(idx.id)
        fresh: list[PassiveAumRow] = []
        etf = etf_aum(repos, rules.etf_index_patterns, idx.id, day)
        if etf is not None:
            fresh.append(etf)
        if rules.non_etf_aum_won.v is not None:
            fresh.append(PassiveAumRow(
                index_id=idx.id, as_of=day, kind="non_etf", aum_won=int(rules.non_etf_aum_won.v),
                n_funds=None, source=CAL_SOURCE, quality=Quality.ESTIMATED,
            ))  # fmt: skip
        if write and fresh:
            repos.index_review.put_passive_aum(fresh, loaded_by=loaded_by, now=now)
    rows = repos.index_review.passive_aum(idx.id, day)
    user = [r for r in rows if r.kind == "user"]
    if user:
        notes.append("추종 자금: 사용자 입력")
        return user[0].aum_won, notes
    parts = [r for r in rows if r.kind in ("etf", "non_etf")]
    if parts:
        notes.append("추종 자금: " + " + ".join(r.kind for r in parts) + " (추정)")
        return sum(r.aum_won for r in parts), notes
    if idx.family == "msci":
        v = cfg.msci_rules(idx.id).aum_won.v
        if v is not None:
            notes.append("추종 자금: 설정 추정")
            return int(v), notes
    return None, notes


# ── 계산 ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Computed:
    idx: IndexCfg
    review: ReviewCfg
    day: date
    snap_day: date | None
    results: dict[str, IndexResult]  # cutoff_kind → 결과
    notes: tuple[str, ...]
    aum_won: int | None


def compute(
    repos: IndexRepos,
    cfg: IndexReviewsConfig,
    idx: IndexCfg,
    review: ReviewCfg,
    as_of: date,
    *,
    now: datetime,
    loaded_by: str,
    write_aum: bool = True,
    user_cutoff: int | None = None,
) -> Computed:
    """한 지수·리뷰 예측(쓰지 않는다 — 추종 자금 표만 write_aum 이면 갱신)."""
    day = min(as_of, review.base)
    members = repos.index_review.constituents(idx.id, day)
    if not members:
        res = IndexResult(
            "no_constituents", None, (), 0, ("구성종목 목록 없음 — 예측하지 않는다",), True
        )
        return Computed(idx, review, day, None, {"model": res}, (), None)
    adv_days = int(
        cfg.msci_rules(idx.id).val("adv_days") if idx.family == "msci"
        else cfg.krx_rules(idx.id).val("adv_days")
    )  # fmt: skip
    inp = gather(repos, cfg, idx, day, members, adv_days)
    if inp.snap_day is None:
        res = IndexResult("no_data", None, (), len(members), tuple(inp.notes), True)
        return Computed(idx, review, day, None, {"model": res}, (), None)
    aum, aum_notes = resolve_aum(
        repos, cfg, idx, inp.snap_day, write=write_aum, now=now, loaded_by=loaded_by
    )
    results: dict[str, IndexResult] = {}
    if idx.family == "msci":
        rules = cfg.msci_rules(idx.id)
        fif_rule = cfg.fif_rule_for(review.announce)
        results["model"] = predict_msci(inp.stocks, rules, fif_rule, cfg.fol, aum_won=aum)
        if user_cutoff is not None:
            results["user"] = predict_msci(
                inp.stocks, rules, fif_rule, cfg.fol, cutoff_override=user_cutoff, aum_won=aum
            )
        inp.notes.append(f"유동비율 규칙 {fif_rule.id}(리뷰 {review.id})")
    else:
        rules_k = cfg.krx_rules(idx.id)
        results["model"] = predict_krx(inp.stocks, rules_k, idx.markets, day, aum_won=aum)
    return Computed(
        idx, review, day, inp.snap_day, results, (*inp.notes, *aum_notes), aum
    )  # fmt: skip


def _evidence(o: StockOut) -> dict[str, Any]:
    return {
        "mktcap": o.mktcap, "ff_mcap": o.ff_mcap, "fif": o.fif, "fif_raw": o.fif_raw,
        "fif_how": o.fif_how, "gap_mcap": o.gap_mcap, "gap_ff": o.gap_ff,
        "condition_met": o.condition_met, "required_rise": o.required_rise,
        "required_basis": o.required_basis, "passive_won": o.passive_won, "adv_won": o.adv_won,
        "adv_multiple": o.adv_multiple, "rank": o.rank, "threshold_mcap": o.threshold_mcap,
        "threshold_ff": o.threshold_ff, "reasons": list(o.reasons),
    }  # fmt: skip


def to_rows(c: Computed) -> list[tuple[PredictionRun, list[PredictionRow]]]:
    snap = c.snap_day or c.day
    data_as_of = datetime.combine(snap, _CLOSE, tzinfo=KST)
    out: list[tuple[PredictionRun, list[PredictionRow]]] = []
    for kind, res in c.results.items():
        cut = res.cutoff
        rows = [
            PredictionRow(
                index_id=c.idx.id, review_id=c.review.id, base_date=c.day,
                cutoff_kind=kind,  # pyright: ignore[reportArgumentType]
                code=o.code, name=o.name, verdict=o.outcome,  # pyright: ignore[reportArgumentType]
                member=o.member, evidence=_evidence(o), cutoff_won=cut.won if cut else None,
                data_as_of=data_as_of, source=SOURCE,
                quality=Quality.ESTIMATED if o.estimated else Quality.OK,
            )
            for o in res.stocks
            if o.outcome != "none"
        ]  # fmt: skip
        n = {v: sum(1 for r in rows if r.verdict == v) for v in ("add", "delete", "borderline")}
        run = PredictionRun(
            index_id=c.idx.id, review_id=c.review.id, base_date=c.day,
            cutoff_kind=kind,  # pyright: ignore[reportArgumentType]
            status=res.status, cutoff_won=cut.won if cut else None,
            cutoff_low_won=cut.low_won if cut else None,
            cutoff_high_won=cut.high_won if cut else None,
            cutoff_method=cut.method if cut else None, n_members=res.n_members,
            n_add=n["add"], n_delete=n["delete"], n_borderline=n["borderline"],
            aum_won=c.aum_won, data_as_of=data_as_of, source=SOURCE,
            quality=Quality.ESTIMATED if res.estimated or res.status != "ok" else Quality.OK,
            notes=(*res.notes, *c.notes),
        )  # fmt: skip
        out.append((run, rows))
    return out


@dataclass
class PredictReport:
    status: str = "ok"
    runs: int = 0
    rows: int = 0
    by_index: dict[str, str] = field(default_factory=dict[str, str])
    failed: dict[str, str] = field(default_factory=dict[str, str])
    proxy_notes: dict[str, str] = field(default_factory=dict[str, str])  # 대리 ETF 를 못 쓴 사유

    def detail(self) -> dict[str, Any]:
        return {"runs": self.runs, "rows": self.rows, "by_index": self.by_index,
                "failed": dict(list(self.failed.items())[:5]),
                "proxy_notes": self.proxy_notes}  # fmt: skip


def _user_cutoff(repos: IndexRepos, index_id: str, review_id: str) -> int | None:
    for ev in repos.index_review.calendar(index_id):
        if ev.review_id == review_id:
            return ev.user_cutoff_won
    return None


PROXY_SIZE_TOL: Final = 2  # 대리 ETF 종목 수가 지수 크기 ± 이만큼 밖이면 쓰지 않는다
PROXY_SOURCE: Final = "etf"  # 대리 ETF 로 맞춘 구성종목 행의 source(사용자·보도 목록은 덮지 않음)


def sync_from_proxy(
    repos: IndexRepos, idx: IndexCfg, as_of: date, *, now: datetime, loaded_by: str
) -> str | None:
    """대리 ETF(완전 복제)의 as_of 이하 마지막 구성종목 공시로 구성종목을 맞춘다(반환 = 안 쓴 사유).

    사용자·보도 목록(source user·press)이 지금 유효하면 덮지 않는다. 같은 종목 집합이면 쓰지 않는다
    (멱등). 지수 크기(size)에서 ±PROXY_SIZE_TOL 넘게 벗어나면 표본 복제로 보고 쓰지 않는다."""
    if idx.proxy_etf is None:
        return None
    held = [
        h
        for h in repos.etf.holdings_of(idx.proxy_etf, as_of)
        if (h.qty or 0) > 0 or (h.wt or 0) > 0
    ]
    if not held:
        return f"대리 ETF {idx.proxy_etf} 구성종목 공시 없음"
    asof = max(h.asof for h in held)
    held = [h for h in held if h.asof == asof]
    if idx.size is not None and abs(len(held) - idx.size) > PROXY_SIZE_TOL:
        return f"대리 ETF {idx.proxy_etf} {len(held)}종목 — 지수 {idx.size}종목과 달라 쓰지 않는다"
    active = repos.index_review.constituents(idx.id, asof)
    if any(r.source != PROXY_SOURCE for r in active):
        return None  # 사용자·보도 목록이 있다 — 그것이 우선
    if {r.code for r in active} == {h.code for h in held}:
        return None
    load_constituents(
        repos, idx.id, [(h.code, h.name, None) for h in held], asof,
        PROXY_SOURCE, now=now, loaded_by=loaded_by,
    )  # fmt: skip
    return None


def run_predict(
    repos: IndexRepos, cfg: IndexReviewsConfig, as_of: date, *, now: datetime, loaded_by: str
) -> PredictReport:
    rep = PredictReport()
    for idx in cfg.enabled_indexes():
        review = cfg.current_review(idx.id, as_of)
        if review is None:
            rep.by_index[idx.id] = "no_review"  # 일정 표를 더 적어야 한다
            continue
        try:
            why = sync_from_proxy(repos, idx, as_of, now=now, loaded_by=loaded_by)
            if why is not None:
                rep.proxy_notes[idx.id] = why
            user = _user_cutoff(repos, idx.id, review.id) if idx.family == "msci" else None
            c = compute(
                repos, cfg, idx, review, as_of, now=now, loaded_by=loaded_by, user_cutoff=user
            )
            for run, rows in to_rows(c):
                rep.rows += repos.index_review.put_run(run, rows, loaded_by=loaded_by, now=now)
                rep.runs += 1
            rep.by_index[idx.id] = c.results["model"].status
        except Exception as e:
            rep.failed[idx.id] = _reason(e)
            rep.by_index[idx.id] = "failed"
    if rep.failed:
        rep.status = "failed"
    return rep


# ── 작업 처리기(config/jobs.yaml owner) ──────────────────────────────────────────────────


def _repos(ctx: JobContext) -> IndexRepos:
    repos = ctx.resources.get("repos")
    if repos is not None:
        return cast(IndexRepos, repos)
    return pg_repos(ctx.resource("connect"))


def _cfg(ctx: JobContext) -> IndexReviewsConfig:
    got = ctx.resources.get("index_reviews")
    return got if isinstance(got, IndexReviewsConfig) else load_index_reviews(settings=ctx.settings)


def calendar(ctx: JobContext) -> JobResult:
    """`index.calendar` — 설정 일정표 → prv_index.review_calendar."""
    n = sync_calendar(_repos(ctx), _cfg(ctx), now=ctx.now, loaded_by=ctx.job)
    return JobResult("ok", collected=ctx.keys, rows=n, detail={"rows": n})


def predict(ctx: JobContext) -> JobResult:
    """`index.predict` — 장 마감 뒤(as_of = 그날)."""
    as_of = date.fromisoformat(ctx.as_of[:10])
    rep = run_predict(_repos(ctx), _cfg(ctx), as_of, now=ctx.now, loaded_by=ctx.job)
    if rep.status == "ok":
        return JobResult("ok", collected=ctx.keys, rows=rep.rows, detail=rep.detail())
    return JobResult("failed", rows=rep.rows, detail=rep.detail())


# ── 수동 CLI ─────────────────────────────────────────────────────────────────────────────


def read_list(path: Path) -> list[tuple[str, str | None, float | None]]:
    """CSV(머리줄 code[,name,fif]) → (코드, 이름, FIF). 형식이 틀리면 ValueError(줄 번호)."""
    out: list[tuple[str, str | None, float | None]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "code" not in reader.fieldnames:
            raise ValueError("머리줄에 code 열이 있어야 한다")
        for i, row in enumerate(reader, start=2):
            code = (row.get("code") or "").strip().zfill(6)
            if not _CODE.fullmatch(code):
                raise ValueError(f"{i}줄: 종목코드 형식")
            name = (row.get("name") or "").strip() or None
            raw = (row.get("fif") or "").strip()
            fif = float(raw) if raw else None
            if fif is not None and fif > 1:
                fif = fif / 100  # 퍼센트로 적은 목록
            out.append((code, name, fif))
    codes = [c for c, _, _ in out]
    if len(set(codes)) != len(codes):
        raise ValueError("같은 종목이 두 번 있다")
    return out


def load_constituents(
    repos: IndexRepos,
    index_id: str,
    items: Sequence[tuple[str, str | None, float | None]],
    as_of: date,
    source: str,
    *,
    now: datetime,
    loaded_by: str,
) -> dict[str, int]:
    """목록을 as_of 부터 유효하게. 빠진 종목·FIF 가 바뀐 종목은 as_of 전날로 닫는다."""
    active = {r.code: r for r in repos.index_review.constituents(index_id, as_of)}
    new = {c: (n, f) for c, n, f in items}
    end = as_of - timedelta(days=1)
    writes: list[ConstituentRow] = []
    closed = opened = 0
    for code, row in active.items():
        if code not in new or (new[code][1] is not None and new[code][1] != row.fif):
            if row.valid_from > end:  # 같은 날 다시 올림 — 닫지 않고 덮는다
                continue
            writes.append(dataclasses.replace(row, valid_to=end))
            closed += 1
    for code, (name, fif) in new.items():
        old = active.get(code)
        if old is not None and (fif is None or fif == old.fif) and old.valid_from <= end:
            continue
        writes.append(ConstituentRow(
            index_id=index_id, code=code, valid_from=as_of, valid_to=None,
            name=name or (old.name if old else None), fif=fif if fif is not None else (
                old.fif if old else None),
            source=source, quality=Quality.OK,
        ))  # fmt: skip
        opened += 1
    n = repos.index_review.put_constituents(writes, loaded_by=loaded_by, received_at=now)
    return {"written": n, "closed": closed, "opened": opened, "size": len(new)}


def backtest(
    repos: IndexRepos,
    cfg: IndexReviewsConfig,
    index_id: str,
    review_ids: Sequence[str] | None,
    *,
    today: date,
    now: datetime,
) -> list[dict[str, Any]]:
    """지난 리뷰(효력일 ≤ today)를 기준일 값으로 다시 계산해 실제(효력일 구성종목)와 맞춘다."""
    idx = cfg.index(index_id)
    out: list[dict[str, Any]] = []
    for review in cfg.reviews_of(index_id):
        if review.effective > today or (review_ids and review.id not in review_ids):
            continue
        before = {r.code for r in repos.index_review.constituents(index_id, review.base)}
        after = {r.code for r in repos.index_review.constituents(index_id, review.effective)}
        if not before or not after:
            out.append({"review": review.id, "status": "no_history"})
            continue
        c = compute(repos, cfg, idx, review, review.base, now=now,
                    loaded_by=f"{JOB_PREDICT}:backtest", write_aum=False)  # fmt: skip
        res = c.results["model"]
        if res.status != "ok":
            out.append({"review": review.id, "status": res.status})
            continue
        pred_add = {o.code for o in res.stocks if o.outcome == "add"}
        pred_del = {o.code for o in res.stocks if o.outcome == "delete"}
        border = {o.code for o in res.stocks if o.outcome == "borderline"}
        for side, pred, actual in (
            ("add", pred_add, after - before), ("delete", pred_del, before - after),
        ):  # fmt: skip
            out.append({"status": "ok", **score(review.id, side, pred, actual, border).as_dict()})
    return out


def _pg_repos() -> IndexRepos:
    from kbj.config.settings import Settings
    from kbj.store.db import connect

    settings = Settings()
    return pg_repos(lambda: connect(settings, service=SERVICE))


def _code(raw: str) -> str:
    if not _CODE.fullmatch(raw):
        raise argparse.ArgumentTypeError("종목코드는 대문자 영숫자 6자리")
    return raw


def main(
    argv: Sequence[str] | None = None,
    *,
    repos: IndexRepos | None = None,
    cfg: IndexReviewsConfig | None = None,
    now: datetime | None = None,
    out: IO[str] | None = None,
) -> int:
    ap = argparse.ArgumentParser(prog="python -m kbj.services.engine.index_review")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lc = sub.add_parser("load-constituents", help="구성종목 목록 CSV 반영(로그인 VM)")
    lc.add_argument("--index", required=True)
    lc.add_argument("--file", required=True, type=Path)
    lc.add_argument("--as-of", required=True, type=date.fromisoformat, help="목록이 유효한 첫날")
    lc.add_argument("--source", required=True, choices=["user", "press"])
    sc = sub.add_parser("set-cutoff", help="증권사 컷오프 추정치(원)")
    sc.add_argument("--index", required=True)
    sc.add_argument("--review", required=True)
    g = sc.add_mutually_exclusive_group(required=True)
    g.add_argument("--won", type=int)
    g.add_argument("--clear", action="store_true")
    bt = sub.add_parser("backtest", help="지난 리뷰 적중·놓침·오탐")
    bt.add_argument("--index", required=True)
    bt.add_argument("--review", action="append")
    args = ap.parse_args(argv)
    stream = out or sys.stdout
    from kbj.core.time import now_kst
    from kbj.store.db import StoreError

    def emit(obj: Mapping[str, Any]) -> None:
        print(json.dumps(obj, ensure_ascii=False, default=str), file=stream)

    try:
        conf = cfg if cfg is not None else load_index_reviews()
        clock = now or now_kst()
        try:
            conf.index(args.index)
        except KeyError as e:
            emit({"error": "input", "message": str(e)})
            return 1
        r = repos if repos is not None else _pg_repos()
        if args.cmd == "load-constituents":
            try:
                items = read_list(args.file)
            except (OSError, ValueError) as e:
                emit({"error": "input", "message": _reason(e)})
                return 1
            emit(load_constituents(r, args.index, items, args.as_of, args.source, now=clock,
                                   loaded_by=f"{JOB_PREDICT}:load"))  # fmt: skip
            return 0
        if args.cmd == "set-cutoff":
            won = None if args.clear else args.won
            if won is not None and won <= 0:
                emit({"error": "input", "message": "컷오프는 0 보다 크다"})
                return 1
            ok = r.index_review.set_user_cutoff(
                args.index, args.review, won, loaded_by=f"{JOB_PREDICT}:cutoff", now=clock
            )
            emit({"updated": ok})
            return 0 if ok else 1
        rows = backtest(r, conf, args.index, args.review, today=clock.astimezone(KST).date(),
                        now=clock)  # fmt: skip
        for row in rows:
            emit(row)
        return 0 if rows and all(x.get("status") == "ok" for x in rows) else 1
    except StoreError as e:  # 문구는 접속 정보를 가린 것(kbj.store.db)
        emit({"error": "store", "message": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
