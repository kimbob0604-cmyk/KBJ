"""누적된 flows.json 을 한 표로 모으고, 이벤트 스터디와 조인한다.

board/state/*/flows.json 을 전부 읽어 flowlab/out/flows_history.csv.gz 로 쌓는다.
같은 (종목, 날짜) 가 이벤트 스터디 원본에 있으면 후행 초과수익을 붙여
**수급 등급별 신고가 후행 성과** 를 낸다. 표본이 작으면 그대로 작다고 적는다.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from . import config as C, report

HIST_CSV = C.OUT / "flows_history.csv.gz"
HIST_JSON = C.OUT / "flows_history.json"
KEEP = ["as_of", "source", "code", "name", "market", "group", "label", "near_kind",
        "close", "chg_pct", "mktcap", "turnover", "vol_mult",
        "inst_1d_eok", "frgn_1d_eok", "net_1d_eok", "inst_5d_eok", "frgn_5d_eok",
        "net_5d_eok", "inst_20d_eok", "frgn_20d_eok", "net_20d_eok", "days_5d",
        "intensity_5d_bp", "concentration_1d_pct", "frgn_rate_chg_5d_pp",
        "frgn_rate_chg_20d_pp", "flow_grade", "supported", "prior_bucket",
        "prior_win_20d_pct"]
MIN_N = 30   # 이 미만 구간은 표에 남기되 '표본 부족' 으로 표시


def collect(log=print) -> pd.DataFrame:
    frames, sources = [], set()
    for d in C.state_dates():
        p = C.STATE / d / "flows.json"
        if not p.exists():
            continue
        fj = json.loads(p.read_text(encoding="utf-8"))
        rows = [r for r in fj.get("rows") or [] if r.get("flow_grade")]
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["as_of"] = fj["as_of"]
        df["source"] = fj.get("source")
        sources.add(fj.get("source"))
        frames.append(df)
    if not frames:
        raise FileNotFoundError("누적할 flows.json 이 없다 — `python3 -m flowlab flows` 또는 backfill 먼저")
    hist = pd.concat(frames, ignore_index=True)
    for c in KEEP:
        if c not in hist.columns:
            hist[c] = np.nan
    hist = hist[KEEP].sort_values(["as_of", "group", "code"]).reset_index(drop=True)
    hist.to_csv(HIST_CSV, index=False, compression="gzip")
    log(f"  누적 {len(hist):,}행 · {hist['as_of'].nunique()}일 "
        f"({hist['as_of'].min()}~{hist['as_of'].max()}) · source={','.join(sorted(map(str, sources)))}")
    return hist


def _stat(g: pd.DataFrame, label: str) -> dict:
    e = g["exc_20d_pct"]
    return {
        "bucket": label, "n": int(len(g)),
        "small_sample": bool(len(g) < MIN_N),
        "win_5d_pct": round(float((g["exc_5d_pct"] > 0).mean() * 100), 1) if len(g) else None,
        "med_5d_pct": round(float(g["exc_5d_pct"].median()), 2) if len(g) else None,
        "med_20d_pct": round(float(e.median()), 2) if len(g) else None,
        "avg_20d_pct": round(float(e.mean()), 2) if len(g) else None,
        "win_20d_pct": round(float((e > 0).mean() * 100), 1) if len(g) else None,
    }


def join_eventstudy(hist: pd.DataFrame, log=print) -> dict | None:
    p = C.OUT / "eventstudy_events.csv.gz"
    if not p.exists():
        log("  이벤트 스터디 원본 없음 — 조인 생략 (`python3 -m flowlab study` 먼저)")
        return None
    ev = pd.read_csv(p, dtype={"code": str, "date": str})
    ev = ev[["code", "date", "kind", "exc_5d_pct", "exc_20d_pct"]].dropna(subset=["exc_20d_pct"])
    ach = hist[hist["group"] == "achieved"]
    j = ach.merge(ev, left_on=["code", "as_of"], right_on=["code", "date"], how="inner")
    if j.empty:
        log("  조인 결과 0건 — 후행 20일이 아직 안 지난 날짜만 있거나 스크리닝이 다르다")
        return {"n_flows_achieved": int(len(ach)), "n_joined": 0}
    grades = ("쌍끌이", "기관주도", "외인주도", "개인주도")
    out = {
        "n_flows_achieved": int(len(ach)),
        "n_joined": int(len(j)),
        "dates": int(j["as_of"].nunique()),
        "by_flow_grade": [_stat(j[j["flow_grade"] == g], g) for g in grades
                          if (j["flow_grade"] == g).any()],
        "by_supported": [_stat(j[j["supported"] == v], "supported" if v else "그 외")
                         for v in (True, False) if (j["supported"] == v).any()],
        "note": (f"신고가 달성 행 중 이벤트 스터디 원본과 (종목, 날짜) 가 맞은 {len(j):,}건. "
                 f"구간 n<{MIN_N} 은 표본 부족으로 표시. 후행 20일이 지나야 조인된다"),
    }
    log(f"  이벤트 스터디 조인 {len(j):,}건 · 등급 {len(out['by_flow_grade'])}구간")
    return out


def summarize(hist: pd.DataFrame, joined: dict | None) -> dict:
    by_date = (hist.groupby("as_of")
               .agg(n=("code", "size"),
                    n_achieved=("group", lambda s: int((s == "achieved").sum())),
                    supported_n=("supported", lambda s: int(s.fillna(False).astype(bool).sum())),
                    intensity_med=("intensity_5d_bp", "median"))
               .reset_index())
    grades = hist.groupby(["as_of", "flow_grade"]).size().unstack(fill_value=0)
    by_date = by_date.merge(grades.reset_index(), on="as_of", how="left")
    by_date["supported_pct"] = (by_date["supported_n"] / by_date["n"] * 100).round(1)
    by_date["intensity_med"] = by_date["intensity_med"].round(1)
    return {
        "source": ",".join(sorted(hist["source"].dropna().astype(str).unique())),
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "module": "flowlab.history",
        "n_rows": int(len(hist)),
        "n_dates": int(hist["as_of"].nunique()),
        "date_range": [str(hist["as_of"].min()), str(hist["as_of"].max())],
        "by_date": json.loads(by_date.to_json(orient="records", force_ascii=False)),
        "eventstudy_join": joined,
        "notes": [
            "누적 원본은 flows_history.csv.gz. 같은 (종목, 날짜) 는 최신 flows.json 이 덮는다",
            "등급은 5일 순매매 기준이라 인접한 날짜의 행이 같은 5일 창을 공유함 — 날짜별 행은 독립 표본이 아님",
            "후행 성과 조인은 신고가 달성 행만 대상. 후행 20일이 지나지 않은 마지막 20거래일은 들어가지 않음",
            "합성(demo) 날짜가 섞이면 source 열로 구분한다 — 실데이터 통계에 합산하지 않는다",
        ],
    }


def run(write_report: bool = True, log=print) -> dict:
    hist = collect(log=log)
    joined = join_eventstudy(hist, log=log)
    summary = summarize(hist, joined)
    HIST_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  → {HIST_CSV}")
    log(f"  → {HIST_JSON}")
    if write_report:
        # 합성 날짜가 하나라도 섞이면 발행 폴더에 넣지 않는다
        src = C.SOURCE_DEMO if C.SOURCE_DEMO in summary["source"] else C.SOURCE_NAVER
        hp = C.docs_dir(src) / "flows-history.html"
        hp.write_text(report.history_html(summary), encoding="utf-8")
        log(f"  리포트 → {hp}")
        report.relink_board(hp.parent, summary["date_range"][1], log=log)
    return summary
