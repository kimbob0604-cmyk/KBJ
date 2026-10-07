"""네이버 수집기.

가격  : api.finance.naver.com/siseJson.naver — 한 요청에 전체 일봉 + 외국인소진율
수급  : m.stock.naver.com/api/stock/{code}/trend — JSON, pageSize 로 깊이를 정한다

수급은 원래 finance.naver.com 의 HTML 표였는데 2026-09 에 그 화면이 없어졌다
(302 로 Next.js 화면으로 넘어가고 응답에 tr/td 가 0개다). 근거와 실측은 D-079.

KRX(data.krx.co.kr)는 로그인 없이 JSON 을 주지 않는다. 여기에 시간 쓰지 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time

import numpy as np
import pandas as pd
import requests

from . import config as C

_SESSION: requests.Session | None = None
_DATE_RE = re.compile(r"^\d{4}[.\-/]\d{2}[.\-/]\d{2}$")
_YMD_RE = re.compile(r"^\d{8}$")            # trend API 의 bizdate (20260916)


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update({"User-Agent": C.USER_AGENT, "Referer": "https://finance.naver.com/"})
        _SESSION = s
    return _SESSION


def _get(url: str, params: dict, encoding: str | None = None) -> str:
    last = None
    for i in range(C.HTTP_TRIES):
        try:
            r = session().get(url, params=params, timeout=C.HTTP_TIMEOUT)
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
            return r.text
        except Exception as e:                      # 에러를 삼키지 않는다
            last = e
            time.sleep(0.4 * (2 ** i))
    raise RuntimeError(f"네이버 요청 실패 {url} {params}: {last}")


def _get_json(url: str, params: dict):
    """JSON 을 받는다. 200 인데 JSON 이 아니면 **그 사실을 사유에 남긴다.**

    옛 수집기가 조용히 빈 표를 돌려주는 바람에 화면이 바뀐 것을 아무도 몰랐다.
    같은 일이 반복되지 않도록, 파싱 실패는 본문 앞부분과 함께 예외로 올린다.
    """
    last = None
    for i in range(C.HTTP_TRIES):
        try:
            r = session().get(url, params=params, timeout=C.HTTP_TIMEOUT,
                              headers={"Accept": "application/json, text/plain, */*"})
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                raise RuntimeError(
                    f"200 인데 JSON 이 아니다 {url} {params} — "
                    f"Content-Type {r.headers.get('Content-Type')!r} · "
                    f"머리 {r.text[:160]!r}") from None
        except RuntimeError:
            raise                                   # 위에서 만든 사유는 그대로 올린다
        except Exception as e:
            last = e
            time.sleep(0.4 * (2 ** i))
    raise RuntimeError(f"네이버 요청 실패 {url} {params}: {last}")


# ── 일봉 ──────────────────────────────────────────────────────
DEFAULT_START = "19900101"


def daily_ohlcv(symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
    """symbol 은 종목코드 또는 KOSPI/KOSDAQ.

    startTime / endTime 을 비워 보내면 표를 주지 않는다(2026-09-07 CI 에서 2,686종목
    전부 빈 응답). 기본으로 1990-01-01 ~ 오늘을 명시한다.
    반환: date(str YYYY-MM-DD) · open · high · low · close · volume · frgn_rate
    """
    txt = raw_sise(symbol, start, end)
    return parse_sise(txt)


def raw_sise(symbol: str, start: str = "", end: str = "") -> str:
    return _get(C.SISE_URL, {
        "symbol": symbol, "requestType": 1,
        "startTime": (start or DEFAULT_START).replace("-", ""),
        "endTime": (end or dt.date.today().strftime("%Y%m%d")).replace("-", ""),
        "timeframe": "day",
    })


def sample(code: str = "005930", log=print) -> None:
    """원문 표본을 로그에 남긴다. 응답 형태가 바뀌었을 때 가장 먼저 볼 것."""
    txt = raw_sise(code)
    log(f"  siseJson {code}: {len(txt)}B · 머리 {txt[:160]!r}")
    px = parse_sise(txt)
    log(f"  → 파싱 {len(px)}행" + (f" · {px['date'].iloc[0]}~{px['date'].iloc[-1]}" if len(px) else ""))
    js = _get_json(C.TREND_URL.format(code=code),
                   {C.TREND_PAGESIZE_PARAM: C.FLOW_ROWS_PER_PAGE})
    fl = parse_trend(js)
    log(f"  trend {code}: {len(js) if isinstance(js, list) else '?'}건 · 파싱 {len(fl)}행"
        + (f" · 최근 {fl['date'].iloc[0]} 기관 {fl['inst_net'].iloc[0]:,.0f} 외인 {fl['frgn_net'].iloc[0]:,.0f}"
           if len(fl) else f" · 응답 머리 {str(js)[:200]!r}"))


def parse_sise(txt: str) -> pd.DataFrame:
    """siseJson 응답(작은따옴표 섞인 JS 배열)을 표로. 수집과 분리해 두어야 테스트가 된다."""
    body = txt.lstrip("\ufeff").strip().replace("\r", "")
    if not body or body in ("[]", "[[]]"):
        return _empty_px()
    body = body.replace("'", '"')
    body = re.sub(r",\s*]", "]", body)
    rows = json.loads(body)
    if not rows or len(rows) < 2:
        return _empty_px()
    head = [str(x).strip() for x in rows[0]]
    df = pd.DataFrame(rows[1:], columns=head)
    ren = {"날짜": "date", "시가": "open", "고가": "high", "저가": "low",
           "종가": "close", "거래량": "volume", "외국인소진율": "frgn_rate"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    for c in ("open", "high", "low", "close", "volume", "frgn_rate"):
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d",
                                errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["date", "close"])
    return df[["date", "open", "high", "low", "close", "volume", "frgn_rate"]] \
        .sort_values("date").reset_index(drop=True)


def _empty_px() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "frgn_rate"])


# ── 투자자별 순매매 ───────────────────────────────────────────
def investor_flows(code: str, pages: int = 1) -> pd.DataFrame:
    """투자자별 순매매를 pages 만큼 받는다 (1페이지 = 20거래일).

    반환: date · close · chg_pct · volume · inst_net · frgn_net · frgn_hold · frgn_rate
          (inst_net / frgn_net 은 **순매매량**, 주 단위)

    옛 frgn.naver HTML 표는 사라졌다(config.TREND_URL 주석 참고). 지금은 JSON 을
    한 번에 받는다 — 이 API 는 페이지를 나눠 주지 않고 `pageSize` 로 깊이를
    늘리므로, 호출부가 쓰던 pages 의미(20거래일 단위)를 그대로 환산한다.
    """
    n = max(1, int(pages)) * C.FLOW_ROWS_PER_PAGE
    js = _get_json(C.TREND_URL.format(code=code), {C.TREND_PAGESIZE_PARAM: n})
    return parse_trend(js)


def parse_trend(js) -> pd.DataFrame:
    """trend API 응답(JSON 배열)을 표로. 수집과 분리해야 테스트가 된다.

    숫자는 문자열로 온다 — "+85,620" · "40,550" · "31.13%". `_num` 이 쉼표·부호·
    퍼센트를 떼고 float 로 만든다. 부호는 순매수/순매도라 반드시 보존한다.

    `frgn_hold`(외국인 보유주수)는 이 응답에 **없다.** 지어내지 않고 NaN 으로
    둔다 — 지금 이 열을 쓰는 계산은 없고(외국인 지표는 frgn_rate 로 잰다),
    없는 것을 0 으로 채우면 '보유 0주' 라는 거짓이 된다.

    `chg_pct`는 응답에 직접 없지만 종가와 전일 대비로 **계산된다.**
    (종가 40,550 · 전일비 -200 → 전일 40,750 → -0.49%)
    계산되는 값이므로 채운다. 전일 종가가 0 이면 나눌 수 없으니 NaN 이다.
    """
    if not isinstance(js, list) or not js:
        return _empty_flow()
    out = []
    for x in js:
        if not isinstance(x, dict):
            continue
        d = str(x.get("bizdate") or "").strip()
        if not _YMD_RE.match(d):
            continue                       # 날짜가 없거나 형식이 다르면 버린다
        close = _num(x.get("closePrice"))
        diff = _num(x.get("compareToPreviousClosePrice"))
        prev = close - diff
        out.append({
            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
            "close": close,
            "chg_pct": (round(diff / prev * 100, 2)
                        if prev and not (pd.isna(close) or pd.isna(diff))
                        else float("nan")),
            "volume": _num(x.get("accumulatedTradingVolume")),
            "inst_net": _num(x.get("organPureBuyQuant")),
            "frgn_net": _num(x.get("foreignerPureBuyQuant")),
            "frgn_hold": float("nan"),     # 응답에 없다. 지어내지 않는다.
            "frgn_rate": _num(x.get("foreignerHoldRatio")),
        })
    if not out:
        return _empty_flow()
    df = pd.DataFrame(out).drop_duplicates("date")
    return df.sort_values("date", ascending=False).reset_index(drop=True)


def _empty_flow() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "close", "chg_pct", "volume",
                                 "inst_net", "frgn_net", "frgn_hold", "frgn_rate"])


def _num(s: str) -> float:
    s = (s or "").replace(",", "").replace("%", "").replace("+", "").strip()
    if s in ("", "-", "--"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


# ── 페이지 수 산정 ────────────────────────────────────────────
def _pages_needed(as_of: str, need_rows: int = None, today: str | None = None) -> int:
    """as_of 기준 need_rows 영업일을 확보하려면 몇 페이지가 필요한지.

    frgn 표는 **오늘부터** 역순이다. 과거 날짜로 돌릴 때 1페이지만 받으면
    as_of 절단 후 남는 행이 0 이 되어 수급이 통째로 비어 버린다.
    (9/3 기준으로 이 절단·확대를 빠뜨렸을 때 결과가 27.0% → 32.4% 로 바뀌었다.)
    """
    need_rows = need_rows or (max(C.FLOW_WINDOWS) + 5)
    end = np.datetime64(today or dt.date.today().isoformat())
    start = np.datetime64(str(as_of)[:10])
    after = max(0, int(np.busday_count(start, end)))
    return int(math.ceil((after + need_rows) / C.FLOW_ROWS_PER_PAGE)) + C.FLOW_PAGE_MARGIN
