"""오프라인 합성 소스.

네이버로 나가는 접속이 막힌 환경에서 배관을 끝까지 돌려 보기 위한 것이다.
`naver` 모듈과 같은 함수 시그니처를 갖는다.

- 종목 일봉: board 데모 DB(board/board.db.demo)의 px 를 그대로 쓰고, 3년 창을
  채우기 위해 그 앞 구간만 종목코드로 시드한 역방향 난수보행으로 채운다.
- 지수: 시장별 종목 수익률 동일가중 평균을 누적한 값. 별도 합성이 아니다.
- 투자자별 순매매: 당일 수익률·거래량에서 만든 결정론적 합성값.

이 소스로 만든 산출물에는 source="demo" 가 박히고 리포트가 그대로 노출한다.
합성 데이터의 수치는 어떤 경우에도 시장 사실로 인용하지 않는다.
"""
from __future__ import annotations

import hashlib
import sqlite3

import numpy as np
import pandas as pd

from . import config as C

DB = C.BOARD / "board.db.demo"
_PX: dict[str, pd.DataFrame] = {}
_UNIVERSE: pd.DataFrame | None = None
_INDEX: dict[str, pd.DataFrame] = {}
_TARGET_ROWS = C.STUDY_YEARS * 252 + max(C.STUDY_HORIZONS) + 5


def available() -> bool:
    return DB.exists()


def _seed(key: str) -> np.random.Generator:
    h = hashlib.sha256(key.encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "big"))


def _universe() -> pd.DataFrame:
    global _UNIVERSE
    if _UNIVERSE is None:
        with sqlite3.connect(DB) as conn:
            _UNIVERSE = pd.read_sql(
                "SELECT code, name, market, close, mktcap FROM snap "
                "WHERE asof=(SELECT MAX(asof) FROM snap)", conn)
    return _UNIVERSE


def codes() -> list[str]:
    return _universe()["code"].tolist()


def _db_px(code: str) -> pd.DataFrame:
    with sqlite3.connect(DB) as conn:
        df = pd.read_sql(
            "SELECT asof AS date, open, high, low, close, volume FROM px "
            "WHERE code=? ORDER BY asof", conn, params=(code,))
    return df


def _backfill(code: str, df: pd.DataFrame, n: int) -> pd.DataFrame:
    """df 앞쪽으로 n 영업일을 역방향 난수보행으로 채운다."""
    if n <= 0 or df.empty:
        return df
    rng = _seed("px:" + code)
    first_close = float(df["close"].iloc[0])
    first_date = np.datetime64(df["date"].iloc[0])
    dates = np.busday_offset(first_date, -np.arange(n, 0, -1), roll="backward")
    rets = rng.normal(0.0004, 0.021, n)
    # 뒤에서 앞으로 되짚어 종가를 만든다 (마지막 합성일 다음이 df 의 첫날).
    closes = np.empty(n)
    c = first_close
    for i in range(n - 1, -1, -1):
        c = c / (1.0 + rets[i])
        closes[i] = c
    span = np.abs(rng.normal(0.012, 0.006, n)) + 0.002
    opens = closes * (1 + rng.normal(0, 0.006, n))
    highs = np.maximum(opens, closes) * (1 + span)
    lows = np.minimum(opens, closes) * (1 - span)
    base_vol = float(df["volume"].head(20).median() or 1e5)
    vols = np.maximum(1000, base_vol * np.exp(rng.normal(0, 0.55, n)))
    head = pd.DataFrame({
        "date": [str(d) for d in dates.astype("datetime64[D]")],
        "open": opens.round(0), "high": highs.round(0),
        "low": lows.round(0), "close": closes.round(0), "volume": vols.round(0),
    })
    return pd.concat([head, df], ignore_index=True)


def daily_ohlcv(symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
    if symbol in C.INDEX_SYMBOL:
        return index_series(symbol)
    if symbol in _PX:
        df = _PX[symbol]
    else:
        df = _db_px(symbol)
        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close",
                                         "volume", "frgn_rate"])
        df = _backfill(symbol, df, _TARGET_ROWS - len(df))
        rng = _seed("frgn:" + symbol)
        rate = np.clip(np.cumsum(rng.normal(0, 0.08, len(df))) + rng.uniform(3, 45), 0.1, 70)
        df["frgn_rate"] = rate.round(2)
        df = df.reset_index(drop=True)
        _PX[symbol] = df
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df.reset_index(drop=True)


def index_series(market: str) -> pd.DataFrame:
    """시장별 동일가중 수익률 누적. 종목 합성값과 같은 세계에서 나온다."""
    if market in _INDEX:
        return _INDEX[market]
    uni = _universe()
    members = uni.loc[uni["market"] == market, "code"].tolist()
    rets = []
    for code in members:
        px = daily_ohlcv(code)
        if px.empty:
            continue
        s = px.set_index("date")["close"].astype(float).pct_change()
        rets.append(s.rename(code))
    if not rets:
        raise RuntimeError(f"데모 지수 산출 실패: {market} 구성종목 없음")
    mat = pd.concat(rets, axis=1).sort_index()
    avg = mat.mean(axis=1).fillna(0.0)
    base = 2500.0 if market == "KOSPI" else 850.0
    level = base * (1 + avg).cumprod()
    out = pd.DataFrame({"date": level.index, "open": level.values, "high": level.values,
                        "low": level.values, "close": level.values,
                        "volume": np.nan, "frgn_rate": np.nan}).reset_index(drop=True)
    _INDEX[market] = out
    return out


def investor_flows(code: str, pages: int = 1) -> pd.DataFrame:
    """당일 수익률·거래량에서 만든 결정론적 합성 순매매. 네이버와 같은 역순 표."""
    px = daily_ohlcv(code)
    if px.empty:
        from .naver import _empty_flow
        return _empty_flow()
    rng = _seed("flow:" + code)
    n = len(px)
    ret = px["close"].astype(float).pct_change().fillna(0.0).to_numpy()
    vol = px["volume"].astype(float).to_numpy()
    # 상승일에 기관·외인이 순매수하는 경향 + 종목별로 다른 성향
    inst_bias, frgn_bias = rng.normal(0, 0.35, 2)
    inst = vol * (0.16 * np.tanh(ret / 0.02) + inst_bias * 0.05 + rng.normal(0, 0.07, n))
    frgn = vol * (0.13 * np.tanh(ret / 0.02) + frgn_bias * 0.05 + rng.normal(0, 0.06, n))
    out = pd.DataFrame({
        "date": px["date"], "close": px["close"].astype(float),
        "chg_pct": (ret * 100).round(2), "volume": vol,
        "inst_net": inst.round(0), "frgn_net": frgn.round(0),
        "frgn_hold": np.nan, "frgn_rate": px["frgn_rate"],
    })
    out = out.sort_values("date", ascending=False).reset_index(drop=True)
    return out.head(max(1, int(pages)) * C.FLOW_ROWS_PER_PAGE)
