"""금융위 주식·지수 일별 — `market.fsc_daily`(D+1 13:10) 처리기. KRX 종가 대조 보조(D-P5-19).

근거: docs/p5_design.md §1.6·§4.6, metrics §23(금융위 대조), probe_results §7 #48. 원본 기능: ET
`kr.yml:datago_daily`(금융위 시세로 일봉 보강). 어댑터는 P2 묶음 D(`kbj.data.private.
fsc_stock_price`·`fsc_index_price` — 로그인, 공공누리 4유형·원천 KRX).

- 금융위 시세 → `prv_market.daily_bar(source='fsc')`. 원장 순위(D-P3-7 — krx > kis > 그 밖)에서
  fsc 는 뒤라 KRX 행을 밀어내지 않는다(대조·보조용 — 화면·브리핑 미사용).
- 대조: 쓰기 전에 같은 날 KRX 행(`source='krx'`)을 읽어 종가를 비교한다 — 허용치
  `config/markets.yaml reconcile.close_tol_pct`(0 = 정확히 같아야). 불일치 비율이
  `reconcile.warn_mismatch_pct` 이상이면 **health 경고만** 낸다(로그 `fsc_mismatch` + detail
  `warn`). 수집 실패로 만들지 않는다(다른 원천의 사실 대조 — KRX 가 원장). 한쪽에만 있는 종목은
  수만 센다.
- 휴장일·공표 전(D+1 13시 전)이면 빈 응답 → 공표 전(`NotReady` — 재시도가 다시 부른다).
- 지수: `FSC_INDEXES` 이름(코스피·코스닥·코스피 200 — idxNm 표기 [확인 필요 #48])을 하나씩.
  코드는 KRX 지수 일별과 같은 규칙(공백을 뺀 이름 — `krx_daily.index_code`).
- 등록부에서 꺼져 있다(실응답 칸 확인 #48 전). 자원: `repos`·`markets`(없으면 설정),
  `fsc_stock`·`fsc_index`(없으면 `for_settings` + `redis`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from kbj.config.markets import ReconcileCfg
from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.core.rows import Bar, IndexBar
from kbj.data.private.fsc_index_price.client import FscIndexPriceClient, FscIndexRow
from kbj.data.private.fsc_stock_price.client import FscPriceRow, FscStockPriceClient
from kbj.data.spec import DataKey
from kbj.services.collectors._p3 import NotReady, Outcome, RepoSet, markets, repos
from kbj.services.collectors.krx_daily import index_code
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult

__all__ = ["FSC_INDEXES", "ROW_SOURCE", "CloseCheck", "compare_close", "run"]

log = logging.getLogger("kbj.services.collectors.fsc_daily")
ROW_SOURCE: Final = "fsc"
KRX_SOURCE: Final = "krx"
STOCK: Final = "DATAGO:15094808"
INDEX: Final = "DATAGO:15094807"
FSC_INDEXES: Final[tuple[str, ...]] = ("코스피", "코스닥", "코스피 200")  # idxNm [확인 필요 #48]
EXAMPLES_MAX: Final = 5


@dataclass(frozen=True)
class CloseCheck:
    """같은 날 KRX 종가 대 금융위 종가 대조 요약."""

    checked: int  # 둘 다 종가가 있는 종목
    mismatch: int
    only_fsc: int
    only_krx: int
    examples: tuple[str, ...]  # 'code krx→fsc' (최대 5)
    warn: bool

    @property
    def mismatch_pct(self) -> float | None:
        return None if not self.checked else round(self.mismatch / self.checked * 100, 3)

    def detail(self) -> dict[str, Any]:
        return dict(
            checked=self.checked, mismatch=self.mismatch, mismatch_pct=self.mismatch_pct,
            only_fsc=self.only_fsc, only_krx=self.only_krx, examples=list(self.examples),
            warn=self.warn,
        )  # fmt: skip


def _diff_pct(a: float, b: float) -> float:
    if a == b:
        return 0.0
    return abs(a - b) / abs(a) * 100 if a else float("inf")


def compare_close(
    krx: Mapping[str, float | None], fsc: Mapping[str, float | None], cfg: ReconcileCfg
) -> CloseCheck:
    """{코드: 종가} 둘 → 대조(순수). 허용치 0 이면 정확히 같아야 한다(metrics §23)."""
    both = sorted(c for c in krx.keys() & fsc.keys() if krx[c] is not None and fsc[c] is not None)
    bad: list[str] = []
    for c in both:
        k, f = krx[c], fsc[c]
        if k is not None and f is not None and _diff_pct(k, f) > cfg.close_tol_pct:
            bad.append(c)
    pct = len(bad) / len(both) * 100 if both else 0.0
    examples = tuple(f"{c} {krx[c]:g}→{fsc[c]:g}" for c in bad[:EXAMPLES_MAX])
    return CloseCheck(
        checked=len(both), mismatch=len(bad), only_fsc=len(fsc.keys() - krx.keys()),
        only_krx=len(krx.keys() - fsc.keys()), examples=examples,
        warn=bool(both) and pct >= cfg.warn_mismatch_pct,
    )  # fmt: skip


def _bar(r: FscPriceRow) -> Bar:
    return Bar(
        code=r.code, date=r.as_of, open=_f(r.mkp), high=_f(r.hipr), low=_f(r.lopr),
        close=_f(r.clpr), volume=r.trqu, turnover=r.tr_prc, source=ROW_SOURCE,
        venue=str(r.venue.value), quality=r.quality,
    )  # fmt: skip


def _index_bar(r: FscIndexRow) -> IndexBar:
    return IndexBar(
        code=index_code(r.idx_nm), date=r.as_of, name=r.idx_nm, open=_f(r.mkp), high=_f(r.hipr),
        low=_f(r.lopr), close=_f(r.clpr), volume=r.trqu, turnover=r.tr_prc, source=ROW_SOURCE,
        quality=r.quality,
    )  # fmt: skip


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _krx_closes(rows: Mapping[str, Sequence[Any]], day: date) -> dict[str, float | None]:
    return {
        c: b.close
        for c, bars in rows.items()
        for b in bars
        if b.date == day and b.source == KRX_SOURCE and b.quality is not Quality.INVALID
    }


def _warn(ctx: JobContext, what: str, day: date, chk: CloseCheck) -> None:
    if chk.warn:
        log_event(log, logging.WARNING, "scheduler", "fsc_mismatch", job=ctx.job, what=what,
                  as_of=day.isoformat(), mismatch=chk.mismatch, checked=chk.checked)  # fmt: skip


def _stock(
    ctx: JobContext, r: RepoSet, cfg: ReconcileCfg, day: date, detail: dict[str, Any]
) -> int:
    client: FscStockPriceClient = ctx.resources.get("fsc_stock") or (
        FscStockPriceClient.for_settings(_settings(ctx), ctx.resource("redis"))
    )
    rows, bad = client.day(day)
    if not rows:
        raise NotReady(f"금융위 주식시세 {day} 행이 없다(휴장일이거나 D+1 13시 공표 전)")
    krx = _krx_closes(r.market.series(None, day, 1), day)
    chk = compare_close(krx, {x.code: _f(x.clpr) for x in rows}, cfg)
    n = r.market.upsert_daily_bars([_bar(x) for x in rows], loaded_by=ctx.job, received_at=ctx.now)
    detail["stock"] = dict(chk.detail(), rows=len(rows), bad_rows=len(bad))
    _warn(ctx, "stock", day, chk)
    return n


def _index(
    ctx: JobContext, r: RepoSet, cfg: ReconcileCfg, day: date, detail: dict[str, Any]
) -> int:
    client: FscIndexPriceClient = ctx.resources.get("fsc_index") or (
        FscIndexPriceClient.for_settings(_settings(ctx), ctx.resource("redis"))
    )
    got: list[FscIndexRow] = []
    bad_n = 0
    for name in FSC_INDEXES:
        rows, bad = client.index(name, day, day)
        got.extend(rows)
        bad_n += len(bad)
    if not got:
        raise NotReady(f"금융위 지수시세 {day} 행이 없다(휴장일이거나 공표 전)")
    bars = [_index_bar(x) for x in got]
    krx = {
        c: b.close
        for c, rs in r.market.index_series([b.code for b in bars], day, 1).items()
        for b in rs
        if b.date == day and b.source == KRX_SOURCE
    }
    chk = compare_close(krx, {b.code: b.close for b in bars}, cfg)
    n = r.market.upsert_index_bars(bars, loaded_by=ctx.job, received_at=ctx.now)
    detail["index"] = dict(chk.detail(), rows=len(bars), bad_rows=bad_n)
    _warn(ctx, "index", day, chk)
    return n


def _settings(ctx: JobContext) -> Settings:
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다(KBJ_DATAGO_KEY)")
    return ctx.settings


def _day(key: DataKey) -> date:
    return date.fromisoformat(key.as_of[:10])


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기 `kbj.services.collectors.fsc_daily:run`(market.fsc_daily)."""
    r = repos(ctx)
    cfg = markets(ctx).reconcile
    out = Outcome(ctx.job, ctx.keys)
    for key in ctx.keys:
        day = _day(key)
        if key.dataset_id == STOCK:
            out.run(key, lambda day=day: _stock(ctx, r, cfg, day, out.detail))
        elif key.dataset_id == INDEX:
            out.run(key, lambda day=day: _index(ctx, r, cfg, day, out.detail))
        else:
            out.errors[key.label()] = "이 작업이 모르는 데이터셋"
    out.detail["warn"] = any(
        bool(v.get("warn")) for v in out.detail.values() if isinstance(v, dict)
    )
    return out.result()
