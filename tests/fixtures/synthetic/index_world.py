"""합성 지수 정기변경 세계 `index_world` — 묶음 IDX 시험·데모(고정 시드, 실자료 아님).

근거: docs/p5_index_rebalance_prompt.md §4(골든 — 합성 시장 50~300종목·합성 구성종목·3개 리뷰).
MSCI·KRX·KIS 실데이터와 구성종목 목록은 쓰지 않는다(CLAUDE.md 공개 레포 규칙) — 이 생성기가
`random.Random(f"{seed}:{이름}")` 으로 결정적으로 만든다(시드는 p5_world 와 같은 20261113).

쓰는 법
    from tests.fixtures.synthetic.index_world import build_index_world, load_into
    w = build_index_world()          # 240종목(코드 993001~993240 — 다른 합성 대역과 겹치지 않는다)
    w.stocks_for("msci_korea")       # 엔진 입력(StockIn — 기준일 BASE)
    load_into(memory_repos(), w)     # 서비스·API 시험용 저장소(스냅·유니버스·일봉·DART
    지분·구성종목)

무엇이 있나
- 기준일 `BASE` = 2026-10-30(금) — 세 리뷰(MSCI 2026-11·KOSPI200 2026-12·KOSDAQ150 2026-12)의
  기준일.
  지난 리뷰 백테스트용으로 `PAST_BASE` = 2026-04-30 스냅(시총 0.8~1.2배)과 MSCI 2026-05 전후
  구성종목.
- 시총 로그정규(약 0.1조~30조), 유동비율 0.08~0.95, 일평균 거래대금 = 시총 × 0.1~2%.
- 종류 우선주 5·거래정지 2·관리 2, 최근 상장 6(그중 시총 큰 것 하나 — 특례), FOL 한도 종목
  1(993007).
- 구성종목: MSCI = 보통주 시총 상위 45 를 흔들어(상위에서 3 빼고 46~70위에서 3 넣음), KOSPI200(정원
  60)·
  KOSDAQ150(정원 30)도 같은 방식. MSCI 구성종목 FIF(직전 값)는 추정 유동비율 근처(완충 시험).
- DART 지분: 발행주식·자기주식·전략적 보유자(합성지주) 한 명 — 추정 유동비율이 위 값과 같게. 일부는
  국민연금 보유도 있다(재무적 투자자 — 빼지 않는다).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Final

import yaml

from kbj.config.index_reviews import IndexReviewsConfig, parse_index_reviews
from kbj.core.fin_rows import CorpProfile, MajorHolderReport, ShareCount
from kbj.core.index_rows import ConstituentRow
from kbj.core.quality import Quality
from kbj.core.rows import Bar, Snap, UniverseRow
from kbj.core.time import KST
from kbj.engines.index_review.types import StockIn
from kbj.store.repos import MemoryRepos

__all__ = [
    "BASE",
    "CLOCK",
    "PAST_BASE",
    "SEED",
    "IndexWorld",
    "SynStock",
    "build_index_world",
    "golden_doc",
    "load_into",
    "synth_config",
]

SEED: Final = 20261113
N: Final = 240
BASE: Final = date(2026, 10, 30)
PAST_BASE: Final = date(2026, 4, 30)
CLOCK: Final = datetime(2026, 10, 30, 17, 30, tzinfo=KST)  # index.predict 뒤
CONFIG_PATH: Final = Path(__file__).with_name("index_reviews_synth.yaml")
GOLDEN_PATH: Final = Path(__file__).resolve().parents[2] / "golden" / "index_review" / "expect.json"
SECTORS: Final = ("10", "20", "21", "26", "27", "28", "30", "35", "58", "64")
ADV_DAYS: Final = 20
MSCI_N: Final = 45
KRX_N: Final = {"kospi200": ("KOSPI", 60), "kosdaq150": ("KOSDAQ", 30)}


@dataclass(frozen=True)
class SynStock:
    code: str
    corp_code: str
    name: str
    market: str
    kind: str
    sector: str
    listed_on: date
    mktcap: int
    past_mktcap: int
    shares: int
    fif_raw: float
    adv: int
    flags: tuple[str, ...]
    nps: bool  # 국민연금 5% 보유(재무적 — 유동으로 본다)

    @property
    def locked(self) -> int:
        """비유동 주식(자기주식 + 합성지주) — DART 합성 공시가 이 값을 낸다."""
        return round(self.shares * (1 - self.fif_raw))

    @property
    def fif_est(self) -> float:
        """서비스가 DART 공시로 추정하는 유동비율과 같은 값(1 − 비유동 ÷ 발행주식)."""
        return (self.shares - self.locked) / self.shares


@dataclass
class IndexWorld:
    seed: int
    stocks: list[SynStock]
    members: dict[str, set[str]]  # 지수 → 기준일 구성종목
    past_members: dict[str, tuple[set[str], set[str]]]  # MSCI 2026-05: (전, 후)
    fif_prev: dict[str, float]  # MSCI 구성종목의 직전 FIF
    config: IndexReviewsConfig
    _by_code: dict[str, SynStock] = field(default_factory=dict[str, SynStock])

    def __post_init__(self) -> None:
        self._by_code = {s.code: s for s in self.stocks}

    def stock(self, code: str) -> SynStock:
        return self._by_code[code]

    def stocks_for(self, index_id: str, *, past: bool = False) -> list[StockIn]:
        """엔진 입력 — 서비스 `gather` 와 같은 값(시세 전부·유동비율 추정·직전 FIF·업종)."""
        idx = self.config.index(index_id)
        members = self.past_members[index_id][0] if past else self.members[index_id]
        out: list[StockIn] = []
        for s in self.stocks:
            if s.market not in idx.markets and s.code not in members:
                continue
            out.append(StockIn(
                code=s.code, name=s.name, market=s.market, kind=s.kind,
                sector=self.config.sectors.sector_of(s.sector + "100"), listed_on=s.listed_on,
                mktcap=s.past_mktcap if past else s.mktcap, adv_won=float(s.adv),
                fif_raw=s.fif_est, fif_prev=self.fif_prev.get(s.code) if index_id == "msci_korea"
                and s.code in members else None,
                fif_source="DART", flags=s.flags, member=s.code in members,
            ))  # fmt: skip
        return out


def synth_config() -> IndexReviewsConfig:
    return parse_index_reviews(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


def _rng(seed: int, name: str) -> random.Random:
    return random.Random(f"{seed}:{name}")  # noqa: S311 — 합성 데이터(보안 무관)


def _shake(ranked: list[str], n: int, rng: random.Random) -> set[str]:
    """시총 순위 상위 n 에서 3 빼고 n+1~n+25 위에서 3 넣는다(편입·편출 후보가 생기게)."""
    top = ranked[:n]
    out_ = set(rng.sample(top[n // 2 :], 3))
    in_ = set(rng.sample(ranked[n : n + 25], 3))
    return (set(top) - out_) | in_


@cache
def build_index_world(seed: int = SEED) -> IndexWorld:
    rng = _rng(seed, "stocks")
    stocks: list[SynStock] = []
    recent = set(rng.sample(range(N), 6))
    pref = set(rng.sample(sorted(set(range(N)) - recent), 5))
    rest = sorted(set(range(N)) - recent - pref)
    halted = set(rng.sample(rest, 2))
    managed = set(rng.sample(sorted(set(rest) - halted), 2))
    for i in range(N):
        code = f"993{i + 1:03d}"
        market = "KOSDAQ" if i % 3 == 2 else "KOSPI"
        mktcap = int(math.exp(rng.gauss(27.6 if market == "KOSPI" else 26.9, 1.25)))
        price = rng.choice((5_000, 12_000, 30_000, 75_000, 150_000))
        shares = max(1_000_000, mktcap // price)
        mktcap = shares * price  # 시총 = 주식 수 × 종가(원 정수)
        listed = (
            (BASE - timedelta(days=rng.randint(30, 120)))
            if i in recent
            else date(2005 + rng.randint(0, 15), rng.randint(1, 12), 1)
        )
        flags: tuple[str, ...] = ()
        if i in halted:
            flags = ("halted",)
        elif i in managed:
            flags = ("managed",)
        stocks.append(SynStock(
            code=code, corp_code=f"9930{i + 1:04d}", name=f"합성{i + 1:03d}", market=market,
            kind="pref" if i in pref else "common", sector=rng.choice(SECTORS),
            listed_on=listed, mktcap=mktcap,
            past_mktcap=int(mktcap * rng.uniform(0.8, 1.2)) // price * price,
            shares=shares, fif_raw=round(rng.uniform(0.08, 0.95), 4),
            adv=int(mktcap * rng.uniform(0.001, 0.02)), flags=flags, nps=rng.random() < 0.15,
        ))  # fmt: skip
    # 최근 상장 가운데 하나는 시총을 크게(신규 상장 특례)
    big = sorted(recent)[0]
    big_shares = 25_000_000_000_000 // 150_000
    stocks[big] = dataclasses.replace(
        stocks[big],
        market="KOSPI",
        shares=big_shares,
        mktcap=big_shares * 150_000,
        past_mktcap=big_shares * 150_000,
    )
    common = [s for s in stocks if s.kind == "common" and not s.flags]
    by_cap = [s.code for s in sorted(common, key=lambda s: (-s.mktcap, s.code))]
    members: dict[str, set[str]] = {}
    mrng = _rng(seed, "members")
    old_ranked = [s.code for s in sorted(
        (s for s in common if s.code != stocks[big].code), key=lambda s: (-s.mktcap, s.code)
    )]  # fmt: skip
    members["msci_korea"] = _shake(old_ranked, MSCI_N, mrng)
    for idx, (mkt, n) in KRX_N.items():
        ranked = [c for c in old_ranked if next(s for s in stocks if s.code == c).market == mkt]
        members[idx] = _shake(ranked, n, mrng)
    # 거래정지 종목 하나는 MSCI 구성종목(대상 밖 구성종목 → 편출 후보)
    members["msci_korea"].add(stocks[sorted(halted)[0]].code)
    frng = _rng(seed, "fif_prev")
    fif_prev = {
        c: round(min(1.0, max(0.05, next(s for s in stocks if s.code == c).fif_raw
                              + frng.uniform(-0.03, 0.03))) * 40) / 40
        for c in sorted(members["msci_korea"])
    }  # fmt: skip
    past_before = _shake(by_cap, MSCI_N, _rng(seed, "past"))
    past_after = set(members["msci_korea"])
    world = IndexWorld(
        seed=seed, stocks=stocks, members=members,
        past_members={"msci_korea": (past_before, past_after)}, fif_prev=fif_prev,
        config=synth_config(),
    )  # fmt: skip
    return world


# ── 저장소에 싣기(서비스·API 시험) ────────────────────────────────────────────────────────


def _weekdays_back(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def load_into(repos: MemoryRepos, w: IndexWorld, *, members: bool = True) -> None:
    now = CLOCK
    for day, past in ((PAST_BASE, True), (BASE, False)):
        snaps = [
            Snap(
                code=s.code, date=day, name=s.name, market=s.market, kind=s.kind,
                close=float((s.past_mktcap if past else s.mktcap) // s.shares), chg_pct=0.0,
                volume=s.adv // 10_000, turnover=s.adv, turnover_is_estimate=False,
                mktcap=s.past_mktcap if past else s.mktcap, shares=s.shares,
                status_flags=s.flags, source="KRX", venue="KRX", quality=Quality.OK,
            )
            for s in w.stocks
        ]  # fmt: skip
        repos.market.upsert_snapshots(snaps, loaded_by="synthetic", received_at=now)
        repos.market.upsert_universe(
            [UniverseRow(code=s.code, as_of=day, name=s.name, market=s.market, kind=s.kind,
                         listed_on=s.listed_on, source="KRX", quality=Quality.OK)
             for s in w.stocks],
            loaded_by="synthetic", received_at=now,
        )  # fmt: skip
        bars = [
            Bar(code=s.code, date=d, open=None, high=None, low=None, close=None, volume=None,
                turnover=s.adv, source="KRX", venue="KRX", quality=Quality.OK)
            for s in w.stocks for d in _weekdays_back(day, ADV_DAYS)
        ]  # fmt: skip
        repos.market.upsert_daily_bars(bars, loaded_by="synthetic", received_at=now)
    f = repos.filings
    for s in w.stocks:
        f.corp_codes[s.code] = (s.corp_code, s.name)
    f.upsert_profiles(
        [CorpProfile(corp_code=s.corp_code, stock_code=s.code, acc_mt=12,
                     induty_code=s.sector + "100", corp_cls="Y", est_dt=None, corp_name=s.name)
         for s in w.stocks],
        loaded_by="synthetic", now=now,
    )  # fmt: skip
    counts: list[ShareCount] = []
    majors: list[MajorHolderReport] = []
    for i, s in enumerate(w.stocks):
        locked = s.locked
        treasury = locked // 5
        for end, rcept in (
            (date(2025, 12, 31), date(2026, 3, 16)),
            (date(2026, 6, 30), date(2026, 8, 14)),
        ):  # 백테스트 기준일(4-30)에도 PIT 값
            counts.append(ShareCount(
                corp_code=s.corp_code, period_end=end, rcept_no=f"{rcept:%Y%m%d}00{i:04d}",
                common_issued=s.shares, pref_issued=0, treasury_common=treasury, treasury_pref=0,
                rcept_dt=rcept,
            ))  # fmt: skip
        majors.append(MajorHolderReport(
            rcept_no=f"2026010500{i:04d}", corp_code=s.corp_code, rcept_dt=date(2026, 1, 5),
            reporter=f"합성지주{i + 1:03d}", report_type=None, shares=locked - treasury,
            shares_delta=None, ratio=None, ratio_delta=None,
        ))  # fmt: skip
        if s.nps:
            majors.append(MajorHolderReport(
                rcept_no=f"2026020200{i:04d}", corp_code=s.corp_code, rcept_dt=date(2026, 2, 2),
                reporter="국민연금공단", report_type=None, shares=s.shares // 20,
                shares_delta=None, ratio=None, ratio_delta=None,
            ))  # fmt: skip
    f.put_share_counts(counts, loaded_by="synthetic", now=now)
    f.put_major_holder_reports(majors, loaded_by="synthetic", now=now)
    if not members:
        return
    rows: list[ConstituentRow] = []
    for idx, codes in w.members.items():
        start = date(2026, 6, 1) if idx == "msci_korea" else date(2025, 12, 12)
        for c in sorted(codes):
            rows.append(ConstituentRow(
                index_id=idx, code=c, valid_from=start, valid_to=None, name=w.stock(c).name,
                fif=w.fif_prev.get(c) if idx == "msci_korea" else None, source="synthetic",
            ))  # fmt: skip
    before, _ = w.past_members["msci_korea"]
    for c in sorted(before):  # 2026-05 리뷰 전(2025-12-01 ~ 2026-05-31)
        rows.append(ConstituentRow(
            index_id="msci_korea", code=c, valid_from=date(2025, 12, 1),
            valid_to=date(2026, 5, 31), name=w.stock(c).name, fif=None, source="synthetic",
        ))  # fmt: skip
    repos.index_review.put_constituents(rows, loaded_by="synthetic", received_at=now)


# ── 골든 ─────────────────────────────────────────────────────────────────────────────────


def golden_doc(w: IndexWorld | None = None) -> dict[str, Any]:
    """세 리뷰의 엔진 결과(후보 아닌 비구성종목은 뺀다) — tests/golden/index_review/expect.json."""
    from kbj.engines.index_review.krx import predict_krx
    from kbj.engines.index_review.msci import predict_msci

    w = w or build_index_world()
    cfg = w.config
    out: dict[str, Any] = {"seed": w.seed, "base": BASE.isoformat(), "reviews": {}}
    for idx_id in ("msci_korea", "kospi200", "kosdaq150"):
        idx = cfg.index(idx_id)
        review = cfg.current_review(idx_id, BASE)
        assert review is not None
        stocks = w.stocks_for(idx_id)
        if idx.family == "msci":
            r = cfg.msci_rules(idx_id)
            res = predict_msci(stocks, r, cfg.fif_rule_for(review.announce), cfg.fol,
                               aum_won=int(r.val("aum_won")))  # fmt: skip
        else:
            k = cfg.krx_rules(idx_id)
            res = predict_krx(stocks, k, idx.markets, BASE, aum_won=int(k.val("non_etf_aum_won")))
        out["reviews"][f"{idx_id}:{review.id}"] = {
            "status": res.status,
            "cutoff": None if res.cutoff is None else res.cutoff.won,
            "n_members": res.n_members,
            "rows": [
                {"code": o.code, "outcome": o.outcome, "member": o.member, "fif": o.fif,
                 "gap_mcap": o.gap_mcap, "gap_ff": o.gap_ff, "required_rise": o.required_rise,
                 "passive_won": o.passive_won}
                for o in res.stocks if o.outcome != "none"
            ],
        }  # fmt: skip
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m tests.fixtures.synthetic.index_world")
    ap.add_argument("--write-golden", action="store_true")
    args = ap.parse_args()
    doc = golden_doc()
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.write_golden:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(text, encoding="utf-8")
        print(f"썼다: {GOLDEN_PATH}")
    else:
        print(text[:2000])


if __name__ == "__main__":
    main()
