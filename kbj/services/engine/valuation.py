"""밸류에이션 작업 — `fin.valuation_band`(평일 08:50, as_of = 전 거래일, `krx.daily` 굳은 의존).

docs/p4_design.md §1.7·§3.2·§4.8·§4.9, D-P4-11·12·25 ④, ADR 0016. 승격 원본: SD
`valuation_calculator.save_valuation_band`:355·`calculate_all_bands`:448(일괄 — 정의는 바꿨다),
`tam_modeler.build_auto_tam`:364(시나리오 — 이름은 '시나리오 적정가').

순수 계산은 `kbj.engines.valuation`, SQL 은 `kbj.store.repos`, 이 모듈은 둘을 잇는 실행만 한다(P3
관례 — `kbj.services.engine.board`). 로그인 작업이다(시세 원장 `prv_market` + 공개 재무 `pub_fin` +
KIS 추정 `prv_fin` — 결과는 `prv_fin`).

순서
- ① 전 거래일(as_of) 원장 스냅(`MarketRepo.snapshot` — krx > kis)이 그날 것이 아니면 `not_ready`
  (재시도).
- ② 유니버스 + P2 고유번호 표로 종류주식 표(`classes.share_classes` — 코드·이름 규칙 둘 다)를 만들어
  `prv_market.share_class` 에 쓴다. 회사 = 보통주 코드 + 우선주들.
- ③ 그날 스냅이 있는 종목(ETF·ETN 제외)마다 계산할 날: 마지막으로 쓴 날 뒤 ~ as_of(as_of 는 언제나
  다시 — 같은 as_of 재실행 멱등). 처음이면 최근 `valuation.band_days` 거래일 창 전부(상장일 이후).
  마지막 행의 재무 지문(`flags.fin_digest`)이 지금 저장소의 PIT 재무와 다르면(백필·정정으로 과거
  재무가 바뀜) 창 전부를 다시 계산한다(`rebuilt`).
- ④ 날마다 스냅을 한 번 읽어(`snapshot(d)` — 그날 행이 없으면 건너뜀) 회사 시총(보통 + 우선) + PIT
  재무(접수일 ≤ d) + NTM(스냅 ≤ d)으로 `ValuationDay` 를 만들고 그날 묶음을 쓴다.
- ⑤ 종목마다 창의 일별 행을 다시 읽어 밴드 네 개(`prv_fin.valuation_band` — 통째로 바꿈)와 시나리오
  세 칸(`prv_fin.scenario`)을 쓴다. **밴드·시나리오는 그날 행이 있는 전 종목**(주요계정 층 포함 —
  상세 층 아님은 EV/EBITDA 만 `not_detail_tier`)이다 — 등록부 노트 '상세 대상만'과 다르다
  (p4_expect.yaml #16 이 밴드·시나리오를 기대 — 메인에 노트 수정 요청).
- ⑥ 우선주의 시나리오는 회사(보통주) 기준 — EPS·BPS·밴드는 회사 것, 가격·업사이드는 **보통주 종가**
  기준이다(화면이 '보통주 기준'으로 적는다 — 묶음 A 에 요청).

- 시나리오 입력(`scenario.inputs_from`): FY1 = 그날 이하 마지막 KIS 추정 스냅(컨센서스 엔진 `ntm` —
  단일 증권사 추정일 수 있어 품질 estimated [확인 필요 #29]), TTM EPS = Σ 분기 기본주당이익(DART).
  주당 값은 그날의 주식 수로(§4.8 — 분할 직후 분할 전 주당 값이 남으면 총액 ÷ 주식 수로 다시 센다).
- 오류: 종목 하나의 계산 오류는 그 종목만 건너뛰고(사유 — 가린 '종류: 문구' — 를 종목별로 모아
  detail 에 종류별 수와 앞 5건) 나머지를 마친 뒤 `failed`(격리 — 삼키지 않는다, 절대 규칙 4. P4
  `kbj.services.engine.filings`·`earnings` 와 같은 방식). 저장소 오류는 그대로 올라가 실행기가
  `failed` 로 기록한다.
- 자원: `repos`(없으면 `connect` 로 `pg_repos`), `fin_config`(없으면 `load_fin`). 시각은 `ctx.now`
  만 쓴다. 알림 없음. 재무 백필 뒤 5년 재계산은 `rebuild()`(평소에는 재무 지문이 알아서 잡는다).

수동 재계산 CLI — KRX 일별 백필(`market.backfill`)로 시세가 더 과거로 늘어난 뒤에 쓴다(재무
지문은 그것을 잡지 못한다):
`python -m kbj.services.engine.valuation rebuild --as-of YYYY-MM-DD [--code C …]`.
출력은 건수만(JSON — 값·종목별 결과는 싣지 않는다. 실패한 종목만 앞 5건의 코드·가린 사유). 종료
코드 0 = ok, 1 = not_ready·failed, 2 = 저장소 오류(문구는 접속 정보를 가린 것).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import IO, Any, Final, Protocol, cast

from kbj.config.fin import FinConfig, load_fin
from kbj.core.fin_rows import BandRow, NaReason, ScenarioRow, ValuationDay
from kbj.core.masking import safe_snippet
from kbj.core.rows import Snap, UniverseRow
from kbj.engines.consensus.universe import consensus_na
from kbj.engines.valuation.band import bands
from kbj.engines.valuation.classes import FUND_KINDS, ShareClasses, share_classes
from kbj.engines.valuation.multiples import (
    Facts,
    company_mktcap,
)
from kbj.engines.valuation.pit import (
    ConsensusBook,
    DayInput,
    PitBook,
    value_day,
)
from kbj.engines.valuation.scenario import inputs_from, scenarios
from kbj.services.scheduler.handlers import JobContext, JobResult, ResultStatus
from kbj.store.repos import ConsensusRepo, FilingsRepo, FinRepo, MarketRepo, pg_repos

__all__ = [
    "JOB",
    "ValuationReport",
    "ValuationRepos",
    "band",
    "main",
    "rebuild",
    "run_valuation",
]

JOB: Final = "fin.valuation_band"
SERVICE: Final = "engine-valuation"
_CODE: Final = re.compile(r"^[0-9A-Z]{6}$")
_UNMATCHED: Final = "보통주 짝을 못 찾은 우선주(코드·이름 규칙 — R13)"
REASON_MAX: Final = 200  # 종목 하나의 실패 사유 길이(가린 뒤 자름)
ERRORS_SHOWN: Final = 5  # detail 에 싣는 실패 사유 수


def _reason(e: BaseException) -> str:
    """'종류: 문구'(공용 마스킹 뒤 자름) — 어느 종목이 왜 실패했는지 남긴다(삼키지 않는다)."""
    return safe_snippet(f"{type(e).__name__}: {' '.join(str(e).split())}", REASON_MAX)


class ValuationRepos(Protocol):
    """이 작업이 쓰는 저장소 — `kbj.store.repos.Repos`(Pg)·`MemoryRepos`(시험) 둘 다 맞는다."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def filings(self) -> FilingsRepo: ...

    @property
    def fin(self) -> FinRepo: ...

    @property
    def consensus(self) -> ConsensusRepo: ...


@dataclass
class ValuationReport:
    as_of: date
    status: ResultStatus = "ok"
    reason: str | None = None
    codes: int = 0
    days_read: int = 0
    days_missing: int = 0
    rows: int = 0
    bands: int = 0
    scenarios: int = 0
    classes: int = 0
    unmatched_pref: int = 0
    rebuilt: int = 0
    errors: dict[str, str] = field(default_factory=dict[str, str])

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "as_of": self.as_of.isoformat(),
            "codes": self.codes,
            "days_read": self.days_read,
            "rows": self.rows,
            "bands": self.bands,
            "scenarios": self.scenarios,
            "share_classes": self.classes,
        }
        if self.reason:
            d["reason"] = self.reason
        if self.days_missing:
            d["days_without_snapshot"] = self.days_missing
        if self.unmatched_pref:
            d["unmatched_pref"] = self.unmatched_pref
        if self.rebuilt:
            d["rebuilt"] = self.rebuilt
        if self.errors:
            kinds: dict[str, int] = {}
            for why in self.errors.values():
                k = why.split(":", 1)[0]
                kinds[k] = kinds.get(k, 0) + 1
            d["n_errors"] = len(self.errors)
            d["error_kinds"] = kinds
            d["errors"] = dict(sorted(self.errors.items())[:ERRORS_SHOWN])
        return d


@dataclass
class _Company:
    """회사(보통주 코드) 하나의 계산 입력 — PIT 재무·NTM·사실."""

    common: str
    codes: tuple[str, ...]
    corp: str | None
    book: PitBook
    facts: Facts
    cons: ConsensusBook


def _stock_universe(
    universe: list[UniverseRow], snaps: Mapping[str, Snap], as_of: date
) -> dict[str, UniverseRow]:
    """유니버스 + 그날 스냅에만 있는 종목(유니버스 결측 — 스냅의 이름·종류로 채운다)."""
    out = {u.code: u for u in universe if (u.kind or "common") not in FUND_KINDS}
    for code, s in snaps.items():
        if code in out or (s.kind or "common") in FUND_KINDS:
            continue
        out[code] = UniverseRow(code, as_of, s.name, s.market, s.kind, None, s.source, s.quality)
    return out


class _Run:
    def __init__(
        self,
        repos: ValuationRepos,
        as_of: date,
        cfg: FinConfig,
        *,
        now: datetime,
        loaded_by: str,
        rep: ValuationReport,
    ) -> None:
        self.repos = repos
        self.as_of = as_of
        self.cfg = cfg
        self.now = now
        self.loaded_by = loaded_by
        self.rep = rep
        self.companies: dict[str, _Company] = {}
        self.window_from = as_of
        self.universe: dict[str, UniverseRow] = {}
        self.classes = ShareClasses((), {})
        self.group_of: dict[str, str] = {}
        self.corp_index: dict[str, tuple[str, str]] = {}
        self.profiles: dict[str, Any] = {}

    # ── 준비 ──

    def company(self, code: str) -> _Company:
        common = self.group_of.get(code, code)
        got = self.companies.get(common)
        if got is not None:
            return got
        codes = self.classes.groups.get(common, (code,))
        idx = self.corp_index.get(common)
        corp = idx[0] if idx else None
        quarters = self.repos.fin.quarters(corp) if corp else []
        counts = self.repos.filings.share_counts(corp) if corp else []
        u = self.universe.get(common)
        prof = self.profiles.get(corp) if corp else None
        is_fin = (
            prof.is_financial(self.cfg.valuation.financial_ksic_prefixes)
            if prof is not None
            else None
        )
        facts = Facts(
            kind=None if u is None else u.kind,
            is_financial=is_fin,
            consensus_na=None if u is None else consensus_na(u),
            listed_on=None if u is None else u.listed_on,
        )
        rows = self.repos.consensus.snapshots(common, self.window_from, self.as_of)
        got = _Company(
            common, tuple(codes), corp, PitBook(quarters, counts), facts, ConsensusBook(rows)
        )
        self.companies[common] = got
        return got

    def plan(self, targets: list[str], window: list[date], rebuild: bool) -> dict[date, list[str]]:
        """날짜 → 그날 계산할 종목."""
        need: dict[date, list[str]] = {}
        for code in targets:
            comp = self.company(code)
            last = self.repos.consensus.last_valuation_day(code)
            redo = rebuild or last is None
            if not redo and last is not None:
                got = self.repos.consensus.valuation_days(code, last, last)
                stored = got[0].flags.get("fin_digest") if got else None
                if stored != comp.book.at(last).digest:
                    redo = True
                    self.rep.rebuilt += 1
            listed = self.universe[code].listed_on if code in self.universe else None
            for d in window:
                if listed is not None and d < listed:
                    continue
                if d == self.as_of or redo or (last is not None and d > last):
                    need.setdefault(d, []).append(code)
        return need

    # ── 계산 ──

    def value(self, code: str, day: date, snaps: Mapping[str, Snap]) -> ValuationDay:
        comp = self.company(code)
        notes: list[str] = []
        if code in self.classes.unmatched:
            cap = None
            notes.append(_UNMATCHED)
        else:
            cap = company_mktcap(snaps, comp.codes, day)
        s = snaps[code]
        cs = snaps.get(comp.common)
        inp = DayInput(day, cap, s.close, None if cs is None else cs.shares)
        return value_day(
            code,
            inp,
            comp.book.at(day),
            comp.facts,
            caps=self.cfg.valuation.caps,
            estimate=comp.cons.ntm(day),
            notes=notes,
        )

    def scenario(
        self, code: str, band_rows: list[BandRow], snaps: Mapping[str, Snap]
    ) -> list[ScenarioRow]:
        comp = self.company(code)
        view = comp.book.at(self.as_of)
        cs = snaps.get(comp.common)
        price = None if cs is None else cs.close
        blocked = NaReason.MISSING_INPUT if code in self.classes.unmatched else None
        inp = inputs_from(
            code,
            self.as_of,
            view,
            comp.cons.ntm(self.as_of),
            band_rows,
            price=price,
            krx_common_shares=None if cs is None else cs.shares,
            blocked=blocked,
            source=(cs.source.lower() if cs is not None else "krx") + "+DART",
        )
        sc = self.cfg.scenario
        return scenarios(inp, bear_eps_mult=sc.bear_eps_mult, bull_eps_mult=sc.bull_eps_mult)


def run_valuation(
    repos: ValuationRepos,
    as_of: date,
    *,
    cfg: FinConfig,
    now: datetime,
    loaded_by: str = JOB,
    codes: Collection[str] | None = None,
    rebuild: bool = False,
) -> ValuationReport:
    """as_of(전 거래일)의 PIT 배수·밴드·시나리오. `codes` 를 주면 그 종목만(회사 시총은 종류주식
    전부로),
    `rebuild` 면 창 전부를 다시 계산한다(`rebuild()` — 백필 뒤)."""
    rep = ValuationReport(as_of)
    snaps_t, used = repos.market.snapshot(as_of)
    if used != as_of:
        rep.status = "not_ready"
        rep.reason = "전 거래일 원장 스냅이 아직 없다(krx.daily 뒤 다시)"
        return rep
    run = _Run(repos, as_of, cfg, now=now, loaded_by=loaded_by, rep=rep)
    run.universe = _stock_universe(repos.market.universe(as_of), snaps_t, as_of)
    run.corp_index = repos.filings.corp_index()
    run.classes = share_classes(run.universe.values(), run.corp_index)
    rep.unmatched_pref = len(run.classes.unmatched)
    rep.classes = repos.consensus.put_share_classes(
        list(run.classes.rows), loaded_by=loaded_by, now=now
    )
    run.group_of = {c: common for common, cs in run.classes.groups.items() for c in cs}
    vcfg = cfg.valuation
    window = sorted(set(repos.market.trading_days(as_of, vcfg.band_days)) | {as_of})
    window = window[-vcfg.band_days :]
    run.window_from = window[0]
    targets = sorted(
        c
        for c in snaps_t
        if c in run.universe
        and (c in run.group_of or c in run.classes.unmatched)
        and (codes is None or c in codes)
    )
    corps = {
        run.corp_index[run.group_of.get(c, c)][0]
        for c in targets
        if run.group_of.get(c, c) in run.corp_index
    }
    run.profiles = dict(repos.filings.profiles(corps)) if corps else {}
    rep.codes = len(targets)
    need = run.plan(targets, window, rebuild)
    for d in sorted(need):
        if d == as_of:
            snaps = snaps_t
        else:
            snaps, got = repos.market.snapshot(d)
            if got != d:
                rep.days_missing += 1
                continue
        rep.days_read += 1
        rows: list[ValuationDay] = []
        for code in need[d]:
            if code not in snaps or code in rep.errors:
                continue
            try:
                rows.append(run.value(code, d, snaps))
            except (ValueError, TypeError, KeyError, ZeroDivisionError) as e:
                rep.errors[code] = _reason(e)  # 그 종목만 — 다른 종목은 계속(격리)
        if rows:
            rep.rows += repos.consensus.put_valuation_days(rows, loaded_by=loaded_by, now=now)
    for code in targets:
        if code in rep.errors:
            continue
        try:
            series = repos.consensus.valuation_days(code, run.window_from, as_of)
            b = bands(
                series,
                code=code,
                as_of=as_of,
                caps=vcfg.caps,
                window_days=vcfg.band_days,
                min_days=vcfg.min_days,
            )
            sc_rows = run.scenario(code, b, snaps_t)
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as e:
            rep.errors[code] = _reason(e)
            continue
        rep.bands += repos.consensus.put_bands(code, as_of, b, loaded_by=loaded_by, now=now)
        rep.scenarios += repos.consensus.put_scenarios(
            code, as_of, sc_rows, loaded_by=loaded_by, now=now
        )
    if rep.errors:
        rep.status = "failed"
    return rep


def rebuild(
    repos: ValuationRepos,
    codes: Collection[str] | None,
    as_of: date,
    *,
    cfg: FinConfig,
    now: datetime,
    loaded_by: str = JOB,
) -> ValuationReport:
    """5년 창을 다시 계산한다(재무 백필 뒤 — 평소에는 재무 지문이 바뀐 종목을 처리기가 알아서 다시
    한다). `codes` None 이면 그날 스냅이 있는 전 종목."""
    return run_valuation(
        repos, as_of, cfg=cfg, now=now, loaded_by=loaded_by, codes=codes, rebuild=True
    )


# ── 작업 처리기(config/jobs.yaml owner) ───────────────────────────────────────────────────


def _repos(ctx: JobContext) -> ValuationRepos:
    """주입된 저장소(`repos` — 시험·시뮬레이션), 없으면 연결 공장(`connect`)으로 Pg 저장소."""
    repos = ctx.resources.get("repos")
    if repos is not None:
        return cast(ValuationRepos, repos)
    return pg_repos(ctx.resource("connect"))


def _cfg(ctx: JobContext) -> FinConfig:
    got = ctx.resources.get("fin_config")
    return got if isinstance(got, FinConfig) else load_fin(settings=ctx.settings)


def band(ctx: JobContext) -> JobResult:
    """`fin.valuation_band`(08:50) — as_of = 전 거래일(`prev_trading_day`)."""
    as_of = date.fromisoformat(ctx.as_of[:10])
    rep = run_valuation(_repos(ctx), as_of, cfg=_cfg(ctx), now=ctx.now, loaded_by=ctx.job)
    if rep.status == "ok":
        return JobResult("ok", collected=ctx.keys, rows=rep.rows, detail=rep.detail())
    return JobResult(rep.status, rows=rep.rows, detail=rep.detail())


# ── 수동 재계산 CLI ───────────────────────────────────────────────────────────────────────


def _pg_repos() -> ValuationRepos:
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
    repos: ValuationRepos | None = None,
    cfg: FinConfig | None = None,
    now: datetime | None = None,
    out: IO[str] | None = None,
) -> int:
    """`rebuild --as-of D [--code C …]` — 5년 창 다시 계산(건수만 출력)."""
    ap = argparse.ArgumentParser(prog="python -m kbj.services.engine.valuation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("rebuild", help="5년 창 다시 계산(KRX 일별 백필 뒤)")
    rb.add_argument("--as-of", required=True, type=date.fromisoformat, help="전 거래일")
    rb.add_argument("--code", action="append", type=_code, help="종목(여럿 가능 — 없으면 전 종목)")
    args = ap.parse_args(argv)
    stream = out or sys.stdout
    from kbj.core.time import now_kst
    from kbj.store.db import StoreError

    try:
        r = repos if repos is not None else _pg_repos()
        rep = rebuild(
            r,
            args.code,
            args.as_of,
            cfg=cfg if cfg is not None else load_fin(),
            now=now or now_kst(),
            loaded_by=f"{JOB}:rebuild",
        )
    except StoreError as e:  # 문구는 접속 정보를 가린 것(kbj.store.db)
        print(json.dumps({"error": "store", "message": str(e)}, ensure_ascii=False), file=stream)
        return 2
    print(json.dumps({"status": rep.status, **rep.detail()}, ensure_ascii=False), file=stream)
    return 0 if rep.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
