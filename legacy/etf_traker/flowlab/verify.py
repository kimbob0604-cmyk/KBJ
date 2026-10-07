"""검증 — 코드를 고칠 때마다 다시 통과시킬 것.

1) 수급 금액 : flows.json 의 5일 기관·외인 억원을 원천 표에서 독립 재계산해 대조
2) 신고가 판정 : 단순 루프로 독립 재현 → newhigh.json 의 달성 종목이 루프 결과에
                 모두 들어가는지 (누락 0)
3) 후행·초과수익 : eventstudy 이벤트 한 건을 인덱싱만으로 다시 계산해 대조
4) as_of 절단 : 과거 날짜로 돌릴 때 창에 미래 거래일이 섞이지 않는지

`--ref` 로 수기 기준값을 주면 그 값과도 대조한다.
  예) --ref 999990:inst5=12.3,frgn5=45.6
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config as C, prices
from .naver import _pages_needed

TOL_EOK = 0.15      # 억원 반올림 오차 허용
TOL_PCT = 0.02      # %p 허용


class Result:
    def __init__(self):
        self.items: list[tuple[str, bool, str]] = []

    def add(self, name, ok, note="", warn=False):
        self.items.append((name, bool(ok), note, warn))
        return ok

    @property
    def ok(self):
        return all(ok for _, ok, _, warn in self.items if not warn)

    def render(self) -> str:
        out = []
        for n, ok, d, warn in self.items:
            tag = "WARN" if warn else ("PASS" if ok else "FAIL")
            out.append(f"  [{tag}] {n}" + (f" — {d}" if d else ""))
        return "\n".join(out)


def _flows(date=None) -> tuple[dict, str]:
    sd = C.state_dir(date)
    p = sd / "flows.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} 없음 — `python3 -m flowlab flows` 를 먼저 돌려라")
    return json.loads(p.read_text(encoding="utf-8")), str(sd)


def _five_day(fl: pd.DataFrame, as_of: str) -> tuple[float, float, int]:
    w = fl[fl["date"] <= as_of].sort_values("date", ascending=False).head(5)
    inst = float((w["inst_net"] * w["close"]).sum()) / 1e8
    frgn = float((w["frgn_net"] * w["close"]).sum()) / 1e8
    return inst, frgn, len(w)


def check_flow_amounts(res: Result, date=None, source=C.SOURCE_NAVER,
                       ref: dict | None = None, sample: int = 5, log=print):
    """flows.json 값을 원천 표에서 다시 합산해 대조한다.

    ref 는 {(code, as_of|None): {'inst5':…, 'frgn5':…}}. 기준일이 없으면 flows.json 의
    as_of 를 쓴다. 그 종목이 그날 표에 없어도 원천 표에서 직접 계산해 수기값과 대조한다.
    """
    fj, _ = _flows(date)
    as_of = fj["as_of"]
    mod = prices.source_module(fj.get("source", source))
    pages = fj["params"]["frgn_pages"]
    rows = [r for r in fj["rows"] if r.get("flow_grade")]
    bad = []
    for r in rows[:sample]:
        fl = mod.investor_flows(r["code"], pages=pages)
        inst, frgn, _n = _five_day(fl, as_of)
        if abs(inst - r["inst_5d_eok"]) > TOL_EOK or abs(frgn - r["frgn_5d_eok"]) > TOL_EOK:
            bad.append(f"{r['name']} 재계산 {inst:.1f}/{frgn:.1f} vs "
                       f"기록 {r['inst_5d_eok']}/{r['frgn_5d_eok']}")
    n_checked = min(sample, len(rows))
    if not n_checked:
        # 아무것도 대조하지 않은 검사를 PASS 로 적으면, 수급이 통째로 비어 있어도
        # 초록으로 보인다. 빈 것은 빈 것이라고 적는다 (CLAUDE.md 2장 1번).
        res.add("수급 금액 재계산 — 대조 표본 0종목", False,
                f"flows.json 에 flow_grade 가 붙은 행이 없다 (전체 {len(fj['rows'])}행). "
                f"수급 수집이 비었는지 확인할 것", warn=True)
    else:
        res.add(f"수급 금액 재계산 ({n_checked}종목)", not bad, "; ".join(bad[:4]))

    for (code, d), want in (ref or {}).items():
        d = d or as_of
        need = max(pages, _pages_needed(d))
        fl = mod.investor_flows(code, pages=need)
        # 받은 표가 어디까지 닿는지. 값이 틀린 것과 그 날짜를 아예 못 받은 것은
        # 고칠 데가 다르다 — 범위를 안 적으면 다음 사람이 수기값부터 의심한다.
        span = ""
        try:
            ds = fl["date"].astype(str)
            span = f"{ds.min()}~{ds.max()} {len(ds)}행"
        except Exception:                                    # noqa: BLE001
            span = "범위 불명"
        inst, frgn, n = _five_day(fl, d)
        log(f"    {code} as_of {d} 5일 기관 {inst:.1f} / 외인 {frgn:.1f} (창 {n}일) · "
            f"수기 {want.get('inst5')} / {want.get('frgn5')}")
        errs = [f"{k} 수기 {want[k]} vs 산출 {got:.1f}"
                for k, got in (("inst5", inst), ("frgn5", frgn))
                if k in want and abs(want[k] - got) > TOL_EOK]
        if errs and not n:
            # 창이 0일인 이유는 둘 중 하나고, **고칠 데가 다르다.**
            #   표가 비었다   → 원천을 못 받았다. 수집 실패다
            #   표는 있는데   → 수기 기준일이 페이지 밖으로 밀려났다
            # 전에는 둘을 '밀려난 것' 하나로 적었는데, 실제로 온 것은 0행이었다.
            # 추정을 단정해 적으면 다음 사람이 엉뚱한 데를 판다.
            if len(fl) == 0:
                res.add(f"수급 금액 수기 대조 {code} — 원천 표 0행", False,
                        f"투자자별 표를 한 행도 못 받았다 (페이지 {need}p). 수기값 문제가 "
                        f"아니라 **수집 실패**다 — 이 상태면 수급 리포트도 빈다. "
                        f"소스 응답을 먼저 확인할 것", warn=True)
            else:
                res.add(f"수급 금액 수기 대조 {code} @ {d} — 대조 불가", False,
                        f"원천 표가 {d} 에 못 닿는다 (받은 구간 {span}, 페이지 {need}p). "
                        f"기준일이 밀려난 것으로 보인다 — 기준값을 최근 날짜로 갱신하거나 "
                        f"페이지 계산을 늘릴 것", warn=True)
            continue
        res.add(f"수급 금액 수기 대조 {code} @ {d}", not errs,
                "; ".join(errs) + (f" (받은 구간 {span})" if errs else ""))


PX_TOL_PCT = 0.05   # 엔진 종가·고가 vs 원천 일봉, 이 이상 다르면 '원천 상이'


def check_newhigh(res: Result, date=None, source=C.SOURCE_NAVER, log=print):
    """단순 루프로 신고가를 독립 재현. 엔진 산출 ⊆ 루프 여야 한다.

    엔진은 고가·종가 두 기준을 따로 저장한다(achieved 에는 종가 기준만 걸린 행도
    들어온다). 루프도 두 기준을 각각, **라벨이 가리키는 룩백 창**(newhigh.json 의
    thresholds.lookback — d20 이 있던 시절의 state 도 있다)으로 재현한다.

    기준별 라벨은 `high_basis` · `close_basis` 에서 읽는다. 행 맨 위의 `label` 은
    **기본 기준(default_basis)의 라벨**이라 그것을 고가 라벨로 쓰면 안 된다 —
    보드가 기본을 종가로 바꾼 뒤(2026-09-16) 종가로만 뚫은 종목이 전부
    '고가 기준 로직 불일치' 로 잡혔다. 실제로 18건이 그렇게 잡혔고 전부 오탐이었다.
    옛 state 에는 두 열이 없으므로 그때만 `label` 로 떨어진다.
    hist 는 사상 최고가 스칼라라 창으로 재현할 수 없어 'w52 를 넘었다'는 필요조건만 본다.

    루프가 못 잡은 건은 두 갈래로 나눈다.
      원천 상이  — 엔진이 저장한 당일 종가·고가가 원천 일봉과 다르다. 판정 로직이 아니라
                   수집 데이터의 문제라 WARN 으로 따로 센다 (2026-09-01 state 에서 실제로
                   14건 — 16:10 수집이 시간외 단일가를 종가로 담은 것이었다).
      로직 불일치 — 당일 가격은 같은데 판정이 갈린다. 이것만 FAIL 이다.

    **D-080 이후 종가의 '원천 상이' 는 정상이다.** 보드의 종가는 KRX 정규장
    종가이고 여기서 읽는 네이버 siseJson 은 NXT 애프터마켓·시간외 단일가까지
    담는다. 두 값은 정의상 다르다. 그래서 state 가 `close_confirmed: true` 면
    종가 쪽 차이는 WARN 이 아니라 사실 보고로 적고, 고가 쪽 차이만 WARN 으로
    남긴다 — 고가는 두 정의가 같아야 한다.
    """
    sd = C.state_dir(date)
    nh = json.loads((sd / "newhigh.json").read_text(encoding="utf-8"))
    as_of = nh["as_of"]
    lookback = dict((nh.get("thresholds") or {}).get("lookback") or
                    {"d60": C.D60_DAYS, "w52": C.W52_DAYS})
    achieved = {s["code"]: s for s in nh.get("achieved") or []}
    # 옛 state 에는 이 키가 없다. 없으면 '모른다' 이므로 옛 규칙(전부 WARN)을 쓴다.
    confirmed = bool(nh.get("close_confirmed"))
    checked, no_px, logic, px_diff, px_expected, diag = 0, 0, [], [], [], []
    for code, row in achieved.items():
        px = prices.load(code, source, need_through=as_of, refresh=False)
        px = px[px["date"] <= as_of].reset_index(drop=True)
        avail = row.get("usable_days")
        if avail and len(px) > avail:
            px = px.tail(int(avail)).reset_index(drop=True)
        if len(px) < min(lookback.values()) + 1 or str(px["date"].iloc[-1]) != as_of:
            no_px += 1
            continue
        checked += 1
        for basis, col, b in (
                ("high", "high", row.get("high_basis") or
                 ({} if row.get("close_basis") else row)),
                ("close", "close", row.get("close_basis") or {})):
            label = b.get("label")
            if not label:
                continue
            kind = "w52" if label == "hist" else label
            n = lookback.get(kind)
            if n is None or len(px) <= n:
                continue
            v = px[col].astype(float)
            src_today = float(v.iloc[-1])
            ref = float(v.iloc[-1 - n:-1].max())
            if src_today > ref:
                continue
            eng_today = row.get(col)
            diff_pct = ((eng_today / src_today - 1) * 100) if (eng_today and src_today) else None
            msg = (f"{code} {row.get('name')} {basis}/{label}: 엔진 {col} {eng_today:,.0f} · "
                   f"원천 {col} {src_today:,.0f} ({diff_pct:+.2f}%) · {kind} 창 최고 {ref:,.0f}")
            if diff_pct is not None and abs(diff_pct) > PX_TOL_PCT:
                (px_expected if (confirmed and basis == "close") else px_diff).append(
                    f"{code}/{basis}/{label}")
                diag.append(("정의 차이  " if (confirmed and basis == "close")
                             else "원천 상이  ") + msg)
            else:
                logic.append(f"{code}/{basis}/{label}")
                diag.append("로직 불일치 " + msg)
    for d in diag[:10]:
        log("    " + d)
    res.add(f"신고가 판정 독립 재현 (대조 {checked}종목, 일봉 부족 {no_px}종목, "
            f"룩백 {','.join(f'{k}={v}' for k, v in lookback.items())})",
            not logic, f"로직 불일치 {len(logic)}건: {', '.join(logic[:6])}" if logic else "누락 0")
    if px_expected:
        res.add(f"엔진 종가 ≠ 네이버 일봉 ({len(px_expected)}건, 달성 표 안)", True,
                f"{', '.join(px_expected[:6])} — 보드 종가는 KRX 정규장 종가이고 "
                f"네이버는 NXT·시간외를 포함한다. 정의가 달라서 나는 차이다 (D-080)")
    if px_diff:
        res.add(f"엔진 당일 종가·고가 ≠ 원천 일봉 ({len(px_diff)}건, 달성 표 안)", False,
                f"{', '.join(px_diff[:6])} — 엔진 수집 시점의 문제로 보임(시간외 단일가). "
                f"판정 로직 문제가 아니라 WARN", warn=True)
    _universe_close_check(res, sd, as_of, source, log, confirmed)


def _universe_close_check(res: Result, sd, as_of: str, source: str, log=print,
                          confirmed: bool = False):
    """유니버스 전체에서 엔진 종가와 네이버 종가가 다른 종목을 센다.

    confirmed 면(보드가 KRX 정규장 확정치를 받은 날) 이 차이는 결함이 아니라
    두 정의의 차이다 — 사실만 적고 WARN 을 올리지 않는다 (D-080).
    """
    p = sd / "universe.json"
    if not p.exists():
        return
    stocks = (json.loads(p.read_text(encoding="utf-8")).get("stocks") or [])
    n, diff, big = 0, [], []
    for s in stocks:
        code, eng = s.get("code"), s.get("close")
        if not code or not eng:
            continue
        px = prices.load(code, source, need_through=as_of, refresh=False)
        row = px[px["date"] == as_of]
        if row.empty:
            continue
        src = float(row["close"].iloc[0])
        if not src:
            continue
        n += 1
        d = (eng / src - 1) * 100
        if abs(d) > PX_TOL_PCT:
            diff.append(d)
            if abs(d) > 3:
                big.append(f"{s.get('name')} {d:+.1f}%")
    if not n:
        return
    med = float(np.median(np.abs(diff))) if diff else 0.0
    tail = (" — 보드는 KRX 정규장 종가, 네이버는 NXT·시간외 포함. 정의 차이다 (D-080)"
            if confirmed else "")
    res.add(f"유니버스 당일 종가 대조 ({n:,}종목)", True,
            f"엔진≠네이버 {len(diff):,}종목({len(diff) / n * 100:.1f}%), |차| 중앙 {med:.2f}%, "
            f"3% 초과 {len(big)}종목" + (f": {', '.join(big[:5])}" if big else "") + tail,
            warn=bool(diff) and not confirmed)


def check_forward(res: Result, sample: int = 3, source=C.SOURCE_NAVER,
                  ref_event: dict | None = None, log=print):
    """이벤트 스터디의 초과수익을 인덱싱만으로 다시 계산.

    ref_event 가 있으면 그 (종목, 날짜) 의 수기값과도 대조한다.
    이벤트 표에 없는 날짜(스크리닝 미달·신고가 아님)면 원천 일봉으로 직접 계산해 보인다.
    """
    p = C.OUT / "eventstudy_events.csv.gz"
    if not p.exists():
        res.add("후행·초과수익 재현", False, f"{p} 없음 — `python3 -m flowlab study` 먼저")
        return
    ev = pd.read_csv(p, dtype={"code": str, "date": str})
    ev = ev.dropna(subset=["exc_20d_pct"]).sort_values(["code", "date"])
    picks = ev.iloc[:: max(1, len(ev) // sample)].head(sample)
    idx_cache, bad = {}, []
    for (code, date), want in (ref_event or {}).items():
        row = ev[(ev["code"] == code) & (ev["date"] == date)]
        px = prices.load(code, source, refresh=False).reset_index(drop=True)
        i = px.index[px["date"] == date]
        if len(i) == 0:
            bad.append(f"{code} {date} 일봉 없음"); continue
        i = int(i[0])
        meta_m = row["market"].iloc[0] if len(row) else None
        markets = [meta_m] if meta_m else list(C.INDEX_SYMBOL)
        for h in C.STUDY_HORIZONS:
            key = f"exc{h}"
            if key not in want or i + h >= len(px):
                continue
            ret = (px["close"].iloc[i + h] / px["close"].iloc[i] - 1) * 100
            d0, d1 = px["date"].iloc[i], px["date"].iloc[i + h]
            outs = []
            for m in markets:
                ix = prices.index(m, source, refresh=False).set_index("date")["close"]
                if d0 in ix.index and d1 in ix.index:
                    iret = (ix.loc[d1] / ix.loc[d0] - 1) * 100
                    outs.append((m, ret, iret, ret - iret))
            if not outs:
                bad.append(f"{code} {date} 지수 일봉 없음"); continue
            m, r_, ir_, ex_ = outs[0]
            log(f"    {code} {date} +{h}d 종목 {r_:+.2f}% · {m} {ir_:+.2f}% · 초과 {ex_:+.2f}% "
                f"(수기 {want[key]:+.2f}%)" + ("" if len(row) else " · 이벤트 표에는 없는 날짜"))
            if abs(ex_ - want[key]) > 0.05:
                bad.append(f"{code} {date} +{h}d 수기 {want[key]:+.2f} vs 산출 {ex_:+.2f}")
            if len(row) and abs(float(row[f"exc_{h}d_pct"].iloc[0]) - ex_) > TOL_PCT:
                bad.append(f"{code} {date} 이벤트 표 {row[f'exc_{h}d_pct'].iloc[0]:+.2f} vs 재계산 {ex_:+.2f}")
    for _, r in picks.iterrows():
        px = prices.load(r["code"], source, refresh=False).reset_index(drop=True)
        i = px.index[px["date"] == r["date"]]
        if len(i) == 0:
            bad.append(f"{r['code']} {r['date']} 일봉 없음")
            continue
        i = int(i[0])
        m = r["market"]
        if m not in idx_cache:
            idx_cache[m] = prices.index(m, source, refresh=False).set_index("date")["close"]
        ix = idx_cache[m]
        for h in C.STUDY_HORIZONS:
            if i + h >= len(px):
                continue
            ret = (px["close"].iloc[i + h] / px["close"].iloc[i] - 1) * 100
            d0, d1 = px["date"].iloc[i], px["date"].iloc[i + h]
            if d0 not in ix.index or d1 not in ix.index:
                continue
            iret = (ix.loc[d1] / ix.loc[d0] - 1) * 100
            got, want = float(r[f"exc_{h}d_pct"]), float(ret - iret)
            if abs(got - want) > TOL_PCT:
                bad.append(f"{r['name']} {r['date']} +{h}d 수기 {want:.2f} vs 산출 {got:.2f}")
    res.add(f"후행·초과수익 재현 ({len(picks)}건)", not bad, "; ".join(bad[:4]))


def check_truncation(res: Result, date=None, source=C.SOURCE_NAVER, log=print):
    """as_of 절단 — 창에 미래 거래일이 섞이면 실패."""
    fj, _ = _flows(date)
    as_of = fj["as_of"]
    late = [r["code"] for r in fj["rows"]
            if r.get("flow_last_date") and r["flow_last_date"] > as_of]
    ok1 = res.add("as_of 절단 (flows.json 최종 수급일 ≤ as_of)", not late,
                  f"{len(late)}종목 초과: {', '.join(late[:5])}" if late else "")
    want = _pages_needed(as_of)
    ok2 = res.add("frgn 페이지 자동 확대", fj["params"]["frgn_pages"] >= want,
                  f"기록 {fj['params']['frgn_pages']}p vs 필요 {want}p")
    return ok1 and ok2


def parse_ref_event(items: list[str]) -> dict:
    """'999990:2026-06-15:exc5=-1.23' → {('999990','2026-06-15'): {'exc5': -1.23}}"""
    out = {}
    for it in items or []:
        parts = it.split(":")
        if len(parts) < 3:
            continue
        code, date, rest = parts[0].strip(), parts[1].strip(), ":".join(parts[2:])
        d = {}
        for kv in rest.split(","):
            k, _, v = kv.partition("=")
            if k and v:
                d[k.strip()] = float(v)
        out[(code, date)] = d
    return out


def show_flows(code: str, date=None, source=C.SOURCE_NAVER, n: int = 8, log=print):
    """한 종목의 최근 as_of 후보별 5일 기관·외인 억원. 수기 기준값의 기준일을 찾을 때 쓴다."""
    fj, _ = _flows(date)
    mod = prices.source_module(fj.get("source", source))
    fl = mod.investor_flows(code, pages=fj["params"]["frgn_pages"])
    fl = fl.sort_values("date", ascending=False).reset_index(drop=True)
    log(f"  {code} 최근 {n}개 기준일별 5일 합산 (억원, 순매매량×그날 종가)")
    for i in range(min(n, len(fl))):
        w = fl.iloc[i:i + 5]
        inst = float((w["inst_net"] * w["close"]).sum()) / 1e8
        frgn = float((w["frgn_net"] * w["close"]).sum()) / 1e8
        log(f"    as_of {w['date'].iloc[0]}  기관 {inst:8.1f}  외인 {frgn:8.1f}  (창 {len(w)}일)")


def parse_ref(items: list[str]) -> dict:
    """'999990@2026-09-04:inst5=12.3,frgn5=45.6' → {('999990','2026-09-04'): {...}}

    '@기준일' 을 빼면 flows.json 의 as_of 를 쓴다.
    """
    out = {}
    for it in items or []:
        head, _, rest = it.partition(":")
        code, _, d = head.partition("@")
        vals = {}
        for kv in rest.split(","):
            k, _, v = kv.partition("=")
            if k and v:
                vals[k.strip()] = float(v)
        out[(code.strip(), d.strip() or None)] = vals
    return out


def run(date=None, source=C.SOURCE_NAVER, ref=None, ref_event=None, log=print) -> bool:
    res = Result()
    for fn in (check_truncation, check_flow_amounts):
        try:
            fn(res, date=date, source=source, **({"ref": ref} if fn is check_flow_amounts else {}))
        except Exception as e:
            res.add(fn.__name__, False, f"{type(e).__name__}: {e}")
    for fn, kw in ((check_newhigh, {"date": date, "source": source}),
                   (check_forward, {"source": source, "ref_event": ref_event})):
        try:
            fn(res, **kw)
        except Exception as e:
            res.add(fn.__name__, False, f"{type(e).__name__}: {e}")
    log(res.render())
    log(f"  → {'모두 통과' if res.ok else '실패 있음'}")
    return res.ok
