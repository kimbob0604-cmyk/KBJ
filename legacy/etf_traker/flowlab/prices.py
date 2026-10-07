"""일봉 캐시.

flowlab/cache/prices/{source}/{code}.csv.gz 에 종목별로 쌓고 증분 갱신한다.
siseJson 은 한 요청에 전 구간을 주므로 '증분'은 캐시가 최신이면 요청을 건너뛰고,
아니면 받아서 합치는 방식이다. 소스별로 폴더를 나눠 합성값이 실데이터 캐시에
섞이지 않게 한다.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import config as C

COLS = ["date", "open", "high", "low", "close", "volume", "frgn_rate"]


def source_module(name: str):
    if name == C.SOURCE_DEMO:
        from . import demo
        return demo
    from . import naver
    return naver


def _path(code: str, source: str):
    d = C.PRICE_CACHE / source
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{code}.csv.gz"


def read_cache(code: str, source: str) -> pd.DataFrame:
    p = _path(code, source)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    try:
        df = pd.read_csv(p, dtype={"date": str})
    except Exception:
        return pd.DataFrame(columns=COLS)
    for c in COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[COLS]


def write_cache(code: str, source: str, df: pd.DataFrame) -> None:
    df[COLS].to_csv(_path(code, source), index=False, compression="gzip")


def _fresh_enough(df: pd.DataFrame, need_through: str | None) -> bool:
    if df.empty:
        return False
    if need_through is None:
        return False
    return str(df["date"].iloc[-1]) >= str(need_through)


def load(code: str, source: str = C.SOURCE_NAVER, need_through: str | None = None,
         refresh: bool = True) -> pd.DataFrame:
    """캐시 우선. need_through(YYYY-MM-DD) 까지 있으면 네트워크를 타지 않는다."""
    cached = read_cache(code, source)
    if not refresh or _fresh_enough(cached, need_through):
        return cached
    mod = source_module(source)
    fresh = mod.daily_ohlcv(code)
    if fresh is None or fresh.empty:
        return cached
    for c in COLS:
        if c not in fresh.columns:
            fresh[c] = np.nan
    merged = (pd.concat([cached, fresh[COLS]], ignore_index=True)
              .drop_duplicates("date", keep="last")
              .sort_values("date").reset_index(drop=True))
    write_cache(code, source, merged)
    return merged


ABORT_AFTER = 20   # 앞쪽 이만큼이 전부 비면 소스가 죽은 것이다. 나머지를 기다리지 않는다


def bulk(codes, source: str = C.SOURCE_NAVER, need_through: str | None = None,
         log=print, every: int = 100) -> dict[str, pd.DataFrame]:
    out, failed = {}, []
    for i, code in enumerate(codes, 1):
        try:
            df = load(code, source, need_through=need_through)
            if not df.empty:
                out[code] = df
            else:
                failed.append(code)
        except Exception as e:
            failed.append(f"{code}({type(e).__name__}: {str(e)[:80]})")
        if i == ABORT_AFTER and not out:
            mod = source_module(source)
            hint = ""
            if hasattr(mod, "raw_sise"):
                try:
                    hint = f" · 원문 머리 {mod.raw_sise(str(codes[0]))[:200]!r}"
                except Exception as e:
                    hint = f" · 원문 요청 실패 {type(e).__name__}: {e}"
            raise RuntimeError(f"일봉 소스({source}) 응답이 전부 비어 있다 — 앞 {i}종목 0건. "
                               f"실패 예: {failed[0]}{hint}")
        if every and i % every == 0:
            log(f"  일봉 {i}/{len(codes)}")
    if failed:
        log(f"  일봉 실패 {len(failed)}종목: {', '.join(map(str, failed[:8]))}"
            + (" ..." if len(failed) > 8 else ""))
    return out


def index(market: str, source: str = C.SOURCE_NAVER,
          need_through: str | None = None, refresh: bool = True) -> pd.DataFrame:
    symbol = C.INDEX_SYMBOL.get(market, market)
    return load(symbol, source, need_through=need_through, refresh=refresh)


def last_business_day(today: str | None = None) -> str:
    d = np.datetime64(today or dt.date.today().isoformat())
    return str(np.busday_offset(d, 0, roll="backward"))
