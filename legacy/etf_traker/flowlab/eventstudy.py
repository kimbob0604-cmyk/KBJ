"""모듈 B — 신고가 이벤트 스터디.

최근 N년(기본 3년) 동안의 52주·60일 신고가와 근접을 전 종목에서 뽑아,
소속 지수(KOSPI·KOSDAQ) 대비 후행 초과수익을 축별로 집계한다.

축
  거래량 배수 (20일 평균 대비) · 신선도(신규/연속) · 연속 일수 · 라벨(w52/d60)
  근접 갭 구간별 5일 내 돌파 전환율

한계는 산출물과 리포트 하단에 함께 적는다. 최신 유니버스 기준이라 상장폐지
종목이 빠지고(생존편향), 과거 시총은 현재 상장주식수 × 당시 종가 근사다.
`hist`(역사적 신고가)는 창 길이 한계로 제외하고 w52·d60 만 본다.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from . import config as C, prices, report

EVENT_COLS = ["date", "code", "name", "market", "kind", "vol_mult", "freshness",
              "streak", "mktcap_eok", "turnover_eok", "chg_pct",
              "ret_5d_pct", "ret_20d_pct", "idx_5d_pct", "idx_20d_pct",
              "exc_5d_pct", "exc_20d_pct"]


def _bucket(v, buckets):
    if v is None or (isinstance(v, float) and v != v):
        return None
    for lo, hi, label in buckets:
        if v >= lo and (hi is None or v < hi):
            return label
    return None


def _streak_bucket(n):
    for lo, hi, label in C.STREAK_BUCKETS:
        if n >= lo and (hi is None or n <= hi):
            return label
    return None


def _segment_after_break(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """일간 |변동| 이 임계를 넘는 계단(액면병합·감자 등) 이후 구간만 남긴다."""
    chg = df["close"].astype(float).pct_change().abs() * 100
    bad = np.where(chg > C.BREAK_MAX_ABS_CHG_PCT)[0]
    if len(bad) == 0:
        return df, False
    return df.iloc[bad[-1]:].reset_index(drop=True), True


def _index_frames(source: str, markets, as_of: str, log) -> dict[str, pd.Series]:
    out = {}
    for m in sorted(set(markets)):
        try:
            ix = prices.index(m, source, need_through=as_of)
        except Exception as e:
            log(f"  지수 {m} 실패: {type(e).__name__}: {e}")
            continue
        if ix is None or ix.empty:
            log(f"  지수 {m} 비어 있음 — 해당 시장 종목은 초과수익 계산 불가")
            continue
        out[m] = ix.set_index("date")["close"].astype(float)
    return out


def _events_for(code: str, meta: dict, px: pd.DataFrame, idx: pd.Series,
                start_date: str) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    df = px.dropna(subset=["close", "high"]).sort_values("date").reset_index(drop=True)
    if len(df) <= C.W52_DAYS + max(C.STUDY_HORIZONS):
        return pd.DataFrame(), pd.DataFrame(), False
    df, broke = _segment_after_break(df)
    if len(df) <= C.W52_DAYS + max(C.STUDY_HORIZONS):
        return pd.DataFrame(), pd.DataFrame(), broke

    high = df["high"].astype(float)
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)

    ref52 = high.shift(1).rolling(C.W52_DAYS, min_periods=C.W52_DAYS).max()
    ref60 = high.shift(1).rolling(C.D60_DAYS, min_periods=C.D60_DAYS).max()
    hit52 = high > ref52
    hit60 = high > ref60
    kind = np.where(hit52, "w52", np.where(hit60, "d60", None))
    hit = pd.Series(hit52 | hit60, index=df.index).fillna(False)

    avg20 = vol.shift(1).rolling(C.VOL_AVG_DAYS, min_periods=5).mean()
    vol_mult = vol / avg20

    shares = meta.get("shares")
    mktcap = close * shares / 1e8 if shares else pd.Series(np.nan, index=df.index)
    turnover = vol * close / 1e8
    chg = close.pct_change() * 100

    # 연속 일수 — 직전 영업일에도 신고가였으면 이어감
    streak = np.zeros(len(df), dtype=int)
    run = 0
    for i, h in enumerate(hit.to_numpy()):
        run = run + 1 if h else 0
        streak[i] = run

    ix = idx.reindex(df["date"].values) if idx is not None else pd.Series(np.nan, index=df.index)
    ix = pd.Series(np.asarray(ix, dtype=float), index=df.index).ffill()

    fwd = {}
    for h in C.STUDY_HORIZONS:
        fwd[f"ret_{h}d_pct"] = (close.shift(-h) / close - 1) * 100
        fwd[f"idx_{h}d_pct"] = (ix.shift(-h) / ix - 1) * 100
        fwd[f"exc_{h}d_pct"] = fwd[f"ret_{h}d_pct"] - fwd[f"idx_{h}d_pct"]

    base = pd.DataFrame({
        "date": df["date"], "code": code, "name": meta.get("name"),
        "market": meta.get("market"), "kind": kind, "vol_mult": vol_mult,
        "streak": streak, "mktcap_eok": mktcap, "turnover_eok": turnover,
        "chg_pct": chg, **fwd,
    })
    base["freshness"] = np.where(base["streak"] == 1, "신규",
                                 np.where(base["streak"] > 1, "연속", None))

    screen = (base["mktcap_eok"] >= C.MIN_MKTCAP_EOK) & \
             (base["turnover_eok"] >= C.MIN_TURNOVER_EOK) & \
             (base["date"] >= start_date)
    ev = base[screen & pd.notna(base["kind"]) &
              base[f"exc_{max(C.STUDY_HORIZONS)}d_pct"].notna()].copy()

    # 근접 — 신고가 미달, 갭 5% 이내. 스크리닝(시총·거래대금)은 신고가와 같은 기준
    gap52 = (ref52 - close) / ref52 * 100
    gap60 = (ref60 - close) / ref60 * 100
    in52 = (gap52 > 0) & (gap52 <= C.PROXIMITY_MAX_GAP_PCT)
    in60 = (gap60 > 0) & (gap60 <= C.PROXIMITY_MAX_GAP_PCT)
    near_gap = np.where(in52, gap52, np.where(in60, gap60, np.nan))
    near_kind = np.where(in52, "w52", np.where(in60, "d60", None))
    # 이후 BREAKOUT_WINDOW 영업일 안에 신고가가 났는지
    fut = pd.Series(hit.to_numpy(), index=df.index)
    conv = pd.Series(False, index=df.index)
    for k in range(1, C.BREAKOUT_WINDOW + 1):
        conv = conv | fut.shift(-k).fillna(False).astype(bool)
    tail_ok = pd.Series(np.arange(len(df)) < len(df) - C.BREAKOUT_WINDOW, index=df.index)

    prox = pd.DataFrame({
        "date": df["date"], "code": code, "name": meta.get("name"),
        "market": meta.get("market"), "near_kind": near_kind, "gap_pct": near_gap,
        "vol_mult": vol_mult, "mktcap_eok": mktcap, "converted": conv,
    })
    prox["turnover_eok"] = turnover
    prox = prox[pd.notna(prox["gap_pct"]) & (~hit.to_numpy()) & tail_ok
                & (prox["mktcap_eok"] >= C.MIN_MKTCAP_EOK)
                & (prox["turnover_eok"] >= C.MIN_TURNOVER_EOK)
                & (prox["date"] >= start_date)].copy()
    return ev, prox, broke


def _agg(g: pd.DataFrame, label: str, key: str = "bucket") -> dict:
    e5, e20 = g["exc_5d_pct"], g["exc_20d_pct"]
    return {
        key: label, "n": int(len(g)),
        "win_5d_pct": round(float((e5 > 0).mean() * 100), 1) if len(g) else None,
        "med_5d_pct": round(float(e5.median()), 2) if len(g) else None,
        "avg_5d_pct": round(float(e5.mean()), 2) if len(g) else None,
        "med_20d_pct": round(float(e20.median()), 2) if len(g) else None,
        "avg_20d_pct": round(float(e20.mean()), 2) if len(g) else None,
        "win_20d_pct": round(float((e20 > 0).mean() * 100), 1) if len(g) else None,
    }


def _by(ev: pd.DataFrame, col: str, order=None) -> list[dict]:
    out = []
    keys = order if order is not None else sorted(ev[col].dropna().unique())
    for k in keys:
        g = ev[ev[col] == k]
        if len(g):
            out.append(_agg(g, k))
    return out


def run(years: int = C.STUDY_YEARS, source: str = C.SOURCE_NAVER,
        date: str | None = None, limit: int | None = None,
        write_report: bool = True, log=print) -> dict:
    sd = C.state_dir(date)
    uni = json.loads((sd / "universe.json").read_text(encoding="utf-8"))
    as_of = uni.get("as_of")
    stocks = uni.get("stocks") or []
    if limit:
        stocks = stocks[:limit]

    meta = {}
    for s in stocks:
        close, mktcap = s.get("close"), s.get("mktcap")
        meta[s["code"]] = {
            "name": s.get("name"), "market": s.get("market"),
            # 과거 시총 = **현재** 상장주식수 × 당시 종가 근사
            "shares": (mktcap * 1e8 / close) if (close and mktcap) else None,
        }
    codes = list(meta)
    start_date = str(np.datetime64(as_of) - np.timedelta64(int(365.25 * years), "D"))
    log(f"기준일 {as_of} · 유니버스 {len(codes)}종목 · 창 {years}년({start_date}~) "
        f"· source={source}")

    px = prices.bulk(codes, source, need_through=as_of, log=log)
    idxs = _index_frames(source, [m["market"] for m in meta.values()], as_of, log)
    log(f"  일봉 {len(px)}종목 · 지수 {', '.join(idxs) or '없음'}")

    evs, prs, n_broken, n_skipped = [], [], 0, 0
    for code, df in px.items():
        m = meta[code]
        ev, pr, broke = _events_for(code, m, df, idxs.get(m["market"]), start_date)
        n_broken += int(broke)
        if ev.empty and pr.empty:
            n_skipped += 1
        if not ev.empty:
            evs.append(ev)
        if not pr.empty:
            prs.append(pr)

    if not evs:
        raise RuntimeError("이벤트가 없다 — 일봉 길이나 스크리닝 기준을 확인하라")
    ev = pd.concat(evs, ignore_index=True)
    pr = pd.concat(prs, ignore_index=True) if prs else pd.DataFrame(
        columns=["gap_pct", "converted"])

    ev["vol_bucket"] = ev["vol_mult"].map(lambda v: _bucket(v, C.VOL_BUCKETS))
    ev["streak_bucket"] = ev["streak"].map(_streak_bucket)
    ev["vf"] = ev["vol_bucket"].astype(str) + " · " + ev["freshness"].astype(str)

    vol_order = [b[2] for b in C.VOL_BUCKETS]
    streak_order = [b[2] for b in C.STREAK_BUCKETS]
    vf_order = [f"{v} · {f}" for v in vol_order for f in ("신규", "연속")]

    prox_rows = []
    if len(pr):
        pr["gap_bucket"] = pr["gap_pct"].map(lambda v: _bucket(v, C.GAP_BUCKETS))
        for lo, hi, label in C.GAP_BUCKETS:
            g = pr[pr["gap_bucket"] == label]
            if len(g):
                prox_rows.append({"bucket": label, "n": int(len(g)),
                                  "conv_pct": round(float(g["converted"].mean() * 100), 1)})

    payload = {
        "source": source,
        "as_of": as_of,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "module": "flowlab.eventstudy",
        "years": years,
        "window_start": start_date,
        "universe_n": len(codes),
        "universe_priced": len(px),
        "params": {
            "horizons": list(C.STUDY_HORIZONS), "w52": C.W52_DAYS, "d60": C.D60_DAYS,
            "min_mktcap_eok": C.MIN_MKTCAP_EOK, "min_turnover_eok": C.MIN_TURNOVER_EOK,
            "vol_avg_days": C.VOL_AVG_DAYS, "breakout_window": C.BREAKOUT_WINDOW,
            "break_max_abs_chg_pct": C.BREAK_MAX_ABS_CHG_PCT,
        },
        "overall": _agg(ev, "전체"),
        "by_volmult": _by(ev, "vol_bucket", vol_order),
        "by_freshness": _by(ev, "freshness", ["신규", "연속"]),
        "by_streak": _by(ev, "streak_bucket", streak_order),
        "by_kind": _by(ev, "kind", ["w52", "d60"]),
        "by_volmult_freshness": _by(ev, "vf", vf_order),
        "proximity": {"n": int(len(pr)), "by_gap": prox_rows},
        "limits": _limits(source, years, n_broken, n_skipped, len(px), len(codes)),
    }
    (C.OUT / "eventstudy.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    keep = [c for c in EVENT_COLS if c in ev.columns] + ["vol_bucket", "streak_bucket"]
    ev[keep].to_csv(C.OUT / "eventstudy_events.csv.gz", index=False, compression="gzip")
    log(f"  집계 → {C.OUT / 'eventstudy.json'}")
    log(f"  이벤트 원본 {len(ev):,}건 → {C.OUT / 'eventstudy_events.csv.gz'}")

    if write_report:
        hp = C.docs_dir(source) / "eventstudy.html"
        hp.write_text(report.eventstudy_html(payload), encoding="utf-8")
        log(f"  리포트 → {hp}")
        report.relink_board(hp.parent, as_of, log=log)
    return payload


def _limits(source, years, n_broken, n_skipped, n_priced, n_codes) -> list[str]:
    L = []
    if source == C.SOURCE_DEMO:
        L.append("합성(demo) 소스로 산출 — 수치는 시장 사실이 아님. 배관 점검용")
    L.append(f"최신 유니버스 기준({n_priced}/{n_codes}종목 일봉 확보) → 상장폐지 종목 제외, 생존편향")
    L.append("과거 시가총액은 현재 상장주식수 × 당시 종가 근사")
    L.append(f"hist(역사적 신고가)는 {years}년 창 한계로 제외. w52·d60 만 집계")
    L.append("같은 종목의 연속 신고가가 중복 표본으로 들어감 → by_freshness / by_streak 로 분리")
    L.append(f"일간 |변동| {C.BREAK_MAX_ABS_CHG_PCT:.0f}% 초과(액면병합·감자 의심) 종목은 "
             f"단절 이후 구간만 사용 — {n_broken}종목 해당")
    L.append(f"일봉이 짧아 이벤트가 나오지 않은 종목 {n_skipped}종목")
    L.append("초과수익 = 종목 후행수익률 − 소속지수 같은 구간 수익률 (단순 차분)")
    return L
