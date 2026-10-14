"""기술 지표 정본 — 규칙 평가·백테스트·차트가 같은 함수를 부른다(docs/metrics.md §30, D-P8-3).

P8 묶음 FND. 정의는 board `systems.py`·`signals.py`(시험이 있는 쪽 — `sma_arr`·`ema_arr`·`rsi_arr`·
`adx_arr`·`turtle_n`·`pstdev`·`true_range`·`bb_width`)를 따른다. SD `_calc_*`(EMA 첫 값 시드·RSI 0
채움)는 버린다.

공통 규칙(metrics §30.0)
- 입력은 같은 간격의 칸(거래일) 배열, 출력은 **같은 길이** 리스트. 워밍업·결측은 `None` — 0 으로
  채우지 않는다.
- 결측 칸(`None` — 거래정지 등)
  - 창 지표(SMA·볼린저·N일 최고/최저·거래량 배수·밴드폭 백분위): 창 안에 `None` 이 하나라도 있으면
    `None`.
  - 재귀 지표(EMA·RSI·MACD·ATR·ADX): 결측 칸을 **건너뛴다** — 그 칸 출력은 `None`, 상태는 직전 유효
    칸 그대로 이어진다(결측 칸을 지운 배열로 계산한 뒤 제자리에 펼친 것과 같다). 그래서 정지일이
    없으면 board 원본과 같은 값이다.
- 계산은 순수 Python float, 왼쪽→오른쪽 누적 순서 고정(board 와 같은 부동소수 순서 — numpy 없음).
- 시작점(anchor)은 부르는 쪽(`kbj.engines.rules.frame`)이 정한다 — 같은 시작점에서 한 번에 계산하고
  필요한 칸을 읽는다(D-P8-7).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "Adx",
    "Bollinger",
    "Macd",
    "adx",
    "atr",
    "bollinger",
    "change_pct",
    "ema",
    "high_n",
    "low_n",
    "macd",
    "pct_rank",
    "pstdev",
    "rsi",
    "sma",
    "trail_from_peak",
    "true_range",
    "vol_ratio",
    "width_rank",
]

Series = Sequence[float | None]
Out = list[float | None]

_MIN_N: Final = 1


def _check_n(name: str, n: int, lo: int = _MIN_N) -> None:
    if n < lo:
        raise ValueError(f"{name}: 기간 n 은 {lo} 이상이어야 한다: {n}")


def _same_len(*xs: Series) -> int:
    m = len(xs[0])
    if any(len(x) != m for x in xs):
        raise ValueError(f"입력 길이가 다르다: {[len(x) for x in xs]}")
    return m


def _window(x: Series, i: int, n: int) -> list[float] | None:
    """x[i-n+1 .. i] — 모자라거나 None 이 섞이면 None."""
    if i - n + 1 < 0:
        return None
    w = x[i - n + 1 : i + 1]
    out: list[float] = []
    for v in w:
        if v is None:
            return None
        out.append(v)
    return out


def _skip_none(
    xs: Sequence[Series], fn: Callable[[Sequence[Sequence[float]]], list[Out]]
) -> list[Out]:
    """재귀 지표의 결측 규칙 — 모든 입력이 있는 칸만 모아 계산하고 제자리에 펼친다."""
    m = _same_len(*xs)
    idx = [i for i in range(m) if all(x[i] is not None for x in xs)]
    packed: list[list[float]] = []
    for x in xs:
        col: list[float] = []
        for i in idx:
            v = x[i]
            if v is None:  # pragma: no cover — idx 는 None 이 없는 칸만
                raise ValueError("결측 칸이 섞였다")
            col.append(v)
        packed.append(col)
    results = fn(packed)
    outs: list[Out] = []
    for r in results:
        full: Out = [None] * m
        for j, i in enumerate(idx):
            full[i] = r[j]
        outs.append(full)
    return outs


# ── 이동평균 ────────────────────────────────────────────────────────────────────────────────


def sma(x: Series, n: int) -> Out:
    """단순이동평균 — 최근 n 칸 산술 평균. 창 안에 None 이 있으면 None(board `signals.sma`)."""
    _check_n("sma", n)
    out: Out = [None] * len(x)
    for i in range(len(x)):
        w = _window(x, i, n)
        if w is not None:
            out[i] = sum(w) / n
    return out


def _ema_dense(x: Sequence[float], n: int) -> Out:
    # board `ema_arr` 그대로 — 시드 = 첫 n 칸 SMA(칸 n−1), 이후 e += α(x − e)
    out: Out = [None] * len(x)
    a = 2 / (n + 1)
    e = 0.0
    for i, v in enumerate(x):
        if i == n - 1:
            e = sum(x[:n]) / n
            out[i] = e
        elif i >= n:
            e = e + a * (v - e)
            out[i] = e
    return out


def ema(x: Series, n: int) -> Out:
    """지수이동평균 — 시드는 첫 n 유효 칸의 SMA, α = 2/(n+1). 결측 칸은 건너뛴다."""
    _check_n("ema", n)
    return _skip_none([x], lambda p: [_ema_dense(p[0], n)])[0]


# ── RSI ─────────────────────────────────────────────────────────────────────────────────────


def _rsi_dense(c: Sequence[float], n: int) -> Out:
    # board `rsi_arr` 그대로(와일더 평활, l = 0 이면 100)
    out: Out = [None] * len(c)
    if len(c) <= n:
        return out
    g = loss = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0)
        loss += max(-d, 0)
    g, loss = g / n, loss / n
    out[n] = 100.0 if loss == 0 else 100 - 100 / (1 + g / loss)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + g / loss)
    return out


def rsi(close: Series, n: int = 14) -> Out:
    """와일더 RSI(0~100). 워밍업 n 칸은 None(SD 의 0.0 채움 폐지). 결측 칸은 건너뛴다."""
    _check_n("rsi", n)
    return _skip_none([close], lambda p: [_rsi_dense(p[0], n)])[0]


# ── MACD ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Macd:
    line: Out
    signal: Out
    hist: Out


def macd(close: Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Macd:
    """line = EMA(fast) − EMA(slow), signal = line 의 EMA(signal — line 의 첫 유효 칸부터 시드),
    hist = line − signal."""
    _check_n("macd.fast", fast)
    _check_n("macd.slow", slow)
    _check_n("macd.signal", signal)
    if fast >= slow:
        raise ValueError(f"macd: fast({fast}) < slow({slow}) 이어야 한다")
    ef, es = ema(close, fast), ema(close, slow)
    line: Out = [None if a is None or b is None else a - b for a, b in zip(ef, es, strict=True)]
    sig = ema(line, signal)
    hist: Out = [None if a is None or b is None else a - b for a, b in zip(line, sig, strict=True)]
    return Macd(line=line, signal=sig, hist=hist)


# ── 볼린저 ──────────────────────────────────────────────────────────────────────────────────


def pstdev(x: Series, n: int) -> Out:
    """모표준편차(창 n — 평균과 같은 창, board `signals.pstdev`)."""
    _check_n("pstdev", n)
    out: Out = [None] * len(x)
    for i in range(len(x)):
        w = _window(x, i, n)
        if w is None:
            continue
        m = sum(w) / n
        out[i] = (sum((v - m) ** 2 for v in w) / n) ** 0.5
    return out


@dataclass(frozen=True)
class Bollinger:
    upper: Out
    mid: Out
    lower: Out
    pctb: Out
    width: Out


def bollinger(close: Series, n: int = 20, k: float = 2.0) -> Bollinger:
    """mid = SMA(n), upper/lower = mid ± kσ(모표준편차), %B = (close − lower)/(upper − lower)
    (σ = 0 이면 None), width = (upper − lower)/mid(mid 가 0 이면 None — board `bb_width`)."""
    _check_n("bollinger", n)
    if k <= 0:
        raise ValueError(f"bollinger: k 는 0 보다 커야 한다: {k}")
    mid = sma(close, n)
    sd = pstdev(close, n)
    m = len(close)
    upper: Out = [None] * m
    lower: Out = [None] * m
    pctb: Out = [None] * m
    width: Out = [None] * m
    for i in range(m):
        mi, si = mid[i], sd[i]
        if mi is None or si is None:
            continue
        up, lo = mi + k * si, mi - k * si
        upper[i], lower[i] = up, lo
        ci = close[i]
        if si != 0 and ci is not None:
            pctb[i] = (ci - lo) / (up - lo)
        if mi != 0:
            width[i] = (2 * k * si) / mi
    return Bollinger(upper=upper, mid=mid, lower=lower, pctb=pctb, width=width)


def pct_rank(window: Sequence[float], value: float) -> float:
    """창(값 포함) 안 순위 백분위 — 동점은 평균 순위 r, `p = (r−1)/(n−1)×100`, n = 1 이면 50
    (metrics §D0 와 같은 규칙)."""
    n = len(window)
    if n == 0:
        raise ValueError("pct_rank: 빈 창")
    if n == 1:
        return 50.0
    below = sum(1 for v in window if v < value)
    equal = sum(1 for v in window if v == value)
    if equal == 0:
        raise ValueError("pct_rank: 값이 창 안에 없다")
    r = below + (equal + 1) / 2
    return (r - 1) / (n - 1) * 100


def width_rank(width: Series, lookback: int = 120) -> Out:
    """밴드폭 백분위(스퀴즈) — 그날 포함 직전 lookback 칸의 밴드폭 중 그날 값의 순위 백분위(0~100,
    `pct_rank`). 창에 None 이 있거나 lookback 미달이면 None."""
    _check_n("width_rank", lookback, 2)
    out: Out = [None] * len(width)
    for i in range(len(width)):
        w = _window(width, i, lookback)
        if w is not None:
            out[i] = pct_rank(w, w[-1])
    return out


# ── 변동폭·추세 ─────────────────────────────────────────────────────────────────────────────


def true_range(high: Series, low: Series, close: Series) -> Out:
    """TR = max(h−l, |h−c₋₁|, |l−c₋₁|). 첫 칸(또는 직전 유효 종가가 없으면)은 h−l(board
    `signals.true_range`). 직전 종가는 직전 **유효** 칸의 것(정지일 건너뜀)."""
    m = _same_len(high, low, close)
    out: Out = [None] * m
    prev_close: float | None = None
    for i in range(m):
        h, lo, c = high[i], low[i], close[i]
        if h is None or lo is None or c is None:
            continue
        if prev_close is None:
            out[i] = h - lo
        else:
            out[i] = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        prev_close = c
    return out


def _atr_dense(h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int) -> Out:
    # board `turtle_n` 그대로 — 시드 = TR[1..n] 평균(칸 n), 이후 (ATR·(n−1) + TR)/n
    m = len(c)
    out: Out = [None] * m
    if m <= n:
        return out
    tr = [0.0] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, m)
    ]
    v = sum(tr[1 : n + 1]) / n
    out[n] = v
    for i in range(n + 1, m):
        v = (v * (n - 1) + tr[i]) / n
        out[i] = v
    return out


def atr(high: Series, low: Series, close: Series, n: int = 14) -> Out:
    """ATR — TR 의 와일더 평균. 시드 = 칸 1..n 의 TR 평균(칸 n — 첫 칸 TR 은 직전 종가가 없어 빼는
    board `turtle_n` 규칙), 이후 `(ATR₋₁·(n−1) + TR)/n`. 결측 칸은 건너뛴다."""
    _check_n("atr", n)
    return _skip_none([high, low, close], lambda p: [_atr_dense(p[0], p[1], p[2], n)])[0]


@dataclass(frozen=True)
class Adx:
    adx: Out
    pdi: Out
    mdi: Out


def _adx_dense(h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int) -> list[Out]:
    # board `adx_arr` 그대로(+DI·−DI 도 같은 계산에서 내놓는다)
    m = len(c)
    out: Out = [None] * m
    pdi_out: Out = [None] * m
    mdi_out: Out = [None] * m
    if m < 2 * n + 1:
        return [out, pdi_out, mdi_out]
    tr, pdm, ndm = [0.0] * m, [0.0] * m, [0.0] * m
    for i in range(1, m):
        up, dn = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        ndm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
    a, p, q = sum(tr[1 : n + 1]), sum(pdm[1 : n + 1]), sum(ndm[1 : n + 1])
    dx: list[float] = []
    adx_v = 0.0
    for i in range(n, m):
        if i > n:
            a = a - a / n + tr[i]
            p = p - p / n + pdm[i]
            q = q - q / n + ndm[i]
        pdi = 100 * p / a if a else 0.0
        ndi = 100 * q / a if a else 0.0
        d = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0.0
        pdi_out[i], mdi_out[i] = pdi, ndi
        dx.append(d)
        if len(dx) == n:
            adx_v = sum(dx) / n
            out[i] = adx_v
        elif len(dx) > n:
            adx_v = (adx_v * (n - 1) + d) / n
            out[i] = adx_v
    return [out, pdi_out, mdi_out]


def adx(high: Series, low: Series, close: Series, n: int = 14) -> Adx:
    """와일더 ADX(board `adx_arr`). +DI/−DI 는 칸 n 부터, ADX 는 칸 2n−1 부터(입력이 2n+1 칸
    미만이면
    전부 None — board 와 같다). 결측 칸은 건너뛴다."""
    _check_n("adx", n)
    a, p, q = _skip_none([high, low, close], lambda x: _adx_dense(x[0], x[1], x[2], n))
    return Adx(adx=a, pdi=p, mdi=q)


# ── 거래량·고저·등락 ────────────────────────────────────────────────────────────────────────


def vol_ratio(volume: Series, n: int = 20) -> Out:
    """오늘 거래량 ÷ 직전 n 칸 평균(오늘 제외). 창에 None 이 있거나 평균이 0 이면 None. 장중 부분
    봉은 누적 거래량 그대로(시간 보정 없음 — metrics §30.2)."""
    _check_n("vol_ratio", n)
    out: Out = [None] * len(volume)
    for i in range(len(volume)):
        v = volume[i]
        w = _window(volume, i - 1, n)
        if v is None or w is None:
            continue
        avg = sum(w) / n
        if avg != 0:
            out[i] = v / avg
    return out


def high_n(high: Series, n: int = 20) -> Out:
    """직전 n 칸 고가 최대(오늘 제외). 창에 None 이 있으면 None."""
    _check_n("high_n", n)
    out: Out = [None] * len(high)
    for i in range(len(high)):
        w = _window(high, i - 1, n)
        if w is not None:
            out[i] = max(w)
    return out


def low_n(low: Series, n: int = 20) -> Out:
    """직전 n 칸 저가 최소(오늘 제외). 창에 None 이 있으면 None."""
    _check_n("low_n", n)
    out: Out = [None] * len(low)
    for i in range(len(low)):
        w = _window(low, i - 1, n)
        if w is not None:
            out[i] = min(w)
    return out


def change_pct(close: Series) -> Out:
    """등락률(%) = (close / 직전 유효 종가 − 1) × 100. 첫 유효 칸·결측 칸은 None, 직전 종가 0 이면
    None."""
    out: Out = [None] * len(close)
    prev: float | None = None
    for i, c in enumerate(close):
        if c is None:
            continue
        if prev is not None and prev != 0:
            out[i] = (c / prev - 1) * 100
        prev = c
    return out


def trail_from_peak(high: Series, close: Series, start: int | None) -> Out:
    """보유 시작 칸(start, 포함) 이후 최고 고가 대비 종가 하락률(%) = (close/peak − 1) × 100(0
    이하).
    start 가 None(포지션 없음)이면 전부 None. start 이전 칸도 None."""
    m = _same_len(high, close)
    out: Out = [None] * m
    if start is None:
        return out
    if start < 0:
        raise ValueError(f"trail_from_peak: start 는 0 이상: {start}")
    peak: float | None = None
    for i in range(start, m):
        h, c = high[i], close[i]
        if h is not None:
            peak = h if peak is None else max(peak, h)
        if c is not None and peak is not None and peak > 0:
            out[i] = (c / peak - 1) * 100
    return out
