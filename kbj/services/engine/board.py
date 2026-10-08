"""신고가 보드 작업 — `board.daily`(16:20, KIS 마감 → 잠정 보드)·`board.confirm`(08:40, KRX
확정으로 전 거래일 보드를 다시 계산해 덮는다). docs/p3_design.md §1.3·§3.2·§3.5·§4.1, D-P3-5·8·11.

순수 계산은 `kbj.engines.board`(I/O 없음), SQL 은 `kbj.store.repos`, 이 모듈은 둘을 잇는 실행만
한다.

순서(ET `board/ingest/pipeline.py:sync_px`:306 → `board/engine/build.py:run`:425 와 같다)
1. 저장소에서 읽는다: 일봉 창(`config/board.yaml service.series_days` 거래일), 그날 이하 가장 최근
   스냅, 유니버스(상장일), 역사적 최고가 스칼라, 전일 라벨·전일 산출(newhigh·rankings), 공시 대조.
2. 스칼라를 그날까지 굴린다(`roll_alltime` — 같은 날을 두 번 반영하지 않는다). 새 스칼라의
   `history_from` = 시장 일봉을 받아 둔 첫날(R8·D-P3-11 — 상장일이 그보다 앞이면 역사적 신고가를
   계산하지 않고 사유를 남긴다. '상장 이후 전체'가 되도록 KRX 백필이 상장일까지 채운다 —
   `python -m kbj.services.scheduler backfill market.backfill --to-listing`, ADR 0017).
3. 시장 거래일 달력(KRX — `kbj.core.calendar`)으로 신고가 거래일 창의 경계를 정해(ADR 0017)
   `compute_day` → `BoardDay`.
4. `BoardDayRecord`(라벨·종목 하루·산출 JSON 5개)로 옮겨 `BoardRepo.put_day`(그날을 통째로 바꾼다 —
   같은 as_of 재실행 멱등). 금액은 **원**(엔진 안의 억원 × 1e8 — 계약: kbj.core.rows.StockDay).

- ETF·ETN 은 보드에 넣지 않는다(ET 의 펀드 제외 — 스냅의 `kind`). 대조 불일치로 invalid 가 된
  스냅은 빼고 수를 안내에 남긴다(지어낸 종가로 신고가를 내지 않는다).
- `board.confirm`: 스냅이 KRX 확정(비율 0.9 — ET `CLOSE_CONFIRM_MIN`)이 아니면 쓰지 않고
  `not_ready`(재시도). 확정이면 그날 스칼라를 확정 일봉으로 다시 쓰고(`restate_last_day`) 보드를
  다시 계산해 덮는다. 바뀐 라벨 목록은 newhigh 산출의 `confirm` 과 실행 detail 에 남긴다.
- 다음 거래일 몫까지 이미 굴린 스칼라가 있으면(늦은 확정·옛 날짜 수동 재실행) 그날의 직전일까지
  최고가를 복원할 수 없으므로 보드를 덮지 않고 `failed`(사유 기록)로 끝낸다.
- 오류는 삼키지 않는다 — 저장소 예외는 그대로 올라가 실행기가 `failed` 로 기록한다(절대 규칙 4).
- 시각은 `ctx.now`(주입한 시계)만 쓴다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Final, Literal, Protocol, cast

from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import (
    AllTime,
    Bar,
    BoardArtifact,
    BoardDayRecord,
    Label,
    Snap,
    StockDay,
)
from kbj.core.time import KST
from kbj.engines.board import newhigh as nh
from kbj.engines.board.build import (
    ENGINE_VERSION,
    BoardDay,
    BoardInputs,
    BoardLabel,
    close_provenance,
    compute_day,
    prev_near_from,
    prev_ranks_from,
    snap_row,
)
from kbj.engines.board.config import BoardConfig, load_sector_map, load_taxonomy, load_themes
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import BoardRepo, MarketRepo, pg_repos

__all__ = [
    "BoardKnowledge",
    "BoardRepos",
    "BoardRunReport",
    "board_market_days",
    "build_record",
    "confirm",
    "daily",
    "label_changes",
    "market_history_from",
    "newhigh_definition",
    "rebuild_alltime",
    "run_board",
]


class BoardRepos(Protocol):
    """이 작업이 쓰는 저장소 둘 — `kbj.store.repos.Repos`(Pg)·`MemoryRepos`(시험) 둘 다 맞는다."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def board(self) -> BoardRepo: ...


FUND_KINDS: Final = frozenset({"etf", "etn"})
CLOSE_TIME: Final = time(15, 30)  # 보드 데이터의 기준 시각(정규장 마감 — as_of)
_STOCK_DAY_COLS: Final = frozenset(
    {
        "code", "name", "close", "chg_pct", "turnover", "turnover_is_estimate", "mktcap",
        "label", "near_kind", "near_gap", "status", "suspect", "sector", "theme", "ret_5d",
        "ret_21d", "vol_mult",
    }
)  # fmt: skip
NEXT_DAY_CONFIRM: Final = (
    "KRX 정규장 확정 시세는 다음 영업일 08:05 에 공표됩니다 — 08:40 board.confirm 이 다시 "
    "계산합니다"
)


@dataclass(frozen=True)
class BoardKnowledge:
    """자체 사전(config/knowledge) — 시험은 직접 넣는다."""

    themes: Mapping[str, Any]
    taxonomy: Mapping[str, Any]
    sectors: Mapping[str, str]
    sector_taxonomy: str

    @classmethod
    def load(cls) -> BoardKnowledge:
        smap, tax = load_sector_map()
        return cls(
            themes=load_themes(), taxonomy=load_taxonomy(), sectors=smap, sector_taxonomy=tax
        )


@dataclass(frozen=True)
class BoardRunReport:
    """한 번 실행의 결과(작업 detail 로 옮긴다 — 값·종목코드는 싣지 않는다)."""

    status: Literal["ok", "not_ready", "skipped", "failed"]
    asof: date
    quality: Quality | None = None
    rows: int = 0
    n_stocks: int = 0
    n_labels: int = 0
    n_alltime: int = 0
    changes: Mapping[str, Any] = field(default_factory=dict[str, Any])
    reason: str | None = None
    n_notes: int = 0
    n_diagnostics: int = 0
    day: BoardDay | None = None

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = dict(asof=self.asof.isoformat(), status=self.status)
        if self.reason:
            d["reason"] = self.reason
        if self.quality is not None:
            d.update(
                quality=self.quality.value,
                n_stocks=self.n_stocks,
                n_labels=self.n_labels,
                n_alltime=self.n_alltime,
                n_notes=self.n_notes,
                n_diagnostics=self.n_diagnostics,
            )
        if self.changes:
            d["n_changed"] = self.changes.get("n_changed", 0)
        return d


# ── 행 변환 ──────────────────────────────────────────────────────────────────────────────


def _won(eok: float | None) -> int | None:
    """억원(엔진) → 원(저장). 음수·NaN 은 없다(거래대금·시총)."""
    return None if eok is None else round(eok * 1e8)


def _alltime_row(code: str, d: Mapping[str, Any], source: str, quality: Quality) -> AllTime:
    def day(v: Any) -> date | None:
        return date.fromisoformat(v) if v else None

    return AllTime(
        code=code,
        hi=d.get("hi"),
        hi_date=day(d.get("hi_date")),
        cl=d.get("cl"),
        cl_date=day(d.get("cl_date")),
        prev_hi=d.get("prev_hi"),
        prev_cl=d.get("prev_cl"),
        first_date=day(d.get("first_date")),
        last_date=day(d.get("last_date")),
        n_days=int(d.get("n_days") or 0),
        suspect=bool(d.get("suspect")),
        suspect_date=day(d.get("suspect_date")),
        suspect_note=d.get("suspect_note") or None,
        history_from=day(d.get("history_from")),
        source=source,
        quality=quality,
    )


def _row_quality(source: str) -> Quality:
    return Quality.OK if source.lower() == "krx" else Quality.ESTIMATED


def build_record(
    day: BoardDay,
    *,
    snaps: Mapping[str, Snap],
    now: datetime,
    input_digest: str,
    hist_scope: Mapping[str, Any] | None = None,
    confirm_changes: Mapping[str, Any] | None = None,
) -> BoardDayRecord:
    """엔진 `BoardDay` → 저장 묶음(계약 ⑦ — 저장소는 엔진을 모른다)."""
    d = date.fromisoformat(day.asof)
    labels = tuple(
        Label(
            code=x.code,
            date=d,
            basis=cast(Literal["close", "high"], x.basis),
            kind=cast(Literal["hist", "w52", "d120", "w52_low"], x.kind),
            rank=x.rank,
            source=snaps[x.code].source if x.code in snaps else day.source,
            quality=day.quality,
        )
        for x in day.labels
    )
    stock_days: list[StockDay] = []
    for r in day.universe["stocks"]:
        src = snaps[r["code"]].source if r["code"] in snaps else day.source
        stock_days.append(
            StockDay(
                code=r["code"],
                date=d,
                name=r.get("name"),
                close=r.get("close"),
                chg_pct=r.get("chg_pct"),
                turnover=_won(r.get("turnover")),
                turnover_is_estimate=bool(r.get("turnover_is_estimate")),
                mktcap=_won(r.get("mktcap")),
                label=r.get("label"),
                near_kind=r.get("near_kind"),
                near_gap=r.get("near_gap"),
                status=r.get("status"),
                suspect=bool(r.get("suspect")),
                sector=r.get("sector"),
                theme=r.get("theme"),
                ret_5d=r.get("ret_5d"),
                ret_21d=r.get("ret_21d"),
                vol_mult=r.get("vol_mult"),
                source=src,
                quality=_row_quality(src),
                # 나머지 키(억원 금액 `turnover_avg20` 등은 그대로 — 산출 JSON 과 같은 단위)
                extra={k: v for k, v in r.items() if k not in _STOCK_DAY_COLS},
            )
        )
    as_of = datetime.combine(d, CLOSE_TIME, tzinfo=KST)
    newhigh = dict(day.newhigh)
    newhigh["hist_scope"] = dict(hist_scope if hist_scope is not None else day.hist_scope)
    if confirm_changes is not None:
        newhigh["confirm"] = dict(confirm_changes)
    universe_meta = {k: v for k, v in day.universe.items() if k != "stocks"}
    payloads: dict[str, Mapping[str, Any]] = dict(
        universe_meta=universe_meta,
        newhigh=newhigh,
        sectors=day.sectors,
        events=day.events,
        rankings=day.rankings,
    )
    artifacts = tuple(
        BoardArtifact(
            trade_date=d,
            name=cast(Literal["universe_meta", "newhigh", "sectors", "events", "rankings"], name),
            payload=json.loads(
                json.dumps(p, ensure_ascii=False)
            ),  # jsonb 와 같은 모양(튜플 → 목록)
            engine_version=ENGINE_VERSION,
            input_digest=input_digest,
            as_of=as_of,
            generated_at=now,
            source=day.source,
            quality=day.quality,
        )
        for name, p in payloads.items()
    )
    return BoardDayRecord(
        trade_date=d,
        labels=labels,
        stock_days=tuple(stock_days),
        artifacts=artifacts,
        source=day.source,
        quality=day.quality,
    )


def label_changes(
    before: Mapping[str, Mapping[str, Label]],
    after: Sequence[BoardLabel | Label],
    *,
    prev_quality: str | None,
) -> dict[str, Any]:
    """잠정 → 확정에서 바뀐 라벨(D-P3-8 — '신규 생김·사라짐'·등급 변경).

    before = {basis: {code: 저장된 라벨}}, after = 새로 계산한 라벨.
    목록은 [기준, 코드, 라벨(, 새 라벨)].
    """
    now_map = {(x.basis, x.code): x.kind for x in after}
    old_map = {(b, c): lab.kind for b, m in before.items() for c, lab in m.items()}
    added = sorted([b, c, k] for (b, c), k in now_map.items() if (b, c) not in old_map)
    removed = sorted([b, c, k] for (b, c), k in old_map.items() if (b, c) not in now_map)
    changed = sorted(
        [b, c, old_map[(b, c)], k]
        for (b, c), k in now_map.items()
        if (b, c) in old_map and old_map[(b, c)] != k
    )
    return dict(
        previous_quality=prev_quality,
        added=added,
        removed=removed,
        changed=changed,
        n_changed=len(added) + len(removed) + len(changed),
    )


def _digest(
    asof: date,
    snap_asof: date | None,
    snaps: Mapping[str, Snap],
    series: Mapping[str, Sequence[Bar]],
) -> str:
    """입력 요약 해시 — 같은 입력이면 같은 값(artifact.input_digest)."""
    h = hashlib.sha256()
    h.update(f"{asof}|{snap_asof}|{ENGINE_VERSION}".encode())
    for code in sorted(snaps):
        s = snaps[code]
        h.update(f"|{code}:{s.source}:{s.close}:{s.turnover}:{s.mktcap}".encode())
    for code in sorted(series):
        rows = series[code]
        if rows:
            b = rows[-1]
            h.update(f"|{code}:{b.date}:{b.source}:{b.close}:{b.high}:{len(rows)}".encode())
    return h.hexdigest()[:32]


# ── 실행 ────────────────────────────────────────────────────────────────────────────────


# 시장 일봉을 '받은 날'로 볼 최소 종목 수 비율(최근 20거래일 중앙값 대비) — 한 종목만 들어온 옛 날짜
# (개별 적재·잘못 들어온 행)가 시장 이력의 첫날을 과거로 끌고 가지 않게 [제안]
MARKET_DAY_MIN_RATIO: Final = 0.5


def market_history_from(series: Mapping[str, Sequence[Bar]], cal: TradingCalendar) -> date | None:
    """시장 일봉을 **빠짐없이** 받아 둔 첫날(ADR 0017 — 역사적 신고가 `history_from`).

    가장 최근 날부터 KRX 거래일을 거꾸로 걸으며, 그날 일봉 종목 수가 최근 20거래일 중앙값의
    `MARKET_DAY_MIN_RATIO` 이상인 동안 이어 간다. 받지 못한 거래일(구멍)이나 몇 종목뿐인 날에서
    멈춘다 — 그 뒤 상장한 종목만 '상장 이후 전체'를 가진 것이다. 일봉이 없으면 None."""
    counts: dict[date, int] = {}
    for bars in series.values():
        for b in bars:
            counts[b.date] = counts.get(b.date, 0) + 1
    if not counts:
        return None
    recent = sorted(sorted(counts)[-20:], key=lambda d: counts[d])
    need = counts[recent[len(recent) // 2]] * MARKET_DAY_MIN_RATIO
    d = max(counts)
    start: date | None = None
    while counts.get(d, 0) >= need:
        start = d
        d = cal.prev_trading_day(d)
    return start


def newhigh_definition(src: Mapping[str, Any]) -> tuple[Any, ...]:
    """신고가 정의의 지문 — (우선순위, 거래일 창, 달력 창). 설정(newhigh 절이 있는 Mapping)이나
    저장된 newhigh 산출(`priority`·`thresholds`) 어느 쪽에서도 같은 모양으로 뽑는다(ADR 0017)."""
    if "newhigh" in src:
        nhc = src["newhigh"]
        win: Mapping[str, Any] = nhc
        pri = nhc.get("priority")
    else:
        win = src.get("thresholds") or {}
        pri = src.get("priority")
    return (
        tuple(pri or ()),
        tuple(sorted(dict(win.get(nh.TRADING_KEY) or {}).items())),
        tuple(sorted(dict(win.get(nh.CALENDAR_KEY) or {}).items())),
        tuple(sorted(dict(win.get("lookback") or {}).items())),  # 옛 정의(d60·252봉)
    )


def board_market_days(cal: TradingCalendar, start: date, asof: date) -> list[date]:
    """[start, asof] 의 KRX 거래일(오름차순) + 판정일 — 신고가 거래일 창의 경계(ADR 0017).

    판정일은 달력이 휴장이라고 해도 넣는다(그날 일봉이 있으면 그날이 판정일이다)."""
    out = [asof]
    d = asof
    while True:
        d = cal.prev_trading_day(d)
        if d < start:
            break
        out.append(d)
    return out[::-1]


def run_board(
    repos: BoardRepos,
    asof: date,
    *,
    now: datetime,
    loaded_by: str,
    mode: Literal["daily", "confirm"] = "daily",
    cfg: BoardConfig | None = None,
    knowledge: BoardKnowledge | None = None,
    calendar: TradingCalendar | None = None,
) -> BoardRunReport:
    """그날 보드를 계산해 저장한다. 시험은 메모리 저장소·사전을 넣는다.

    calendar  KRX 거래일 달력(없으면 `TradingCalendar.default()`) — 신고가 120거래일 창의 경계."""
    if now.tzinfo is None:
        raise ValueError("now 는 시간대가 있어야 한다")
    cfg = cfg if cfg is not None else BoardConfig.load()
    know = knowledge if knowledge is not None else BoardKnowledge.load()
    basis = cfg["newhigh"]["default_basis"]

    days = repos.market.trading_days(asof, 2)
    if not days or days[0] != asof:
        return BoardRunReport("not_ready", asof, reason="그날 일봉이 아직 없다")
    prev_asof = days[1] if len(days) > 1 else None

    raw_snaps, snap_asof = repos.market.snapshot(asof)
    universe = repos.market.universe(asof)
    # 종류는 스냅의 kind, 없으면 유니버스의 kind(ET 실측: 펀드를 못 거르면 역사적 신고가 대부분이
    # 채권형 ETF 가 된다). 둘 다 없으면 모름 — 이름으로 짐작하지 않고 수를 안내에 남긴다
    ukind = {u.code: u.kind for u in universe}
    kind_of = {c: s.kind or ukind.get(c) for c, s in raw_snaps.items()}
    funds = {c for c, k in kind_of.items() if (k or "") in FUND_KINDS}
    n_kind_unknown = sum(1 for k in kind_of.values() if not k)
    invalid = {c for c, s in raw_snaps.items() if s.quality is Quality.INVALID}
    snaps = {c: s for c, s in raw_snaps.items() if c not in funds and c not in invalid}
    if not snaps:
        return BoardRunReport("not_ready", asof, reason="그날 이하 종목 스냅이 없다")
    confirmed, _, _ = close_provenance({c: snap_row(s) for c, s in snaps.items()})
    if mode == "confirm" and not confirmed:
        return BoardRunReport(
            "not_ready", asof, reason="KRX 확정 스냅 비율이 기준 미만 — 확정 보드를 쓰지 않는다"
        )

    series = repos.market.series(None, asof, cfg.series_days)
    listed_on = {u.code: u.listed_on for u in universe}
    kr = calendar if calendar is not None else TradingCalendar.default()
    # 읽은 일봉 창의 첫날(시장 기준) — 신고가 창의 달력이 여기서 시작한다
    window_from = min((b[0].date for b in series.values() if b), default=asof)
    # 스칼라가 없는 종목의 history_from — 창 안에서 시장 일봉을 빠짐없이 받은 첫날
    hist_from = market_history_from(series, kr) or window_from

    # 스칼라: 수집 → 갱신 → 계산(ET 순서). 새 스칼라의 history_from = 시장 일봉 창의 첫날 — 그 뒤에
    # 상장한 종목은 상장 이후 전체를 갖고, 그 전에 상장한 종목은 백필·다시 쌓기 전까지 hist 보류.
    prev_at = repos.board.alltime()
    rolled: dict[str, dict[str, Any]] = {}
    changed_at: list[AllTime] = []
    ahead = 0
    for code, bars in series.items():
        if not bars:
            continue
        old = prev_at.get(code)
        old_d = nh.alltime_dict(old) if old is not None else None
        if old is not None and old.last_date is not None and old.last_date > asof:
            # 다음 거래일 몫까지 이미 굴린 스칼라 — 그날의 '직전일까지 최고가'(prev_hi)를
            # 복원할 수 없다. 이대로 계산하면 역사적 신고가가 전부 '복원 불가'로 빠진 보드가
            # 멀쩡한 보드를 덮는다
            ahead += code in snaps
            continue
        if old_d is not None and bars[-1].date == asof:
            # 그날 몫을 이미 반영한 스칼라(같은 as_of 재실행·board.confirm) — 지금 일봉 값으로 그날
            # 몫만 다시 쓴다(값이 같으면 그대로 — 멱등)
            old_d = nh.restate_last_day(old_d, bars[-1])
        new = nh.roll_alltime(old_d, bars, cfg)
        new["history_from"] = (old_d or {}).get("history_from") or hist_from.isoformat()
        rolled[code] = new
        last_src = bars[-1].source
        row = _alltime_row(code, new, last_src, _row_quality(last_src))
        if old is None or row != old:
            changed_at.append(row)
    if ahead:
        # 다시 시도해도 풀리지 않는다 — 실패로 남겨 알린다(그날 보드는 이전 것 그대로 둔다)
        return BoardRunReport(
            "failed",
            asof,
            reason=(
                f"다음 거래일까지 반영된 역사적 최고가 스칼라가 {ahead:,}종목 — 그날 기준을 "
                "복원할 수 없어 보드를 다시 쓰지 않는다"
            ),
        )

    before: dict[str, dict[str, Label]] = {}
    prev_quality: str | None = None
    if mode == "confirm":
        before = {b: repos.board.labels(asof, b) for b in nh.BASES}
        old_nh = repos.board.artifact(asof, "newhigh")
        prev_quality = old_nh.quality.value if old_nh is not None else None

    prev_nh = repos.board.artifact(prev_asof, "newhigh") if prev_asof else None
    prev_rk = repos.board.artifact(prev_asof, "rankings") if prev_asof else None
    # 전일 보드가 다른 신고가 정의로 계산됐으면(정의 전환일 — ADR 0017) 전일 라벨·근접과 비교하지
    # 않는다. 저장된 전일 산출에서 매번 다시 판단하므로 같은 날 재실행해도 결과가 같다(상태를 들고
    # 있지 않는다 — ET 전환일 재실행 버그와 같은 일이 생기지 않는다)
    incomparable = None
    if prev_nh is not None and newhigh_definition(prev_nh.payload) != newhigh_definition(cfg):
        incomparable = (
            f"전일({prev_asof}) 보드는 예전 신고가 정의로 계산돼 오늘 라벨과 비교할 수 없습니다"
        )
    notes: list[tuple[str, str]] = []
    if invalid:
        notes.append(
            (
                "reconcile_invalid",
                f"KRX 대조에서 어긋난 {len(invalid):,}종목은 보드에서 뺐습니다 — "
                "종가를 확정하지 못했습니다",
            )
        )
    inp = BoardInputs(
        asof=asof,
        prev_asof=prev_asof,
        series=series,
        snapshot=snaps,
        snap_asof=snap_asof,
        generated_at=now.astimezone(KST).isoformat(timespec="seconds"),
        alltime=rolled,
        sectors=know.sectors,
        taxonomy_counts=[(know.sector_taxonomy, len(know.sectors))] if know.sectors else [],
        prev_ranks=(
            prev_ranks_from(repos.board.labels(prev_asof, basis))
            if prev_asof and incomparable is None
            else {}
        ),
        prev_incomparable=incomparable,
        split_cleared=frozenset(repos.board.split_cleared()),
        split_unknown=repos.board.split_unknown(),
        prev_near=prev_near_from(prev_nh.payload if prev_nh and incomparable is None else None),
        collect_notes=notes,
        close_note=None if confirmed else NEXT_DAY_CONFIRM,
        funds_excluded=(
            f"{len(funds)}종목 제외 / 종류 판정 {len(kind_of) - n_kind_unknown}종목"
            + (f" · 종류 모름 {n_kind_unknown}종목(펀드인지 확인 못 함)" if n_kind_unknown else "")
        ),
        themes_yaml=know.themes,
        taxonomy=know.taxonomy,
        prev_rankings=prev_rk.payload if prev_rk else None,
        listed_on=listed_on,
        trading_days=board_market_days(kr, window_from, asof),
    )
    day = compute_day(inp, cfg)

    changes = (
        label_changes(before, day.labels, prev_quality=prev_quality) if mode == "confirm" else None
    )
    rec = build_record(
        day,
        snaps=snaps,
        now=now,
        input_digest=_digest(asof, snap_asof, snaps, series),
        confirm_changes=changes,
    )
    n_at = repos.board.put_alltime(changed_at, loaded_by=loaded_by, now=now) if changed_at else 0
    n = repos.board.put_day(rec, loaded_by=loaded_by, now=now)
    return BoardRunReport(
        "ok",
        asof,
        quality=day.quality,
        rows=n + n_at,
        n_stocks=len(rec.stock_days),
        n_labels=len(rec.labels),
        n_alltime=n_at,
        changes=changes or {},
        n_notes=len(day.notes),
        n_diagnostics=len(day.diagnostics),
        day=day,
    )


def rebuild_alltime(
    repos: BoardRepos,
    upto: date,
    *,
    now: datetime,
    loaded_by: str,
    codes: Sequence[str] | None = None,
    max_days: int = 20_000,
    cfg: BoardConfig | None = None,
    calendar: TradingCalendar | None = None,
) -> int:
    """역사적 최고가 스칼라를 받은 일봉 전체로 **처음부터** 다시 쌓는다(설계 §3.8 'board 연결').

    KRX 백필(`market.backfill` — `--to-listing` 이면 상장일까지)이 과거 구간을 채운 뒤 부른다 —
    `roll_alltime` 은 이미 지난 날을 다시 반영하지 않으므로(ET `--init` 와 같은 뜻) 늘어난 이력을
    보려면 다시 쌓아야 한다. `history_from` = **시장 일봉을 빠짐없이 받아 둔 첫날**
    (`market_history_from` — D-P3-11·ADR 0017. 종목의 첫 봉이 아니다: 상장 직후 거래정지로 첫 봉이
    늦은 종목도 상장 이후 전체를 가진 것이다. 상장일이 이 날보다 앞인 종목은 원천 바닥에 닿지
    않았으면 hist 보류). 반환: 쓴 스칼라 수.
    """
    cfg = cfg if cfg is not None else BoardConfig.load()
    kr = calendar if calendar is not None else TradingCalendar.default()
    series = repos.market.series(codes, upto, max_days)
    market_from = market_history_from(series, kr)
    rows: list[AllTime] = []
    for code, bars in series.items():
        if not bars:
            continue
        a = nh.roll_alltime(None, bars, cfg)
        a["history_from"] = (market_from or bars[0].date).isoformat()
        src = bars[-1].source
        rows.append(_alltime_row(code, a, src, _row_quality(src)))
    return repos.board.put_alltime(rows, loaded_by=loaded_by, now=now) if rows else 0


# ── 작업 처리기(config/jobs.yaml owner) ───────────────────────────────────────────────────


def _repos(ctx: JobContext) -> BoardRepos:
    """주입된 저장소(`repos` — 시험·시뮬레이션), 없으면 연결 공장(`connect`)으로 Pg 저장소."""
    repos = ctx.resources.get("repos")
    if repos is not None:
        return cast(BoardRepos, repos)
    return pg_repos(ctx.resource("connect"))


def _handle(ctx: JobContext, mode: Literal["daily", "confirm"]) -> JobResult:
    asof = date.fromisoformat(ctx.as_of)
    kr = ctx.resources.get("kr")  # 실행기 공용 KRX 달력(없으면 기본)
    rep = run_board(
        _repos(ctx),
        asof,
        now=ctx.now,
        loaded_by=ctx.job,
        mode=mode,
        calendar=kr if isinstance(kr, TradingCalendar) else None,
    )
    return JobResult(status=rep.status, rows=rep.rows, detail=rep.detail())


def daily(ctx: JobContext) -> JobResult:
    """`board.daily`(16:20) — KIS 마감 스냅으로 잠정 보드(quality estimated). as_of = 그날."""
    return _handle(ctx, "daily")


def confirm(ctx: JobContext) -> JobResult:
    """`board.confirm`(08:40) — KRX 확정 일봉으로 전 거래일 보드를 다시 계산해 덮는다(ok)."""
    return _handle(ctx, "confirm")
