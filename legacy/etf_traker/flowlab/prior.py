"""모듈 B 결과를 모듈 A 에 되먹인다.

거래량 배수 구간별 과거 20일 승률·중앙값을 수급 표에 붙인다. 이벤트 스터디를
아직 안 돌렸으면 아무것도 붙이지 않고 그 사실만 남긴다 (없는 값을 만들지 않는다).
"""
from __future__ import annotations

import json

from . import config as C

PATH = C.OUT / "eventstudy.json"


def bucket_of(vol_mult) -> str | None:
    if vol_mult is None:
        return None
    try:
        v = float(vol_mult)
    except (TypeError, ValueError):
        return None
    for lo, hi, label in C.VOL_BUCKETS:
        if v >= lo and (hi is None or v < hi):
            return label
    return None


def load_prior() -> dict | None:
    if not PATH.exists():
        return None
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def attach(rows: list[dict]) -> dict:
    es = load_prior()
    if not es:
        for r in rows:
            r["prior_bucket"] = bucket_of(r.get("vol_mult"))
            r["prior_win_20d_pct"] = None
        return {"available": False,
                "note": "이벤트 스터디 미실행 — `python3 -m flowlab study` 후 다시 붙는다"}

    table = {b["bucket"]: b for b in (es.get("by_volmult") or [])}
    hit = 0
    for r in rows:
        b = bucket_of(r.get("vol_mult"))
        r["prior_bucket"] = b
        stat = table.get(b) if b else None
        r["prior_win_20d_pct"] = (stat or {}).get("win_20d_pct")
        r["prior_med_20d_pct"] = (stat or {}).get("med_20d_pct")
        r["prior_n"] = (stat or {}).get("n")
        if stat:
            hit += 1
    return {
        "available": True,
        "source": es.get("source"),
        "as_of": es.get("as_of"),
        "years": es.get("years"),
        "n_events": (es.get("overall") or {}).get("n"),
        "attached": hit,
        "note": "거래량 배수 구간별 과거 통계. 표본 기간·생존편향 한계는 eventstudy 리포트 참조",
    }
