"""간밤 미국 신고가 수집 — `us.universe`(주 1회)·`us.eod`(미국 마감 16:10 ET) 처리기.

근거: docs/p5_design.md §1.6·§3.2·§4.6, D-P5-18, metrics §23, ADR 0023(초안). 승격 원본:
ETF-Traker `board/us/pipeline.py`(`universe`:22·`fetch_history`:58·`asof_from`:89·`daily`:128).

- `universe`: Nasdaq 스크리너(거래소 셋) → 하한(`config/us.yaml universe` — 시총·가격·거래대금
  추정)을 생성 단계에서 건다(ET 그대로) → `prv_market.us_universe` 에 그날 판 통째로. 섹터는 원본
  열(sector_raw·industry_raw)을 그대로 두고 분류는 엔진 단계에서(규칙이 바뀌어도 다시 받지 않게).
  ET 는 매일 스크리너를 다시 받았다 — KBJ 는 주 1회(등록부)라 하한 판정도 주 1회다(일봉 단계에서
  `engine.in_universe` 가 그날 값으로 한 번 더 건다 — ET `build` 와 같다).
- `eod`: 키 둘.
  - `NASDAQ:daily` — 유니버스 종목 일봉(출처 Nasdaq → 실패하면 Stooq, `history`). 이력이 짧은
    종목(< 252 + 추이 + 10봉)은 전 구간(760일), 나머지는 최근 90일만 겹쳐 받는다(ET `daily`).
    기준일은 **받은 일봉이 정한다**(`asof_from` — 가장 많은 종목의 마지막 봉). 그날 봉이 아직
    없으면 공표 전(`NotReady`). 받은 뒤 엔진(`kbj.engines.us` — 252봉)으로 us_summary(보드)·
    us_brief(텔레그램 HTML 조각 — 아침 브리핑 ① 이 그대로 싣는다)를 `prv_board.artifact`
    (market='US')에 쓴다. 일봉 수집 실패 종목·섹터 미분류·이력 부족 보류는 숨기지 않고 보드의
    `missing`(브리프 '⚠ 빠진 데이터')과 실행 기록 detail 에 적는다(ET 그대로).
  - `YAHOO:us_index_daily` — 지수(`config/us.yaml indexes`) 일봉 → daily_bar(asset index, yahoo).
- 수정주가 여부: 실행마다 `adjust_probe`(NVDA 분할 대조) 사실을 detail·보드에 남기고, Nasdaq 행의
  `adjusted` 에 싣는다(모르면 None). Stooq 행은 None [확인 필요 #47].
- 로그인 작업 — `pub_*` 에 쓰지 않는다(ADR 0002). 키 없는 출처라 마스킹할 비밀은 없다.
- 자원: `us_config`(없으면 `load_us`), `repos`(없으면 `connect` → `pg_repos`), `nasdaq`·`stooq`·
  `yahoo`(없으면 `from_settings` + `redis`).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

from kbj.core.p5_rows import UsArtifact, UsDailyRow, UsUniverseRow
from kbj.core.quality import Quality
from kbj.data.http import FetchError
from kbj.data.private.nasdaq.client import ROW_SOURCE as NASDAQ_SOURCE
from kbj.data.private.nasdaq.client import NasdaqClient, NasdaqThrottled
from kbj.data.private.nasdaq.history import adjust_probe, adjusted_flag, history
from kbj.data.private.stooq.client import StooqClient
from kbj.data.private.yahoo.client import YahooClient, YahooError
from kbj.data.spec import DataKey
from kbj.engines.us import ENGINE_VERSION
from kbj.engines.us import board as us_board
from kbj.engines.us import brief as us_brief
from kbj.engines.us import sectors as us_sectors
from kbj.engines.us.config import load_us
from kbj.engines.us.engine import BASES
from kbj.services.collectors._p3 import NotReady, Outcome
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import Repos, pg_repos

__all__ = [
    "asof_from",
    "build_artifacts",
    "eod",
    "fetch_history",
    "need_bars",
    "select_universe",
    "universe",
]

log = logging.getLogger("kbj.services.collectors.us")
SCREENER: Final = "NASDAQ:screener"
DAILY: Final = "NASDAQ:daily"
INDEX: Final = "YAHOO:us_index_daily"
UNIVERSE_SOURCE: Final = "nasdaq"
INDEX_RANGE: Final = "1mo"
FAIL_HEAD: Final = 3
FRESH_PROBE_N: Final = 3  # 전 종목을 받기 전에 그날 봉이 나왔는지 먼저 볼 종목 수


# ── 순수 도우미(ET pipeline 본문) ─────────────────────────────────────────────────────────


def select_universe(
    rows: Sequence[dict[str, Any]], cfg: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """스크리너 행 → 하한 통과 행 + 메타(ET `universe` 본문 — 섹터 배정은 미분류 수를 세려고)."""
    us_sectors.apply(rows)  # 행에 sector·industry·sector_src 를 채운다(원본 열은 남는다)
    u = cfg["universe"]
    keep = [
        r for r in rows
        if (r.get("mktcap") or 0) >= u["min_mktcap_usd"]
        and (r.get("close") or 0) >= u["min_price_usd"]
        and (r.get("turnover") or 0) >= u["min_turnover_usd"]
    ]  # fmt: skip
    # 미분류는 **쓰는 유니버스 기준**으로 센다(ET run #3). 원인도 가른다 — 소스 공란과 규칙 미비.
    n_unmapped = sum(1 for r in keep if r.get("sector") == us_sectors.UNMAPPED)
    n_blank = sum(
        1 for r in keep
        if r.get("sector") == us_sectors.UNMAPPED
        and not (r.get("industry_raw") or r.get("sector_raw"))
    )  # fmt: skip
    meta = dict(
        screened=len(rows), kept=len(keep), unmapped=n_unmapped, blank=n_blank,
        by_source=us_sectors.by_source(keep),
    )  # fmt: skip
    return keep, meta


def asof_from(series: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[str | None, int]:
    """가장 많은 종목이 공유하는 마지막 봉 날짜. (기준일, 그 날짜를 쓰는 종목 수) — ET 그대로.

    기준일은 달력이 아니라 받은 일봉이 정한다(휴장·서머타임·날짜 밀림)."""
    c = Counter(bars[-1]["asof"] for bars in series.values() if bars)
    if not c:
        return None, 0
    asof, n = c.most_common(1)[0]
    return asof, n


def need_bars(cfg: Mapping[str, Any]) -> int:
    """52주 창(252) + 5일 추이 + 여유 — 이만큼 없으면 전 구간을 새로 받는다(ET `daily`)."""
    return int(cfg["newhigh"]["lookback"]["w52"]) + int(cfg["brief"]["trend_days"]) + 10


def fetch_history(
    tickers: Sequence[str],
    fetch: Callable[[str], list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[str, str]]]:
    """일봉 수집. 실패는 삼키지 않고 사유와 함께 모아 돌려준다(ET `fetch_history` — 재시도는
    리미터·작업 재시도가 한다). 429(감속 신호)는 남은 종목을 부르지 않고 올린다."""
    out: dict[str, list[dict[str, Any]]] = {}
    fails: list[tuple[str, str]] = []
    for t in tickers:
        try:
            bars = fetch(t)
        except NasdaqThrottled:
            raise
        except FetchError as e:
            fails.append((t, e.reason[:160]))
            continue
        if bars:
            out[t] = bars
        else:
            fails.append((t, "빈 응답"))
    return out, fails


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _vol(v: Any) -> int | None:
    return None if v is None else round(float(v))


def _daily_row(sym: str, r: Mapping[str, Any], adjusted: bool | None) -> UsDailyRow:
    src = str(r.get("source") or NASDAQ_SOURCE)
    return UsDailyRow(
        symbol=sym, date=date.fromisoformat(r["asof"]), asset="stock", open=_f(r.get("open")),
        high=_f(r.get("high")), low=_f(r.get("low")), close=_f(r.get("close")),
        volume=_vol(r.get("volume")), adjusted=adjusted if src == NASDAQ_SOURCE else None,
        source=src, quality=Quality.OK if r.get("close") is not None else Quality.INVALID,
    )  # fmt: skip


def _bar_dict(b: UsDailyRow) -> dict[str, Any]:
    return dict(asof=b.date.isoformat(), open=b.open, high=b.high, low=b.low, close=b.close,
                volume=b.volume)  # fmt: skip


def _snap(u: UsUniverseRow, bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """스냅샷(ET 스크리너 행 모양) — 시총·이름·섹터는 유니버스, 가격·등락은 그날 일봉."""
    last = bars[-1] if bars else {}
    prev = bars[-2] if len(bars) > 1 else {}
    close, vol = last.get("close"), last.get("volume")
    chg = (
        round((close / prev["close"] - 1) * 100, 2)
        if close is not None and prev.get("close")
        else None
    )
    sec, ind, why = us_sectors.classify(u.symbol, u.sector, u.industry, name=u.name)
    return dict(
        ticker=u.symbol, name=u.name, close=close, chg_pct=chg, volume=vol, mktcap=u.market_cap,
        turnover=(close * vol) if close is not None and vol is not None else None,
        sector=sec, industry=ind, sector_src=why, sector_raw=u.sector or "",
        industry_raw=u.industry or "", exchange=u.exchange, source=u.source,
    )  # fmt: skip


def _view(board: Mapping[str, Any], cfg: Mapping[str, Any]) -> dict[str, Any]:
    """브리프 절 함수가 읽는 표시 값(경계·행 수)을 설정에서 붙인다 — `brief.render` 와 같은 규칙."""
    br = cfg["brief"]
    b = dict(board)
    b.setdefault("move_pct", br["move_pct"])
    for k in ("megacap_usd", "movers_min_mktcap_usd", "sector_top", "streak_rows", "fresh_rows",
              "fresh_days"):  # fmt: skip
        b[k] = br[k]
    return b


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, ensure_ascii=False, default=str))


def build_artifacts(
    *,
    day: date,
    uni: Sequence[UsUniverseRow],
    bars: Mapping[str, Sequence[UsDailyRow]],
    cfg: Mapping[str, Any],
    now: datetime,
    missing: Sequence[str] = (),
    probe: Sequence[tuple[str, bool, str]] = (),
) -> tuple[UsArtifact, UsArtifact, dict[str, Any]]:
    """저장된 유니버스·일봉 → (us_summary, us_brief, 요약). 순수(시계 주입 — `now`).

    us_summary = 설정 기본 기준(종가)의 보드·종목 행 + 다른 기준(고가)의 보드·행(`alt` — API
    `basis=high` 가 읽는다. 리더는 DB 만 읽으므로 두 해석을 여기서 미리 만든다) + 한눈에 문장
    (`summary_lines` — 브리프 ① 의 줄, 템플릿·LLM 없음) + 라벨 이름. us_brief = 기본 기준의
    텔레그램 HTML 조각(`parts`)·평문(`text`)."""
    asof = day.isoformat()
    series = {s: [_bar_dict(b) for b in rows] for s, rows in bars.items() if rows}
    snaps = {u.symbol: _snap(u, series.get(u.symbol, [])) for u in uni if u.symbol in series}
    need = need_bars(cfg)
    pending = sorted(u.symbol for u in uni if len(series.get(u.symbol, ())) < need)
    miss = list(missing)
    unmapped = sum(1 for s in snaps.values() if s["sector"] == us_sectors.UNMAPPED)
    if unmapped:
        miss.append(f"섹터 미분류 {unmapped}종목")
    if pending:
        miss.append(
            f"이력이 짧아 52주 판정 보류 {len(pending)}종목 "
            f"(상장 후 {need}거래일을 못 채웠거나 소스가 과거를 안 준다)"
        )
    stamp = now.isoformat(timespec="seconds")
    default = str(cfg["newhigh"]["default_basis"])
    by_basis: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    for basis in (default, *(b for b in BASES if b != default)):
        cfg_b = dict(cfg, newhigh=dict(cfg["newhigh"], default_basis=basis))
        rows_b, board_b = us_board.build(
            series, snaps, asof, cfg_b, missing=miss, log=lambda *_a: None, generated_at=stamp
        )
        board_b["pending"] = len(pending)
        board_b["adjust_probe"] = [dict(name=n, ok=ok, note=why) for n, ok, why in probe]
        by_basis[basis] = (rows_b, board_b)
    rows, board = by_basis[default]
    sources = sorted({b.source for rs in bars.values() for b in rs[-1:]})
    src = "+".join(sources) or NASDAQ_SOURCE
    digest = hashlib.sha256(
        json.dumps(
            [asof, sorted((s, r[-1]["close"]) for s, r in series.items())], default=str
        ).encode()
    ).hexdigest()[:32]
    summary = UsArtifact(
        trade_date=day, name="us_summary",
        payload=_jsonable(
            dict(
                board=board, rows=rows, labels=dict(cfg["newhigh"].get("labels") or {}),
                summary_lines=us_brief.sec1(_view(board, cfg))[1:],
                alt={
                    b: dict(board=bd, rows=rs, summary_lines=us_brief.sec1(_view(bd, cfg))[1:])
                    for b, (rs, bd) in by_basis.items()
                    if b != default
                },
            )
        ),
        engine_version=ENGINE_VERSION, input_digest=digest, as_of=now, generated_at=now,
        source=src, quality=Quality.OK,
    )  # fmt: skip
    brief = UsArtifact(
        trade_date=day, name="us_brief",
        payload=_jsonable(
            dict(parts=us_brief.telegram_parts(board, rows, cfg),
                 text=us_brief.render(board, rows, cfg))
        ),
        engine_version=ENGINE_VERSION, input_digest=digest, as_of=now, generated_at=now,
        source=src, quality=Quality.OK,
    )  # fmt: skip
    info = dict(
        universe=board["universe"]["n"], collected=board["universe"]["collected"],
        newhigh=board["summary"]["newhigh"], w52_high=board["summary"]["w52_high"],
        pending=len(pending), unmapped=unmapped,
    )  # fmt: skip
    return summary, brief, info


# ── 자원 ────────────────────────────────────────────────────────────────────────────────


def _cfg(ctx: JobContext) -> dict[str, Any]:
    return ctx.resources.get("us_config") or load_us(settings=ctx.settings)


def _repos(ctx: JobContext) -> Repos:
    return ctx.resources.get("repos") or pg_repos(ctx.resource("connect"))


def _settings(ctx: JobContext) -> Any:
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다")
    return ctx.settings


def _nasdaq(ctx: JobContext) -> NasdaqClient:
    got = ctx.resources.get("nasdaq")
    return got or NasdaqClient.from_settings(_settings(ctx), ctx.resource("redis"))


def _stooq(ctx: JobContext) -> StooqClient:
    got = ctx.resources.get("stooq")
    return got or StooqClient.from_settings(_settings(ctx), ctx.resource("redis"))


def _yahoo(ctx: JobContext) -> YahooClient:
    got = ctx.resources.get("yahoo")
    return got or YahooClient.from_settings(_settings(ctx), ctx.resource("redis"))


def _key_day(key: DataKey) -> date:
    return date.fromisoformat(key.as_of[:10])


# ── us.universe ─────────────────────────────────────────────────────────────────────────


def _universe_key(
    ctx: JobContext, cfg: Mapping[str, Any], repos: Repos, key: DataKey, detail: dict[str, Any]
) -> int:
    day = _key_day(key)
    rows, drop = _nasdaq(ctx).screener(cfg)
    keep, meta = select_universe(rows, cfg)
    if not keep:
        raise NotReady(f"스크리너 {len(rows)}종목 중 하한 통과 0 — 응답·하한 확인 필요")
    out = [
        UsUniverseRow(
            symbol=r["ticker"], as_of=day, name=r.get("name") or None,
            exchange=r.get("exchange"), sector=r.get("sector_raw") or None,
            industry=r.get("industry_raw") or None, market_cap=r.get("mktcap"), is_fund=False,
            source=UNIVERSE_SOURCE, quality=Quality.OK,
        )
        for r in keep
    ]  # fmt: skip
    n = repos.us.put_universe(day, out, loaded_by=ctx.job, received_at=ctx.now)
    detail.update(meta, dropped=drop)
    log_event(log, logging.INFO, "scheduler", "us_universe", job=ctx.job, as_of=key.as_of,
              screened=meta["screened"], kept=meta["kept"], unmapped=meta["unmapped"])  # fmt: skip
    return n


def universe(ctx: JobContext) -> JobResult:
    """등록부 처리기 `kbj.services.collectors.us:universe`(us.universe — 주 1회)."""
    cfg = _cfg(ctx)
    repos = _repos(ctx)
    out = Outcome(ctx.job, ctx.keys)
    for key in ctx.keys:
        if key.dataset_id == SCREENER:
            out.run(key, lambda key=key: _universe_key(ctx, cfg, repos, key, out.detail))
        else:
            out.errors[key.label()] = "이 작업이 모르는 데이터셋"
    return out.result()


# ── us.eod ──────────────────────────────────────────────────────────────────────────────


def _require_day_bar(
    tickers: Sequence[str], fetch: Callable[[str], list[dict[str, Any]]], day: date
) -> None:
    """앞 몇 종목으로 그날 봉이 공표됐는지 먼저 본다 — 받은 종목이 **모두** 그날 봉을 가져야 통과.

    전에는 1,700여 종목을 다 받은 뒤(약 1시간)에야 그날 봉이 없음을 알아, 재시도 한 번이 마감
    (120분)을 다 써 미국 보드가 이틀 묵었다(2026-10-19 — 16:12 ET 에 아직 없었다). 하나만 있어도
    통과시키면 공표가 막 시작된 때 전 종목을 받고(67분) 다수 기준일이 전날이라 다시 '공표 전'이
    됐다(2026-10-20 점검). 받기 자체가 실패한 종목은 판정하지 않는다."""
    seen = 0
    for t in tickers[:FRESH_PROBE_N]:
        try:
            rows = fetch(t)
        except FetchError:
            continue
        seen += 1
        if not any(r.get("asof") == day.isoformat() for r in rows):
            raise NotReady(f"기준일 {day} 봉이 아직 없다(앞 종목 {t} 확인)")


def _daily_key(
    ctx: JobContext, cfg: Mapping[str, Any], repos: Repos, key: DataKey, detail: dict[str, Any]
) -> int:
    day = _key_day(key)
    uni = [u for u in repos.us.universe(day) if not u.is_fund]
    if not uni:
        raise NotReady(f"{day} 이하 미국 유니버스가 없다 — us.universe 먼저")
    tickers = [u.symbol for u in uni]
    need = need_bars(cfg)
    src = cfg["sources"]
    have = repos.us.daily(tickers, day, need)
    fresh = [t for t in tickers if len(have.get(t, ())) < need]
    fresh_set = set(fresh)
    known = [t for t in tickers if t not in fresh_set]
    nasdaq, stooq = _nasdaq(ctx), _stooq(ctx)

    def fetcher(days: int) -> Callable[[str], list[dict[str, Any]]]:
        start = day - timedelta(days=days)
        return lambda t: history(t, cfg, nasdaq=nasdaq, stooq=stooq, start=start, end=day)

    _require_day_bar(known or tickers, fetcher(10), day)
    probe = adjust_probe(fetcher(int(src.get("history_days_known", 90))))
    adjusted = adjusted_flag(probe)
    series: dict[str, list[dict[str, Any]]] = {}
    fails: list[tuple[str, str]] = []
    for group, days in ((known, int(src.get("history_days_known", 90))),
                        (fresh, int(src.get("history_days_new", 760)))):  # fmt: skip
        if not group:
            continue
        got, bad = fetch_history(group, fetcher(days))
        series.update(got)
        fails.extend(bad)
    rows = [
        _daily_row(sym, r, adjusted)
        for sym, bars in series.items()
        for r in bars
        if r["asof"] <= day.isoformat()
    ]
    n = repos.us.upsert_daily(rows, loaded_by=ctx.job, received_at=ctx.now)
    asof, n_asof = asof_from({s: [r for r in b if r["asof"] <= day.isoformat()]
                              for s, b in series.items()})  # fmt: skip
    detail.update(
        fetched=len(series), fetch_failed=len(fails), fresh=len(fresh), asof_bars=n_asof,
        adjusted=adjusted, fail_head=[f"{t}: {why}" for t, why in fails[:FAIL_HEAD]],
    )  # fmt: skip
    if asof != day.isoformat():
        raise NotReady(f"기준일 {day} 봉이 아직 없다(받은 일봉의 마지막 날 {asof})")
    missing: list[str] = []
    if fails:
        head = ", ".join(f"{t}: {why}" for t, why in fails[:FAIL_HEAD])
        more = f" 외 {len(fails) - FAIL_HEAD}종목" if len(fails) > FAIL_HEAD else ""
        missing.append(f"일봉 수집 실패 {len(fails)}종목 — {head}{more}")
    window = int(src.get("series_days", 520))
    stored = repos.us.daily(tickers, day, window)
    summary, brief, info = build_artifacts(
        day=day, uni=uni, bars=stored, cfg=cfg, now=ctx.now, missing=missing, probe=probe
    )
    repos.board.put_us_artifacts([summary, brief], loaded_by=ctx.job)
    detail.update(info)
    return n + 2


def _index_key(ctx: JobContext, cfg: Mapping[str, Any], repos: Repos, key: DataKey) -> int:
    day = _key_day(key)
    symbols = [str(i["symbol"]) for i in cfg.get("indexes") or ()]
    if not symbols:
        raise NotReady("config/us.yaml indexes 가 비었다")
    got, failed = _yahoo(ctx).charts(symbols, INDEX_RANGE)
    rows = [
        UsDailyRow(
            symbol=s, date=b.date, asset="index", open=b.open, high=b.high, low=b.low,
            close=b.close, volume=b.volume, adjusted=None, source=b.source, quality=b.quality,
        )
        for s, bars in got.items()
        for b in bars
        if b.date <= day
    ]  # fmt: skip
    n = repos.us.upsert_daily(rows, loaded_by=ctx.job, received_at=ctx.now)
    if failed:
        sym, why = next(iter(failed.items()))
        raise YahooError(f"chart:{sym}", None, f"지수 {len(failed)}개 실패({sym} …) — {why}")
    if not any(b.date == day for bars in got.values() for b in bars):
        raise NotReady(f"지수 {day} 봉이 아직 없다")
    return n


def eod(ctx: JobContext) -> JobResult:
    """등록부 처리기 `kbj.services.collectors.us:eod`(us.eod — 16:10 ET)."""
    cfg = _cfg(ctx)
    repos = _repos(ctx)
    out = Outcome(ctx.job, ctx.keys)
    order = {INDEX: 0, DAILY: 1}
    for key in sorted(ctx.keys, key=lambda k: order.get(k.dataset_id, 9)):
        if key.dataset_id == DAILY:
            out.run(key, lambda key=key: _daily_key(ctx, cfg, repos, key, out.detail))
        elif key.dataset_id == INDEX:
            out.run(key, lambda key=key: _index_key(ctx, cfg, repos, key))
        else:
            out.errors[key.label()] = "이 작업이 모르는 데이터셋"
    return out.result()
