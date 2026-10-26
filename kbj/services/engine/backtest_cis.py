"""cis 순추세 백테스트 CLI — `kbj.sh backtest-cis [옵션]`(맥 DB 의 전 종목 일봉으로).

    python -m kbj.services.engine.backtest_cis [--years 3] [--stop 2] [--trail 8] [--vol-mult 3]
        [--lookback 20] [--no-regime] [--no-pyramid] [--capital 10000000] [--trades 20]
        [--json|--csv|--sweep [entry|exit]|--edge]

`--sweep entry` 손절·진입 조합 비교, `--sweep exit` 지금 손절·진입에서 청산 규칙 비교, `--edge` 매매
규칙 없이 신호 뒤 1·5·10·20일 움직임을 '전 종목·모든 날' 기준선과 비교(신호 자체에 우위가 있나).

규칙·기본값은 config/screen.yaml(`cis`·`cis_backtest`), 계산은 엔진(`kbj.engines.screen.backtest` —
스크리너 화면과 같은 판정). 결과는 화면(표준 출력)에만 — 저장하지 않는다. 주문 코드 없음.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import IO, Any, Final

from kbj.config.screen import CisBacktestCfg, CisCfg, load_screen
from kbj.core.rows import IndexBar
from kbj.engines.screen.backtest import (
    REASONS,
    BacktestResult,
    OhlcSeries,
    Summary,
    regime_series,
    run_backtest,
    series_from,
    signals_of,
    summarize,
)
from kbj.engines.screen.cis import StockMeta, eligible
from kbj.engines.screen.edge import HORIZONS, EdgeRow, baseline, edge_row, forward_of

__all__ = [
    "EXIT_SWEEP",
    "ROBUST_SWEEP",
    "SWEEP",
    "Loaded",
    "edge_table",
    "main",
    "report",
    "sweep_table",
]

DAYS_PER_YEAR: Final = 250
BATCH: Final = 300
CIS_RATIO: Final = "cis 본인(알려진 값): 이김 40% · 짐 45% · 본전 15%"
CAVEATS: Final = (
    "상장폐지·이름이 바뀐 종목은 지금 유니버스에 없어 빠진다 — 결과가 좋게 치우칠 수 있다",
    "일봉이라 같은 날 손절가와 새 고가가 다 나오면 손절이 먼저라고 본다(보수적)",
    "관리·정지 이력은 모른다(지금 상태만) · 과거 성과는 앞날을 보장하지 않는다 · 매매 권유 아님",
)


@dataclass(frozen=True)
class Loaded:
    series: Mapping[str, OhlcSeries]
    meta: Mapping[str, StockMeta]
    index_bars: Mapping[str, Sequence[IndexBar]]
    days: Sequence[date]  # 오름차순 거래일(창 전체)


Loader = Callable[[int, date | None, CisCfg], Loaded]


def _load_pg(n: int, end: date | None, cfg: CisCfg) -> Loaded:  # pragma: no cover — 운영(DB)
    from kbj.config.settings import Settings
    from kbj.core.time import KST
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("KBJ_DATABASE_URL 이 필요하다")
    m = pg_repos(lambda: connect(settings, service="backtest_cis")).market
    from datetime import datetime

    last = m.trading_days(end or datetime.now(KST).date(), 1)
    if not last:
        raise RuntimeError("일봉이 없다")
    day = last[0]
    days = sorted(m.trading_days(day, n))
    snaps, _ = m.snapshot(day)
    meta = {c: StockMeta(s.name, s.market, s.kind, s.flagged) for c, s in snaps.items()}
    for u in m.universe(day):
        meta.setdefault(u.code, StockMeta(u.name, u.market, u.kind, None))
    codes = sorted(c for c, x in meta.items() if eligible(c, x) is None)
    series: dict[str, OhlcSeries] = {}
    for i in range(0, len(codes), BATCH):
        for code, bars in m.series(codes[i : i + BATCH], day, n).items():
            s = series_from(code, bars)
            if s is not None:
                series[code] = s
    idx_codes = sorted({c for v in cfg.index.values() for c in v})
    return Loaded(series, meta, m.index_series(idx_codes, day, n), days)


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m kbj.services.engine.backtest_cis")
    ap.add_argument("--years", type=int, default=None, help="기간(년, 기본 config)")
    ap.add_argument("--end", type=date.fromisoformat, default=None, help="끝 날짜(YYYY-MM-DD)")
    ap.add_argument("--vol-mult", type=float, default=None)
    ap.add_argument("--lookback", type=int, default=None)
    ap.add_argument("--stop", type=float, default=None, help="최초 손절 %%")
    ap.add_argument("--trail", type=float, default=None, help="추적 손절 %%")
    ap.add_argument("--gap", type=float, default=None, help="이 %% 넘게 갭 상승하면 건너뜀")
    ap.add_argument("--risk", type=float, default=None, help="한 번 위험 %%")
    ap.add_argument("--max-positions", type=int, default=None)
    ap.add_argument("--capital", type=int, default=None, help="시험 자금(원)")
    ap.add_argument("--no-regime", action="store_true", help="시장 국면(지수 20일선) 거르지 않음")
    ap.add_argument("--no-pyramid", action="store_true", help="불타기 끔")
    ap.add_argument("--no-ma-exit", action="store_true", help="이평 두 번째 이탈 청산 끔")
    ap.add_argument("--no-time-stop", action="store_true", help="시간 손절 끔")
    ap.add_argument("--stop-mode", choices=("pct", "signal_low", "atr"), default=None,
                    help="최초 손절: pct(진입가 −%%)·signal_low(신호일 저가)·"
                         "atr(ATR×배수)")  # fmt: skip
    ap.add_argument("--atr-mult", type=float, default=None)
    ap.add_argument("--vol-max", type=float, default=None, help="거래량 배수 상한(넘으면 뺌)")
    ap.add_argument("--entry", choices=("open", "confirm", "close"), default=None,
                    help="진입: open(다음 날 시가)·confirm(다음 날 신호일 고가 돌파)")  # fmt: skip
    ap.add_argument("--trades", type=int, default=15, help="끝에 보일 최근 거래 수")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--sweep", nargs="?", const="entry", choices=("entry", "exit", "robust"),
                     default=None,
                     help="entry(기본): 손절·진입 조합 · exit: 청산 규칙 · robust: 추적 손절 × "
                          "거래량 배수 상한 격자(시간·이평 끔)")  # fmt: skip
    fmt.add_argument("--edge", action="store_true",
                     help="신호 우위 — 신호 뒤 n일 움직임 vs 전 종목·모든 날")  # fmt: skip
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--csv", action="store_true", help="거래 목록 CSV")
    return ap.parse_args(argv)


def _configs(a: argparse.Namespace) -> tuple[CisCfg, CisBacktestCfg]:
    conf = load_screen()
    cfg, bt = conf.cis, conf.cis_backtest
    plan: dict[str, Any] = {}
    for k, v in (("stop_pct", a.stop), ("trail_pct", a.trail), ("gap_skip_pct", a.gap),
                 ("risk_pct", a.risk), ("max_positions", a.max_positions)):  # fmt: skip
        if v is not None:
            plan[k] = v
    top: dict[str, Any] = {"plan": cfg.plan.model_copy(update=plan)}
    if a.vol_mult is not None:
        top["vol_mult"] = a.vol_mult
    if a.vol_max is not None:
        top["vol_max"] = a.vol_max
    if a.lookback is not None:
        top["lookback_days"] = a.lookback
    cfg = cfg.model_copy(update=top)
    upd: dict[str, Any] = {}
    if a.years is not None:
        upd["years"] = a.years
    if a.capital is not None:
        upd["capital_won"] = a.capital
    if a.no_pyramid:
        upd["add_frac"] = 0.0
    if a.no_ma_exit:
        upd["ma_exit_breaks"] = 0
    if a.no_time_stop:
        upd["time_stop_days"] = 0
    if a.stop_mode is not None:
        upd["stop_mode"] = a.stop_mode
    if a.atr_mult is not None:
        upd["atr_mult"] = a.atr_mult
    if a.entry is not None:
        upd["entry_mode"] = a.entry
    return cfg, bt.model_copy(update=upd)


# 비교 조합(이름, plan 덮기, cis_backtest 덮기, cis 덮기) — 나머지는 지금 설정·옵션 그대로
Row = tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]
SWEEP: Final[tuple[Row, ...]] = (
    ("시가·−2%", {"stop_pct": 2.0}, {"entry_mode": "open", "stop_mode": "pct"}, {}),
    ("시가·−3%", {"stop_pct": 3.0}, {"entry_mode": "open", "stop_mode": "pct"}, {}),
    ("시가·−5%", {"stop_pct": 5.0}, {"entry_mode": "open", "stop_mode": "pct"}, {}),
    ("시가·−7%", {"stop_pct": 7.0}, {"entry_mode": "open", "stop_mode": "pct"}, {}),
    ("시가·신호일 저가", {}, {"entry_mode": "open", "stop_mode": "signal_low"}, {}),
    ("시가·ATR×2", {}, {"entry_mode": "open", "stop_mode": "atr", "atr_mult": 2.0}, {}),
    ("확인·−2%", {"stop_pct": 2.0}, {"entry_mode": "confirm", "stop_mode": "pct"}, {}),
    ("확인·신호일 저가", {}, {"entry_mode": "confirm", "stop_mode": "signal_low"}, {}),
    ("확인·ATR×2", {}, {"entry_mode": "confirm", "stop_mode": "atr", "atr_mult": 2.0}, {}),
    ("종가·신호일 저가", {}, {"entry_mode": "close", "stop_mode": "signal_low"}, {}),
    ("종가·ATR×2", {}, {"entry_mode": "close", "stop_mode": "atr", "atr_mult": 2.0}, {}),
)
OFF: Final = {"time_stop_days": 0}
NO_MA: Final = {"ma_exit_breaks": 0}
# 청산 규칙 비교 — 진입·최초 손절은 지금 설정(config·옵션) 그대로
EXIT_SWEEP: Final[tuple[Row, ...]] = (
    ("지금 설정", {}, {}, {}),
    ("시간 손절 끔", {}, OFF, {}),
    ("이평 청산 끔", {}, NO_MA, {}),
    ("시간·이평 끔", {}, {**OFF, **NO_MA}, {}),
    ("20일선 청산", {}, {"ma_exit_days": 20}, {}),
    ("본전 이동 끔", {}, {"breakeven_at_pct": 1000.0}, {}),
    ("불타기 끔", {}, {"add_frac": 0.0}, {}),
    ("추적 −12%", {"trail_pct": 12.0}, {}, {}),
    ("추적 −15%·시간·이평 끔", {"trail_pct": 15.0}, {**OFF, **NO_MA}, {}),
)
# 견고성 격자 — 한 칸만 튀면 우연, 이웃 칸도 비슷해야 믿는다(시간·이평 청산 끔)
ROBUST_SWEEP: Final[tuple[Row, ...]] = tuple(
    (f"추적 −{t}%·배수 {'상한 없음' if vm is None else f'≤{vm:g}'}", {"trail_pct": float(t)},
     {**OFF, **NO_MA}, {"vol_max": vm})
    for vm in (None, 7.0, 5.0)
    for t in (10, 15, 20, 25)
)  # fmt: skip
SWEEPS: Final = {"entry": SWEEP, "exit": EXIT_SWEEP, "robust": ROBUST_SWEEP}


def sweep_table(rows: Sequence[tuple[str, Summary]]) -> str:
    head = (
        f"{'조합':<16} {'거래':>6} {'이김/짐/본전':>14} {'평균이익':>8} {'평균손실':>8} "
        f"{'손익비':>6} "
        f"{'기대값':>7} {'평균R':>6} {'이익계수':>6} {'총수익':>8} {'연':>7} {'최대낙폭':>8} "
        f"{'보유일':>5}  연도별"
    )
    out = [head]
    for name, s in rows:
        wl = f"{s.win_pct:.0f}/{s.loss_pct:.0f}/{s.flat_pct:.0f}"
        hold = "—" if s.avg_days is None else f"{s.avg_days:.1f}"
        out.append(
            f"{name:<16} {s.trades:>6,} {wl:>14} {_p(s.avg_win_pct):>8} {_p(s.avg_loss_pct):>8} "
            f"{_x(s.payoff):>6} {_p(s.expectancy_pct, 2):>7} {_x(s.avg_r):>6} "
            f"{_x(s.profit_factor):>6} {_p(s.total_return_pct):>8} "
            f"{_p(s.cagr_pct):>7} {_p(s.mdd_pct):>8} {hold:>5}  "
            + " ".join(f"{y % 100}년 {_p(v, 0)}" for y, v in s.by_year.items())
        )
    return "\n".join(out) + "\n"


def _z(v: float, digits: int) -> float:
    """반올림하면 0 인 음수를 0 으로(−0.00 표기 없애기)."""
    return 0.0 if round(v, digits) == 0 else v


def _p(v: float | None, digits: int = 1, signed: bool = True) -> str:
    if v is None:
        return "—"
    v = _z(v, digits)
    return f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"


def _x(v: float | None) -> str:
    return "—" if v is None else f"{_z(v, 2):.2f}"


def report(
    res: BacktestResult, s: Summary, cfg: CisCfg, bt: CisBacktestCfg, *, universe: int,
    names: Mapping[str, StockMeta], last: int,
) -> str:  # fmt: skip
    p, c = cfg.plan, bt.costs
    entered = s.trades
    skipped = " · ".join(f"{k} {v:,}" for k, v in sorted(res.skipped.items())) or "없음"
    lines = [
        f"cis 순추세 백테스트 — {res.start} ~ {res.end} (거래일 {len(res.equity):,})",
        f"대상 {universe:,}종목 · 신호 {res.signals:,} · 진입 {entered:,} · 건너뜀: {skipped}",
        (
            f"규칙: 거래량 ≥ {cfg.lookback_days}일 평균×{cfg.vol_mult:g}"
            f"{'' if cfg.vol_max is None else f'~{cfg.vol_max:g}'} · 거래대금 ≥ "
            f"{cfg.min_turnover_won / 1e8:,.0f}억 · {cfg.lookback_days}일 박스 돌파 · "
            f"손절 −{p.stop_pct:g}% · "
            f"추적 −{p.trail_pct:g}% · 갭 +{p.gap_skip_pct:g}% 건너뜀 · 불타기 +{p.add_at_pct:g}% "
            f"×{bt.add_frac:g} · 위험 {p.risk_pct:g}% · 최대 {p.max_positions}종목 · 비중 "
            f"{bt.max_weight_pct:g}% · 손절 방식 {bt.stop_mode} · 진입 {bt.entry_mode} · "
            f"시간 손절 {f'{bt.time_stop_days}일' if bt.time_stop_days else '끔'} · "
            f"이평 청산 {f'{bt.ma_exit_days}일선' if bt.ma_exit_breaks else '끔'}"
        ),
        f"비용: 수수료 {c.fee_bps:g}bp · 매도 거래세 {c.sell_tax_pct:g}% · "
        f"슬리피지 {c.slippage_bps:g}bp"
        f" · 시험 자금 {bt.capital_won:,}원",
        "",
        "결과",
        f"- 총수익 {_p(s.total_return_pct)} · 연 {_p(s.cagr_pct)} · 최대 낙폭 {_p(s.mdd_pct)}",
        f"- 거래 {s.trades:,}회: 이김 {_p(s.win_pct, 0, False)} · 짐 {_p(s.loss_pct, 0, False)} · "
        f"본전 {_p(s.flat_pct, 0, False)}  ({CIS_RATIO})",
        f"- 평균 이익 {_p(s.avg_win_pct)} · 평균 손실 {_p(s.avg_loss_pct)} · "
        f"손익비 {_x(s.payoff)} · "
        f"거래당 기대값 {_p(s.expectancy_pct, 2)} · 이익계수 {_x(s.profit_factor)} · "
        f"평균 R {_x(s.avg_r)}",
        f"- 최대 연속 손실 {s.max_consec_losses}회 · 평균 보유 "
        f"{'—' if s.avg_days is None else f'{s.avg_days:.1f}'}일 · "
        f"자금 사용률 {s.exposure * 100:.0f}%",
        "- 청산 까닭: "
        + (" · ".join(f"{k} {v:,}" for k, v in sorted(s.reasons.items())) or "없음"),
        "- 연도별: " + (" · ".join(f"{y} {_p(v)}" for y, v in s.by_year.items()) or "없음"),
        "",
        "주의: " + " / ".join(CAVEATS),
    ]
    if last > 0 and res.trades:
        lines += ["", f"최근 거래 {min(last, len(res.trades))}개"]
        for t in res.trades[-last:]:
            nm = names.get(t.code)
            lines.append(
                f"  {t.entry_date}→{t.exit_date} {t.code} {(nm.name if nm else None) or ''} "
                f"{t.entry_px:,.0f}→{t.exit_px:,.0f} {_p(t.ret_pct)} R {_z(t.r_mult, 2):+.2f} "
                f"{REASONS[t.reason]}{' +불타기' if t.adds else ''}"
            )
    return "\n".join(lines) + "\n"


def edge_table(rows: Sequence[EdgeRow]) -> str:
    hs = " ".join(f"{f'{h}일':>7}" for h in HORIZONS)
    far = HORIZONS[-1]
    out = [
        f"{'구분':<22} {'개수':>8} {hs} {f'{far}일 오른비율':>10} {f'{far}일 중앙':>8} "
        f"{'최고(평균)':>9} {'최저(평균)':>9}"
    ]
    for r in rows:
        ms = " ".join(f"{_p(m, 2):>7}" for m in r.mean)
        out.append(
            f"{r.label:<22} {r.n:>8,} {ms} {_p(r.up_pct[-1], 0, False):>10} "
            f"{_p(r.median_last, 2):>8} {_p(r.mfe):>9} {_p(r.mae):>9}"
        )
    return "\n".join(out) + "\n"


def _edge(got: Loaded, cfg: CisCfg, bt: CisBacktestCfg, *, start: date, no_regime: bool) -> str:
    """신호 우위 표 — 신호는 시장 국면을 거르지 않고 내서 강세·약세로 나눈다."""
    regimes = {} if no_regime else regime_series(got.index_bars, cfg)

    def on(code: str, d: date) -> bool:
        m = got.meta.get(code)
        return regimes.get(str(m.market) if m else "", {}).get(d) is not False

    free = cfg.model_copy(update={"vol_max": None})  # 배수 구간을 다 보려고 상한은 뺀다
    sig = signals_of(got.series, got.meta, {}, free, start=start, atr_days=bt.atr_days)
    on_sig = {d: [x for x in v if on(x.code, d)] for d, v in sig.items()}
    fwd = forward_of(got.series, sig)
    fwd_on = [f for f in fwd if on(f.code, f.date)]
    vm = cfg.vol_mult
    codes = sorted(c for c, m in got.meta.items() if eligible(c, m) is None)
    rows = [
        baseline(got.series, codes, start=start),
        edge_row("신호 전체(시장 무시)", fwd),
        edge_row("시장 강세 날", fwd_on),
        edge_row("시장 약세 날", fwd, lambda f: not on(f.code, f.date)),
        edge_row(f"강세·거래량 {vm:g}~5배", fwd_on, lambda f: f.vol_ratio < 5),
        edge_row("강세·거래량 5~10배", fwd_on, lambda f: 5 <= f.vol_ratio < 10),
        edge_row("강세·거래량 10배 이상", fwd_on, lambda f: f.vol_ratio >= 10),
        edge_row("강세·확인 진입", forward_of(got.series, on_sig, entry="confirm")),
        edge_row("강세·신호일 종가 진입", forward_of(got.series, on_sig, entry="close")),
        edge_row(f"강세·{vm:g}~5배·종가 진입", forward_of(got.series, on_sig, entry="close"),
                 lambda f: f.vol_ratio < 5),
    ]  # fmt: skip
    c = bt.costs
    trip = 2 * (c.fee_bps + c.slippage_bps) / 100 + c.sell_tax_pct
    return (
        f"cis 신호 우위 — {start} ~ {max(got.days)} · 대상 {len(got.series):,}종목\n"
        "진입 = 신호 다음 날 시가(확인 = 신호일 고가 돌파 값, 종가 = 신호일 종가) → n일째 종가 · "
        f"비용 빼기 전(왕복 약 {trip:.2f}%) · 최고·최저 = {HORIZONS[-1]}일 안 고가·저가\n"
        "읽는 법: 신호 줄이 기준선보다 뚜렷이 높아야 신호에 우위가 있다 — 비슷하면 손절·청산을 "
        "바꿔도 소용없다\n"
        + ("시장 국면 끔(--no-regime) — 강세·약세를 나누지 않는다\n" if no_regime else "")
        + "\n"
        + edge_table(rows)
        + "\n주의: "
        + " / ".join(CAVEATS)
        + "\n"
    )


def main(
    argv: Sequence[str] | None = None, *, out: IO[str] | None = None, load: Loader | None = None
) -> int:
    o: IO[str] = out if out is not None else sys.stdout
    a = _args(argv)
    cfg, bt = _configs(a)
    n = bt.years * DAYS_PER_YEAR + cfg.lookback_days + bt.ma_exit_days + 5
    try:
        got = (load or _load_pg)(n, a.end, cfg)
    except RuntimeError as e:
        o.write(f"오류: {e}\n")
        return 2
    days = list(got.days)
    if len(days) <= cfg.lookback_days + 1:
        o.write(f"오류: 일봉 거래일이 {len(days)}일뿐이다(최소 {cfg.lookback_days + 2})\n")
        return 2
    start = days[max(cfg.lookback_days + 1, len(days) - bt.years * DAYS_PER_YEAR)]
    regimes = {} if a.no_regime else regime_series(got.index_bars, cfg)
    sig = signals_of(got.series, got.meta, regimes, cfg, start=start, atr_days=bt.atr_days)
    sim_days = [d for d in days if d >= start]
    if a.edge:
        o.write(_edge(got, cfg, bt, start=start, no_regime=a.no_regime))
        return 0
    if a.sweep:
        # 배수 상한은 신호를 거르기만 하므로 상한 없이 한 번 내고 줄마다 거른다
        free = cfg.model_copy(update={"vol_max": None})
        sig_all = signals_of(got.series, got.meta, regimes, free, start=start,
                             atr_days=bt.atr_days)  # fmt: skip
        rows: list[tuple[str, Summary]] = []
        for name, plan_upd, bt_upd, cis_upd in SWEEPS[a.sweep]:
            c2 = cfg.model_copy(update={**cis_upd, "plan": cfg.plan.model_copy(update=plan_upd)})
            b2 = bt.model_copy(update=bt_upd)
            vmax = c2.vol_max
            s2 = (
                sig_all
                if vmax is None
                else {d: [x for x in v if x.vol_ratio <= vmax] for d, v in sig_all.items()}
            )
            r2 = run_backtest(got.series, s2, sim_days, c2, b2)
            rows.append((name, summarize(r2, flat_band_pct=b2.flat_band_pct,
                                         max_positions=c2.plan.max_positions)))  # fmt: skip
        o.write(
            f"cis 순추세 백테스트 비교 — {sim_days[0]} ~ {sim_days[-1]} · "
            f"대상 {len(got.series):,}종목"
            f" · 신호 {sum(len(v) for v in sig.values()):,}\n"
            + {
                "entry": "시가 = 다음 날 시가 · 확인 = 다음 날 신호일 고가를 넘을 때 · "
                "종가 = 신호일 종가(동시호가) 진입 · 나머지 규칙은 같다\n",
                "exit": f"진입 {bt.entry_mode} · 최초 손절 {bt.stop_mode} 고정 — "
                "청산 규칙만 바꾼다\n",
                "robust": f"진입 {bt.entry_mode} · 최초 손절 {bt.stop_mode} · 시간·이평 청산 끔 — "
                "추적 손절 × 거래량 배수 상한 격자(이웃 칸도 비슷해야 믿는다)\n",
            }[a.sweep]
            + "평균R = 거래당 손익 ÷ 처음 건 위험(돈 기준) · "
            "기대값 = 거래당 수익률 평균(비중 무시)\n\n"
        )
        o.write(sweep_table(rows))
        o.write("\n주의: " + " / ".join(CAVEATS) + "\n")
        return 0
    res = run_backtest(got.series, sig, sim_days, cfg, bt)
    s = summarize(res, flat_band_pct=bt.flat_band_pct, max_positions=cfg.plan.max_positions)
    if a.json:
        body = {"summary": asdict(s), "skipped": dict(res.skipped), "signals": res.signals,
                "start": str(res.start), "end": str(res.end)}  # fmt: skip
        o.write(json.dumps(body, ensure_ascii=False, default=str) + "\n")
    elif a.csv:
        w = csv.writer(o)
        w.writerow(["entry_date", "exit_date", "code", "entry_px", "exit_px", "qty", "adds",
                    "pnl", "ret_pct", "r_mult", "days", "reason"])  # fmt: skip
        for t in res.trades:
            w.writerow([t.entry_date, t.exit_date, t.code, round(t.entry_px), round(t.exit_px),
                        t.qty, t.adds, round(t.pnl), round(t.ret_pct, 3), round(t.r_mult, 3),
                        t.days, t.reason])  # fmt: skip
    else:
        o.write(report(res, s, cfg, bt, universe=len(got.series), names=got.meta, last=a.trades))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
