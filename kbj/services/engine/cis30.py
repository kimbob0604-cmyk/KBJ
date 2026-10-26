"""cis 30분봉 — 분봉 받기·백테스트 CLI(`kbj.sh cis30 probe|fetch|run`).

    python -m kbj.services.engine.cis30 probe [--code 005930]
    python -m kbj.services.engine.cis30 fetch [--months 12] [--pool passed|spikes] [--window 5]
        [--limit N] [--dry-run]
    python -m kbj.services.engine.cis30 run [--stop 2] [--trail 4] [--vol-mult 2] [--box 13]
        [--stop-mode pct|bar_low] [--intraday] [--hold 3] [--no-pyramid] [--trades 15] [--sweep]

- probe: KIS 주식일별분봉조회가 되는지·얼마나 옛날까지 주는지·칸 이름(값은 찍지 않는다).
- fetch: 일봉 신호(config/screen.yaml `cis30.pool`)의 신호일 D ~ D+`window_days` 를 5분봉으로 받는다
  (`prv_market.stock_bar_5m`). 이미 받은 (종목, 날)은 건너뛴다 — 끊겨도 다시 돌리면 이어 간다.
  KIS 앱키 리미터(4/s)를 나눠 쓰는 P4 라 장 마감 뒤·주말에 돌린다.
- run: 받은 분봉으로 30분봉 백테스트(`kbj.engines.screen.intraday`). 결과는 화면만. 주문 코드 없음.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import IO, Any, Final

from kbj.config.screen import Cis30Cfg, CisCfg, load_screen
from kbj.engines.screen.backtest import (
    REASONS,
    Signal,
    Summary,
    regime_series,
    signals_of,
    summarize,
)
from kbj.engines.screen.intraday import fetch_pairs, run_intraday, watch_days
from kbj.services.engine.backtest_cis import CAVEATS, Loaded, _p, _x, load_daily, sweep_table

__all__ = ["SWEEP30", "main"]

KIS_KEEP_DAYS: Final = 364  # KIS 분봉 보관(최대 1년)
CALLS_PER_DAY: Final = 4
KIS_RATE: Final = 4.0  # config/limits.yaml kis.rate — 다른 수집과 나눠 쓴다
FLAT_BAND: Final = 0.5
CAVEATS30: Final = (
    "분봉은 KIS 보관 1년 안·신호 종목의 D~D+창만 — 기간이 짧다",
    "5분봉 안에서 손절가와 새 고가가 다 나오면 손절이 먼저라고 본다(보수적)",
    *CAVEATS,
)


@dataclass
class Env:
    load: Callable[[int, date | None, CisCfg], Loaded]
    minutes: Callable[[], Any]  # → 분봉 저장소
    kis: Callable[[], Any]  # → KIS 클라이언트(fetch·probe 만)


def _pg_env() -> Env:  # pragma: no cover — 운영(DB·Redis)
    def minutes() -> Any:
        from kbj.config.settings import Settings
        from kbj.store.db import connect
        from kbj.store.repos.stock_minute import PgStockMinuteRepo

        settings = Settings()
        return PgStockMinuteRepo(lambda: connect(settings, service="cis30"))

    def kis() -> Any:
        from kbj.config.settings import Settings
        from kbj.data.private.kis.rest import KisRestClient
        from kbj.services.runtime.heartbeat import connect_redis

        settings = Settings()
        if settings.redis_url is None:
            raise RuntimeError("KBJ_REDIS_URL 이 필요하다")
        return KisRestClient.for_service(settings, connect_redis(settings.redis_url))

    return Env(load_daily, minutes, kis)


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m kbj.services.engine.cis30")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe", help="KIS 분봉 조회 확인(보관 기간·칸 이름)")
    pr.add_argument("--code", default="005930")
    fe = sub.add_parser("fetch", help="신호 종목 분봉 받기(이어받기)")
    fe.add_argument("--months", type=int, default=12, help="몇 달 전 신호부터(KIS 보관 1년 안)")
    fe.add_argument("--pool", choices=("passed", "spikes"), default=None)
    fe.add_argument("--window", type=int, default=None, help="신호일 뒤 며칠까지")
    fe.add_argument("--limit", type=int, default=None, help="이번에 받을 (종목, 날) 상한")
    fe.add_argument("--dry-run", action="store_true", help="계획만 찍는다")
    ru = sub.add_parser("run", help="30분봉 백테스트")
    ru.add_argument("--pool", choices=("passed", "spikes"), default=None)
    ru.add_argument("--stop", type=float, default=None)
    ru.add_argument("--trail", type=float, default=None)
    ru.add_argument("--vol-mult", type=float, default=None)
    ru.add_argument("--box", type=int, default=None)
    ru.add_argument("--stop-mode", choices=("pct", "bar_low"), default=None)
    ru.add_argument("--hold", type=int, default=None, help="보유 거래일 상한")
    ru.add_argument("--intraday", action="store_true", help="당일 매매(그날 종가에 다 판다)")
    ru.add_argument("--no-pyramid", action="store_true")
    ru.add_argument("--trades", type=int, default=15)
    ru.add_argument("--sweep", action="store_true", help="규칙 조합 비교 표")
    return ap.parse_args(argv)


def _cfg30(a: argparse.Namespace, base: Cis30Cfg) -> Cis30Cfg:
    upd: dict[str, Any] = {}
    flags = {"pool": "pool", "window_days": "window", "stop_pct": "stop", "trail_pct": "trail",
             "vol_mult": "vol_mult", "box_bars": "box", "stop_mode": "stop_mode",
             "max_hold_days": "hold"}  # fmt: skip
    for key, flag in flags.items():
        val = getattr(a, flag, None)
        if val is not None:
            upd[key] = val
    if getattr(a, "intraday", False):
        upd["overnight"] = False
    if getattr(a, "no_pyramid", False):
        upd["add_frac"] = 0.0
    return base.model_copy(update=upd)


def _signals(got: Loaded, cfg: CisCfg, c30: Cis30Cfg, start: date) -> dict[date, list[Signal]]:
    passed = c30.pool == "passed"
    regimes = regime_series(got.index_bars, cfg) if passed else {}
    return signals_of(got.series, got.meta, regimes, cfg, start=start, passed_only=passed)


def _load(env: Env, cfg: CisCfg, months: int) -> tuple[Loaded, list[date], date]:
    n = months * 22 + cfg.lookback_days + 10
    got = env.load(n, None, cfg)
    days = sorted(got.days)
    if len(days) <= cfg.lookback_days + 1:
        raise RuntimeError(f"일봉 거래일이 {len(days)}일뿐이다")
    last = days[-1]
    start = max(days[cfg.lookback_days + 1], last - timedelta(days=min(months * 31, KIS_KEEP_DAYS)))
    return got, days, start


def _probe(env: Env, code: str, out: IO[str]) -> int:
    from kbj.services.collectors.stock_minutes import probe

    cfg = load_screen().cis
    got = env.load(cfg.lookback_days + 5, None, cfg)
    last = max(got.days)
    picks = [last - timedelta(days=k) for k in (0, 30, 90, 180, 270, 340, 380)]
    out.write(f"KIS 주식일별분봉조회 확인 — {code} · 장 마감(15:30)부터 120봉씩\n")
    client = env.kis()
    try:
        probe(client, code, [_weekday(d) for d in picks], out)
    finally:
        client.close()
    out.write("보관 기간: 봉이 0 이 되는 날짜 앞까지. 칸 이름·시각 범위를 그대로 붙여 주세요\n")
    return 0


def _weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _fetch(env: Env, a: argparse.Namespace, out: IO[str]) -> int:
    from kbj.services.collectors.stock_minutes import collect

    conf = load_screen()
    cfg, c30 = conf.cis, _cfg30(a, conf.cis30)
    got, days, start = _load(env, cfg, a.months)
    sig = _signals(got, cfg, c30, start)
    pairs = [p for p in fetch_pairs(sig, days, c30.window_days) if p[1] >= start]
    repo = env.minutes()
    have = repo.days(start, days[-1])
    todo = [p for p in pairs if p not in have]
    if a.limit is not None:
        todo = todo[: a.limit]
    calls = len(todo) * CALLS_PER_DAY
    out.write(
        f"cis30 분봉 받기 — 신호 {sum(len(v) for v in sig.values()):,}개({c30.pool}, {start} ~ "
        f"{days[-1]}) · 창 D~D+{c30.window_days}\n"
        f"(종목, 날) {len(pairs):,} · 이미 있음 {sum(p in have for p in pairs):,}"
        f" · 이번에 받을 것 {len(todo):,} · 호출 약 {calls:,}회 · {KIS_RATE:g}/s 로 약 "
        f"{calls / KIS_RATE / 3600:.1f}시간\n"
        "(다른 수집이 쓰면 더 걸린다 — 끊겨도 다시 돌리면 이어 간다)\n"
    )
    if a.dry_run or not todo:
        return 0
    client = env.kis()
    try:
        res = collect(client, repo, todo, out=out)
    finally:
        client.close()
    errs = " · ".join(f"{k} {v:,}" for k, v in res.errors.most_common(5)) or "없음"
    out.write(
        f"끝 — 받음 {res.days:,}일 · 5분봉 {res.bars:,}개 · 빈 날 {res.empty:,} · "
        f"호출 {res.calls:,} · "
        f"오류: {errs}\n"
    )
    return 0


SWEEP30: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("지금 설정", {}),
    ("손절 −1.5%", {"stop_pct": 1.5}),
    ("손절 −3%", {"stop_pct": 3.0}),
    ("손절 = 돌파 봉 저가", {"stop_mode": "bar_low"}),
    ("추적 −3%", {"trail_pct": 3.0}),
    ("추적 −6%", {"trail_pct": 6.0}),
    ("추적 −10%·보유 5일", {"trail_pct": 10.0, "max_hold_days": 5}),
    ("당일 매매", {"overnight": False}),
    ("거래량 ×1.5", {"vol_mult": 1.5}),
    ("거래량 ×3", {"vol_mult": 3.0}),
    ("박스 26봉(이틀)", {"box_bars": 26}),
    ("불타기 끔", {"add_frac": 0.0}),
)


def _rules(c: Cis30Cfg) -> str:
    stop = f"−{c.stop_pct:g}%" if c.stop_mode == "pct" else "돌파 봉 저가"
    return (
        f"규칙: 일봉 신호({c.pool}) 뒤 {c.entry_days}일 감시 · "
        f"{c.bar_minutes}분봉 박스({c.box_bars}봉) 돌파 "
        f"+ 거래량 ×{c.vol_mult:g} · 다음 봉 시가 진입 · 손절 {stop} · 추적 −{c.trail_pct:g}% · "
        f"본전 +{c.breakeven_at_pct:g}% · 불타기 +{c.add_at_pct:g}% ×{c.add_frac:g} · "
        f"{'보유 최대 ' + str(c.max_hold_days) + '일' if c.overnight else '당일 매매'} · 위험 "
        f"{c.risk_pct:g}% · 최대 {c.max_positions}종목"
    )


def _run(env: Env, a: argparse.Namespace, out: IO[str]) -> int:
    conf = load_screen()
    cfg, c30 = conf.cis, _cfg30(a, conf.cis30)
    got, days, start = _load(env, cfg, 12)
    sig = _signals(got, cfg, c30, start)
    watch = watch_days(sig, days, c30.entry_days)
    pairs = fetch_pairs(sig, days, c30.window_days)
    codes = {c for c, _ in pairs}
    bars = env.minutes().bars(codes, start, days[-1])
    have = {(c, b.trade_date) for c, bs in bars.items() for b in bs}
    covered = [p for p in pairs if p in have]
    if not covered:
        out.write("오류: 받은 분봉이 없다 — 먼저 `kbj.sh cis30 fetch`\n")
        return 2
    first = min(d for _, d in covered)
    sim_days = [d for d in days if d > first]
    head = (
        f"cis 30분봉 백테스트 — {sim_days[0]} ~ {sim_days[-1]} · "
        f"일봉 신호 {sum(len(v) for v in sig.values()):,}"
        f" · 분봉 있는 (종목, 날) {len(covered):,}/{len(pairs):,}\n"
    )
    if a.sweep:
        rows: list[tuple[str, Summary]] = []
        for name, upd in SWEEP30:
            c2 = c30.model_copy(update=upd)
            res = run_intraday(
                bars, watch_days(sig, days, c2.entry_days), sim_days, c2, calendar=days
            )
            rows.append(
                (name, summarize(res, flat_band_pct=FLAT_BAND, max_positions=c2.max_positions))
            )
        out.write(head + _rules(c30) + " — 표는 한 가지씩만 바꾼다\n\n")
        out.write(sweep_table(rows))
        out.write("\n주의: " + " / ".join(CAVEATS30) + "\n")
        return 0
    res = run_intraday(bars, watch, sim_days, c30, calendar=days)
    s = summarize(res, flat_band_pct=FLAT_BAND, max_positions=c30.max_positions)
    out.write(_report(head, res, s, c30, got.meta, a.trades))
    return 0


def _report(
    head: str, res: Any, s: Summary, c30: Cis30Cfg, names: Mapping[str, Any], last: int
) -> str:
    skipped = " · ".join(f"{k} {v:,}" for k, v in sorted(res.skipped.items())) or "없음"
    lines = [
        head.rstrip("\n"),
        f"30분봉 돌파 신호 {res.signals:,} · 진입 {s.trades:,} · 건너뜀: {skipped}",
        _rules(c30),
        "",
        "결과",
        f"- 총수익 {_p(s.total_return_pct)} · 연 {_p(s.cagr_pct)} · 최대 낙폭 {_p(s.mdd_pct)}",
        f"- 거래 {s.trades:,}회: 이김 {_p(s.win_pct, 0, False)} · 짐 {_p(s.loss_pct, 0, False)} · "
        f"본전 {_p(s.flat_pct, 0, False)}",
        f"- 평균 이익 {_p(s.avg_win_pct)} · 평균 손실 {_p(s.avg_loss_pct)} · "
        f"손익비 {_x(s.payoff)} · "
        f"기대값 {_p(s.expectancy_pct, 2)} · 이익계수 {_x(s.profit_factor)} · 평균 R {_x(s.avg_r)}",
        f"- 최대 연속 손실 {s.max_consec_losses}회 · 평균 보유 "
        f"{'—' if s.avg_days is None else f'{s.avg_days:.1f}'}일",
        "- 청산 까닭: "
        + (" · ".join(f"{k} {v:,}" for k, v in sorted(s.reasons.items())) or "없음"),
        "",
        "주의: " + " / ".join(CAVEATS30),
    ]
    if last > 0 and res.trades:
        lines += ["", f"최근 거래 {min(last, len(res.trades))}개"]
        for t in res.trades[-last:]:
            nm = names.get(t.code)
            lines.append(
                f"  {t.entry_date}→{t.exit_date} {t.code} {(getattr(nm, 'name', None)) or ''} "
                f"{t.entry_px:,.0f}→{t.exit_px:,.0f} {_p(t.ret_pct)} R {t.r_mult:+.2f} "
                f"{REASONS[t.reason]}{' +불타기' if t.adds else ''}"
            )
    return "\n".join(lines) + "\n"


def main(
    argv: Sequence[str] | None = None, *, out: IO[str] | None = None, env: Env | None = None
) -> int:
    o: IO[str] = out if out is not None else sys.stdout
    a = _args(argv)
    e = env or _pg_env()
    try:
        if a.cmd == "probe":
            return _probe(e, a.code, o)
        if a.cmd == "fetch":
            return _fetch(e, a, o)
        return _run(e, a, o)
    except RuntimeError as err:
        o.write(f"오류: {err}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
