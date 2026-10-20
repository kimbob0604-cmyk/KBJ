"""테마·밸류체인 reader — `/api/themes`·`/api/themes/strength`·`/api/themes/{id}`(ADR 0039,
docs/metrics.md §31).

- `/api/themes`: 대테마 분류(공개 표 `pub_themes.theme` 만 — 공개 파일 `themes.json` 과 같은 모양).
- `/api/themes/strength`: 대테마·밸류체인 강도 순위(로그인) — 편입(`prv_themes.*`)과 보드 종목 하루
  (`prv_board.stock_day` — KRX/KIS 정본 일봉에서 보드가 계산한 등락·수익률·시총·신고가 라벨)를 엔진
  `kbj.engines.theme.strength` 가 묶는다. 저장 표 없음(읽을 때 계산 — 수십 대테마 × 수천 종목).
- `/api/themes/{id}`: 대테마 하나의 네이버 테마·편입 종목·사유·종목별 등락(로그인).
- DB 만 읽는다(계약 ⑩). 품질: 분류 판(built_at)이 `config/theme.yaml stale_days` 보다 오래되면
  stale, 공개 분류 판과 로그인 편입 판의 built_at 이 다르면 stale + notes(두 작업 사이·한쪽 실패).
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Final, Literal

from kbj.config.theme import load_theme
from kbj.core.calendar import EQUITY_CLOSE
from kbj.core.quality import Quality
from kbj.core.rows import StockDay
from kbj.core.theme_rows import SOURCE_LOGIN, SOURCE_MASTER, MasterLoad, ThemeRow
from kbj.core.time import KST
from kbj.engines.theme.strength import (
    Strength,
    chain_strengths,
    codes_by_theme,
    naver_by_theme,
    rank,
    stock_items,
    theme_strengths,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.theme import (
    ChainStrengthRow,
    NaverThemeRef,
    ThemeCatalog,
    ThemeDetail,
    ThemeInfo,
    ThemeLeader,
    ThemeMember,
    ThemeMemberReason,
    ThemeSection,
    ThemeStats,
    ThemeStrength,
    ThemeStrengthRow,
    ValueChainGroup,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst
from kbj.services.api.readers.board import BOARD_DAILY_ETA

__all__ = [
    "NOT_A_THEME",
    "NO_BOARD",
    "NO_CLASSES",
    "NO_MASTER",
    "catalog_data",
    "theme_catalog",
    "theme_detail",
    "theme_strength",
]

NO_CLASSES: Final = "아직 없음 — theme.classes_sync 일 12:40(테마 마스터 주 1회)"
NO_MASTER: Final = "아직 없음 — theme.master_sync 일 12:40(편입 종목 — 테마 마스터 주 1회)"
NO_BOARD: Final = f"아직 없음 — 테마 강도는 보드 종목 하루로 계산({BOARD_DAILY_ETA})"
NOT_A_THEME: Final = "대테마 없음 — 분류에 없는 id"
NOTE_CLASSES: Final = "대테마 분류는 사용자 작성(stock-dashboard 테마 마스터 — 주 1회)"
NOTE_MEMBERS: Final = "편입 = 대테마에 걸린 네이버 테마(stock-dashboard 수집)의 종목 합집합"
NOTE_METHOD: Final = (
    "등락은 편입 종목 동일가중 평균(대형주 하나가 테마를 덮지 않게) · "
    "20일 = 21거래일 전 종가 대비(보드)"
)
NOTE_MISMATCH: Final = "공개 분류 판과 편입 판의 기준 시각이 다르다 — 다음 theme.*_sync 뒤 맞춰진다"
_STALE_FALLBACK_DAYS: Final = 15
# 강도의 시세 날: 종목 하루가 빈 보드 날을 건너뛰며 거슬러 볼 최대 보드 날 수(약 2주)
_BOARD_LOOKBACK: Final = 10


@functools.cache
def _stale_days() -> int:
    try:
        return load_theme().master.stale_days
    except (OSError, ValueError):  # 설정 파일이 없는 실행 — 기본값(문서 [제안] 값과 같다)
        return _STALE_FALLBACK_DAYS


def _built_at(rows: Sequence[ThemeRow]) -> datetime:
    """분류 판의 기준 시각(KST — 공개 파일 `themes.json` 과 같은 글자)."""
    return max(r.as_of for r in rows).astimezone(KST)


def _age_quality(ctx: ReadContext, built: datetime, base: Quality) -> Quality:
    if ctx.now() - built > timedelta(days=_stale_days()):
        return worst([base, Quality.STALE]) or Quality.STALE
    return base


def _themes(ctx: ReadContext) -> list[ThemeRow]:
    rows = ctx.repos.theme_pub.themes()
    if not rows:
        raise NoData(NO_CLASSES)
    return rows


def catalog_data(rows: Sequence[ThemeRow]) -> ThemeCatalog:
    """대테마 → 분류 응답(섹션은 원천 첫 등장 순서, 밸류체인은 이름순 — 공개 파일과 같은 규칙)."""
    ordered = sorted(rows, key=lambda r: (r.ord, r.id))
    sections: dict[str, list[ThemeInfo]] = {}
    chains: dict[str, list[str]] = {}
    for r in ordered:
        sections.setdefault(r.section, []).append(
            ThemeInfo(id=r.id, name=r.name, section=r.section, valuechain=r.valuechain)
        )
        if r.valuechain is not None:
            chains.setdefault(r.valuechain, []).append(r.id)
    return ThemeCatalog(
        built_at=_built_at(ordered),
        n_themes=len(ordered),
        sections=[ThemeSection(section=k, themes=v) for k, v in sections.items()],
        valuechains=[ValueChainGroup(key=k, themes=chains[k]) for k in sorted(chains)],
    )


def theme_catalog(ctx: ReadContext) -> Envelope[ThemeCatalog]:
    rows = _themes(ctx)
    data = catalog_data(rows)
    q = worst(r.quality for r in rows)
    return envelope(
        data,
        source=SOURCE_MASTER,
        as_of=data.built_at,
        quality=_age_quality(ctx, data.built_at, q or Quality.INVALID),
        notes=[NOTE_CLASSES],
        generated_at=ctx.now(),
    )


def _master(ctx: ReadContext) -> MasterLoad:
    load = ctx.repos.theme.last_load()
    if load is None:
        raise NoData(NO_MASTER)
    return load


def _board_day(ctx: ReadContext, *, required: bool) -> tuple[date | None, dict[str, StockDay]]:
    """오늘 이하에서 종목 하루가 있는 가장 최근 보드 날(주말·휴장·오늘 보드 전이면 지난 거래일).

    산출 JSON 만 있고 종목 하루가 빈 날(중간에 멈춘 실행 등)은 건너뛰고 그 앞 보드 날을 본다 —
    최대 `_BOARD_LOOKBACK` 보드 날까지. 화면은 응답의 `trade_date`·notes 로 기준 날을 보인다."""
    upto = ctx.today()
    for _ in range(_BOARD_LOOKBACK):
        day = ctx.repos.board.last_day(upto)
        if day is None:
            break
        days = ctx.repos.board.stock_days(day)
        if days:
            return day, days
        upto = day - timedelta(days=1)
    if required:
        raise NoData(NO_BOARD)
    return None, {}


def _board_as_of(ctx: ReadContext, day: date) -> datetime:
    art = ctx.repos.board.artifact(day, "newhigh")
    return art.as_of if art is not None else datetime.combine(day, EQUITY_CLOSE, tzinfo=KST)


def _stats(s: Strength) -> ThemeStats:
    lead = (
        ThemeLeader(
            code=s.leader_code, name=s.leader_name or s.leader_code, chg_pct=s.leader_chg_pct
        )
        if s.leader_code is not None
        else None
    )
    return ThemeStats(
        n_members=s.n_members, n_priced=s.n_priced, chg_pct=s.chg_pct, ret_5d=s.ret_5d,
        ret_20d=s.ret_20d, weighted=s.weighted, up=s.up, flat=s.flat, down=s.down,
        unknown=s.unknown, up_ratio=s.up_ratio, n_newhigh=s.n_newhigh, n_near=s.n_near,
        turnover_mult=s.turnover_mult, mktcap=s.mktcap, leader=lead,
    )  # fmt: skip


def _login_notes(themes: Sequence[ThemeRow], load: MasterLoad) -> tuple[list[str], bool]:
    mismatch = _built_at(themes) != load.built_at
    notes = [NOTE_MEMBERS, NOTE_METHOD]
    if mismatch:
        notes.append(NOTE_MISMATCH)
    return notes, mismatch


def _login_quality(
    ctx: ReadContext, themes: Sequence[ThemeRow], load: MasterLoad, days: Mapping[str, StockDay]
) -> Quality:
    _, mismatch = _login_notes(themes, load)
    q = worst([load.quality, *(r.quality for r in themes), *(s.quality for s in days.values())])
    q = _age_quality(ctx, min(load.built_at, _built_at(themes)), q or Quality.INVALID)
    if mismatch:
        return worst([q, Quality.STALE]) or Quality.STALE
    return q


def theme_strength(ctx: ReadContext, *, period: Literal[1, 5, 20]) -> Envelope[ThemeStrength]:
    themes = _themes(ctx)
    load = _master(ctx)
    day, days = _board_day(ctx, required=True)
    if day is None:  # required=True 면 _board_day 가 NoData 를 던진다(형 좁히기)
        raise NoData(NO_BOARD)
    universe = {s.code for s in ctx.repos.theme.stocks()}
    codes = codes_by_theme(ctx.repos.theme.links(), ctx.repos.theme.members(), universe or None)
    items = stock_items(days)
    strengths = theme_strengths(themes, codes, items)
    rows = [
        ThemeStrengthRow(
            rank=i,
            id=t.id,
            name=t.name,
            section=t.section,
            valuechain=t.valuechain,
            stats=_stats(strengths[t.id]),
        )
        for i, t in enumerate(rank(themes, strengths, period), start=1)
    ]
    chains = [
        ChainStrengthRow(key=c.key, themes=list(c.themes), stats=_stats(c.strength))
        for c in chain_strengths(themes, codes, items)
    ]
    notes, _ = _login_notes(themes, load)
    data = ThemeStrength(
        trade_date=day, period=period, built_at=load.built_at, rows=rows, chains=chains
    )
    return envelope(
        data,
        source=f"{label(s.source for s in days.values())}+{SOURCE_LOGIN}",
        as_of=_board_as_of(ctx, day),
        quality=_login_quality(ctx, themes, load, days),
        notes=[*notes, f"시세 {day.isoformat()} 마감(보드)"],
        generated_at=ctx.now(),
    )


def theme_detail(ctx: ReadContext, *, theme_id: str) -> Envelope[ThemeDetail]:
    themes = _themes(ctx)
    t = next((r for r in themes if r.id == theme_id), None)
    if t is None:
        raise NoData(NOT_A_THEME)
    load = _master(ctx)
    links = ctx.repos.theme.links(theme_id)
    nos = naver_by_theme(links).get(theme_id, [])
    naver = {n.no: n for n in ctx.repos.theme.naver_themes(nos)}
    members = ctx.repos.theme.members(nos)
    stocks = {s.code: s for s in ctx.repos.theme.stocks({m.code for m in members})}
    groups = {g.id: g.name for g in ctx.repos.theme.groups()}
    day, days = _board_day(ctx, required=False)
    by_code: dict[str, list[tuple[int, str | None]]] = {}
    per_no: dict[int, int] = {}
    for m in members:
        if m.code not in stocks:
            continue
        by_code.setdefault(m.code, []).append((m.naver_no, m.reason))
        per_no[m.naver_no] = per_no.get(m.naver_no, 0) + 1
    order = {no: i for i, no in enumerate(nos)}
    rows: list[ThemeMember] = []
    for code, refs in by_code.items():
        s = stocks[code]
        bd = days.get(code)
        refs.sort(key=lambda x: order.get(x[0], len(order)))
        lab = bd.label if bd is not None and bd.label in ("hist", "w52", "d120") else None
        rows.append(
            ThemeMember(
                code=code,
                name=s.name,
                market=s.market,
                type=s.type,
                industry_group=groups.get(s.industry_group or "", s.industry_group),
                naver=[no for no, _ in refs],
                reasons=[ThemeMemberReason(naver_no=no, reason=r) for no, r in refs],
                chg_pct=None if bd is None else bd.chg_pct,
                ret_5d=None if bd is None else bd.ret_5d,
                ret_20d=None if bd is None else bd.ret_21d,
                mktcap=None if bd is None else bd.mktcap,
                label=lab,  # pyright: ignore[reportArgumentType] — 위에서 셋 중 하나로 좁혔다
            )
        )
    rows.sort(key=lambda r: (r.mktcap is None, -(r.mktcap or 0), r.code))
    stats = None
    if days:
        st = theme_strengths([t], {t.id: list(by_code)}, stock_items(days))[t.id]
        stats = _stats(st)
    refs_out = [
        NaverThemeRef(
            no=no,
            name=naver[no].name if no in naver else f"#{no}",
            count=naver[no].count if no in naver else None,
            in_master=naver[no].in_master if no in naver else None,
            n_members=per_no.get(no, 0),
        )
        for no in nos
    ]
    notes, _ = _login_notes(themes, load)
    if day is None:
        notes.append(f"종목별 등락 없음 — {NO_BOARD.removeprefix('아직 없음 — ')}")
    else:
        notes.append(f"시세 {day.isoformat()} 마감(보드)")
    src = SOURCE_LOGIN if not days else f"{label(s.source for s in days.values())}+{SOURCE_LOGIN}"
    data = ThemeDetail(
        id=t.id, name=t.name, section=t.section, valuechain=t.valuechain,
        built_at=load.built_at, trade_date=day, naver=refs_out, members=rows, stats=stats,
    )  # fmt: skip
    return envelope(
        data,
        source=src,
        as_of=_board_as_of(ctx, day) if day is not None else load.built_at,
        quality=_login_quality(ctx, themes, load, days),
        notes=notes,
        generated_at=ctx.now(),
    )
