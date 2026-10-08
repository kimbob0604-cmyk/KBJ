"""`krx.daily`·`market.backfill` — KRX OpenAPI 일별(주식·기본정보·지수·ETF·ETN) 수집 + D+1 대조.

근거: docs/p3_design.md §1.2·§3.2·§3.5·§3.6·§3.8, D-P3-7·8. 승격 원본: ET `board/ingest/pipeline.py`
(`krx_regular_day`:67 — 기준일 것이 아니면 덮지 않는다, `_apply_krx_snapshot`:121,
`_apply_krx_bar`:153, `sync_universe`:190, `coverage_drop`:265 —
`COVERAGE_FLOOR`:254·`MIN_STOCKS`:262).

선점한 데이터 키(`KRX:<엔드포인트> @ 날짜`)마다:

- `sto/stk_bydd_trd`·`ksq_bydd_trd`·`knx_bydd_trd` → `prv_market.daily_bar`(asset
  stock)·`stock_snapshot` — source `krx`, venue KRX
- `sto/stk_isu_base_info`·`ksq_isu_base_info` → `prv_market.universe` — 상장일·주식종류(flags)
- `idx/kospi_dd_trd`·`kosdaq_dd_trd`·`krx_dd_trd` → `daily_bar`(asset index) — 코드 = 공백 뺀 지수
  이름
- `etp/etf_bydd_trd` → `prv_etf.etf_daily`·`meta`·`universe`(kind etf) — 메타는 이름·기초지수만 갱신
- `etp/etn_bydd_trd` → `daily_bar`(asset etn)·`universe`(kind etn)

규칙
- **빈 응답 = 공표 전**(`not_ready` — 10분마다 10:00 까지 재시도). 휴장일은 등록부가 부르지 않는다.
- **기준일 것이 아니면 쓰지 않는다**(ET `krx_regular_day` — 과거 세션 값을 그날 확정치로 찍지
  않는다): 행의 `BAS_DD` 가 요청 날짜와 다르면 그 키는 실패.
- **커버리지 가드**(ET `coverage_drop`): 직전 거래일 같은 시장 KRX 행 수의 95% 미만이거나 시장별
  절대 하한 미만이면 **쓰지 않고 실패**(조용히 덮지 않는다). 하한은 [확인 필요] — 코스피 700·코스닥
  1,200.
- 유니버스의 시장은 **엔드포인트가 정한다**(stk → KOSPI, ksq → KOSDAQ). 응답의 `MKT_TP_NM`(예:
  'KOSDAQ GLOBAL' 같은 소속 구분 [실측 필요 #21])은 flags `mkt_tp_nm` 에 사실로 둔다 — 시장 이름이
  달라 마감 수집 대상에서 빠지는 종목이 없게.
- 종목 종류(보통·우선·스팩·리츠)는 **넣지 않는다**(kind=None — 분류 정본은 board 엔진
  `kbj.engines.board.kinds.of`). KRX 기본정보의 주식종류·증권구분·소속부는 universe.flags 에 사실로
  남긴다. ETF·ETN 은 엔드포인트가 정하는 사실이라 kind 를 넣는다.
- ETF 메타: 이름·기초지수만 갱신하고 운용사·테마·유형·레버리지·상장일은 **있던 값을 지킨다**.
  처음 보는 ETF(또는 유형이 빈 행)만 그날 이름·기초지수로 규칙 분류한다
  (`kbj.engines.etf.types.typed_meta` — 묶음 E3 요청, 정본 규칙은 metrics §8.1. 다음
  `etf.collect` 가 다시 맞춘다).
- ETF 일별 키를 받은 `krx.daily` 실행은 끝에 검산 ③ 실패(`prv_flows.ledger_check` c3)와
  감지한 분할·병합(`prv_etf.split_event` origin=detected)을 기록한다
  (`etf_holdings.record_flow_checks` — 멱등, 수동 표는 덮지 않는다. 묶음 E3 요청·메인이 본
  열린 항목). 백필은 하지 않는다(과거 구간 검산은 `python -m kbj.services.engine.verify`).
- 주식 키를 하나라도 받은 실행은 끝에 D+1 대조(`reconcile.record`)를 한다(멱등 — 다시 돌려도 같다).
- 백필(`backfill`)은 같은 처리를 과거 날짜에 — 일 예산은 공용 + 백필 상한(`krx.backfill_cap`),
  우선순위 P4. 백필은 대조·유니버스 변동 알림을 하지 않는다. `prv_board.alltime` 다시 쌓기는 board
  서비스의 몫(§3.8 — 묶음 E1).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from kbj.config.markets import EtfCfg
from kbj.core.quality import Quality
from kbj.core.rows import Bar, EtfDay, EtfMeta, IndexBar, Snap, UniverseRow
from kbj.data.private.krx.client import KrxClient, StockMarket
from kbj.data.private.krx.models import (
    KrxIndexDaily,
    parse_base_info_rows,
    parse_etf_daily_rows,
    parse_etn_daily_rows,
)
from kbj.data.private.krx.stocks import KrxStockRow, isu_to_code, parse_index_rows, parse_stock_rows
from kbj.data.spec import DataKey
from kbj.engines.etf.types import typed_meta
from kbj.services.collectors import reconcile
from kbj.services.collectors._p3 import (
    NotReady,
    Outcome,
    RepoSet,
    krx_client,
    markets,
    reason,
    repos,
)
from kbj.services.collectors.etf_holdings import record_flow_checks
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult

__all__ = [
    "SOURCE",
    "Coverage",
    "CoverageDrop",
    "backfill",
    "collect",
    "index_code",
    "run",
]

SOURCE: Final = "krx"  # 원장 순위 이름(kbj.core.rows.SOURCE_RANK) — 소문자 한 낱말
VENUE: Final = "KRX"
log = logging.getLogger("kbj.services.collectors.krx_daily")

STOCK: Final[Mapping[str, StockMarket]] = {
    "sto/stk_bydd_trd": "kospi",
    "sto/ksq_bydd_trd": "kosdaq",
    "sto/knx_bydd_trd": "konex",
}
BASE_INFO: Final[Mapping[str, str]] = {
    "sto/stk_isu_base_info": "KOSPI",
    "sto/ksq_isu_base_info": "KOSDAQ",
}
INDEX: Final = ("idx/kospi_dd_trd", "idx/kosdaq_dd_trd", "idx/krx_dd_trd")
ETF: Final = "etp/etf_bydd_trd"
ETN: Final = "etp/etn_bydd_trd"
# 처리 순서 — 기본정보(유니버스) → 주식 → 지수 → ETP
ORDER: Final[tuple[str, ...]] = (*BASE_INFO, *STOCK, *INDEX, ETF, ETN)
_SPACE = re.compile(r"\s+")


class CoverageDrop(RuntimeError):
    """받은 행 수가 너무 적다 — 쓰지 않았다(ET coverage_drop)."""


class WrongDay(RuntimeError):
    """응답 행의 기준일이 요청한 날과 다르다 — 쓰지 않았다(ET krx_regular_day)."""


@dataclass(frozen=True)
class Coverage:
    """커버리지 가드. floor = 직전 거래일 대비 최소 비율, min_rows = 시장별 절대 하한
    [확인 필요]."""

    floor: float = 0.95
    min_rows: Mapping[str, int] = field(
        default_factory=lambda: {"KOSPI": 700, "KOSDAQ": 1200, "KONEX": 0}
    )

    def check(self, market: str, n_today: int, n_prev: int | None, prev_day: date | None) -> None:
        low = self.min_rows.get(market, 0)
        if n_today < low:
            raise CoverageDrop(
                f"{market} {n_today:,}행 — 절대 하한 {low:,} 미만. 목록을 덜 받은 것으로 보고 쓰지 "
                "않았다"
            )
        if n_prev and n_today < n_prev * self.floor:
            raise CoverageDrop(
                f"{market} 행 수가 {prev_day} {n_prev:,} → {n_today:,} 로 "
                f"{(1 - n_today / n_prev) * 100:.1f}% 줄었다 — 쓰지 않았다"
            )


def index_code(name: str) -> str:
    """KRX 지수 일별에는 코드 칸이 없다 [실측 필요] — 공백을 뺀 지수 이름을 코드로 쓴다."""
    code = _SPACE.sub("", name)
    if not code:
        raise ValueError("지수 이름이 비었다")
    return code


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def _check_day(day: date, got: Sequence[date], what: str) -> None:
    other = sorted({d for d in got if d != day})
    if other:
        raise WrongDay(f"{what}: 요청 {day} 인데 응답 기준일 {other[0]} — 쓰지 않았다")


def _base_market(name: str | None) -> str:
    """'KOSDAQ GLOBAL' → 'KOSDAQ'(첫 낱말, 대문자). 커버리지 비교를 시장 단위로."""
    parts = (name or "").upper().split()
    return parts[0] if parts else ""


def _prev_count(r: RepoSet, day: date, market: str) -> tuple[int | None, date | None]:
    days = r.market.trading_days(day - timedelta(days=1), 1)
    if not days:
        return None, None
    prev = days[0]
    n = sum(1 for s in r.market.snapshots(prev, source=SOURCE) if _base_market(s.market) == market)
    return (n or None), prev


# ── 데이터셋별 ───────────────────────────────────────────────────────────────────────────


@dataclass
class _Ctx:
    repos: RepoSet
    client: KrxClient
    day: date
    now: datetime
    job: str
    coverage: Coverage
    detail: dict[str, Any]


def _stock(c: _Ctx, dataset: str) -> int:
    market = STOCK[dataset]
    raw = c.client.daily("/" + dataset, c.day)
    if not raw:
        raise NotReady(f"{dataset} {c.day} 아직 공표 전(빈 응답)")
    rows, bad = parse_stock_rows(raw, market=market)
    _check_day(c.day, [r.as_of for r in rows], dataset)
    label = market.upper()
    n_prev, prev_day = _prev_count(c.repos, c.day, label)
    c.coverage.check(label, len(rows), n_prev, prev_day)
    bars = [_bar(r) for r in rows]
    snaps = [_snap(r) for r in rows]
    c.repos.market.upsert_daily_bars(bars, loaded_by=c.job, asset="stock", received_at=c.now)
    c.repos.market.upsert_snapshots(snaps, loaded_by=c.job, received_at=c.now)
    if bad:
        c.detail.setdefault("bad_rows", {})[dataset] = len(bad)
    return len(rows)


def _bar(r: KrxStockRow) -> Bar:
    return Bar(
        code=r.code, date=r.as_of, open=_f(r.open), high=_f(r.high), low=_f(r.low),
        close=_f(r.close), volume=r.volume, turnover=r.turnover, source=SOURCE, venue=VENUE,
        quality=r.quality,
    )  # fmt: skip


def _snap(r: KrxStockRow) -> Snap:
    return Snap(
        code=r.code, date=r.as_of, name=r.name, market=r.market or None, kind=None,
        close=_f(r.close), chg_pct=_f(r.chg_pct), volume=r.volume, turnover=r.turnover,
        turnover_is_estimate=r.turnover_is_estimate, mktcap=r.mktcap, shares=r.shares,
        status_flags=None, source=SOURCE, venue=VENUE, quality=r.quality,
    )  # fmt: skip


def _base_info(c: _Ctx, dataset: str, *, notify_changes: bool) -> int:
    market = BASE_INFO[dataset]
    raw = c.client.daily("/" + dataset, c.day)
    if not raw:
        raise NotReady(f"{dataset} {c.day} 아직 공표 전(빈 응답)")
    rows, bad = parse_base_info_rows(raw, endpoint="/" + dataset, bas_dd=c.day)
    _check_day(c.day, [r.as_of for r in rows], dataset)
    uni: list[UniverseRow] = []
    for r in rows:
        code = isu_to_code(r.isu_srt_cd) or isu_to_code(r.isu_cd)
        if not code:
            continue
        flags = {
            k: v
            for k, v in (
                ("stock_kind", r.kind_stkcert_tp_nm),
                ("mkt_tp_nm", r.mkt_tp_nm),
                ("secugrp", r.secugrp_nm),
                ("sect", r.sect_tp_nm),
                ("isin", r.isu_cd),
                ("list_shrs", r.list_shrs),
            )
            if v is not None
        }
        uni.append(
            UniverseRow(
                code=code, as_of=c.day, name=r.isu_abbrv or r.isu_nm,
                market=market, kind=None, listed_on=r.list_dd, source=SOURCE,
                quality=Quality.OK, flags=flags,
            )
        )  # fmt: skip
    prev = {
        u.code
        for u in c.repos.market.universe(c.day - timedelta(days=1))
        if u.source == SOURCE and u.kind is None and u.market == market
    }
    if prev and len(uni) < len(prev) * c.coverage.floor:
        raise CoverageDrop(
            f"{market} 기본정보 {len(prev):,} → {len(uni):,}행 — 줄어든 폭이 커 쓰지 않았다"
        )
    c.repos.market.upsert_universe(uni, loaded_by=c.job, received_at=c.now)
    if bad:
        c.detail.setdefault("bad_rows", {})[dataset] = len(bad)
    if prev:
        now_codes = {u.code for u in uni}
        added, removed = sorted(now_codes - prev), sorted(prev - now_codes)
        if added or removed:
            ch = c.detail.setdefault("universe_changed", {})
            ch[market] = {"added": len(added), "removed": len(removed)}
            if notify_changes:
                c.detail.setdefault("_notify", []).append((market, added, removed))
    return len(uni)


def _index(c: _Ctx, dataset: str) -> int:
    raw = c.client.daily("/" + dataset, c.day)
    if not raw:
        raise NotReady(f"{dataset} {c.day} 아직 공표 전(빈 응답)")
    rows, bad = parse_index_rows(raw, endpoint="/" + dataset)
    _check_day(c.day, [r.as_of for r in rows], dataset)
    bars = [_index_bar(r) for r in rows]
    c.repos.market.upsert_index_bars(bars, loaded_by=c.job, received_at=c.now)
    if bad:
        c.detail.setdefault("bad_rows", {})[dataset] = len(bad)
    return len(bars)


def _index_bar(r: KrxIndexDaily) -> IndexBar:
    return IndexBar(
        code=index_code(r.idx_nm), date=r.as_of, name=r.idx_nm, open=_f(r.opnprc_idx),
        high=_f(r.hgprc_idx), low=_f(r.lwprc_idx), close=_f(r.clsprc_idx), volume=r.acc_trdvol,
        turnover=r.acc_trdval, source=SOURCE, quality=r.quality,
    )  # fmt: skip


def _etf(c: _Ctx) -> int:
    raw = c.client.daily("/" + ETF, c.day)
    if not raw:
        raise NotReady(f"{ETF} {c.day} 아직 공표 전(빈 응답)")
    rows, bad = parse_etf_daily_rows(raw)
    _check_day(c.day, [r.as_of for r in rows], ETF)
    days: list[EtfDay] = []
    for r in rows:
        code = isu_to_code(r.isu_cd)
        if not code:
            continue
        days.append(
            EtfDay(
                code=code, date=r.as_of, name=r.isu_nm, close=_f(r.tdd_clsprc), nav=_f(r.nav),
                list_shrs=r.list_shrs, net_asset=r.net_assets, turnover=r.acc_trdval,
                volume=r.acc_trdvol, mktcap=r.mktcap, base_index=r.idx_ind_nm, source=SOURCE,
                venue=VENUE, quality=r.quality,
            )
        )  # fmt: skip
    old = c.repos.etf.meta()
    meta = [_meta(d, old.get(d.code)) for d in days]
    c.repos.etf.upsert_etf_days(days, loaded_by=c.job, received_at=c.now)
    c.repos.etf.upsert_meta(meta, loaded_by=c.job, now=c.now)
    c.repos.market.upsert_universe(
        [_etp_universe(d.code, d.name, c.day, "etf") for d in days],
        loaded_by=c.job,
        received_at=c.now,
    )
    if bad:
        c.detail.setdefault("bad_rows", {})[ETF] = len(bad)
    return len(days)


def _meta(d: EtfDay, old: EtfMeta | None) -> EtfMeta:
    """이름·기초지수만 KRX 값으로. 나머지는 있던 값. 처음 보는 ETF(또는 유형이 빈 행)만 규칙 분류
    (`typed_meta` — 운용사·브랜드·테마·유형·배수. 상장일 등 모르는 칸은 None — 지어내지 않는다)."""
    m = _kept_meta(d, old)
    return typed_meta(m) if old is None or old.etf_type is None else m


def _kept_meta(d: EtfDay, old: EtfMeta | None) -> EtfMeta:
    return EtfMeta(
        code=d.code,
        name=d.name,
        issuer=None if old is None else old.issuer,
        brand=None if old is None else old.brand,
        theme=None if old is None else old.theme,
        etf_type=None if old is None else old.etf_type,
        leverage=None if old is None else old.leverage,
        base_index=d.base_index
        if d.base_index is not None
        else (None if old is None else old.base_index),
        listed_on=None if old is None else old.listed_on,
        delisted_on=None if old is None else old.delisted_on,
        source=SOURCE,
        quality=Quality.OK,
    )


def _etp_universe(code: str, name: str | None, day: date, kind: str) -> UniverseRow:
    return UniverseRow(
        code=code, as_of=day, name=name, market=None, kind=kind, listed_on=None, source=SOURCE,
        quality=Quality.OK,
    )  # fmt: skip


def _etn(c: _Ctx) -> int:
    raw = c.client.daily("/" + ETN, c.day)
    if not raw:
        raise NotReady(f"{ETN} {c.day} 아직 공표 전(빈 응답)")
    rows, bad = parse_etn_daily_rows(raw)
    _check_day(c.day, [r.as_of for r in rows], ETN)
    bars: list[Bar] = []
    for r in rows:
        code = isu_to_code(r.isu_cd)
        if not code:
            continue
        bars.append(
            Bar(
                code=code, date=r.as_of, open=_f(r.tdd_opnprc), high=_f(r.tdd_hgprc),
                low=_f(r.tdd_lwprc), close=_f(r.tdd_clsprc), volume=r.acc_trdvol,
                turnover=r.acc_trdval, source=SOURCE, venue=VENUE, quality=r.quality,
            )
        )  # fmt: skip
    c.repos.market.upsert_daily_bars(bars, loaded_by=c.job, asset="etn", received_at=c.now)
    names = {isu_to_code(r.isu_cd): r.isu_nm for r in rows}
    c.repos.market.upsert_universe(
        [_etp_universe(b.code, names.get(b.code), c.day, "etn") for b in bars],
        loaded_by=c.job,
        received_at=c.now,
    )
    if bad:
        c.detail.setdefault("bad_rows", {})[ETN] = len(bad)
    return len(bars)


# ── 실행 ────────────────────────────────────────────────────────────────────────────────


def collect(
    keys: Sequence[DataKey],
    *,
    r: RepoSet,
    client: KrxClient,
    day: date,
    now: datetime,
    job: str,
    coverage: Coverage | None = None,
    reconcile_cfg: Any = None,
    notify_changes: bool = True,
    etf_checks: EtfCfg | None = None,
) -> tuple[Outcome, list[tuple[str, list[str], list[str]]]]:
    """선점한 키들을 받는다. (결과, 유니버스 변동 [(시장, 새 종목, 빠진 종목)])."""
    out = Outcome(job, tuple(keys))
    c = _Ctx(r, client, day, now, job, coverage or Coverage(), out.detail)
    order = {name: i for i, name in enumerate(ORDER)}
    for key in sorted(keys, key=lambda k: order.get(k.dataset, len(order))):
        ds = key.dataset
        if ds in STOCK:
            out.run(key, lambda ds=ds: _stock(c, ds))
        elif ds in BASE_INFO:
            out.run(key, lambda ds=ds: _base_info(c, ds, notify_changes=notify_changes))
        elif ds in INDEX:
            out.run(key, lambda ds=ds: _index(c, ds))
        elif ds == ETF:
            out.run(key, lambda: _etf(c))
        elif ds == ETN:
            out.run(key, lambda: _etn(c))
        else:
            out.errors[key.label()] = f"모르는 KRX 데이터셋: {ds}"
    changes: list[tuple[str, list[str], list[str]]] = out.detail.pop("_notify", [])
    stock_done = any(k.dataset in STOCK for k in out.collected)
    if stock_done and reconcile_cfg is not None:
        try:
            summary = reconcile.record(r, day, now, reconcile_cfg)
        except Exception as e:  # 대조 실패는 기록하고 실행 실패로(사유) — 다음 시도가 다시 대조
            out.errors[f"reconcile@{day}"] = reason(e)
        else:
            out.detail.update(summary.detail())
            if summary.warn:
                log_event(log, logging.WARNING, "scheduler", "reconcile_mismatch", job=job,
                          as_of=day.isoformat(), mismatch=summary.mismatch,
                          checked=summary.checked)  # fmt: skip
    if etf_checks is not None and any(k.dataset == ETF for k in out.collected):
        _etf_checks(out, r, day, now, job, etf_checks)
    return out, changes


def _etf_checks(out: Outcome, r: RepoSet, day: date, now: datetime, job: str, cfg: EtfCfg) -> None:
    """검산 ③·분할 감지 기록(멱등). 실패는 실행 실패로(사유) — 삼키지 않는다.

    다음 시도가 다시 한다."""
    try:
        got = record_flow_checks(
            r, day, now=now, loaded_by=job, tol=cfg.split_tol, ratios=cfg.split_ratios
        )
    except Exception as e:
        out.errors[f"etf_checks@{day}"] = reason(e)
    else:
        out.detail["etf_checks"] = got


def _notify(
    ctx: JobContext, day: date, changes: Sequence[tuple[str, list[str], list[str]]]
) -> None:
    fn = ctx.resources.get("notify")
    if fn is None or not changes:
        return
    lines = [f"유니버스 변동 {day} (KRX 기본정보)"]
    for market, added, removed in changes:
        lines.append(f"{market}: 신규 {len(added)} · 제외 {len(removed)}")
        if added:
            lines.append("  신규 " + ", ".join(added[:10]) + (" …" if len(added) > 10 else ""))
        if removed:
            lines.append("  제외 " + ", ".join(removed[:10]) + (" …" if len(removed) > 10 else ""))
    try:
        ticket = fn("\n".join(lines), kind="ops.universe", source=ctx.job, as_of=day)
    except Exception as e:  # 알림 실패는 수집 실패가 아니다 — 사유만 남긴다
        log_event(log, logging.WARNING, "scheduler", "universe_notify_failed", error=reason(e))
        return
    if getattr(ticket, "ok", True) is False:
        log_event(log, logging.WARNING, "scheduler", "universe_notify_failed",
                  error=str(getattr(ticket, "error", ""))[:200])  # fmt: skip


def _handle(ctx: JobContext, *, backfill_mode: bool) -> JobResult:
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    days = {k.as_of for k in ctx.keys}
    if len(days) != 1:
        raise ValueError(f"한 실행에 날짜가 여럿이다: {sorted(days)}")
    day = date.fromisoformat(next(iter(days)))
    cfg = markets(ctx)
    client = krx_client(ctx, backfill=backfill_mode)
    coverage = ctx.resources.get("krx_coverage") or Coverage()
    out, changes = collect(
        ctx.keys,
        r=repos(ctx),
        client=client,
        day=day,
        now=ctx.now,
        job=ctx.job,
        coverage=coverage,
        reconcile_cfg=None if backfill_mode else cfg.reconcile,
        notify_changes=not backfill_mode,
        etf_checks=None if backfill_mode else cfg.etf,
    )
    if not backfill_mode:
        _notify(ctx, day, changes)
    return out.result()


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.krx_daily:run` — `krx.daily`)."""
    return _handle(ctx, backfill_mode=False)


def backfill(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.krx_daily:backfill` — `market.backfill`)."""
    return _handle(ctx, backfill_mode=True)
