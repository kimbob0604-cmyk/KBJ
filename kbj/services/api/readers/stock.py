"""종목 상세 readers — 종목 찾기·검색·머리·차트·컨센서스·밸류에이션·시나리오 적정가
(docs/p4_design.md §5·§6.2, 페이지 8).

- **종목 찾기**(`Lookup.ref`): 유니버스(전 거래일 — KRX 기본정보)와 DART 고유번호 표(`pub_filings.
  corp_code`) 어디에도 없으면 `UnknownCode`(404 `unknown_code`). 우선주는 `prv_market.share_class`
  로 회사(`common_code`·`corp_code`)를 찾는다 — 재무·공시·오버행·내부자·컨센서스·밸류는 회사 것,
  시세· 수급은 그 코드 것(§5.3). 매핑이 없는 우선주는 회사 섹션이 404 `no_data`(R13 — 코드
  규칙만으로 짐작하지 않는다).
- 값은 저장소에서 읽고(수집·계산은 작업이 미리 했다) 엔진(순수)으로 읽을 때 계산할 것만 계산한다 —
  NTM·목표가 분포(`kbj.engines.consensus`), 수집 대상 규칙(`consensus_na`). 신고가 배지는 board
  산출을 읽기만 한다(D-P4-23 — 다시 계산하지 않는다, 3축 ADR 0017).
- SMA 20·60·120(D-P4-21 — 차트 겹쳐 보기만)은 지표 정본(`kbj.core.indicators` — P8) 전까지 이 모듈의
  `sma` 하나로 낸다(산술 평균, 창을 못 채운 칸은 None).
- 적용 불가는 200 + `applicable=false` + `na_reason`(사유 우선순위는 docs/metrics.md §11 — 계산하는
  작업(V·K)이 정해 저장한 사유를 그대로 싣는다). 작업이 행을 아직 쓰지 않았을 때만 회사 단위 사유
  (`currency_mismatch` — 재무 통화 ≠ KRW, `no_filings` — 정기보고서 없음)를 여기서 정한다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cached_property
from typing import Final, Literal, cast

from kbj.core.fin_rows import (
    BAND_METRICS,
    BandRow,
    ConsensusRow,
    CorpProfile,
    NaReason,
    ScenarioRow,
    ShareClass,
    ValuationDay,
)
from kbj.core.quality import Quality
from kbj.core.rows import Bar, Snap, UniverseRow
from kbj.engines.consensus import LABEL as CONSENSUS_LABEL
from kbj.engines.consensus.forward import latest_snapshot, ntm
from kbj.engines.consensus.target import opinion_counts, target_consensus
from kbj.engines.consensus.universe import consensus_na
from kbj.engines.financials.period import report_of_title
from kbj.engines.flows.ledger import close_as_of
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.stock import (
    BandView,
    ChartBar,
    ChartEvent,
    ClassCap,
    Consensus,
    FyEstimateView,
    Kind,
    MultipleCell,
    NewHighBadges,
    NtmView,
    OpinionDist,
    OpinionView,
    PriceChart,
    RevisionView,
    Scenario,
    ScenarioCase,
    ScenarioCases,
    SeriesPoint,
    SourcedQuote,
    StockHeader,
    StockSearch,
    StockSearchItem,
    TargetView,
    Valuation,
    ValuationCurrent,
)
from kbj.services.api.readers._common import (
    NoData,
    ReadContext,
    UnknownCode,
    day_start,
    envelope,
    label,
    worst,
)

__all__ = [
    "CONSENSUS_ETA",
    "LOST_BY_KIS",
    "SMA_WINDOWS",
    "VALUATION_ETA",
    "WATCH_LIST",
    "Lookup",
    "StockRef",
    "company_na",
    "dart_as_of",
    "kind_of",
    "sma",
    "stock_chart",
    "stock_consensus",
    "stock_header",
    "stock_scenario",
    "stock_search",
    "stock_valuation",
]

WATCH_LIST: Final = "main"
SMA_WINDOWS: Final[tuple[int, ...]] = (20, 60, 120)  # D-P4-21
MARKET_ETA: Final = "krx.daily 08:05 · market.close_collect 15:35"
CORP_CODE_ETA: Final = "filings.corp_code 03:05(DART 고유번호 표)"
SHARE_CLASS_ETA: Final = "fin.valuation_band 08:50(우선주 → 보통주 매핑 share_class)"
CONSENSUS_ETA: Final = "consensus.snapshot 16:00(실데이터 확인 전 꺼짐 — #29)"
VALUATION_ETA: Final = "fin.valuation_band 08:50"
FIN_ETA: Final = "fin.quarterly(30분 — 07:00~21:30)"
# D-P4-10 — KIS 교체로 잃은 것(화면 '출처 없음 — KIS 교체로 잃음' 줄, metrics §10)
LOST_BY_KIS: Final[tuple[str, ...]] = (
    "분기 컨센서스",
    "분기 어닝 서프라이즈",
    "컨센서스 최고·최저",
)
LOST_NOTE: Final = "출처 없음 — KIS 교체로 잃음: " + "·".join(LOST_BY_KIS)
LABEL_NOTE: Final = (
    f"{CONSENSUS_LABEL} — 여러 증권사 평균(시장 컨센서스)인지 KIS 자체 추정인지 [확인 필요 #29]"
)
SCENARIO_NOTES: Final[tuple[str, ...]] = (
    "시나리오 적정가 = EPS × 배수(Bear·Base·Bull) — 시장 규모 추정이 아니다(D-P4-12)",
    "컨센서스 최고값 출처 없음(KIS 교체) — Bull EPS = Base × 1.2",
)
CHART_MAX_DAYS: Final = 1240
SEARCH_MAX: Final = 20
PERIODIC_LOOKBACK_DAYS: Final = 400  # 정기보고서 공시를 찾아보는 기간(no_filings 판정)
CONSENSUS_LOOKBACK_DAYS: Final = 400
BAND_SERIES_DAYS: Final = 1830  # 밴드 창(1,240거래일 ≈ 5년) + 여유 — 시계열 읽기 범위
_LABELS: Final = ("hist", "w52", "d120")
_MULTIPLES: Final = ("per", "pbr", "psr", "ev_ebitda", "per_fwd", "psr_fwd", "ev_ebitda_fwd")
_KINDS: Final = ("common", "pref", "spac", "reit", "etf", "etn")
Metric = Literal["per", "pbr", "psr", "ev_ebitda"]


def dart_as_of(d: date) -> datetime:
    """DART 날짜 값(접수일·분기말)의 기준 시각 — 그날 00:00 KST."""
    return day_start(d)


def kind_of(v: str | None) -> Kind | None:
    return cast(Kind, v) if v in _KINDS else None


def _parts(sources: Iterable[str]) -> str:
    """작업이 저장한 묶음 원천(`KRX+DART+KIS` 등)을 낱개로 풀어 한 번씩만 — 겹친 표기
    (`KRX+DART+KRX+DART+KIS`)가 나오지 않게."""
    return label(p for s in sources for p in s.split("+") if p)


def _finite(v: float | None) -> float | None:
    if v is None or not math.isfinite(v):
        return None
    return float(v)


# ── 종목 찾기 ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StockRef:
    """요청한 종목코드와 그 회사. `common_code`·`corp_code` 가 None 이면 회사 섹션은 404 no_data."""

    code: str
    name: str | None
    market: str | None
    kind: str | None
    listed_on: date | None
    uni: UniverseRow | None
    share_class: ShareClass | None
    common_code: str | None
    corp_code: str | None
    corp_name: str | None
    profile: CorpProfile | None

    def need_common(self) -> str:
        if self.common_code is None:
            raise NoData(f"아직 없음 — {SHARE_CLASS_ETA}")
        return self.common_code

    def need_corp(self) -> str:
        self.need_common()
        if self.corp_code is None:
            raise NoData(f"아직 없음 — {CORP_CODE_ETA}")
        return self.corp_code


class Lookup:
    """한 요청 안의 종목 찾기 — 유니버스·고유번호 표·종류주식·관심종목·스냅을 한 번씩만 읽는다."""

    def __init__(self, ctx: ReadContext) -> None:
        self.ctx = ctx
        self.repos = ctx.repos
        self.today = ctx.today()

    @cached_property
    def universe(self) -> dict[str, UniverseRow]:
        return {u.code: u for u in self.repos.market.universe(self.today)}

    @cached_property
    def corp_index(self) -> dict[str, tuple[str, str]]:
        return self.repos.filings.corp_index()

    @cached_property
    def stock_of_corp(self) -> dict[str, tuple[str, str]]:
        """{corp_code: (종목코드, 회사명)} — 상장사 고유번호 표를 거꾸로."""
        return {corp: (code, name) for code, (corp, name) in self.corp_index.items()}

    @cached_property
    def classes(self) -> dict[str, ShareClass]:
        return self.repos.consensus.share_classes()

    @cached_property
    def watch(self) -> set[str]:
        return self.repos.journal.watch_codes(WATCH_LIST)

    @cached_property
    def watch_common(self) -> set[str]:
        """관심종목을 보통주 코드로(우선주 → 보통주 — 회사 단위 판단에 쓴다)."""
        out: set[str] = set()
        for c in self.watch:
            sc = self.classes.get(c)
            out.add(sc.common_code if sc is not None else c)
        return out

    @cached_property
    def watch_corps(self) -> set[str]:
        out: set[str] = set()
        for c in self.watch_common:
            hit = self.corp_index.get(c)
            if hit is not None:
                out.add(hit[0])
        return out

    @cached_property
    def snapshot(self) -> tuple[dict[str, Snap], date | None]:
        return self.repos.market.snapshot(self.today)

    def snap(self, code: str) -> Snap | None:
        return self.snapshot[0].get(code)

    def close(self, code: str) -> tuple[float | None, date | None]:
        s = self.snap(code)
        return (None, None) if s is None else (s.close, s.date)

    @cached_property
    def top_n(self) -> set[str]:
        """전 거래일 시총 상위 `consensus.top_n` 보통주(코스피·코스닥 — 엔진 `consensus_na`
        규칙)."""
        n = self.ctx.fin_config().consensus.top_n
        if n <= 0:
            return set()
        snaps, _ = self.snapshot
        ranked = sorted(
            (
                (s.mktcap, code)
                for code, s in snaps.items()
                if s.mktcap is not None
                and (u := self.universe.get(code)) is not None
                and consensus_na(u) is None
            ),
            key=lambda t: (-t[0], t[1]),
        )
        return {code for _cap, code in ranked[:n]}

    @cached_property
    def consensus_universe(self) -> set[str]:
        """컨센서스 수집 대상 보통주 = 관심종목 ∪ 시총 상위 N(§4.7 — 수집 작업과 같은 규칙)."""
        cfg = self.ctx.fin_config().detail_universe
        out = set(self.top_n) if cfg.consensus_top_n else set()
        out |= {c for c in self.watch_common if (u := self.universe.get(c)) and not consensus_na(u)}
        return out

    @cached_property
    def detail_codes(self) -> set[str]:
        """상세 수집 대상(보통주 코드) = 관심종목 ∪ 컨센서스 유니버스 ∪ 공개 데모(D-P4-5)."""
        cfg = self.ctx.fin_config().detail_universe
        out: set[str] = set()
        if cfg.watchlist:
            out |= self.watch_common
        if cfg.consensus_top_n:
            out |= self.top_n
        if cfg.public_demo:
            out |= set(self.ctx.reports_config().public_demo)
        return out

    def ref(self, code: str) -> StockRef:
        u = self.universe.get(code)
        idx = self.corp_index.get(code)
        sc = self.classes.get(code)
        if u is None and idx is None:
            raise UnknownCode()
        kind = u.kind if u is not None else None
        if sc is not None:
            common: str | None = sc.common_code
        elif kind == "pref":
            common = None  # 매핑이 없는 우선주 — 코드 규칙만으로 짐작하지 않는다(R13)
        else:
            common = code
        corp: str | None = None
        corp_name: str | None = None
        if common is not None and (hit := self.corp_index.get(common)) is not None:
            corp, corp_name = hit
        elif sc is not None and sc.corp_code is not None:
            corp = sc.corp_code
            corp_name = self.stock_of_corp.get(corp, (None, None))[1]
        profile = None
        if corp is not None:
            profile = self.repos.filings.profiles([corp]).get(corp)
        name = (u.name if u is not None else None) or (idx[1] if idx is not None else None)
        return StockRef(
            code=code,
            name=name,
            market=u.market if u is not None else None,
            kind=kind,
            listed_on=u.listed_on if u is not None else None,
            uni=u,
            share_class=sc,
            common_code=common,
            corp_code=corp,
            corp_name=corp_name or (profile.corp_name if profile is not None else None),
            profile=profile,
        )

    def has_periodic(self, corp: str, acc_mt: int | None) -> bool:
        """최근 400일에 정기보고서(제목의 '(YYYY.MM)' 로 사업연도·보고서를 읽을 수 있는 것)가 있나.

        공시 종류 `periodic` 은 제목에 '사업보고서'만 들어가도 붙는다('관리종목지정(사업보고서
        미제출)' 등) — 재무 수집과 같은 제목 해석(`report_of_title`)으로 진짜 보고서만 센다.
        """
        rows = self.repos.filings.disclosures(
            start=self.today - timedelta(days=PERIODIC_LOOKBACK_DAYS),
            end=self.today,
            corp_code=corp,
            kinds={"periodic"},
        )
        return any(report_of_title(r.title, acc_mt) is not None for r in rows)

    @cached_property
    def fin_loaded(self) -> bool:
        """재무 수집(백필)이 한 번이라도 돌았는가 — 빈 재무를 '정기보고서 없음'으로 읽어도
        되는가."""
        return bool(self.repos.fin.quarter_corps())


def company_na(lk: Lookup, ref: StockRef) -> NaReason | None:
    """작업이 행을 쓰지 않았을 때 회사 단위로 정하는 적용 불가 사유(metrics §11 우선순위 앞쪽 둘).

    재무 통화 ≠ KRW → `currency_mismatch`. 재무가 없고 최근 400일 정기보고서 공시도 없으며 재무
    수집이 돈 적이 있으면 → `no_filings`. 그 밖은 None(아직 수집 안 됨 — 부르는 쪽이 404 no_data).
    """
    if ref.corp_code is None:
        return None
    quarters = lk.repos.fin.quarters(ref.corp_code)
    if quarters:
        if any(q.currency != "KRW" for q in quarters):
            return NaReason.CURRENCY_MISMATCH
        return None
    acc_mt = ref.profile.acc_mt if ref.profile is not None else None
    if lk.fin_loaded and not lk.has_periodic(ref.corp_code, acc_mt):
        return NaReason.NO_FILINGS
    return None


# ── 검색 ──────────────────────────────────────────────────────────────────────────────


def stock_search(ctx: ReadContext, q: str, *, limit: int) -> Envelope[StockSearch]:
    """유니버스 ∪ DART 상장사 — 코드 앞부분·이름 부분 일치(같은 순위면 시총 큰 순)."""
    lk = Lookup(ctx)
    if not lk.universe and not lk.corp_index:
        raise NoData(f"아직 없음 — {MARKET_ETA} · {CORP_CODE_ETA}")
    needle = q.strip()
    up = needle.upper()
    low = needle.lower()
    names: dict[str, str | None] = {code: name for code, (_c, name) in lk.corp_index.items()}
    for code, u in lk.universe.items():
        names[code] = u.name or names.get(code)
    snaps, _ = lk.snapshot
    hits: list[tuple[int, int, str]] = []
    for code, name in names.items():
        n = (name or "").lower()
        if code == up:
            rank = 0
        elif code.startswith(up):
            rank = 1
        elif n.startswith(low):
            rank = 2
        elif low in n:
            rank = 3
        else:
            continue
        s = snaps.get(code)
        cap = s.mktcap if s is not None and s.mktcap is not None else -1
        hits.append((rank, -cap, code))
    hits.sort()
    items: list[StockSearchItem] = []
    for _rank, _cap, code in hits[:limit]:
        u = lk.universe.get(code)
        idx = lk.corp_index.get(code)
        sc = lk.classes.get(code)
        corp = idx[0] if idx is not None else (sc.corp_code if sc is not None else None)
        items.append(
            StockSearchItem(
                code=code,
                name=names.get(code),
                market=u.market if u is not None else None,
                kind=kind_of(u.kind) if u is not None else None,
                corp_code=corp,
                is_watch=code in lk.watch,
            )
        )
    days = [u.as_of for u in lk.universe.values()]
    as_of = close_as_of(max(days)) if days else ctx.now()
    srcs = {u.source for u in lk.universe.values()} | ({"DART"} if lk.corp_index else set())
    return envelope(
        StockSearch(q=needle, n_total=len(hits), items=items),
        source=label(srcs),
        as_of=as_of,
        quality=Quality.OK,
        notes=[],
        generated_at=ctx.now(),
    )


# ── 머리 ──────────────────────────────────────────────────────────────────────────────


def _traded(b: Bar) -> bool:
    """거래가 있던 봉 — KRX 일별은 정지일에도 행을 주고 시·고·저 0, 거래량 0(관행)이라 뺀다."""
    if b.close is None or b.volume is None or b.volume <= 0:
        return False
    return not (b.open is not None and b.open <= 0)


def _last_trade(ctx: ReadContext, code: str) -> date | None:
    bars = ctx.repos.market.series([code], ctx.today(), 30).get(code, [])
    days = [b.date for b in bars if _traded(b)]
    return max(days) if days else None


def _status_flags(lk: Lookup, s: Snap | None) -> list[str] | None:
    """상태 배지 — 그 스냅의 status_flags, 없으면 같은 날 KIS 스냅(KRX 일별에는 상태가 없다)."""
    if s is None:
        return None
    if s.status_flags is not None:
        return list(s.status_flags)
    for k in lk.repos.market.snapshots(s.date, source="kis"):
        if k.code == s.code and k.status_flags is not None:
            return list(k.status_flags)
    return None


def _why_no_trade(flags: Sequence[str] | None) -> str:
    """마지막 거래가 시세 날짜보다 앞인 까닭 — 정지 배지가 있으면 '정지', 없으면 '거래 없음'(코넥스
    등 거래가 안 된 날도 KRX 일별은 거래량 0 행을 준다 — 정지로 단정하지 않는다)."""
    return "정지" if flags and "halted" in flags else "거래 없음"


def _newhigh(ctx: ReadContext, code: str) -> NewHighBadges | None:
    """board 산출의 신고가 3축(종가 기준 — ADR 0017). 축마다 None = 창을 못 채움('—')."""
    board = ctx.repos.board
    d = board.last_day(ctx.today())
    if d is None:
        return None
    sd = board.stock_days(d).get(code)
    if sd is None:
        lab = board.labels(d, "close").get(code)
        if lab is None:
            return None
        axes: dict[str, bool | None] = {k: (True if k == lab.kind else None) for k in _LABELS}
        return NewHighBadges(
            date=d,
            basis="close",
            d120=axes["d120"],
            w52=axes["w52"],
            hist=axes["hist"],
            label=cast(Literal["hist", "w52", "d120"], lab.kind) if lab.kind in _LABELS else None,
            source=label([lab.source]),
            quality=lab.quality,
        )
    basis = sd.extra.get("close_basis")
    hits: dict[str, object] = {}
    gaps: dict[str, object] = {}
    lab_kind: object = sd.label
    if isinstance(basis, dict):
        b = cast(dict[str, object], basis)
        h, g = b.get("hits"), b.get("gap")
        hits = cast(dict[str, object], h) if isinstance(h, dict) else {}
        gaps = cast(dict[str, object], g) if isinstance(g, dict) else {}
        lab_kind = b.get("label")

    def axis(k: str) -> bool | None:
        hit = hits.get(k)
        if hit is True:
            return True
        if gaps.get(k) is None:
            return None  # 창을 못 채웠다(또는 산출에 없다)
        return False

    return NewHighBadges(
        date=d,
        basis="close",
        d120=axis("d120"),
        w52=axis("w52"),
        hist=axis("hist"),
        label=cast(Literal["hist", "w52", "d120"], lab_kind) if lab_kind in _LABELS else None,
        source=label([sd.source]),
        quality=sd.quality,
    )


def stock_header(ctx: ReadContext, code: str) -> Envelope[StockHeader]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    s = lk.snap(code)
    notes: list[str] = []
    quote: SourcedQuote | None = None
    quality: Quality | None = None
    if s is not None:
        last_trade = _last_trade(ctx, code)
        flags = _status_flags(lk, s)
        stale = (last_trade is not None and last_trade < s.date) or bool(
            flags and "halted" in flags
        )
        q = Quality.STALE if stale and s.quality is Quality.OK else s.quality
        quote = SourcedQuote(
            date=s.date,
            close=s.close,
            chg_pct=s.chg_pct,
            volume=s.volume,
            turnover=s.turnover,
            mktcap=s.mktcap,
            last_trade=last_trade,
            stale=stale,
            source=label([s.source]),
            quality=q,
        )
        quality = q
        if stale:
            when = last_trade.isoformat() if last_trade is not None else "모름"
            notes.append(f"{_why_no_trade(flags)} — 마지막 거래 {when}")
    else:
        flags = None
        notes.append(f"시세 없음 — {MARKET_ETA}")
    common = ref.common_code
    data = StockHeader(
        code=code,
        name=ref.name,
        market=ref.market,
        kind=kind_of(ref.kind),
        common_code=common,
        corp_code=ref.corp_code,
        corp_name=ref.corp_name,
        status_flags=flags,
        listed_on=ref.listed_on,
        acc_mt=ref.profile.acc_mt if ref.profile is not None else None,
        induty_code=ref.profile.induty_code if ref.profile is not None else None,
        last=quote,
        is_watch=code in lk.watch,
        detail_tier=common is not None and common in lk.detail_codes,
        newhigh=_newhigh(ctx, code),
    )
    if ref.kind == "pref" and common is None:
        notes.append("우선주 — 보통주 매핑(share_class) 없음: 재무·공시 섹션 없음(R13)")
    srcs = ([s.source] if s is not None else []) + (["DART"] if ref.corp_code else [])
    if quality is None:
        quality = ref.profile.quality if ref.profile is not None else Quality.OK
    return envelope(
        data,
        source=label(srcs),
        as_of=close_as_of(s.date) if s is not None else ctx.now(),
        quality=quality,
        notes=notes,
        generated_at=ctx.now(),
    )


# ── 차트 ──────────────────────────────────────────────────────────────────────────────


def sma(values: Sequence[float | None], n: int) -> list[float | None]:
    """단순 이동평균(산술) — 창 n 을 못 채웠거나 창 안에 None 이 있으면 None(D-P4-21)."""
    if n < 1:
        raise ValueError("창은 1 이상")
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < n:
            out.append(None)
            continue
        win = values[i + 1 - n : i + 1]
        if any(v is None for v in win):
            out.append(None)
            continue
        out.append(sum(cast(Sequence[float], win)) / n)
    return out


def stock_chart(ctx: ReadContext, code: str, *, days: int) -> Envelope[PriceChart]:
    lk = Lookup(ctx)
    lk.ref(code)
    need = days + max(SMA_WINDOWS) - 1
    rows = ctx.repos.market.series([code], ctx.today(), need).get(code, [])
    traded = [b for b in rows if _traded(b)]
    if not traded:
        raise NoData(f"아직 없음 — {MARKET_ETA}(일봉)")
    closes = [b.close for b in traded]
    smas = {str(n): sma(closes, n) for n in SMA_WINDOWS}
    keep = traded[-days:]
    off = len(traded) - len(keep)
    first = keep[0].date
    events = [
        ChartEvent(date=e.effective_date, kind="split", ratio=e.ratio, note=e.note)
        for e in ctx.repos.etf.split_events().get(code, [])
        if first <= e.effective_date <= keep[-1].date
    ]
    data = PriceChart(
        code=code,
        days=days,
        bars=[
            ChartBar(
                date=b.date, o=b.open, h=b.high, l=b.low, c=b.close, v=b.volume, turnover=b.turnover
            )
            for b in keep
        ],
        sma={k: [_finite(v) for v in vs[off:]] for k, vs in smas.items()},
        events=events,
        last_trade=keep[-1].date,
    )
    notes = ["KRX 일별 — 비수정 가격(분할은 events 로 표시)"]
    s = lk.snap(code)
    if s is not None and s.date > keep[-1].date:
        why = _why_no_trade(_status_flags(lk, s))
        notes.append(f"{why} — 마지막 거래 {keep[-1].date.isoformat()}(거래 없는 날은 봉에서 뺐다)")
    return envelope(
        data,
        source=label(b.source for b in keep),
        as_of=close_as_of(keep[-1].date),
        quality=worst(b.quality for b in keep),
        notes=notes,
        generated_at=ctx.now(),
    )


# ── 컨센서스(로그인 — KIS) ────────────────────────────────────────────────────────────


def _na_consensus(code: str, reason: NaReason) -> Consensus:
    return Consensus(
        applicable=False,
        na_reason=reason,
        code=code,
        label=CONSENSUS_LABEL,
        snap_date=None,
        fy=[],
        ntm=None,
        target=None,
        opinions=None,
        revisions=[],
        recent=[],
        lost=list(LOST_BY_KIS),
    )


def _uni_as_of(lk: Lookup, code: str) -> tuple[datetime, str, Quality]:
    u = lk.universe.get(code)
    if u is None:
        return lk.ctx.now(), "KBJ", Quality.OK
    return close_as_of(u.as_of), label([u.source]), u.quality


def _fy(r: ConsensusRow) -> FyEstimateView:
    return FyEstimateView(
        fiscal_ym=r.fiscal_ym,
        fiscal_end=r.fiscal_end,
        is_estimate=r.is_estimate,
        revenue=r.revenue,
        op=r.op,
        ni=r.ni,
        ni_owner=r.ni_owner,
        eps=r.eps,
        ebitda=r.ebitda,
        quality=r.quality,
    )


def stock_consensus(ctx: ReadContext, code: str) -> Envelope[Consensus]:
    """컨센서스(§4.7) — 우선주는 보통주 것. 수집 대상이 아니면 200 적용 불가, 대상인데 스냅이
    없으면 404 no_data(KIS 빈 응답·수집 꺼짐 — 0 을 사실로 쓰지 않는다)."""
    lk = Lookup(ctx)
    ref = lk.ref(code)
    common = ref.need_common()
    cfg = ctx.fin_config().consensus
    u = lk.universe.get(common)
    rule = consensus_na(u) if u is not None else NaReason.NOT_IN_CONSENSUS_UNIVERSE
    t = lk.today
    snaps = ctx.repos.consensus.snapshots(common, t - timedelta(days=CONSENSUS_LOOKBACK_DAYS), t)
    member = common in lk.consensus_universe
    if rule is not None or (not member and not snaps):
        reason = rule or NaReason.NOT_IN_CONSENSUS_UNIVERSE
        as_of, src, q = _uni_as_of(lk, common)
        why = (
            "코넥스 — 컨센서스 없음"
            if reason is NaReason.KONEX_NO_CONSENSUS
            else (
                "컨센서스 수집 대상 아님(보통주·코스피/코스닥 · 관심종목 ∪ 시총 상위 "
                f"{cfg.top_n}) — 관심종목에 넣으면 다음 수집(16:00)"
            )
        )
        return envelope(
            _na_consensus(common, reason),
            source=src,
            as_of=as_of,
            quality=q,
            notes=[why, LOST_NOTE],
            generated_at=ctx.now(),
        )
    if not snaps:
        raise NoData(f"아직 없음 — {CONSENSUS_ETA}")
    rows = latest_snapshot(snaps, t)
    if not rows:
        raise NoData(f"아직 없음 — {CONSENSUS_ETA}")
    snap_date = rows[0].snap_date
    n = ntm(snaps, t)
    window = cfg.opinion_window_days
    ops = ctx.repos.consensus.opinions(common, snap_date - timedelta(days=window), t)
    tgt = ctx.repos.consensus.target(common)
    if tgt is None or tgt.as_of < snap_date:
        tgt = (
            target_consensus(ops, snap_date, window_days=window, min_brokers_ok=cfg.min_brokers_ok)
            or tgt
        )
    close, _ = lk.close(common)
    target = None
    if tgt is not None:
        up = None if close is None or close <= 0 else (tgt.mean / close - 1) * 100
        target = TargetView(
            as_of=tgt.as_of,
            mean=tgt.mean,
            median=tgt.median,
            high=tgt.high,
            low=tgt.low,
            n_brokers=tgt.n_brokers,
            window_days=tgt.window_days,
            upside_pct=_finite(up),
            quality=tgt.quality,
        )
    dist = opinion_counts(ops, snap_date, window_days=window)
    revs = ctx.repos.consensus.revisions(common)
    recent = sorted(ops, key=lambda o: (o.opinion_date, o.broker), reverse=True)[:20]
    stale = snap_date < ctx.cal.prev_trading_day(t)
    data = Consensus(
        code=common,
        label=CONSENSUS_LABEL,
        snap_date=snap_date,
        fy=[_fy(r) for r in rows],
        ntm=None
        if n is None
        else NtmView(
            as_of=n.as_of,
            snap_date=n.snap_date,
            fy1_ym=n.fy1.fiscal_ym,
            fy2_ym=n.fy2.fiscal_ym if n.fy2 is not None else None,
            weight=n.weight,
            revenue=n.revenue,
            op=n.op,
            ni=n.ni,
            eps=n.eps,
            ebitda=n.ebitda,
            quality=n.quality,
        ),
        target=target,
        opinions=OpinionDist(
            buy=dist.buy,
            hold=dist.hold,
            sell=dist.sell,
            unrated=dist.unrated,
            unknown=dist.unknown,
            n_brokers=dist.n_brokers,
            window_days=window,
        ),
        revisions=[
            RevisionView(
                as_of=r.as_of,
                metric=r.metric,
                fiscal_ym=r.fiscal_ym,
                window_days=r.window_days,
                current=r.current,
                baseline=r.baseline,
                baseline_date=r.baseline_date,
                pct=r.pct,
                signal=r.signal,
                quality=r.quality,
            )
            for r in revs
        ],
        recent=[
            OpinionView(
                broker=o.broker,
                opinion_date=o.opinion_date,
                opinion=o.opinion,
                opinion_prev=o.opinion_prev,
                target_price=o.target_price,
                quality=o.quality,
            )
            for o in recent
        ],
        lost=list(LOST_BY_KIS),
    )
    notes = [LABEL_NOTE, LOST_NOTE]
    if not member:
        notes.append("지금은 수집 대상이 아니다 — 마지막 스냅까지")
    if stale:
        notes.append(f"스냅이 오래됐다 — 마지막 {snap_date.isoformat()}(consensus.snapshot 16:00)")
    if code != common:
        notes.append(f"우선주 — 보통주({common}) 컨센서스")
    qs: list[Quality | None] = [r.quality for r in rows]
    qs += [target.quality if target is not None else None, Quality.STALE if stale else None]
    return envelope(
        data,
        source=_parts(r.source for r in rows),
        as_of=close_as_of(snap_date),
        quality=worst(qs),
        notes=notes,
        generated_at=ctx.now(),
    )


# ── 밸류에이션(로그인) ────────────────────────────────────────────────────────────────


def _na_of(row: ValuationDay) -> dict[str, NaReason]:
    raw = row.flags.get("na", {})
    out: dict[str, NaReason] = {}
    if isinstance(raw, dict):
        for k, v in cast(dict[str, object], raw).items():
            out[k] = NaReason(str(v))
    return out


def _out_of_cap(row: ValuationDay) -> set[str]:
    raw = row.flags.get("out_of_cap", [])
    if isinstance(raw, list | tuple):
        return {str(x) for x in cast(Iterable[object], raw)}
    return set()


def _cell(value: float | None, reason: NaReason | None, capped: bool = False) -> MultipleCell:
    if reason is not None:
        return MultipleCell(value=None, applicable=False, na_reason=reason, out_of_cap=False)
    return MultipleCell(value=_finite(value), applicable=True, na_reason=None, out_of_cap=capped)


def _current(row: ValuationDay) -> ValuationCurrent:
    na = _na_of(row)
    cap = _out_of_cap(row)
    cells = {m: _cell(getattr(row, m), na.get(m), m in cap) for m in _MULTIPLES}
    return ValuationCurrent(
        date=row.trade_date,
        price=row.price,
        ttm_ref=row.ttm_ref,
        ev=row.ev,
        per=cells["per"],
        pbr=cells["pbr"],
        psr=cells["psr"],
        ev_ebitda=cells["ev_ebitda"],
        per_fwd=cells["per_fwd"],
        psr_fwd=cells["psr_fwd"],
        ev_ebitda_fwd=cells["ev_ebitda_fwd"],
        quality=row.quality,
    )


def _na_current(day: date, reason: NaReason, price: float | None) -> ValuationCurrent:
    c = _cell(None, reason)
    return ValuationCurrent(
        date=day,
        price=price,
        ttm_ref=None,
        ev=None,
        per=c,
        pbr=c,
        psr=c,
        ev_ebitda=c,
        per_fwd=c,
        psr_fwd=c,
        ev_ebitda_fwd=c,
        quality=Quality.OK,
    )


def _band(b: BandRow) -> BandView:
    return BandView(
        metric=cast(Metric, b.metric),
        as_of=b.as_of,
        p10=b.p10,
        p25=b.p25,
        p50=b.p50,
        p75=b.p75,
        p90=b.p90,
        min=b.min,
        max=b.max,
        n_days=b.n_days,
        n_excluded=b.n_excluded,
        current=b.current,
        current_rank_pct=b.current_rank_pct,
        window_from=b.window_from,
        applicable=b.na_reason is None,
        na_reason=b.na_reason,
        quality=b.quality,
    )


def _na_band(metric: str, day: date, reason: NaReason) -> BandView:
    return BandView(
        metric=cast(Metric, metric),
        as_of=day,
        p10=None,
        p25=None,
        p50=None,
        p75=None,
        p90=None,
        min=None,
        max=None,
        n_days=0,
        n_excluded=0,
        current=None,
        current_rank_pct=None,
        window_from=None,
        applicable=False,
        na_reason=reason,
        quality=Quality.OK,
    )


def _weekly(rows: Sequence[ValuationDay], metric: str) -> list[SeriesPoint]:
    """주 1점(그 주 마지막 거래일) — 밴드 차트(§5.2 series)."""
    last: dict[tuple[int, int], ValuationDay] = {}
    for r in sorted(rows, key=lambda x: x.trade_date):
        iso = r.trade_date.isocalendar()
        last[(iso.year, iso.week)] = r
    return [
        SeriesPoint(
            date=r.trade_date,
            price=_finite(r.price),
            value=_finite(cast(float | None, getattr(r, metric))),
        )
        for _k, r in sorted(last.items())
    ]


def _classes(lk: Lookup, common: str) -> list[ClassCap]:
    out: list[ClassCap] = []
    for c, sc in sorted(lk.classes.items()):
        if sc.common_code != common:
            continue
        s = lk.snap(c)
        out.append(
            ClassCap(
                code=c,
                share_class=sc.share_class,
                close=s.close if s is not None else None,
                shares=s.shares if s is not None else None,
                mktcap=s.mktcap if s is not None else None,
            )
        )
    return out


def _valuation_code(ctx: ReadContext, ref: StockRef) -> str:
    """밸류 행이 있는 코드 — 그 코드가 먼저, 없으면 보통주(회사 배수는 같다)."""
    common = ref.need_common()
    for c in dict.fromkeys((ref.code, common)):
        if ctx.repos.consensus.last_valuation_day(c) is not None or ctx.repos.consensus.bands(c):
            return c
    return common


def stock_valuation(ctx: ReadContext, code: str, *, metric: Metric) -> Envelope[Valuation]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    common = ref.need_common()
    vc = _valuation_code(ctx, ref)
    repo = ctx.repos.consensus
    last = repo.last_valuation_day(vc)
    bands = repo.bands(vc)
    notes: list[str] = ["PIT — 그날까지 공시된 정기보고서 분기만(D-P4-11, ADR 0016)"]
    if last is None and not bands:
        reason = company_na(lk, ref)
        if reason is None:
            raise NoData(f"아직 없음 — {VALUATION_ETA}")
        close, cday = lk.close(code)
        day = cday or lk.today
        data = Valuation(
            applicable=False,
            na_reason=reason,
            code=common,
            metric=metric,
            current=_na_current(day, reason, close),
            bands=[_na_band(m, day, reason) for m in BAND_METRICS],
            series=[],
            mktcap_company=None,
            classes=_classes(lk, common),
        )
        if reason is NaReason.CURRENCY_MISMATCH:
            notes.append("재무 통화가 원화가 아니다 — 환산하지 않는다(배수 산출 안 함)")
        as_of = close_as_of(day)
        return envelope(
            data,
            source=label(["DART"]),
            as_of=as_of,
            quality=Quality.OK,
            notes=notes,
            generated_at=ctx.now(),
        )
    current = None
    rows: list[ValuationDay] = []
    if last is not None:
        start = last - timedelta(days=BAND_SERIES_DAYS)
        rows = repo.valuation_days(vc, start, last)
        today_rows = [r for r in rows if r.trade_date == last]
        if today_rows:
            current = _current(today_rows[-1])
    band_views = [_band(b) for b in sorted(bands, key=lambda b: BAND_METRICS.index(b.metric))]
    cells = [] if current is None else [getattr(current, m) for m in _MULTIPLES[:4]]
    reasons = {c.na_reason for c in cells} | {b.na_reason for b in band_views}
    common_reason = next(iter(reasons)) if len(reasons) == 1 else None
    data = Valuation(
        applicable=common_reason is None,
        na_reason=common_reason,
        code=vc,
        metric=metric,
        current=current,
        bands=band_views,
        series=_weekly(rows, metric),
        mktcap_company=rows[-1].mktcap_company if rows else None,
        classes=_classes(lk, common),
    )
    if current is not None and any(getattr(current, m).out_of_cap for m in _MULTIPLES):
        notes.append("거름값(config/fin.yaml valuation.caps) 밖 배수 — 밴드에서 뺐다")
    if vc != code:
        notes.append(f"우선주 — 회사 배수(보통주 {common} + 우선주 시총)")
    src_rows: list[str] = [rows[-1].source] if rows else []
    src_rows += [b.source for b in bands]
    as_of_day = last if last is not None else max(b.as_of for b in bands)
    qs: list[Quality | None] = [current.quality if current is not None else None]
    qs += [b.quality for b in bands]
    return envelope(
        data,
        source=_parts(src_rows),
        as_of=close_as_of(as_of_day),
        quality=worst(qs),
        notes=notes,
        generated_at=ctx.now(),
    )


# ── 시나리오 적정가(로그인 — 구 이름은 쓰지 않는다, D-P4-12) ──────────────────────────


def _case(r: ScenarioRow) -> ScenarioCase:
    return ScenarioCase(
        case=r.case,
        method=r.method,
        eps=r.eps,
        eps_source=r.eps_source,
        multiple=r.multiple,
        multiple_source=r.multiple_source,
        tp=r.tp,
        upside_pct=_finite(r.upside_pct),
        na_reason=r.na_reason,
        quality=r.quality,
    )


def _na_case(case: Literal["bear", "base", "bull"], reason: NaReason) -> ScenarioCase:
    return ScenarioCase(
        case=case,
        method="INSUFFICIENT",
        eps=None,
        eps_source=None,
        multiple=None,
        multiple_source=None,
        tp=None,
        upside_pct=None,
        na_reason=reason,
        quality=Quality.OK,
    )


def stock_scenario(ctx: ReadContext, code: str) -> Envelope[Scenario]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    common = ref.need_common()
    rows: list[ScenarioRow] = []
    for c in dict.fromkeys((code, common)):
        rows = ctx.repos.consensus.scenarios(c)
        if rows:
            break
    notes: list[str] = list(SCENARIO_NOTES)
    if not rows:
        reason = company_na(lk, ref)
        if reason is None:
            raise NoData(f"아직 없음 — {VALUATION_ETA}")
        close, cday = lk.close(code)
        data = Scenario(
            applicable=False,
            na_reason=reason,
            code=common,
            as_of=None,
            method="INSUFFICIENT",
            cases=ScenarioCases(
                bear=_na_case("bear", reason),
                base=_na_case("base", reason),
                bull=_na_case("bull", reason),
            ),
            price=close,
        )
        return envelope(
            data,
            source=label(["DART"]),
            as_of=close_as_of(cday or lk.today),
            quality=Quality.OK,
            notes=notes,
            generated_at=ctx.now(),
        )
    by = {r.case: r for r in rows}
    base = by.get("base") or rows[0]
    method = base.method
    insufficient = method == "INSUFFICIENT"
    reason = base.na_reason if insufficient else None
    if insufficient and reason is None:
        reason = next((r.na_reason for r in rows if r.na_reason is not None), None)
    data = Scenario(
        applicable=not insufficient,
        na_reason=reason,
        code=base.code,
        as_of=base.as_of,
        method=method,
        cases=ScenarioCases(
            bear=_case(by["bear"]) if "bear" in by else None,
            base=_case(by["base"]) if "base" in by else None,
            bull=_case(by["bull"]) if "bull" in by else None,
        ),
        price=base.price if base.price is not None else lk.close(code)[0],
    )
    if method == "PBR":
        notes.append("PBR 폴백 — Base EPS 가 없거나(적자) PER 밴드가 없다(BPS × PBR 밴드)")
    if code != common:
        notes.append(
            f"우선주 — 회사(보통주 {common}) 기준 EPS·배수, 적정가·업사이드는 보통주 종가 기준"
        )
    return envelope(
        data,
        source=_parts(r.source for r in rows),
        as_of=close_as_of(base.as_of),
        quality=worst(r.quality for r in rows),
        notes=notes,
        generated_at=ctx.now(),
    )
