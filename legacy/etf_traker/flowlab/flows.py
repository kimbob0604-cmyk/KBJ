"""모듈 A — 수급 레이어.

newhigh.json 의 achieved + proximity 종목에 네이버 투자자별 순매매를 결합한다.

지표
  inst_/frgn_/net_{1,5,20}d_eok   일별 순매매량 × 그날 종가를 합산, 억원
  intensity_5d_bp                 5일 (기관+외인) 순매수 / 시가총액, bp
  concentration_1d_pct            당일 (기관+외인) 순매수 / 당일 거래대금, %
  frgn_rate_chg_{5,20}d_pp        외국인 보유율(소진율) 변화, %p
  flow_grade                      쌍끌이 / 기관주도 / 외인주도 / 개인주도 (5일 기준)
  supported                       개인주도가 아니고 강도 >= 20bp

**as_of 절단이 필수다.** frgn 표는 오늘부터 역순이라 과거 날짜로 돌리면서
절단을 빠뜨리면 미래 거래일이 창에 섞인다. 페이지 수는 _pages_needed 가 늘린다.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from . import config as C, prior, report
from .naver import _pages_needed

GRADES = ("쌍끌이", "기관주도", "외인주도", "개인주도")


def _fmt_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _window(fl: pd.DataFrame, n: int) -> pd.DataFrame:
    return fl.head(n)


def _eok(qty: pd.Series, close: pd.Series) -> float:
    """순매매량(주) × 그날 종가(원) 합계를 억원으로."""
    v = (pd.to_numeric(qty, errors="coerce") * pd.to_numeric(close, errors="coerce")).sum()
    return float(v) / 1e8


def _grade(inst_5d: float, frgn_5d: float) -> str:
    if inst_5d > 0 and frgn_5d > 0:
        return "쌍끌이"
    if inst_5d > 0:
        return "기관주도"
    if frgn_5d > 0:
        return "외인주도"
    return "개인주도"


def _rate_chg(fl: pd.DataFrame, n: int) -> float | None:
    r = pd.to_numeric(fl.get("frgn_rate"), errors="coerce")
    if r is None or r.isna().all() or len(r) <= n:
        return None
    a, b = r.iloc[0], r.iloc[n]
    if pd.isna(a) or pd.isna(b):
        return None
    return round(float(a - b), 2)


def metrics(fl: pd.DataFrame, mktcap_eok: float | None,
            turnover_eok: float | None) -> dict:
    """as_of 로 이미 절단된, 날짜 내림차순 표를 받는다."""
    out: dict = {"flow_days": int(len(fl))}
    for n in C.FLOW_WINDOWS:
        w = _window(fl, n)
        inst = _eok(w["inst_net"], w["close"])
        frgn = _eok(w["frgn_net"], w["close"])
        out[f"inst_{n}d_eok"] = round(inst, 1)
        out[f"frgn_{n}d_eok"] = round(frgn, 1)
        out[f"net_{n}d_eok"] = round(inst + frgn, 1)
        out[f"days_{n}d"] = int(len(w))          # 실제로 몇 거래일을 썼는지 남긴다

    net5 = out["net_5d_eok"]
    out["intensity_5d_bp"] = (round(net5 / mktcap_eok * 10000, 1)
                              if mktcap_eok else None)
    out["concentration_1d_pct"] = (round(out["net_1d_eok"] / turnover_eok * 100, 1)
                                   if turnover_eok else None)
    out["frgn_rate_chg_5d_pp"] = _rate_chg(fl, 5)
    out["frgn_rate_chg_20d_pp"] = _rate_chg(fl, 20)
    out["flow_grade"] = _grade(out["inst_5d_eok"], out["frgn_5d_eok"])
    inten = out["intensity_5d_bp"]
    out["supported"] = bool(out["flow_grade"] != "개인주도"
                            and inten is not None and inten >= C.SUPPORT_INTENSITY_BP)
    return out


def _collect_rows(nh: dict) -> list[dict]:
    rows = []
    for group in ("achieved", "proximity"):
        for s in nh.get(group) or []:
            rows.append({
                "code": s.get("code"), "name": s.get("name"),
                "market": s.get("market"), "sector": s.get("sector"),
                "theme_name": s.get("theme_name"),
                "group": group,
                "label": s.get("label"), "near_kind": s.get("near_kind"),
                "near_gap": s.get("near_gap"),
                "close": s.get("close"), "chg_pct": s.get("chg_pct"),
                "mktcap": s.get("mktcap"), "turnover": s.get("turnover"),
                "vol_mult": s.get("vol_mult"),
            })
    return rows


def aggregate(rows: list[dict], nh: dict) -> dict:
    have = [r for r in rows if r.get("flow_grade")]
    by_grade = {g: sum(1 for r in have if r["flow_grade"] == g) for g in GRADES}
    sup = [r for r in have if r.get("supported")]
    inten = [r["intensity_5d_bp"] for r in have if r.get("intensity_5d_bp") is not None]
    top = sorted([r for r in have if r.get("net_5d_eok") is not None],
                 key=lambda r: r["net_5d_eok"], reverse=True)[:10]
    return {
        "n_rows": len(rows),
        "n_achieved": sum(1 for r in rows if r["group"] == "achieved"),
        "n_proximity": sum(1 for r in rows if r["group"] == "proximity"),
        "n_with_flows": len(have),
        "coverage_pct": round(len(have) / len(rows) * 100, 1) if rows else None,
        "by_grade": by_grade,
        "by_grade_pct": {g: (round(by_grade[g] / len(have) * 100, 1) if have else None)
                         for g in GRADES},
        "supported_n": len(sup),
        "supported_pct": round(len(sup) / len(have) * 100, 1) if have else None,
        "intensity_5d_bp_median": round(float(np.median(inten)), 1) if inten else None,
        "net_5d_eok_sum": round(sum(r["net_5d_eok"] for r in have), 1) if have else None,
        "top_net_5d": [{k: r[k] for k in ("code", "name", "group", "flow_grade",
                                          "net_5d_eok", "intensity_5d_bp")} for r in top],
        "newhigh_counts": nh.get("counts"),
    }


def run(date: str | None = None, source: str = C.SOURCE_NAVER,
        limit: int | None = None, write_report: bool = True, log=print) -> dict:
    from . import prices

    sd = C.state_dir(date)
    nh = json.loads((sd / "newhigh.json").read_text(encoding="utf-8"))
    as_of = nh.get("as_of")
    if not as_of:
        raise RuntimeError(f"{sd}/newhigh.json 에 as_of 가 없다")

    drift = C.settings_drift()
    if drift:
        log("  경고 — 엔진 settings.yaml 과 기준값이 다르다: " + "; ".join(drift))

    rows = _collect_rows(nh)
    if limit:
        rows = rows[:limit]
    pages = _pages_needed(as_of)
    mod = prices.source_module(source)
    log(f"기준일 {as_of} · 대상 {len(rows)}종목 · frgn {pages}페이지 · source={source}")

    missing = []
    for i, r in enumerate(rows, 1):
        try:
            fl = mod.investor_flows(r["code"], pages=pages)
        except Exception as e:
            missing.append({"code": r["code"], "name": r["name"],
                            "reason": f"{type(e).__name__}: {e}"})
            continue
        if fl is None or fl.empty:
            missing.append({"code": r["code"], "name": r["name"], "reason": "수급 표 없음"})
            continue
        fl = fl[fl["date"] <= as_of]                      # ★ as_of 절단 (필수)
        fl = fl.sort_values("date", ascending=False).reset_index(drop=True)
        if fl.empty:
            missing.append({"code": r["code"], "name": r["name"],
                            "reason": f"as_of({as_of}) 이전 행 없음 — 페이지 부족"})
            continue
        r.update(metrics(fl, r.get("mktcap"), r.get("turnover")))
        r["flow_last_date"] = str(fl["date"].iloc[0])
        r["flow_stale"] = bool(r["flow_last_date"] < as_of)
        if i % 50 == 0:
            log(f"  수급 {i}/{len(rows)}")

    prior_note = prior.attach(rows)
    agg = aggregate(rows, nh)

    payload = {
        "source": source,
        "as_of": as_of,
        "generated_at": _fmt_now(),
        "module": "flowlab.flows",
        "params": {
            "windows": list(C.FLOW_WINDOWS),
            "support_intensity_bp": C.SUPPORT_INTENSITY_BP,
            "min_mktcap_eok": C.MIN_MKTCAP_EOK,
            "min_turnover_eok": C.MIN_TURNOVER_EOK,
            "frgn_pages": pages,
        },
        "prior": prior_note,
        "aggregate": agg,
        "missing": missing,
        "notes": _notes(source, missing, rows),
        "rows": rows,
    }
    (sd / "flows.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  flows.json → {sd / 'flows.json'}")

    if write_report:
        html = report.flows_html(payload)
        hp = C.docs_dir(source) / f"flows-{as_of.replace('-', '')}.html"
        hp.write_text(html, encoding="utf-8")
        md = report.flows_md(payload)
        mp = C.OUT / f"flows-{as_of.replace('-', '')}.md"
        mp.write_text(md, encoding="utf-8")
        log(f"  리포트 → {hp}")
        log(f"  코멘트 문단 → {mp}")
        report.relink_board(hp.parent, as_of, log=log)
    return payload


def _notes(source: str, missing: list, rows: list) -> list[str]:
    n = []
    if source == C.SOURCE_DEMO:
        n.append("합성(demo) 소스로 산출 — 수치는 시장 사실이 아님. 배관 점검용")
    if missing:
        n.append(f"수급 결합 실패 {len(missing)}종목 — 상단 missing 참조")
    short = [r for r in rows if r.get("days_5d") not in (None, 5)]
    if short:
        n.append(f"5일 창이 5거래일에 못 미친 종목 {len(short)}종목 — days_5d 필드 확인")
    n.append("순매매 금액 = 일별 순매매량 × 그날 종가 합계 (억원). 네이버 표에 금액이 없어 환산값")
    return n
