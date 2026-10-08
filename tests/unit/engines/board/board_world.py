"""보드 서비스 시험용 합성 세계 — 메모리 저장소에 일봉·스냅·유니버스를 심는다(값은 전부 가짜).

종목(코드 → 사례)
  000010  D 에 KIS 종가로 52주·역사적 신고가, KRX 확정 종가는 그 아래(확정하면 라벨이 사라진다)
  000020  D 에 120일 신고가(52주 고점은 아래 — 200거래일 전) — KIS·KRX 같음
  000030  상장일이 이력 시작(history_from)보다 앞 → 역사적 신고가를 계산하지 않는다(D-P3-11)
  000040  상장일 모름 → 역사적 신고가를 계산하지 않는다
  000050  ETF(kind etf) → 보드에서 뺀다
  000060  대조 불일치로 invalid 가 된 KIS 스냅만 있다 → 보드에서 뺀다
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from kbj.core.quality import Quality
from kbj.core.rows import Bar, Snap, UniverseRow
from kbj.engines.board.config import BoardConfig
from kbj.services.engine.board import BoardKnowledge
from kbj.store.repos import memory_repos
from kbj.store.repos.memory import MemoryRepos

CFG = BoardConfig.load()
N_DAYS = 300
D = date(2026, 10, 6)  # 화요일
NEXT = date(2026, 10, 7)
NOW_DAILY = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)  # 16:00 KST(ADR 0018)
NOW_CONFIRM = datetime(2026, 10, 6, 23, 40, tzinfo=UTC)  # 다음 날 08:40 KST
CAP = 500_000_000_000  # 5,000억(원) — 보드 하한(1,000억) 위
KNOW = BoardKnowledge(
    themes={},
    taxonomy={"meta": {"taxonomy": "board48"}, "sectors": [{"name": "반도체장비"}]},
    sectors={"000010": "반도체장비", "000020": "반도체장비"},
    sector_taxonomy="board48",
)


def weekdays_until(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


DAYS = weekdays_until(D, N_DAYS)
FIRST = DAYS[0]


def _closes(code: str) -> list[float]:
    base = [10000.0 + (i % 7) * 10 for i in range(N_DAYS)]  # 10,000~10,060 횡보
    if code == "000010":
        base[-1] = 10500.0  # D: 전 구간 최고 돌파
    elif code == "000020":
        base[100] = 11000.0  # 52주 고점은 과거에
        base[-1] = 10200.0  # D: 120일 고점(10,060) 돌파, 52주(11,000 — 200거래일 전) 아래
    elif code in ("000030", "000040"):
        base[-1] = 10600.0  # 52주 돌파 — 역사적은 이력 깊이 때문에 못 낸다
    return base


@dataclass(frozen=True)
class World:
    repos: MemoryRepos
    codes: tuple[str, ...]


def bar(code: str, d: date, close: float, source: str) -> Bar:
    return Bar(
        code=code,
        date=d,
        open=close,
        high=close,
        low=close * 0.99,
        close=close,
        volume=100_000,
        turnover=int(close * 100_000),
        source=source,
        venue="KRX",
        quality=Quality.OK if source == "krx" else Quality.ESTIMATED,
    )


def snap(
    code: str,
    d: date,
    close: float,
    source: str,
    *,
    kind: str = "common",
    quality: Quality | None = None,
) -> Snap:
    return Snap(
        code=code,
        date=d,
        name=f"합성{code[-2:]}",
        market="KOSPI",
        kind=kind,
        close=close,
        chg_pct=None,
        volume=100_000,
        turnover=int(close * 100_000),
        turnover_is_estimate=False,
        mktcap=CAP,
        shares=None,
        status_flags=(),
        source=source,
        venue="KRX",
        quality=quality or (Quality.OK if source == "krx" else Quality.ESTIMATED),
    )


CODES = ("000010", "000020", "000030", "000040", "000050", "000060")


def build_world() -> World:
    """D 의 KIS 마감까지(16:00 board.daily 직전) — 이전 날은 전부 KRX 확정."""
    repos = memory_repos()
    bars: list[Bar] = []
    for code in CODES:
        closes = _closes(code)
        for d, c in zip(DAYS[:-1], closes[:-1], strict=True):
            bars.append(bar(code, d, c, "krx"))
        bars.append(bar(code, D, closes[-1], "kis"))
    repos.market.upsert_daily_bars(bars, loaded_by="test")
    snaps = [snap(c, D, _closes(c)[-1], "kis") for c in CODES if c not in ("000050", "000060")]
    snaps.append(snap("000050", D, 10000.0, "kis", kind="etf"))
    snaps.append(snap("000060", D, 10000.0, "kis", quality=Quality.INVALID))
    repos.market.upsert_snapshots(snaps, loaded_by="test")
    uni = [
        UniverseRow(
            code=c,
            as_of=D,
            name=f"합성{c[-2:]}",
            market="KOSPI",
            kind="etf" if c == "000050" else "common",
            listed_on=None if c == "000040" else (date(2001, 3, 2) if c == "000030" else FIRST),
            source="krx",
            quality=Quality.OK,
        )
        for c in CODES
    ]
    repos.market.upsert_universe(uni, loaded_by="test")
    return World(repos, CODES)


def add_krx_confirm(w: World, *, close_000010: float = 9900.0) -> None:
    """다음 날 08:05 krx.daily 가 쓴 것 — D 의 KRX 확정 일봉·스냅(000010 은 KIS 보다 낮다)."""
    rows: list[Bar] = []
    snaps: list[Snap] = []
    for code in CODES:
        c = close_000010 if code == "000010" else _closes(code)[-1]
        rows.append(bar(code, D, c, "krx"))
        if code == "000050":
            snaps.append(snap(code, D, c, "krx", kind="etf"))
        elif code != "000060":
            snaps.append(snap(code, D, c, "krx"))
    w.repos.market.upsert_daily_bars(rows, loaded_by="test")
    w.repos.market.upsert_snapshots(snaps, loaded_by="test")
