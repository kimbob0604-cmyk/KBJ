"""
kis_api.py — 한국투자증권 REST API 클라이언트 (데이터 조회 전용)

매매 기능 없음. 메모리·파일 캐시로 호출 최소화.

KBJ P2(설계 §3.8 K1): 토큰을 여기서 발급하지 않는다. 접근토큰은 KBJ auth 서비스가 Redis 에 두고,
이 모듈은 `kbj.data.legacy_bridge`(이름 `requests` 로 끼웠다)로 읽기만 한다. 주소는 논리 URL
`kis:`·`kis-master:` — 브리지가 앱키·시크릿·토큰을 넣고 앱키당 레이트리미터(초당 4건, KBJ 정본)를
지킨다. 옛 토큰 파일 캐시(`cache/kis_token.json`)와 자체 리미터(초당 18회)는 지웠다.
장중 판정은 KBJ 캘린더(`kbj.core.calendar_compat`), 시각은 `kbj.core.time.now_kst`.
"""
from __future__ import annotations
import json
import time
import logging
from pathlib import Path
from datetime import datetime

from kbj.core.calendar_compat import is_kr_regular_hours
from kbj.core.time import now_kst
from kbj.data import legacy_bridge as requests

log = logging.getLogger(__name__)

KIS_BASE = "kis:"
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"


# ── 토큰 (KBJ auth 가 발급 — 여기서는 읽기만) ─────────────
def _get_token() -> str | None:
    return requests.access_token_or_none()


def _headers(tr_id: str) -> dict | None:
    """토큰이 없으면 None(호출자가 빈 결과로 끝낸다 — 기존 동작). 토큰·앱키는 브리지가 넣는다."""
    if not _get_token():
        return None
    return {
        "Content-Type": "application/json; charset=utf-8",
        "tr_id": tr_id,
        "x-kbj-priority": "P3",
    }


# ── 메모리 + 파일 캐시 ─────────────────────────────
_mem_cache: dict = {}


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"kis_{key}.json"


def _get_cache(key: str, ttl: int):
    e = _mem_cache.get(key)
    now = time.time()
    if e and now - e["ts"] < ttl:
        return e["data"]
    p = _cache_path(key)
    if p.exists():
        mt = p.stat().st_mtime
        if now - mt < ttl:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                _mem_cache[key] = {"data": d, "ts": mt}
                return d
            except Exception:
                pass
    return None


def _set_cache(key: str, data):
    _mem_cache[key] = {"data": data, "ts": time.time()}
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _cache_path(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _is_kr_market_hours() -> bool:
    return is_kr_regular_hours(now_kst())


# ── 분봉 (장중 60s, 장외 1h) ─────────────────────
def get_minute_chart(code: str, interval: int = 1) -> list:
    key = f"minute_{code}_{interval}"
    ttl = 60 if _is_kr_market_hours() else 3600
    c = _get_cache(key, ttl)
    if c is not None:
        return c
    h = _headers("FHKST03010200")
    if not h:
        return []
    p = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": _now_kst().strftime("%H%M%S"),
        "FID_PW_DATA_INCU_YN": "Y",
    }
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 분봉 %s: %s", code, d.get("msg1"))
            return []
        out = []
        for it in d.get("output2", []):
            try:
                o = int(it.get("stck_oprc") or 0)
                hi = int(it.get("stck_hgpr") or 0)
                lo = int(it.get("stck_lwpr") or 0)
                cl = int(it.get("stck_prpr") or 0)
                v = int(it.get("cntg_vol") or 0)
                if o <= 0 or cl <= 0:
                    continue
                out.append({
                    "time": it.get("stck_cntg_hour", ""),
                    "date": it.get("stck_bsop_date", ""),
                    "open": o, "high": hi, "low": lo, "close": cl,
                    "volume": v,
                })
            except Exception:
                continue
        # 한투는 최신순 → 오래된순으로 뒤집어 차트 친화 형태
        out.reverse()
        if out:
            _set_cache(key, out)
        return out
    except Exception as exc:
        log.warning("[KIS] 분봉 %s 호출실패: %s", code, exc)
        return []


# ── 호가 10단계 (장중 5s, 장외 1h) ────────────────
def get_orderbook(code: str) -> dict | None:
    key = f"orderbook_{code}"
    ttl = 5 if _is_kr_market_hours() else 3600
    c = _get_cache(key, ttl)
    if c is not None:
        return c
    h = _headers("FHKST01010200")
    if not h:
        return None
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 호가 %s: %s", code, d.get("msg1"))
            return None
        out1 = d.get("output1", {})
        asks, bids = [], []
        for i in range(1, 11):
            ap = int(out1.get(f"askp{i}") or 0)
            aq = int(out1.get(f"askp_rsqn{i}") or 0)
            if ap > 0:
                asks.append({"price": ap, "qty": aq})
            bp = int(out1.get(f"bidp{i}") or 0)
            bq = int(out1.get(f"bidp_rsqn{i}") or 0)
            if bp > 0:
                bids.append({"price": bp, "qty": bq})
        result = {
            "asks": asks, "bids": bids,
            "total_ask_qty": int(out1.get("total_askp_rsqn") or 0),
            "total_bid_qty": int(out1.get("total_bidp_rsqn") or 0),
            "timestamp": _now_kst().strftime("%H:%M:%S"),
        }
        if asks or bids:
            _set_cache(key, result)
        return result
    except Exception as exc:
        log.warning("[KIS] 호가 %s 호출실패: %s", code, exc)
        return None


# ── 투자자별 매매동향 (10분 캐시) ────────────────
def get_investor_trading(code: str) -> list:
    key = f"investor_{code}"
    c = _get_cache(key, 600)
    if c is not None:
        return c
    h = _headers("FHKST01010900")
    if not h:
        return []
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 투자자 %s: %s", code, d.get("msg1"))
            return []
        out = []
        for it in d.get("output", [])[:10]:
            try:
                out.append({
                    "date": it.get("stck_bsop_date", ""),
                    "foreign_net": int(it.get("frgn_ntby_qty") or 0),
                    "inst_net": int(it.get("orgn_ntby_qty") or 0),
                    "retail_net": int(it.get("prsn_ntby_qty") or 0),
                })
            except Exception:
                continue
        if out:
            _set_cache(key, out)
        return out
    except Exception as exc:
        log.warning("[KIS] 투자자 %s 호출실패: %s", code, exc)
        return []


# ── 현재가 상세 (장중 30s, 장외 1h) ──────────────
def get_price_detail(code: str) -> dict | None:
    key = f"price_{code}"
    ttl = 30 if _is_kr_market_hours() else 3600
    c = _get_cache(key, ttl)
    if c is not None:
        return c
    h = _headers("FHKST01010100")
    if not h:
        return None
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 현재가 %s: %s", code, d.get("msg1"))
            return None
        o = d.get("output", {})

        def _i(k):
            try: return int(o.get(k) or 0)
            except Exception: return 0
        def _f(k):
            try: return float(o.get(k) or 0)
            except Exception: return 0.0

        result = {
            "price": _i("stck_prpr"),
            "change": _i("prdy_vrss"),
            "change_pct": _f("prdy_ctrt"),
            "open": _i("stck_oprc"),
            "high": _i("stck_hgpr"),
            "low": _i("stck_lwpr"),
            "volume": _i("acml_vol"),
            "trade_amount": _i("acml_tr_pbmn"),
            "per": _f("per"),
            "pbr": _f("pbr"),
            "eps": _f("eps"),
            "market_cap": _i("hts_avls"),  # 단위: 억
            "high_52w": _i("stck_dryc_hgpr"),
            "low_52w": _i("stck_dryc_lwpr"),
            "high_52w_date": o.get("dryy_hgpr_date", ""),
            "low_52w_date": o.get("dryy_lwpr_date", ""),
        }
        _set_cache(key, result)
        return result
    except Exception as exc:
        log.warning("[KIS] 현재가 %s 호출실패: %s", code, exc)
        return None


# ── 코스피200 선물 (근월물·원월물) ─────────────────────
# 2026-09-23 러너 실측(ETF-Traker board/tools/probe_kis_futures.py)으로 확정한 것:
#   · 종목코드는 2026 표준코드 개편 이후 형식 'A01612' 다. 예전 '101W12' 식도,
#     'A' 를 뗀 '01612' 도 rt_cd=0 에 빈 output1 을 준다 — 실패가 아니라 빈 값이라
#     조용히 넘어가기 쉽다. 그래서 빈 output1 을 실패로 센다.
#   · 월물 순서는 지수선물 마스터(fo_idx_code_mts)의 7번째 칸(1=근월물).
#     날짜로 만기를 계산하지 않는다 — 만기일(둘째 목요일) 당일까지 근월물이
#     살아 있고, 휴장으로 만기가 밀리는 해도 있다. 마스터가 거래소 기준이다.
#   · 현재가 API(FHMIF10000000) output1 필드:
#     futs_oprc 시가 · futs_hgpr 고가 · futs_lwpr 저가 · futs_prpr 현재가(마감 뒤엔 종가)
#     futs_prdy_vrss/futs_prdy_ctrt 전일 대비 · hts_otst_stpl_qty 미결제약정
#     otst_stpl_qty_icdc 미결제약정 증감 · acml_vol 거래량 · futs_last_tr_date 최종거래일
FO_MASTER_URL = "kis-master:fo_idx_code_mts.mst.zip"  # 브리지가 KBJ 마스터 내려받기로
_fut_master_cache: dict = {"date": None, "contracts": None}


def _now_kst() -> datetime:
    # Render 는 UTC 로 돈다. 날짜 경계·as_of 는 KST 로 잡는다(KBJ 벽시계 한 곳).
    return now_kst()


def _kospi200_futures_contracts() -> list:
    """[(순번, 단축코드, 이름)] — 1=근월물. 마스터는 하루 한 번만 받는다."""
    import io, zipfile
    today = _now_kst().strftime("%Y%m%d")
    if _fut_master_cache["date"] == today and _fut_master_cache["contracts"]:
        return _fut_master_cache["contracts"]
    r = requests.get(FO_MASTER_URL, timeout=20)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    out = []
    for ln in text.splitlines():
        f = ln.split("|")
        # '1|A01612|KR4A016C0004|F 202612| |00000.00|1|2001|KOSPI200'
        if len(f) > 8 and f[0] == "1" and f[8].strip() == "KOSPI200":
            try:
                out.append((int(f[6]), f[1].strip(), f[3].strip()))
            except ValueError:
                continue
    out.sort()
    if out:
        _fut_master_cache.update(date=today, contracts=out)
    return out


def get_kospi200_futures(n: int = 2) -> dict:
    """코스피200 선물 근월물·원월물 시세·미결제약정.

    반환: {"contracts": [...], "error": str|None, "source", "as_of"}
    값을 못 받은 월물은 목록에 넣지 않고 error 에 사유를 적는다 — 0 으로 채우지 않는다.
    """
    result = {"contracts": [], "error": None,
              "source": "KIS FHMIF10000000",
              "as_of": _now_kst().strftime("%Y-%m-%d %H:%M")}
    c = _get_cache("k200_futures", 300)
    if c is not None:
        return c
    try:
        master = _kospi200_futures_contracts()
    except Exception as exc:
        result["error"] = f"월물 마스터 수신 실패: {type(exc).__name__}"
        return result
    if not master:
        result["error"] = "월물 마스터에 코스피200 선물이 없음"
        return result
    errors = []
    labels = {1: "근월물", 2: "원월물"}
    for order, code, name in master[:n]:
        h = _headers("FHMIF10000000")
        if not h:
            result["error"] = "KIS 토큰 없음 (KBJ auth 서비스·KBJ_REDIS_URL 확인)"
            return result
        try:
            r = requests.get(
                f"{KIS_BASE}/uapi/domestic-futureoption/v1/quotations/inquire-price",
                headers=h, params={"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code},
                timeout=10,
            )
            d = r.json()
        except Exception as exc:
            errors.append(f"{code} 요청 실패 {type(exc).__name__}")
            continue
        o = d.get("output1") or {}
        if d.get("rt_cd") != "0" or not o.get("futs_prpr"):
            errors.append(f"{code} 응답 없음 ({d.get('msg1', '')[:40]})")
            continue

        def _f(k):
            v = o.get(k)
            try:
                return float(v) if v not in (None, "") else None
            except ValueError:
                return None

        def _i(k):
            v = _f(k)
            return int(v) if v is not None else None

        result["contracts"].append({
            "label": labels.get(order, f"{order}번째"),
            "code": code,
            "name": o.get("hts_kor_isnm") or name,
            "open": _f("futs_oprc"), "high": _f("futs_hgpr"),
            "low": _f("futs_lwpr"), "close": _f("futs_prpr"),
            "change": _f("futs_prdy_vrss"), "change_pct": _f("futs_prdy_ctrt"),
            "oi": _i("hts_otst_stpl_qty"), "oi_change": _i("otst_stpl_qty_icdc"),
            "volume": _i("acml_vol"),
            "last_trade_date": o.get("futs_last_tr_date"),
        })
    if errors:
        result["error"] = " · ".join(errors)
    if result["contracts"] and not errors:
        _set_cache("k200_futures", result)
    return result
