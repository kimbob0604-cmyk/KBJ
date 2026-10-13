"""실데이터 검산 CLI.

`python -m kbj.services.engine.verify --date YYYY-MM-DD [--days N] [--record]`

docs/p3_design.md §0.3·§8.7 체크리스트 #20. 키를 받은 뒤 실 DB 원장에 검산 ①②③ 을 돌려 **건수와 잔차
분포만** 낸다(값·종목코드는 출력하지 않는다 — `docs/probe_results.md` 에 옮겨 적을 수 있게).

- ① 4구분 합 = 0, ② 7구분 합 = 기관: `kbj.engines.flows.checks`(종목 원장)와 시장 합계
  (`kbj.engines.flows.totals`). 4구분·7구분이 없으면 **검산 불가**로 세고 사유를 남긴다 — 0 으로
  바꾸지 않는다(메인 결정 R2: 가능한 검산은 0 차이, 불가한 검산은 사유 기록).
- ③ 순자산 변화 = 순유입 + 가격효과: ETF 엔진 `kbj.engines.etf.flows`(묶음 E3)의
  `daily_flow`·`check3`(허용오차 = 0.005원 × (Sₜ + Sₜ₋₁) + 순자산 공표 단위 — 엔진 `c3_tol`,
  단위는 `--net-asset-unit`, 기본 1원 — [실측 필요]).
  엔진이 아직 없으면 ③ 은 '불가(엔진 없음)'로 적는다. 엔진이 예외를 내면 오류로 적고 종료 코드 2.
- `--record`: 실패 행을 `prv_flows.ledger_check` 에 쓴다(기본은 읽기만).

`--program`(P5 F2 — docs/p5_design.md §8.8, probe_results §7 #42): 장중 투자자 1분
(`prv_gex.investor_flow`)의 검산 ①(현물 합 0)·②(기관 = 7세부)·③(마지막 분 대 일별 확정)과 프로그램
시장 표의 검산 ④(전체 = 차익 + 비차익, 합계 = 위탁 + 자기)를 그날 하루 돌려 **건수만** 낸다(조합 id·
시장·거래소 이름만 — 값·종목코드 없음). 계산은 순수 엔진 `kbj.engines.flows.{minute,program}`.

종료 코드: 0 = 할 수 있는 검산이 모두 0 차이, 1 = 실패가 있다, 2 = 오류(DB·엔진).
읽기는 저장소(`kbj.store.repos`)만 쓴다 — 외부 원천을 부르지 않는다.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import IO, Any, Final, Protocol

from kbj.config.markets import MarketsConfig, load_markets
from kbj.core.rows import EtfDay, Investor, LedgerCheck, SplitEvent
from kbj.core.time import utcnow
from kbj.engines.flows.checks import CheckFailure, apply_checks, ledger_checks
from kbj.engines.flows.ledger import build_ledger
from kbj.engines.flows.minute import SPOT_DAILY_MARKET, minute_report
from kbj.engines.flows.program import program_report
from kbj.engines.flows.totals import market_investor_totals
from kbj.store.repos import EtfRepo, FlowsRepo, MarketRepo, ProgramRepo

__all__ = [
    "ETF_ENGINE",
    "ProgramVerifyRepos",
    "VerifyRepos",
    "main",
    "residual_bucket",
    "verify_day",
    "verify_program_day",
]

ETF_ENGINE: Final = "kbj.engines.etf.flows"
SERVICE: Final = "engine-verify"
# 잔차 분포 칸(원) — 값 대신 이 칸의 수만 낸다
_BUCKETS: Final[tuple[tuple[str, float], ...]] = (
    ("le_1won", 1.0),
    ("le_1m", 1_000_000.0),
    ("le_100m", 100_000_000.0),
)


class VerifyRepos(Protocol):
    @property
    def market(self) -> MarketRepo: ...

    @property
    def flows(self) -> FlowsRepo: ...

    @property
    def etf(self) -> EtfRepo: ...


class ProgramVerifyRepos(Protocol):
    """`--program` 이 읽는 저장소(P5 F2)."""

    @property
    def flows(self) -> FlowsRepo: ...

    @property
    def program(self) -> ProgramRepo: ...


def residual_bucket(residual: float) -> str:
    """|잔차|의 칸 이름 — 1원 이하·100만원 이하·1억원 이하·그 위."""
    a = abs(residual)
    for name, top in _BUCKETS:
        if a <= top:
            return name
    return "gt_100m"


def _hist(residuals: Sequence[float]) -> dict[str, int]:
    return dict(Counter(residual_bucket(r) for r in residuals))


def _stock_checks(repos: VerifyRepos, day: date) -> tuple[dict[str, Any], list[CheckFailure]]:
    snaps = repos.market.snapshots(day)
    bars = [b for rows in repos.market.series(None, day, 1).values() for b in rows if b.date == day]
    invs = [r for rows in repos.flows.days(None, day, day).values() for r in rows]
    uni = repos.market.universe(day)
    led, failures = apply_checks(build_ledger(snaps, bars, invs, uni, [day]))
    s = led.checks
    if s is None:  # apply_checks 가 늘 채운다 — 깨졌으면 조용히 넘기지 않는다
        raise RuntimeError("apply_checks 가 검산 집계를 남기지 않았다")
    by_id: dict[str, list[float]] = {"c1": [], "c2": []}
    for f in failures:
        by_id[f.check_id].append(float(f.residual))
    out: dict[str, Any] = {"rows": len(led.rows), "invalid_before": s.skipped_invalid}
    for cid in ("c1", "c2"):
        out[cid] = {
            "checked": s.checked.get(cid, 0),
            "failed": s.failed.get(cid, 0),
            "unavailable": s.unavailable.get(cid, 0),
            "reasons": dict(s.reasons.get(cid, {})),
            "residual_hist": _hist(by_id[cid]),
        }
    return out, failures


def _market_checks(repos: VerifyRepos, day: date) -> dict[str, Any]:
    rows = [r for rs in repos.flows.market_days(day, 1).values() for r in rs if r.date == day]
    t = market_investor_totals(rows, day)
    if t is None:
        return {"status": "no_rows"}

    def one(res: int | None) -> dict[str, Any]:
        if res is None:
            return {"status": "unavailable"}
        return {"status": "ok" if res == 0 else "failed", "bucket": residual_bucket(res)}

    return {
        "markets": len(t.by_market),
        "c1": one(t.check1_residual),
        "c2": one(t.check2_residual),
        "notes": len(t.notes),
    }


def _etf_checks(
    repos: VerifyRepos,
    day: date,
    *,
    net_asset_unit: float,
    load: Callable[[str], Any] = importlib.import_module,
) -> dict[str, Any]:
    try:
        engine = load(ETF_ENGINE)
    except ModuleNotFoundError as e:
        # 엔진 모듈(또는 그 상위 패키지) 자체가 없을 때만 '불가'. 엔진 안의 다른 import 가 깨진
        # 것은 오류다 — '불가'로 삼키면 종료 코드 0 이 된다(CLAUDE.md §1-4 에러를 삼키지 않는다).
        if e.name is None or not (e.name == ETF_ENGINE or ETF_ENGINE.startswith(e.name + ".")):
            raise
        return {"status": "unavailable", "reason": f"{ETF_ENGINE} 없음(묶음 E3 전)"}
    days_by = repos.etf.etf_days(day, 2)
    events: dict[tuple[str, date], SplitEvent] = {
        (ev.code, ev.effective_date): ev for evs in repos.etf.split_events().values() for ev in evs
    }
    status: Counter[str] = Counter()
    residuals: list[float] = []
    failed = checked = 0
    for code, rows in sorted(days_by.items()):
        cur: EtfDay | None = next((r for r in rows if r.date == day), None)
        if cur is None:
            continue
        prev = next((r for r in reversed(rows) if r.date < day), None)
        flow = engine.daily_flow(prev, cur, events.get((code, day)), unit=net_asset_unit)
        status[str(flow.status)] += 1
        ok = engine.check3(flow)  # 허용오차는 엔진(c3_tol)이 흐름에 싣는다 — None = 검산 불가
        if ok is None:
            continue
        checked += 1
        if flow.residual is not None:
            residuals.append(float(flow.residual))
        if not ok:
            failed += 1
    return {
        "status": "ok",
        "flows": dict(status),
        "checked": checked,
        "failed": failed,
        "residual_hist": _hist(residuals),
    }


def verify_day(
    repos: VerifyRepos,
    day: date,
    *,
    now: datetime,
    record: bool = False,
    net_asset_unit: float = 1.0,
    load: Callable[[str], Any] = importlib.import_module,
) -> dict[str, Any]:
    """하루 검산 보고(건수·분포만). record=True 면 실패 행을 ledger_check 에 쓴다."""
    stock, failures = _stock_checks(repos, day)
    report: dict[str, Any] = {
        "date": day.isoformat(),
        "stock": stock,
        "market": _market_checks(repos, day),
    }
    try:
        report["etf"] = _etf_checks(repos, day, net_asset_unit=net_asset_unit, load=load)
    except Exception as e:  # 엔진 오류는 삼키지 않고 보고·종료 코드 2 로
        report["etf"] = {"status": "error", "error": type(e).__name__}
    if record and failures:
        rows: list[LedgerCheck] = ledger_checks(failures, now)
        report["recorded"] = repos.flows.put_checks(rows)
    return report


# ── P5 F2: --program(검산 ①~④ — 건수만) ────────────────────────────────────────────────


def _daily_won(repos: ProgramVerifyRepos, day: date) -> dict[str, dict[Investor, int | None]]:
    """시장 코드 → 그날 일별 확정 순매수 대금(원 — `market_investor_daily`)."""
    out: dict[str, dict[Investor, int | None]] = {}
    for code, rows in repos.flows.market_days(day, 1).items():
        out[code] = {r.investor: r.net_value for r in rows if r.date == day}
    return out


def verify_program_day(
    repos: ProgramVerifyRepos, day: date, markets: MarketsConfig
) -> dict[str, Any]:
    """그날 F2 검산 보고(건수만 — 조합 id·시장·거래소 이름 외의 값·종목코드 없음)."""
    names = {
        (p.market, p.sector): p.id
        for p in markets.flows.minute.pairs
        if p.market is not None and p.session == "day"
    }
    groups: dict[str, list[Any]] = {}
    for m in repos.flows.investor_minutes(day, None):
        pid = names.get((m.market_code, m.sector_code), "unknown")
        groups.setdefault(pid, []).append(m)
    daily = _daily_won(repos, day)
    minute: dict[str, Any] = {}
    for pid in sorted(groups):
        spot = pid in SPOT_DAILY_MARKET
        won = daily.get(SPOT_DAILY_MARKET[pid]) if spot else None
        minute[pid] = minute_report(groups[pid], spot=spot, daily_won=won or None)
    program: dict[str, Any] = {}
    for mkt in ("K", "Q"):
        for venue in markets.kis.venues:
            days = [r for r in repos.program.market_days(mkt, venue, day, 1) if r.trade_date == day]
            mins = repos.program.market_minutes(day, mkt, venue)
            program[f"{mkt}:{venue}"] = {
                "daily": program_report(days),
                "intraday": program_report(mins),
            }
    return {"date": day.isoformat(), "minute": minute, "program": program}


def _program_exit(reports: Sequence[dict[str, Any]]) -> int:
    for r in reports:
        for rep in r["minute"].values():
            for k in ("c1", "c2", "c3"):
                if rep.get(k, {}).get("failed", 0):
                    return 1
        for rep in r["program"].values():
            if rep["daily"]["failed"] or rep["intraday"]["failed"]:
                return 1
    return 0


def _exit_code(reports: Sequence[dict[str, Any]]) -> int:
    code = 0
    for r in reports:
        if r["etf"].get("status") == "error":
            return 2
        failed = r["stock"]["c1"]["failed"] + r["stock"]["c2"]["failed"]
        failed += r["etf"].get("failed", 0)
        failed += sum(1 for k in ("c1", "c2") if r["market"].get(k, {}).get("status") == "failed")
        if failed:
            code = 1
    return code


def _pg_repos() -> VerifyRepos:
    from kbj.config.settings import Settings
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    return pg_repos(lambda: connect(settings, service=SERVICE))


def main(
    argv: Sequence[str] | None = None,
    *,
    repos: VerifyRepos | None = None,
    now: datetime | None = None,
    out: IO[str] | None = None,
    load: Callable[[str], Any] = importlib.import_module,
    markets: MarketsConfig | None = None,
) -> int:
    ap = argparse.ArgumentParser(prog="python -m kbj.services.engine.verify", description=__doc__)
    ap.add_argument("--date", required=True, type=date.fromisoformat, help="기준 거래일")
    ap.add_argument("--days", type=int, default=1, help="기준일부터 거슬러 볼 거래일 수")
    ap.add_argument("--record", action="store_true", help="실패 행을 prv_flows.ledger_check 에")
    ap.add_argument("--net-asset-unit", type=float, default=1.0, help="순자산 공표 단위(원)")
    ap.add_argument(
        "--program",
        action="store_true",
        help="P5 F2 — 장중 투자자 1분 검산 ①②③·프로그램 검산 ④ 건수(probe_results #42)",
    )
    args = ap.parse_args(argv)
    if args.days < 1:
        ap.error("--days 는 1 이상")
    stream = out or sys.stdout
    from kbj.store.db import StoreError

    if args.program:
        return _main_program(args.date, args.days, repos=repos, markets=markets, out=stream)
    try:
        r = repos if repos is not None else _pg_repos()
        # 기준일은 늘 넣는다(KRX 일봉 전인 KIS 마감일도 검산한다)
        days = sorted({*r.market.trading_days(args.date, args.days), args.date})
        reports = [
            verify_day(
                r,
                d,
                now=now or utcnow(),
                record=args.record,
                net_asset_unit=args.net_asset_unit,
                load=load,
            )
            for d in days
        ]
    except StoreError as e:  # 문구는 접속 정보를 가린 것(kbj.store.db)
        print(json.dumps({"error": "store", "message": str(e)}, ensure_ascii=False), file=stream)
        return 2
    print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2), file=stream)
    return _exit_code(reports)


def _main_program(
    day: date,
    n: int,
    *,
    repos: Any,
    markets: MarketsConfig | None,
    out: IO[str],
) -> int:
    from kbj.store.db import StoreError

    cfg = markets or load_markets()
    try:
        r: Any = repos if repos is not None else _pg_repos()  # Repos(Pg) — market·flows·program
        days = sorted({*r.market.trading_days(day, n), day})
        reports = [verify_program_day(r, d, cfg) for d in days]
    except StoreError as e:  # 문구는 접속 정보를 가린 것(kbj.store.db)
        print(json.dumps({"error": "store", "message": str(e)}, ensure_ascii=False), file=out)
        return 2
    print(json.dumps({"program_reports": reports}, ensure_ascii=False, indent=2), file=out)
    return _program_exit(reports)


if __name__ == "__main__":
    raise SystemExit(main())
