"""월물 전광판의 ATM 누락을 메울 경로를 잰다 (PLAN §3 #11 후속).

2026-09-28 13:33 실측: 전광판 콜/풋은 각 100행 상한이고 행사가 내림차순이라,
월물은 최고 행사가부터 100개(1595.0~1347.5)만 오고 ATM(약 1100) 구간이 빠졌다.
메울 후보를 추측 없이 잰다.

  1. 종목코드 규칙 — 전광판이 준 행의 (행사가, 단축코드) 짝을 모은다
  2. KIS 지수선물옵션 마스터에 옵션 종목이 있는가 — 있으면 ATM 코드를 거기서 얻는다
  3. 옵션 단건 현재가(FHMIF10000000, 시장 O) — 미결제약정·그릭스·IV 를 주는가
  4. 관심종목 멀티 시세(최대 30종목) 가 옵션 코드를 받는가 — 받으면 30개씩 묶어 조회
  5. 마스터에서 뽑은 월물 ATM±N 코드를 단건 현재가로 FILL_RPS 로 연달아 조회 —
     한도초과 없이 다 오는가, 행사가가 맞는가, OI·IV·감마가 채워져 오는가
  6. 위클리 코드도 단건 현재가가 받는가
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import Any

from data.kis.master import download_fo_master
from scripts.probe_callput import callput_params
from scripts.probe_common import (
    P_CALLPUT,
    P_PRICE,
    TR_CALLPUT,
    TR_PRICE,
    Ctx,
    brief,
    futures_codes,
    rows,
)
from scripts.probe_option_list import expiries

P_MULTI = "/uapi/domestic-stock/v1/quotations/intstock-multprice"
TR_MULTI = "FHKST11300006"

FILL_ATM_RANGE = 20  # ATM±20 행사가 × 콜·풋 = 82건
FILL_RPS = 5.0  # #10 실측 무오류 단계(5/s)를 연속 부하로 확인한다
FILL_FIELDS = ("hts_otst_stpl_qty", "hts_ints_vltl", "gama", "delta_val", "futs_prpr")


@dataclass(frozen=True)
class MasterRow:
    """마스터 한 줄. 옵션이면 cp 가 'C'·'P', strike 가 채워진다.

    다섯째 필드는 콜/풋이 아니라 ATM 구분(1 ATM·2 ITM·3 OTM)이다 — 2026-09-28 에 이를
    콜/풋으로 읽어 ATM 표시 행사가(202610 의 1125.0)를 버린 적이 있다. 콜/풋은 이름의
    결제월 앞 토큰(C·P 로 끝남)에서, 행사가는 여섯째 필드(0 이면 옵션 아님)에서 읽는다.
    """

    kind: str
    code: str
    name: str
    cp: str
    strike: float | None
    moneyness: str = ""

    @property
    def series(self) -> str:
        """이름에서 행사가를 뗀 부분 — 같은 만기·같은 콜/풋이면 같다 ('C 202610')."""
        return self.name.rsplit(maxsplit=1)[0].strip() if self.strike is not None else self.name


def parse_master_line(line: str) -> MasterRow | None:
    f = line.split("|")
    if len(f) < 6 or not f[1].strip():
        return None
    name = f[3].strip()
    try:
        strike: float | None = float(f[5]) or None
    except ValueError:
        strike = None
    cp = ""
    toks = name.split()
    for i, t in enumerate(toks):
        if i > 0 and re.fullmatch(r"\d{6}|\d{4}W\d", t):
            cp = toks[i - 1][-1:] if toks[i - 1][-1:] in ("C", "P") else ""
            break
    if not cp:
        strike = None  # 선물·스프레드 등 옵션이 아닌 줄
    return MasterRow(f[0].strip(), f[1].strip(), name, cp, strike, f[4].strip())


def series_rows(master: list[MasterRow], codes: list[str]) -> dict[str, list[MasterRow]]:
    """전광판이 준 코드가 속한 시리즈별로, 그 시리즈의 마스터 행 전체(행사가 오름차순)."""
    by_code = {m.code: m for m in master}
    names = {by_code[c].series for c in codes if c in by_code and by_code[c].strike is not None}
    out: dict[str, list[MasterRow]] = {}
    for s in sorted(names):
        rs = [m for m in master if m.strike is not None and m.series == s]
        out[s] = sorted(rs, key=lambda m: m.strike or 0.0)
    return out


def atm_window(rs: list[MasterRow], atm: float, n: int) -> list[MasterRow]:
    """atm 에 가장 가까운 행사가를 가운데로 양쪽 n 개씩."""
    if not rs:
        return []
    i = min(range(len(rs)), key=lambda j: abs((rs[j].strike or 0.0) - atm))
    return rs[max(0, i - n) : i + n + 1]


def code_pairs(rs: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(str(r.get("acpr", "")).strip(), str(r.get("optn_shrn_iscd", "")).strip()) for r in rs]


def master_options(ctx: Ctx) -> list[MasterRow]:
    """마스터의 줄 종류별 개수·표본을 기록하고, 파싱한 행 전체를 돌려준다."""
    raw = download_fo_master(read_s=40.0, total_s=60.0)  # KBJ P2: 배포 주소는 KBJ 모듈에만
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    lines = text.splitlines()
    kinds = Counter(ln.split("|", 1)[0] for ln in lines)
    samples: dict[str, list[str]] = {}
    for ln in lines:
        k = ln.split("|", 1)[0]
        if len(samples.setdefault(k, [])) < 3:
            samples[k].append(ln[:160])
    ctx.findings["master"] = {
        "lines": len(lines),
        "kinds": dict(kinds),
        "samples": samples,
        "with_202610": [ln[:160] for ln in lines if "202610" in ln][:8],
    }
    return [m for m in (parse_master_line(ln) for ln in lines) if m is not None]


def fill_sweep(ctx: Ctx, targets: list[MasterRow]) -> dict[str, Any]:
    """단건 현재가를 FILL_RPS 로 연달아 부른다. 한도초과·행사가 일치·필드 채움을 센다."""
    gap = 1.0 / FILL_RPS
    ok = limited = other = strike_match = 0
    filled = dict.fromkeys(FILL_FIELDS, 0)
    sample: dict[str, Any] = {}
    t0 = time.monotonic()
    for m in targets:
        params = {"FID_COND_MRKT_DIV_CODE": "O", "FID_INPUT_ISCD": m.code}
        r = ctx.call(P_PRICE, TR_PRICE, params, min_gap=gap)
        if r.rate_limited:
            limited += 1
            continue
        if not r.ok:
            other += 1
            continue
        ok += 1
        o = rows(r.body, "output1")
        row = o[0] if o else {}
        try:
            if abs(float(row.get("acpr", "nan")) - (m.strike or 0.0)) < 1e-6:
                strike_match += 1
        except ValueError:
            pass
        for k in FILL_FIELDS:
            if str(row.get(k, "")).strip() not in ("", "0", "0.00", "0.0000"):
                filled[k] += 1
        if not sample:
            sample = {"code": m.code, "strike": m.strike, **{k: row.get(k) for k in FILL_FIELDS}}
    wall = time.monotonic() - t0
    return {
        "sent": len(targets),
        "ok": ok,
        "rate_limited": limited,
        "other_error": other,
        "strike_match": strike_match,
        "filled": filled,
        "wall_s": round(wall, 2),
        "achieved_rps": round(len(targets) / wall, 2) if wall > 0 else None,
        "strikes": [targets[0].strike, targets[-1].strike] if targets else [],
        "sample": sample,
    }


def run(ctx: Ctx) -> None:
    ex = expiries(ctx)
    monthly = (ex.get("(blank)") or [""])[0]
    weekly = (ex.get("WKM") or ex.get("WKI") or [""])[0]
    wcls = "WKM" if ex.get("WKM") else "WKI"

    # 1) 코드 규칙
    pairs: dict[str, Any] = {}
    board_codes: dict[str, list[str]] = {}
    board_strikes: dict[str, int] = {}
    known: list[str] = []
    weekly_atm: list[str] = []
    for cls, mtrt in (("", monthly), (wcls, weekly)):
        if not mtrt:
            continue
        label = f"{cls or '월물'}:{mtrt}"
        r = ctx.call(P_CALLPUT, TR_CALLPUT, callput_params(mtrt, cls))
        o1, o2 = rows(r.body, "output1"), rows(r.body, "output2")
        c, p = code_pairs(o1), code_pairs(o2)
        pairs[label] = {"calls": c[:3] + c[-3:], "puts": p[:3] + p[-3:]}
        board_codes[label] = [code for _, code in c + p if code]
        board_strikes[label] = len({k for k, _ in c})
        if cls:
            weekly_atm += [
                str(x.get("optn_shrn_iscd", "")).strip()
                for x in o1 + o2
                if str(x.get("atm_cls_name", "")).strip().upper() == "ATM"
            ]
        else:
            known += [code for _, code in c[-2:] + p[-2:] if code]
    ctx.findings["code_pairs"] = pairs
    ctx.note(f"코드 짝 표본: {pairs}")

    # 2) 마스터
    master: list[MasterRow] = []
    try:
        master = master_options(ctx)
        ctx.note(f"마스터 줄 종류 {ctx.findings['master']['kinds']} · 파싱 {len(master)}줄")
    except Exception as e:  # 마스터 실패가 나머지를 막지 않게
        ctx.note(f"마스터 실패: {type(e).__name__}: {e}")

    # 전광판 코드 → 마스터 시리즈. 시리즈 전체 행사가 수와 전광판이 준 수를 비교한다
    series: dict[str, dict[str, list[MasterRow]]] = {}
    coverage: dict[str, Any] = {}
    for label, codes in board_codes.items():
        series[label] = series_rows(master, codes)
        coverage[label] = {
            s: {"master_strikes": len(rs), "min": rs[0].strike, "max": rs[-1].strike}
            for s, rs in series[label].items()
            if rs
        } | {"board_strikes": board_strikes[label]}
    ctx.findings["coverage"] = coverage
    ctx.note(f"전광판 vs 마스터 행사가 수: {coverage}")

    # 3) 옵션 단건 현재가
    single: dict[str, Any] = {}
    for code in known[:2]:
        r = ctx.call(P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": "O", "FID_INPUT_ISCD": code})
        single[code] = brief(r, ("output1", "output2", "output3"))
    ctx.findings["single_price"] = single
    ctx.note(
        "옵션 단건 현재가: "
        + ", ".join(f"{k} rt_cd={v['rt_cd']} {v.get('msg1')}" for k, v in single.items())
    )

    # 4) 멀티 시세 — 시장 구분 코드를 몇 가지로 시도
    multi: dict[str, Any] = {}
    for mk in ("O", "J", "F"):
        params: dict[str, str] = {}
        for i, code in enumerate(known[:3], start=1):
            params[f"FID_COND_MRKT_DIV_CODE_{i}"] = mk
            params[f"FID_INPUT_ISCD_{i}"] = code
        if not params:
            break
        r = ctx.call(P_MULTI, TR_MULTI, params, min_gap=0.6)
        multi[mk] = brief(r, ("output",))
    ctx.findings["multi_price"] = multi
    ctx.note(
        "멀티 시세: "
        + ", ".join(
            f"{k} rt_cd={v['rt_cd']} 행 {v.get('output', {}).get('rows')}" for k, v in multi.items()
        )
    )

    # 5) 월물 ATM±N 을 마스터 코드로 연달아 조회
    fut = futures_codes(ctx)
    head = ctx.findings.get("futures_board", {}).get("output", {}).get("head", [])
    f_px = next(
        (float(h["futs_prpr"]) for h in head if h.get("futs_shrn_iscd") == (fut[0] if fut else "")),
        None,
    )
    ctx.findings["atm_ref"] = {"futures": fut[:1], "price": f_px}
    month_label = f"월물:{monthly}"
    targets: list[MasterRow] = []
    if f_px is not None:
        for rs in series.get(month_label, {}).values():
            targets += atm_window(rs, f_px, FILL_ATM_RANGE)
    if targets:
        # 이미 1초 넘게 쉬었다고 보장한 뒤 연속 부하를 건다
        time.sleep(1.1)
        sweep = fill_sweep(ctx, targets)
        ctx.findings["fill_sweep"] = sweep
        ctx.note(
            f"월물 ATM±{FILL_ATM_RANGE} 단건 {sweep['sent']}건 @{FILL_RPS}/s: 성공 {sweep['ok']} "
            f"한도초과 {sweep['rate_limited']} 기타오류 {sweep['other_error']} "
            f"행사가일치 {sweep['strike_match']} 채움 {sweep['filled']} "
            f"실제 {sweep['achieved_rps']}/s 범위 {sweep['strikes']}"
        )
    else:
        found = list(series.get(month_label, {}))
        ctx.note(f"월물 ATM 보강 조회 못 함: 선물가 {f_px}, 시리즈 {found}")

    # 6) 위클리 코드 단건 현재가
    wk: dict[str, Any] = {}
    for code in weekly_atm[:2]:
        r = ctx.call(P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": "O", "FID_INPUT_ISCD": code})
        o = rows(r.body, "output1")
        wk[code] = {
            "rt_cd": r.rt_cd,
            "msg1": r.body.get("msg1"),
            **{k: (o[0].get(k) if o else None) for k in ("acpr", *FILL_FIELDS)},
        }
    ctx.findings["weekly_single"] = wk
    ctx.note(f"위클리 ATM 단건 현재가: {wk}")
