"""
신고가 엔진 — 이 프로젝트의 심장.

KBJ 정본(docs/p3_design.md §4.1, docs/metrics.md §7, ADR 0017). ET
`board/engine/newhigh.py`(410줄)를
옮겼다(D-P3-10). 옮길 때 바꾼 것:
  - `rows` 는 ET 의 dict 행(`asof` 키 — 골든 입력) 또는 `kbj.core.rows.Bar` 를 받는다(`as_rows`).
  - `hist_ref_for` 가 스칼라의 `history_from`(시장 일봉을 받아 둔 첫날)과 상장일을 본다(D-P3-11 —
    이력이 상장일까지 닿지 않은 종목은 역사적 신고가를 계산하지 않고 사유를 돌려준다. 메인 결정 R8).
  - **신고가 3축(ADR 0017 — 사용자 요청 2026-10-08)**: 축은 d120·w52·hist 이고 d60(직전 60봉)·
    w52=252봉 정의는 없앴다. 창은 '판정일 당일' 기준이고 당일 봉은 창에 넣지 않는다.

정의를 바꾸려면 docs/metrics.md §7 과 결정 기록(docs/adr)을 함께 고쳐야 한다. 여기만 고치면 안 된다.

  120일 신고가   판정일 직전 120 **시장 거래일**(KRX 거래일 달력 — `market_days`) 창의 최고가 초과.
                창의 경계는 시장 거래일로 정하고 그 안에 있는 그 종목의 봉만 본다(거래정지로 창이
                늘어나지 않는다). 설정 newhigh.lookback_trading_days.d120
  52주 신고가    달력 52주 — 판정일 − 364일 ≤ 봉 날짜 < 판정일 창의 최고가 초과.
                설정 newhigh.lookback_calendar_days.w52
  역사적 신고가   상장 이후 전체(판정일 전까지) 최고가 초과 — 스칼라(`roll_alltime`). 이력이 상장일
                또는 원천 바닥(가장 이른 봉)까지 닿지 않으면 판정하지 않는다(`hist_depth`)
  창 못 채움     종목의 (수정주가 이상 지점 이후) 첫 봉이 창의 첫 시장 거래일보다 뒤면 그 축은 None
                (상장 120거래일 미만 등 — 지어내지 않는다). 신규상장은 hist 로만 판정될 수 있다
  비교          당일 값 > 창 최고가(엄격 — 같은 값은 신고가가 아니다. ET 원본 그대로)
  종가/고가 기준  둘 다 계산해 별도로 저장. 리포트 기본값은 설정(default_basis — 종가)
  라벨 우선순위   역사적 > 52주 > 120일. 상위가 하위를 포함(52주 ≈ 250거래일 ⊇ 120거래일)
  신규/이어감    직전 영업일에 같은(또는 상위) 라벨이 없었으면 신규
  갭            (기준 최고가 - 현재가) / 기준 최고가
  5일 축소폭     5영업일 전 갭 - 현재 갭. 음수일수록 빠르게 좁혀짐
  저항두께       현재가~기준 최고가 구간의 누적 거래량 / 20일 평균 거래량
  거래량 배수    당일 거래량 / 20일 평균 거래량
  재료 반납      (고가 - 종가) / (고가 - 전일종가) >= 0.7

계산되지 않는 값은 None 으로 두고 절대 채워 넣지 않는다 (CLAUDE.md 2장 1번).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from itertools import pairwise
from typing import Any, Final

from kbj.core.rows import AllTime, Bar

__all__ = [
    "BASES",
    "CALENDAR_KEY",
    "HIST_BEFORE_LISTING",
    "HIST_LISTING_UNKNOWN",
    "TRADING_KEY",
    "MarketDays",
    "alltime_dict",
    "as_row",
    "as_rows",
    "continuity",
    "displayable",
    "evaluate",
    "hist_depth",
    "hist_ref_for",
    "kinds",
    "lookbacks",
    "market_days",
    "proximity_kind",
    "rank_of",
    "restate_last_day",
    "roll_alltime",
    "split_guard",
    "window_bounds",
]

BASES = ("high", "close")

# D-P3-11 사유(골든에는 없다 — 골든 입력에 history_from 이 없다)
HIST_BEFORE_LISTING = "이력이 상장일 전에서 끊김"
HIST_LISTING_UNKNOWN = "상장일을 몰라 이력이 상장 이후 전체인지 확인하지 못함"

# 룩백 설정 키(ADR 0017). 거래일 창은 시장 거래일 수, 달력 창은 달력 일수. 'lookback' 은 옛 키 —
# legacy 미국장 설정(us.yaml)만 쓴다(그 종목 봉 수 = 거래일로 읽는다). config/board.yaml 은 쓰지
# 않는다.
TRADING_KEY: Final = "lookback_trading_days"
CALENDAR_KEY: Final = "lookback_calendar_days"
_OLD_KEY: Final = "lookback"


def as_row(r: Mapping[str, Any] | Bar) -> Mapping[str, Any]:
    """일봉 한 줄을 ET 모양 dict(`asof` ISO 문자열 키)로. dict 는 그대로 돌려준다."""
    if isinstance(r, Bar):
        return dict(
            asof=r.date.isoformat(),
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
        )
    return r


def as_rows(rows: Iterable[Mapping[str, Any] | Bar]) -> list[Mapping[str, Any]]:
    return [as_row(r) for r in rows]


# 라벨 종류는 config/board.yaml 이 정한다. 코드에 박지 않는다.
# 'hist' 는 룩백 창이 아니라 스칼라라 lookback_* 에 없고 priority 에만 있다.


def lookbacks(cfg) -> dict[str, tuple[str, int]]:
    """{라벨: ('trading'|'calendar', n)} — 우선순위 순서. hist 는 없다(스칼라)."""
    nhc = cfg["newhigh"]
    got: dict[str, tuple[str, int]] = {}
    for key, unit in ((_OLD_KEY, "trading"), (TRADING_KEY, "trading"), (CALENDAR_KEY, "calendar")):
        for kind, n in (nhc.get(key) or {}).items():
            got[kind] = (unit, int(n))
    order = {k: i for i, k in enumerate(nhc["priority"])}
    return dict(sorted(got.items(), key=lambda kv: order.get(kv[0], len(order))))


class MarketDays:
    """시장 거래일 달력(오름차순 ISO 날짜 — 중복 없음). 창의 경계를 정한다(ADR 0017).

    kbj 서비스는 KRX 거래일 달력(`kbj.core.calendar`)을, 골든·legacy 는 전 종목 일봉 날짜의
    합집합(그날 한 종목이라도 거래했으면 시장 거래일)을 넣는다."""

    __slots__ = ("days",)

    def __init__(self, days: Iterable[date | str]) -> None:
        self.days: tuple[str, ...] = tuple(
            sorted({d.isoformat() if isinstance(d, date) else str(d) for d in days})
        )

    def __len__(self) -> int:
        return len(self.days)

    def __repr__(self) -> str:
        return (
            f"MarketDays({self.days[0] if self.days else ''}~{self.days[-1] if self.days else ''})"
        )


def market_days(days: Iterable[date | str] | MarketDays) -> MarketDays:
    return days if isinstance(days, MarketDays) else MarketDays(days)


def window_bounds(cal: MarketDays, day: str, cfg) -> dict[str, tuple[str, str] | None]:
    """판정일 `day` 의 룩백 창 {라벨: (창 시작일(포함), 덮기 기준일) 또는 None}. 창 = [시작, day).

    - 거래일 창(n): 시작 = 판정일 전 n번째 시장 거래일. 달력이 거기까지 닿지 않으면 None.
    - 달력 창(n): 시작 = 판정일 − n일. 덮기 기준일 = 창 안의 첫 시장 거래일(달력이 시작일 전까지
      닿지 않으면 시작일 그대로 — 그 종목이 시작일 이전 봉을 가져야 덮는다).

    종목은 (수정주가 이상 지점 이후) 첫 봉이 덮기 기준일 이하일 때만 그 창을 채운다(`_window`).
    """
    ds = cal.days
    pos = bisect_left(ds, day)  # 판정일 전 시장 거래일 수
    out: dict[str, tuple[str, str] | None] = {}
    for kind, (unit, n) in lookbacks(cfg).items():
        if unit == "trading":
            if n < 1 or pos < n:
                out[kind] = None
                continue
            s = ds[pos - n]
            out[kind] = (s, s)
            continue
        s = (date.fromisoformat(day) - timedelta(days=n)).isoformat()
        i = bisect_left(ds, s)
        known = bool(ds) and ds[0] <= s
        out[kind] = (s, ds[i] if known and i < pos else s)
    return out


def kinds(cfg):
    return tuple(cfg["newhigh"]["priority"])


def rank_of(cfg):
    return {k: i for i, k in enumerate(cfg["newhigh"]["priority"])}


def displayable(cfg):
    """표에 올릴 라벨 집합.

    `min_display_kind` 아래 등급은 계산만 하고 표에서 뺀다. 20일이 있던 시절
    그 용도였는데 20일 자체를 없앴으므로(D-071) 지금은 계산한 셋이 모두 오른다.
    손잡이는 남겨 둔다 — 창을 다시 늘릴 때 필요하다."""
    pri = cfg["newhigh"]["priority"]
    cut = cfg["newhigh"].get("min_display_kind")
    return set(pri if not cut or cut not in pri else pri[: pri.index(cut) + 1])


# ─────────────────────────── 수정주가 가드 ───────────────────────────
def split_guard(rows, ratio):
    """수정주가 미반영 시계열을 잡는다 (CLAUDE.md 9장 미확정 1번).

    국내 가격제한폭은 +-30% 다. 하루 사이 종가가 그 이상 변했다면 실제 등락이
    아니라 액면분할·무상증가·주식병합이 시계열에 반영되지 않은 것이다.
    이 종목은 역사적 신고가 판정에서 빼고, 그 사실을 리포트에 노출한다.

    가장 마지막 이상 지점의 인덱스를 함께 돌려준다. 그 이전 구간은 현재 주가와
    단위가 다르므로 120일·52주 룩백 창에서도 잘라내야 한다. 자르지 않으면 분할
    직후 종목은 분할 전 가격을 최고가로 들고 있어 몇 달간 신고가가 뜨지 않는다.

    반환: (suspect, 발생일, 사유, 이후_시작_인덱스)
    """
    hit = None
    for i, (a, b) in enumerate(pairwise(rows)):
        pa, pb = a.get("close"), b.get("close")
        if not pa or not pb or pa <= 0:
            continue
        r = pb / pa - 1.0
        if abs(r) > ratio:
            hit = (b["asof"], f"{a['asof']}→{b['asof']} 종가 {r * 100:+.1f}%", i + 1)
    if hit:
        return True, hit[0], hit[1], hit[2]
    return False, None, "", 0


# ─────────────────────────── 보조 계산 ───────────────────────────
def _key(basis):
    return "high" if basis == "high" else "close"


def _prev_bars(rows, idx, n, floor=0):
    """당일 제외, 그 종목의 직전 n봉 슬라이스(거래량 평균 — 신고가 창이 아니다).

    floor 는 수정주가 이상 지점 이후의 시작 인덱스다. 창이 floor 를 넘어
    과거로 가야 채워지는 경우에는 계산하지 않는다 (None). 단위가 다른 구간의
    값을 섞지 않기 위해서다.
    """
    if idx - n < floor:
        return None
    return rows[idx - n : idx]


def _window(rows, dates, idx, bound, floor=0):
    """신고가 룩백 창의 봉(당일 제외). 창을 못 채우면 None (ADR 0017).

    bound = `window_bounds` 의 (시작일, 덮기 기준일). 그 종목의 쓸 수 있는 첫 봉(수정주가 이상
    지점 이후 — floor)이 덮기 기준일보다 뒤면 창을 못 채운 것이다(상장·이력이 짧거나, 분할 전
    가격을 섞어야 채워진다). 창 안의 봉은 [시작일, 판정일) 의 그 종목 봉 전부 — 거래정지로 빈
    날은 비어 있을 뿐 창을 과거로 늘리지 않는다.
    """
    if bound is None or floor >= len(rows) or idx <= floor:
        return None
    start, cover = bound
    if dates[floor] > cover:
        return None
    lo = max(floor, bisect_left(dates, start, 0, idx))
    return rows[lo:idx]


def _max(win, key):
    vals = [r[key] for r in win if r.get(key)]
    return max(vals) if vals else None


def _avg_vol(rows, idx, n, floor=0):
    """직전 n영업일 평균 거래량. 창을 못 채우면 None.

    예전에는 못 채우면 있는 만큼으로 평균을 냈다. 그러면 17~19일 정지 후
    평소와 같은 거래량으로 재개해도 거래량 배수가 6~20배로 나와 탐지기 4가
    터진다(정지 기간의 0 이 분모에 들어가서). D-006 이 '직전 20영업일'이라고
    못박았고 2장 1번은 계산 안 된 값을 내지 말라고 한다.
    """
    win = _prev_bars(rows, idx, n, floor)
    if not win:
        return None
    vals = [r["volume"] for r in win if r.get("volume") is not None]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    return m or None


def _gap(ref, cur):
    """(기준 최고가 - 현재가) / 기준 최고가, %. 이미 넘겼으면 음수."""
    if not ref or ref <= 0 or cur is None:
        return None
    return (ref - cur) / ref * 100.0


def _windows_at(rows, dates, idx, cal, cfg, floor=0):
    """idx 봉 날짜를 판정일로 한 룩백 창의 봉 {라벨: 봉 목록 또는 None}."""
    bounds = window_bounds(cal, dates[idx], cfg)
    return {kind: _window(rows, dates, idx, b, floor) for kind, b in bounds.items()}


def _refs_at(wins, basis, hist_ref):
    """창들(당일 제외)의 기준 최고가 + 역사적 스칼라."""
    k = _key(basis)
    out = {kind: (_max(win, k) if win else None) for kind, win in wins.items()}
    out["hist"] = (hist_ref or {}).get(basis)
    return out


def _resistance(rows, idx, win, cur, ref, avg20, floor=0):
    """현재가~기준 최고가 구간에 쌓인 매물의 두께.

    **봉의 고가~저가 범위가 그 구간과 겹치면 그 봉의 거래량을 센다.**

    처음에는 종가가 구간 안에 드는 봉만 셌는데, 고가 기준에서는 그 값이 거의
    항상 0 이 나왔다 (2026-08-27 실행에서 근접 종목 대부분이 '얇음 0.0').
    당연한 결과다 — 구간이 [당일 고가, 기간 최고가]인데 종가는 체계적으로
    고가보다 아래라 그 좁은 띠에 거의 들어오지 않는다. 기준과 판정에 서로 다른
    가격을 쓴 것이 문제였다.

    범위 겹침으로 보면 기준(고가/종가)과 무관하게 같은 뜻이 되고, 매물대라는
    개념 자체와도 맞는다. 하루 안에서 어느 가격에 얼마나 체결됐는지는 일봉으로
    알 수 없으므로 봉 전체 거래량을 세는 근사다. 그래서 이 값은 추정치다.

    룩백 창은 해당 라벨의 창(win)을 쓰고, 역사적은(win=None) DB 가 들고 있는 구간 전체를
    쓰므로 window_days 를 함께 돌려준다.
    """
    if not ref or not cur or not avg20 or ref <= cur:
        return None
    if win is None:
        win = rows[floor:idx]
    if not win:
        return None
    vol = 0.0
    for r in win:
        v = r.get("volume")
        if v is None:
            continue
        lo, hi = r.get("low"), r.get("high")
        if lo is None or hi is None:
            lo = hi = r.get("close")  # 고저가가 없으면 종가 한 점으로 본다
            if lo is None:
                continue
        if hi >= cur and lo <= ref:  # 봉의 범위가 구간과 겹치면 센다
            vol += v
    return dict(value=round(vol / avg20, 2), window_days=len(win))


def _bucket(v, cfg):
    if v is None:
        return None
    r = cfg["resistance"]
    return "얇음" if v < r["thin_below"] else ("두꺼움" if v > r["thick_above"] else "보통")


# ─────────────────────────── 종목 1개 평가 ───────────────────────────
def evaluate(rows, asof, cfg, hist_ref=None, hist_days=None, split_cleared=False, calendar=None):
    """한 종목의 기준일 신고가 지표 전부.

    rows           오름차순 일봉 [{asof,open,high,low,close,volume}]
    calendar       시장 거래일(`MarketDays` 또는 날짜 목록) — 거래일 창의 경계(ADR 0017). 없으면 그
                   종목 봉 날짜로 대신한다(그러면 거래정지 기간만큼 창이 늘어난다 — 보드 계산
                   `compute_day` 는 늘 시장 달력을 넣는다. legacy 미국장처럼 종목 하나만 보는
                   호출의 예전 동작)
    hist_ref       {'high':x,'close':y} 역사적 최고가 스칼라. 없으면 hist 판정 생략
    hist_days      상장 이후 누적 영업일 수. DB 가 일부 구간만 들고 있어도 되도록 분리
    split_cleared  공시 대조로 '분할이 아니다' 가 확인된 종목 (D-056). 계단은
                   실재하는 등락이므로 룩백을 자르지도, 역사적 판정을 막지도
                   않는다. 조회에 실패한 종목에는 절대 주지 않는다 — 못 본 것과
                   아닌 것은 다르다.
    반환           dict. 기준일 봉이 없으면 None
    """
    rows = as_rows(rows)
    asof = asof.isoformat() if isinstance(asof, date) else asof
    dates = [r["asof"] for r in rows]
    idx = next((i for i, d in enumerate(dates) if d == asof), None)
    if idx is None:
        return None
    cal = market_days(calendar if calendar is not None else dates)
    today = rows[idx]
    prev = rows[idx - 1] if idx > 0 else None

    # 직전 행이 달력상 얼마나 떨어져 있는지. 거래정지로 봉이 비면 그 공백
    # 전체의 수익률이 '당일 등락률'로 나간다. 15일 정지 후 재개한 종목의
    # +30% 가 당일 등락률로 섹터 시총가중 평균에 들어가는 식이다.
    gap_days = None
    if prev:
        try:
            gap_days = (date.fromisoformat(today["asof"]) - date.fromisoformat(prev["asof"])).days
        except (ValueError, TypeError):
            gap_days = None

    gi = cfg["integrity"]
    suspect, sdate, snote, floor = split_guard(rows, gi["split_guard_ratio"])
    if suspect and split_cleared:
        # 공시가 없다고 확인된 계단이다. 실제 등락이므로 그 이전 구간도 지금
        # 주가와 같은 단위다 — 자를 이유가 없다. 사유는 남겨서 화면이 왜
        # 통과시켰는지 설명할 수 있게 한다.
        suspect, floor = False, 0
        snote = f"{snote} — 공시 대조 결과 분할이 아니다 (D-056)"
    n_days = hist_days if hist_days is not None else idx + 1
    # 이상 지점 이후로만 룩백을 허용한다. 분할 전 가격은 지금 주가와 단위가 다르다.
    usable = idx - floor

    avg20 = _avg_vol(rows, idx, cfg["volume"]["avg_days"], floor)
    # 거래량 0 은 '거래 없음'이라는 사실이다. None(못 받음)과 다르다.
    vol_mult = (
        round(today["volume"] / avg20, 2) if (avg20 and today.get("volume") is not None) else None
    )

    chg = None
    if prev and prev.get("close") and today.get("close") is not None:
        chg = round((today["close"] / prev["close"] - 1) * 100, 2)

    # 재료 반납 — 분모가 0 이하면 그날 재료 자체가 없었던 것이라 판정하지 않는다.
    # 리포트에 쓰는 값은 비율이 아니라 %p 다. 레퍼런스 코멘트가 그렇게 쓴다:
    #   "고가 대비 종가 괴리 11.2%p" = 고가 등락률 +12.65% - 종가 등락률 +1.45%
    give = high_chg = give_pp = None
    if prev and today.get("high") is not None and prev.get("close"):
        high_chg = round((today["high"] / prev["close"] - 1) * 100, 2)
        denom = today["high"] - prev["close"]
        # 종가를 못 받았으면 반납은 계산되지 않는다. 예전 판은 종가를 안 보고
        # 뺄셈에 넣어 TypeError 로 그날 평가가 통째로 죽었고, 바로 다음 줄은
        # chg 가 None 이면 0 으로 대신해 give_pp 에 high_chg 를 그대로 실었다 —
        # '고가 대비 종가 괴리 %p' 자리에 고가 등락률이 사실처럼 찍힌다.
        # 계산되지 않은 값은 출력하지 않는다 (CLAUDE.md 2장 1번).
        if denom > 0 and today.get("close") is not None:
            give = round((today["high"] - today["close"]) / denom, 3)
            if chg is not None:
                give_pp = round(high_chg - chg, 2)

    out: dict[str, Any] = dict(
        asof=asof,
        open=today.get("open"),
        high=today.get("high"),
        low=today.get("low"),
        close=today.get("close"),
        volume=today.get("volume"),
        chg_pct=chg,
        vol_mult=vol_mult,
        avg_vol_20=avg20,
        giveback=give,
        giveback_pp=give_pp,
        high_chg_pct=high_chg,
        prev_asof=prev["asof"] if prev else None,
        prev_gap_days=gap_days,
        n_days=n_days,
        usable_days=usable,
        split_floor=floor,
        suspect=suspect,
        suspect_date=sdate,
        suspect_note=snote,
        basis={},
    )

    min_h = gi["min_history_days"]
    nd = cfg["proximity"]["narrow_days"]
    KINDS = kinds(cfg)
    RANK = rank_of(cfg)
    # 룩백 창(판정일 당일 기준 — 창을 못 채우면 None). 두 기준이 같은 창을 쓴다.
    wins = _windows_at(rows, dates, idx, cal, cfg, floor)
    j = idx - nd
    prev_wins = _windows_at(rows, dates, j, cal, cfg, floor) if j >= floor else None

    for basis in BASES:
        k = _key(basis)
        cur = today.get(k)
        refs = _refs_at(wins, basis, hist_ref)
        # 역사적은 이력이 짧거나 수정주가 의심이면 계산하지 않는다.
        if n_days < min_h["hist"] or (suspect and gi["suppress_hist_on_suspect"]):
            refs["hist"] = None

        hit, gaps, resist = {}, {}, {}
        for kind in KINDS:
            ref = refs.get(kind)
            # 당일 값 > 창 최고가 (엄격 — ADR 0017. 같은 값은 신고가가 아니다: 거래 없는 날의
            # 같은 가격이 매일 신고가로 찍히지 않고, 종가 기준 신고가는 등락률이 늘 플러스다)
            hit[kind] = bool(ref and cur is not None and cur > ref)
            g = _gap(ref, cur)  # ref 나 cur 이 없으면 None 을 돌려준다
            gaps[kind] = None if g is None else round(g, 2)
            r = _resistance(rows, idx, wins.get(kind), cur, ref, avg20, floor)
            resist[kind] = r

        # 5일 축소폭 — 같은 정의로 5영업일 전 갭을 다시 계산해 뺀다.
        narrow = {}
        if prev_wins is not None:
            # 룩백 창 기반 라벨은 j 시점에서 창을 다시 잡으므로 문제없다.
            # hist 는 다르다 — 오늘의 사상최고가를 5일 전 기준으로 쓰면
            # 그 사이에 최고가가 움직인 만큼이 통째로 '축소폭'으로 잡힌다.
            # 3일 전에 신고가를 낸 종목이 "5일 만에 20%p 좁혔다"로 나와
            # 근접 표 맨 위를 먹는다. hist 는 계산하지 않는다.
            prev_refs = _refs_at(prev_wins, basis, None)
            pcur = rows[j].get(k)
            for kind in KINDS:
                if kind == "hist":
                    narrow[kind] = None
                    continue
                g0, g1 = _gap(prev_refs.get(kind), pcur), gaps.get(kind)
                narrow[kind] = round(g1 - g0, 2) if (g0 is not None and g1 is not None) else None
        else:
            narrow = {kind: None for kind in KINDS}

        label = next((kind for kind in KINDS if hit[kind]), None)
        out["basis"][basis] = dict(
            cur=cur,
            refs=refs,
            hit=hit,
            gap=gaps,
            narrow5=narrow,
            resistance={kind: (resist[kind] or {}).get("value") for kind in KINDS},
            resistance_window={kind: (resist[kind] or {}).get("window_days") for kind in KINDS},
            resistance_label={
                kind: _bucket((resist[kind] or {}).get("value"), cfg) for kind in KINDS
            },
            label=label,
            rank=RANK[label] if label else None,
        )
    return out


# ─────────────────────────── 역사적 최고가 스칼라 ───────────────────────────
def roll_alltime(prev, rows, cfg):
    """역사적 최고가 스칼라 갱신. 전체 일봉을 매일 재계산하지 않는다.

    hi 만 들고 있으면 안 된다. 수집은 당일 봉까지 받아 오므로, 당일 고가가
    사상 최고가면 hi 가 이미 그 값이 되어 '당일 갱신' 판정이 영원히 거짓이 된다.
    그래서 직전 처리일까지의 최고가(prev_hi / prev_cl)를 함께 들고 있고,
    엔진은 기준일이 last_date 와 같을 때 그 값을 기준 최고가로 쓴다.

    같은 날짜를 다시 넣어도 값이 변하지 않도록 last_date 이후 행만 반영한다.
    과거 구간을 뒤늦게 채우려면(백필) 이 함수로는 안 되고 --init 으로 다시 쌓아야 한다.

    prev  기존 스칼라 dict 또는 None
    rows  오름차순 일봉
    """
    prev = alltime_dict(prev) if isinstance(prev, AllTime) else (prev or {})
    rows = as_rows(rows)
    hi, hi_d = prev.get("hi"), prev.get("hi_date")
    cl, cl_d = prev.get("cl"), prev.get("cl_date")
    p_hi, p_cl = prev.get("prev_hi"), prev.get("prev_cl")
    first, last = prev.get("first_date"), prev.get("last_date")
    n = prev.get("n_days") or 0

    fresh = sorted(
        {r["asof"]: r for r in rows if not last or r["asof"] > last}.values(),
        key=lambda r: r["asof"],
    )
    for r in fresh:
        # 이 행을 반영하기 직전의 값이 곧 '직전 영업일까지의 최고가'다.
        p_hi, p_cl = hi, cl
        d = r["asof"]
        if first is None or d < first:
            first = d
        if r.get("high") and (hi is None or r["high"] > hi):
            hi, hi_d = r["high"], d
        if r.get("close") and (cl is None or r["close"] > cl):
            cl, cl_d = r["close"], d
        last = d
        n += 1

    suspect, sdate, snote, _ = split_guard(rows, cfg["integrity"]["split_guard_ratio"])
    return dict(
        hi=hi,
        hi_date=hi_d,
        cl=cl,
        cl_date=cl_d,
        prev_hi=p_hi,
        prev_cl=p_cl,
        first_date=first,
        last_date=last,
        n_days=n,
        suspect=suspect,
        suspect_date=sdate,
        suspect_note=snote,
    )


def alltime_dict(at: AllTime) -> dict[str, Any]:
    def iso(d: date | None) -> str | None:
        return d.isoformat() if d else None

    return dict(
        hi=at.hi,
        hi_date=iso(at.hi_date),
        cl=at.cl,
        cl_date=iso(at.cl_date),
        prev_hi=at.prev_hi,
        prev_cl=at.prev_cl,
        first_date=iso(at.first_date),
        last_date=iso(at.last_date),
        n_days=at.n_days,
        suspect=at.suspect,
        suspect_date=iso(at.suspect_date),
        suspect_note=at.suspect_note,
        history_from=iso(at.history_from),
    )


def hist_depth(at, listed_on=None, source_floor=None):
    """역사적 신고가를 판정할 만큼 이력이 깊은가 — ('listing'|'floor'|None, 보류 사유).

    KBJ(D-P3-11·ADR 0017): 스칼라의 `history_from`(시장 일봉을 받아 둔 첫날)이
      - 상장일(`listed_on`) 이하 → 'listing'(상장 이후 전체)
      - 아니면 원천 바닥(`source_floor` — 원천이 주는 가장 이른 날, config/board.yaml
        newhigh.hist_source_floor) 이하 → 'floor'(더 받을 수 없다 — '바닥일 이후 최고가' 기준이라고
        화면이 한 번 적는다). 상장일을 몰라도 바닥에 닿았으면 판정한다.
      - 둘 다 아니면 보류: 상장일을 알면 `HIST_BEFORE_LISTING`, 모르면 `HIST_LISTING_UNKNOWN`
        (지어내지 않는다 — 짧은 구간으로 조용히 대신하지 않는다).
    `history_from` 이 없는 스칼라(ET 방식 — 골든)는 예전 그대로 'listing' 으로 본다.
    """
    if isinstance(at, AllTime):
        at = alltime_dict(at)
    hf = (at or {}).get("history_from")
    if not hf:
        return "listing", ""
    lo = listed_on.isoformat() if isinstance(listed_on, date) else listed_on
    if lo and lo >= hf:
        return "listing", ""
    fl = source_floor.isoformat() if isinstance(source_floor, date) else source_floor
    if fl and hf <= str(fl):
        return "floor", ""
    return None, (HIST_BEFORE_LISTING if lo else HIST_LISTING_UNKNOWN)


def hist_ref_for(at, asof, listed_on=None, source_floor=None):
    """스칼라에서 기준일의 '역사적 최고가' 기준값을 꺼낸다.

    반환 (ref dict 또는 None, 사유). 기준일이 스칼라가 아는 마지막 날보다
    과거면 스칼라만으로는 복원할 수 없으므로 판정하지 않는다.

    이력 깊이는 `hist_depth`(상장일 또는 원천 바닥에 닿아야 판정 — D-P3-11·ADR 0017).
    """
    if isinstance(at, AllTime):
        at = alltime_dict(at)
    if isinstance(asof, date):
        asof = asof.isoformat()
    depth, why = hist_depth(at, listed_on, source_floor)
    if depth is None:
        return None, why
    if not at or not at.get("last_date"):
        return None, "역사적 최고가 스칼라 없음"
    last = at["last_date"]
    if asof == last:
        if at.get("prev_hi") is None:
            return None, "직전일까지의 최고가 미확보 (상장 첫날이거나 최초 적재)"
        return {"high": at["prev_hi"], "close": at.get("prev_cl")}, ""
    if asof > last:
        return {"high": at.get("hi"), "close": at.get("cl")}, ""
    return None, f"기준일 {asof} 이 스칼라 기준일 {last} 보다 과거 — 스칼라로 복원 불가"


# ─────────────────────────── 신규 / 이어감 ───────────────────────────
def continuity(today_rank, prev_rank):
    """상위 라벨이 하위를 포함하므로 순위 비교로 판정한다.

    어제 52주였고 오늘 120일이면 '이어감'. 어제 120일이었고 오늘 52주면 '신규'.
    """
    if today_rank is None:
        return None
    if prev_rank is None:
        return "신규"
    return "이어감" if prev_rank <= today_rank else "신규"


# ─────────────────────────── 근접 ───────────────────────────
def proximity_kind(ev, basis, cfg):
    """근접으로 볼 기준을 고른다.

    hist 의 기준 최고가는 항상 w52 이상, w52 는 d120 이상이다. 따라서 임계 안에
    드는 것 중 가장 상위 라벨이 정보량이 가장 크다. 그걸 고른다.
    """
    b = ev["basis"][basis]
    lim = cfg["proximity"]["max_gap_pct"]
    # 표시 대상 라벨만 본다. 표에 안 올라가는 등급의 근접은 정보량이 없다.
    show = displayable(cfg)
    for kind in kinds(cfg):
        if kind not in show:
            continue
        g = b["gap"].get(kind)
        if g is not None and 0 < g <= lim and not b["hit"][kind]:
            return kind
    return None


# ─────────────────────────── 확정치로 마지막 날 다시 쓰기 (KBJ) ───────────────────────────
def restate_last_day(at, bar):
    """스칼라의 마지막 날(`last_date`) 값을 확정 일봉으로 바꾼다 — `board.confirm`(D-P3-8).

    `roll_alltime` 은 같은 날을 두 번 반영하지 않는다(last_date 이후 행만). 그래서 KIS
    마감값(잠정)으로 굴린 날을 다음 날 KRX 확정 일봉으로 고치려면 그날 몫만 다시 계산해야 한다.
    직전일까지의 최고가(`prev_hi`·`prev_cl`)는 그날 값과 무관하므로 그대로 두고, 그날을 포함한
    최고가(`hi`·`cl`)만 `max(prev_*, 확정값)` 으로 다시 정한다. 그날이 최고였는데 확정값이 직전
    최고 이하가 되면 최고가는 `prev_*` 로 돌아가고 그 날짜는 스칼라가 들고 있지 않으므로 None
    이다(지어내지 않는다).

    at   스칼라 dict(또는 AllTime)
    bar  그날 확정 일봉(dict 또는 Bar). 날짜가 마지막 날과 다르면 스칼라를 그대로 돌려준다.
    """
    at = alltime_dict(at) if isinstance(at, AllTime) else dict(at or {})
    row = as_row(bar)
    d = row["asof"]
    if not at or at.get("last_date") != d:
        return at
    for key, top, top_d, prev_key in (
        ("high", "hi", "hi_date", "prev_hi"),
        ("close", "cl", "cl_date", "prev_cl"),
    ):
        v, p = row.get(key), at.get(prev_key)
        if not v:
            continue
        if p is None or v > p:
            at[top], at[top_d] = v, d
        else:
            # 그날은 최고가 아니다 — 최고는 직전일까지의 최고
            # (그날 잠정값이 최고였다면 그 최고의 날짜는 모른다)
            at[top] = p
            if at.get(top_d) == d:
                at[top_d] = None
    return at
