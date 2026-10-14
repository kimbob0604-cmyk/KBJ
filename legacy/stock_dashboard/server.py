"""
server.py  —  테마 트리맵 Flask 서버

의존성: pip install flask
실행:   python server.py
접속:   http://localhost:8080

동작 방식
  · 서버 시작 시 data.json 이 없거나 오늘 날짜가 아니면 data_fetcher.py 를 백그라운드에서 자동 실행
  · /            → index.html 즉시 반환 (수집 완료 전에도 더미 데이터로 동작)
  · /data.json   → 수집 완료된 data.json 반환 (미완료 시 503)
  · /api/status  → 수집 상태 JSON  {"state": "running"|"idle"|"error", ...}
  · /api/refresh → 수동 재수집 트리거 (GET/POST)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import subprocess
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations as _comb
from pathlib import Path

# ── .env 파일 로더 (python-dotenv 미의존) ──────────────────────────────
# 로컬 개발 시 .env 파일이 있으면 os.environ 에 병합.
# Render/프로덕션은 서비스의 환경변수를 직접 사용하므로 .env 가 없어도 무해.
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)  # 기존 환경변수 덮어쓰지 않음
    except Exception as exc:
        print(f"[.env 로드 실패] {exc}")

_load_dotenv(Path(__file__).parent / ".env")

# Render 등 UTC 서버에서도 KST 기준으로 날짜/시간 계산
KST = timezone(timedelta(hours=9))

# KBJ P2(설계 §7.2): 벽시계는 kbj.core.time 한 곳, 휴장·장중 판정은 KBJ 정본 캘린더
# (XKRX + config/holidays_override.yaml — kbj.core.calendar_compat). 텔레그램 발송은 KBJ notifier
# 대기열(kbj.services.notifier.client.legacy_send — 설계 §5.9), DART 는 KBJ 브리지(`dart:`).
from kbj.core.calendar_compat import (  # noqa: E402
    is_kr_holiday as _kbj_is_kr_holiday,
    is_kr_regular_hours as _kbj_is_kr_regular_hours,
    next_trading_open as _kbj_next_trading_open,
)
from kbj.core.time import now_kst as _kbj_now_kst  # noqa: E402
from kbj.services.notifier.client import legacy_send  # noqa: E402


def now_kst() -> datetime:
    return _kbj_now_kst()


def _dart_key_configured() -> str | None:
    """DART 키가 설정됐는가(KBJ_DART_API_KEY). 키 값은 KBJ 브리지만 쓴다 — 여기 값은 표시일 뿐이고
    `crtfc_key` 로 넘겨도 브리지가 버리고 KBJ 키를 넣는다."""
    from kbj.config.settings import Settings
    return "KBJ_DART_API_KEY" if Settings().dart_api_key is not None else None

try:
    from flask import Flask, Response, jsonify, request, send_file
except ImportError:
    raise SystemExit(
        "Flask 설치 필요: pip install flask\n"
        "  또는: pip install -r requirements.txt"
    )

# APScheduler (선택 의존성 — pip install apscheduler)
try:
    from apscheduler.schedulers.background import BackgroundScheduler as _BgScheduler
    _SCHEDULER_OK = True
except ImportError:
    _BgScheduler  = None
    _SCHEDULER_OK = False

_scheduler = None

# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DATA_JSON = BASE_DIR / "data.json"

# ── SQLite 캐시 (JSON 폴백 유지) ──
USE_SQLITE = os.environ.get("USE_SQLITE", "1") == "1"
try:
    from db.database import (
        get_db as _get_db, dict_from_row as _db_row, init_db as _init_db,
        read_chart_db as _read_chart_db,
        read_financial_db as _read_financial_db,
        read_flow_db as _read_flow_db,
        read_yinfo_db as _read_yinfo_db,
    )
    _SQLITE_OK = True
except ImportError:
    _SQLITE_OK = False
    USE_SQLITE = False
FETCHER   = BASE_DIR / "data_fetcher.py"
# Render 등 호스팅 환경은 PORT 환경변수를 주입하며 0.0.0.0 바인딩이 필요.
# 로컬 실행 시에는 127.0.0.1:8080 기본값 유지.
PORT      = int(os.environ.get("PORT", 8080))
HOST      = os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)


class _PykrxNoiseFilter(logging.Filter):
    """pykrx 내부 로깅 버그 억제.

    pykrx 는 상장폐지/미상장 티커(예: 294400) 조회 실패 시
    comm/util.py 의 `logging.info(args, kwargs)` (튜플을 메시지로 전달) +
    내부 트레이스백을 root 로거로 쏟아낸다 → "not all arguments converted"
    Logging error + NoneType 트레이스백 노이즈. 우리 호출은 _pykrx_call /
    _pykrx_ticker_name 이 None 으로 안전 처리하므로 로그만 차단한다.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        p = record.pathname or ""
        return "pykrx" not in p

# root 로거 + 모든 핸들러에 필터 부착 (record 출처가 pykrx 면 차단)
_pf = _PykrxNoiseFilter()
logging.getLogger().addFilter(_pf)
for _h in logging.getLogger().handlers:
    _h.addFilter(_pf)

log = logging.getLogger("server")

app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")
# 한글이 \uXXXX 로 이스케이프되지 않도록 (jsonify 응답)
app.config["JSON_AS_ASCII"] = False
try:
    app.json.ensure_ascii = False      # Flask >= 2.2
except Exception:
    pass

# ── WebSocket (Flask-SocketIO) ──
try:
    from flask_socketio import SocketIO, emit, join_room, leave_room
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    _SOCKETIO_OK = True
except ImportError:
    socketio = None
    _SOCKETIO_OK = False


# ─────────────────────────────────────────────────────────────────────────────
# 수집 상태 (스레드 안전)
# ─────────────────────────────────────────────────────────────────────────────
_lock   = threading.Lock()
_status: dict = {
    "state":            "idle",   # "running" | "idle" | "error"
    "started_at":       None,
    "finished_at":      None,
    "error":            None,
    "interval_minutes": 5,
}

def _get() -> dict:
    with _lock:
        return dict(_status)

def _set(**kw):
    with _lock:
        _status.update(kw)


# ─────────────────────────────────────────────────────────────────────────────
# 장중 여부
# ─────────────────────────────────────────────────────────────────────────────
def is_market_hours() -> bool:
    """KST 기준 거래일 주식 정규장(09:00 ~ 15:30, 휴장·지연 개장 반영) 여부 — KBJ 캘린더."""
    return _kbj_is_kr_regular_hours(now_kst())


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 신선도 확인
# ─────────────────────────────────────────────────────────────────────────────
def data_is_fresh() -> bool:
    """data.json 의 updated_at 이 KST 오늘 날짜인지 확인"""
    if not DATA_JSON.exists():
        return False
    try:
        data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        today = now_kst().strftime("%Y-%m-%d")
        return str(data.get("updated_at", "")).startswith(today)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 수집 실행 (별도 스레드)
# ─────────────────────────────────────────────────────────────────────────────
def _run_fetcher(force_market: bool = False):
    """data_fetcher.py 를 subprocess 로 실행하고 상태를 갱신한다."""
    if _get()["state"] == "running":
        log.info("data_fetcher 이미 실행 중 — 중복 실행 방지")
        return

    _set(state="running", started_at=now_kst().isoformat(), error=None)
    cmd = [sys.executable, str(FETCHER)]
    if force_market:
        cmd.append("--force-market")
    log.info("▶  data_fetcher.py 시작%s", "  [장중 갱신]" if force_market else "")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise RuntimeError(err)

        _set(state="idle", finished_at=now_kst().isoformat(), error=None)
        log.info("✓  data_fetcher.py 완료")

    except Exception as exc:
        _set(state="error", finished_at=now_kst().isoformat(), error=str(exc)[:500])
        log.error("✗  data_fetcher.py 실패: %s", exc)


def trigger_fetch(background: bool = True, force_market: bool = False) -> threading.Thread:
    t = threading.Thread(target=_run_fetcher, kwargs={"force_market": force_market},
                         daemon=True, name="fetcher")
    t.start()
    if not background:
        t.join()
    return t


# ─────────────────────────────────────────────────────────────────────────────
# pykrx 타임아웃 래퍼 (KRX 서버 무한 대기 방지)
# ─────────────────────────────────────────────────────────────────────────────
_pykrx_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="pykrx")


def _pykrx_call(func, *args, timeout: int | None = None, **kwargs):
    """pykrx 함수를 타임아웃 감싸서 호출. 장외 시간에는 5초로 단축."""
    if timeout is None:
        timeout = 15 if is_market_hours() else 5
    try:
        future = _pykrx_pool.submit(func, *args, **kwargs)
        return future.result(timeout=timeout)
    except Exception as exc:
        log.debug("[pykrx] %s(%s) timeout/fail (%ds): %s",
                  getattr(func, "__name__", "?"), args[:2], timeout, exc)
        return None


def _pykrx_ticker_name(code: str) -> str | None:
    """pykrx 종목명 안전 조회. 비정상 반환(DataFrame/Series 등)·예외 시 None.

    pykrx 가 상장폐지/미상장 코드에 str 이 아닌 값을 돌려주는 케이스에서
    호출부의 `... or code` 진리값 평가가 'DataFrame is ambiguous' ValueError
    로 터지는 버그를 방지한다. 반드시 str 또는 None 만 반환.
    """
    try:
        from pykrx import stock as _s
    except ImportError:
        return None
    nm = _pykrx_call(_s.get_market_ticker_name, code, timeout=5)
    return nm if isinstance(nm, str) and nm.strip() else None


# ─────────────────────────────────────────────────────────────────────────────
# 차트 계산 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _get_trading_date() -> str:
    """오늘(또는 가장 최근) 거래일 YYYYMMDD.

    **data.json 만 믿지 않는다.** data.json 은 맥북 크론이 git push 로
    갱신하는데, 그게 멈추면 이 함수가 몇 주 전 날짜를 계속 돌려준다.
    이 함수를 읽는 곳이 server.py 안에만 15곳이 넘어서, 한 번 낡으면
    시황·스크리너·일봉·펀더멘털 캐시가 전부 과거를 본다.

    실제로 2026-09-29 에 20260915 를 돌려주고 있었다. 그래서
    /api/screener 가 cache/fundamental_20260915.json 을 찾다 못 찾고
    시가총액·PER·PBR 을 전 종목 null 로 내보냈다.

    그래서 **서버가 오늘 실제로 데이터를 받았다는 증거** 와 비교해
    더 최신인 쪽을 쓴다. 날짜를 과거로 되돌리지 않는다.
      1) data.json 의 actual_date
      2) 오늘자 naver_universe 캐시 파일 (가격 sync 가 성공해야만 생긴다)
      3) 둘 다 없으면 오늘 날짜
    """
    cands: list[str] = []

    if DATA_JSON.exists():
        try:
            d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
            ad = d.get("actual_date") or d.get("updated_at", "")[:10]
            if ad:
                cands.append(ad.replace("-", ""))
        except Exception:
            pass

    # 가격 sync 는 종목을 실제로 갱신했을 때만 cache/naver_universe_<날짜>.json
    # 을 쓴다. 그 파일이 있다는 건 그날 장이 돌았다는 1차 증거다.
    try:
        for p in (BASE_DIR / "cache").glob("naver_universe_*.json"):
            stem = p.stem.rsplit("_", 1)[-1]
            if len(stem) == 8 and stem.isdigit():
                cands.append(stem)
    except Exception:
        pass

    today = now_kst().strftime("%Y%m%d")
    # 미래 날짜는 버린다(파일명이 잘못 남아 있을 수 있다).
    cands = [c for c in cands if c <= today]

    # 주말이면 직전 금요일을 오늘로 친다. 공휴일까지는 못 가리지만,
    # 2주 전을 돌려주는 것보다는 훨씬 덜 틀린다.
    _n = now_kst()
    _recent = _n - timedelta(days=max(0, _n.weekday() - 4))
    recent_weekday = _recent.strftime("%Y%m%d")

    best = max(cands) if cands else recent_weekday

    # **낡은 증거는 증거가 아니다.** Render 는 영속 디스크가 없어 재시작 직후
    # cache/ 가 비고, 그러면 후보가 data.json 하나만 남는다. 그게 몇 주 전이면
    # 다시 과거를 보게 된다 — 이 함수가 처음 고장 난 방식이 정확히 그것이다.
    try:
        gap = (datetime.strptime(today, "%Y%m%d")
               - datetime.strptime(best, "%Y%m%d")).days
    except Exception:
        gap = 0
    if gap > 7:
        _note_collect_error(
            "trading_date",
            f"최신 증거가 {best} 로 {gap}일 낡았다 (data.json 갱신 중단 의심) "
            f"— {recent_weekday} 로 대체한다")
        return recent_weekday
    return best


# 수집 실패 링버퍼. Render 로그를 볼 수 없으니 밖에서 읽을 창구가 필요하다.
_COLLECT_ERRORS: deque = deque(maxlen=120)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|auth[_-]?key"
    r"|appkey|appsecret|bot\d+:[\w-]+)"
    r"\s*[=:]\s*[\"']?([^\s\"'&,}]{4,})")


def _mask_secrets(s: str) -> str:
    """로그·진단 응답에 키가 섞여 나가지 않게 가린다."""
    if not s:
        return s
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", s)
    # 텔레그램 봇 토큰(숫자:영문). URL 의 /bot<토큰>/ 형태로도 나오므로
    # 앞에 단어경계를 두지 않는다 — 't' 와 '1' 사이엔 경계가 없다.
    out = re.sub(r"\d{6,}:[A-Za-z0-9_-]{20,}", "***", out)
    out = re.sub(r"(?i)([?&](?:key|apikey|auth_key|serviceKey)=)[^&\s]+", r"\1***", out)
    return out


def _calc_rsi_macd(closes: list) -> dict:
    """RSI(14) + MACD(12,26,9) 순수 Python 계산. pandas 미사용."""
    n = len(closes)
    # RSI 14
    rsi = [0.0] * n
    if n >= 15:
        gains = [0.0] * n
        losses = [0.0] * n
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains[i] = diff
            else:
                losses[i] = -diff
        avg_gain = sum(gains[1:15]) / 14
        avg_loss = sum(losses[1:15]) / 14
        for i in range(14, n):
            if i > 14:
                avg_gain = (avg_gain * 13 + gains[i]) / 14
                avg_loss = (avg_loss * 13 + losses[i]) / 14
            if avg_loss == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi[i] = round(100 - 100 / (1 + rs), 2)

    # MACD (12, 26, 9) — EMA 계산
    def _ema(data: list, span: int) -> list:
        out = [0.0] * len(data)
        if not data:
            return out
        k = 2 / (span + 1)
        out[0] = data[0]
        for i in range(1, len(data)):
            out[i] = data[i] * k + out[i - 1] * (1 - k)
        return out

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [round(ema12[i] - ema26[i], 3) for i in range(n)]
    macd_signal = _ema(macd_line, 9)
    macd_signal = [round(v, 3) for v in macd_signal]
    macd_hist = [round(macd_line[i] - macd_signal[i], 3) for i in range(n)]

    # 다이버전스 감지 (최근 40봉)
    divergences = _detect_divergences(closes, rsi, macd_line)

    return {
        "rsi":         rsi,
        "macd":        macd_line,
        "macd_signal": macd_signal,
        "macd_hist":   macd_hist,
        "divergences": divergences,
    }


def _detect_divergences(closes: list, rsi: list, macd: list) -> list:
    """
    RSI/MACD 기반 다이버전스 감지.
    베어리시: 가격 신고가 but 지표 저하
    불리시: 가격 신저가 but 지표 상승
    최근 40봉 내 2개 피크/밸리 비교.
    """
    n = len(closes)
    if n < 40:
        return []
    lookback = min(40, n)
    window = slice(n - lookback, n)
    cs = closes[window]
    rs = rsi[window]
    mc = macd[window]
    divs: list[dict] = []

    def _find_peaks(arr: list, is_max: bool = True) -> list[int]:
        """단순 피크/밸리 인덱스 (3봉 기준 로컬 극값)."""
        peaks = []
        for i in range(2, len(arr) - 2):
            if arr[i] == 0:
                continue
            if is_max and arr[i] >= arr[i-1] and arr[i] >= arr[i-2] and arr[i] >= arr[i+1] and arr[i] >= arr[i+2]:
                peaks.append(i)
            elif not is_max and arr[i] <= arr[i-1] and arr[i] <= arr[i-2] and arr[i] <= arr[i+1] and arr[i] <= arr[i+2]:
                peaks.append(i)
        return peaks

    # 가격 고점들
    price_highs = _find_peaks(cs, True)
    price_lows  = _find_peaks(cs, False)

    # 베어리시 다이버전스: 가격 신고가 but RSI/MACD 저하
    if len(price_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        if cs[p2] > cs[p1]:
            for label, ind in [("RSI", rs), ("MACD", mc)]:
                if ind[p2] < ind[p1] and ind[p2] != 0 and ind[p1] != 0:
                    divs.append({
                        "type": "bearish",
                        "indicator": label,
                        "idx1": p1 + (n - lookback),
                        "idx2": p2 + (n - lookback),
                    })

    # 불리시 다이버전스: 가격 신저가 but RSI/MACD 상승
    if len(price_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        if cs[p2] < cs[p1]:
            for label, ind in [("RSI", rs), ("MACD", mc)]:
                if ind[p2] > ind[p1] and ind[p2] != 0 and ind[p1] != 0:
                    divs.append({
                        "type": "bullish",
                        "indicator": label,
                        "idx1": p1 + (n - lookback),
                        "idx2": p2 + (n - lookback),
                    })

    return divs


def _calc_adx(highs: list, lows: list, closes: list, period: int = 14) -> dict | None:
    """ADX(14) 순수 Python 계산. 추세 강도 지표."""
    n = len(closes)
    if n < period * 2 + 1:
        return None
    adx_arr = [0.0] * n
    plus_di_arr = [0.0] * n
    minus_di_arr = [0.0] * n

    # True Range + DM
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        plus_dm[i]  = h_diff if h_diff > l_diff and h_diff > 0 else 0
        minus_dm[i] = l_diff if l_diff > h_diff and l_diff > 0 else 0
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i]  - closes[i - 1]))

    # Wilder smoothing (SMA 초기 → EMA)
    def _smooth(arr, p):
        out = [0.0] * len(arr)
        out[p] = sum(arr[1:p + 1]) / p
        for i in range(p + 1, len(arr)):
            out[i] = (out[i - 1] * (p - 1) + arr[i]) / p
        return out

    sm_tr = _smooth(tr, period)
    sm_pdm = _smooth(plus_dm, period)
    sm_mdm = _smooth(minus_dm, period)

    dx = [0.0] * n
    for i in range(period, n):
        if sm_tr[i] == 0:
            continue
        pdi = sm_pdm[i] / sm_tr[i] * 100
        mdi = sm_mdm[i] / sm_tr[i] * 100
        plus_di_arr[i] = round(pdi, 2)
        minus_di_arr[i] = round(mdi, 2)
        denom = pdi + mdi
        dx[i] = abs(pdi - mdi) / denom * 100 if denom > 0 else 0

    adx_smoothed = _smooth(dx, period)
    for i in range(period * 2, n):
        adx_arr[i] = round(adx_smoothed[i], 2)

    return {
        "adx":       adx_arr,
        "plus_di":   plus_di_arr,
        "minus_di":  minus_di_arr,
    }


def _calc_bollinger(closes: list, period: int = 20, num_std: float = 2) -> dict:
    sma, upper, lower = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            sma.append(None); upper.append(None); lower.append(None)
        else:
            w = closes[i - period + 1 : i + 1]
            m = sum(w) / period
            s = math.sqrt(sum((x - m) ** 2 for x in w) / period)
            sma.append(round(m))
            upper.append(round(m + num_std * s))
            lower.append(round(m - num_std * s))
    return {"sma_20": sma, "upper": upper, "lower": lower}


def _calc_fibonacci(highs: list, lows: list) -> dict:
    h, lo = max(highs), min(lows)
    d = h - lo
    return {
        "0.0":   round(float(h)),
        "23.6":  round(float(h - d * 0.236)),
        "38.2":  round(float(h - d * 0.382)),
        "50.0":  round(float(h - d * 0.500)),
        "61.8":  round(float(h - d * 0.618)),
        "78.6":  round(float(h - d * 0.786)),
        "100.0": round(float(lo)),
    }


def _find_best_trendline(pivots: list, closes: list, direction: str):
    if len(pivots) < 2:
        return None
    best, best_score = None, -1
    for (i1, p1), (i2, p2) in _comb(pivots[-8:], 2):
        if i2 <= i1:
            continue
        slope = (p2 - p1) / (i2 - i1)
        intercept = p1 - slope * i1
        violations = 0
        for idx, c in enumerate(closes):
            proj = slope * idx + intercept
            tol  = proj * 0.005
            if direction == "support"    and c < proj - tol:
                violations += 1
            elif direction == "resistance" and c > proj + tol:
                violations += 1
        score = len(closes) - violations
        if score > best_score:
            best_score = score
            best = {"i1": i1, "p1": p1, "i2": i2, "p2": p2,
                    "slope": slope, "intercept": intercept}
    return best


def _calc_trendlines(highs: list, lows: list, closes: list, window: int = 10) -> dict:
    pivot_highs, pivot_lows = [], []
    for i in range(window, len(closes) - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            pivot_highs.append((i, highs[i]))
        if lows[i]  == min(lows[i  - window : i + window + 1]):
            pivot_lows.append((i, lows[i]))
    support    = _find_best_trendline(pivot_lows,  closes, "support")
    resistance = _find_best_trendline(pivot_highs, closes, "resistance")
    return {
        "support":     support,
        "resistance":  resistance,
        "pivot_lows":  [[i, p] for i, p in pivot_lows],
        "pivot_highs": [[i, p] for i, p in pivot_highs],
    }


def _generate_analysis(closes, volumes, bollinger, fibonacci, trendlines) -> dict:
    comments = []
    current  = closes[-1]

    # ── 볼린저 밴드 ──
    sma_val = bollinger["sma_20"][-1]
    if sma_val is not None:
        bb_upper = bollinger["upper"][-1]
        bb_lower = bollinger["lower"][-1]
        bb_width = (bb_upper - bb_lower) / sma_val * 100
        if current >= bb_upper:
            comments.append({"type": "bollinger", "signal": "과매수",
                "detail": f"현재가({current:,})가 볼린저 상단({bb_upper:,})을 돌파. 단기 과열 구간으로 차익실현 매물 출회 가능성."})
        elif current <= bb_lower:
            comments.append({"type": "bollinger", "signal": "과매도",
                "detail": f"현재가({current:,})가 볼린저 하단({bb_lower:,}) 이하. 기술적 반등 가능 구간이나 추세 하락 시 추가 하락 주의."})
        elif current > sma_val:
            comments.append({"type": "bollinger", "signal": "중립 상향",
                "detail": f"현재가({current:,})가 20일 이평선({sma_val:,}) 위에서 거래 중. 단기 상승 추세 유지."})
        else:
            comments.append({"type": "bollinger", "signal": "중립 하향",
                "detail": f"현재가({current:,})가 20일 이평선({sma_val:,}) 아래. 단기 약세 흐름."})
        if bb_width < 5:
            comments.append({"type": "bollinger", "signal": "스퀴즈",
                "detail": f"볼린저 밴드폭({bb_width:.1f}%)이 극도로 수축. 큰 변동성 확대 임박 가능성. 방향은 돌파 방향에 따라 결정."})
        elif bb_width > 20:
            comments.append({"type": "bollinger", "signal": "밴드 확장",
                "detail": f"볼린저 밴드폭({bb_width:.1f}%)이 크게 확장. 강한 추세 진행 중이나 추세 피로 누적 가능."})

    # ── 피보나치 ──
    fib_h   = fibonacci["0.0"]
    fib_236 = fibonacci["23.6"]
    fib_382 = fibonacci["38.2"]
    fib_500 = fibonacci["50.0"]
    fib_618 = fibonacci["61.8"]
    if current >= fib_h:
        comments.append({"type": "fibonacci", "signal": "신고가 근접",
            "detail": f"현재가({current:,})가 120일 고점({fib_h:,}) 이상. 신고가 돌파 시 추가 상승 모멘텀 기대."})
    elif current >= fib_236:
        comments.append({"type": "fibonacci", "signal": "약조정 구간",
            "detail": f"현재가({current:,})가 23.6% 되돌림({fib_236:,}) 위. 상승 추세 내 얕은 조정 수준으로 강세 유지."})
    elif current >= fib_382:
        comments.append({"type": "fibonacci", "signal": "일반 조정",
            "detail": f"현재가({current:,})가 38.2% 되돌림({fib_382:,}) 부근. 건전한 조정 구간. 이 레벨에서 지지 확인 시 재상승 가능."})
    elif current >= fib_500:
        comments.append({"type": "fibonacci", "signal": "중간 조정",
            "detail": f"현재가({current:,})가 50% 되돌림({fib_500:,}) 부근. 추세 전환 가능성이 높아지는 구간. 거래량 동반 여부 확인 필요."})
    elif current >= fib_618:
        comments.append({"type": "fibonacci", "signal": "깊은 조정",
            "detail": f"현재가({current:,})가 61.8% 되돌림({fib_618:,}) 부근. 황금 비율 지지선. 이탈 시 추세 전환으로 판단."})
    else:
        comments.append({"type": "fibonacci", "signal": "추세 전환",
            "detail": f"현재가({current:,})가 61.8% 되돌림({fib_618:,}) 하회. 기존 상승분 대부분 반납. 하락 추세 전환 가능성 높음."})

    # ── 추세선 ──
    if trendlines.get("support"):
        tl   = trendlines["support"]
        proj = round(tl["slope"] * (len(closes) - 1) + tl["intercept"])
        if current >= proj:
            comments.append({"type": "trendline", "signal": "지지선 위",
                "detail": f"현재가({current:,})가 상승 지지선({proj:,}) 위에 위치. 지지 구조 유효."})
        else:
            comments.append({"type": "trendline", "signal": "지지선 이탈",
                "detail": f"현재가({current:,})가 지지선({proj:,}) 하회. 추가 하락 압력 주의."})
    if trendlines.get("resistance"):
        tl   = trendlines["resistance"]
        proj = round(tl["slope"] * (len(closes) - 1) + tl["intercept"])
        if current < proj:
            comments.append({"type": "trendline", "signal": "저항선 하",
                "detail": f"현재가({current:,})가 저항선({proj:,}) 아래. 저항 돌파 시 추가 상승 가능."})
        else:
            comments.append({"type": "trendline", "signal": "저항선 돌파",
                "detail": f"현재가({current:,})가 저항선({proj:,})을 돌파. 강한 상승 신호."})

    # ── 거래량 ──
    if len(volumes) >= 20:
        avg   = sum(volumes[-20:]) / 20
        ratio = volumes[-1] / avg if avg > 0 else 0
        if ratio > 2.0:
            comments.append({"type": "volume", "signal": "거래량 급증",
                "detail": f"당일 거래량이 20일 평균 대비 {ratio:.1f}배. 세력 매집 또는 이벤트성 매매 가능성."})
        elif ratio < 0.3:
            comments.append({"type": "volume", "signal": "거래량 급감",
                "detail": f"당일 거래량이 20일 평균 대비 {ratio:.1f}배로 극도로 위축. 관망세 또는 바닥 다지기 구간."})

    # ── 종합 ──
    bull = {"과매도", "약조정 구간", "신고가 근접", "거래량 급증", "중립 상향", "지지선 위", "저항선 돌파"}
    bear = {"과매수", "깊은 조정", "추세 전환", "중립 하향", "지지선 이탈", "밴드 확장"}
    n_bull = sum(1 for c in comments if c["signal"] in bull)
    n_bear = sum(1 for c in comments if c["signal"] in bear)
    if n_bull > n_bear:
        summary = "종합: 기술적 지표가 단기 긍정적 신호를 시사. 다만 개별 지표 확인 필요."
    elif n_bear > n_bull:
        summary = "종합: 기술적 지표가 단기 부정적 신호 우세. 리스크 관리 필요."
    else:
        summary = "종합: 기술적 지표 혼조. 방향성 확인 후 대응 권장."

    return {"comments": comments, "summary": summary}


# ─────────────────────────────────────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file(BASE_DIR / "index.html")


# ============================================================
# Step 4-5-2-C: SPA URL path 라우팅 — index.html 폴백
# 프론트의 parseUrlPath() 가 pathname 을 보고 해당 페이지 렌더.
# ============================================================
@app.route("/verification")
@app.route("/verification/<code>")
# KBJ P8 삭제: /journal 화면 경로 — kbj 페이지 12
@app.route("/ops/<page>")
def spa_route(**kwargs):  # noqa: ARG001  (kwargs 는 프론트가 읽음)
    return send_file(BASE_DIR / "index.html")


def _overlay_live_prices_on_data(data: dict) -> dict:
    """data.json 의 themes.stocks[].change_pct 와 weighted_avg_pct 를
    naver_universe 실시간 값으로 덮어씀. 장중 실시간 반영용.

    Render 환경(naver_universe 부재) 폴백: themes 안 종목 코드를 모아
    polling.finance.naver.com에 일괄 조회 (3분 캐시)."""
    uni = _load_naver_universe()
    stocks_live = (uni or {}).get("stocks") or {}

    # naver_universe 부재 시 가벼운 실시간 폴링 폴백 (Render용)
    if not stocks_live:
        kr_codes: list = []
        for theme in (data.get("themes") or []):
            for s in (theme.get("stocks") or []):
                code = s.get("code")
                if code and re.fullmatch(r"\d{6}", code):
                    kr_codes.append(code)
        if not kr_codes:
            return data
        # dedupe + 일괄 조회
        kr_codes = list(dict.fromkeys(kr_codes))
        stocks_live = _fetch_naver_live_prices(kr_codes)
        if not stocks_live:
            return data

    updated_stocks = 0
    for theme in (data.get("themes") or []):
        total_vol = 0.0
        weighted_chg = 0.0
        active = 0
        for s in (theme.get("stocks") or []):
            code = s.get("code")
            live = stocks_live.get(code)
            if live and live.get("change_pct") is not None and live.get("close"):
                s["change_pct"] = round(float(live["change_pct"]), 2)
                s["volume_mn"] = live.get("volume_mn") or s.get("volume_mn", 0)
                s["close"] = live.get("close")
                updated_stocks += 1
            # 가중평균 계산용
            chg = s.get("change_pct") or 0
            vol = s.get("volume_mn") or 0
            if vol > 0:
                total_vol += vol
                weighted_chg += chg * vol
            if abs(chg) > 0.01:
                active += 1
        if total_vol > 0:
            theme["weighted_avg_pct"] = round(weighted_chg / total_vol, 2)
            theme["active_count"] = active

    # KOSPI/KOSDAQ 지수는 macro_data.json 또는 naver_universe 에서 집계된 현 값 유지
    # → naver_universe 에는 없으니 기존 값 그대로 두되, updated_at 갱신
    data["updated_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    data["_overlay"] = {"live_stocks_matched": updated_stocks,
                        "overlay_at": data["updated_at"]}
    return data


# ─── 실시간 데이터 보강 (Render 환경 — pykrx/yfinance 미사용, urllib만) ───
# Render는 자동 fetcher 비활성화 → /data.json 응답 시 가벼운 polling으로 신선도 보강.
_KR_INDEX_CACHE = {"data": None, "ts": 0}
_KR_INDEX_TTL = 300  # 5분

_LIVE_PRICE_CACHE = {"data": {}, "ts": 0}
_LIVE_PRICE_TTL = 180  # 3분 (장중 종목 가격)


def _fetch_naver_live_prices(codes: list) -> dict:
    """polling.finance.naver.com 일괄 조회 → {code: {close, change_pct, volume_mn}}.
    종목 100개 단위 batch + 3분 메모리 캐시 (Render 메모리 안전)."""
    if not codes:
        return {}
    now = time.time()
    cache = _LIVE_PRICE_CACHE
    if cache["data"] and (now - cache["ts"]) < _LIVE_PRICE_TTL:
        # 캐시에서 요청 종목만 추출
        return {c: cache["data"][c] for c in codes if c in cache["data"]}

    import urllib.request
    import urllib.error
    out: dict = {}
    BATCH = 100
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        query = "SERVICE_ITEM:" + ",".join(batch)
        url = ("https://polling.finance.naver.com/api/realtime"
               "?query=" + urllib.parse.quote(query, safe=":,"))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
            text = None
            for enc in ('utf-8', 'euc-kr', 'cp949'):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if not text:
                continue
            payload = json.loads(text)
            datas = payload.get("result", {}).get("areas", [{}])[0].get("datas") or []
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, KeyError) as e:
            log.debug("[live prices] batch %d 실패: %s", i, e)
            continue
        for row in datas:
            cd = row.get("cd")
            nv = row.get("nv")  # 현재가
            cr = row.get("cr")  # 등락률
            aq = row.get("aq")  # 누적 거래량
            if cd and nv is not None:
                out[cd] = {
                    "close": float(nv),
                    "change_pct": round(float(cr or 0.0), 2),
                    "volume_mn": round(float(aq or 0) / 1_000_000, 1),
                }
    if out:
        # 캐시는 누적 (요청 종목 union)
        merged = dict(cache.get("data") or {})
        merged.update(out)
        _LIVE_PRICE_CACHE["data"] = merged
        _LIVE_PRICE_CACHE["ts"] = now
    return out


import urllib.parse  # _fetch_naver_live_prices 위에서 사용


def _fetch_kr_indices_live() -> dict:
    """Naver polling API로 KOSPI/KOSDAQ 실시간 지수 (urllib만, 가벼움)."""
    now = time.time()
    if _KR_INDEX_CACHE["data"] and (now - _KR_INDEX_CACHE["ts"]) < _KR_INDEX_TTL:
        return _KR_INDEX_CACHE["data"]

    import urllib.request
    import urllib.error
    url = ("https://polling.finance.naver.com/api/realtime"
           "?query=SERVICE_INDEX%3AKOSPI%2CKOSDAQ")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
        text = None
        for enc in ('utf-8', 'euc-kr', 'cp949'):
            try:
                text = raw.decode(enc); break
            except UnicodeDecodeError:
                continue
        if not text:
            return {}
        payload = json.loads(text)
        datas = payload["result"]["areas"][0]["datas"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
        log.debug("[KR index] Naver 실패: %s", e)
        return {}

    out = {}
    for row in datas:
        cd = row.get("cd"); nv = row.get("nv"); cr = row.get("cr")
        if cd is None or nv is None:
            continue
        key = "kospi" if cd == "KOSPI" else "kosdaq" if cd == "KOSDAQ" else None
        if not key:
            continue
        # nv는 원지수 × 100 → /100 스케일링
        out[key] = {"value": round(nv / 100, 2),
                    "change_pct": round(float(cr or 0.0), 2)}

    if out:
        _KR_INDEX_CACHE["data"] = out
        _KR_INDEX_CACHE["ts"] = now
    return out


@app.route("/data.json")
def route_data_json():
    if not DATA_JSON.exists():
        st = _get()
        return jsonify({
            "error":   "data.json 아직 준비 중입니다.",
            "state":   st["state"],
            "started": st["started_at"],
        }), 503
    try:
        data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        data = _overlay_live_prices_on_data(data)
        # KOSPI/KOSDAQ stale 보강 — 값 없거나 actual_date 가 오늘 아닐 때 라이브 덮어쓰기
        today_str = now_kst().strftime("%Y%m%d")
        data_stale = data.get("actual_date") != today_str
        kr_missing = (not data.get("kospi") or not data.get("kospi", {}).get("value")
                      or not data.get("kosdaq") or not data.get("kosdaq", {}).get("value"))
        if kr_missing or data_stale or is_market_hours():
            live_idx = _fetch_kr_indices_live()
            if live_idx.get("kospi"):
                data["kospi"] = live_idx["kospi"]
            if live_idx.get("kosdaq"):
                data["kosdaq"] = live_idx["kosdaq"]
        return Response(json.dumps(data, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as exc:
        log.debug("[data.json overlay] %s", exc)
        return Response(
            DATA_JSON.read_text(encoding="utf-8"),
            content_type="application/json; charset=utf-8",
        )


@app.route("/api/index_kr")
def api_index_kr():
    """KOSPI/KOSDAQ 실시간 지수 (Render fallback용 endpoint)."""
    out = _fetch_kr_indices_live()
    if not out:
        return jsonify({"error": "Naver index fetch failed"}), 502
    return jsonify({**out, "ttl_sec": _KR_INDEX_TTL})


@app.route("/api/status")
def api_status():
    st = _get()
    st["data_fresh"]  = data_is_fresh()
    st["data_exists"] = DATA_JSON.exists()
    st["market_open"] = is_market_hours()
    return jsonify(st)


@app.route("/api/refresh", methods=["GET", "POST"])
def api_refresh():
    if _get()["state"] == "running":
        return jsonify({"ok": False, "message": "이미 수집 중입니다."}), 409
    trigger_fetch(background=True, force_market=is_market_hours())
    return jsonify({"ok": True, "message": "수집을 시작했습니다."})


@app.route("/api/interval/<int:minutes>", methods=["POST"])
def api_set_interval(minutes: int):
    if not 1 <= minutes <= 60:
        return jsonify({"ok": False, "message": "1~60분 범위만 가능"}), 400
    _set(interval_minutes=minutes)
    if _scheduler is not None and _scheduler.running:
        _scheduler.reschedule_job("market_update", trigger="interval", minutes=minutes)
        log.info("갱신 주기 변경: %d분", minutes)
    return jsonify({"ok": True, "interval_minutes": minutes})


@app.route("/api/rank_change/<int:minutes_ago>")
def api_rank_change(minutes_ago: int):
    ranking_file = BASE_DIR / "cache" / "ranking_history.json"
    if not ranking_file.exists():
        return jsonify({})
    try:
        loaded = json.loads(ranking_file.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({})

    # 구형식({previous,current})과 신형식(list) 모두 지원
    if isinstance(loaded, dict):
        curr = loaded.get("current",  {})
        prev = loaded.get("previous", {})
    elif isinstance(loaded, list) and loaded:
        curr = loaded[-1]["ranking"]
        target     = now_kst() - timedelta(minutes=minutes_ago)
        target_str = target.strftime("%Y-%m-%d %H:%M:%S")
        prev = next(
            (s["ranking"] for s in reversed(loaded) if s["timestamp"] <= target_str),
            loaded[0]["ranking"]
        )
    else:
        return jsonify({})

    changes = {}
    for name, cr in curr.items():
        pr = prev.get(name)
        changes[name] = (pr - cr) if pr else 0
    return jsonify(changes)


@app.route("/api/themes", methods=["GET"])
def api_themes_get():
    themes_file = BASE_DIR / "themes_mapping.json"
    if not themes_file.exists():
        return jsonify([])
    return Response(
        themes_file.read_text(encoding="utf-8"),
        content_type="application/json; charset=utf-8",
    )


@app.route("/api/themes", methods=["POST"])
def api_themes_post():
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "invalid data"}), 400
    themes_file = BASE_DIR / "themes_mapping.json"
    themes_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonify({"ok": True})


@app.route("/api/stock_search")
def api_stock_search():
    """
    국내 종목 검색. 데이터 소스 우선순위:
      1) naver_universe (4,000+ 종목 전체) — Phase 10
      2) stock_master (테마 구성 134종목) — 폴백
    대소문자 무시 부분 일치. 정확 일치 → 접두 일치 → 부분 일치 순 정렬.
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    ql = q.lower()

    def _score(code: str, name: str) -> int:
        """낮을수록 앞에 노출. 0=정확일치, 1=접두, 2=부분."""
        cl = code.lower()
        nl = (name or "").lower()
        if cl == ql or nl == ql: return 0
        if cl.startswith(ql) or nl.startswith(ql): return 1
        return 2

    results: list[tuple[int, str, str]] = []

    # 1) naver_universe 우선
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    if stocks:
        for code, rec in stocks.items():
            name = rec.get("name") or ""
            if ql in code.lower() or ql in name.lower():
                results.append((_score(code, name), code, name))
    else:
        # 2) 폴백: 테마 기반 stock_master
        import glob as _glob
        masters = sorted(
            _glob.glob(str(BASE_DIR / "cache" / "stock_master_*.json")), reverse=True
        )
        if masters:
            try:
                master: dict = json.loads(open(masters[0], encoding="utf-8").read())
                for code, name in master.items():
                    if ql in code.lower() or ql in (name or "").lower():
                        results.append((_score(code, name), code, name))
            except Exception:
                pass

    results.sort(key=lambda x: (x[0], x[1]))
    return jsonify([{"code": c, "name": n} for _, c, n in results[:10]])


def _get_krx_all_stocks_cached() -> dict:
    """
    KRX Open API 로 KOSPI+KOSDAQ 전 종목 시세를 조회, 종목코드-레코드 dict 로 반환.
    일 1회 호출 → cache/krx_all_stocks_{today}.json.
    구독되지 않은 경우 {} 반환.

    각 레코드는 KRX 응답 필드를 그대로 보관 (ISU_SRT_CD, ISU_ABBRV, TDD_CLSPRC,
    FLUC_RT, ACC_TRDVAL, MKTCAP 등). 호출 측에서 필요 키만 추출.
    """
    today      = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"krx_all_stocks_{today}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        import krx_api
    except ImportError:
        return {}

    if not krx_api.has_api_key():
        return {}

    rows: list = []
    k = krx_api.krx_all_stocks_kospi(today)
    if k: rows.extend(k)
    q = krx_api.krx_all_stocks_kosdaq(today)
    if q: rows.extend(q)

    if not rows:
        return {}

    # code → record
    out = {}
    for r in rows:
        code = r.get("ISU_SRT_CD") or r.get("ISU_CD") or r.get("isuSrtCd")
        if code:
            out[str(code).strip()] = r

    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _krx_get_float(row: dict, *keys) -> float | None:
    """KRX 응답에서 숫자 필드 추출. 콤마 포함 문자열도 처리."""
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            continue
    return None


def _get_stock_name(code: str) -> str | None:
    """
    종목 코드로부터 이름 조회. 우선순위:
      1) SQLite stocks 테이블 (4,000+ KR 종목 전체 커버)
      2) data.json 테마 (활성 유니버스)
      3) cache/stock_master_*.json (테마 구성 + 유니버스)
    이전엔 1번이 없어서 활성 유니버스 밖 종목이 코드만 반환되는 케이스
    있었음 (flow_cache.name 95% 가 코드로 들어간 원인).
    """
    code = (code or "").strip()
    if not code:
        return None
    # 1) SQLite stocks (가장 큰 풀, 정확)
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as conn:
                row = conn.execute(
                    "SELECT name FROM stocks WHERE code=?", (code,)
                ).fetchone()
                if row and row["name"]:
                    return row["name"]
        except Exception:
            pass
    # 2) data.json
    if DATA_JSON.exists():
        try:
            data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
            for theme in data.get("themes", []):
                for s in theme.get("stocks", []):
                    if s.get("code") == code:
                        return s.get("name") or code
        except Exception:
            pass
    # 3) stock_master cache
    import glob as _glob
    masters = sorted(
        _glob.glob(str(BASE_DIR / "cache" / "stock_master_*.json")), reverse=True
    )
    for m in masters:
        try:
            with open(m, encoding="utf-8") as f:
                master = json.load(f)
            if code in master:
                return master[code]
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 뉴스 검색 (네이버 검색 API)  —  Phase 10
# ─────────────────────────────────────────────────────────────────────────────
def _format_time_ago(pub_date_str: str) -> str:
    """RFC 2822 (pubDate) → 'N분 전/N시간 전/N일 전' 한국어 표기."""
    try:
        from email.utils import parsedate_to_datetime
        pub_dt = parsedate_to_datetime(pub_date_str)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        delta = now_kst() - pub_dt.astimezone(KST)
        mins  = int(delta.total_seconds() / 60)
        if mins < 1:   return "방금"
        if mins < 60:  return f"{mins}분 전"
        hrs = mins // 60
        if hrs < 24:   return f"{hrs}시간 전"
        days = hrs // 24
        return f"{days}일 전"
    except Exception:
        return ""


_NEWS_SOURCE_MAP = {
    # 주요 경제/종합지
    "hankyung.com": "한국경제",       "mk.co.kr":      "매일경제",
    "sedaily.com":  "서울경제",       "edaily.co.kr":  "이데일리",
    "mt.co.kr":     "머니투데이",     "news1.kr":      "뉴스1",
    "newsis.com":   "뉴시스",         "yna.co.kr":     "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV",
    "chosun.com":   "조선일보",       "donga.com":     "동아일보",
    "joongang.co.kr": "중앙일보",     "khan.co.kr":    "경향신문",
    "heraldcorp.com": "헤럴드경제",   "fnnews.com":    "파이낸셜뉴스",
    "etnews.com":   "전자신문",       "thebell.co.kr": "더벨",
    "bloter.net":   "블로터",         "businesspost.co.kr": "비즈니스포스트",
    "infostock.co.kr": "인포스탁데일리", "etoday.co.kr":    "이투데이",
    "ajunews.com":  "아주경제",       "biz.chosun.com": "조선비즈",
    "dt.co.kr":     "디지털타임스",   "asiae.co.kr":   "아시아경제",
    "einfomax.co.kr": "연합인포맥스", "newspim.com":   "뉴스핌",
    "tf.co.kr":     "더팩트",         "ebn.co.kr":     "EBN",
    "smedaily.co.kr": "SME데일리",    "pinpointnews.co.kr": "핀포인트뉴스",
    "niceeconomy.co.kr": "나이스경제", "lcnews.co.kr": "로컬뉴스",
    "joongangenews.com": "중앙이뉴스",
}

def _news_source(url: str) -> str:
    u = (url or "").lower()
    for domain, name in _NEWS_SOURCE_MAP.items():
        if domain in u:
            return name
    return "기타"


def _strip_html(s: str) -> str:
    import re as _re
    return _re.sub(r"<[^>]+>", "", s or "")


# ─────────────────────────────────────────────────────────────────────────────
# US MARKET (Phase 14)  —  S&P 500 via yfinance + Wikipedia
# ─────────────────────────────────────────────────────────────────────────────
def _sp500_tickers() -> list[dict]:
    """
    S&P 500 + S&P 400 MidCap + S&P 600 SmallCap 구성 종목 (Wikipedia).
    캐시: cache/sp500_tickers.json, TTL 7일.
    Returns: [{symbol, name, sector, sub_industry}, ...]
    """
    cache_file = BASE_DIR / "cache" / "sp500_tickers.json"
    if cache_file.exists():
        try:
            age_days = (now_kst().timestamp() - cache_file.stat().st_mtime) / 86400
            if age_days < 7:
                return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        import pandas as _pd
    except ImportError:
        return []

    wiki_sources = [
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "S&P500"),
        ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "S&P400"),
        ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", "S&P600"),
    ]

    seen: set[str] = set()
    out: list[dict] = []

    for url, label in wiki_sources:
        try:
            tables = _pd.read_html(url, storage_options={"User-Agent": "Mozilla/5.0"})
            if not tables:
                continue
            df = tables[0]
            added = 0
            for _, row in df.iterrows():
                sym = str(row.get("Symbol") or row.get("Ticker symbol") or "").strip()
                sym = sym.replace(".", "-")
                if not sym or sym == "nan" or sym in seen:
                    continue
                seen.add(sym)
                name = str(row.get("Security") or row.get("Company") or sym)
                sector = str(row.get("GICS Sector") or row.get("GICS sector") or "")
                sub = str(row.get("GICS Sub-Industry") or row.get("GICS sub-industry") or "")
                out.append({
                    "symbol": sym, "name": name,
                    "sector": sector, "sub_industry": sub,
                })
                added += 1
            log.info("[US Universe] %s: %d종목", label, added)
        except Exception as exc:
            log.debug("[US Universe] %s 파싱 실패: %s", label, exc)

    # 소형주/테마주 보충 (S&P 1500 밖 hot small caps)
    try:
        additional = _get_additional_us_tickers()
        seen_syms = {t["symbol"] for t in out}
        for sym in additional:
            if sym not in seen_syms:
                out.append({"symbol": sym, "name": sym, "sector": "",
                            "sub_industry": "", "source": "additional"})
                seen_syms.add(sym)
        log.info("[US Universe] 소형주 보충: %d종목", len(additional))
    except Exception as exc:
        log.debug("[US Universe] additional 실패: %s", exc)

    if out:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    log.info("[US Universe] 총 %d종목 로드 (S&P 500+400+600 + 추가)", len(out))
    return out


# S&P 1500 밖 핵심 소형주·테마주 (고정 리스트, 자동 보충 실패 시 백업)
_US_HOT_SMALL_CAPS = [
    # 우주
    "PL", "RKLB", "LUNR", "ASTS", "ASTR", "RDW", "MNTS", "BKSY", "SPCE",
    # 양자컴퓨터
    "IONQ", "RGTI", "QUBT", "QBTS", "ARQQ",
    # AI 소형주
    "SOUN", "BBAI", "PLTR",
    # 원전/SMR
    "OKLO", "SMR", "NNE", "LEU",
    # 반도체 소형주
    "AXTI", "ACLS", "FORM", "CAMT", "ONTO", "IMMR",
    # eVTOL/로봇
    "JOBY", "LILM", "ACHR",
    # 크립토 마이닝
    "RIOT", "IREN", "CIFR", "CLSK", "BTBT", "HUT",
]


def _get_additional_us_tickers() -> set:
    """S&P 1500 밖 추가 종목: 고정 hot_small_caps + watchlist/portfolio US + 테마."""
    additional: set = set(_US_HOT_SMALL_CAPS)
    try:
        # 관심종목/포트폴리오에 있는 US 종목
        for path in (BASE_DIR / "cache" / "server_watchlist.json",
                     BASE_DIR / "cache" / "server_portfolio.json"):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items = data.get("items") or data.get("positions") or data or []
                if isinstance(items, dict):
                    items = items.get("items") or items.get("positions") or []
                for it in items:
                    code = (it.get("code") or "").strip()
                    mkt = (it.get("market") or "").lower()
                    if code and mkt == "us" and code.isalpha() and 1 <= len(code) <= 5:
                        additional.add(code)
            except Exception:
                pass

        # US agent 파이프라인 테마 종목
        try:
            from agents.pipeline import _US_THEME_TO_GICS, get_us_theme_stocks
            for theme in (_US_THEME_TO_GICS or {}).keys():
                for code in (get_us_theme_stocks(theme) or []):
                    if code and len(code) <= 5:
                        additional.add(code)
        except Exception:
            pass
    except Exception as exc:
        log.debug("[US additional] %s", exc)
    return additional


def add_us_stocks_now(tickers: list) -> dict:
    """누락된 US 종목을 yfinance로 즉시 조회 → stocks 테이블 INSERT."""
    if not (_SQLITE_OK and USE_SQLITE):
        return {"error": "SQLite 비활성"}
    try:
        import yfinance as _yf
    except ImportError:
        return {"error": "yfinance 미설치"}

    added = 0; failed = 0; skipped = 0
    details: list = []
    with _get_db() as conn:
        for raw in tickers:
            sym = (raw or "").strip().upper()
            if not sym:
                continue
            try:
                t = _yf.Ticker(sym)
                info = t.info or {}
                price = info.get("regularMarketPrice") or info.get("currentPrice") or 0
                prev = info.get("regularMarketPreviousClose") or info.get("previousClose") or 0
                if not price:
                    details.append(f"{sym}: no price")
                    skipped += 1
                    continue
                chg = round((price / prev - 1) * 100, 2) if prev else 0
                name = info.get("shortName") or info.get("longName") or sym
                sector = info.get("sector") or ""
                cap = info.get("marketCap") or 0
                conn.execute(
                    "INSERT OR REPLACE INTO stocks "
                    "(code, name, market, sector, market_cap, close, change_pct, "
                    "volume_mn, sectors_json, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))",
                    (sym, name, "US", sector, cap, price, chg,
                     (info.get("regularMarketVolume") or 0) / 1e6,
                     json.dumps([sector] if sector else [], ensure_ascii=False))
                )
                added += 1
                details.append(f"{sym}: ✓ {name} ${price} ({sector})")
            except Exception as exc:
                failed += 1
                details.append(f"{sym}: err {exc}")
        conn.commit()
    log.info("[US add] +%d, skip %d, fail %d", added, skipped, failed)
    return {"added": added, "skipped": skipped, "failed": failed, "details": details}


@app.route("/api/us/add_stocks", methods=["POST"])
def api_us_add_stocks():
    """수동으로 US 종목 추가 (쉼표 구분 또는 body.tickers 배열).
    기본: _US_HOT_SMALL_CAPS 전체."""
    data = request.get_json(silent=True) or {}
    tickers = data.get("tickers")
    if not tickers:
        q = request.args.get("tickers", "")
        tickers = [t.strip() for t in q.split(",") if t.strip()] if q else _US_HOT_SMALL_CAPS
    return jsonify(add_us_stocks_now(tickers))


def _is_us_market_hours() -> bool:
    """미국 장중 여부 (대략 KST 22:30~06:00)."""
    now = now_kst()
    if now.weekday() >= 5 and now.weekday() != 0:  # 월~금의 장이 KST 기준 토요일까지 걸침
        pass
    t = now.hour * 100 + now.minute
    return (t >= 2230) or (t <= 600)


def _format_usd_cap(value) -> str:
    """시가총액 $ 단위 포맷."""
    if not value:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def _fetch_us_market_data(force: bool = False) -> dict:
    """
    S&P 500 전 종목 당일 시세 batch 수집 + 섹터별 집계.
    캐시: cache/us_market_{YYYYMMDD_KST}.json
    TTL: 미국 장중 15분 / 장외 24시간.
    부분 빌드(< 400종목) 감지 시 자동 재빌드.
    """
    today = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"us_market_{today}.json"
    MIN_STOCKS = 400   # 정상 빌드 최소 기준 (S&P1500 중 일부 실패 허용)
    if cache_file.exists() and not force:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            n_cached = len(cached.get("all_stocks") or [])
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            ttl = 15 if _is_us_market_hours() else 1440
            if n_cached >= MIN_STOCKS and age_min < ttl:
                return cached
            if n_cached < MIN_STOCKS:
                print(f"[US] 부분 빌드 감지 ({n_cached}종목) — 재빌드")
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return {"error": "yfinance 미설치", "sectors": [], "all_stocks": []}

    tickers = _sp500_tickers()
    if not tickers:
        return {"error": "S&P 500 리스트 없음", "sectors": [], "all_stocks": []}

    symbols = [t["symbol"] for t in tickers]
    by_sym = {t["symbol"]: t for t in tickers}

    stocks: list[dict] = []
    print(f"[US] yfinance batch download, {len(symbols)} 종목…")

    # 50 종목씩 청크 (rate-limit 완화) + 청크간 sleep
    chunk_size = 50
    failed_chunks: list[list[str]] = []
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        try:
            df = _yf.download(
                " ".join(chunk),
                period="5d",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            print(f"[US chunk {i}] fail: {exc}")
            failed_chunks.append(chunk)
            continue
        if df is None or df.empty:
            print(f"[US chunk {i}] empty result — retry later")
            failed_chunks.append(chunk)
            time.sleep(0.3)
            continue

        for sym in chunk:
            try:
                if sym not in df.columns.get_level_values(0):
                    continue
                t_df = df[sym].dropna(how="all")
                if len(t_df) < 1:
                    continue
                cur = float(t_df["Close"].iloc[-1])
                prev = float(t_df["Close"].iloc[-2]) if len(t_df) >= 2 else cur
                chg_pct = round((cur / prev - 1) * 100, 2) if prev else 0.0
                vol = int(t_df["Volume"].iloc[-1] or 0)
                info = by_sym[sym]
                stocks.append({
                    "symbol":     sym,
                    "name":       info["name"],
                    "sector":     info["sector"],
                    "price":      round(cur, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": chg_pct,
                    "volume":     vol,
                    "volume_mn":  round(vol * cur / 1_000_000, 1),    # $M traded
                })
            except Exception:
                continue
        time.sleep(0.3)   # 청크 간 rate-limit 완화

    # 실패 청크 개별 재시도 (1회)
    if failed_chunks:
        retry_syms = [s for chunk in failed_chunks for s in chunk]
        print(f"[US] 실패 청크 재시도: {len(retry_syms)} 종목 (개별)")
        for sym in retry_syms:
            try:
                t_df = _yf.Ticker(sym).history(period="5d", auto_adjust=True)
                if t_df is None or t_df.empty:
                    continue
                cur = float(t_df["Close"].iloc[-1])
                prev = float(t_df["Close"].iloc[-2]) if len(t_df) >= 2 else cur
                chg_pct = round((cur / prev - 1) * 100, 2) if prev else 0.0
                vol = int(t_df["Volume"].iloc[-1] or 0)
                info = by_sym[sym]
                stocks.append({
                    "symbol":     sym,
                    "name":       info["name"],
                    "sector":     info["sector"],
                    "price":      round(cur, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": chg_pct,
                    "volume":     vol,
                    "volume_mn":  round(vol * cur / 1_000_000, 1),
                })
            except Exception as exc:
                print(f"[US retry] {sym}: {exc}")
            time.sleep(0.05)

    if not stocks:
        return {"error": "yfinance 응답 없음", "sectors": [], "all_stocks": []}

    print(f"[US] yfinance 최종: {len(stocks)}/{len(symbols)} 종목 수집")

    # 섹터별 그룹
    sectors_map: dict = {}
    for s in stocks:
        sec = s["sector"] or "Unknown"
        bucket = sectors_map.setdefault(sec, {
            "name":             sec,
            "stocks":           [],
            "weighted_avg_pct": 0.0,
            "stock_count":      0,
        })
        bucket["stocks"].append(s)

    for bucket in sectors_map.values():
        tot = sum(s["volume_mn"] for s in bucket["stocks"]) or 1
        bucket["weighted_avg_pct"] = round(
            sum(s["change_pct"] * s["volume_mn"] for s in bucket["stocks"]) / tot, 2
        )
        bucket["stock_count"] = len(bucket["stocks"])
        bucket["stocks"].sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    # 섹터 리스트 (등락률 내림차순)
    sector_list = sorted(
        sectors_map.values(),
        key=lambda b: abs(b["weighted_avg_pct"]),
        reverse=True,
    )

    result = {
        "updated_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":      "US",
        "total_stocks": len(stocks),
        "sectors":     sector_list,
        "all_stocks":  stocks,
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False),
                          encoding="utf-8")
    print(f"[US] 완료: {len(stocks)} stocks, {len(sector_list)} sectors")
    # 캐시 → DB 동기화 (사용자가 보는 모든 화면이 stocks 테이블을 참조)
    try:
        _sync_us_stocks_to_db(stocks)
    except Exception as exc:
        log.debug("[US] DB sync fail: %s", exc)
    return result


def _sync_us_stocks_to_db(stocks: list) -> int:
    """us_market 캐시의 all_stocks 를 stocks 테이블에 INSERT OR REPLACE.
    호출처: _fetch_us_market_data (캐시 빌드 후), _startup (부팅 시), 매시간 cron."""
    if not (_SQLITE_OK and USE_SQLITE) or not stocks:
        return 0
    n = 0
    try:
        with _get_db() as conn:
            for s in stocks:
                sym = (s.get("symbol") or "").strip()
                if not sym:
                    continue
                sect = s.get("sector") or ""
                conn.execute(
                    "INSERT INTO stocks "
                    "(code, name, market, sector, market_cap, close, change_pct, "
                    " volume_mn, sectors_json, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?, datetime('now')) "
                    "ON CONFLICT(code) DO UPDATE SET "
                    "  name = excluded.name, "
                    "  market = excluded.market, "
                    "  sector = excluded.sector, "
                    "  market_cap = COALESCE(excluded.market_cap, stocks.market_cap), "
                    "  close = excluded.close, "
                    "  change_pct = excluded.change_pct, "
                    "  volume_mn = excluded.volume_mn, "
                    "  sectors_json = excluded.sectors_json, "
                    "  updated_at = datetime('now')",
                    (sym, s.get("name") or sym, "US", sect,
                     s.get("market_cap"), s.get("price"), s.get("change_pct"),
                     s.get("volume_mn"),
                     json.dumps([sect] if sect else [], ensure_ascii=False))
                )
                n += 1
            conn.commit()
        log.info("[US sync] %d종목 DB upsert", n)
    except Exception as exc:
        log.warning("[US sync] %s", exc)
    return n


def sync_us_market_to_db_from_cache() -> dict:
    """오늘자 us_market 캐시 → DB 일괄 동기화. 스케줄러용."""
    today = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"us_market_{today}.json"
    if not cache_file.exists():
        # 가장 최근 캐시 — us_market_summary_*.json (시황 텔레그램용)는 제외
        try:
            files = sorted(
                BASE_DIR.glob("cache/us_market_2[0-9][0-9][0-9][0-9][0-9][0-9][0-9].json"),
                reverse=True,
            )
            cache_file = files[0] if files else None
        except Exception:
            cache_file = None
    if not cache_file or not cache_file.exists():
        return {"ok": False, "reason": "us_market 캐시 없음"}
    try:
        d = json.loads(cache_file.read_text(encoding="utf-8"))
        n = _sync_us_stocks_to_db(d.get("all_stocks") or [])
        return {"ok": True, "synced": n, "cache": cache_file.name,
                "cache_updated_at": d.get("updated_at")}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


@app.route("/api/us/sync_db", methods=["POST", "GET"])
def api_us_sync_db():
    """수동 트리거: us_market 캐시 → DB 동기화."""
    return jsonify(sync_us_market_to_db_from_cache())


@app.route("/api/refresh_all", methods=["POST", "GET"])
def api_refresh_all():
    """수동 트리거: 모든 글로벌 데이터(매크로·야간선물·옵션·F&G·밸류체인) 즉시 갱신."""
    try:
        _refresh_global_data_periodic()
        return jsonify({"ok": True, "message": "글로벌 데이터 갱신 완료"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _build_us_market_background():
    """
    서버 부팅 시 백그라운드로 S&P 500 전 종목 batch 를 돌린다.
    초회 빌드 ~180s 소요 (yfinance). 정상 캐시(≥400종목) 존재 시 스킵.
    부분 빌드는 감지하여 재실행.
    """
    today = now_kst().strftime("%Y%m%d")
    out_file = BASE_DIR / "cache" / f"us_market_{today}.json"
    if out_file.exists():
        try:
            cached = json.loads(out_file.read_text(encoding="utf-8"))
            n = len(cached.get("all_stocks") or [])
            if n >= 400:
                log.info("us_market 캐시 정상 (%d종목) — 스킵 (%s)", n, out_file.name)
                return
            log.warning("us_market 부분 빌드 감지 (%d종목) — 재빌드 강제", n)
        except Exception as exc:
            log.warning("us_market 캐시 읽기 실패 — 재빌드: %s", exc)
    lock = out_file.with_suffix(".lock")
    if lock.exists():
        return
    try:
        lock.touch()
        log.info("▶  S&P 500 market 백그라운드 빌드 시작 (~3분)")
        _fetch_us_market_data(force=True)
        log.info("✓  S&P 500 market 빌드 완료")
    except Exception as exc:
        log.error("S&P 500 빌드 실패: %s", exc)
    finally:
        try: lock.unlink()
        except Exception: pass


@app.route("/api/us/market")
def api_us_market():
    """
    S&P 500 전 종목 시세 + 섹터 집계. 캐시 우선. 캐시 없으면 빌드 중 표시
    (프론트가 polling 으로 재시도).
    """
    today = now_kst().strftime("%Y%m%d")
    out_file = BASE_DIR / "cache" / f"us_market_{today}.json"
    if out_file.exists():
        try:
            return Response(
                out_file.read_text(encoding="utf-8"),
                content_type="application/json; charset=utf-8",
            )
        except Exception:
            pass
    # 캐시 없음 → 백그라운드 빌드 킥
    threading.Thread(target=_build_us_market_background,
                     daemon=True, name="us-market-build").start()
    return jsonify({
        "building":    True,
        "message":     "S&P 500 batch 빌드 중입니다. 약 2~3분 후 다시 시도하세요.",
        "sectors":     [],
        "all_stocks":  [],
        "total_stocks": 0,
    }), 202


@app.route("/api/us/search")
def api_us_search():
    """US 종목 검색.
    데이터 소스: 1) DB stocks(market='US') 우선 (authoritative, 시총순 정렬)
                 2) _sp500_tickers() 캐시 폴백 (DB 아직 비어있을 때)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify([])
    ql = q.lower()
    ql_like = f"%{ql}%"

    results: list = []
    seen: set = set()

    # 1) DB 우선 — 정확 일치 > 접두 > 부분 순
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                rows = conn.execute(
                    "SELECT code, name, market, sector, close, market_cap "
                    "FROM stocks WHERE market = 'US' "
                    "AND (LOWER(code) LIKE ? OR LOWER(name) LIKE ?) "
                    "ORDER BY "
                    "  CASE WHEN LOWER(code) = ? OR LOWER(name) = ? THEN 0 "
                    "       WHEN LOWER(code) LIKE ? OR LOWER(name) LIKE ? THEN 1 "
                    "       ELSE 2 END, "
                    "  COALESCE(market_cap, 0) DESC "
                    "LIMIT 20",
                    (ql_like, ql_like, ql, ql, f"{ql}%", f"{ql}%")
                ).fetchall()
                for r in rows:
                    code = r["code"]
                    if code in seen:
                        continue
                    seen.add(code)
                    results.append({
                        "code":       code,
                        "name":       r["name"],
                        "sector":     r["sector"] or "",
                        "price":      r["close"],
                        "market_cap": r["market_cap"],
                    })
        except Exception as exc:
            log.debug("[us/search] DB: %s", exc)

    # 2) 캐시 폴백 — DB에 없는 종목만 보충
    if len(results) < 10:
        try:
            tickers = _sp500_tickers()
            for t in tickers:
                sym = t["symbol"]
                if sym in seen:
                    continue
                if ql in sym.lower() or ql in (t.get("name") or "").lower():
                    results.append({
                        "code":   sym,
                        "name":   t.get("name") or sym,
                        "sector": t.get("sector") or "",
                    })
                    seen.add(sym)
                    if len(results) >= 20:
                        break
        except Exception:
            pass

    return jsonify(results[:20])


@app.route("/api/us/extended/<symbol>")
def api_us_extended(symbol: str):
    """미국 프리마켓/애프터마켓 가격. yfinance info 기반."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    try:
        info = _yf.Ticker(symbol).info or {}
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    market_state = info.get("marketState", "")
    regular_price = info.get("regularMarketPrice")
    regular_chg = info.get("regularMarketChangePercent")

    result = {
        "symbol": symbol,
        "market_state": market_state,
        "regular_price": regular_price,
        "regular_change_pct": regular_chg,
        "extended_price": None,
        "extended_change_pct": None,
        "extended_label": None,
    }

    # PRE: 프리마켓 진행 중
    # POST/CLOSED: 애프터마켓 또는 장 마감 후
    if market_state == "PRE" and info.get("preMarketPrice"):
        result["extended_price"] = info.get("preMarketPrice")
        result["extended_change_pct"] = info.get("preMarketChangePercent")
        result["extended_label"] = "프리마켓"
    elif market_state in ("POST", "POSTPOST", "CLOSED") and info.get("postMarketPrice"):
        result["extended_price"] = info.get("postMarketPrice")
        result["extended_change_pct"] = info.get("postMarketChangePercent")
        result["extended_label"] = "애프터마켓"
    elif info.get("preMarketPrice"):
        # fallback: marketState가 애매해도 preMarketPrice가 있으면 표시
        result["extended_price"] = info.get("preMarketPrice")
        result["extended_change_pct"] = info.get("preMarketChangePercent")
        result["extended_label"] = "프리마켓"
    elif info.get("postMarketPrice"):
        result["extended_price"] = info.get("postMarketPrice")
        result["extended_change_pct"] = info.get("postMarketChangePercent")
        result["extended_label"] = "애프터마켓"

    return jsonify(result)


@app.route("/api/us/chart/<symbol>")
def api_us_chart(symbol: str):
    """
    미국 종목 차트 (yfinance history).
    응답 스키마는 /api/chart 와 호환 → 프론트의 _drawCandles 등 무수정 재사용.
    """
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    try:
        days = int(request.args.get("days", "180"))
    except ValueError:
        days = 180
    days = max(7, min(3650, days))

    today_kst = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"us_chart_{symbol}_{days}d_{today_kst}.json"

    # ── SQLite 캐시 우선 조회 ──
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as _conn:
                row = _conn.execute(
                    "SELECT * FROM chart_cache WHERE code=? AND days=? AND cache_date=?",
                    (symbol, days, today_kst),
                ).fetchone()
                if row:
                    d = _db_row(row)
                    if d and d.get("rsi_macd"):
                        result = {
                            "code": d["code"], "name": d.get("name", symbol),
                            "days": d["days"], "market": "US", "currency": "USD",
                            "dates": d.get("dates", []),
                            "open": d.get("open", []), "high": d.get("high", []),
                            "low": d.get("low", []), "close": d.get("close", []),
                            "volume": d.get("volume", []),
                            "bollinger": d.get("bollinger", {}),
                            "fibonacci": d.get("fibonacci", {}),
                            "trendlines": d.get("trendlines", {}),
                            "analysis": d.get("analysis", {}),
                            "rsi_macd": d.get("rsi_macd", {}),
                            "adx": d.get("adx", {}),
                        }
                        return jsonify(result)
        except Exception as exc:
            log.debug("[SQLite] us_chart read fail %s: %s", symbol, exc)

    # ── JSON 파일 캐시 폴백 ──
    if cache_file.exists():
        try:
            _cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if "rsi_macd" in _cached and _cached["rsi_macd"] is not None:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    period_map = {30: "1mo", 90: "3mo", 180: "6mo",
                  365: "1y", 730: "2y", 1095: "5y", 1825: "5y", 3650: "10y"}
    period = "6mo"
    for d in sorted(period_map):
        if days <= d:
            period = period_map[d]; break

    try:
        t = _yf.Ticker(symbol)
        df = t.history(period=period, auto_adjust=True)
    except Exception as exc:
        return jsonify({"error": f"yfinance 실패: {exc}"}), 502

    if df is None or df.empty:
        return jsonify({"error": "데이터 없음"}), 404

    dates   = [d.strftime("%Y-%m-%d") for d in df.index]
    opens   = [round(float(v), 2) for v in df["Open"].tolist()]
    highs   = [round(float(v), 2) for v in df["High"].tolist()]
    lows    = [round(float(v), 2) for v in df["Low"].tolist()]
    closes  = [round(float(v), 2) for v in df["Close"].tolist()]
    volumes = [int(v) for v in df["Volume"].tolist()]

    bollinger  = _calc_bollinger(closes)
    fibonacci  = _calc_fibonacci(highs, lows)
    trendlines = _calc_trendlines(highs, lows, closes)
    analysis   = _generate_analysis(closes, volumes, bollinger, fibonacci, trendlines)

    name = symbol
    try:
        info = t.info or {}
        name = info.get("shortName") or info.get("longName") or symbol
    except Exception:
        pass

    result = {
        "code":       symbol,
        "name":       name,
        "market":     "US",
        "currency":   "USD",
        "days":       days,
        "dates":      dates,
        "open":       opens, "high": highs, "low": lows, "close": closes,
        "volume":     volumes,
        "bollinger":  bollinger,
        "fibonacci":  fibonacci,
        "trendlines": trendlines,
        "analysis":   analysis,
        "rsi_macd":   _calc_rsi_macd(closes),
        "adx":        _calc_adx(highs, lows, closes),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False),
                          encoding="utf-8")
    _save_chart_to_sqlite(symbol, days, today_kst, result)
    return jsonify(result)


@app.route("/api/us/news/<symbol>")
def api_us_news(symbol: str):
    """미국 종목 뉴스 (yfinance Ticker.news, 영어 원문). 30분 캐시."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    cache_file = BASE_DIR / "cache" / f"us_news_{symbol}.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 30:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    try:
        news = _yf.Ticker(symbol).news or []
    except Exception as exc:
        return jsonify({"error": f"yfinance 뉴스 실패: {exc}", "items": []}), 502

    def _time_ago_utc(ts: int) -> str:
        if not ts:
            return ""
        from datetime import timezone as _tz
        dt = datetime.fromtimestamp(ts, tz=_tz.utc)
        delta = datetime.now(_tz.utc) - dt
        mins = int(delta.total_seconds() / 60)
        if mins < 1:  return "방금"
        if mins < 60: return f"{mins}분 전"
        hrs = mins // 60
        if hrs < 24:  return f"{hrs}시간 전"
        return f"{hrs // 24}일 전"

    items = []
    for a in news[:15]:
        # yfinance 뉴스 스키마가 최근 버전에서 {content: {...}} 로 감싸짐
        c = a.get("content") or a
        title = c.get("title") or ""
        link  = (c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("clickThroughUrl"), dict) else c.get("link", "")
        if not link:
            link = (c.get("canonicalUrl") or {}).get("url", "") if isinstance(c.get("canonicalUrl"), dict) else ""
        provider = c.get("provider") or {}
        source = provider.get("displayName") if isinstance(provider, dict) else c.get("publisher", "")
        pub_date = c.get("pubDate") or ""
        # providerPublishTime (legacy) 또는 pubDate (new ISO)
        time_ago = ""
        if c.get("providerPublishTime"):
            time_ago = _time_ago_utc(c["providerPublishTime"])
        elif pub_date:
            try:
                from datetime import timezone as _tz
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                delta = datetime.now(_tz.utc) - dt
                mins = int(delta.total_seconds() / 60)
                if mins < 60: time_ago = f"{mins}분 전"
                elif mins < 1440: time_ago = f"{mins // 60}시간 전"
                else: time_ago = f"{mins // 1440}일 전"
            except Exception:
                pass

        thumbnail = ""
        thumb = c.get("thumbnail")
        if isinstance(thumb, dict):
            res = thumb.get("resolutions") or []
            if res and isinstance(res, list):
                thumbnail = res[0].get("url", "")
            elif thumb.get("originalUrl"):
                thumbnail = thumb["originalUrl"]

        if title and link:
            items.append({
                "title":     title,
                "link":      link,
                "source":    source or "",
                "thumbnail": thumbnail,
                "pubDate":   pub_date,
                "timeAgo":   time_ago,
            })

    result = {"symbol": symbol, "count": len(items), "items": items}
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/us/financial/<symbol>")
def api_us_financial(symbol: str):
    """미국 종목 재무 요약 (yfinance Ticker.info). 24시간 캐시."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    cache_file = BASE_DIR / "cache" / f"us_fin_{symbol}.json"
    if cache_file.exists():
        try:
            age_hr = (now_kst().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hr < 24:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    try:
        info = _yf.Ticker(symbol).info or {}
    except Exception as exc:
        return jsonify({"error": f"yfinance info 실패: {exc}"}), 502

    def _pct(v):
        if v is None: return None
        try: return round(float(v) * 100, 2)
        except (TypeError, ValueError): return None

    result = {
        "symbol":         symbol,
        "name":           info.get("shortName") or info.get("longName") or symbol,
        "sector":         info.get("sector"),
        "industry":       info.get("industry"),
        "per":            info.get("trailingPE"),
        "forward_per":    info.get("forwardPE"),
        "pbr":            info.get("priceToBook"),
        "roe":            _pct(info.get("returnOnEquity")),
        "profit_margin":  _pct(info.get("profitMargins")),
        "dividend_yield": info.get("dividendYield"),  # yfinance returns percent already
        "market_cap":     info.get("marketCap"),
        "market_cap_str": _format_usd_cap(info.get("marketCap")),
        "revenue":        info.get("totalRevenue"),
        "revenue_str":    _format_usd_cap(info.get("totalRevenue")),
        "target_price":   info.get("targetMeanPrice"),
        "recommendation": info.get("recommendationKey"),
        "beta":           info.get("beta"),
        "w52_high":       info.get("fiftyTwoWeekHigh"),
        "w52_low":        info.get("fiftyTwoWeekLow"),
        "current_price":  info.get("currentPrice") or info.get("regularMarketPrice"),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/us/screener")
def api_us_screener():
    """미국 스크리너 — _fetch_us_market_data 결과에서 서버-사이드 필터."""
    args = request.args
    min_change = float(args.get("min_change", "-100") or -100)
    max_change = float(args.get("max_change", "100")  or 100)
    min_volume = float(args.get("min_volume", "0")    or 0)   # $M
    sector     = (args.get("sector") or "").strip()
    q          = (args.get("q") or "").strip()

    data = _fetch_us_market_data()
    if "error" in data:
        return jsonify({"error": data["error"], "count": 0, "stocks": []}), 503

    results = []
    q_up = q.upper()
    q_lo = q.lower()
    for s in data.get("all_stocks", []):
        if s["change_pct"] < min_change or s["change_pct"] > max_change: continue
        if s["volume_mn"] < min_volume: continue
        if sector and s.get("sector") != sector: continue
        if q and (q_up not in s["symbol"].upper() and q_lo not in s["name"].lower()):
            continue
        results.append(s)

    results.sort(key=lambda r: r["volume_mn"], reverse=True)
    return jsonify({
        "count":           len(results),
        "stocks":          results[:200],
        "universe_source": "sp500_yfinance",
        "universe_size":   data.get("total_stocks", 0),
        "fetched_at":      data.get("updated_at"),
    })


@app.route("/api/us/price/<symbol>")
def api_us_price(symbol: str):
    """미국 종목 현재가.
    데이터 소스: 1) DB stocks(market='US') 우선 (소형주 포함)
                 2) us_market_*.json 캐시 폴백 (prev_close 등 부가 필드)."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    # 1) DB 우선
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                r = conn.execute(
                    "SELECT code, name, close, change_pct, volume_mn, updated_at "
                    "FROM stocks WHERE code = ? AND market = 'US'",
                    (symbol,)
                ).fetchone()
            if r and r["close"] is not None:
                return jsonify({
                    "code":       r["code"],
                    "name":       r["name"],
                    "price":      r["close"],
                    "prev_close": None,
                    "change":     None,
                    "change_pct": r["change_pct"],
                    "volume_mn":  r["volume_mn"],
                    "source":     "db",
                    "fetched_at": r["updated_at"],
                })
        except Exception as exc:
            log.debug("[us/price] DB: %s", exc)

    # 2) us_market 캐시 폴백
    data = _fetch_us_market_data()
    for s in data.get("all_stocks", []):
        if s["symbol"] == symbol:
            chg = (s["price"] - s["prev_close"]) if s.get("prev_close") else None
            return jsonify({
                "code":       symbol,
                "name":       s["name"],
                "price":      s["price"],
                "prev_close": s.get("prev_close"),
                "change":     round(chg, 2) if chg is not None else None,
                "change_pct": s["change_pct"],
                "volume_mn":  s["volume_mn"],
                "source":     "us_market_cache",
                "fetched_at": data.get("updated_at"),
            })
    return jsonify({"error": "종목 없음"}), 404


@app.route("/api/news/<code>")
def api_news(code: str):
    """
    종목 관련 뉴스 검색 (네이버 검색 API).
    캐시: cache/news_{code}.json, TTL 1시간.
    NAVER_CLIENT_ID/SECRET 미설정 시 503 + hint.
    """
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    stock_name = _get_stock_name(code)
    if not stock_name:
        return jsonify({"error": "종목 없음"}), 404

    cache_file = BASE_DIR / "cache" / f"news_{code}.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 60:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    client_id     = os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        return jsonify({
            "error": "네이버 검색 API 키 미설정",
            "hint":  ".env 또는 환경변수에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 추가",
        }), 503

    try:
        import requests as _rq
        res = _rq.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id":     client_id,
                "X-Naver-Client-Secret": client_secret,
            },
            params={
                "query":   f"{stock_name} 주가",
                "display": 15,
                "sort":    "date",
            },
            timeout=8,
        )
        res.raise_for_status()
        data = res.json()
    except Exception as exc:
        return jsonify({"error": f"네이버 API 호출 실패: {exc}"}), 502

    items_in = data.get("items", []) or []
    items = []
    for it in items_in:
        link = it.get("originallink") or it.get("link") or ""
        items.append({
            "title":       _strip_html(it.get("title", "")),
            "description": _strip_html(it.get("description", "")),
            "link":        link,
            "source":      _news_source(link),
            "pubDate":     it.get("pubDate", ""),
            "timeAgo":     _format_time_ago(it.get("pubDate", "")),
        })

    result = {
        "code":  code,
        "name":  stock_name,
        "count": len(items),
        "items": items,
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# 애널 리포트 마이닝 (네이버 금융 리서치 + PyPDF2)  —  Phase 10
# ─────────────────────────────────────────────────────────────────────────────
# KBJ P4 삭제: _crawl_naver_research — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


# KBJ P4 삭제: _extract_report_html — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


# 하위 호환: 기존 함수명을 호출하는 경로가 남아있을 수 있어 alias 유지
# KBJ P4 삭제: _extract_report_pdf — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


# KBJ P4 삭제: api_reports — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


@app.route("/api/screener")
def api_screener():
    """
    종목 스크리너 — data.json 테마 종목을 기본 소스로 하고,
    KRX Open API 가 구독되어 있으면 전 종목 시세로 보강(시총·등락률 등).
    API 호출은 일 1회만 발생 (캐시).
    """
    args = request.args
    market     = (args.get("market") or "ALL").upper()
    min_change = float(args.get("min_change", "-100") or -100)
    min_volume = float(args.get("min_volume", "0")    or 0)
    max_per    = float(args.get("max_per",    "9999") or 9999)
    max_pbr    = float(args.get("max_pbr",    "9999") or 9999)
    q          = (args.get("q") or "").strip()

    krx_stocks  = _get_krx_all_stocks_cached()   # code → KRX 레코드 (빈 dict면 미구독)
    naver_uni   = _load_naver_universe()         # Phase 10: Naver 전 종목 유니버스
    universe_src = bool(naver_uni.get("stocks"))

    today       = _get_trading_date()
    fund_file   = BASE_DIR / "cache" / f"fundamental_{today}.json"
    fundamentals: dict = {}
    if fund_file.exists():
        try:
            fundamentals = json.loads(fund_file.read_text(encoding="utf-8"))
        except Exception:
            fundamentals = {}

    # ─── 1순위 소스: Naver universe (2,500+ 종목) ───
    if universe_src:
        stocks_map = naver_uni["stocks"]   # {code: {code,name,change_pct,volume_mn,close,sectors}}
        results = []
        for code, s in stocks_map.items():
            name   = s.get("name") or code
            chg    = float(s.get("change_pct", 0.0))
            vol_mn = float(s.get("volume_mn",  0.0))    # Naver 는 이미 백만원 단위
            # 시장 정보는 Naver universe 에 없음 — 필터 "ALL" 아니면 패스
            if market != "ALL":
                continue
            if chg < min_change:                     continue
            if vol_mn < min_volume:                  continue
            # PER/PBR 은 Naver 에 없음 — fundamentals 캐시에 있으면 사용
            f = fundamentals.get(code, {})
            per = f.get("per")
            pbr = f.get("pbr")
            if per is not None and per > max_per:    continue
            if pbr is not None and pbr > max_pbr:    continue
            if q and q not in code and q not in (name or ''):
                continue
            results.append({
                "code": code, "name": name,
                "change_pct": round(chg, 2),
                "volume_mn":  int(vol_mn),
                "close":      s.get("close"),
                "per": per, "pbr": pbr,
                # 시총은 fundamentals 캐시가 1순위지만, 그 캐시는 Render 에
                # 영속 디스크가 없어 재시작마다 사라진다. 없다고 null 을
                # 내보내면 화면 시총이 통째로 빈다 — 실제로 그랬다.
                # 가격 sync 가 폴링(marketValueFullRaw)으로 받아 둔 값이
                # universe 에 있으니 그걸 2순위로 쓴다.
                "market_cap": f.get("market_cap") or s.get("market_cap"),
                "market_cap_asof": s.get("market_cap_asof"),
                "market":     "",
                "sectors":    s.get("sectors", []),
                "theme":      (s.get("sectors") or [None])[0],
            })

        results.sort(key=lambda r: r["volume_mn"], reverse=True)
        return jsonify({
            "count":             len(results),
            "stocks":            results[:200],
            "has_fundamentals":  bool(fundamentals),
            "krx_api_enriched":  bool(krx_stocks),
            "universe_source":   "naver_finance",
            "universe_size":     naver_uni.get("stock_count", 0),
            "fetched_at":        naver_uni.get("fetched_at"),
        })

    # ─── 2순위 폴백: data.json 테마 (기존 134종목) ───
    if not DATA_JSON.exists():
        return jsonify({"error": "data.json 미수집"}), 503
    try:
        data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "data.json 로드 실패"}), 500

    seen = set()
    results = []
    for theme in data.get("themes", []):
        for s in theme.get("stocks", []):
            code = s.get("code")
            if not code or code in seen:
                continue
            seen.add(code)
            name   = s.get("name", code)
            chg    = float(s.get("change_pct", 0.0))
            vol_mn = float(s.get("volume_mn",  0.0))

            # KRX 보강 (가능하면 KRX 수치 우선)
            krx_row = krx_stocks.get(code) or {}
            mkt     = ""
            market_cap = None
            if krx_row:
                krx_chg = _krx_get_float(krx_row, "FLUC_RT", "flucRt", "CHG_RT")
                if krx_chg is not None:
                    chg = krx_chg
                krx_vol = _krx_get_float(krx_row, "ACC_TRDVAL", "accTrdVal", "TRDVAL")
                if krx_vol is not None:
                    vol_mn = krx_vol / 1_000_000
                market_cap = _krx_get_float(krx_row, "MKTCAP", "mktCap")
                if market_cap is not None:
                    market_cap = market_cap / 1_000_000
                mkt = krx_row.get("MKT_NM") or krx_row.get("mktNm") or ""
                if "KOSPI" in mkt.upper() or "유가증권" in mkt:
                    mkt = "KOSPI"
                elif "KOSDAQ" in mkt.upper() or "코스닥" in mkt:
                    mkt = "KOSDAQ"

            f = fundamentals.get(code, {})
            per = f.get("per")
            pbr = f.get("pbr")
            if market_cap is None:
                market_cap = f.get("market_cap")
            if not mkt:
                mkt = f.get("market", "")

            if market != "ALL" and mkt and mkt != market: continue
            if chg < min_change:                          continue
            if vol_mn < min_volume:                       continue
            if per is not None and per > max_per:         continue
            if pbr is not None and pbr > max_pbr:         continue
            if q and q not in code and q not in name:     continue

            results.append({
                "code": code, "name": name,
                "change_pct": round(chg, 2),
                "volume_mn":  int(vol_mn),
                "per": per, "pbr": pbr,
                "market_cap": market_cap,
                "market": mkt,
                "theme": theme.get("name"),
            })

    results.sort(key=lambda r: r["volume_mn"], reverse=True)
    return jsonify({
        "count":             len(results),
        "stocks":            results[:200],
        "has_fundamentals":  bool(fundamentals),
        "krx_api_enriched":  bool(krx_stocks),
        "universe_source":   "themes_fallback",
        "universe_size":     len(results),
    })


def _parse_naver_flow_number(s: str) -> int:
    """
    네이버 frgn.naver 의 순매매량 셀 파싱.
    예: '+465,171' → 465171, '-13,418,579' → -13418579, '' → 0
    """
    if not s:
        return 0
    s = s.replace(",", "").replace(" ", "").strip()
    if not s or s in ("-", "—"):
        return 0
    try:
        return int(s)
    except ValueError:
        # 붙어있는 부호 제거 후 재시도
        s = s.lstrip("+")
        try:
            return int(s)
        except ValueError:
            return 0


def _note_collect_error(source: str, detail: str) -> None:
    """수집 실패를 링버퍼에 남긴다. /api/ops/diag/collect_errors 로 읽는다.

    Render 로그를 볼 수 없는 상황에서 '무엇이 언제부터 왜 안 들어오는지' 를
    밖에서 확인할 유일한 창구다. 2026-09-29 에 네이버 PC 페이지가 SPA 로
    바뀌어 스크레이핑이 전부 0행을 돌려줬는데, 서버는 조용히 False 만
    반환해서 어디가 끊겼는지 알 방법이 없었다.
    """
    try:
        _COLLECT_ERRORS.append({
            "at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "detail": _mask_secrets(str(detail))[:300],
        })
    except Exception:
        pass


def _fetch_naver_trend(code: str, days: int = 20) -> dict | None:
    """
    종목별 투자자 매매동향을 네이버 모바일 JSON API 에서 받는다.

    **왜 JSON 인가** — 2026-09-29 확인: finance.naver.com 의 PC 페이지
    (item/frgn.naver, sise/sise_group.naver)가 Next.js 클라이언트 렌더링으로
    바뀌었다. HTTP 200 에 136KB 를 주지만 `<table>` 이 한 개도 없다.
    BeautifulSoup 셀렉터(table.type2)가 맞을 대상 자체가 사라진 것이다.
    차단이 아니라 소스 형식 변경이라, IP 를 바꿔도 안 된다.

    응답: [{itemCode, bizdate, foreignerPureBuyQuant, organPureBuyQuant,
            individualPureBuyQuant, closePrice, foreignerHoldRatio, ...}]
    최신이 앞. 예전 스크레이퍼와 같게 오래된 → 최신 순으로 뒤집어 돌려준다.

    개인 순매수와 외국인 보유율은 예전 스크레이퍼에 없던 값이다. 같이 받아 둔다.
    """
    import re as _re
    import urllib.request
    if not _re.fullmatch(r"\d{6}", code):
        return None

    url = (f"https://m.stock.naver.com/api/stock/{code}/trend"
           f"?pageSize={days}&page=1")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _note_collect_error("naver_trend", f"{code}: {type(exc).__name__}: {exc}")
        return None

    if not isinstance(rows, list) or not rows:
        _note_collect_error("naver_trend", f"{code}: 빈 응답 또는 형식 변경 ({type(rows).__name__})")
        return None

    dates, closes, foreign_net, inst_net, indi_net = [], [], [], [], []
    hold_ratio = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        bd = str(r.get("bizdate") or "")
        if not _re.fullmatch(r"\d{8}", bd):
            continue
        close = _parse_naver_flow_number(str(r.get("closePrice") or ""))
        if close <= 0:
            continue
        dates.append(f"{bd[:4]}-{bd[4:6]}-{bd[6:]}")
        closes.append(close)
        foreign_net.append(_parse_naver_flow_number(str(r.get("foreignerPureBuyQuant") or "")))
        inst_net.append(_parse_naver_flow_number(str(r.get("organPureBuyQuant") or "")))
        indi_net.append(_parse_naver_flow_number(str(r.get("individualPureBuyQuant") or "")))
        if hold_ratio is None:
            hold_ratio = str(r.get("foreignerHoldRatio") or "") or None

    if not dates:
        _note_collect_error("naver_trend", f"{code}: 행 추출 0 — 응답 키가 바뀐 듯")
        return None

    # 네이버는 최신이 앞. 기존 소비자들이 '오래된 → 최신' 을 기대한다.
    dates.reverse(); closes.reverse()
    foreign_net.reverse(); inst_net.reverse(); indi_net.reverse()

    return {
        "dates": dates, "closes": closes,
        "foreign_net": foreign_net, "inst_net": inst_net,
        "individual_net": indi_net, "foreign_hold_ratio": hold_ratio,
    }


def _save_flow(code: str, stock_name: str, t: dict) -> dict:
    """_fetch_naver_trend 결과를 캐시 + flow_cache DB 에 쓰고 응답 dict 를 만든다."""
    dates, closes = t["dates"], t["closes"]
    foreign_net, inst_net = t["foreign_net"], t["inst_net"]
    # 순매수 '원' 금액 = 주식수 × 종가 (추정치 — 체결단가가 아니라 종가 기준)
    foreign_value = [f * c for f, c in zip(foreign_net, closes)]
    inst_value = [i * c for i, c in zip(inst_net, closes)]
    fetched_at = now_kst().strftime("%Y-%m-%d %H:%M:%S")

    result = {
        "code": code, "name": stock_name,
        "dates": dates, "close": closes,
        "foreign_shares": foreign_net, "inst_shares": inst_net,
        "individual_shares": t.get("individual_net") or [],
        "foreign_hold_ratio": t.get("foreign_hold_ratio"),
        "foreign_value": foreign_value, "inst_value": inst_value,
        "foreign_sum_20": sum(foreign_value),
        "inst_sum_20": sum(inst_value),
        "source": "naver_mobile_api", "fetched_at": fetched_at,
    }
    try:
        cache_file = BASE_DIR / "cache" / f"flow_{code}.json"
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:
        pass

    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO flow_cache "
                    "(code, name, dates_json, close_json, foreign_shares_json, "
                    "inst_shares_json, foreign_value_json, inst_value_json, "
                    "foreign_sum_20, inst_sum_20, source, fetched_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (code, stock_name,
                     json.dumps(dates, ensure_ascii=False),
                     json.dumps(closes, ensure_ascii=False),
                     json.dumps(foreign_net, ensure_ascii=False),
                     json.dumps(inst_net, ensure_ascii=False),
                     json.dumps(foreign_value, ensure_ascii=False),
                     json.dumps(inst_value, ensure_ascii=False),
                     sum(foreign_value), sum(inst_value),
                     "naver_mobile_api", fetched_at),
                )
                conn.commit()
        except Exception as exc:
            log.debug("[flow] %s DB fail: %s", code, exc)
    return result


def _fetch_and_save_flow(code: str) -> bool:
    """수급 fetch → cache + flow_cache DB 갱신. cron batch 용.
    Returns: True 성공 / False 실패."""
    t = _fetch_naver_trend(code)
    if not t:
        return False
    _save_flow(code, _get_stock_name(code) or code, t)
    return True


# KBJ P4 삭제: _fetch_and_save_flow_legacy_unused — P3 대체(쓰지 않던 옛 판 — 부르는 곳 없음)


def _refresh_flow_batch(top_n: int = 200) -> dict:
    """KR 시총 상위 top_n 종목의 외인/기관 수급 일괄 갱신.
    cron: 평일 15:40 (가격 sync 15:35 후, 시황 발송 15:50 전).
    소요: ~1.5분 (200개 × 0.4s rate limit).
    """
    if not (_SQLITE_OK and USE_SQLITE):
        return {"ok": False, "error": "SQLite 비활성"}
    t0 = time.time()
    # 대상 선정 — 시총 순이 원칙이다. 다만 **시총이 비어 있다고 수집을 통째로
    # 거르면 안 된다.** 예전엔 market_cap > 0 조건 하나로 0종목을 돌려주고
    # 수급이 통째로 안 모였다. Render 는 영속 디스크가 없어 재시작 직후
    # 시총이 비는 구간이 실제로 생긴다. 그때는 거래대금으로 대신 줄을 세운다.
    try:
        with _get_db() as conn:
            rows = conn.execute("""
                SELECT code FROM stocks
                WHERE (market='' OR market LIKE 'KOS%')
                  AND COALESCE(is_etf, 0) = 0
                  AND market_cap IS NOT NULL AND market_cap > 0
                  AND close >= 1000
                ORDER BY market_cap DESC LIMIT ?
            """, (top_n,)).fetchall()
            codes = [r["code"] for r in rows]
            if not codes:
                log.warning("[flow batch] 시총 기준 0종목 — 거래대금 순으로 대체한다 "
                            "(stocks.market_cap 이 비어 있다)")
                _note_collect_error(
                    "flow_batch",
                    "stocks.market_cap 이 전부 비어 시총 정렬이 0종목을 반환 — 거래대금 순으로 대체")
                rows = conn.execute("""
                    SELECT code FROM stocks
                    WHERE (market='' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0
                      AND close >= 1000
                      AND volume_mn IS NOT NULL
                    ORDER BY volume_mn DESC LIMIT ?
                """, (top_n,)).fetchall()
                codes = [r["code"] for r in rows]
    except Exception as exc:
        _note_collect_error("flow_batch", f"종목 조회 실패: {exc}")
        return {"ok": False, "error": f"종목 조회 실패: {exc}"}

    if not codes:
        # Render 는 영속 디스크가 없어 재시작 직후 stocks 가 비어 있다.
        # 첫 가격 sync 전에 배치가 돌면 여기에 걸린다 — 실제로 배포 직후
        # 'stocks 테이블이 비었다' 로 한 번 죽었다. 그때는 메모리에 이미
        # 올라와 있는 naver_universe 로 대상을 고른다.
        uni = _load_naver_universe()
        umap = (uni or {}).get("stocks") or {}
        if umap:
            ranked = sorted(
                (v for v in umap.values()
                 if str(v.get("code") or "").isdigit()
                 and float(v.get("close") or 0) >= 1000),
                key=lambda v: float(v.get("market_cap") or 0) or float(v.get("volume_mn") or 0),
                reverse=True)
            codes = [v["code"] for v in ranked[:top_n]]
            log.warning("[flow batch] stocks 비어 있음 — naver_universe 로 %d종목 선정", len(codes))
            _note_collect_error(
                "flow_batch",
                f"stocks 비어 있어 naver_universe 로 대체 선정 ({len(codes)}종목)")

    if not codes:
        _note_collect_error("flow_batch", "대상 종목 0개 — stocks 와 naver_universe 둘 다 비었다")
        return {"ok": False, "error": "대상 종목 0개"}

    ok = 0; fail = 0
    for i, code in enumerate(codes, 1):
        if _fetch_and_save_flow(code):
            ok += 1
        else:
            fail += 1
        time.sleep(0.4)  # rate limit (Naver 차단 회피)
    elapsed = time.time() - t0
    log.info("[flow batch] %d종목 갱신 (%d 성공, %d 실패, %.1fs)",
             len(codes), ok, fail, elapsed)
    # 전부 실패하면 소스가 바뀐 것이다. 조용히 넘어가지 않는다.
    if codes and ok == 0:
        _note_collect_error("flow_batch",
                            f"{len(codes)}종목 전부 실패 — 네이버 매매동향 API 응답 확인 필요")
    return {
        "ok": True, "total": len(codes),
        "success": ok, "failed": fail,
        "elapsed_sec": round(elapsed, 1),
    }


@app.route("/api/flow/refresh-batch", methods=["POST"])
def api_flow_refresh_batch():
    """수동 트리거 — 시총 상위 N개 수급 일괄 갱신.
    Query: ?top_n=200 (기본). 백그라운드 실행."""
    top_n = min(int(request.args.get("top_n", 200)), 500)
    def _bg():
        _refresh_flow_batch(top_n)
    threading.Thread(target=_bg, daemon=True, name="flow-batch").start()
    return jsonify({"ok": True, "message": f"백그라운드 갱신 시작 (top_n={top_n})"})


# KBJ P4 삭제: api_flow — P3 대체(페이지 1·2·4 — /api/market·/api/flows/stock/{code})



def _parse_pct(s: str) -> float:
    """'+7.94%', '-1.32%' → float"""
    if not s:
        return 0.0
    s = s.replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _naver_json(url: str, source: str, timeout: int = 10):
    """네이버 모바일 JSON API 공통 호출. 실패는 링버퍼에 남기고 None."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _note_collect_error(source, f"{type(exc).__name__}: {exc}")
        return None


def _scrape_naver_sectors() -> list[dict]:
    """
    79개 KRX 업종 요약.

    예전엔 finance.naver.com/sise/sise_group.naver 를 BeautifulSoup 으로
    긁었다. 2026-09-29 그 페이지가 Next.js SPA 로 바뀌어 `<table>` 이
    0개가 됐고(HTTP 200, 136KB, table 0), /api/sectors 가 502 를 냈다.
    모바일 JSON API 로 옮긴다 — 같은 필드를 그대로 준다.

    Returns: [{no, name, change_pct, total, up, flat, down}]
    """
    d = _naver_json(
        "https://m.stock.naver.com/api/stocks/industry?page=1&pageSize=100",
        "naver_sectors")
    if not isinstance(d, dict):
        return []
    groups = d.get("groups")
    if not isinstance(groups, list) or not groups:
        _note_collect_error("naver_sectors", f"groups 없음 — 응답 키 {list(d)[:8]}")
        return []

    out: list[dict] = []
    for g in groups:
        if not isinstance(g, dict) or g.get("no") is None:
            continue
        out.append({
            "no": str(g.get("no")),
            "name": g.get("name") or "",
            "change_pct": _parse_pct(str(g.get("changeRate") or "0")),
            "total": int(g.get("totalCount") or 0),
            "up": int(g.get("riseCount") or 0),
            "flat": int(g.get("steadyCount") or 0),
            "down": int(g.get("fallCount") or 0),
        })
    return out


# KBJ P4 삭제: _scrape_naver_sectors_legacy_unused — P3 대체(페이지 1·2·4 — /api/market·/api/flows/stock/{code})


def _scrape_naver_sector_detail(no: str) -> dict:
    """
    업종 소속 종목 리스트. 업종 요약과 같은 이유로 모바일 JSON API 를 쓴다.
    Returns: {sector_name, stocks: [{code, name, close, change_pct, volume, volume_mn}]}
    """
    d = _naver_json(
        f"https://m.stock.naver.com/api/stocks/industry/{no}?page=1&pageSize=100",
        "naver_sector_detail")
    if not isinstance(d, dict):
        return {"error": "업종 상세 조회 실패", "sector_name": "", "stocks": []}

    ginfo = d.get("groupInfo") or {}
    sector_name = ginfo.get("name") or ""
    rows = d.get("stocks")
    if not isinstance(rows, list):
        _note_collect_error("naver_sector_detail", f"no={no} stocks 없음 — 키 {list(d)[:8]}")
        return {"sector_name": sector_name, "stocks": []}

    stocks: list[dict] = []
    seen: set = set()
    for s in rows:
        if not isinstance(s, dict):
            continue
        code = str(s.get("itemCode") or "")
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            continue
        seen.add(code)
        close = _parse_naver_flow_number(str(s.get("closePrice") or ""))
        vol = _parse_naver_flow_number(str(s.get("accumulatedTradingVolume") or ""))
        stocks.append({
            "code": code,
            "name": s.get("stockName") or code,
            "close": close,
            "change_pct": _parse_pct(str(s.get("fluctuationsRatio") or "0")),
            "volume": vol,
            "volume_mn": int(vol * close / 1_000_000) if close and vol else 0,
        })
    return {"sector_name": sector_name, "stocks": stocks}


# KBJ P4 삭제: _scrape_naver_sector_detail_legacy_unused — P3 대체(페이지 1·2·4 — /api/market·/api/flows/stock/{code})


def _build_naver_universe_background():
    """
    모든 79개 업종 상세 페이지를 순차 스크랩해 전 종목 딕셔너리를 구축.
    결과는 cache/naver_universe_{date}.json 에 저장.
    스크리너가 구독된 KRX API 없이도 2,500+ 커버 가능.
    서버 부팅 시 백그라운드 스레드로 실행.
    """
    today = _get_trading_date()
    out_file = BASE_DIR / "cache" / f"naver_universe_{today}.json"
    if out_file.exists():
        log.info("naver_universe 캐시 이미 존재 — 스킵 (%s)", out_file.name)
        return
    lock_file = out_file.with_suffix(".lock")

    # ── Lock stale TTL (S-2-A): 600초 초과 시 stale 로 간주하고 재시도 ──
    # daemon thread 가 Render sleep/restart 등으로 죽고 lock 만 남는 케이스
    # 회복 보장. 5/19~6/1 12일간 stocks 갱신 0건의 근본 원인.
    if lock_file.exists():
        try:
            age = time.time() - lock_file.stat().st_mtime
        except Exception:
            age = 0
        if age < 600:
            log.info("[KR universe] 빌더 진행 중 스킵 (lock age=%ds)", int(age))
            return
        log.warning("[KR universe] stale lock 감지 (age=%ds, threshold=600s) "
                    "→ 삭제 후 재시도", int(age))
        try:
            lock_file.unlink()
        except Exception as exc:
            log.error("[KR universe] stale lock 삭제 실패: %s", exc)
            return

    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.touch()
    except Exception:
        pass

    try:
        log.info("▶  Naver 업종 유니버스 빌드 시작")
        sectors = _scrape_naver_sectors()
        if not sectors:
            log.warning("Naver 유니버스: 랜딩 스크랩 실패")
            return

        import time as _time
        universe: dict = {}
        stock_to_sector: dict = {}
        for i, sec in enumerate(sectors):
            no = sec["no"]
            try:
                detail = _scrape_naver_sector_detail(no)
            except Exception as exc:
                log.warning("섹터 %s 스크랩 예외: %s", no, exc)
                continue
            stocks = detail.get("stocks", []) if isinstance(detail, dict) else []
            for s in stocks:
                code = s["code"]
                # 최초 출현 섹터를 기본으로, 추가 섹터는 리스트에 누적
                if code not in universe:
                    universe[code] = {**s, "sectors": [sec["name"]]}
                    stock_to_sector[code] = sec["name"]
                else:
                    if sec["name"] not in universe[code]["sectors"]:
                        universe[code]["sectors"].append(sec["name"])
            _time.sleep(0.25)    # 네이버 rate limit 회피
            if (i + 1) % 10 == 0:
                log.info("  ... %d/%d 섹터 (누적 %d 종목)",
                         i + 1, len(sectors), len(universe))

        result = {
            "fetched_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "sector_count": len(sectors),
            "stock_count":  len(universe),
            "stocks":       universe,
        }
        out_file.parent.mkdir(exist_ok=True)
        out_file.write_text(json.dumps(result, ensure_ascii=False),
                            encoding="utf-8")
        log.info("✓  Naver 유니버스 빌드 완료: %d 종목 → %s",
                 len(universe), out_file.name)
    finally:
        try: lock_file.unlink()
        except Exception: pass


_UNI_CACHE: dict = {"data": None, "mtime": 0, "path": None}


def _startup_naver_universe_sync():
    """부팅 직후 naver_universe 동기 빌드 + 가격 갱신 (S-2-A).

    - 오늘자 본체 json 이 이미 있으면 즉시 skip (빠른 부팅)
    - 없으면 빌더 동기 호출 → 완료 후 _refresh_prices_from_naver() 까지 연쇄
    - daemon thread 가 죽어 본체 미완성 + lock 잔존 케이스에서도 다음 부팅 시
      lock TTL(600s) 가드 덕에 자동 회복.

    호출 컨텍스트: _startup() 안의 daemon thread 에서 실행 (Flask 시작 비차단).
    """
    today = _get_trading_date()
    out_file = BASE_DIR / "cache" / f"naver_universe_{today}.json"
    if out_file.exists():
        try:
            size = out_file.stat().st_size
        except Exception:
            size = 0
        log.info("[startup] naver_universe 오늘자 본체 존재 (%d bytes) → 빌드 skip",
                 size)
        return

    log.info("[startup] naver_universe 오늘자 본체 없음 → 동기 빌드 시작")
    try:
        _build_naver_universe_background()
    except Exception as exc:
        log.error("[startup] naver_universe 빌드 실패: %s", exc, exc_info=True)
        return

    if not out_file.exists():
        log.warning("[startup] naver_universe 빌드 후에도 본체 파일 없음 — "
                    "_scrape_naver_sectors 실패 가능성")
        return

    log.info("[startup] naver_universe 빌드 완료 → 가격 갱신 시작")
    try:
        n = _refresh_prices_from_naver()
        log.info("[startup] 가격 갱신 완료: %d종목", n or 0)
    except Exception as exc:
        log.error("[startup] 가격 갱신 실패: %s", exc, exc_info=True)


def _startup_ohlcv_fill():
    """부팅 직후 일봉(ohlcv) 채움. 별도 데몬 스레드에서 돈다.

    **고를 종목이 생긴 다음이라야 한다** — 대상을 `stocks` 의 시가총액으로
    고르는데(ETF 제외 1,000억 이상) 그 표가 아직 비었으면 0종목을 받고 끝난다.

    그래서 기다리는 조건이 '유니버스에 종목이 100개 넘게 있는가' 가 아니다.
    그 조건은 **재배포 직후 늘 즉시 참이다** — `_load_naver_universe()` 가
    커밋된 시드(4,063종목)로 떨어지기 때문이다. 그런데 시드에는 거래대금이
    없어서, 조건을 통과하자마자 0종목을 받고 끝났다. 조건이 재는 것과 다음
    단계가 필요로 하는 것이 달랐던 것이다.

    지금은 `_ohlcv_ranked_codes()` 로 **실제로 고를 수 있는 종목이 섰는지**를
    직접 본다(부팅 가격 갱신 `_boot_refresh_kr` 가 `stocks` 를 채우면 선다).

    기다림에 상한을 둔다. 못 차면 **그 사실을 로그로 남기고 물러난다** —
    빈 유니버스로 0종목을 받아 놓고 성공한 척하지 않는다. 다음 16:10 잡이
    다시 시도하고, 그때까지 시황은 신고가 섹션에 사유를 적어 내보낸다.
    """
    deadline = time.time() + 600                 # 최대 10분 기다린다
    while time.time() < deadline:
        try:
            basis, ranked = _ohlcv_ranked_codes()
            if len(ranked) >= 100:
                log.info("[startup] 일봉 대상 %d종목 확보 (%s 기준) — 채움 시작",
                         len(ranked), basis)
                break
        except Exception:                        # noqa: BLE001
            pass
        time.sleep(15)
    else:
        log.warning("[startup] 일봉 대상 종목이 10분 안에 서지 않아 건너뛴다 "
                    "— 16:10 잡이 다시 시도한다")
        return
    try:
        _fill_ohlcv_job()
    except Exception as exc:                     # noqa: BLE001
        log.error("[startup] 일봉 채움 실패: %s", exc, exc_info=True)


def _seed_cap_asof(uni: dict) -> str | None:
    """유니버스가 들고 있는 시가총액이 **언제 것인지** (YYYYMMDD).

    시드(data/naver_universe_seed.json)는 `source_date`, 폴링이 쓴 캐시는
    `fetched_at` 을 남긴다. 둘 다 없으면 None — 모르는 것을 오늘로 적지 않는다.
    None 이면 그 시총은 '언제 것인지 모름' 으로 다뤄져 시황에서 `*` 가 붙는다.
    """
    for key in ("source_date", "fetched_at"):
        raw = (uni or {}).get(key)
        if not raw:
            continue
        digits = re.sub(r"\D", "", str(raw))
        if len(digits) >= 8:
            try:
                datetime.strptime(digits[:8], "%Y%m%d")
                return digits[:8]
            except ValueError:
                continue
    return None


def _load_naver_universe() -> dict:
    """
    naver_universe 캐시 로드 (메모리 캐싱).
    파일 mtime 비교해서 변경됐을 때만 재로드. KST 오늘자 우선 → 거래일 → 최근 파일 fallback.
    """
    today_kst = now_kst().strftime("%Y%m%d")
    trading_date = _get_trading_date()

    # 후보 파일 결정: KST 오늘 > pykrx 거래일 > 가장 최근 파일
    target: Path | None = None
    for cand in (today_kst, trading_date):
        f = BASE_DIR / "cache" / f"naver_universe_{cand}.json"
        if f.exists():
            target = f
            break
    if target is None:
        try:
            import glob as _glob
            files = sorted(
                _glob.glob(str(BASE_DIR / "cache" / "naver_universe_*.json")),
                reverse=True,
            )
            if files:
                target = Path(files[0])
        except Exception:
            pass

    # 최종 폴백: git 커밋된 시드 (data/naver_universe_seed.json).
    # Render 무료플랜은 cache/ 영속 디스크가 없고 finance.naver.com HTML
    # 스크랩이 데이터센터 IP에서 실패 → 유니버스 공백 → stocks 영구 빈값.
    # 시드(종목코드+섹터)는 git 으로 항상 배포되므로, polling API(작동함)로
    # 가격만 덮어쓰면 stocks/flow/섹터 시황이 정상 복구된다.
    if target is None:
        seed = BASE_DIR / "data" / "naver_universe_seed.json"
        if seed.exists():
            log.info("[KR universe] 스크랩 캐시 없음 → 커밋된 시드 폴백 (%s)", seed.name)
            target = seed

    if target is None:
        return {}

    # mtime 비교 — 변경 없으면 메모리 캐시 반환
    try:
        mt = target.stat().st_mtime
    except Exception:
        return _UNI_CACHE["data"] or {}

    if _UNI_CACHE["path"] == str(target) and _UNI_CACHE["mtime"] == mt and _UNI_CACHE["data"]:
        return _UNI_CACHE["data"]

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        _UNI_CACHE.update({"data": data, "mtime": mt, "path": str(target)})
        return data
    except Exception:
        return _UNI_CACHE["data"] or {}


# KBJ P4 삭제: api_sectors — P3 대체(페이지 1·2·4 — /api/market·/api/flows/stock/{code})


# KBJ P4 삭제: api_sector_detail — P3 대체(페이지 1·2·4 — /api/market·/api/flows/stock/{code})


@app.route("/api/krx_status")
def api_krx_status():
    """KRX Open API 구독 상태 진단 — 각 엔드포인트에 실제 호출해서 성공 여부 리포트."""
    try:
        import krx_api
    except ImportError:
        return jsonify({"error": "krx_api 모듈 로드 실패"}), 500
    return jsonify({
        "has_api_key": krx_api.has_api_key(),
        "base_url":    krx_api.KRX_API_BASE,
        "probed_date": _get_trading_date(),
        "subscriptions": krx_api.probe_subscriptions(_get_trading_date()),
        "note": ("각 엔드포인트별로 openapi.krx.co.kr 마이페이지에서 별도 구독이 필요합니다. "
                 "ok=false 인 항목은 '활용 신청' 후 다시 호출하세요."),
    })


# KBJ P4 삭제: api_compare — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


# KBJ P4 삭제: api_financial — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


# KBJ P4 삭제: api_price — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


# KBJ P4 삭제: _fetch_naver_minute_candles — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


def _aggregate_minute_candles(raw: list[dict], interval: int) -> dict:
    """
    1분봉 raw 리스트를 N분봉으로 집계.
    interval: 1, 5, 15, 30, 60
    Returns: {dates, open, high, low, close, volume} (분봉 차트용)

    버킷팅: (HHMM 분 // interval) * interval 로 분 단위 바닥.
    날짜+버킷분 조합이 키. 같은 키 안에서 open=첫, high=max, low=min, close=마지막,
    volume=합.
    """
    if not raw or interval < 1:
        return {"dates": [], "open": [], "high": [], "low": [], "close": [], "volume": []}

    buckets: dict[str, dict] = {}
    order: list[str] = []
    for c in raw:
        dt = c.get("localDateTime", "")
        if len(dt) < 12:
            continue
        # YYYYMMDDHHMMSS → date YYYYMMDD, HH, MM
        date  = dt[:8]
        hour  = int(dt[8:10])
        minu  = int(dt[10:12])
        bucket_min = (hour * 60 + minu) // interval * interval
        bh = bucket_min // 60
        bm = bucket_min % 60
        key = f"{date}{bh:02d}{bm:02d}"

        try:
            o = float(c.get("openPrice", 0))
            h = float(c.get("highPrice", 0))
            l = float(c.get("lowPrice", 0))
            cl = float(c.get("currentPrice", 0) or c.get("closePrice", 0))
            v = float(c.get("accumulatedTradingVolume", 0) or 0)
        except (TypeError, ValueError):
            continue

        b = buckets.get(key)
        if b is None:
            buckets[key] = {"o": o, "h": h, "l": l, "c": cl, "v": v}
            order.append(key)
        else:
            if h > b["h"]: b["h"] = h
            if l < b["l"]: b["l"] = l
            b["c"] = cl     # raw 가 시간 순이라 마지막이 close
            b["v"] += v

    dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for key in order:
        b = buckets[key]
        # key = YYYYMMDDHHMM → 'MM-DD HH:MM' 표시
        label = f"{key[4:6]}-{key[6:8]} {key[8:10]}:{key[10:12]}"
        dates.append(label)
        opens.append(int(b["o"]))
        highs.append(int(b["h"]))
        lows.append(int(b["l"]))
        closes.append(int(b["c"]))
        volumes.append(int(b["v"]))

    return {"dates": dates, "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes}


# KBJ P4 삭제: api_chart_intraday — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


# KBJ P4 삭제: _append_today_candle_kr — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


# KBJ P4 삭제: api_chart — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


def _save_chart_to_sqlite(code, days, cache_date, result):
    """차트 결과를 SQLite에 동시 기록 (best-effort)."""
    if not (USE_SQLITE and _SQLITE_OK):
        return
    try:
        with _get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chart_cache "
                "(code, days, cache_date, name, dates_json, open_json, high_json, "
                "low_json, close_json, volume_json, bollinger_json, fibonacci_json, "
                "trendlines_json, analysis_json, rsi_macd_json, adx_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    code, days, cache_date, result.get("name"),
                    json.dumps(result.get("dates", []), ensure_ascii=False),
                    json.dumps(result.get("open", []), ensure_ascii=False),
                    json.dumps(result.get("high", []), ensure_ascii=False),
                    json.dumps(result.get("low", []), ensure_ascii=False),
                    json.dumps(result.get("close", []), ensure_ascii=False),
                    json.dumps(result.get("volume", []), ensure_ascii=False),
                    json.dumps(result.get("bollinger", {}), ensure_ascii=False),
                    json.dumps(result.get("fibonacci", {}), ensure_ascii=False),
                    json.dumps(result.get("trendlines", {}), ensure_ascii=False),
                    json.dumps(result.get("analysis", {}), ensure_ascii=False),
                    json.dumps(result.get("rsi_macd", {}), ensure_ascii=False),
                    json.dumps(result.get("adx", {}), ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception as exc:
        log.debug("[SQLite] chart write fail %s: %s", code, exc)


# 정적 파일 (themes_mapping.json, cache/ 등) 서빙
@app.route("/<path:filename>")
def static_file(filename: str):
    target = BASE_DIR / filename
    if not target.exists() or not target.is_file():
        return Response("Not Found", status=404)
    # cache/ 폴더 직접 접근은 보안상 차단
    if filename.startswith("cache/") or filename.startswith("cache\\"):
        return Response("Forbidden", status=403)
    return send_file(target)


# ─────────────────────────────────────────────────────────────────────────────
# 시작 루틴 (gunicorn import / 직접 실행 양쪽 모두에서 호출)
# ─────────────────────────────────────────────────────────────────────────────
_startup_done = False
_start_time = time.time()
_last_scheduler_check = 0.0


def _self_keep_alive():
    """Render 슬립 방지 — 자기 자신의 /api/health 호출. 외부 핑(cron-job.org/UptimeRobot)이
    1차 방어, 이건 2차 방어. 슬립 후 재시작되면 외부 핑이 깨우고 이 잡이 유지."""
    import urllib.request as _ur
    base = (os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if not base:
        # 로컬에서는 자체 핑 불필요
        return
    try:
        req = _ur.Request(f"{base}/api/health",
                          headers={"User-Agent": "self-keepalive"})
        with _ur.urlopen(req, timeout=15) as r:
            if r.status == 200:
                log.debug("[Keep-Alive] self-ping OK")
    except Exception as exc:
        log.debug("[Keep-Alive] self-ping fail: %s", exc)


@app.route("/api/health")
def api_health():
    """헬스체크 — 외부 cron(cron-job.org/UptimeRobot)이 5분 간격 호출 권장."""
    sched_running = False
    job_count = 0
    try:
        if _scheduler is not None:
            sched_running = bool(_scheduler.running)
            job_count = len(_scheduler.get_jobs())
    except Exception:
        pass
    return jsonify({
        "status": "ok",
        "uptime_sec": round(time.time() - _start_time),
        "uptime_hours": round((time.time() - _start_time) / 3600, 2),
        "timestamp": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "scheduler_running": sched_running,
        "scheduler_jobs": job_count,
        # **지금 도는 코드가 어느 커밋인지.** 이게 없어서 배포 완료를 매번
        # 다른 방법으로 짐작해야 했다 — 새 엔드포인트의 404→200 이나
        # 특정 값의 변화 같은 것들. 그런 감지는 '이미 있던 경로를 고친 배포'
        # 에서는 통하지 않는다. Render 가 RENDER_GIT_COMMIT 을 넣어 준다.
        "git_commit": (os.environ.get("RENDER_GIT_COMMIT")
                       or os.environ.get("GIT_COMMIT") or "")[:40] or None,
        "git_branch": os.environ.get("RENDER_GIT_BRANCH") or None,
    })


@app.route("/api/db/backup", methods=["POST", "GET"])
def api_db_backup():
    """수동 DB 백업 (Gist). GET·POST 모두 허용."""
    try:
        from db_backup import backup_db as _bk
        return jsonify(_bk())
    except Exception as exc:
        return jsonify({"ok": False, "reason": str(exc)}), 500


@app.route("/api/db/restore", methods=["POST"])
def api_db_restore():
    """수동 DB 복원 (Gist 가장 최신). POST만."""
    try:
        from db_backup import restore_db as _rs
        return jsonify(_rs())
    except Exception as exc:
        return jsonify({"ok": False, "reason": str(exc)}), 500


# KBJ P2 삭제(설계 §5.10): api_test_telegram_get — #19 인증 없는 텔레그램 시험 발송 라우트


@app.before_request
def _check_scheduler_health():
    """요청 들어올 때마다 스케줄러 상태 점검. 10분 간격으로만 체크 (오버헤드 최소화)."""
    global _last_scheduler_check
    try:
        now = time.time()
        if now - _last_scheduler_check < 600:
            return
        _last_scheduler_check = now
        if _scheduler is None:
            return
        if not _scheduler.running:
            log.warning("[Scheduler] 멈춤 감지 — 재시작 시도")
            try:
                _scheduler.start()
                log.info("[Scheduler] 재시작 성공")
            except Exception as exc:
                log.warning("[Scheduler] 재시작 실패: %s", exc)
        # 잡 개수 확인 (정상이면 20+개)
        try:
            n = len(_scheduler.get_jobs())
            if n < 5:
                log.warning("[Scheduler] 잡 부족 (%d개) — _startup 재호출 필요할 수 있음", n)
        except Exception:
            pass
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────
# PHASE 24 Step 2 — 국내 야간선물 + 옵션 PCR (best-effort)
# ─────────────────────────────────────────────────────────────────────────
def _fetch_night_futures() -> dict:
    """
    코스피200 야간선물 종가 조회. 3-tier fallback:
      1) yfinance ^KS200 (코스피200 현물 지수, 가장 신뢰도 높음)
      2) esignal.co.kr 스크랩 (페이지 구조 변경 시 실패)
      3) investing.com 스크랩 (봇 차단 가능성)
    전부 실패 시 structured empty state.
    """
    result: dict = {
        "day_close":   None,
        "night_close": None,
        "change":      None,
        "change_pct":  None,
        "signal":      None,
        "source":      None,
        "attempted":   [],
        "updated_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Tier 1: yfinance ^KS200 — 최근 2일 종가 비교로 "야간선물 프록시"
    try:
        import yfinance as _yf
        h = _yf.Ticker("^KS200").history(period="5d")
        if h is not None and not h.empty and len(h) >= 2:
            closes = [float(v) for v in h["Close"].tolist() if v == v]
            if len(closes) >= 2:
                result["day_close"]   = round(closes[-2], 2)
                result["night_close"] = round(closes[-1], 2)
                result["source"] = "yfinance:^KS200"
                result["attempted"].append({"source": "yfinance ^KS200", "ok": True})
    except Exception as exc:
        result["attempted"].append({
            "source": "yfinance ^KS200", "ok": False, "error": str(exc)[:80]
        })

    # Tier 1b: pykrx 로 day_close 보강 (night_close 가 다른 소스에서 확보된 경우)
    # 코스피200 지수 (1028) 최근 영업일 종가 조회
    if result["day_close"] is None:
        try:
            from pykrx import stock as _pykrx_stock
            from datetime import timedelta as _td
            # 최근 7일 내에서 데이터가 있는 영업일 찾기
            today_dt = now_kst().date()
            day_close_val = None
            for back in range(1, 8):
                d_str = (today_dt - _td(days=back)).strftime("%Y%m%d")
                if (today_dt - _td(days=back)).weekday() >= 5:
                    continue
                try:
                    df = _pykrx_call(_pykrx_stock.get_index_ohlcv_by_date, d_str, d_str, "1028", timeout=10)
                    if df is not None and not df.empty and "종가" in df.columns:
                        val = float(df["종가"].iloc[-1])
                        if val > 0:
                            day_close_val = val
                            break
                except Exception:
                    continue
            if day_close_val is not None:
                result["day_close"] = round(day_close_val, 2)
                result["attempted"].append({
                    "source": "pykrx 코스피200(1028)", "ok": True
                })
            else:
                result["attempted"].append({
                    "source": "pykrx 코스피200(1028)", "ok": False,
                    "error": "최근 7일간 OHLCV 없음"
                })
        except ImportError:
            result["attempted"].append({
                "source": "pykrx 코스피200(1028)", "ok": False, "error": "pykrx 미설치"
            })
        except Exception as exc:
            result["attempted"].append({
                "source": "pykrx 코스피200(1028)", "ok": False, "error": str(exc)[:80]
            })

    # Tier 2: esignal.co.kr
    if result["night_close"] is None:
        try:
            import requests as _rq
            from bs4 import BeautifulSoup
            r = _rq.get(
                "https://esignal.co.kr/kospi200-futures-night/",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                price_tag = (soup.select_one(".current_price")
                             or soup.select_one(".price")
                             or soup.select_one("[class*='price']"))
                if price_tag:
                    txt = price_tag.get_text(strip=True).replace(",", "")
                    try:
                        result["night_close"] = float(txt)
                        result["source"] = "esignal.co.kr"
                        result["attempted"].append({"source": "esignal.co.kr", "ok": True})
                    except ValueError:
                        result["attempted"].append({
                            "source": "esignal.co.kr", "ok": False,
                            "error": f"파싱 실패: '{txt[:30]}'"
                        })
                else:
                    result["attempted"].append({
                        "source": "esignal.co.kr", "ok": False,
                        "error": "price 셀렉터 매칭 실패"
                    })
            else:
                result["attempted"].append({
                    "source": "esignal.co.kr", "ok": False,
                    "error": f"HTTP {r.status_code}"
                })
        except Exception as exc:
            result["attempted"].append({
                "source": "esignal.co.kr", "ok": False, "error": str(exc)[:80]
            })

    # Tier 3: investing.com
    if result["night_close"] is None:
        try:
            import requests as _rq
            from bs4 import BeautifulSoup
            r = _rq.get(
                "https://kr.investing.com/indices/korea-200-futures",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 Chrome/120"},
                timeout=8,
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                pt = (soup.select_one('[data-test="instrument-price-last"]')
                      or soup.select_one(".text-5xl"))
                if pt:
                    txt = pt.get_text(strip=True).replace(",", "")
                    try:
                        result["night_close"] = float(txt)
                        result["source"] = "investing.com"
                        result["attempted"].append({"source": "investing.com", "ok": True})
                    except ValueError:
                        result["attempted"].append({
                            "source": "investing.com", "ok": False,
                            "error": f"파싱 실패: '{txt[:30]}'"
                        })
            else:
                result["attempted"].append({
                    "source": "investing.com", "ok": False,
                    "error": f"HTTP {r.status_code}"
                })
        except Exception as exc:
            result["attempted"].append({
                "source": "investing.com", "ok": False, "error": str(exc)[:80]
            })

    # 등락률 계산 (day_close 있을 때만)
    if result["night_close"] and result["day_close"]:
        result["change"] = round(result["night_close"] - result["day_close"], 2)
        if result["day_close"]:
            result["change_pct"] = round(
                (result["night_close"] / result["day_close"] - 1) * 100, 2
            )

    # 시그널 매핑
    pct = result.get("change_pct")
    if pct is not None:
        if   pct >  1.5: result["signal"] = "야간선물 강세 → 익일 갭업 출발 예상"
        elif pct >  0.5: result["signal"] = "야간선물 소폭 강세 → 소폭 상승 출발"
        elif pct > -0.5: result["signal"] = "야간선물 보합 → 횡보 출발"
        elif pct > -1.5: result["signal"] = "야간선물 소폭 약세 → 소폭 하락 출발"
        else:            result["signal"] = "야간선물 약세 → 갭다운 출발 예상"
    elif result["night_close"] is not None:
        result["signal"] = f"현재가 {result['night_close']} (전일 종가 불명 — 등락률 계산 불가)"
    else:
        result["signal"] = ("모든 소스 차단 — yfinance·esignal·investing 순서로 시도 실패. "
                            "한투/키움 API 연동 시 활성화")
    return result


@app.route("/api/night_futures")
def api_night_futures():
    """코스피200 야간선물 — 30분 캐시."""
    cache_file = BASE_DIR / "cache" / "night_futures.json"
    cached = _read_fresh_json(cache_file, 30)
    if cached:
        return jsonify(cached)
    result = _fetch_night_futures()
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/kr_options")
def api_kr_options():
    """
    코스피200 옵션 PCR — KRX data.krx.co.kr 차단 상태라 현재 전부 unavailable.
    엔드포인트는 유지해서 소스 복구 시 즉시 활성화. 6시간 캐시.
    """
    cache_file = BASE_DIR / "cache" / "kr_options_pcr.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    result = {
        "pcr_volume":     None,
        "pcr_oi":         None,
        "signal":         ("KRX data.krx.co.kr 가 외부 접근을 차단한 상태이며 "
                           "공매도와 동일하게 옵션 PCR 도 현재 무료 소스가 없습니다. "
                           "한투 OpenAPI / KIS 증권사 API 연동 시 활성화 가능."),
        "source":         "unavailable",
        "attempted":      [
            {"source": "pykrx option PCR", "ok": False, "error": "KRX blocked"},
            {"source": "data.krx.co.kr getJsonData", "ok": False, "error": "HTTP 400 blocked"},
            {"source": "investing.com kospi200 options", "ok": False, "error": "봇 차단"},
        ],
        "updated_at":     now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 24 — 미국 옵션 시그널 (SPY/QQQ): PCR + MaxPain + GEX 근사
# ─────────────────────────────────────────────────────────────────────────
def _nan_int(v) -> int:
    """NaN/None/빈값을 0으로 변환."""
    try:
        if v is None:
            return 0
        f = float(v)
        if f != f:   # NaN check
            return 0
        return int(f)
    except (TypeError, ValueError):
        return 0


def _nan_float(v) -> float:
    try:
        if v is None:
            return 0.0
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def _compute_options_signal(symbol: str) -> dict:
    """
    SPY/QQQ yfinance 옵션 체인에서 PCR/MaxPain/GEX 근사 계산.
    - 가장 가까운 2개 만기 수집
    - 맥스페인: ATM ±20% 행사가 범위에서 총 옵션 가치 최소 지점
    - GEX 근사: (C_OI − P_OI) × (1 - moneyness*10) × 100 × spot
    """
    try:
        import yfinance as _yf
    except ImportError:
        return {"error": "yfinance 미설치"}

    try:
        t = _yf.Ticker(symbol)
        # 미국 장 마감 후 period='1d' 가 빈 DF 가 되는 경우가 있어 5d 로 폴백
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return {"error": "현재가 조회 실패 (5d 기간에 데이터 없음)"}
        # NaN 제거 후 마지막 종가
        closes = [c for c in hist["Close"].tolist() if c == c]
        if not closes:
            return {"error": "현재가 NaN"}
        spot = _nan_float(closes[-1])

        exps = list(t.options or [])
        if not exps:
            return {"error": "옵션 만기 없음"}
        target_exps = exps[:2]

        all_calls: list[dict] = []
        all_puts:  list[dict] = []
        for exp in target_exps:
            try:
                chain = t.option_chain(exp)
            except Exception as exc:
                log.debug("chain %s %s fail: %s", symbol, exp, exc)
                continue
            for _, r in chain.calls.iterrows():
                all_calls.append({
                    "expiry":       exp,
                    "strike":       _nan_float(r.get("strike")),
                    "volume":       _nan_int(r.get("volume")),
                    "openInterest": _nan_int(r.get("openInterest")),
                    "iv":           _nan_float(r.get("impliedVolatility")),
                })
            for _, r in chain.puts.iterrows():
                all_puts.append({
                    "expiry":       exp,
                    "strike":       _nan_float(r.get("strike")),
                    "volume":       _nan_int(r.get("volume")),
                    "openInterest": _nan_int(r.get("openInterest")),
                    "iv":           _nan_float(r.get("impliedVolatility")),
                })

        if not all_calls or not all_puts:
            return {"error": "옵션 체인 비어 있음"}

        # ── PCR ──
        tot_pv = sum(p["volume"] for p in all_puts)
        tot_cv = sum(c["volume"] for c in all_calls)
        tot_pi = sum(p["openInterest"] for p in all_puts)
        tot_ci = sum(c["openInterest"] for c in all_calls)
        pcr_vol = round(tot_pv / tot_cv, 3) if tot_cv > 0 else None
        pcr_oi  = round(tot_pi / tot_ci, 3) if tot_ci > 0 else None

        if pcr_vol is None:
            pcr_signal = "거래량 부족"
        elif pcr_vol > 1.2:
            pcr_signal = "극도의 공포 → 역발상 매수 시그널"
        elif pcr_vol > 1.0:
            pcr_signal = "약한 공포 → 주의"
        elif pcr_vol > 0.7:
            pcr_signal = "중립"
        elif pcr_vol > 0.5:
            pcr_signal = "낙관 → 과매수 주의"
        else:
            pcr_signal = "극도의 낙관 → 역발상 매도 시그널"

        # ── 맥스페인 (가장 가까운 만기만) ──
        near_calls = [c for c in all_calls if c["expiry"] == target_exps[0]]
        near_puts  = [p for p in all_puts  if p["expiry"] == target_exps[0]]

        strikes_set = sorted(set(
            [c["strike"] for c in near_calls] + [p["strike"] for p in near_puts]
        ))
        # ATM ±20% 범위로 클램프
        strikes = [s for s in strikes_set if spot * 0.8 <= s <= spot * 1.2]
        if not strikes:
            strikes = strikes_set[:30]

        # ── OI stale fallback (S-2-B 후속) ──
        # Yahoo Finance는 openInterest 응답이 종종 0/지연 (장후 D+1 업데이트 패턴).
        # 5/20 이후 SPY/QQQ MaxPain 이 비현실적 strike (-19%) 로 폴백되는 원인.
        # OI 합이 임계치 미만이면 volume 을 proxy 로 사용 (MaxPain/GEX 만, PCR 은
        # volume 기반이라 영향 X).
        _near_oi_sum = (sum(c["openInterest"] for c in near_calls)
                        + sum(p["openInterest"] for p in near_puts))
        _use_volume_proxy = _near_oi_sum < 1000
        if _use_volume_proxy:
            log.warning("[옵션] %s OI stale (near sum=%d) → volume proxy (MaxPain/GEX)",
                        symbol, _near_oi_sum)
            call_oi_map = {c["strike"]: max(c["volume"] or 0, 0) for c in near_calls}
            put_oi_map  = {p["strike"]: max(p["volume"] or 0, 0) for p in near_puts}
        else:
            call_oi_map = {c["strike"]: c["openInterest"] for c in near_calls}
            put_oi_map  = {p["strike"]: p["openInterest"] for p in near_puts}

        pain_by_strike: list[dict] = []
        for test in strikes:
            total = 0.0
            for k in strikes:
                c_oi = call_oi_map.get(k, 0)
                p_oi = put_oi_map.get(k, 0)
                total += max(test - k, 0) * c_oi * 100
                total += max(k - test, 0) * p_oi * 100
            pain_by_strike.append({"strike": test, "total_pain": int(total)})

        if pain_by_strike:
            min_item = min(pain_by_strike, key=lambda x: x["total_pain"])
            max_pain_strike = min_item["strike"]
            mp_diff = round((max_pain_strike / spot - 1) * 100, 2) if spot else 0
            if mp_diff > 1:
                mp_signal = f"현재가 < 맥스페인 → 상방 수렴 압력 (+{mp_diff}%)"
            elif mp_diff < -1:
                mp_signal = f"현재가 > 맥스페인 → 하방 수렴 압력 ({mp_diff}%)"
            else:
                mp_signal = f"맥스페인 근접 → 횡보/레인지 예상 ({mp_diff}%)"
        else:
            max_pain_strike = None
            mp_diff = 0
            mp_signal = "계산 불가"

        # ── GEX 근사 ──
        gex_by_strike: list[dict] = []
        total_gex = 0.0
        for k in strikes:
            c_oi = call_oi_map.get(k, 0)
            p_oi = put_oi_map.get(k, 0)
            moneyness = abs(k - spot) / spot if spot else 1.0
            approx_gamma = max(0.0, 1.0 - moneyness * 10) * 0.01
            strike_gex = (c_oi - p_oi) * approx_gamma * 100 * spot
            gex_by_strike.append({
                "strike": k,
                "call_oi": c_oi,
                "put_oi":  p_oi,
                "net_gex": int(round(strike_gex)),
            })
            total_gex += strike_gex

        if total_gex > 0:
            gex_signal = "양수 GEX → 마켓메이커 롱감마 · 변동성 축소, 레인지바운드 예상"
            gex_regime = "positive"
        else:
            gex_signal = "음수 GEX → 마켓메이커 숏감마 · 변동성 확대, 추세 지속 예상"
            gex_regime = "negative"

        # call_wall / put_wall 도 OI stale 시 volume proxy 적용
        _wall_key = (lambda x: x["volume"] or 0) if _use_volume_proxy \
                    else (lambda x: x["openInterest"])
        call_wall = max(near_calls, key=_wall_key) if near_calls else None
        put_wall  = max(near_puts,  key=_wall_key) if near_puts  else None

        # ── 종합 판단 ──
        bullish = 0
        bearish = 0
        reasons = []
        if pcr_vol is not None:
            if pcr_vol > 1.0:
                bullish += 2
                reasons.append(f"PCR {pcr_vol} (공포 → 역발상 매수)")
            elif pcr_vol < 0.7:
                bearish += 2
                reasons.append(f"PCR {pcr_vol} (낙관 → 과매수 경고)")
            else:
                reasons.append(f"PCR {pcr_vol} (중립)")
        if mp_diff > 1:
            bullish += 1
            reasons.append(f"맥스페인 ${max_pain_strike} → 상방 수렴")
        elif mp_diff < -1:
            bearish += 1
            reasons.append(f"맥스페인 ${max_pain_strike} → 하방 수렴")
        if gex_regime == "positive":
            reasons.append("양수 GEX → 안정적 레인지")
        else:
            reasons.append("음수 GEX → 변동성 확대 주의")
            bearish += 1

        if bullish > bearish:
            overall = "강세"; emoji = "🟢"
        elif bearish > bullish:
            overall = "약세"; emoji = "🔴"
        else:
            overall = "중립"; emoji = "🟡"

        return {
            "symbol":     symbol,
            "spot_price": round(spot, 2),
            "expiry":     target_exps[0],
            "expiry_count": len(target_exps),
            "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "pcr": {
                "volume":         pcr_vol,
                "open_interest":  pcr_oi,
                "total_put_vol":  tot_pv,
                "total_call_vol": tot_cv,
                "total_put_oi":   tot_pi,
                "total_call_oi":  tot_ci,
                "signal":         pcr_signal,
            },
            "max_pain": {
                "strike":   max_pain_strike,
                "diff_pct": mp_diff,
                "signal":   mp_signal,
                "pain_by_strike": sorted(pain_by_strike, key=lambda x: x["strike"])[:60],
            },
            "gex": {
                "total":     int(round(total_gex)),
                "regime":    gex_regime,
                "signal":    gex_signal,
                "by_strike": gex_by_strike,
                "call_wall": {"strike": call_wall["strike"], "oi": call_wall["openInterest"]} if call_wall else None,
                "put_wall":  {"strike": put_wall["strike"],  "oi": put_wall["openInterest"]}  if put_wall  else None,
            },
            "overall": {
                "direction":      overall,
                "emoji":          emoji,
                "bullish_score":  bullish,
                "bearish_score":  bearish,
                "reasons":        reasons,
            },
        }
    except Exception as exc:
        log.debug("options_signal %s fail: %s", symbol, exc)
        return {"error": str(exc)}


@app.route("/api/options_signal")
def api_options_signal():
    symbol = (request.args.get("symbol") or "SPY").upper()
    if symbol not in ("SPY", "QQQ", "IWM", "DIA"):
        return jsonify({"error": "지원 심볼: SPY/QQQ/IWM/DIA"}), 400
    cache_file = BASE_DIR / "cache" / f"options_signal_{symbol}.json"
    cached = _read_fresh_json(cache_file, 30)
    if cached:
        return jsonify(cached)
    result = _compute_options_signal(symbol)
    if "error" not in result:
        try:
            cache_file.parent.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 23 — 텔레그램 알림 / ETF 히트맵 / 배당 스크리너
# ─────────────────────────────────────────────────────────────────────────
def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """텔레그램 메시지 전송 — KBJ notifier 대기열에 넣는다(설계 §5.9). 발송 꺼짐·실패면 False.

    토큰·chat id 는 여기서 읽지 않는다(notifier 만). 종류(kind)는 부른 함수 이름으로
    config/notify.yaml `legacy_kinds` 가 정한다.
    """
    return legacy_send(message, source="sd.send_telegram", parse_mode=parse_mode)[0]


# 텔레그램 sendMessage 본문 상한. 넘기면 400 이 나거나 잘린다.
_TG_LIMIT = 4096
# 실제로 한 조각에 담는 양. "(1/3)" 같은 조각 표시와 멀티바이트 여유를 뺀 값이다.
_TG_CHUNK = 3900


def _split_telegram_lines(text: str, limit: int = _TG_CHUNK) -> list[str]:
    """본문을 **줄 경계에서만** 잘라 여러 조각으로 나눈다.

    종목 한 줄이 문장 중간에서 끊기면 그 종목은 이름도 수치도 못 읽는 쓰레기가
    된다. 그래서 줄은 절대 쪼개지 않는다 — 한 줄이 통째로 한도를 넘는 병적인
    경우에만 어쩔 수 없이 공백에서 자르고, 그마저 없으면 글자 수로 자른다.

    이 저장소가 여태 쓰던 `msg[:3990] + '…(생략)'` 은 두 가지가 잘못이었다.
    (1) 뒤쪽 종목이 통째로 사라지는데 몇 종목이 사라졌는지 안 적었고,
    (2) 자르는 위치가 줄 한가운데라 마지막 줄이 깨진 채 나갔다. HTML 파스
    모드에서는 `<b>` 가 열린 채 잘리면 텔레그램이 400 으로 거절하기까지 한다.
    줄 경계로 나누면 이 빌더가 만드는 태그는 줄 안에서 열고 닫히므로 항상
    짝이 맞는다.
    """
    out: list[str] = []
    cur = ""
    for line in (text or "").split("\n"):
        while len(line) > limit:            # 한 줄이 통째로 한도를 넘는 경우
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:cut])
            line = line[cut:].lstrip()
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += "\n" + line
        else:
            out.append(cur)
            cur = line
    if cur:
        out.append(cur)
    return [c for c in out if c.strip()] or [""]


def send_telegram_long(message: str, parse_mode: str = "HTML") -> bool:
    """긴 본문을 여러 건으로 — 줄 경계 분할과 `(i/n)` 머리는 KBJ notifier(format)가 한다.

    `_split_telegram_lines` 는 검사 스크립트(check_newhigh_full_list.py)가 떼어 가 시험하므로
    남겨 둔다(KBJ P2 기준선 — 메시지를 kbj 작업으로 옮길 때 함께 지운다).
    """
    return legacy_send(message, source="sd.send_telegram_long", parse_mode=parse_mode,
                       numbered=True)[0]


@app.route("/api/volume_profile/<code>")
def api_volume_profile(code: str):
    """거래량 프로파일 (POC + Value Area) + VWAP + 저항/지지선. 4시간 캐시."""
    market = (request.args.get("market") or "kr").lower()
    cache_file = BASE_DIR / "cache" / f"vp_{market}_{code}.json"
    cached = _read_fresh_json(cache_file, 240)
    if cached:
        return jsonify(cached)

    # 차트 데이터 로드 (in-process)
    chart_url = f"/api/us/chart/{code}" if market == "us" else f"/api/chart/{code}"
    chart = _call_api_internal(chart_url) or {}
    if chart.get("error") or not chart.get("close"):
        return jsonify({"error": chart.get("error") or "차트 데이터 없음"}), 404

    highs  = chart.get("high")  or []
    lows   = chart.get("low")   or []
    closes = chart.get("close") or []
    volumes = chart.get("volume") or []
    n = len(closes)
    if n < 20:
        return jsonify({"error": "데이터 부족"}), 404

    # 거래량 프로파일 (30 bins)
    price_max = max(highs)
    price_min = min(lows)
    num_bins = 30
    bin_size = (price_max - price_min) / num_bins if price_max > price_min else 1
    bins: list[dict] = []
    for bi in range(num_bins):
        bl = price_min + bi * bin_size
        bh = bl + bin_size
        vol_in = 0.0
        for i in range(n):
            if lows[i] <= bh and highs[i] >= bl:
                overlap = min(highs[i], bh) - max(lows[i], bl)
                bar_range = highs[i] - lows[i]
                if bar_range > 0:
                    vol_in += volumes[i] * (overlap / bar_range)
        bins.append({
            "price_mid": round((bl + bh) / 2, 2),
            "volume":    int(round(vol_in)),
        })

    # POC
    poc_bin = max(bins, key=lambda b: b["volume"])
    poc = poc_bin["price_mid"]

    # Value Area (70%)
    sorted_bins = sorted(bins, key=lambda b: b["volume"], reverse=True)
    total_vol = sum(b["volume"] for b in bins)
    target = total_vol * 0.7
    acc = 0
    va_prices = []
    for b in sorted_bins:
        acc += b["volume"]
        va_prices.append(b["price_mid"])
        if acc >= target:
            break
    va_high = max(va_prices) + bin_size / 2 if va_prices else price_max
    va_low  = min(va_prices) - bin_size / 2 if va_prices else price_min

    # VWAP (최근 60봉)
    vwap_list = []
    cum_tpv = 0.0
    cum_vol = 0.0
    start = max(0, n - 60)
    for i in range(start, n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_tpv += tp * volumes[i]
        cum_vol += volumes[i]
        vwap_list.append(round(cum_tpv / cum_vol, 2) if cum_vol else 0)

    # 저항/지지 (클러스터링)
    extremes = sorted(highs[-60:] + lows[-60:])
    clusters: list[dict] = []
    if extremes:
        cur_cluster = [extremes[0]]
        thresh = 0.02
        for p in extremes[1:]:
            if (p - cur_cluster[-1]) / cur_cluster[-1] < thresh:
                cur_cluster.append(p)
            else:
                if len(cur_cluster) >= 3:
                    clusters.append({
                        "price": round(sum(cur_cluster) / len(cur_cluster), 2),
                        "touches": len(cur_cluster),
                    })
                cur_cluster = [p]
        if len(cur_cluster) >= 3:
            clusters.append({
                "price": round(sum(cur_cluster) / len(cur_cluster), 2),
                "touches": len(cur_cluster),
            })

    cur_price = closes[-1]
    resistance = sorted(
        [c for c in clusters if c["price"] > cur_price],
        key=lambda c: c["price"],
    )[:3]
    support = sorted(
        [c for c in clusters if c["price"] < cur_price],
        key=lambda c: -c["price"],
    )[:3]

    result = {
        "code":       code,
        "market":     market,
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "volume_profile": {
            "bins": bins,
            "poc":     round(poc, 2),
            "va_high": round(va_high, 2),
            "va_low":  round(va_low, 2),
        },
        "vwap_current":   vwap_list[-1] if vwap_list else None,
        "vwap_series":    vwap_list,
        "resistance":     resistance,
        "support":        support,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# KBJ P8 삭제: api_portfolio_sync — kbj 페이지 12 포트폴리오(매매 기록 재생 — 서버 파일 동기화 없음, ADR 0033)


# KBJ P8 삭제: _check_trailing_stops — kbj rules.intraday·rules.close(페이지 11 규칙 알림 — ADR 0031) — DSL trail_from_peak 조건


# KBJ P8 삭제: api_alerts_sync — kbj 규칙 쓰기 모듈(kbj.services.personal.rules — /api/rules)


# KBJ P8 삭제: api_alerts_list — kbj /api/rules


# KBJ P8 삭제: _format_rule_label — check_alert_rules 전용 도우미(그 함수가 P8 에 지워져 부르는 곳 없음)


# KBJ P8 삭제: check_alert_rules — kbj rules.intraday·rules.close(페이지 11 규칙 알림 — ADR 0031)


# KBJ P2 삭제(설계 §5.10): api_telegram_test — #19 인증 없는 텔레그램 시험 발송 라우트


# KBJ P2 삭제(설계 §5.10): api_briefing_test — #19 인증 없는 브리핑 시험 발송 라우트


# ── 텔레그램 양방향 봇 (webhook 명령 처리) ───────────────────────────────────
# push 전용 → 온디맨드 조회 지원. 보안: 소유자 chat_id 만 응답 + 시크릿 헤더.
# KBJ P2 삭제(설계 §5.10): _telegram_secret — #20·#21 봇 토큰 파생 시크릿 — 웹훅 시크릿은 KBJ_TELEGRAM_WEBHOOK_SECRET(notifier)


def _resolve_kr_code(q: str) -> tuple[str, str] | None:
    """종목명 또는 코드 → (code, name). 정확>접두>부분 일치 우선."""
    q = (q or "").strip()
    if not q:
        return None
    import re as _re
    if _re.fullmatch(r"\d{6}", q):
        return (q, _get_stock_name(q) or q)
    ql = q.lower()
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    best = None  # (score, code, name)
    for code, rec in stocks.items():
        name = rec.get("name") or ""
        nl = name.lower()
        if nl == ql:
            return (code, name)
        if nl.startswith(ql):
            sc = 1
        elif ql in nl:
            sc = 2
        else:
            continue
        if best is None or sc < best[0]:
            best = (sc, code, name)
    return (best[1], best[2]) if best else None


def _tg_reply(text: str) -> None:
    send_telegram(text)


def _tg_cmd_price(arg: str) -> str:
    hit = _resolve_kr_code(arg)
    if not hit:
        return f"❓ '{arg}' 종목을 찾지 못했습니다."
    code, name = hit
    d = _call_api_internal(f"/api/price/{code}") or {}
    price = d.get("price") or d.get("close")
    chg = d.get("change_pct")
    if price is None:
        return f"❓ {name}({code}) 가격 조회 실패"
    sign = "+" if (chg or 0) >= 0 else ""
    vol = d.get("volume_mn")
    vol_str = f"\n거래대금 {vol/1e2:,.0f}억" if vol else ""
    return (f"📈 <b>{name}</b> ({code})\n"
            f"{price:,.0f}원 {sign}{(chg or 0):.2f}%{vol_str}")


def _tg_cmd_flow(arg: str) -> str:
    hit = _resolve_kr_code(arg)
    if not hit:
        return f"❓ '{arg}' 종목을 찾지 못했습니다."
    code, name = hit
    try:
        _fetch_and_save_flow(code)  # 최신 갱신 (Naver 라이브)
    except Exception:
        pass
    d = _read_flow_db(code) if (_SQLITE_OK and USE_SQLITE) else None
    if not d or not d.get("dates"):
        return f"❓ {name}({code}) 수급 데이터 없음"
    dates = d["dates"]; fv = d.get("foreign_value") or []; iv = d.get("inst_value") or []
    n = min(5, len(dates))
    lines = [f"💰 <b>{name}</b> ({code}) 최근 수급"]
    for i in range(len(dates) - n, len(dates)):
        md = dates[i][5:].replace("-", "/")
        f_eok = (fv[i] / 1e8) if i < len(fv) else 0
        i_eok = (iv[i] / 1e8) if i < len(iv) else 0
        sf = "+" if f_eok >= 0 else ""; si = "+" if i_eok >= 0 else ""
        lines.append(f"  {md} 외 {sf}{f_eok:,.0f}억 · 기 {si}{i_eok:,.0f}억")
    f20 = d.get("foreign_sum_20"); i20 = d.get("inst_sum_20")
    if f20 is not None:
        lines.append(f"📊 20일 누적 외 {f20/1e8:+,.0f}억 · 기 {(i20 or 0)/1e8:+,.0f}억")
    return "\n".join(lines)


def _tg_help() -> str:
    return ("🤖 <b>명령어</b>\n"
            "/시황 — 장마감 확정 시황\n"
            "/시그널 — 수급 시그널 (쌍끌이·연속·반전)\n"
            "/수급 &lt;종목&gt; — 종목 최근 수급 (예: /수급 삼성전자)\n"
            "/가격 &lt;종목&gt; — 현재가\n"
            "/도움 — 이 안내")


def _handle_telegram_command(text: str) -> None:
    """명령 디스패치 → 백그라운드 응답 발송."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return
    parts = text[1:].split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/")
    arg = parts[1].strip() if len(parts) > 1 else ""
    # @botname 접미사 제거 (그룹 채팅 대응)
    cmd = cmd.split("@")[0]

    try:
        if cmd in ("도움", "help", "start", "명령"):
            _tg_reply(_tg_help())
        elif cmd in ("시황", "summary"):
            _tg_reply("⏳ 시황 생성 중…")
            send_market_summary_telegram(header="🌙 장마감 확정 시황 (요청)")
        elif cmd in ("시그널", "수급시그널", "signals"):
            # KBJ P5: 수급 시그널 알림은 kbj flows.report(16:00)가 대체 — alert_flow_signals 삭제
            _tg_reply("수급 리포트는 KBJ flows.report(평일 16:00)로 옮겼다")
        elif cmd in ("수급", "flow"):
            if not arg:
                _tg_reply("사용법: /수급 삼성전자")
            else:
                _tg_reply(_tg_cmd_flow(arg))
        elif cmd in ("가격", "시세", "price"):
            if not arg:
                _tg_reply("사용법: /가격 삼성전자")
            else:
                _tg_reply(_tg_cmd_price(arg))
        else:
            _tg_reply(f"❓ 알 수 없는 명령: /{cmd}\n" + _tg_help())
    except Exception as exc:
        log.warning("[봇] 명령 처리 실패 (%s): %s", cmd, exc)
        _tg_reply(f"⚠️ 처리 중 오류: {str(exc)[:100]}")


# KBJ P2 삭제(설계 §5.10): api_telegram_webhook — #21 웹훅 수신은 KBJ notifier 하나(POST /telegram/webhook) — 명령 로직은 notifier commands(P3)


# KBJ P2 삭제(설계 §5.10): _telegram_setup_webhook — #20 setWebhook 은 notifier 만(python -m kbj.services.notifier setup-webhook)


# KBJ P2 삭제(설계 §5.10): api_telegram_setup_webhook — #20 setWebhook 은 notifier 만


@app.route("/api/watchlist/sync", methods=["POST"])
def api_watchlist_sync():
    """프론트 localStorage 관심종목 → 서버 파일 동기화 (알림용)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    items = data.get("items") or []
    out = BASE_DIR / "cache" / "server_watchlist.json"
    try:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500
    return jsonify({"status": "ok", "count": len(items)})


def _load_server_watchlist() -> list:
    f = BASE_DIR / "cache" / "server_watchlist.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")) or []
    except Exception:
        return []


# ── 알림 1: 장 시작 전 브리핑 (평일 08:30) ──
# KBJ P4 삭제: _fmt_revision_period — kbj consensus.snapshot·kbj.engines.consensus.revision·/api/stock/{code}/consensus(KIS — ADR 0015)


# KBJ P4 삭제: alert_revision_signals — kbj consensus.snapshot·kbj.engines.consensus.revision·/api/stock/{code}/consensus(KIS — ADR 0015)


# KBJ P5 삭제: alert_morning_briefing


# ── 알림 2: 종목 발굴 Stage 2 신규 진입 ──
# KBJ P8 삭제: _alert_cooldown_ok — SD 알림 쿨다운(종목×종류 60분) — kbj 규칙 쿨다운 상태기(kbj.engines.rules.cooldown)


# KBJ P8 삭제: _record_alert — SD 알림 기록 도우미(부르던 발굴·관심종목 알림이 P8 에 지워짐)


# KBJ P8 삭제: alert_discovery_new_entries — 폐지(사용자 결정 2026-10-13 — 발굴 신규 진입 알림, jobs.yaml retired)


# ── 알림 3: 관심종목 급등/급락 (±5%) ──
# KBJ P8 삭제: alert_watchlist_price — kbj rules.intraday·rules.close(페이지 11 규칙 알림 — ADR 0031) — 관심종목 가격 조건은 규칙으로


# ── 알림 4: 관심종목 신규 리포트 ──
def alert_new_reports():
    try:
        watchlist = _load_server_watchlist()
        if not watchlist:
            return
        wl_codes = set(it.get("code") for it in watchlist if it.get("code"))
        reports_file = BASE_DIR / "cache" / "company_reports.json"
        if not reports_file.exists():
            return
        data = json.loads(reports_file.read_text(encoding="utf-8"))
        today_prefix = now_kst().strftime("%y.%m")  # 26.04
        matched = []
        for r in data.get("reports") or []:
            if r.get("stock_code") in wl_codes:
                d = r.get("date") or ""
                if d.startswith(today_prefix):
                    matched.append(r)
        if matched:
            lines = ["📑 <b>관심종목 신규 리포트</b>", ""]
            for r in matched[:5]:
                lines.append(f"• <b>{r.get('stock_name')}</b> — {r.get('title','')}")
                lines.append(f"   {r.get('broker','')} · {r.get('date','')}")
            send_telegram("\n".join(lines))
    except Exception as exc:
        log.warning("alert_new_reports: %s", exc)


# ── 알림 6: 새벽 5:30 익일 시장 예측 (미국 옵션 + 야간선물 + 매크로) ──
# KBJ P5 삭제: alert_overnight_prediction


# KBJ P5 삭제: _refresh_briefing_data


def refresh_us_options_signal():
    """옵션 시그널 재빌드 + 캐시 저장 (S-2-B: race-free).

    원래 선삭제 후 재빌드였으나, 빌드 실패 시 기존 캐시까지 잃고
    재빌드 중 race window (1~2초) 에 시황 빌더가 SPY 누락 출력하는
    문제 발생. atomic write 패턴으로 변경 — 성공 시에만 덮어쓰기.
    """
    for sym in ("SPY", "QQQ"):
        f = BASE_DIR / "cache" / f"options_signal_{sym}.json"
        try:
            result = _compute_options_signal(sym)
        except Exception as exc:
            log.warning("[옵션] %s 빌드 예외 → 기존 캐시 유지: %s", sym, exc)
            continue
        if not result or (isinstance(result, dict) and "error" in result):
            err = (result or {}).get("error") if isinstance(result, dict) else "no result"
            log.warning("[옵션] %s 빌드 실패 → 기존 캐시 유지: %s", sym, err)
            continue
        try:
            # atomic write: tmp 에 쓰고 rename — 시황 빌더가 부분 파일 못 읽도록.
            f.parent.mkdir(exist_ok=True)
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
            log.info("[옵션] %s 재빌드 + 캐시 저장 OK", sym)
        except Exception as exc:
            log.warning("[옵션] %s 캐시 저장 실패: %s", sym, exc)


def _refresh_global_data_periodic():
    """텔레그램 토큰 유무와 무관하게 4시간마다 매크로·야간선물·옵션·공포탐욕 갱신."""
    log.info("[자동갱신] 시작")
    # macro
    try:
        _call_api_internal("/api/macro")
    except Exception as exc:
        log.debug("[자동갱신] macro: %s", exc)
    # night_futures
    try:
        _call_api_internal("/api/night_futures")
    except Exception as exc:
        log.debug("[자동갱신] night: %s", exc)
    # options (compute + 캐시 저장 둘 다 처리하는 refresh_us_options_signal 활용)
    try:
        refresh_us_options_signal()
    except Exception as exc:
        log.debug("[자동갱신] options: %s", exc)
    # fear_greed
    try:
        _call_api_internal("/api/fear_greed")
    except Exception as exc:
        log.debug("[자동갱신] fg: %s", exc)
    # valuechain heat (뉴스 기반)
    try:
        from valuechain import calculate_layer_heat
        # 강제 갱신: 캐시 만료 처리
        from valuechain import _heat_cache as _vch
        _vch["ts"] = 0
        calculate_layer_heat()
    except Exception as exc:
        log.debug("[자동갱신] valuechain: %s", exc)
    log.info("[자동갱신] 완료")


# ── 알림 5: 장 마감 요약 (평일 15:40) ──
def alert_closing_summary():
    try:
        lines = ["🔔 <b>장 마감 요약</b>", ""]

        # KOSPI/KOSDAQ — 4-5-2-B: 라이브 fetch 우선 (data.json stale 회피)
        live_kr = {}
        try:
            live_kr = _fetch_kr_indices_live() or {}
        except Exception as exc:
            log.debug("[closing] live fetch fail: %s", exc)
        fallback = {}
        if DATA_JSON.exists():
            try:
                fallback = json.loads(DATA_JSON.read_text(encoding="utf-8")) or {}
            except Exception:
                fallback = {}
        for label, key in (("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")):
            idx = live_kr.get(key) or fallback.get(key) or {}
            val = idx.get("value")
            chg = idx.get("change_pct")
            if val is not None:
                sign = "+" if (chg or 0) >= 0 else ""
                lines.append(f"{label} {val:,.2f} ({sign}{chg}%)")
        lines.append("")

        # 종목 발굴 TOP5 (KR stage2)
        ds = BASE_DIR / "cache" / "discover_kr_stage2.json"
        if ds.exists():
            try:
                dd = json.loads(ds.read_text(encoding="utf-8"))
                lines.append("🔬 <b>종목 발굴 TOP5</b>")
                for it in (dd.get("items") or [])[:5]:
                    chg = it.get("change_pct") or 0
                    sign = "+" if chg >= 0 else ""
                    lines.append(f"  🇰🇷 {it['name']} {it['total_score']}점 ({sign}{chg}%)")
            except Exception:
                pass
        send_telegram("\n".join(lines))
    except Exception as exc:
        log.warning("alert_closing_summary: %s", exc)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 23 — ETF 히트맵
# ─────────────────────────────────────────────────────────────────────────
_ETF_THEMES = {
    "반도체/AI": [
        ("091160", "KODEX 반도체"),
        ("395160", "TIGER AI반도체핵심공정"),
        ("466920", "TIGER 코리아AI전력핵심설비"),
        ("139260", "TIGER 200 IT"),
    ],
    "2차전지": [
        ("305720", "KODEX 2차전지산업"),
        ("364960", "TIGER 2차전지테마"),
    ],
    "바이오/헬스": [
        ("244580", "KODEX 바이오"),
        ("227540", "TIGER 헬스케어"),
    ],
    "방산/조선": [
        ("464510", "TIGER 우주방산"),
    ],
    "레버리지/인버스": [
        ("122630", "KODEX 레버리지"),
        ("114800", "KODEX 인버스"),
        ("252670", "KODEX 200선물인버스2X"),
    ],
    "미국 지수": [
        ("379800", "KODEX 미국S&P500TR"),
        ("381170", "TIGER 미국나스닥100"),
        ("453810", "TIGER 미국필라델피아반도체나스닥"),
    ],
    "배당": [
        ("211900", "KODEX 배당가치"),
        ("161510", "TIGER 배당성장"),
    ],
    "에너지/화학": [
        ("117460", "KODEX 에너지화학"),
    ],
}


# KBJ P5 삭제: _scrape_etf_price


# KBJ P5 삭제: api_etf_map


# ─────────────────────────────────────────────────────────────────────────
# PHASE 23 — 배당 스크리너
# ─────────────────────────────────────────────────────────────────────────
_DIV_SEED_CODES = [
    "017670",  # SK텔레콤
    "030200",  # KT
    "032640",  # LG유플러스
    "086790",  # 하나금융지주
    "105560",  # KB금융
    "055550",  # 신한지주
    "316140",  # 우리금융지주
    "000270",  # 기아
    "005490",  # POSCO홀딩스
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "003550",  # LG
    "000810",  # 삼성화재
    "010130",  # 고려아연
    "033780",  # KT&G
    "005380",  # 현대차
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "138930",  # BNK금융지주
    "175330",  # JB금융지주
    "139130",  # DGB금융지주
    "006260",  # LS
    "097950",  # CJ제일제당
    "011780",  # 금호석유
    "010950",  # S-Oil
    "003490",  # 대한항공
    "090430",  # 아모레퍼시픽
    "009540",  # HD한국조선해양
    "024110",  # 기업은행
    "034730",  # SK
]


@app.route("/api/dividend")
def api_dividend():
    """
    배당수익률 상위 종목. 기존 /api/financial 캐시 재사용 우선,
    없으면 네이버 main.naver 스크랩. 일 1회 캐시.
    """
    today = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"dividend_{today}.json"
    cached = _read_fresh_json(cache_file, 1440)
    if cached:
        return jsonify(cached)

    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    items: list[dict] = []
    seen = set()

    for code in _DIV_SEED_CODES:
        try:
            fin = _call_api_internal(f"/api/financial/{code}") or {}
            dy = fin.get("dividend_yield")
            if dy is None or dy <= 0:
                continue
            if code in seen:
                continue
            seen.add(code)
            uni_stock = stocks.get(code) or {}
            items.append({
                "code":           code,
                "name":           uni_stock.get("name") or fin.get("name") or code,
                "price":          uni_stock.get("close") or 0,
                "change_pct":     uni_stock.get("change_pct") or 0,
                "dividend_yield": round(dy, 2),
                "per":            fin.get("per"),
                "pbr":            fin.get("pbr"),
                "sector":         (uni_stock.get("sectors") or [None])[0] or "—",
            })
            time.sleep(0.05)
        except Exception as exc:
            log.debug("div fetch %s fail: %s", code, exc)
            continue

    items.sort(key=lambda x: x["dividend_yield"], reverse=True)

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(items),
        "items":      items,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _cleanup_old_cache(max_days: int = 7):
    """서버 시작 시 max_days 이상 된 캐시 JSON 파일 자동 삭제."""
    cache_dir = BASE_DIR / "cache"
    if not cache_dir.exists():
        return
    import glob as _glob
    cutoff = time.time() - max_days * 86400
    removed = 0
    # 날짜가 포함된 일별 캐시만 삭제 (매핑 파일은 보존)
    preserve = {"dart_corp_codes.json", "sp500_tickers.json",
                "sectors_naver_landing.json", "server_watchlist.json"}
    for f in _glob.glob(str(cache_dir / "*.json")):
        fname = Path(f).name
        if fname in preserve:
            continue
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
                removed += 1
        except Exception:
            pass
    if removed:
        log.info("캐시 클린업: %d개 파일 삭제 (>%d일)", removed, max_days)


# ─────────────────────────────────────────────────────────────────────────
# WebSocket 실시간 가격 시스템
# ─────────────────────────────────────────────────────────────────────────
_ws_subscriptions: dict[str, list[str]] = {}   # {sid: [codes]}
_ws_subscribed_codes: set[str] = set()         # 전체 구독 종목
_ws_last_prices: dict[str, float] = {}         # {code: last_close} 변경 감지용


def _ws_recompute_codes():
    global _ws_subscribed_codes
    codes = set()
    for cl in _ws_subscriptions.values():
        codes.update(cl)
    _ws_subscribed_codes = codes


if _SOCKETIO_OK:
    @socketio.on("connect")
    def _ws_connect():
        sid = request.sid
        _ws_subscriptions[sid] = []
        emit("connected", {"status": "ok", "sid": sid})

    @socketio.on("disconnect")
    def _ws_disconnect():
        sid = request.sid
        for code in _ws_subscriptions.pop(sid, []):
            leave_room(f"s_{code}")
        _ws_recompute_codes()

    @socketio.on("subscribe")
    def _ws_subscribe(data):
        sid = request.sid
        codes = data.get("codes") or []
        if isinstance(codes, str):
            codes = [codes]
        codes = [c for c in codes if c and len(c) <= 10]
        for code in codes:
            join_room(f"s_{code}")
            if code not in _ws_subscriptions.get(sid, []):
                _ws_subscriptions.setdefault(sid, []).append(code)
        _ws_recompute_codes()
        emit("subscribed", {"codes": codes})

    @socketio.on("unsubscribe")
    def _ws_unsubscribe(data):
        sid = request.sid
        codes = data.get("codes") or []
        if isinstance(codes, str):
            codes = [codes]
        for code in codes:
            leave_room(f"s_{code}")
            try:
                _ws_subscriptions.get(sid, []).remove(code)
            except ValueError:
                pass
        _ws_recompute_codes()
        emit("unsubscribed", {"codes": codes})


# KBJ P4 삭제: _price_broadcaster — P3 대체(페이지 1·2·4 — /api/market·/api/flows/stock/{code})


def _startup():
    global _startup_done, _scheduler
    if _startup_done:
        return
    _startup_done = True

    _cleanup_old_cache(7)

    # SQLite DB 초기화
    if _SQLITE_OK and USE_SQLITE:
        try:
            _init_db()
            log.info("[SQLite] DB 초기화 완료")
        except Exception as exc:
            log.warning("[SQLite] DB 초기화 실패: %s → JSON 폴백", exc)
        # Render 재배포 후 핵심 데이터 복원 (Gist) — 첫 부팅 시
        try:
            from db_backup import restore_db as _restore_db
            r = _restore_db()
            if r.get("ok"):
                log.info("[DB복원] %s (raw=%dKB)", r.get("merged"), r.get("raw_kb", 0))
            else:
                log.info("[DB복원] skip: %s", r.get("reason"))
        except Exception as exc:
            log.debug("[DB복원] 모듈 로드 실패: %s", exc)
        # US 캐시 → DB 동기화 (재시작 시 stocks 테이블 stale 방지)
        try:
            r = sync_us_market_to_db_from_cache()
            if r.get("ok"):
                log.info("[US sync] 부팅 시 %d종목 DB 동기화", r.get("synced", 0))
        except Exception as exc:
            log.debug("[US sync] 부팅 시 실패: %s", exc)
        # 글로벌 데이터 부팅 시 1회 백그라운드 갱신 (stale 즉시 해소)
        try:
            import threading as _th
            _th.Thread(target=_refresh_global_data_periodic,
                       daemon=True, name="boot-data-refresh").start()
        except Exception as exc:
            log.debug("[부팅 자동갱신] %s", exc)
        # KR stocks 가격 부팅 시 1회 갱신 — 장중 cron(9-15:5,35) 외 시간에
        # 부팅하면 다음 평일 09:05 까지 stale. 텔레 시황 섹터/특징주 빈 값 방지.
        try:
            import threading as _th
            def _boot_refresh_kr():
                try:
                    n = _refresh_prices_from_naver()
                    log.info("[부팅] KR 가격 stale 해소 — %d종목 갱신", n)
                    # 재배포로 DB 가 새로 차면 새 행은 is_etf 기본값 0 이다.
                    # 03:10 cron 을 기다리면 그 사이 화면·알림에 ETF 가 섞인다.
                    try:
                        log.info("[부팅] ETF 표식 %d종목", mark_etf_stocks())
                    except Exception as exc:
                        log.warning("[부팅] ETF 표식 실패: %s", exc)
                    # flow_cache 도 부팅 시 1회 갱신 (시총 큰 종목만)
                    # 가격 갱신이 끝난 후 stocks.market_cap 으로 정렬 가능
                    try:
                        r = _refresh_flow_batch(top_n=200)
                        if r.get("ok"):
                            log.info("[부팅] flow_cache stale 해소 — %d/%d 성공 (%.1fs)",
                                     r.get("success", 0), r.get("total", 0),
                                     r.get("elapsed_sec", 0))
                    except Exception as exc:
                        log.warning("[부팅] flow batch 실패: %s", exc)
                except Exception as exc:
                    log.warning("[부팅] KR 가격 갱신 실패: %s", exc)
            _th.Thread(target=_boot_refresh_kr,
                       daemon=True, name="boot-kr-price").start()
        except Exception as exc:
            log.debug("[부팅] KR 가격 thread 시작 실패: %s", exc)
        # 추천 이력 테이블 + 과거 discover 스냅샷 소급 (최초 1회)
        try:
            _init_recommendation_history()
            migrate_discover_to_recommendations()
            # KBJ P8 삭제: _init_trade_journal() — kbj prv_journal.trade(페이지 12)
        except Exception as exc:
            log.debug("[추천이력] init 실패: %s", exc)

    if not FETCHER.exists():
        log.error("data_fetcher.py 를 찾을 수 없습니다: %s", FETCHER)
        return

    # ── Render 환경 감지 (RENDER_EXTERNAL_URL 자동 주입됨) ──
    # Render에서는 무거운 pykrx/yfinance 서브프로세스 비활성화 → 맥북 cron으로 분리
    IS_RENDER = bool(os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("RENDER"))
    DISABLE_AUTO_FETCH = IS_RENDER or bool(os.environ.get("DISABLE_AUTO_FETCH"))

    if DISABLE_AUTO_FETCH:
        log.info("⏸  자동 fetcher 비활성화 (Render 환경) — 맥북 cron으로 갱신")
    else:
        # data.json 이 없거나 오래됐으면 자동 수집 (로컬만)
        if data_is_fresh():
            log.info("data.json 최신 상태 — 수집 생략")
        else:
            reason = "없음" if not DATA_JSON.exists() else "오늘 날짜 아님"
            log.info("data.json %s → data_fetcher.py 백그라운드 실행", reason)
            trigger_fetch(background=True)

        # Phase 10: Naver 업종 유니버스 백그라운드 빌드 (비차단)
        #   일 1회, 약 79 섹터 × 0.25s ≈ 20 초 소요.
        # S-2-A: 빌드 후 즉시 가격 갱신까지 연쇄 (universe 본체 미완성 시
        # stocks.change_pct 동결되는 회귀 방지).
        threading.Thread(target=_startup_naver_universe_sync,
                         daemon=True, name="naver-startup").start()

        # KBJ P2 삭제(설계 §5.10 #20): 부팅 setWebhook — 웹훅은 KBJ notifier 하나(§5.11)

        # Phase 14: S&P 500 market 백그라운드 빌드
        #   일 1회, ~180 초 소요. 사용자가 [🇺🇸 미국] 토글 누르기 전에 완료되도록.
        threading.Thread(target=_build_us_market_background,
                         daemon=True, name="us-market-build").start()

    # ── 일봉(ohlcv) 부팅 채움 ── **분기 밖이어야 한다.**
    #
    # 2026-09-18 에 이 스레드가 `if DISABLE_AUTO_FETCH: ... else:` 의 else 쪽에
    # 있었다. Render 는 `IS_RENDER` 가 참이라 늘 if 쪽으로 가므로 **정작 DB 가
    # 사라지는 환경에서만 이 스레드가 한 번도 안 돌았다.** 그날 신고가 섹션이
    # 하루 종일 '일봉 거래일이 0일뿐' 이었던 까닭이다.
    #
    # else 분기의 취지는 pykrx·yfinance 서브프로세스를 Render 에서 안 돌리는
    # 것인데, 일봉 채움은 표준 라이브러리로 네이버 JSON 만 읽는다(ohlcv_autofill).
    # 여기 걸릴 이유가 없었다.
    #
    # 데몬 스레드라 Flask 기동을 막지 않는다. 재배포 직후(빈 DB)는 시총 800억
    # 이상 ~1,500종목 전 구간이라 동시 4개로 수 분(러너 실측 기준 추정 ~7분),
    # DB 가 살아 있는 재시작이면 증분이라 거의 즉시 끝난다.
    threading.Thread(target=_startup_ohlcv_fill,
                     daemon=True, name="ohlcv-startup").start()

    # Render 는 영속 디스크가 없어 재시작하면 data.json 이 git 에 있는 판으로
    # 되돌아간다. 부팅 직후 한 번 덮어써야 그날 값이 올라온다.
    threading.Thread(target=_startup_data_json,
                     daemon=True, name="datajson-startup").start()

    # yfinance 를 미리 한 번 제대로 올려 둔다. 여러 스레드가 동시에 처음
    # import 하면 반쯤 만들어진 모듈이 와서 해외지수가 통째로 빈다.
    threading.Thread(target=_warm_yfinance,
                     daemon=True, name="yfinance-warm").start()

    # APScheduler: 장중 자동 갱신 (Render에선 _scheduled_update 등록 안 함)
    if _SCHEDULER_OK:
        def _scheduled_update():
            if is_market_hours():
                log.info("⏰  장중 자동 갱신 트리거")
                trigger_fetch(background=True, force_market=True)
            else:
                log.debug("장외 — 자동 갱신 스킵")

        _scheduler = _BgScheduler(daemon=True)
        interval = _get()["interval_minutes"]
        if not DISABLE_AUTO_FETCH:
            _scheduler.add_job(
                _scheduled_update, "interval", minutes=interval,
                id="market_update", max_instances=1,
            )

        # Phase 23: 텔레그램 알림 스케줄 (토큰 있을 때만)
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            # KBJ P5 삭제: 등록 alert_morning_briefing
            # KBJ P4 삭제: tg_revision_signals(alert_revision_signals) — kbj consensus.snapshot 의 alert.revision(16:00, ADR 0018)
            # KBJ P8 삭제: 등록 alert_watchlist_price(tg_watchlist) — kbj rules.intraday
            _scheduler.add_job(alert_new_reports, "cron",
                               day_of_week="mon-fri", hour=10, minute=0,
                               id="tg_reports")
            _scheduler.add_job(alert_closing_summary, "cron",
                               day_of_week="mon-fri", hour=15, minute=40,
                               id="tg_closing")
            # flow_cache 일괄 갱신 (15:40 — 가격 sync 15:35 후, 시황 15:50 전)
            # 시총 상위 200종목 외인/기관 수급 fetch → 시황 메시지 신선도 확보.
            _scheduler.add_job(lambda: _refresh_flow_batch(top_n=200), "cron",
                               day_of_week="mon-fri", hour=15, minute=40,
                               id="flow_batch", max_instances=1,
                               misfire_grace_time=600)
            # 장마감 시황 (16:00 — 사용자 요청). 정규장 마감 15:30 직후 확정
            # 종가로 그날을 정리해 받는다.
            #
            # 19:00 이었던 것을 2026-09-18 에 앞당겼다. 19:00 을 고른 이유는
            # 투자자별 수급 확정치(KRX ~18:00 · 네이버 ~18:30)를 기다리려는
            # 것이었는데, 그 대가로 시황이 장 끝나고 세 시간 반 뒤에 왔다.
            # 수급 한 줄 때문에 나머지 전부를 늦추는 건 맞는 거래가 아니다.
            # 16:00 수급은 전일 값이지만 오늘 값인 척 나가지 않는다 —
            # build_market_summary 가 수급 섹션에 실제 기준일을 적는다.
            #
            # **misfire_grace_time 을 넉넉히 준다.** 기본값은 1초라, 정각에
            # 스케줄러가 바쁘거나 막 깨어났으면 그날 발송이 통째로 날아간다.
            # 그래도 못 보낸 날은 closing_brief_catchup 이 뒤늦게 보낸다.
            _scheduler.add_job(send_closing_market_summary, "cron",
                               day_of_week="mon-fri",
                               hour=_CLOSING_BRIEF_HHMM[0],
                               minute=_CLOSING_BRIEF_HHMM[1],
                               id="tg_closing_summary", max_instances=1,
                               misfire_grace_time=1800)
            # 수급 심화 시그널 (19:30 — 저녁 확정 수급 분석 후)
            # 쌍끌이 매수/매도 · 외국인 연속 순매수/순매도 · 수급 반전 포착.
            # KBJ P5 삭제: 등록 alert_flow_signals
            # 신고가 캐시 프리워밍 (15:48 — 가격 sync 15:35 후) — 첫 진입 행 방지
            _scheduler.add_job(_prewarm_new_highs, "cron",
                               day_of_week="mon-fri", hour=15, minute=48,
                               id="prewarm_new_highs", max_instances=1,
                               misfire_grace_time=600)
            # 데이터 정합성 워치독 (평일 08:00~20:00 30분 간격) — 시황 핵심
            # 데이터(stocks·flow_cache) 공백/정체 감지 시 자동 재갱신 + 1회 알림.
            _scheduler.add_job(_market_watchdog, "cron",
                               day_of_week="mon-fri", hour="8-20", minute="0,30",
                               id="data_watchdog", max_instances=1,
                               misfire_grace_time=300)
            # 밀린 장마감 시황을 뒤늦게라도 보낸다. Render 무료 플랜이 16:00 에
            # 자고 있었으면 cron 은 돌지 않는다 — 깨어 있는 30분마다 확인한다.
            # 하루 한 번 제한은 send_closing_market_summary 가 건다.
            # 16:05 부터 20:35 까지 30분마다. 마지막 슬롯이
            # _CLOSING_BRIEF_DEADLINE_HHMM(20:35)과 같아야 한다 — 그 시각에는
            # 데이터가 덜 찼어도 보낸다. 창이 더 짧으면 재배포로 DB 가 날아간
            # 날 시황이 통째로 안 온다.
            _scheduler.add_job(closing_brief_catchup, "cron",
                               day_of_week="mon-fri", hour="16-20", minute="5,35",
                               id="closing_brief_catchup", max_instances=1,
                               misfire_grace_time=1800)
            # 미국 장마감 시황 (KST 06:10 — 미국 월~금 마감 = KST 화~토)
            # KBJ P5 삭제: 등록 send_us_market_summary_telegram
            # Phase 24 Step 3: 새벽 5:00 브리핑 데이터 갱신
            # KBJ P5 삭제: 등록 _refresh_briefing_data
            # Phase 24 Step 3: 새벽 5:30 익일 예측 브리핑 (화~토, 월요일 제외)
            # KBJ P5 삭제: 등록 alert_overnight_prediction
            # Phase 24 Step 3: 밤 22:00 미국장 시작 전 옵션 캐시 무효화
            _scheduler.add_job(refresh_us_options_signal, "cron",
                               day_of_week="mon-fri", hour=22, minute=0,
                               id="refresh_options")
            # KBJ P8 삭제: 등록 check_alert_rules(tg_custom_alerts)·_check_trailing_stops(tg_trailing) — kbj rules.intraday
            # 부팅 직후에도 한 번 본다. Render 가 잠들었다 깨어나는 순간이
            # 가장 확실한 기회다 — 그때 밀린 것이 있으면 바로 보낸다.
            # 백그라운드로 돌린다: 발송이 가격·수급 갱신을 먼저 하므로
            # 부팅 경로를 막으면 첫 요청이 그만큼 늦어진다.
            try:
                import threading as _th
                _th.Thread(target=closing_brief_catchup, daemon=True,
                           name="closing-brief-catchup").start()
            except Exception as exc:                          # noqa: BLE001
                log.debug("[장마감시황] 부팅 캐치업 기동 실패: %s", exc)
            log.info("[텔레그램] 알림 스케줄 등록 완료")
        else:
            log.info("[텔레그램] 토큰 미설정 — 알림 비활성화")

        # ── 가격 동기화 ──
        # 장중 30분마다 + 장마감 직후(15:35) 가격 갱신
        _scheduler.add_job(_refresh_prices_from_naver, "cron",
                           day_of_week="mon-fri", hour="9-15", minute="5,35",
                           id="price_sync_intraday", max_instances=1)
        _scheduler.add_job(_refresh_prices_from_naver, "cron",
                           day_of_week="mon-fri", hour=15, minute=35,
                           id="price_sync_close", max_instances=1)
        # 시간외 단일가: 16:00~18:00 5분 간격
        _scheduler.add_job(_refresh_prices_from_naver, "cron",
                           day_of_week="mon-fri", hour="16-17", minute="*/5",
                           id="price_sync_afterhours", max_instances=1)
        log.info("[가격 동기화] 장중 30분 + 장마감 + 시간외 스케줄 등록")

        # ── data.json 서버 생성 ──
        # 예전엔 맥북 cron 이 18:00 에 만들어 git push 했다. 그 cron 이 멈추면
        # 아무 신호 없이 파일이 굳는다(2026-09-15 에 실제로 멈췄다). 서버가 직접 만든다.
        #
        # 두 번 도는 이유:
        #   15:45 — 15:35 가격 sync 직후. 15:50 마감 시황이 오늘 값을 쓰게 한다.
        #           (맥북 18:00 체제에서는 시황이 늘 전날 data.json 을 봤다)
        #   16:20 — 16:10 일봉 채움 직후. 스파크라인에 오늘 봉이 들어간다.
        _scheduler.add_job(_refresh_data_json_job, "cron",
                           day_of_week="mon-fri", hour=15, minute=45,
                           id="data_json_close", max_instances=1)
        _scheduler.add_job(_refresh_data_json_job, "cron",
                           day_of_week="mon-fri", hour=16, minute=20,
                           id="data_json_evening", max_instances=1)
        log.info("[data.json] 15:45 · 16:20 생성 스케줄 등록")

        # ── 일봉(ohlcv) 자동 채움 ──
        # ohlcv 는 신고가·52주 밴드·상관관계 등 server.py 읽기 15곳의 입력인데
        # 채우는 자동 경로가 없었다. Render 는 영속 디스크가 없어 재시작마다
        # db/dashboard.db 가 사라지므로 재시작 한 번에 신고가 섹션이 영구히
        # 비었다("일봉 거래일이 0일뿐"). 그 구멍을 막는다.
        #
        # 16:10 인 이유 — 15:35 장마감 가격 sync 뒤라서 오늘 확정 종가 봉을
        # 받고, 대상을 고르는 시총도 당일 종가 기준이 된다. 대상은 ETF 를 뺀
        # 시총 800억 이상 전 종목(~1,500)이지만 **증분**이라 종목마다 어제 이후
        # 며칠치만 받는다. 이 봉들이 다음 날 16:00 시황의 '전일까지 종가' 가 된다.
        _scheduler.add_job(_fill_ohlcv_job, "cron",
                           day_of_week="mon-fri", hour=16, minute=10,
                           id="ohlcv_autofill", max_instances=1,
                           misfire_grace_time=1800)
        log.info("[일봉 채움] 평일 16:10 스케줄 등록")

        # ── Stage 2 자동 스캔 ──
        # 1) 장중 5분 간격 KR 실시간 스캔 (SQLite 기반 ~1초)
        def _stage2_realtime_kr():
            # 장외 시간이면 스킵 (cron만으로 부족 — 공휴일 체크)
            if not is_market_hours():
                log.debug("[Stage 2 realtime] 장외 시간 — 스킵")
                return
            with _discover_lock:
                if _discover_state["status"] in ("starting", "running"):
                    return
            _discover_set(status="running", phase="kr_stage1",
                          message="장중 자동 스캔…",
                          started_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
            try:
                _run_stage2_kr()
            except Exception as exc:
                log.debug("stage2 realtime kr fail: %s", exc)
            finally:
                _discover_set(status="done", phase=None,
                              finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                              message="장중 자동 스캔 완료")

        _scheduler.add_job(_stage2_realtime_kr, "cron",
                           day_of_week="mon-fri", hour="9-15", minute="*/5",
                           id="stage2_realtime_kr", max_instances=1)

        # 2) 장 시작 전(08:00) + 장 마감 후(16:00) KR 스캔
        #    (US는 yfinance 1,500종목 빌드가 수 분 걸려서 별도 분리)
        def _auto_stage2_scan():
            # 주말/공휴일 보조 가드
            wd = now_kst().weekday()
            if wd >= 5:
                log.debug("[자동 스캔] 주말 — 스킵")
                return
            with _discover_lock:
                if _discover_state["status"] in ("starting", "running"):
                    log.info("[자동 스캔] 이미 실행 중 — 스킵")
                    return
            log.info("⏰  자동 Stage 2 스캔 시작 (kr)")
            _stage2_scoring_worker("kr")
        _scheduler.add_job(_auto_stage2_scan, "cron",
                           day_of_week="mon-fri", hour="8,16", minute=0,
                           id="stage2_auto", max_instances=1)

        # ── AI 에이전트 파이프라인 (장 시작 전 08:45 + 장 마감 후 15:45) ──
        def _auto_agent_run():
            t0 = time.time()
            try:
                from agents.pipeline import run_pipeline, send_agent_telegram
                result = run_pipeline()
                send_agent_telegram(result)
                # 결과 요약 — picks 개수, 캐시 생성 확인
                picks_n = len((result or {}).get("final_picks") or [])
                cache_p = BASE_DIR / "cache" / "agent_result_latest.json"
                cache_exists = cache_p.exists()
                log.info("[Agent] 자동 실행 성공 (%.1fs) — picks=%d, cache=%s",
                         time.time() - t0, picks_n,
                         'OK' if cache_exists else 'MISSING')
            except Exception as exc:
                # 4-5: debug → warning 격상 (실패 흔적 보존)
                log.warning("[Agent] 자동 실행 실패 (%.1fs): %s",
                            time.time() - t0, exc)
        _scheduler.add_job(_auto_agent_run, "cron",
                           day_of_week="mon-fri", hour="8,15", minute=45,
                           id="agent_pipeline", max_instances=1)
        log.info("[Agent] 파이프라인 자동 실행 스케줄 등록 (08:45, 15:45)")

        # ── 데이터 유지보수 cron ──
        _scheduler.add_job(mark_etf_stocks, "cron",
                           hour=3, minute=10,
                           id="mark_etf_stocks", max_instances=1)
        _scheduler.add_job(generate_themes_mapping, "cron",
                           day_of_week="sun", hour=3, minute=20,
                           id="gen_themes_mapping", max_instances=1)
        _scheduler.add_job(refresh_us_universe_if_stale, "cron",
                           day_of_week="sun", hour=4, minute=0,
                           id="refresh_us_universe", max_instances=1)
        log.info("[Maint] ETF 마킹/테마 매핑/US 유니버스 자동화 등록")

        # KBJ P4 삭제: DART 공시 폴링·corp_map 갱신 등록 — kbj filings.dart_feed·filings.corp_code

        # ── Render 슬립 방지: 자체 핑 4분 간격 ──
        _scheduler.add_job(_self_keep_alive, "interval", minutes=4,
                           id="self_keepalive", max_instances=1)
        log.info("[Keep-Alive] 자체 핑 스케줄 등록 (4분 간격)")

        # ── US 시장 캐시 → DB 동기화 (US 장중·장외 모두 1시간 간격) ──
        _scheduler.add_job(sync_us_market_to_db_from_cache, "interval",
                           minutes=60, id="us_db_sync", max_instances=1)
        log.info("[US sync] 60분 간격 DB 동기화 스케줄 등록")

        # ── 글로벌 데이터 자동 갱신 (텔레그램 토큰 무관) — 4시간 간격 ──
        # macro_data / night_futures / options_signal_SPY,QQQ / fear_greed / valuechain heat
        _scheduler.add_job(_refresh_global_data_periodic, "interval",
                           hours=4, id="global_data_refresh", max_instances=1)
        log.info("[자동갱신] 4시간 간격 글로벌 데이터 cron 등록")

        # ── 매일 KR 종목 유니버스 빌드 (08:00 — 장 시작 1시간 전) ──
        def _daily_naver_universe_build():
            try:
                _build_naver_universe_background()
            except Exception as exc:
                log.warning("[KR universe] daily build: %s", exc)
        _scheduler.add_job(_daily_naver_universe_build, "cron",
                           hour=8, minute=0, id="kr_universe_daily",
                           max_instances=1)
        log.info("[KR universe] 매일 08:00 자동 빌드 cron 등록")

        # ── 매일 US 시장 데이터 빌드 (05:50 KST — US 장마감 05:00 KST(DST) 직후) ──
        # 06:10 텔레그램 발송 전에 데이터가 갖춰지도록 빌드를 앞당김.
        def _daily_us_market_build():
            try:
                _fetch_us_market_data(force=True)
                # 빌드 후 즉시 DB 동기화
                sync_us_market_to_db_from_cache()
            except Exception as exc:
                log.warning("[US market] daily build: %s", exc)
        _scheduler.add_job(_daily_us_market_build, "cron",
                           day_of_week="tue-sat", hour=5, minute=50,
                           id="us_market_daily", max_instances=1)
        log.info("[US market] 화~토 05:50 자동 빌드 + DB 동기화 cron 등록")

        # ── DB 핵심 테이블 백업 (매시 30분) ──
        def _hourly_db_backup():
            try:
                from db_backup import backup_db as _bk
                r = _bk()
                if r.get("ok"):
                    log.info("[DB백업] OK gist=%s, %dKB",
                             r.get("gist_id"), r.get("size_kb"))
                else:
                    log.debug("[DB백업] skip: %s", r.get("reason"))
            except Exception as exc:
                log.debug("[DB백업] %s", exc)
        _scheduler.add_job(_hourly_db_backup, "cron", minute=30,
                           id="db_backup_hourly", max_instances=1)
        log.info("[DB백업] 매시 30분 스케줄 등록")

        # ── Step 4-7-B: 유니버스 자동 갱신 (매월 1일 03:00) ──
        def _scheduled_universe_sync():
            try:
                from universe_manager import sync_valuechain_to_universe
                result = sync_valuechain_to_universe(verbose=False)
                log.info("[유니버스] %s 활성 %d (+%d/-%d)",
                         result.get('source'),
                         result.get('total_active', 0),
                         result.get('newly_added', 0),
                         result.get('removed', 0))
                # 변동 있을 때만 텔레그램 알림
                if result.get('newly_added', 0) > 0 or result.get('removed', 0) > 0:
                    try:
                        msg = (f"📊 유니버스 갱신\n"
                               f"활성: {result['total_active']}종목\n"
                               f"➕ 추가: {result['newly_added']}\n"
                               f"➖ 제거: {result['removed']}")
                        send_telegram(msg)
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("[유니버스 동기화] %s", exc)
        _scheduler.add_job(_scheduled_universe_sync, "cron",
                           day=1, hour=3, minute=0,
                           id="universe_sync_monthly", max_instances=1)
        log.info("[유니버스] 매월 1일 03:00 동기화 cron 등록")

        # KBJ P4 삭제: 분기 컨센서스·어닝 파이프라인·어닝 백필·컨센서스 스냅샷 등록 — kbj consensus.snapshot·earnings.alerts·earnings.backfill(등록부)

        _scheduler.start()
        log.info("APScheduler 시작 — %d분 간격", interval)
    else:
        log.info("APScheduler 미설치 — 자동 갱신 비활성  (pip install apscheduler)")

    # KBJ P4 삭제: WebSocket 가격 브로드캐스터(_price_broadcaster) — 페이지 1·2 장중 갱신(P3)


# ─────────────────────────────────────────────────────────────────────────
# 실시간 가격 동기화 — naver_universe + stocks 테이블 갱신
# ─────────────────────────────────────────────────────────────────────────

# 이름에 들어 있으면 ETF/ETN 으로 본다. SQLite `LIKE '%p%'` 라 ASCII 는 대소문자를
# 가리지 않고 **이름 어디에 있어도** 걸린다. 그래서 운용사 브랜드는 뒤에 공백을
# 붙여 적는다 — ETF 는 `BNK 주주가치액티브` 처럼 브랜드 뒤가 띄어져 있고, 같은
# 글자로 시작하는 회사는 붙여 쓴다(`BNK금융지주` · `HK이노엔` · `파워로직스`).
#
# 2026-09-23 전 종목 4,063개(data/naver_universe_seed.json)로 잰 값:
#  - 'BNK' 가 BNK금융지주(138930, 은행 지주사)를 ETF 로 찍고 있었다 → 'BNK '.
#    BNK 운용 ETF 5개는 전부 공백이 있어 하나도 놓치지 않는다.
#  - 브랜드 14개가 빠져 ETF 101개가 표식 없이 남아 있었다(KIWOOM 200 · TIME
#    코스피액티브 · KoAct … ). 아래 두 번째 묶음이 그것이다.
#  - 고친 뒤 4,063개 중 1,255개가 ETF 로 잡히고 새 오탐은 0개다. 표지가 있는데
#    안 잡히는 것은 신한글로벌액티브리츠(481850) 하나 — ETF 가 아니라 상장
#    리츠(부동산투자회사)라 맞게 남는다.
# 브랜드를 더할 때는 scripts/check_etf_marking.py 를 돌려 오탐을 먼저 본다.
ETF_PATTERNS = (
    'KODEX', 'TIGER', 'KBSTAR', 'KOSEF', 'HANARO',
    'ARIRANG', 'KINDEX', 'TREX', 'ACE ', 'SOL ',
    ' ETF', ' ETN', 'TRF', '레버리지', '인버스', '선물',
    'TIMEFOLIO', 'BNK ', 'FOCUS', 'WON ', 'SMART',
    'PLUS ', 'RISE ', 'WOORI',
    # 2026-09-23 전 종목 대조로 찾은 누락 브랜드
    '1Q ', 'DAISHIN343 ', 'HK ', 'KCGI ', 'KIWOOM ', 'KoAct ', 'MIDAS ',
    'TIME ', 'TRUSTON ', 'UNICORN ', 'VITA ', '마이티 ', '에셋플러스 ', '파워 ',
)


def _is_etf_name(name: str) -> bool:
    """`mark_etf_stocks` 와 같은 판정을 이름 하나에. DB 없이 거를 때 쓴다.

    SQLite `LIKE '%p%'` 와 맞춘다 — ASCII 는 대소문자를 가리지 않고 이름
    어디에 있어도 걸린다. 일봉 채움이 시드(is_etf 표식이 없다)에서 대상을
    고를 때 ETF 를 빼는 데 쓴다.
    """
    n = (name or "").lower()
    return any(p.lower() in n for p in ETF_PATTERNS)


def mark_etf_stocks():
    """ETF/ETN 종목 자동 마킹. 매일 03:10 cron."""
    if not (_SQLITE_OK and USE_SQLITE):
        return 0
    with _get_db() as conn:
        conn.execute("UPDATE stocks SET is_etf = 0")
        for p in ETF_PATTERNS:
            conn.execute("UPDATE stocks SET is_etf = 1 WHERE name LIKE ?", (f'%{p}%',))
        cnt = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_etf = 1").fetchone()[0]
        conn.commit()
    log.info("[ETF 마킹] %d종목", cnt)
    return cnt


def generate_themes_mapping(force: bool = False):
    """DB 섹터 기반 themes_mapping.json 생성.
    force=False(기본): 파일 없을 때만 생성 (큐레이티드 파일 보존).
    force=True: 덮어쓰기 (자동 cron은 사용 안 함).
    """
    path_check = BASE_DIR / "themes_mapping.json"
    if not force and path_check.exists() and path_check.stat().st_size > 100:
        log.info("[테마 매핑] 기존 큐레이티드 파일 존재 — 스킵 (force=True로 덮어쓰기)")
        return 0
    if not (_SQLITE_OK and USE_SQLITE):
        return 0
    with _get_db() as conn:
        sectors = conn.execute("""
            SELECT sector, COUNT(*) AS cnt
            FROM stocks
            WHERE (market = '' OR market LIKE 'KOS%')
              AND COALESCE(is_etf, 0) = 0
              AND sector IS NOT NULL AND sector != ''
            GROUP BY sector HAVING cnt >= 3
            ORDER BY cnt DESC
        """).fetchall()

        themes_list = []
        tid = 1
        for s in sectors:
            sect_name = s["sector"]
            top = conn.execute("""
                SELECT code, name FROM stocks
                WHERE sector = ? AND (market = '' OR market LIKE 'KOS%')
                  AND COALESCE(is_etf, 0) = 0
                ORDER BY COALESCE(market_cap, 0) DESC, volume_mn DESC LIMIT 15
            """, (sect_name,)).fetchall()
            stocks_list = [{"code": r["code"], "name": r["name"]} for r in top]
            if stocks_list:
                themes_list.append({
                    "id": tid, "name": sect_name, "stocks": stocks_list,
                })
                tid += 1

    path = BASE_DIR / "themes_mapping.json"
    if path.exists():
        try:
            import shutil
            shutil.copy(str(path), str(path) + ".bak")
        except Exception:
            pass
    path.write_text(json.dumps(themes_list, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("[테마 매핑] %d개 섹터 자동 생성", len(themes_list))
    return len(themes_list)


def refresh_us_universe_if_stale(max_days: int = 7):
    """US 유니버스가 N일 이상 오래됐으면 재수집. 일요일 04:00 cron."""
    if not (_SQLITE_OK and USE_SQLITE):
        return False
    try:
        with _get_db() as conn:
            r = conn.execute(
                "SELECT MAX(updated_at) AS upd FROM stocks WHERE market='US' OR market LIKE 'NASD%' OR market LIKE 'NYS%'"
            ).fetchone()
        if r and r["upd"]:
            from datetime import datetime as _dt
            last = _dt.fromisoformat(r["upd"])
            age_days = (now_kst().replace(tzinfo=None) - last).total_seconds() / 86400
            if age_days < max_days:
                log.info("[US Universe] 최근 갱신 %.1f일 전 — 스킵", age_days)
                return False
    except Exception:
        pass
    # 재빌드 트리거
    try:
        (BASE_DIR / "cache" / "sp500_tickers.json").unlink(missing_ok=True)
        _fetch_us_market_data(force=True)
        log.info("[US Universe] 재수집 완료")
        return True
    except Exception as exc:
        log.warning("[US Universe] 재수집 실패: %s", exc)
        return False


# ── data.json 서버 생성 ────────────────────────────────────────────────────
# 여태 data.json 은 맥북 cron(scripts/daily_macbook_cron.sh)이 매일 18:00 에
# 만들어 git push 하면 Render 가 자동 배포로 받아 가는 구조였다. 그 cron 이
# 멈추면 아무도 모른 채 파일이 굳는다 — 실제로 2026-09-15 에 멈췄고 2주간
# 아무 신호가 없었다.
#
# 맥북 파이프라인(data_fetcher.py)을 그대로 옮기지는 않는다. 그쪽은 종목마다
# pykrx 를 한 번씩 부르는데, pykrx 1.2.x 는 KRX 계정 로그인을 요구하고
# (website/comm/auth.py) 수백 회 호출이라 Render 무료 플랜에서 감당이 안 된다.
#
# 대신 **서버가 이미 쓰고 있는 소스**로 같은 스키마를 만든다. 전부 지금
# 돌아가는 경로다: naver_universe(등락률·거래대금), ohlcv 테이블(스파크라인),
# polling 지수 API, yfinance(해외지수).
#
# 영속성: Render 는 영속 디스크가 없어 재시작하면 런타임에 쓴 data.json 이
# 사라지고 git 에 있는 판으로 되돌아간다. 그래서 부팅 직후에 한 번 만들고
# 이후 스케줄로 갱신한다. git push 는 하지 않는다.

_DATA_JSON_SPARK_DAYS = 20

# 빌드를 직렬화한다. 부팅 스레드와 수동 재생성 요청이 겹치면 같은 .tmp 를
# 두 스레드가 쓰고 각자 replace 해서 어느 쪽 결과가 남는지 알 수 없게 된다.
_DATA_JSON_LOCK = threading.Lock()
# 지금 도는 빌드가 언제 시작했는지. 멈춘 빌드를 밖에서 알아보려면 필요하다.
_DATA_JSON_BUILD_STARTED: float | None = None


def _run_with_budget(fn, seconds: float, label: str, default=None):
    """fn() 을 별도 스레드에서 돌리고 제한 시간을 넘기면 포기한다.

    **왜 필요한가** — 2026-09-29 실측에서 data.json 빌드 하나가 12분 넘게
    끝나지 않고 락을 쥐고 있었다(status 가 building=True 를 계속 돌려줬다).
    입력은 멀쩡했다(매핑 171종목 중 162종목 시세 확보, 기준 102).
    단계 하나가 매달리면 전체가 인질이 되고, 그 뒤 예약 생성(15:45·16:20)
    까지 전부 막힌다. 한 조각이 느리다고 나머지를 포기할 이유가 없다.

    파이썬은 스레드를 죽일 수 없다. 넘긴 스레드는 그대로 두고(데몬이라
    프로세스를 붙잡지 않는다) 호출만 돌아온다.
    """
    box: dict = {}

    def _work():
        try:
            box["v"] = fn()
        except Exception as exc:          # noqa: BLE001
            box["e"] = exc

    th = threading.Thread(target=_work, daemon=True, name=f"budget-{label}")
    th.start()
    th.join(seconds)
    if th.is_alive():
        _note_collect_error("data_json", f"{label} {seconds:.0f}초 초과 — 건너뛴다")
        return default, False
    if "e" in box:
        _note_collect_error("data_json", f"{label} 실패: {box['e']}")
        return default, False
    return box.get("v", default), True


def _spark_from_ohlcv(codes: list[str], days: int = _DATA_JSON_SPARK_DAYS) -> dict:
    """ohlcv 테이블에서 종목별 최근 종가를 첫날=100 으로 정규화."""
    out: dict = {}
    if not (_SQLITE_OK and USE_SQLITE) or not codes:
        return out
    try:
        with _get_db() as conn:
            for code in codes:
                rows = conn.execute(
                    "SELECT close FROM ohlcv WHERE code=? AND close > 0 "
                    "ORDER BY date DESC LIMIT ?", (code, days)).fetchall()
                if len(rows) < 2:
                    continue
                closes = [float(r["close"]) for r in rows][::-1]  # 오래된 → 최신
                base = closes[0]
                if base <= 0:
                    continue
                out[code] = [round(c / base * 100, 2) for c in closes]
    except Exception as exc:
        _note_collect_error("data_json", f"스파크라인 조회 실패: {exc}")
    return out


def _rank_history_push(themes_out: list) -> list:
    """현재 순위를 히스토리에 적재하고 rank_change 를 붙인다.
    data_fetcher.save_ranking_history / apply_rank_changes 와 같은 규칙·같은 파일."""
    rf = BASE_DIR / "cache" / "ranking_history.json"
    now_str = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    current = {t["name"]: i + 1 for i, t in enumerate(
        sorted(themes_out, key=lambda x: abs(x["weighted_avg_pct"]), reverse=True))}

    history: list = []
    if rf.exists():
        try:
            loaded = json.loads(rf.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
            elif isinstance(loaded, dict) and "current" in loaded:
                history = [{"timestamp": now_str, "ranking": loaded["current"]}]
        except Exception:
            pass
    history.append({"timestamp": now_str, "ranking": current})
    history = history[-288:]
    try:
        rf.parent.mkdir(exist_ok=True)
        rf.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    prev = history[-2]["ranking"] if len(history) >= 2 else {}
    for t in themes_out:
        c, p = current.get(t["name"]), prev.get(t["name"])
        t["rank_change"] = (p - c) if (p and c) else 0
    return themes_out


_YF_LOCK = threading.Lock()


def _yf(retries: int = 3, delay: float = 1.5):
    """완전히 초기화된 yfinance 모듈을 돌려준다. 못 얻으면 None.

    스레드 여러 개가 같은 모듈을 **처음** import 하는 순간이 겹치면 파이썬이
    반쯤 만들어진 모듈 객체를 돌려준다:
      partially initialized module 'yfinance' has no attribute 'Ticker'
      (most likely due to a circular import)
    2026-09-29 배포 직후 실제로 이게 나서 data.json 의 market_overview 가
    통째로 비었다 — 부팅 스레드들이 yfinance 를 물어오는 동안 재생성 요청이
    같은 import 에 올라탔다. 예외가 아니라 값이 빈 것이라 조용히 지나갔다.

    lock 으로 첫 import 를 직렬화하고, 그래도 반쯤 된 객체가 오면 잠깐 뒤
    다시 본다. 한 번 제대로 올라오면 이후 import 는 캐시 조회라 공짜다.
    """
    for i in range(retries):
        with _YF_LOCK:
            try:
                import yfinance as yf
                if hasattr(yf, "Ticker"):
                    return yf
            except Exception as exc:
                if i == retries - 1:
                    _note_collect_error("yfinance", f"import 실패: {exc}")
                    return None
        time.sleep(delay)
    _note_collect_error("yfinance", "반쯤 초기화된 모듈만 반복 관측 — 포기")
    return None


def _warm_yfinance():
    """부팅 직후 yfinance 를 한 번 제대로 올려 둔다.
    이후 곳곳의 `import yfinance` 는 캐시 조회가 되어 경합이 사라진다."""
    try:
        _yf(retries=5, delay=2.0)
    except Exception:
        pass


def _build_market_overview() -> dict:
    """해외 지수·환율. data_fetcher 의 fetch_market_overview 와 같은 4개 키."""
    out: dict = {}
    try:
        rows, errs = _fetch_us_indices_live(["S&P 500", "NASDAQ", "나스닥100 선물"])
        keymap = {"S&P 500": "sp500", "NASDAQ": "nasdaq", "나스닥100 선물": "nasdaq_futures"}
        for r in rows:
            name, value, chg = r[0], r[1], r[2]
            if name in keymap and value:
                out[keymap[name]] = {"value": round(float(value), 2),
                                     "change_pct": round(float(chg), 2)}
        for e in errs or []:
            _note_collect_error("data_json", f"해외지수: {e}")
    except Exception as exc:
        _note_collect_error("data_json", f"해외지수 실패: {exc}")

    # USD/KRW 는 US_INDEX_TICKERS 에 없어 따로 받는다.
    try:
        _m = _yf()
        if _m is None:
            raise RuntimeError("yfinance 사용 불가")
        h = _m.Ticker("KRW=X").history(period="5d", interval="1d", auto_adjust=False)
        if h is not None and not h.empty:
            cl = h["Close"].dropna().tolist()
            if cl:
                cur = float(cl[-1])
                prv = float(cl[-2]) if len(cl) >= 2 else 0.0
                out["usd_krw"] = {
                    "value": round(cur, 2),
                    "change_pct": round((cur / prv - 1) * 100, 2) if prv else 0.0,
                }
    except Exception as exc:
        _note_collect_error("data_json", f"USD/KRW 실패: {exc}")
    return out


def _build_new_high_sectors(mapping: list, themes_out: list) -> list:
    """신고가 종목이 5개 이상인 테마. 서버의 신고가 캐시를 재사용한다.
    캐시가 없으면 빈 리스트 — 없는 것을 지어내지 않는다."""
    cf = BASE_DIR / "cache" / f"new_highs_kr_{_get_trading_date()}.json"
    data = _read_fresh_json(cf, 1440)
    items = (data or {}).get("items") or []
    if not items:
        return []
    hit = {str(i.get("code")) for i in items if i.get("code")}
    out = []
    for t in mapping:
        codes = {(s["code"] if isinstance(s, dict) else s) for s in t.get("stocks", [])}
        n = len(codes & hit)
        if n >= 5:
            out.append({"id": t.get("id"), "name": t.get("name"), "count": n})
    return sorted(out, key=lambda x: -x["count"])


def _universe_live_count(umap: dict | None = None) -> int:
    """유니버스에서 **오늘 시세가 들어온** 종목 수.

    _load_naver_universe() 는 스크랩 캐시가 없으면 커밋된 시드
    (data/naver_universe_seed.json)로 폴백한다. 시드에는 name·sectors·
    market_cap 만 있고 change_pct·volume_mn 이 아예 없다 —
    '종목이 있다' 와 '시세가 있다' 는 다른 얘기다.
    이걸 구분하지 않으면 부팅 직후 시드를 보고 data.json 을 만들다가
    거래대금 0 때문에 테마가 전부 걸러져 0개가 나온다.
    """
    if umap is None:
        umap = (_load_naver_universe() or {}).get("stocks") or {}
    return sum(1 for v in umap.values() if float(v.get("volume_mn") or 0) > 0)


def _build_data_json(write: bool = True) -> dict:
    """data.json 을 서버에서 만든다. 맥북 cron + git push 를 대신한다.

    실패해도 **기존 파일을 망가뜨리지 않는다**. 테마를 하나도 못 만들면
    쓰지 않고 그대로 둔다 — 낡은 데이터가 빈 데이터보다 낫다.
    """
    # **락을 오래 기다리지 않는다.** 예전엔 120초를 기다렸는데, 호출이 몇 개만
    # 겹쳐도 서로가 서로를 막아 전부 타임아웃으로 끝났다(2026-09-29 실측에서
    # 재현). 진행 중이면 그렇게 말하고 바로 돌아가는 편이 훨씬 낫다.
    global _DATA_JSON_BUILD_STARTED
    if not _DATA_JSON_LOCK.acquire(timeout=5):
        held = (time.time() - (_DATA_JSON_BUILD_STARTED or time.time()))
        return {"ok": False, "error": "다른 생성이 진행 중", "busy": True,
                "running_sec": round(held, 1)}
    _DATA_JSON_BUILD_STARTED = time.time()
    try:
        return _build_data_json_inner(write)
    finally:
        _DATA_JSON_BUILD_STARTED = None
        _DATA_JSON_LOCK.release()


def _build_data_json_inner(write: bool = True) -> dict:
    t0 = time.time()
    mapping_file = BASE_DIR / "themes_mapping.json"
    if not mapping_file.exists():
        _note_collect_error("data_json", "themes_mapping.json 없음 — 생성 불가")
        return {"ok": False, "error": "themes_mapping.json 없음"}
    try:
        mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _note_collect_error("data_json", f"themes_mapping.json 파싱 실패: {exc}")
        return {"ok": False, "error": f"매핑 파싱 실패: {exc}"}

    uni = _load_naver_universe()
    umap = (uni or {}).get("stocks") or {}
    if not umap:
        _note_collect_error("data_json", "naver_universe 비어 있음 — 가격 sync 전이다")
        return {"ok": False, "error": "naver_universe 없음"}

    live = _universe_live_count(umap)
    if live < 100:
        # 시드만 올라온 상태다. 여기서 만들면 거래대금 0 때문에 테마가 전부
        # 걸러져 빈 파일이 된다. 만들지 않고 물러난다.
        _note_collect_error(
            "data_json",
            f"유니버스에 시세가 없다 (시세 있는 종목 {live}개) — 가격 sync 전이다. 생성 보류")
        return {"ok": False, "error": f"시세 없는 유니버스 (live={live})",
                "live_stocks": live}

    def _code(it):
        return it["code"] if isinstance(it, dict) else it

    all_codes = list({_code(s) for t in mapping for s in t.get("stocks", [])})

    # **전체 유니버스가 아니라 이 매핑의 종목에 시세가 있는지**를 본다.
    # 부팅 직후엔 가격 sync 가 4063종목을 배치로 훑는 중이라 일부만 채워진다.
    # 그 상태로 만들면 거래대금 0인 테마가 통째로 걸러져 26개가 13개가 된다 —
    # 빈 파일은 아니지만 멀쩡한 파일을 열화된 것으로 덮는 셈이다.
    # 2026-09-29 배포 직후 실제로 그렇게 나왔다.
    mapped_live = sum(1 for c in all_codes
                      if float((umap.get(c) or {}).get("volume_mn") or 0) > 0)
    need = max(1, int(len(all_codes) * 0.6))
    if mapped_live < need:
        _note_collect_error(
            "data_json",
            f"매핑 종목 시세 부족 ({mapped_live}/{len(all_codes)}, 최소 {need}) "
            f"— 가격 sync 진행 중으로 보인다. 생성 보류")
        return {"ok": False,
                "error": f"매핑 종목 시세 부족 ({mapped_live}/{len(all_codes)})",
                "mapped_live": mapped_live, "mapped_total": len(all_codes)}

    # 각 단계에 예산을 건다. 하나가 매달려도 나머지는 나가야 한다.
    sparks, _ok_sp = _run_with_budget(
        lambda: _spark_from_ohlcv(all_codes), 60, "스파크라인", default={})
    sparks = sparks or {}

    themes_out: list = []
    for theme in mapping:
        stocks_out = []
        for item in theme.get("stocks", []):
            code = _code(item)
            u = umap.get(code) or {}
            name = (item.get("name") if isinstance(item, dict) else None) \
                or u.get("name") or _get_stock_name(code) or code
            stocks_out.append({
                "code": code,
                "name": name,
                "change_pct": round(float(u.get("change_pct") or 0.0), 2),
                "volume_mn": int(float(u.get("volume_mn") or 0)),
                "sparkline": sparks.get(code, []),
            })
        active = [s for s in stocks_out if s["volume_mn"] > 0]
        if not active:
            continue
        themes_out.append({
            "id": theme.get("id"),
            "name": theme.get("name"),
            "weighted_avg_pct": round(_weighted_avg_pct(stocks_out), 2),
            "stock_count": len(theme.get("stocks", [])),
            "active_count": len(active),
            "stocks": stocks_out,
        })

    if not themes_out:
        _note_collect_error("data_json", "테마 0개 — 기존 data.json 을 그대로 둔다")
        return {"ok": False, "error": "테마 0개"}

    # 마지막 안전망 — **멀쩡한 파일을 열화된 것으로 덮지 않는다.**
    # 위 시세 커버리지 검사를 빠져나온 경우에도, 기존 파일보다 테마가 크게
    # 줄었다면 뭔가 덜 채워진 것이다. 기존이 하루 넘게 낡았다면 그때는
    # 적은 테마라도 오늘 값이 낫다.
    if write:
        try:
            prev = json.loads(DATA_JSON.read_text(encoding="utf-8")) if DATA_JSON.exists() else {}
            prev_n = len(prev.get("themes") or [])
            age = _data_json_stale_min()
            if prev_n and len(themes_out) < prev_n * 0.7 and (age is not None and age < 24 * 60):
                _note_collect_error(
                    "data_json",
                    f"테마가 {prev_n}개 → {len(themes_out)}개로 줄어 생성 보류 "
                    f"(기존 파일은 {age:.0f}분 전 것이라 아직 쓸 만하다)")
                return {"ok": False,
                        "error": f"테마 감소 {prev_n}→{len(themes_out)} — 기존 유지",
                        "themes": len(themes_out), "prev_themes": prev_n}
        except Exception:
            pass   # 기존 파일을 못 읽으면 비교를 건너뛴다 — 새로 쓰는 게 낫다

    _rank_history_push(themes_out)
    idx, _ = _run_with_budget(_fetch_kr_indices_live, 20, "국내지수", default={})
    idx = idx or {}

    # 해외지수는 yfinance 네트워크라 제일 잘 매달린다. 12분 멈춤의 유력 용의자다.
    mkt, ok_mkt = _run_with_budget(_build_market_overview, 45, "해외지수", default={})
    if not mkt:
        # **빈 값을 그냥 내보내지 않는다**(2026-09-29 에 그렇게 조용히 비었다).
        # 직전 파일에 있던 값을 재사용하고, 재사용했다는 사실을 남긴다.
        try:
            if DATA_JSON.exists():
                prev_mkt = (json.loads(DATA_JSON.read_text(encoding="utf-8"))
                            .get("market_overview") or {})
                if prev_mkt:
                    mkt = prev_mkt
                    _note_collect_error(
                        "data_json", "해외지수 실패 — 직전 파일 값 재사용")
        except Exception:
            pass

    output = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "actual_date": _get_trading_date(),
        "kospi": idx.get("kospi", {"value": 0.0, "change_pct": 0.0}),
        "kosdaq": idx.get("kosdaq", {"value": 0.0, "change_pct": 0.0}),
        "themes": themes_out,
        "new_high_sectors": _build_new_high_sectors(mapping, themes_out),
        "market_overview": mkt,
        "source": "server",     # 맥북 cron 산출물과 구분된다
    }

    if write:
        try:
            tmp = DATA_JSON.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(DATA_JSON)   # 원자적 교체 — 반쯤 쓰인 파일을 남기지 않는다
        except Exception as exc:
            _note_collect_error("data_json", f"파일 쓰기 실패: {exc}")
            return {"ok": False, "error": f"쓰기 실패: {exc}"}

    n_spark = sum(1 for t in themes_out for s in t["stocks"] if s["sparkline"])
    n_stock = sum(len(t["stocks"]) for t in themes_out)
    log.info("[data.json] 생성 완료 — 테마 %d개, 종목 %d개, 스파크라인 %d개, %.1fs",
             len(themes_out), n_stock, n_spark, time.time() - t0)
    return {
        "ok": True, "themes": len(themes_out), "stocks": n_stock,
        "sparklines": n_spark, "actual_date": output["actual_date"],
        "market_overview_keys": sorted(output["market_overview"]),
        "new_high_sectors": len(output["new_high_sectors"]),
        "elapsed_sec": round(time.time() - t0, 1),
    }


def _weighted_avg_pct(stocks_list: list) -> float:
    """거래대금 가중 평균 등락률. data_fetcher.weighted_avg 와 같은 규칙."""
    total = sum(s.get("volume_mn") or 0 for s in stocks_list)
    if total <= 0:
        return 0.0
    return sum((s.get("change_pct") or 0.0) * (s.get("volume_mn") or 0)
               for s in stocks_list) / total


def _data_json_stale_min() -> float | None:
    """data.json 의 updated_at 경과(분). 못 읽으면 None."""
    if not DATA_JSON.exists():
        return None
    try:
        d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        upd = datetime.strptime(d.get("updated_at", ""), "%Y-%m-%d %H:%M:%S")
        return (now_kst().replace(tzinfo=None) - upd).total_seconds() / 60
    except Exception:
        return None


def _refresh_data_json_job():
    """스케줄 진입점. 장 마감 뒤 하루 한 번 + 부팅 직후."""
    res = _build_data_json()
    if not res.get("ok"):
        log.warning("[data.json] 생성 실패: %s", res.get("error"))
    return res


def _startup_data_json():
    """부팅 직후 1회. Render 는 재시작하면 git 에 있는 낡은 판으로 되돌아가므로
    여기서 한 번 덮어써야 그날 값이 올라온다.

    **시드가 아니라 실제 시세를 기다린다.** _load_naver_universe() 는 캐시가
    없으면 커밋된 시드로 폴백하는데 거기엔 거래대금이 없다. 종목 수만 보고
    출발하면 테마가 전부 걸러져 0개가 나오고, 그대로 끝나 버린다.
    """
    try:
        for i in range(60):                      # 최대 10분
            if _universe_live_count() >= 100:
                break
            if i == 12:
                # 2분이 지나도 시세가 없으면 가격 sync 를 직접 한 번 돌린다.
                # 부팅 시각이 장중 sync 사이 구간이면 다음 cron 까지 30분을
                # 기다리게 되는데, 그동안 화면은 낡은 data.json 을 보여 준다.
                log.info("[data.json] 시세 대기 2분 경과 — 가격 sync 를 직접 부른다")
                try:
                    _refresh_prices_from_naver()
                except Exception as exc:
                    log.warning("[data.json] 가격 sync 실패: %s", exc)
            time.sleep(10)
        else:
            _note_collect_error("data_json", "부팅 후 10분간 시세가 안 들어와 생성 보류")
            return

        age = _data_json_stale_min()
        if age is not None and age < 180:
            log.info("[data.json] 부팅 시점에 이미 신선함 (%.0f분 전) — 생성 생략", age)
            return
        _refresh_data_json_job()
    except Exception as exc:
        log.warning("[data.json] 부팅 생성 실패: %s", exc)


def _refresh_prices_from_naver():
    """
    네이버 실시간 API로 naver_universe 캐시 + SQLite stocks 가격 일괄 갱신.
    장마감 후 호출하면 종가 반영. 장중에도 호출 가능.
    """
    import urllib.request
    uni = _load_naver_universe()
    if not uni or not uni.get("stocks"):
        log.warning("[가격 갱신] naver_universe 없음")
        return 0

    stocks_map = uni["stocks"]
    codes = list(stocks_map.keys())
    updated = 0

    # 폴링이 시가총액(marketValueFullRaw)을 준 종목 / 안 준 종목. 이 둘의 비가
    # 곧 '오늘 시총을 아는 종목의 비율' 이라, 필드명이 바뀌거나 응답에서
    # 빠지면 _mv_ok 가 0 으로 떨어져 로그에 바로 드러난다.
    _mv_ok = _mv_miss = 0
    _fail = 0
    for i in range(0, len(codes), 100):
        batch = codes[i:i + 100]
        codes_str = ",".join(batch)
        data = None
        # 최대 2회 재시도
        for _attempt in range(2):
            try:
                url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{codes_str}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as exc:
                if _attempt == 1:
                    _fail += 1
                    log.warning("[가격 갱신] 배치 %d 실패 (%d 재시도 후): %s",
                                i, _attempt + 1, exc)
                    data = None
                else:
                    time.sleep(0.5)
        if data is None:
            continue
        try:
            datas_list = data.get("datas") or []
            _not_in_uni = 0; _zero_price = 0; _parse_err = 0; _ok = 0
            sample_miss = []

            for s in datas_list:
                code = s.get("itemCode")
                if not code or code not in stocks_map:
                    _not_in_uni += 1
                    if len(sample_miss) < 2: sample_miss.append(code)
                    continue
                try:
                    price = float(str(s.get("closePrice", "0")).replace(",", ""))
                    chg = float(str(s.get("fluctuationsRatio", "0")).replace(",", ""))
                    vol = int(float(str(s.get("accumulatedTradingVolume", "0")).replace(",", "")))
                except (ValueError, TypeError):
                    _parse_err += 1
                    continue
                if price <= 0:
                    _zero_price += 1
                    continue
                _ok += 1
                # naver_universe 메모리 캐시 갱신 (변경 여부 무관하게 항상 덮어쓰기)
                stocks_map[code]["close"] = price
                stocks_map[code]["change_pct"] = chg
                stocks_map[code]["volume"] = vol
                stocks_map[code]["volume_mn"] = int(vol * price / 1_000_000)
                # 시가총액 (marketValueFullRaw — 원 단위). 가격 sync 때 같이 갱신.
                #
                # **오늘 폴링이 준 값인지 표시해 둔다.** stocks_map 은 시드
                # (data/naver_universe_seed.json)에서 온 market_cap 을 이미 들고
                # 있다. 폴링이 시총을 안 주면 그 시드 값이 그대로 남는데, 여태
                # 아래 UPSERT 가 그것까지 '오늘 갱신' 으로 도장을 찍었다.
                # Render 는 영속 디스크가 없어 재시작마다 cache/ 가 비고 시드로
                # 되돌아가므로, 이 도장 하나 때문에 몇 달 묵은 시총이 매일
                # 오늘 값으로 되살아난다. 출처를 갈라 두면 그 일이 없다.
                mv_raw = s.get("marketValueFullRaw")
                try:
                    mv = int(str(mv_raw).replace(",", "")) if mv_raw else 0
                    if mv > 0:
                        stocks_map[code]["market_cap"] = mv
                        stocks_map[code]["market_cap_asof"] = now_kst().strftime("%Y%m%d")
                        _mv_ok += 1
                    else:
                        _mv_miss += 1
                except (ValueError, TypeError):
                    _mv_miss += 1

                # 시간외 단일가 (overMarketPriceInfo)
                # tradingSessionType=REGULAR_MARKET 은 정규장 데이터 → 시간외로 쓰지 않음
                over = s.get("overMarketPriceInfo") or {}
                sess = (over.get("tradingSessionType") or "").upper() if isinstance(over, dict) else ""
                is_real_after_hours = isinstance(over, dict) and over.get("overPrice") and \
                                      sess and sess != "REGULAR_MARKET"
                if is_real_after_hours:
                    try:
                        ap = float(str(over.get("overPrice", "0")).replace(",", ""))
                        apc = float(str(over.get("fluctuationsRatio", "0")).replace(",", ""))
                        stocks_map[code]["after_hours_price"] = ap
                        stocks_map[code]["after_hours_change_pct"] = apc
                        stocks_map[code]["after_hours_status"] = over.get("overMarketStatus")
                        stocks_map[code]["after_hours_time"] = over.get("localTradedAt")
                    except (ValueError, TypeError):
                        pass
                else:
                    # 정규장이거나 시간외 데이터 없음 → 이전 기록 초기화
                    stocks_map[code]["after_hours_price"] = None
                    stocks_map[code]["after_hours_change_pct"] = None
                    stocks_map[code]["after_hours_status"] = None
                    stocks_map[code]["after_hours_time"] = None

                updated += 1
            if i == 0:
                log.info("[가격 갱신] batch0 상세: ok=%d, not_in_uni=%d, zero=%d, parse_err=%d, sample_miss=%s",
                         _ok, _not_in_uni, _zero_price, _parse_err, sample_miss)
        except Exception as exc:
            log.warning("[가격 갱신] 배치 %d 파싱 실패: %s", i, exc)
        time.sleep(0.2)

    # 시총을 몇 종목에서 받았는지 한 줄로 남긴다. marketValueFullRaw 가
    # 응답에서 사라지거나 이름이 바뀌면 _mv_ok 가 0 이 되어 여기서 드러난다 —
    # 예전에는 그래도 시드 값이 오늘 날짜로 찍혀 나가 아무 흔적이 없었다.
    if _mv_ok == 0 and _mv_miss:
        log.warning("[가격 갱신] 시가총액을 한 종목도 못 받았다 (%d종목 시도) — "
                    "polling 응답에 marketValueFullRaw 가 없다. "
                    "DB 시총은 갱신되지 않고 기존 값이 그대로 남는다", _mv_miss)
    else:
        log.info("[가격 갱신] 시가총액 %d종목 수신 / %d종목 미수신", _mv_ok, _mv_miss)

    # naver_universe JSON 파일 덮어쓰기
    if updated > 0:
        # 네이버 실시간 API 기준 "실제 거래된 날짜"를 우선 사용 (pykrx 지연 영향 배제)
        today_kst = now_kst().strftime("%Y%m%d")
        out_file = BASE_DIR / "cache" / f"naver_universe_{today_kst}.json"
        uni["fetched_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
        try:
            out_file.write_text(json.dumps(uni, ensure_ascii=False), encoding="utf-8")
            # 메모리 캐시 무효화 → 다음 _load_naver_universe() 시 재로드
            _UNI_CACHE["mtime"] = 0
        except Exception as exc:
            log.debug("[가격 갱신] universe 저장 실패: %s", exc)

        # SQLite stocks 테이블도 갱신 (시간외 + 시가총액 포함)
        # market_cap 은 Naver 폴링 응답의 marketValueFullRaw(원 단위) 사용.
        # S-3-B: UPSERT — 빈 stocks 테이블도 universe 정보로 시드 (INSERT) +
        # 기존 행은 가격만 갱신 (UPDATE). Render 부팅 시 stocks=0 회복.
        #
        # **market_cap_updated 는 그 시총이 실제로 언제 것인지를 적는다.**
        # 예전에는 값이 0 만 아니면 무조건 오늘 날짜를 찍었다. 그래서 시드
        # (source_date 2026-06-02)에서 온 몇 달 묵은 시총이 매일 '오늘 갱신'
        # 으로 되살아났고, 시황 메시지는 오늘 등락률 옆에 6월 시총을 나란히
        # 실었다. 값이 틀렸다는 신호가 어디에도 없었던 것이 진짜 문제다.
        #
        # 그리고 **낡은 값이 새 값을 덮지 못하게** 한다. UPDATE 조건에
        # 날짜 비교를 넣었다 (YYYYMMDD 문자열이라 사전순 비교가 곧 날짜순).
        # 이게 없으면 Render 가 재시작해 시드로 되돌아간 순간, 어제 폴링으로
        # 받아 둔 제대로 된 시총을 6월 값이 덮어쓴다.
        if _SQLITE_OK and USE_SQLITE:
            try:
                # 시드에서 온 시총은 시드가 밝힌 날짜의 값이다. 모르면 NULL —
                # 모르는 것을 오늘로 적지 않는다. 오늘 폴링이 준 값의 날짜는
                # 수집 시점에 stocks_map['market_cap_asof'] 로 이미 박아 뒀다.
                seed_asof = _seed_cap_asof(uni)
                with _get_db() as conn:
                    before_cnt = conn.execute(
                        "SELECT COUNT(*) FROM stocks "
                        "WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
                    ).fetchone()[0]
                    for code, info in stocks_map.items():
                        mcap = info.get("market_cap") or 0
                        # 오늘 폴링이 준 값이면 오늘, 시드에서 온 값이면 시드 날짜.
                        cap_asof = info.get("market_cap_asof") or seed_asof
                        sectors = info.get("sectors") or []
                        sector = sectors[0] if sectors else ""
                        name = info.get("name") or code
                        conn.execute(
                            "INSERT INTO stocks "
                            "(code, name, market, sector, market_cap, market_cap_updated, "
                            " close, change_pct, volume_mn, sectors_json, "
                            " after_hours_price, after_hours_change_pct, "
                            " after_hours_status, after_hours_time, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now')) "
                            "ON CONFLICT(code) DO UPDATE SET "
                            "  close = excluded.close, "
                            "  change_pct = excluded.change_pct, "
                            "  volume_mn = excluded.volume_mn, "
                            # 새 값이 있고, 그 값이 DB 에 있는 것보다 오래되지
                            # 않았을 때만 바꾼다. 시총을 과거로 되돌리지 않는다.
                            "  market_cap = CASE WHEN excluded.market_cap > 0 "
                            "                    AND excluded.market_cap_updated IS NOT NULL "
                            "                    AND (stocks.market_cap_updated IS NULL "
                            "                         OR excluded.market_cap_updated "
                            "                            >= stocks.market_cap_updated) "
                            "                    THEN excluded.market_cap ELSE stocks.market_cap END, "
                            "  market_cap_updated = CASE WHEN excluded.market_cap > 0 "
                            "                    AND excluded.market_cap_updated IS NOT NULL "
                            "                    AND (stocks.market_cap_updated IS NULL "
                            "                         OR excluded.market_cap_updated "
                            "                            >= stocks.market_cap_updated) "
                            "                            THEN excluded.market_cap_updated "
                            "                            ELSE stocks.market_cap_updated END, "
                            "  after_hours_price = excluded.after_hours_price, "
                            "  after_hours_change_pct = excluded.after_hours_change_pct, "
                            "  after_hours_status = excluded.after_hours_status, "
                            "  after_hours_time = excluded.after_hours_time, "
                            "  updated_at = datetime('now')",
                            (code, name, "", sector,
                             mcap if mcap > 0 else None,
                             cap_asof if mcap > 0 else None,
                             info.get("close"), info.get("change_pct"),
                             info.get("volume_mn"),
                             json.dumps(sectors, ensure_ascii=False),
                             info.get("after_hours_price"),
                             info.get("after_hours_change_pct"),
                             info.get("after_hours_status"),
                             info.get("after_hours_time")),
                        )
                    conn.commit()
                    after_cnt = conn.execute(
                        "SELECT COUNT(*) FROM stocks "
                        "WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
                    ).fetchone()[0]
                inserted = after_cnt - before_cnt
                if inserted > 0:
                    log.info("[가격 갱신] stocks 신규 INSERT %d종목 (이전 %d → 현재 %d)",
                             inserted, before_cnt, after_cnt)
            except Exception as exc:
                log.warning("[가격 갱신] stocks DB UPSERT 실패: %s", exc)

    log.info("[가격 갱신] %d종목 반영, 실패 배치 %d개", updated, _fail)
    return updated


@app.route("/api/refresh_prices", methods=["POST"])
def api_refresh_prices():
    """가격 데이터 수동 갱신 (백그라운드 실행)."""
    def _bg():
        _refresh_prices_from_naver()
    threading.Thread(target=_bg, daemon=True, name="price-refresh").start()
    return jsonify({"ok": True, "message": "백그라운드 갱신 시작"})


# ── 일봉(ohlcv) 자동 채움 ──────────────────────────────────────────────────
def _ohlcv_ranked_codes() -> tuple[str, list]:
    """일봉 대상 후보를 `stocks` 표에서 뽑는다. (기준, [(시가총액 원, 코드)])

    ohlcv_autofill 은 server.py 를 import 할 수 없어(순환) 이 함수를 주입받는다.
    **ETF/ETN 은 여기서 뺀다.** 시총 하한(1,000억 판정 · 800억부터 받아 둠)은
    ohlcv_autofill.select_universe 한 곳에서 건다 — 기준이 두 곳에 흩어지면
    한쪽만 고쳐지는 날이 온다.

    **왜 유니버스가 아니라 이 표인가.** Render 무료 플랜은 cache/ 가 비영속이라
    재배포하면 `_load_naver_universe()` 가 커밋된 시드로 떨어진다. `stocks` 는
    부팅 직후 가격 갱신(`_boot_refresh_kr` → `_refresh_prices_from_naver`)이
    오늘 폴링 시총(원)으로 채운다. 시드 시총(몇 달 묵은 값)보다 그쪽이 낫다.

    ETF 는 is_etf 표식만 믿지 않고 **이름으로도 거른다**(`_is_etf_name`,
    mark_etf_stocks 와 같은 패턴). 03:10 cron 은 Render 가 자는 시각이라 거의
    안 돌고, 재배포로 새로 찬 행은 is_etf 가 기본값 0 이다 — 2026-09-22 시황에
    ETF 가 섞여 나간 이유다. 이 함수는 부팅 대기 중 15초마다 불리므로 표식을
    UPDATE 로 다시 붙이지 않고 읽기만 한다.
    """
    if not (_SQLITE_OK and USE_SQLITE):
        return "none", []
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT code, name, market_cap FROM stocks "
            "WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' "
            "  AND (market = '' OR market LIKE 'KOS%') "
            "  AND COALESCE(is_etf, 0) = 0 "
            "  AND COALESCE(market_cap, 0) > 0").fetchall()
    by_cap = [(float(r["market_cap"]), r["code"]) for r in rows
              if not _is_etf_name(r["name"])]
    if by_cap:
        return "market_cap", by_cap
    return "none", []


# 일봉 채움은 **한 번에 하나만** 돈다. 부르는 데가 넷이다 — 부팅 스레드,
# 16:10 잡, 시황이 데이터 미완일 때, 수동 API. 둘이 겹치면 같은 1,500종목을
# 동시에 네이버에 두 번 물어 차단을 부른다. 뒤엣것은 앞엣것이 끝날 때까지
# 기다렸다가, 이미 찬 종목은 증분 판정(ohlcv_autofill.plan)에 걸려 묻지도
# 않고 곧바로 돌아간다. 이 락이 잡혀 있는 동안은 시황도 기다린다
# (_brief_data_ready) — 채우다 만 일봉으로 신고가를 내지 않으려는 것이다.
_OHLCV_FILL_LOCK = threading.Lock()


def _fill_ohlcv_job(force: bool = False) -> dict:
    """`_fill_ohlcv_job_inner` 를 한 번에 하나만 돌게 감싼다."""
    if not _OHLCV_FILL_LOCK.acquire(timeout=900):
        log.warning("[일봉 채움] 다른 채움이 15분 넘게 돌고 있다 — 이번은 건너뛴다")
        return {"skipped": True, "reason": "다른 채움이 돌고 있다"}
    try:
        return _fill_ohlcv_job_inner(force)
    finally:
        _OHLCV_FILL_LOCK.release()


def _fill_ohlcv_job_inner(force: bool = False) -> dict:
    """일봉을 받아 ohlcv 를 채운다. 스케줄러(16:10)와 부팅 스레드가 부른다.

    수집 자체는 ohlcv_autofill 모듈이 한다 — 여기는 대상 후보·ETF 판정·최근
    거래일을 넘겨 주고 결과를 로그로 남기는 얇은 껍데기다. 예외를 올리지
    않는다(부르는 쪽이 데몬 스레드와 스케줄러라 죽으면 침묵이 된다).

    **건너뛰기는 종목마다 한다.** 예전에는 테이블 전체의 최신일 하나만 보고
    통째로 건너뛰었는데, 대상이 1,500종목이 되면 재배포 직후 채우다 끊긴 날
    (일부만 최신) 나머지를 영영 안 받는다. 지금은 `up_to` 를 넘겨 주고
    ohlcv_autofill.plan 이 종목마다 '최근 거래일까지 있으면 묻지 않음 / 모자라면
    자기 마지막 날부터 / 없으면 전 구간' 을 정한다. 부팅이 잦은 Render 에서도
    다 찬 종목에 다시 요청을 쓰지 않는다.

    기준을 '며칠 이내' 로 두면 안 된다 — 16:10 잡이 도는 시점에 각 종목의
    최신 날짜는 늘 전 거래일이라, 그런 기준이면 **매일 자기 자신을 건너뛰고
    오늘 봉이 영영 안 들어온다.** 기준은 `_get_trading_date()` 가 말하는 최근
    거래일이다. 주말·휴장에는 그 값이 금요일이므로 부팅 때 재수집이 도는 일도
    없다. `force=True` 면 가진 것을 무시하고 전 구간을 다시 받는다.
    """
    try:
        import ohlcv_autofill as _oa
    except Exception as exc:                               # noqa: BLE001
        log.error("[일봉 채움] 모듈 로드 실패: %s", exc)
        return {"error": f"import 실패: {exc}"}

    latest_needed = None
    if not force:
        try:
            td = _get_trading_date()                       # YYYYMMDD
            latest_needed = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
        except Exception:                                  # noqa: BLE001
            pass       # 거래일을 못 구하면 종목마다 마지막 날부터 다시 받는다

    try:
        return _oa.fill(load_universe=_load_naver_universe,
                        load_ranked=_ohlcv_ranked_codes, is_etf=_is_etf_name,
                        up_to=latest_needed, full=force, now=now_kst())
    except Exception as exc:                               # noqa: BLE001
        log.exception("[일봉 채움] 실패")
        return {"error": f"{type(exc).__name__}: {exc}"}


@app.route("/api/ops/ohlcv/fill", methods=["POST"])
def api_ops_ohlcv_fill():
    """일봉 수동 채움 (백그라운드). `?force=1` 이면 최신이어도 다시 받는다."""
    force = (request.args.get("force") or "").strip() in ("1", "true", "yes")
    threading.Thread(target=_fill_ohlcv_job, args=(force,),
                     daemon=True, name="ohlcv-fill").start()
    return jsonify({"ok": True, "message": "백그라운드 일봉 채움 시작",
                    "force": force})


# KBJ P2 삭제(설계 §5.10): api_ops_brief_closing — #43 재발송은 P3 운영 화면(인증·강제 키)


@app.route("/api/ops/ohlcv/status")
def api_ops_ohlcv_status():
    """ohlcv 현황 — 행 수·종목 수·구간. 비었는지 한눈에 본다."""
    try:
        import ohlcv_autofill as _oa
        return jsonify(_oa.status())
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/after_hours/<code>")
def api_after_hours(code: str):
    """단일 종목의 시간외 단일가 조회."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT code, name, close, change_pct, "
                "after_hours_price, after_hours_change_pct, "
                "after_hours_status, after_hours_time "
                "FROM stocks WHERE code = ?", (code,)
            ).fetchone()
            if not row:
                return jsonify({"error": "종목 없음"}), 404
            d = dict(row)
            return jsonify({
                "code": d["code"],
                "name": d["name"],
                "close": d["close"],
                "change_pct": d["change_pct"],
                "after_hours": {
                    "price": d.get("after_hours_price"),
                    "change_pct": d.get("after_hours_change_pct"),
                    "status": d.get("after_hours_status"),
                    "traded_at": d.get("after_hours_time"),
                } if d.get("after_hours_price") else None,
            })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────
# PHASE 15 — 종목 발굴 스코어링 (Stage 1 MVP: 국내, 모멘텀 + 섹터)
# ─────────────────────────────────────────────────────────────────────────
def _load_ticker_sparklines_kr() -> dict:
    """오늘자 ticker_data 캐시에서 sparklines 맵만 추출 ({code: [20 normalized prices]})."""
    today = _get_trading_date()
    f = BASE_DIR / "cache" / f"ticker_data_{today}.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data.get("sparklines") or {}
    except Exception:
        return {}


def _load_naver_sector_aggregates() -> list:
    """sectors_naver_landing.json 의 sectors 배열 반환 (stale 허용)."""
    f = BASE_DIR / "cache" / "sectors_naver_landing.json"
    if not f.exists():
        return []
    try:
        return (json.loads(f.read_text(encoding="utf-8")) or {}).get("sectors", [])
    except Exception:
        return []


def _calc_momentum_score_kr(stock: dict, rank_change: int, rank_vol: int,
                            total: int, sparkline: list) -> tuple[int, dict, list]:
    """
    모멘텀 점수 (0~20). 반환: (score, sub_dict, explanation_list).
    서브: 등락률 순위(5) + 5d(5) + 20d(5) + 거래대금 순위(5).
    """
    sub = {"chg_rank": 0, "ret_5d": 0, "ret_20d": 0, "vol_rank": 0}
    expl: list = []

    if total > 0:
        pct = (rank_change / total) * 100
        sub["chg_rank"] = max(0, 5 - int(pct / 10))
        chg = stock.get("change_pct") or 0
        expl.append({
            "label":  "당일 등락률 순위",
            "detail": f"{chg:+.2f}% · 전체 중 상위 {pct:.0f}%",
            "pts":    sub["chg_rank"], "max": 5,
        })
    else:
        expl.append({"label": "당일 등락률 순위", "detail": "데이터 없음", "pts": 0, "max": 5})

    if sparkline and len(sparkline) >= 5 and sparkline[-5]:
        r5 = (sparkline[-1] / sparkline[-5] - 1) * 100
        if   r5 > 10: sub["ret_5d"] = 5
        elif r5 >  5: sub["ret_5d"] = 4
        elif r5 >  2: sub["ret_5d"] = 3
        elif r5 >  0: sub["ret_5d"] = 2
        elif r5 > -2: sub["ret_5d"] = 1
        expl.append({
            "label":  "5일 수익률",
            "detail": f"{r5:+.2f}%",
            "pts":    sub["ret_5d"], "max": 5,
        })
    else:
        expl.append({"label": "5일 수익률", "detail": "데이터 없음", "pts": 0, "max": 5})

    if sparkline and len(sparkline) >= 20 and sparkline[0]:
        r20 = (sparkline[-1] / sparkline[0] - 1) * 100
        if   r20 > 20: sub["ret_20d"] = 5
        elif r20 > 10: sub["ret_20d"] = 4
        elif r20 >  5: sub["ret_20d"] = 3
        elif r20 >  0: sub["ret_20d"] = 2
        elif r20 > -5: sub["ret_20d"] = 1
        expl.append({
            "label":  "20일 수익률",
            "detail": f"{r20:+.2f}%",
            "pts":    sub["ret_20d"], "max": 5,
        })
    else:
        expl.append({"label": "20일 수익률", "detail": "데이터 없음", "pts": 0, "max": 5})

    if total > 0:
        vpct = (rank_vol / total) * 100
        sub["vol_rank"] = max(0, 5 - int(vpct / 10))
        expl.append({
            "label":  "거래대금 순위",
            "detail": f"상위 {vpct:.0f}% · {(stock.get('volume_mn') or 0):,}백만원",
            "pts":    sub["vol_rank"], "max": 5,
        })

    # v2 보너스: 볼밴 수축 (sparkline 변동성 축소 → 돌파 임박)
    if sparkline and len(sparkline) >= 20:
        try:
            mean_all = sum(sparkline) / len(sparkline)
            std_all = (sum((v - mean_all) ** 2 for v in sparkline) / len(sparkline)) ** 0.5
            recent5 = sparkline[-5:]
            mean5 = sum(recent5) / 5
            std5 = (sum((v - mean5) ** 2 for v in recent5) / 5) ** 0.5
            if std_all > 0 and std5 < std_all * 0.6:
                sub["squeeze"] = 3
                expl.append({
                    "label": "볼밴 수축 보너스",
                    "detail": f"5일 변동성 {std5/std_all*100:.0f}% (돌파 임박 가능)",
                    "pts": 3, "max": 3,
                })
        except Exception:
            pass

    # v2 보너스: 돌파 임박 (20일 박스권 <10% + 상단 95% 근접)
    if sparkline and len(sparkline) >= 20:
        try:
            hi20 = max(sparkline[-20:])
            lo20 = min(sparkline[-20:])
            avg20 = sum(sparkline[-20:]) / 20
            box_pct = (hi20 - lo20) / avg20 * 100 if avg20 > 0 else 999
            near_top = sparkline[-1] >= hi20 * 0.95
            if box_pct < 10 and near_top:
                sub["breakout"] = 5
                expl.append({
                    "label": "돌파 임박 보너스",
                    "detail": f"20일 박스 {box_pct:.1f}% + 상단 근접",
                    "pts": 5, "max": 5,
                })
        except Exception:
            pass

    return sum(sub.values()), sub, expl


def _calc_sector_score_kr(stock: dict, sector_by_name: dict,
                          sector_rank_by_name: dict, total_sectors: int) -> tuple[int, dict, list]:
    """섹터 점수 (0~15): 섹터 등락률 순위(5) + 절대 수준(5) + up/total 비율(5)."""
    sub = {"sect_rank": 0, "sect_level": 0, "sect_up_ratio": 0}
    expl: list = []
    names = stock.get("sectors") or []
    if not names or not sector_by_name:
        expl.append({"label": "섹터 정보", "detail": "섹터 집계 없음", "pts": 0, "max": 15})
        return 0, sub, expl

    best_score = -1
    best_sub = sub
    best_expl: list = []
    for name in names:
        info = sector_by_name.get(name)
        if not info:
            continue
        s = {"sect_rank": 0, "sect_level": 0, "sect_up_ratio": 0}
        e: list = []

        if total_sectors:
            rk = sector_rank_by_name.get(name, total_sectors)
            pct = (rk / total_sectors) * 100
            s["sect_rank"] = max(0, 5 - int(pct / 10))
            e.append({
                "label":  "섹터 등락률 순위",
                "detail": f"{name} · 전체 {total_sectors}개 섹터 중 상위 {pct:.0f}%",
                "pts":    s["sect_rank"], "max": 5,
            })

        chg = info.get("change_pct") or 0
        if   chg >  3: s["sect_level"] = 5
        elif chg >  2: s["sect_level"] = 4
        elif chg >  1: s["sect_level"] = 3
        elif chg >  0: s["sect_level"] = 2
        elif chg > -1: s["sect_level"] = 1
        e.append({
            "label":  "섹터 당일 등락률",
            "detail": f"{chg:+.2f}%",
            "pts":    s["sect_level"], "max": 5,
        })

        up = info.get("up") or 0
        tot = info.get("total") or 0
        if tot:
            ratio = up / tot
            if   ratio > 0.8: s["sect_up_ratio"] = 5
            elif ratio > 0.6: s["sect_up_ratio"] = 4
            elif ratio > 0.5: s["sect_up_ratio"] = 3
            elif ratio > 0.4: s["sect_up_ratio"] = 2
            elif ratio > 0.3: s["sect_up_ratio"] = 1
            e.append({
                "label":  "섹터 내 상승 비율",
                "detail": f"{up}/{tot} 종목 상승 ({ratio*100:.0f}%)",
                "pts":    s["sect_up_ratio"], "max": 5,
            })

        total_s = sum(s.values())
        if total_s > best_score:
            best_score = total_s
            best_sub = s
            best_expl = e

    if best_score < 0:
        return 0, sub, [{"label": "섹터 정보", "detail": "매칭된 섹터 없음", "pts": 0, "max": 15}]
    return best_score, best_sub, best_expl


def _calc_momentum_score_us(stock: dict, rank_change: int, rank_vol: int,
                            total: int) -> tuple[int, dict, list]:
    """
    US 모멘텀 (0~20). 이력 없이 당일 지표만으로 구성.
    서브: 등락률 순위(5) + 거래대금 순위(5) + 등락률 절대 수준(5) + 거래대금 절대 수준(5).
    """
    sub = {"chg_rank": 0, "vol_rank": 0, "chg_level": 0, "vol_level": 0}
    expl: list = []

    if total > 0:
        pct = (rank_change / total) * 100
        sub["chg_rank"] = max(0, 5 - int(pct / 10))
        chg = stock.get("change_pct") or 0
        expl.append({
            "label":  "당일 등락률 순위",
            "detail": f"{chg:+.2f}% · S&P500 내 상위 {pct:.0f}%",
            "pts":    sub["chg_rank"], "max": 5,
        })

    chg = stock.get("change_pct") or 0
    if   chg >  5: sub["chg_level"] = 5
    elif chg >  3: sub["chg_level"] = 4
    elif chg >  2: sub["chg_level"] = 3
    elif chg >  1: sub["chg_level"] = 2
    elif chg >  0: sub["chg_level"] = 1
    expl.append({
        "label":  "등락률 절대 수준",
        "detail": f"{chg:+.2f}%",
        "pts":    sub["chg_level"], "max": 5,
    })

    if total > 0:
        vpct = (rank_vol / total) * 100
        sub["vol_rank"] = max(0, 5 - int(vpct / 10))
        expl.append({
            "label":  "거래대금 순위",
            "detail": f"상위 {vpct:.0f}% · ${(stock.get('volume_mn') or 0):,.0f}M",
            "pts":    sub["vol_rank"], "max": 5,
        })

    vol_mn = stock.get("volume_mn") or 0
    if   vol_mn > 10_000: sub["vol_level"] = 5
    elif vol_mn >  5_000: sub["vol_level"] = 4
    elif vol_mn >  2_000: sub["vol_level"] = 3
    elif vol_mn >  1_000: sub["vol_level"] = 2
    elif vol_mn >    500: sub["vol_level"] = 1
    expl.append({
        "label":  "거래대금 절대 수준",
        "detail": f"${vol_mn:,.0f}M",
        "pts":    sub["vol_level"], "max": 5,
    })

    return sum(sub.values()), sub, expl


def _calc_sector_score_us(stock: dict, sector_by_name: dict,
                          sector_rank_by_name: dict, total_sectors: int) -> tuple[int, dict, list]:
    """US 섹터 점수 (0~15). us_market.json 의 sectors 구조 기준."""
    sub = {"sect_rank": 0, "sect_level": 0, "sect_up_ratio": 0}
    expl: list = []
    name = stock.get("sector") or ""
    info = sector_by_name.get(name)
    if not info:
        expl.append({"label": "섹터", "detail": "섹터 미매칭", "pts": 0, "max": 15})
        return 0, sub, expl

    if total_sectors:
        rk = sector_rank_by_name.get(name, total_sectors)
        pct = (rk / total_sectors) * 100
        sub["sect_rank"] = max(0, 5 - int(pct / 10))
        expl.append({
            "label":  "섹터 등락률 순위",
            "detail": f"{name} · GICS {total_sectors}개 섹터 중 상위 {pct:.0f}%",
            "pts":    sub["sect_rank"], "max": 5,
        })

    chg = info.get("weighted_avg_pct") or 0
    if   chg >  3: sub["sect_level"] = 5
    elif chg >  2: sub["sect_level"] = 4
    elif chg >  1: sub["sect_level"] = 3
    elif chg >  0: sub["sect_level"] = 2
    elif chg > -1: sub["sect_level"] = 1
    expl.append({
        "label":  "섹터 당일 등락률",
        "detail": f"{chg:+.2f}% (가중평균)",
        "pts":    sub["sect_level"], "max": 5,
    })

    stocks_in = info.get("stocks") or []
    if stocks_in:
        up = sum(1 for s in stocks_in if (s.get("change_pct") or 0) > 0)
        tot = len(stocks_in)
        ratio = up / tot
        if   ratio > 0.8: sub["sect_up_ratio"] = 5
        elif ratio > 0.6: sub["sect_up_ratio"] = 4
        elif ratio > 0.5: sub["sect_up_ratio"] = 3
        elif ratio > 0.4: sub["sect_up_ratio"] = 2
        elif ratio > 0.3: sub["sect_up_ratio"] = 1
        expl.append({
            "label":  "섹터 내 상승 비율",
            "detail": f"{up}/{tot} 종목 상승 ({ratio*100:.0f}%)",
            "pts":    sub["sect_up_ratio"], "max": 5,
        })

    return sum(sub.values()), sub, expl


def _calc_flow_score_us(info: dict) -> tuple[int, dict, list]:
    """US 수급 점수 (0~25): 기관 보유(10) + 내부자 보유(5) + 애널리스트 의견(10)."""
    sub = {"institutions": 0, "insiders": 0, "analyst": 0}
    expl: list = []
    if not info:
        expl.append({"label": "yfinance info", "detail": "데이터 없음", "pts": 0, "max": 25})
        return 0, sub, expl

    inst = info.get("heldPercentInstitutions")
    if inst is not None:
        if   inst >= 0.90: sub["institutions"] = 10
        elif inst >= 0.80: sub["institutions"] = 8
        elif inst >= 0.70: sub["institutions"] = 6
        elif inst >= 0.50: sub["institutions"] = 4
        elif inst >= 0.30: sub["institutions"] = 2
        expl.append({
            "label":  "기관 보유 비중",
            "detail": f"{inst*100:.1f}%",
            "pts":    sub["institutions"], "max": 10,
        })
    else:
        expl.append({"label": "기관 보유 비중", "detail": "데이터 없음", "pts": 0, "max": 10})

    ins = info.get("heldPercentInsiders")
    if ins is not None:
        if   ins >= 0.10: sub["insiders"] = 5
        elif ins >= 0.05: sub["insiders"] = 3
        elif ins >= 0.01: sub["insiders"] = 1
        expl.append({
            "label":  "내부자 보유 비중",
            "detail": f"{ins*100:.2f}%",
            "pts":    sub["insiders"], "max": 5,
        })
    else:
        expl.append({"label": "내부자 보유 비중", "detail": "데이터 없음", "pts": 0, "max": 5})

    rec = info.get("recommendationKey")
    rec_map = {"strong_buy": 10, "buy": 8, "hold": 4, "sell": 0, "strong_sell": 0}
    if rec:
        sub["analyst"] = rec_map.get(str(rec).lower(), 0)
        n_analysts = info.get("numberOfAnalystOpinions")
        detail = str(rec).replace("_", " ").title()
        if n_analysts:
            detail += f" ({n_analysts}명)"
        expl.append({
            "label":  "애널리스트 의견",
            "detail": detail,
            "pts":    sub["analyst"], "max": 10,
        })
    else:
        expl.append({"label": "애널리스트 의견", "detail": "데이터 없음", "pts": 0, "max": 10})

    return sum(sub.values()), sub, expl


def _calc_valuation_score_us(info: dict, high_52w: float | None,
                             current_price: float | None) -> tuple[int, dict, list]:
    """US 밸류 (0~20): Trailing PER(7) + fwd<trail 보너스(3) + PBR(5) + 52w 괴리(5)."""
    sub = {"per": 0, "fwd_bonus": 0, "pbr": 0, "high_gap": 0}
    expl: list = []
    if not info:
        expl.append({"label": "yfinance info", "detail": "데이터 없음", "pts": 0, "max": 20})
        return 0, sub, expl

    tpe = info.get("trailingPE")
    fpe = info.get("forwardPE")

    if tpe and tpe > 0:
        if   tpe < 10: sub["per"] = 7
        elif tpe < 15: sub["per"] = 5
        elif tpe < 20: sub["per"] = 3
        elif tpe < 25: sub["per"] = 1
        expl.append({
            "label":  "Trailing PER",
            "detail": f"PER {tpe:.2f}",
            "pts":    sub["per"], "max": 7,
        })
    else:
        expl.append({"label": "Trailing PER", "detail": "미제공 또는 적자", "pts": 0, "max": 7})

    if tpe and fpe and tpe > 0 and fpe > 0 and fpe < tpe:
        sub["fwd_bonus"] = 3
        expl.append({
            "label":  "Forward vs Trailing",
            "detail": f"Forward {fpe:.2f} < Trailing {tpe:.2f} (실적 개선 기대)",
            "pts":    3, "max": 3,
        })
    else:
        expl.append({
            "label":  "Forward vs Trailing",
            "detail": "개선 기대 없음 또는 데이터 부족",
            "pts":    0, "max": 3,
        })

    pbr = info.get("priceToBook")
    if pbr and pbr > 0:
        if   pbr < 1: sub["pbr"] = 5
        elif pbr < 2: sub["pbr"] = 3
        elif pbr < 3: sub["pbr"] = 1
        expl.append({
            "label":  "PBR",
            "detail": f"PBR {pbr:.2f}",
            "pts":    sub["pbr"], "max": 5,
        })
    else:
        expl.append({"label": "PBR", "detail": "미제공", "pts": 0, "max": 5})

    if high_52w and current_price and high_52w > 0:
        gap = (high_52w - current_price) / high_52w * 100
        if   gap > 40: sub["high_gap"] = 5
        elif gap > 30: sub["high_gap"] = 4
        elif gap > 20: sub["high_gap"] = 3
        elif gap > 10: sub["high_gap"] = 2
        expl.append({
            "label":  "52주 고점 대비",
            "detail": f"-{gap:.1f}% · 고점 ${high_52w:.2f}",
            "pts":    sub["high_gap"], "max": 5,
        })
    else:
        expl.append({"label": "52주 고점 대비", "detail": "데이터 없음", "pts": 0, "max": 5})

    return sum(sub.values()), sub, expl


def _stage1_prefilter_us(min_volume_mn: float = 1, top_k: int = 1500) -> dict:
    """미국 S&P 1500 에서 모멘텀+섹터 점수로 상위 top_k 추출 (캐시 데이터만 사용)."""
    us_data = _fetch_us_market_data()
    if "error" in us_data:
        return {"error": us_data["error"], "items": [], "total_scanned": 0}

    all_stocks = us_data.get("all_stocks") or []
    sectors = us_data.get("sectors") or []
    eligible = [s for s in all_stocks if (s.get("volume_mn") or 0) >= min_volume_mn]
    total = len(eligible)
    if not total:
        return {"error": "US 조건 통과 종목 없음", "items": [], "total_scanned": 0}

    # 랭킹
    sorted_by_chg = sorted(eligible, key=lambda x: x.get("change_pct") or 0, reverse=True)
    rank_change = {s["symbol"]: i for i, s in enumerate(sorted_by_chg)}
    sorted_by_vol = sorted(eligible, key=lambda x: x.get("volume_mn") or 0, reverse=True)
    rank_vol = {s["symbol"]: i for i, s in enumerate(sorted_by_vol)}

    sector_by_name = {x["name"]: x for x in sectors}
    sorted_sectors = sorted(sectors, key=lambda x: x.get("weighted_avg_pct") or 0, reverse=True)
    sector_rank_by_name = {x["name"]: i for i, x in enumerate(sorted_sectors)}

    results = []
    for s in eligible:
        sym = s["symbol"]
        mom, mom_sub, mom_expl = _calc_momentum_score_us(
            s, rank_change[sym], rank_vol[sym], total
        )
        sect, sect_sub, sect_expl = _calc_sector_score_us(
            s, sector_by_name, sector_rank_by_name, len(sectors)
        )
        results.append({
            "code":       sym,
            "name":       s.get("name"),
            "market":     "us",
            "sector":     s.get("sector"),
            "price":      s.get("price"),
            "change_pct": s.get("change_pct"),
            "volume_mn":  s.get("volume_mn"),
            "total_score": mom + sect,
            "scores": {
                "momentum": mom,
                "sector":   sect,
                "flow":     None,
                "valuation": None,
                "technical": None,
                "undervalued_bonus": 0,
            },
            "sub_scores":   {"momentum": mom_sub,  "sector": sect_sub},
            "explanations": {"momentum": mom_expl, "sector": sect_expl},
        })

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return {
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "us",
        "stage":         1,
        "total_scanned": total,
        "items":         results[:top_k],
    }


def _stage1_prefilter_kr(min_volume_mn: int = 1, top_k: int = 2600) -> dict:
    """국내 전 종목에서 모멘텀+섹터 점수로 상위 top_k 추출.
    v3: 거래대금 1백만 이상 전종목 커버 (~3,500). KOSPI/KOSDAQ 분리 선발.
    """
    universe = _load_naver_universe()
    stocks_map = (universe or {}).get("stocks") or {}
    if not stocks_map:
        return {"error": "naver_universe 캐시 없음", "items": [], "total_scanned": 0}

    sparklines = _load_ticker_sparklines_kr()
    sectors = _load_naver_sector_aggregates()

    # KRX 마켓 분류 로드 (KOSPI/KOSDAQ 분리용)
    krx_stocks = _get_krx_all_stocks_cached()

    # 거래대금 필터 적용 후 남은 종목만 랭킹
    eligible = [s for s in stocks_map.values()
                if (s.get("volume_mn") or 0) >= min_volume_mn]

    # ETF/ETN/펀드/선물 제외: DB is_etf 컬럼 우선, 없으면 이름 패턴
    etf_codes: set = set()
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as _conn:
                rows = _conn.execute(
                    "SELECT code FROM stocks WHERE is_etf = 1"
                ).fetchall()
                etf_codes = {r["code"] for r in rows}
        except Exception:
            etf_codes = set()
    _ETF_PATTERNS = (
        'KODEX', 'TIGER', 'KBSTAR', 'KOSEF', 'HANARO',
        'ARIRANG', 'KINDEX', 'TREX', 'ACE ', 'SOL ',
        ' ETF', ' ETN', 'TRF', '레버리지', '인버스', '선물',
        'TIMEFOLIO', 'BNK', 'FOCUS', 'WON ', 'SMART',
        'PLUS ', 'RISE ', 'WOORI',
    )
    def _is_etf(s):
        if s.get("code") in etf_codes:
            return True
        name = s.get("name", "")
        return any(p in name for p in _ETF_PATTERNS)
    before_etf = len(eligible)
    eligible = [s for s in eligible if not _is_etf(s)]
    log.info("[Stage1 KR] ETF 제외: %d → %d (DB: %d, 패턴 보완)",
             before_etf, len(eligible), len(etf_codes))

    total = len(eligible)
    if not total:
        return {"error": "조건 통과 종목 없음", "items": [], "total_scanned": 0}

    sorted_by_chg = sorted(eligible, key=lambda x: x.get("change_pct") or 0, reverse=True)
    rank_change = {s["code"]: i for i, s in enumerate(sorted_by_chg)}
    sorted_by_vol = sorted(eligible, key=lambda x: x.get("volume_mn") or 0, reverse=True)
    rank_vol = {s["code"]: i for i, s in enumerate(sorted_by_vol)}

    # 섹터 랭킹 (|change_pct| 내림차순)
    sector_by_name = {x["name"]: x for x in sectors}
    sorted_sectors = sorted(sectors, key=lambda x: abs(x.get("change_pct") or 0), reverse=True)
    sector_rank_by_name = {x["name"]: i for i, x in enumerate(sorted_sectors)}

    results = []
    for s in eligible:
        code = s["code"]
        spark = sparklines.get(code) or []
        mom, mom_sub, mom_expl = _calc_momentum_score_kr(
            s, rank_change[code], rank_vol[code], total, spark
        )
        sect, sect_sub, sect_expl = _calc_sector_score_kr(
            s, sector_by_name, sector_rank_by_name, len(sectors)
        )
        # KRX 마켓 분류
        krx_row = krx_stocks.get(code) or {}
        mkt = krx_row.get("MKT_NM") or krx_row.get("mktNm") or ""
        if "KOSPI" in mkt.upper() or "유가증권" in mkt:
            mkt_label = "KOSPI"
        elif "KOSDAQ" in mkt.upper() or "코스닥" in mkt:
            mkt_label = "KOSDAQ"
        else:
            mkt_label = ""

        # KRX 시가총액 (있으면 활용)
        mkt_cap = _krx_get_float(krx_row, "MKTCAP", "mktCap")

        results.append({
            "code": code,
            "name": s.get("name"),
            "market": "kr",
            "market_type": mkt_label,
            "market_cap": mkt_cap,
            "sector": (s.get("sectors") or [None])[0],
            "price": s.get("close"),
            "change_pct": s.get("change_pct"),
            "volume_mn": s.get("volume_mn"),
            "total_score": mom + sect,
            "scores": {
                "momentum": mom,
                "sector": sect,
                "flow": None,
                "valuation": None,
                "technical": None,
                "undervalued_bonus": 0,
            },
            "sub_scores":   {"momentum": mom_sub,  "sector": sect_sub},
            "explanations": {"momentum": mom_expl, "sector": sect_expl},
        })

    results.sort(key=lambda x: x["total_score"], reverse=True)

    # ── KOSPI/KOSDAQ 분리 선발 (v2) ──
    kospi_items = [r for r in results if r.get("market_type") == "KOSPI"]
    kosdaq_items = [r for r in results if r.get("market_type") == "KOSDAQ"]
    unknown_items = [r for r in results if not r.get("market_type")]

    half = top_k // 2  # 1300

    if kospi_items and kosdaq_items:
        # KOSPI 200 + KOSDAQ 200 분리 선발
        selected = kospi_items[:half] + kosdaq_items[:half]
        # 잔여 슬롯을 나머지에서 보충
        selected_codes = {r["code"] for r in selected}
        remainder = [r for r in results if r["code"] not in selected_codes]
        fill = top_k - len(selected)
        if fill > 0:
            selected += remainder[:fill]
        # 다시 score 순 정렬
        selected.sort(key=lambda x: x["total_score"], reverse=True)
        log.info("Stage 1 KR v2: KOSPI %d + KOSDAQ %d + 보충 %d = %d",
                 min(len(kospi_items), half), min(len(kosdaq_items), half),
                 max(0, fill), len(selected))
    else:
        # KRX 데이터 없으면 통합 상위 top_k
        selected = results[:top_k]
        log.info("Stage 1 KR v2: KRX 마켓 분류 없음, 통합 상위 %d", len(selected))

    # 분류 카운트: KRX 분류 불가 시 None (프론트가 'null' 체크)
    kospi_n = len(kospi_items) if kospi_items else None
    kosdaq_n = len(kosdaq_items) if kosdaq_items else None

    return {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market": "kr",
        "stage": 1,
        "total_scanned": total,
        "kospi_count": kospi_n,
        "kosdaq_count": kosdaq_n,
        "unclassified_count": sum(1 for r in results if not r.get("market_type")),
        "items": selected,
    }


def _read_fresh_json(path, ttl_min: float) -> dict | None:
    if not path.exists():
        return None
    try:
        age_min = (now_kst().timestamp() - path.stat().st_mtime) / 60
        if age_min < ttl_min:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────
# PHASE 17 — 리서치: 섹터/기업 리포트 수집 + 추천 스코어링
# ─────────────────────────────────────────────────────────────────────────
_BUY_WORDS  = {"매수", "buy", "strong buy", "outperform", "비중확대", "trading buy"}
_HOLD_WORDS = {"중립", "hold", "neutral", "시장수익률", "marketperform"}
_SELL_WORDS = {"매도", "sell", "strong sell", "underperform", "비중축소"}


# KBJ P4 삭제: _scrape_naver_research_list — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


# KBJ P4 삭제: api_research_sectors — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


# KBJ P4 삭제: api_research_companies — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


def _kr_current_prices() -> dict:
    """naver_universe 에서 {code: close} 맵 구성."""
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    return {code: (s.get("close") or 0) for code, s in stocks.items() if s.get("close")}


# KBJ P4 삭제: api_research_recommend — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


@app.route("/api/research/us_recommend")
def api_research_us_recommend():
    """Finnhub 애널리스트 추천 트렌드 + 목표가. 상위 50 심볼. 6시간 캐시."""
    finn = os.getenv("FINNHUB_API_KEY")
    if not finn:
        return jsonify({"error": "FINNHUB_API_KEY 미설정", "items": []}), 503

    cache_file = BASE_DIR / "cache" / "us_research_recommend.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    sp500_syms, sp500_names = _load_sp500_symbols()
    if not sp500_syms:
        return jsonify({"error": "S&P500 리스트 없음", "items": []}), 503

    # 시총 상위는 sp500_tickers.json 순서상 앞쪽에 있지 않으므로 모두 시도
    symbols = sorted(sp500_syms)[:50]
    try:
        import requests as _rq
    except ImportError:
        return jsonify({"error": "requests 미설치"}), 500

    results = []
    for sym in symbols:
        try:
            r1 = _rq.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": sym, "token": finn}, timeout=5,
            )
            data = r1.json() if r1.status_code == 200 else []
            if not isinstance(data, list) or not data:
                continue
            latest = data[0]
            buy = (latest.get("buy") or 0) + (latest.get("strongBuy") or 0)
            hold = latest.get("hold") or 0
            sell = (latest.get("sell") or 0) + (latest.get("strongSell") or 0)
            total = buy + hold + sell
            if total == 0:
                continue
            buy_ratio = round(buy / total * 100)

            prev = data[1] if len(data) > 1 else {}
            prev_buy = (prev.get("buy") or 0) + (prev.get("strongBuy") or 0)
            prev_total = prev_buy + (prev.get("hold") or 0) + (prev.get("sell") or 0) + (prev.get("strongSell") or 0)
            prev_ratio = round(prev_buy / prev_total * 100) if prev_total > 0 else buy_ratio
            direction = "up" if buy_ratio > prev_ratio else "down" if buy_ratio < prev_ratio else "flat"

            # 목표가 (실패해도 계속)
            target_mean = target_high = target_low = None
            try:
                r2 = _rq.get(
                    "https://finnhub.io/api/v1/stock/price-target",
                    params={"symbol": sym, "token": finn}, timeout=5,
                )
                if r2.status_code == 200:
                    pt = r2.json() or {}
                    target_mean = pt.get("targetMean")
                    target_high = pt.get("targetHigh")
                    target_low  = pt.get("targetLow")
            except Exception:
                pass

            score = 0
            expl = []
            if   buy_ratio >= 80: score += 40; expl.append(f"매수비율 {buy_ratio}% (강한 공감대)")
            elif buy_ratio >= 60: score += 30; expl.append(f"매수비율 {buy_ratio}%")
            elif buy_ratio >= 40: score += 20; expl.append(f"매수비율 {buy_ratio}%")
            else:                 score += 10; expl.append(f"매수비율 {buy_ratio}%")
            if   direction == "up":   score += 20; expl.append(f"추세 상승 ({prev_ratio}% → {buy_ratio}%)")
            elif direction == "flat": score += 10; expl.append("추세 유지")
            else:                     expl.append(f"추세 하락 ({prev_ratio}% → {buy_ratio}%)")

            results.append({
                "code":            sym,
                "symbol":          sym,
                "name":            sp500_names.get(sym, sym),
                "market":          "us",
                "score":           score,
                "explanation":     expl,
                "buy":             buy, "hold": hold, "sell": sell,
                "buy_ratio":       buy_ratio,
                "prev_buy_ratio":  prev_ratio,
                "direction":       direction,
                "target_mean":     target_mean,
                "target_high":     target_high,
                "target_low":      target_low,
                "period":          latest.get("period", ""),
            })
            time.sleep(0.05)
        except Exception as exc:
            log.debug("finnhub rec %s fail: %s", sym, exc)
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(results),
        "items":      results[:20],
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 16 — 경제지표 + 실적발표 캘린더 (Finnhub / DART)
# ─────────────────────────────────────────────────────────────────────────
_COUNTRY_KR = {
    "US": "🇺🇸 미국", "KR": "🇰🇷 한국", "CN": "🇨🇳 중국",
    "JP": "🇯🇵 일본", "EU": "🇪🇺 유럽", "GB": "🇬🇧 영국", "DE": "🇩🇪 독일",
}
_EVENT_KR = {
    "CPI MoM": "소비자물가지수(CPI) 전월대비",
    "CPI YoY": "소비자물가지수(CPI) 전년대비",
    "Core CPI MoM": "근원 CPI 전월대비",
    "Core CPI YoY": "근원 CPI 전년대비",
    "GDP Growth Rate QoQ": "GDP 성장률 전분기대비",
    "GDP Growth Rate YoY": "GDP 성장률 전년대비",
    "Unemployment Rate": "실업률",
    "Non Farm Payrolls": "비농업 고용",
    "Interest Rate Decision": "기준금리 결정",
    "Retail Sales MoM": "소매판매 전월대비",
    "PPI MoM": "생산자물가지수 전월대비",
    "PPI YoY": "생산자물가지수 전년대비",
    "ISM Manufacturing PMI": "ISM 제조업 PMI",
    "ISM Services PMI": "ISM 서비스업 PMI",
    "Consumer Confidence": "소비자신뢰지수",
    "Initial Jobless Claims": "신규 실업수당청구건수",
    "Trade Balance": "무역수지",
    "Industrial Production MoM": "산업생산 전월대비",
    "Industrial Production YoY": "산업생산 전년대비",
    "Manufacturing PMI": "제조업 PMI",
    "Services PMI": "서비스업 PMI",
    "Housing Starts": "주택착공건수",
    "Building Permits": "건축허가건수",
    "Durable Goods Orders": "내구재 주문",
    "Fed Interest Rate Decision": "Fed 금리 결정",
    "BOJ Interest Rate Decision": "BOJ 금리 결정",
    "ECB Interest Rate Decision": "ECB 금리 결정",
    "BOK Interest Rate Decision": "한국은행 기준금리 결정",
}


def _load_sp500_symbols() -> tuple[set, dict]:
    """S&P500 심볼 세트 + {symbol: name} 맵. _sp500_tickers 캐시 재사용."""
    cache_file = BASE_DIR / "cache" / "sp500_tickers.json"
    if not cache_file.exists():
        return set(), {}
    try:
        tickers = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return set(), {}
    if not isinstance(tickers, list):
        return set(), {}
    syms = set()
    names = {}
    for t in tickers:
        sym = t.get("symbol")
        if sym:
            syms.add(sym)
            names[sym] = t.get("name") or sym
    return syms, names


def _calendar_date_range() -> tuple[str, str]:
    """이번 주 월요일 ~ 다음 주 금요일 (2주)."""
    today = now_kst().date()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=11)
    return monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")


def _extract_econ_date(event: dict) -> str:
    """Finnhub 이벤트의 time 필드('2026-04-13 00:00:00')에서 날짜만 추출."""
    t = event.get("time") or ""
    if isinstance(t, str) and len(t) >= 10 and t[4] == "-":
        return t[:10]
    return ""


# ─────────────────────────────────────────────────────────────────────────
# PHASE 22 — 섹터 로테이션 / 동종비교 / 매크로
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/sector_rotation")
def api_sector_rotation():
    """
    KR 업종별 1주(5d) / 1개월(20d) 수익률. pykrx 지수 API 차단 상태이므로
    ticker_data sparkline + naver_universe 섹터로 거래대금 가중평균 집계.
    6시간 캐시.
    """
    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"sector_rotation_{today}.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    sparklines = _load_ticker_sparklines_kr()
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    if not sparklines or not stocks:
        return jsonify({"error": "캐시 데이터 부족", "sectors": []}), 503

    sectors_raw: dict[str, list[dict]] = {}
    for code, st in stocks.items():
        sp = sparklines.get(code)
        if not sp or len(sp) < 20 or not sp[0]:
            continue
        sec_list = st.get("sectors") or []
        if not sec_list:
            continue
        sector = sec_list[0]
        try:
            ret_5d  = (sp[-1] / sp[-5] - 1) * 100 if sp[-5] else None
            ret_20d = (sp[-1] / sp[0]  - 1) * 100
        except Exception:
            continue
        sectors_raw.setdefault(sector, []).append({
            "code":      code,
            "volume_mn": st.get("volume_mn") or 0,
            "chg_today": st.get("change_pct") or 0,
            "ret_5d":    ret_5d,
            "ret_20d":   ret_20d,
        })

    def _weighted_avg(rows: list, key: str) -> float | None:
        vals = [r for r in rows if r.get(key) is not None]
        if not vals:
            return None
        total_w = sum(r["volume_mn"] for r in vals) or 0
        if total_w > 0:
            return round(
                sum((r[key] or 0) * (r["volume_mn"] or 0) for r in vals) / total_w, 2
            )
        return round(sum((r[key] or 0) for r in vals) / len(vals), 2)

    sectors_out: list[dict] = []
    for name, rows in sectors_raw.items():
        if len(rows) < 2:
            continue
        sectors_out.append({
            "name":         name,
            "stock_count":  len(rows),
            "ret_1w":       _weighted_avg(rows, "ret_5d"),
            "ret_1m":       _weighted_avg(rows, "ret_20d"),
            "change_today": _weighted_avg(rows, "chg_today"),
        })
    sectors_out.sort(key=lambda x: (x.get("ret_1m") or 0), reverse=True)

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(sectors_out),
        "sectors":    sectors_out,
        "note":       "1주=5영업일, 1개월=20영업일 sparkline 가중평균. 3개월 데이터 없음.",
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/peers/<code>")
def api_peers_kr(code: str):
    """KR 동종 업계 비교. naver_universe 같은 섹터 → 거래대금 상위 20, 12h 캐시."""
    if not re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400
    cache_file = BASE_DIR / "cache" / f"peers_{code}.json"
    cached = _read_fresh_json(cache_file, 720)
    if cached:
        return jsonify(cached)

    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    target = stocks.get(code)
    if not target:
        return jsonify({"error": "종목 유니버스에 없음", "peers": []}), 404

    target_sector = (target.get("sectors") or [None])[0]
    if not target_sector:
        return jsonify({"error": "섹터 정보 없음", "peers": []}), 404

    same_sector = []
    for c, s in stocks.items():
        if (s.get("sectors") or [None])[0] != target_sector:
            continue
        same_sector.append({
            "code":       c,
            "name":       s.get("name"),
            "price":      s.get("close"),
            "change_pct": s.get("change_pct"),
            "volume_mn":  s.get("volume_mn") or 0,
        })
    same_sector.sort(key=lambda x: x["volume_mn"], reverse=True)
    top_peers = same_sector[:20]

    # 기존 cache/financial_*.json 만 재사용 (신규 호출 없음)
    for p in top_peers:
        fin_cache = BASE_DIR / "cache" / f"financial_{p['code']}.json"
        p["per"] = None
        p["pbr"] = None
        p["industry_per"] = None
        if fin_cache.exists():
            try:
                age_hr = (now_kst().timestamp() - fin_cache.stat().st_mtime) / 3600
                if age_hr < 24:
                    fin = json.loads(fin_cache.read_text(encoding="utf-8"))
                    p["per"] = fin.get("per")
                    p["pbr"] = fin.get("pbr")
                    p["industry_per"] = fin.get("industry_per")
            except Exception:
                pass

    per_vals = [p["per"] for p in top_peers if isinstance(p.get("per"), (int, float)) and p["per"] > 0]
    pbr_vals = [p["pbr"] for p in top_peers if isinstance(p.get("pbr"), (int, float)) and p["pbr"] > 0]
    sector_avg = {
        "per": round(sum(per_vals) / len(per_vals), 1) if per_vals else None,
        "pbr": round(sum(pbr_vals) / len(pbr_vals), 2) if pbr_vals else None,
    }
    target_rank = next((i for i, p in enumerate(top_peers) if p["code"] == code), -1)

    result = {
        "code":        code,
        "sector":      target_sector,
        "sector_avg":  sector_avg,
        "target_rank": target_rank,
        "peer_count":  len(top_peers),
        "peers":       top_peers,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/us/peers/<symbol>")
def api_peers_us(symbol: str):
    """US 동종비교. sp500_tickers + us_market + us_yinfo 캐시 조합, 12h 캐시."""
    symbol = symbol.upper()
    cache_file = BASE_DIR / "cache" / f"us_peers_{symbol}.json"
    cached = _read_fresh_json(cache_file, 720)
    if cached:
        return jsonify(cached)

    sp_file = BASE_DIR / "cache" / "sp500_tickers.json"
    if not sp_file.exists():
        return jsonify({"error": "sp500_tickers.json 없음", "peers": []}), 503
    try:
        sp500 = json.loads(sp_file.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "sp500 파싱 실패", "peers": []}), 503

    target = next((t for t in sp500 if t.get("symbol") == symbol), None)
    if not target:
        return jsonify({"error": "S&P500 목록에 없음", "peers": []}), 404
    sector = target.get("sector")
    if not sector:
        return jsonify({"error": "섹터 정보 없음", "peers": []}), 404

    us_market = _fetch_us_market_data()
    stocks_by_sym = {s["symbol"]: s for s in (us_market.get("all_stocks") or [])}
    same_sector = [t for t in sp500 if t.get("sector") == sector]

    peers: list[dict] = []
    for t in same_sector:
        sym = t["symbol"]
        m = stocks_by_sym.get(sym, {})
        p = {
            "code":         sym,
            "symbol":       sym,
            "name":         t.get("name") or sym,
            "sub_industry": t.get("sub_industry"),
            "price":        m.get("price"),
            "change_pct":   m.get("change_pct"),
            "volume_mn":    m.get("volume_mn") or 0,
            "per":          None,
            "pbr":          None,
            "roe":          None,
            "market_cap":   None,
        }
        yinfo_cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
        if yinfo_cache.exists():
            try:
                age_hr = (now_kst().timestamp() - yinfo_cache.stat().st_mtime) / 3600
                if age_hr < 24:
                    info = json.loads(yinfo_cache.read_text(encoding="utf-8"))
                    tpe = info.get("trailingPE")
                    pb  = info.get("priceToBook")
                    roe = info.get("returnOnEquity")
                    p["per"]        = round(tpe, 1) if tpe else None
                    p["pbr"]        = round(pb, 2)  if pb  else None
                    p["roe"]        = round(roe * 100, 1) if roe else None
                    p["market_cap"] = info.get("marketCap")
            except Exception:
                pass
        peers.append(p)

    peers.sort(key=lambda x: (x.get("market_cap") or 0), reverse=True)
    peers = peers[:25]

    per_vals = [p["per"] for p in peers if isinstance(p.get("per"), (int, float)) and p["per"] > 0]
    pbr_vals = [p["pbr"] for p in peers if isinstance(p.get("pbr"), (int, float)) and p["pbr"] > 0]
    sector_avg = {
        "per": round(sum(per_vals) / len(per_vals), 1) if per_vals else None,
        "pbr": round(sum(pbr_vals) / len(pbr_vals), 2) if pbr_vals else None,
    }
    target_rank = next((i for i, p in enumerate(peers) if p["symbol"] == symbol), -1)

    result = {
        "symbol":      symbol,
        "sector":      sector,
        "sector_avg":  sector_avg,
        "target_rank": target_rank,
        "peer_count":  len(peers),
        "peers":       peers,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


_MACRO_TICKERS = [
    ("USD/KRW",     "KRW=X",    "currency",   ""),
    ("USD/JPY",     "JPY=X",    "currency",   ""),
    ("EUR/USD",     "EURUSD=X", "currency",   ""),
    ("USD/CNY",     "CNY=X",    "currency",   ""),
    ("WTI 원유",    "CL=F",     "commodity",  "$/bbl"),
    ("금",          "GC=F",     "commodity",  "$/oz"),
    ("은",          "SI=F",     "commodity",  "$/oz"),
    ("구리",        "HG=F",     "commodity",  "$/lb"),
    ("천연가스",    "NG=F",     "commodity",  "$"),
    ("미국 10년물", "^TNX",     "bond",       "%"),
    ("VIX",         "^VIX",     "volatility", "pt"),
    ("BTC",         "BTC-USD",  "crypto",     "$"),
]


@app.route("/api/macro")
def api_macro():
    """yfinance 기반 글로벌 매크로 12종. 30분 캐시."""
    cache_file = BASE_DIR / "cache" / "macro_data.json"
    cached = _read_fresh_json(cache_file, 30)
    if cached:
        return jsonify(cached)

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치", "items": []}), 500

    items: list[dict] = []
    for name, ticker, category, unit in _MACRO_TICKERS:
        try:
            t = _yf.Ticker(ticker)
            hist = t.history(period="5d")
            if hist is None or hist.empty:
                continue
            closes = [float(c) for c in hist["Close"].tolist() if c == c]
            if not closes:
                continue
            cur = round(closes[-1], 4)
            prev = round(closes[-2], 4) if len(closes) >= 2 else cur
            change = round(cur - prev, 4)
            change_pct = round((cur / prev - 1) * 100, 2) if prev else 0.0
            items.append({
                "name": name, "ticker": ticker, "category": category,
                "value": cur, "change": change, "change_pct": change_pct, "unit": unit,
            })
            time.sleep(0.05)
        except Exception as exc:
            log.debug("macro %s fail: %s", ticker, exc)
            continue

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(items),
        "items":      items,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# DART 공시 실시간 감지
# ─────────────────────────────────────────────────────────────────────────

# 주가 영향 키워드 (제목에서 검색)
_DART_CRITICAL_KW = [
    "대규모", "최대주주변경", "최대주주 변경", "공개매수", "상장폐지", "거래정지",
    "관리종목", "경영권",
]
_DART_HIGH_KW = [
    "공급계약", "수주", "자기주식", "자사주", "유상증자", "무상증자",
    "감자", "분할", "합병", "흑자전환", "적자전환",
    "배당", "주식분할", "영업이익", "전환사채", "신주인수권",
    "주요사항", "타법인 주식", "자산양수", "특허",
]


# 공시 중요도 점수표 (규정 기반 하드코딩 — 각 카테고리별 대표 키워드)
# KBJ P4 삭제: _DISCLOSURE_SCORE_TABLE — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: _classify_disclosure — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: _cap_bonus_for_code — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


def _is_watchlist_code(stock_code: str) -> bool:
    if not stock_code:
        return False
    try:
        wl = _load_server_watchlist() or []
        return any(w.get("code") == stock_code for w in wl)
    except Exception:
        return False


# KBJ P4 삭제: score_disclosure — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: recalc_disclosure_scores — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: api_disclosure_bonus — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: api_disclosure_events — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: api_disclosures_recalc — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: init_dart_corp_map_db — kbj filings.corp_code(pub_filings.corp_code)


# KBJ P4 삭제: poll_dart_disclosures — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# KBJ P4 삭제: api_disclosures — kbj filings.dart_feed·filings.derive(kbj.engines.filings.importance)·/api/filings/feed


# ─────────────────────────────────────────────────────────────────────────
# PHASE 20 — DART 12분기 손익계산서 (슬림)
# ─────────────────────────────────────────────────────────────────────────
_DART_CORP_MAP_CACHE: dict | None = None


# KBJ P4 삭제: _load_dart_corp_code_map — kbj filings.corp_code(pub_filings.corp_code)


_DART_IS_KEYWORDS = {
    "revenue":      ["매출액"],
    "cogs":         ["매출원가"],
    "gross_profit": ["매출총이익"],
    "sga":          ["판매비와관리비", "판관비"],
    "op_income":    ["영업이익"],
    "net_income":   ["당기순이익", "분기순이익", "반기순이익"],
}


def _extract_is_aggregates(is_items: list) -> dict:
    """
    DART IS/CIS 리스트에서 6개 집계 항목만 추출.
    thstrm_amount (당기금액) 를 그대로 사용 — 검증 결과 이미 개별 분기값이며 누적 아님.
    """
    out = {k: None for k in _DART_IS_KEYWORDS}
    for item in is_items:
        nm = (item.get("account_nm") or "").strip()
        amt_str = (item.get("thstrm_amount") or "").replace(",", "").strip()
        if not amt_str:
            continue
        try:
            amt = int(amt_str)
        except ValueError:
            try:
                amt = int(float(amt_str))
            except ValueError:
                continue
        for key, kws in _DART_IS_KEYWORDS.items():
            if out[key] is not None:
                continue
            # 특별 처리: '매출액' 은 '매출원가', '총매출' 등을 피해야 함
            if key == "revenue":
                if "매출액" in nm and "원가" not in nm and "총매" not in nm and "차감" not in nm:
                    out[key] = amt
                    break
            elif key == "op_income":
                if "영업이익" in nm and "영업외" not in nm and "조정" not in nm:
                    out[key] = amt
                    break
            elif key == "net_income":
                # '지배기업' / '비지배' 수식이 붙은 것은 제외, 순수 '당기순이익' 우선
                if any(k in nm for k in kws) and "지배" not in nm and "비지배" not in nm:
                    out[key] = amt
                    break
            else:
                if any(k in nm for k in kws):
                    out[key] = amt
                    break
    # 매출총이익 역산
    if out["gross_profit"] is None and out["revenue"] and out["cogs"]:
        out["gross_profit"] = out["revenue"] - out["cogs"]
    return out


# KBJ P4 삭제: _fetch_dart_quarter — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


# KBJ P4 삭제: _try_dart_segment_revenue — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


# KBJ P4 삭제: api_dart_financial — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


def _extract_econ_time(event: dict) -> str:
    """time 필드에서 HH:MM 부분만 추출 (UTC 기준, 00:00 은 '미정')."""
    t = event.get("time") or ""
    if isinstance(t, str) and len(t) >= 16:
        hm = t[11:16]
        return "" if hm == "00:00" else hm
    return ""


# ─────────────────────────────────────────────────────────────────────────
# PHASE 19 — 52주 신고가 (KR/US)
# ─────────────────────────────────────────────────────────────────────────
def _kr_new_highs_from_charts(top_by_volume: int = 200,
                              ratio_threshold: float = 0.95) -> list[dict]:
    """
    거래대금 상위 top_by_volume 종목에 대해 /api/chart?days=252 호출,
    52주(≈252영업일) 고점 대비 현재가 비율이 threshold 이상인 종목 반환.
    """
    uni = _load_naver_universe()
    stocks_map = (uni or {}).get("stocks") or {}
    if not stocks_map:
        return []

    # 거래대금 기준 정렬
    eligible = sorted(
        [s for s in stocks_map.values() if (s.get("volume_mn") or 0) >= 50],
        key=lambda x: x.get("volume_mn") or 0,
        reverse=True,
    )[:top_by_volume]

    out: list[dict] = []
    today_str = now_kst().strftime("%Y-%m-%d")

    for st in eligible:
        code = st.get("code")
        if not code:
            continue
        try:
            chart = _call_api_internal(f"/api/chart/{code}?days=252")
            if not chart or chart.get("error"):
                continue
            highs  = chart.get("high")  or []
            lows   = chart.get("low")   or []
            closes = chart.get("close") or []
            dates  = chart.get("dates") or []
            if not highs or not closes:
                continue
            hi_52w = max(highs)
            lo_52w = min(lows) if lows else None
            current = closes[-1]
            if not hi_52w or not current:
                continue
            ratio = current / hi_52w
            if ratio < ratio_threshold:
                continue
            # 52주 고점 발생일 (오늘과 같으면 TODAY)
            hi_idx = highs.index(hi_52w)
            hi_date = dates[hi_idx] if hi_idx < len(dates) else ""
            is_today = (dates[-1] == today_str and highs[-1] == hi_52w)

            out.append({
                "code":       code,
                "name":       st.get("name"),
                "sector":     (st.get("sectors") or [None])[0],
                "market":     "kr",
                "market_cap": None,                           # naver_universe 는 시총 미포함
                "volume_mn":  st.get("volume_mn"),
                "per":        None,                            # 비용 큰 조회라 생략
                "price":      current,
                "change_pct": st.get("change_pct"),
                "w52_high":   hi_52w,
                "w52_low":    lo_52w,
                "w52_ratio":  round(ratio * 100, 1),
                "hi_date":    hi_date,
                "is_today":   is_today,
            })
        except Exception as exc:
            log.debug("new_high chart %s fail: %s", code, exc)
            continue

    # 회전율 = 거래대금/시총 은 시총 없어 계산 불가. 프론트에서 표시 생략.
    out.sort(key=lambda x: x["w52_ratio"], reverse=True)
    return out


# ── 신고가 비차단 캐시 빌더 ──────────────────────────────────────────────
# 콜드 캐시 시 200종목 차트(KR)/500종목 yfinance(US) 순차 스캔이 60초+ 걸려
# 요청이 행 걸리던 문제. 캐시 없으면 백그라운드 빌드 시작 + 즉시 building 응답.
_NH_BUILDING: set = set()
_NH_LOCK = threading.Lock()


def _new_highs_cached_or_build(market: str, cache_file, builder) -> dict:
    """new_highs 비차단 반환. 캐시 있으면 즉시, 없으면 백그라운드 빌드 + building 응답."""
    cached = _read_fresh_json(cache_file, 1440)   # 24h
    if cached:
        return cached
    with _NH_LOCK:
        already = market in _NH_BUILDING
        if not already:
            _NH_BUILDING.add(market)
    if not already:
        def _bg():
            try:
                items = builder()
                result = {"updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                          "market": market, "count": len(items), "items": items}
                cache_file.parent.mkdir(exist_ok=True)
                cache_file.write_text(json.dumps(result, ensure_ascii=False),
                                      encoding="utf-8")
                log.info("[신고가] %s 캐시 빌드 완료 (%d종목)", market, len(items))
            except Exception as exc:
                log.warning("[신고가] %s 빌드 실패: %s", market, exc)
            finally:
                with _NH_LOCK:
                    _NH_BUILDING.discard(market)
        threading.Thread(target=_bg, daemon=True, name=f"newhigh-{market}").start()
    return {"updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "market": market, "count": 0, "items": [], "building": True}


def _prewarm_new_highs():
    """신고가 캐시 프리워밍 (장 마감 후 cron + 부팅). 첫 진입 즉시 응답 보장."""
    today = _get_trading_date()
    for market, builder in (("kr", _kr_new_highs_from_charts),
                            ("us", _us_new_highs_from_yinfo)):
        cf = BASE_DIR / "cache" / f"new_highs_{market}_{today}.json"
        if _read_fresh_json(cf, 1440):
            continue
        try:
            items = builder()
            cf.parent.mkdir(exist_ok=True)
            cf.write_text(json.dumps(
                {"updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                 "market": market, "count": len(items), "items": items},
                ensure_ascii=False), encoding="utf-8")
            log.info("[신고가 프리워밍] %s %d종목", market, len(items))
        except Exception as exc:
            log.warning("[신고가 프리워밍] %s 실패: %s", market, exc)


@app.route("/api/new_highs")
def api_new_highs_kr():
    """KR 52주 신고가 근접 종목. 거래대금 상위 200 스캔, 일 1회 캐시 (비차단)."""
    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"new_highs_kr_{today}.json"
    return jsonify(_new_highs_cached_or_build("kr", cache_file, _kr_new_highs_from_charts))


def _us_new_highs_from_yinfo(ratio_threshold: float = 0.95) -> list[dict]:
    """
    S&P500 전 종목에 대해 us_yinfo 캐시 + yfinance info.fiftyTwoWeekHigh 로
    52주 고점 비율 계산 후 필터.
    """
    us_data = _fetch_us_market_data()
    all_stocks = us_data.get("all_stocks") or []
    if not all_stocks:
        return []

    try:
        import yfinance as _yf
    except ImportError:
        return []

    out: list[dict] = []
    for s in all_stocks:
        sym = s.get("symbol")
        if not sym:
            continue
        info = None
        cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
        if cache.exists():
            try:
                age_hr = (now_kst().timestamp() - cache.stat().st_mtime) / 3600
                if age_hr < 24:
                    info = json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                info = None
        if info is None:
            try:
                raw = _yf.Ticker(sym).info or {}
                info = {k: v for k, v in raw.items()
                        if isinstance(v, (int, float, str, bool, type(None)))}
                cache.parent.mkdir(exist_ok=True)
                cache.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                log.debug("us_new_highs yinfo %s fail: %s", sym, exc)
                continue
            time.sleep(0.05)

        hi = info.get("fiftyTwoWeekHigh")
        lo = info.get("fiftyTwoWeekLow")
        current = info.get("currentPrice") or info.get("regularMarketPrice") or s.get("price")
        if not hi or not current:
            continue
        ratio = current / hi
        if ratio < ratio_threshold:
            continue

        market_cap = info.get("marketCap")
        per        = info.get("trailingPE")
        vol_mn     = s.get("volume_mn") or 0    # US 기준: $M traded
        turnover = (vol_mn * 1e6 / market_cap * 100) if market_cap else None

        out.append({
            "symbol":     sym,
            "code":       sym,
            "name":       s.get("name"),
            "sector":     s.get("sector"),
            "market":     "us",
            "market_cap": market_cap,
            "volume_mn":  vol_mn,
            "turnover":   round(turnover, 3) if turnover is not None else None,
            "per":        round(per, 2) if isinstance(per, (int, float)) else None,
            "price":      round(current, 2),
            "change_pct": s.get("change_pct"),
            "w52_high":   round(hi, 2),
            "w52_low":    round(lo, 2) if lo else None,
            "w52_ratio":  round(ratio * 100, 1),
            "hi_date":    "",   # yfinance 는 고점 발생일 미제공
            "is_today":   ratio >= 0.998,
        })

    out.sort(key=lambda x: x["w52_ratio"], reverse=True)
    return out


@app.route("/api/us/new_highs")
def api_new_highs_us():
    """US 52주 신고가 근접 종목. S&P500 전체 스캔, 일 1회 캐시 (비차단)."""
    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"new_highs_us_{today}.json"
    return jsonify(_new_highs_cached_or_build("us", cache_file, _us_new_highs_from_yinfo))


@app.route("/api/calendar/economic")
def api_calendar_economic():
    """경제지표 발표 일정 (Finnhub). 6시간 캐시."""
    finnhub_key = os.getenv("FINNHUB_API_KEY")
    if not finnhub_key:
        return jsonify({"error": "FINNHUB_API_KEY 미설정", "events": []}), 503

    from_date, to_date = _calendar_date_range()
    cache_file = BASE_DIR / "cache" / f"economic_calendar_{from_date}.json"
    cached = _read_fresh_json(cache_file, 360)  # 6h
    if cached:
        return jsonify(cached)

    try:
        import requests as _rq
        res = _rq.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"token": finnhub_key, "from": from_date, "to": to_date},
            timeout=10,
        )
        if res.status_code != 200:
            return jsonify({"error": f"Finnhub HTTP {res.status_code}", "events": []}), 502
        data = res.json()
    except Exception as exc:
        return jsonify({"error": f"Finnhub 요청 실패: {exc}", "events": []}), 502

    raw_events = data.get("economicCalendar") or []

    # 필터: 주요국 + medium/high 임팩트만
    keep_countries = {"US", "KR", "CN", "JP", "EU", "GB", "DE"}
    keep_impacts = {"high", "medium"}
    filtered = []
    for e in raw_events:
        if e.get("country") not in keep_countries:
            continue
        if (e.get("impact") or "").lower() not in keep_impacts:
            continue
        date = _extract_econ_date(e)
        if not date:
            continue
        impact = (e.get("impact") or "low").lower()
        filtered.append({
            "date":         date,
            "time":         _extract_econ_time(e),
            "country":      e.get("country"),
            "country_kr":   _COUNTRY_KR.get(e.get("country"), e.get("country")),
            "event":        e.get("event"),
            "event_kr":     _EVENT_KR.get(e.get("event"), e.get("event")),
            "impact":       impact,
            "impact_emoji": "🔴" if impact == "high" else "🟡" if impact == "medium" else "⚪",
            "actual":       e.get("actual"),
            "estimate":     e.get("estimate"),
            "prev":         e.get("prev"),
            "unit":         e.get("unit") or "",
        })

    impact_order = {"high": 0, "medium": 1, "low": 2}
    filtered.sort(key=lambda x: (x["date"], impact_order.get(x["impact"], 2), x["time"] or "99:99"))

    result = {
        "from":   from_date,
        "to":     to_date,
        "count":  len(filtered),
        "raw_count": len(raw_events),
        "events": filtered,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _fetch_us_earnings(from_date: str, to_date: str) -> list:
    """Finnhub 미국 실적 — S&P500 종목만 필터."""
    finnhub_key = os.getenv("FINNHUB_API_KEY")
    if not finnhub_key:
        return []
    try:
        import requests as _rq
        res = _rq.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"token": finnhub_key, "from": from_date, "to": to_date},
            timeout=10,
        )
        if res.status_code != 200:
            return []
        data = res.json()
    except Exception as exc:
        log.debug("finnhub earnings fail: %s", exc)
        return []

    raw = data.get("earningsCalendar") or []
    sp500_syms, sp500_names = _load_sp500_symbols()
    out = []
    hour_label = {"bmo": "장전", "amc": "장후", "dmh": "장중"}
    for e in raw:
        sym = e.get("symbol")
        if not sym or sym not in sp500_syms:
            continue
        out.append({
            "date":             e.get("date") or "",
            "market":           "us",
            "symbol":           sym,
            "name":             sp500_names.get(sym, sym),
            "time":             hour_label.get(e.get("hour") or "", ""),
            "eps_estimate":     e.get("epsEstimate"),
            "eps_actual":       e.get("epsActual"),
            "revenue_estimate": e.get("revenueEstimate"),
            "revenue_actual":   e.get("revenueActual"),
            "flag":             "🇺🇸",
        })
    return out


def _fetch_kr_earnings(from_date: str, to_date: str) -> list:
    """DART 정기공시 (사업/반기/분기 보고서) 조회."""
    dart_key = _dart_key_configured()
    if not dart_key:
        return []
    try:
        from kbj.data import legacy_bridge as _rq  # KBJ P2: DART 는 브리지(`dart:`)로
        res = _rq.get(
            "dart:/list.json",
            params={
                "crtfc_key":  dart_key,
                "bgn_de":     from_date.replace("-", ""),
                "end_de":     to_date.replace("-", ""),
                "pblntf_ty":  "A",   # 정기공시
                "page_count": 100,
            },
            timeout=10,
        )
        data = res.json()
    except Exception as exc:
        log.debug("DART fail: %s", exc)
        return []

    if data.get("status") != "000":
        return []

    earnings = []
    kw = ("분기보고서", "반기보고서", "사업보고서")
    for item in data.get("list") or []:
        report_nm = item.get("report_nm") or ""
        if not any(k in report_nm for k in kw):
            continue
        stock_code = item.get("stock_code") or ""
        if not stock_code:   # 상장사만
            continue
        rcept_dt = item.get("rcept_dt") or ""
        if len(rcept_dt) != 8:
            continue
        date_str = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"
        earnings.append({
            "date":             date_str,
            "market":           "kr",
            "symbol":           stock_code,
            "name":             item.get("corp_name") or "",
            "time":             "",
            "report_type":      report_nm,
            "eps_estimate":     None,
            "eps_actual":       None,
            "revenue_estimate": None,
            "revenue_actual":   None,
            "flag":             "🇰🇷",
            "dart_link":        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no', '')}",
        })
    return earnings


@app.route("/api/calendar/earnings")
def api_calendar_earnings():
    """실적발표 일정: Finnhub(미국 S&P500) + DART(한국). 6시간 캐시."""
    from_date, to_date = _calendar_date_range()
    cache_file = BASE_DIR / "cache" / f"earnings_calendar_{from_date}.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    all_earn = []
    all_earn.extend(_fetch_us_earnings(from_date, to_date))
    all_earn.extend(_fetch_kr_earnings(from_date, to_date))
    all_earn.sort(key=lambda x: (x.get("date") or "", x.get("market"), x.get("symbol")))

    result = {
        "from":     from_date,
        "to":       to_date,
        "count":    len(all_earn),
        "earnings": all_earn,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _apply_live_prices_to_items(items: list) -> int:
    """items 배열의 각 원소 price/change_pct/volume_mn 을 실시간 값으로 패치.
    KR: naver_universe live, US: SQLite stocks.close. 주중이면 실행, 주말이면 skip."""
    if not items:
        return 0
    # 주말에는 기존 전일 종가 유지 (user 요구: 주중만 실시간 반영)
    if now_kst().weekday() >= 5:
        return 0
    uni = _load_naver_universe()
    kr_live = (uni or {}).get("stocks") or {}
    patched = 0
    # US codes 를 DB 한번에 조회
    us_codes = [it.get("code") for it in items
                if it.get("code") and not (it.get("code") or "").isdigit()]
    us_map: dict = {}
    if us_codes and _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                qmarks = ",".join(["?"] * len(us_codes))
                rows = conn.execute(
                    f"SELECT code, close, change_pct, volume_mn FROM stocks "
                    f"WHERE market='US' AND code IN ({qmarks})", us_codes
                ).fetchall()
                us_map = {r["code"]: dict(r) for r in rows}
        except Exception:
            pass
    for it in items:
        code = it.get("code", "")
        if not code:
            continue
        if code.isdigit() and len(code) == 6:  # KR
            live = kr_live.get(code)
            if live and live.get("close"):
                it["price"] = live["close"]
                it["change_pct"] = live.get("change_pct", it.get("change_pct"))
                it["volume_mn"] = live.get("volume_mn", it.get("volume_mn"))
                patched += 1
        else:  # US
            live = us_map.get(code)
            if live and live.get("close"):
                it["price"] = live["close"]
                it["change_pct"] = live.get("change_pct", it.get("change_pct"))
                it["volume_mn"] = live.get("volume_mn", it.get("volume_mn"))
                patched += 1
    return patched


def _patch_discover_prices(data: dict) -> dict:
    """종목 발굴 결과의 price/change_pct 를 실시간 값으로 패치."""
    items = data.get("items")
    if items:
        _apply_live_prices_to_items(items)
    return data


@app.route("/api/discover")
def api_discover():
    """
    Phase 15 종목 발굴. market ∈ {kr, us, all}.
    Stage 2 결과 (6시간 캐시) 우선, 없으면 Stage 1 (15분 캐시) 폴백.
    all: kr+us Stage 2 를 읽어 병합 (있는 쪽만이라도 반환).
    """
    market = (request.args.get("market") or "kr").lower()
    if market not in ("kr", "us", "all"):
        return jsonify({"error": "market 파라미터는 kr/us/all"}), 400

    cache_dir = BASE_DIR / "cache"

    if market == "kr":
        d = (_read_fresh_json(cache_dir / "discover_kr_stage2.json", 360)
             or _read_fresh_json(cache_dir / "discover_kr_stage1.json", 15))
        if d:
            return jsonify(_patch_discover_prices(d))
        result = _stage1_prefilter_kr()
        if "error" not in result:
            try:
                cache_dir.mkdir(exist_ok=True)
                (cache_dir / "discover_kr_stage1.json").write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        return jsonify(result)

    if market == "us":
        d = (_read_fresh_json(cache_dir / "discover_us_stage2.json", 360)
             or _read_fresh_json(cache_dir / "discover_us_stage1.json", 15))
        if d:
            return jsonify(_patch_discover_prices(d))
        result = _stage1_prefilter_us()
        if "error" not in result:
            try:
                cache_dir.mkdir(exist_ok=True)
                (cache_dir / "discover_us_stage1.json").write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        return jsonify(result)

    # market == "all" — 두 시장을 개별로 읽어 병합
    all_fresh = _read_fresh_json(cache_dir / "discover_all_stage2.json", 360)
    if all_fresh:
        return jsonify(_patch_discover_prices(all_fresh))

    kr_data = (_read_fresh_json(cache_dir / "discover_kr_stage2.json", 360)
               or _read_fresh_json(cache_dir / "discover_kr_stage1.json", 15))
    us_data = (_read_fresh_json(cache_dir / "discover_us_stage2.json", 360)
               or _read_fresh_json(cache_dir / "discover_us_stage1.json", 15))

    kr_items = (kr_data or {}).get("items", [])
    us_items = (us_data or {}).get("items", [])
    kr_total = (kr_data or {}).get("total_scanned", 0)
    us_total = (us_data or {}).get("total_scanned", 0)
    if not kr_items:
        kr_result = _stage1_prefilter_kr()
        if "error" not in kr_result:
            kr_items = kr_result["items"]
            kr_total = kr_result["total_scanned"]
            try:
                (cache_dir / "discover_kr_stage1.json").write_text(
                    json.dumps(kr_result, ensure_ascii=False), encoding="utf-8")
            except Exception: pass
    if not us_items:
        us_result = _stage1_prefilter_us()
        if "error" not in us_result:
            us_items = us_result["items"]
            us_total = us_result["total_scanned"]
            try:
                (cache_dir / "discover_us_stage1.json").write_text(
                    json.dumps(us_result, ensure_ascii=False), encoding="utf-8")
            except Exception: pass

    merged = sorted(kr_items + us_items,
                    key=lambda x: x.get("total_score", 0), reverse=True)
    kr_stage = (kr_data or {}).get("stage", 1)
    us_stage = (us_data or {}).get("stage", 1)
    return jsonify({
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "all",
        "stage":         min(kr_stage, us_stage) if (kr_items and us_items) else max(kr_stage, us_stage),
        "total_scanned": kr_total + us_total,
        "items":         merged,
    })


# ─────────────────────────────────────────────────────────────────────────
# PHASE 15 Stage 2 — 상위 200종목 상세 스코어링 (수급/밸류/기술 + 보너스)
# ─────────────────────────────────────────────────────────────────────────
_discover_lock = threading.Lock()
_discover_state: dict = {
    "status":      "idle",    # idle | starting | running | done | error
    "phase":       None,      # stage1 | fetch | scoring
    "market":      None,
    "progress":    0,
    "total":       0,
    "started_at":  None,
    "finished_at": None,
    "error":       None,
    "message":     None,
    "last_tick":   None,      # 마지막 progress/phase 변경 시각 (stall 감지용)
}


def _discover_get_state() -> dict:
    with _discover_lock:
        return dict(_discover_state)


def _discover_set(**kw):
    """상태 업데이트. progress/phase가 바뀌면 last_tick도 갱신."""
    with _discover_lock:
        # progress 또는 phase 가 바뀌면 tick 갱신 → stall 감지 타이머 리셋
        prev_prog = _discover_state.get("progress")
        prev_phase = _discover_state.get("phase")
        _discover_state.update(kw)
        new_prog = _discover_state.get("progress")
        new_phase = _discover_state.get("phase")
        if ("progress" in kw and new_prog != prev_prog) or \
           ("phase" in kw and new_phase != prev_phase) or \
           ("status" in kw and kw["status"] in ("starting", "running")):
            _discover_state["last_tick"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")


def _call_api_internal(path: str) -> dict | None:
    """Flask test_client 로 in-process route 호출. worker thread 안전."""
    try:
        with app.test_client() as c:
            r = c.get(path)
            if r.status_code == 200:
                return r.get_json()
    except Exception as exc:
        log.debug("internal call %s failed: %s", path, exc)
    return None


def _fmt_eok(won: float) -> str:
    """원 → 억 단위 표기."""
    if won is None:
        return "—"
    eok = won / 1e8
    sign = "+" if eok > 0 else ""
    return f"{sign}{eok:,.0f}억원"


def _calc_flow_score_kr(flow: dict) -> tuple[int, dict, list]:
    """수급 점수 (0~25). foreign_value/inst_value 단위는 원(KRW)."""
    sub = {"today": 0, "cum5d": 0, "inst": 0, "streak": 0}
    expl: list = []
    if not flow or "error" in flow:
        expl.append({"label": "수급 데이터", "detail": "데이터 없음", "pts": 0, "max": 25})
        return 0, sub, expl

    fv = flow.get("foreign_value") or []
    iv = flow.get("inst_value") or []
    if not fv:
        expl.append({"label": "수급 데이터", "detail": "시계열 없음", "pts": 0, "max": 25})
        return 0, sub, expl

    today = fv[-1]
    if   today > 10_000_000_000: sub["today"] = 8
    elif today >  5_000_000_000: sub["today"] = 6
    elif today >  1_000_000_000: sub["today"] = 4
    elif today >  0:             sub["today"] = 2
    expl.append({
        "label":  "외국인 당일 순매수",
        "detail": _fmt_eok(today),
        "pts":    sub["today"], "max": 8,
    })

    cum5 = sum(fv[-5:])
    if   cum5 > 30_000_000_000: sub["cum5d"] = 8
    elif cum5 > 10_000_000_000: sub["cum5d"] = 6
    elif cum5 >  0:             sub["cum5d"] = 4
    elif cum5 > -5_000_000_000: sub["cum5d"] = 1
    expl.append({
        "label":  "외국인 5일 누적",
        "detail": _fmt_eok(cum5),
        "pts":    sub["cum5d"], "max": 8,
    })

    if iv:
        inst_today = iv[-1]
        if   inst_today > 5_000_000_000: sub["inst"] = 5
        elif inst_today > 1_000_000_000: sub["inst"] = 3
        elif inst_today > 0:             sub["inst"] = 1
        expl.append({
            "label":  "기관 당일 순매수",
            "detail": _fmt_eok(inst_today),
            "pts":    sub["inst"], "max": 5,
        })
    else:
        expl.append({"label": "기관 당일 순매수", "detail": "데이터 없음", "pts": 0, "max": 5})

    streak = 0
    for v in reversed(fv):
        if v > 0: streak += 1
        else: break
    if   streak >= 5: sub["streak"] = 4
    elif streak >= 3: sub["streak"] = 3
    elif streak >= 2: sub["streak"] = 2
    elif streak >= 1: sub["streak"] = 1
    expl.append({
        "label":  "외국인 연속 순매수",
        "detail": f"{streak}일 연속" if streak > 0 else "순매도 전환",
        "pts":    sub["streak"], "max": 4,
    })

    # v2 보너스: 외국인+기관 동반 매수 (최근 5일 양수 모두 양수)
    if fv and iv:
        frgn_5d = sum(fv[-5:])
        inst_5d = sum(iv[-5:])
        if frgn_5d > 0 and inst_5d > 0:
            sub["alignment"] = 5
            expl.append({
                "label":  "외국인+기관 동반 매수",
                "detail": f"외국인 5일 {_fmt_eok(frgn_5d)} · 기관 {_fmt_eok(inst_5d)}",
                "pts":    5, "max": 5,
            })
        elif frgn_5d < 0 and inst_5d < 0:
            sub["alignment"] = -5
            expl.append({
                "label":  "외국인+기관 동반 매도",
                "detail": f"외국인 5일 {_fmt_eok(frgn_5d)} · 기관 {_fmt_eok(inst_5d)}",
                "pts":    -5, "max": 5,
            })

    return sum(sub.values()), sub, expl


def _calc_valuation_score_kr(fin: dict, current_price: float | None,
                             high_180d: float | None,
                             sector_per_rank_pct: float | None) -> tuple[int, dict, list]:
    """밸류 점수 (0~20). per/pbr 음수/None 은 0점."""
    sub = {"per_vs_ind": 0, "pbr": 0, "high_gap": 0, "sector_rank": 0}
    expl: list = []
    if not fin or "error" in fin:
        expl.append({"label": "재무 데이터", "detail": "데이터 없음", "pts": 0, "max": 20})
        return 0, sub, expl

    per = fin.get("per")
    pbr = fin.get("pbr")
    ind = fin.get("industry_per")

    if per and per > 0 and ind and ind > 0:
        r = per / ind
        if   r < 0.3: sub["per_vs_ind"] = 7
        elif r < 0.5: sub["per_vs_ind"] = 6
        elif r < 0.7: sub["per_vs_ind"] = 5
        elif r < 0.9: sub["per_vs_ind"] = 3
        elif r < 1.0: sub["per_vs_ind"] = 1
        expl.append({
            "label":  "PER 업종 대비",
            "detail": f"PER {per} · 업종평균 {ind} · 비율 {r*100:.0f}%",
            "pts":    sub["per_vs_ind"], "max": 7,
        })
    elif per and per > 0:
        if   per <  5: sub["per_vs_ind"] = 7
        elif per < 10: sub["per_vs_ind"] = 5
        elif per < 15: sub["per_vs_ind"] = 3
        elif per < 20: sub["per_vs_ind"] = 1
        expl.append({
            "label":  "PER 업종 대비",
            "detail": f"PER {per} · 업종평균 없음 (절대 기준 적용)",
            "pts":    sub["per_vs_ind"], "max": 7,
        })
    else:
        expl.append({
            "label":  "PER 업종 대비",
            "detail": "PER 미제공 또는 적자",
            "pts":    0, "max": 7,
        })

    if pbr and pbr > 0:
        if   pbr < 0.5: sub["pbr"] = 5
        elif pbr < 0.8: sub["pbr"] = 4
        elif pbr < 1.0: sub["pbr"] = 3
        elif pbr < 1.5: sub["pbr"] = 2
        elif pbr < 2.0: sub["pbr"] = 1
        expl.append({
            "label":  "PBR",
            "detail": f"PBR {pbr}",
            "pts":    sub["pbr"], "max": 5,
        })
    else:
        expl.append({"label": "PBR", "detail": "PBR 미제공", "pts": 0, "max": 5})

    if high_180d and current_price and high_180d > 0:
        gap = (high_180d - current_price) / high_180d * 100
        if   gap > 40: sub["high_gap"] = 4
        elif gap > 30: sub["high_gap"] = 3
        elif gap > 20: sub["high_gap"] = 2
        elif gap > 10: sub["high_gap"] = 1
        expl.append({
            "label":  "180일 고점 대비",
            "detail": f"-{gap:.1f}% · 고점 {int(high_180d):,}원",
            "pts":    sub["high_gap"], "max": 4,
        })
    else:
        expl.append({"label": "180일 고점 대비", "detail": "차트 데이터 없음", "pts": 0, "max": 4})

    if sector_per_rank_pct is not None:
        pct = sector_per_rank_pct * 100
        if   sector_per_rank_pct < 0.10: sub["sector_rank"] = 4
        elif sector_per_rank_pct < 0.25: sub["sector_rank"] = 3
        elif sector_per_rank_pct < 0.40: sub["sector_rank"] = 2
        elif sector_per_rank_pct < 0.50: sub["sector_rank"] = 1
        expl.append({
            "label":  "섹터 내 PER 랭크",
            "detail": f"섹터 내 하위 {pct:.0f}% (저평가일수록 높은 점수)",
            "pts":    sub["sector_rank"], "max": 4,
        })
    else:
        expl.append({"label": "섹터 내 PER 랭크", "detail": "비교 가능 데이터 없음", "pts": 0, "max": 4})

    return sum(sub.values()), sub, expl


# _generate_analysis 의 실제 signal 문자열 기준 (server.py L279-376)
_TECH_BB    = {"과매도": 4, "중립 상향": 3, "스퀴즈": 2, "중립 하향": 1,
               "밴드 확장": 0, "과매수": 0}
_TECH_TREND = {"저항선 돌파": 4, "지지선 위": 3, "저항선 하": 1, "지지선 이탈": 0}
_TECH_FIB   = {"깊은 조정": 4, "중간 조정": 3, "일반 조정": 3,
               "약조정 구간": 2, "신고가 근접": 1, "추세 전환": 0}
_TECH_VOL   = {"거래량 급증": 3, "거래량 급감": 0}


def _calc_technical_score_kr(analysis: dict | None) -> tuple[int, dict, list]:
    """기술 점수 (0~15). comments 배열에서 type별 최고 점수 합산."""
    sub = {"bb": 0, "trend": 0, "fib": 0, "volume": 0}
    expl: list = []
    if not analysis:
        expl.append({"label": "차트 분석", "detail": "차트 데이터 없음", "pts": 0, "max": 15})
        return 0, sub, expl

    sigs = {"bollinger": None, "trendline": None, "fibonacci": None, "volume": None}
    for c in analysis.get("comments") or []:
        t = c.get("type")
        sig = c.get("signal", "") or ""
        if   t == "bollinger":
            pts = _TECH_BB.get(sig, 0)
            if pts >= sub["bb"]: sub["bb"] = pts; sigs["bollinger"] = sig
        elif t == "trendline":
            pts = _TECH_TREND.get(sig, 0)
            if pts >= sub["trend"]: sub["trend"] = pts; sigs["trendline"] = sig
        elif t == "fibonacci":
            pts = _TECH_FIB.get(sig, 0)
            if pts >= sub["fib"]: sub["fib"] = pts; sigs["fibonacci"] = sig
        elif t == "volume":
            pts = _TECH_VOL.get(sig, 0)
            if pts >= sub["volume"]: sub["volume"] = pts; sigs["volume"] = sig

    expl.append({"label": "볼린저밴드", "detail": sigs["bollinger"] or "신호 없음",
                 "pts": sub["bb"],     "max": 4})
    expl.append({"label": "추세선",     "detail": sigs["trendline"] or "신호 없음",
                 "pts": sub["trend"],  "max": 4})
    expl.append({"label": "피보나치",   "detail": sigs["fibonacci"] or "신호 없음",
                 "pts": sub["fib"],    "max": 4})
    expl.append({"label": "거래량",     "detail": sigs["volume"] or "신호 없음",
                 "pts": sub["volume"], "max": 3})
    return sum(sub.values()), sub, expl


def _calc_undervalued_bonus(stock_ret_20d: float | None,
                            sector_avg_ret_20d: float | None,
                            stock_ret_5d: float | None = None,
                            sector_avg_ret_5d: float | None = None) -> tuple[int, list]:
    """
    섹터가 올랐는데 본인은 덜 오른 경우 가산 (0~10).
    v2 안전장치: 5일 수익률이 음수이면 절반.
    v3 안전장치: 5일 RS(종목-섹터)가 -2%p 이하이면 보너스 해제.
    """
    if sector_avg_ret_20d is None or stock_ret_20d is None:
        return 0, [{"label": "덜오른 보너스",
                    "detail": "20일 수익률 데이터 없음",
                    "pts": 0, "max": 10}]

    # v3 안전장치: 5일 RS < -2%p → 보너스 해제 (개별 약세 방치 방지)
    if stock_ret_5d is not None and sector_avg_ret_5d is not None:
        rs_5d = stock_ret_5d - sector_avg_ret_5d
        if rs_5d < -2:
            return 0, [{
                "label":  "덜오른 보너스",
                "detail": f"5일 RS {rs_5d:+.1f}%p (섹터 대비 약세) → 보너스 해제",
                "pts":    0, "max": 10,
            }]

    if sector_avg_ret_20d > 5 and stock_ret_20d < sector_avg_ret_20d * 0.5:
        gap = sector_avg_ret_20d - stock_ret_20d
        pts = (10 if gap > 15 else 7 if gap > 10 else
               5  if gap >  5 else 3 if gap >  3 else 0)
        # v2 안전장치: 5일 모멘텀이 음수 → 아직 반등 안 함 → 절반만
        safety_note = ""
        if stock_ret_5d is not None and stock_ret_5d < 0:
            pts = max(0, pts // 2)
            safety_note = f" (5일 {stock_ret_5d:+.1f}% 약세 → 절반)"
        return pts, [{
            "label":  "덜오른 보너스",
            "detail": (f"섹터 20일 평균 {sector_avg_ret_20d:+.1f}%인데 "
                       f"이 종목은 {stock_ret_20d:+.1f}%. 차이 {gap:.1f}%p{safety_note}"),
            "pts":    pts, "max": 10,
        }]
    return 0, [{
        "label":  "덜오른 보너스",
        "detail": ("섹터 평균과 비슷하거나 더 많이 올라 가산점 없음"
                   if sector_avg_ret_20d is not None else "섹터 평균 계산 불가"),
        "pts":    0, "max": 10,
    }]


def _find_signal(comments: list, ctype: str) -> str | None:
    for c in comments or []:
        if c.get("type") == ctype:
            return c.get("signal")
    return None


def _generate_macd_tags(macd_vals: list, macd_sig: list,
                        macd_hist: list, lookback: int = 5) -> list[str]:
    """MACD 기반 태그 세분화 생성.

    - 골든크로스/데드크로스: 최근 lookback일 내 교차 발생 여부
    - 상태 태그: 현재 MACD 위치 기반 지속 조건
    """
    tags: list[str] = []
    n_m = len(macd_vals)
    n_s = len(macd_sig)
    n_h = len(macd_hist)

    # ── 최근 N일 내 골든/데드 크로스 ──
    if n_m >= 2 and n_s >= 2:
        recent_golden = False
        recent_dead = False
        scan = min(lookback, n_m - 1, n_s - 1)
        for i in range(1, scan + 1):
            m_cur, m_prev = macd_vals[-i], macd_vals[-i - 1]
            s_cur, s_prev = macd_sig[-i],  macd_sig[-i - 1]
            if m_prev <= s_prev and m_cur > s_cur:
                recent_golden = True
            if m_prev >= s_prev and m_cur < s_cur:
                recent_dead = True

        if recent_golden:
            tags.append("MACD_골든크로스")
        if recent_dead:
            tags.append("MACD_데드크로스")

    # ── MACD 상태 태그 (지속 조건) ──
    if n_h >= 2:
        h_cur  = macd_hist[-1]
        h_prev = macd_hist[-2]

        # 양전환: 히스토그램이 음→양
        if h_cur > 0 and h_prev <= 0:
            tags.append("MACD_양전환")

        # 양수 구간 세분화
        if h_cur > 0:
            if h_cur > h_prev:
                tags.append("MACD_상승강화")   # 양수 + 확대 중
            else:
                tags.append("MACD_양수유지")   # 양수지만 축소 중

    # 강세구간: MACD > Signal AND MACD > 0
    if n_m >= 1 and n_s >= 1:
        if macd_vals[-1] > 0 and macd_vals[-1] > macd_sig[-1]:
            tags.append("MACD_강세구간")

    return tags


def _stage2_scoring_worker(market: str):
    """백그라운드 진입점. finally 블록으로 상태 고착 절대 방지."""
    _discover_set(
        status="running", phase="stage1", market=market,
        progress=0, total=0, error=None,
        started_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        finished_at=None, message="시작 중…",
    )
    kr_items = None
    us_items = None
    try:
        if market in ("kr", "all"):
            try:
                kr_items = _run_stage2_kr()
            except Exception as exc:
                log.exception("stage2 KR failed")

        if market in ("us", "all"):
            try:
                us_items = _run_stage2_us()
            except Exception as exc:
                log.exception("stage2 US failed")

        if market == "all" and (kr_items or us_items):
            merged = sorted(
                (kr_items or []) + (us_items or []),
                key=lambda x: x["total_score"], reverse=True,
            )
            out = {
                "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                "market":        "all",
                "stage":         2,
                "total_scanned": len(kr_items or []) + len(us_items or []),
                "items":         merged,
            }
            try:
                (BASE_DIR / "cache" / "discover_all_stage2.json").write_text(
                    json.dumps(out, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass

        kr_n = len(kr_items or [])
        us_n = len(us_items or [])
        total_msg = (f"완료 · 국내 {kr_n} + 미국 {us_n}종목"
                     if market == "all" else f"완료 · {kr_n or us_n}종목")
        _discover_set(
            status="done", phase=None,
            finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            message=total_msg,
        )
        log.info("✓  Stage 2 스코어링 완료: market=%s kr=%d us=%d",
                 market, kr_n, us_n)
    except Exception as exc:
        log.exception("stage2 worker failed")
        _discover_set(
            status="error", phase=None, error=str(exc),
            finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        )
    finally:
        # 어떤 상황이든 running 상태 해제 보장
        with _discover_lock:
            if _discover_state["status"] == "running":
                _discover_state["status"] = "done"
                _discover_state["finished_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                _discover_state["message"] = "완료 (finally)"
                log.warning("[discover] finally 블록에서 running → done 강제 전환")


def _run_stage2_kr() -> list | None:
    """KR Stage 2 실행. 성공 시 items 리스트, 실패 시 None (state error 설정)."""
    _discover_set(phase="kr_stage1", message="🇰🇷 Stage 1 프리필터 실행 중…")
    stage1 = _stage1_prefilter_kr()
    if "error" in stage1:
        _discover_set(status="error", error=stage1["error"],
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    candidates = stage1["items"]
    total = len(candidates)
    if not total:
        _discover_set(status="error", error="KR Stage 1 결과 없음",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    sparklines = _load_ticker_sparklines_kr()

    # 섹터별 20일/5일 평균 수익률 (덜 오른 종목 보너스용)
    sector_rets: dict[str, list[float]] = {}
    sector_rets_5d: dict[str, list[float]] = {}
    for it in candidates:
        sp = sparklines.get(it["code"]) or []
        sect = it["sector"] or "_"
        if len(sp) >= 20 and sp[0]:
            sector_rets.setdefault(sect, []).append(
                (sp[-1] / sp[0] - 1) * 100
            )
        if len(sp) >= 5 and sp[-5]:
            sector_rets_5d.setdefault(sect, []).append(
                (sp[-1] / sp[-5] - 1) * 100
            )
    sector_avg_ret = {k: sum(v) / len(v) for k, v in sector_rets.items() if v}
    sector_avg_ret_5d = {k: sum(v) / len(v) for k, v in sector_rets_5d.items() if v}

    # ── Phase A: 상세 데이터 수집 (SQLite 우선 → HTTP 폴백) ──
    _discover_set(phase="kr_fetch", progress=0, total=total,
                  message=f"🇰🇷 상세 데이터 수집 중 (0/{total})")
    financials: dict[str, dict] = {}
    flows:      dict[str, dict] = {}
    charts:     dict[str, dict] = {}

    today_date = _get_trading_date()
    # YYYYMMDD → YYYY-MM-DD (flow_cache stale 비교용)
    today_iso = (
        f"{today_date[:4]}-{today_date[4:6]}-{today_date[6:8]}"
        if len(today_date) == 8 else None
    )
    _fetch_done = [0]  # mutable counter for progress
    _db_hits = [0]
    _http_falls = [0]

    _market_open = is_market_hours()

    def _fetch_kr_single(it):
        code = it["code"]
        fin = flow = chart = {}
        try:
            # SQLite 직접 조회 (HTTP 오버헤드 제거)
            if USE_SQLITE and _SQLITE_OK:
                fin   = _read_financial_db(code) or {}
                flow  = _read_flow_db(code, latest_trading_date=today_iso) or {}
                chart = _read_chart_db(code, 180, today_date) or {}
                if chart:
                    _db_hits[0] += 1

            # DB에 없으면 HTTP 폴백 — 장외에는 pykrx 호출 스킵
            if not chart.get("rsi_macd"):
                if _market_open:
                    chart = _call_api_internal(f"/api/chart/{code}") or {}
                _http_falls[0] += 1
            if not fin.get("per") and not fin.get("pbr"):
                if _market_open:
                    fin = _call_api_internal(f"/api/financial/{code}") or {}
            # flow는 staleness 발생 시(주말/공휴일 포함) HTTP 폴백 — /api/flow 자체가 60분 캐시
            if not flow.get("foreign_value"):
                flow = _call_api_internal(f"/api/flow/{code}") or {}
        except Exception as exc:
            log.debug("stage2 kr fetch fail %s: %s", code, exc)
        _fetch_done[0] += 1
        if _fetch_done[0] % 20 == 0 or _fetch_done[0] == total:
            _discover_set(progress=_fetch_done[0],
                          message=f"🇰🇷 상세 데이터 수집 중 ({_fetch_done[0]}/{total})")
        return code, fin, flow, chart

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_fetch_kr_single, it) for it in candidates]
        for future in as_completed(futures):
            try:
                code, fin, flow, chart = future.result(timeout=30)
                financials[code] = fin
                flows[code] = flow
                charts[code] = chart
            except Exception as exc:
                log.debug("stage2 kr future fail: %s", exc)

    log.info("[Stage2 KR] DB hits=%d, HTTP fallbacks=%d / total=%d",
             _db_hits[0], _http_falls[0], total)

    # 섹터별 PER 랭크 (오름차순 = 저평가 상위)
    sector_pers: dict[str, list[tuple[str, float]]] = {}
    sector_by_code = {it["code"]: (it["sector"] or "_") for it in candidates}
    for code, fin in financials.items():
        per = fin.get("per")
        if per and per > 0:
            sector_pers.setdefault(sector_by_code[code], []).append((code, per))
    sector_per_rank_pct: dict[str, float] = {}
    for sect, lst in sector_pers.items():
        lst.sort(key=lambda x: x[1])
        n = len(lst)
        for idx, (code, _) in enumerate(lst):
            sector_per_rank_pct[code] = (idx / n) if n > 0 else 0.5

    # ── Phase B: 스코어링 ──
    _discover_set(phase="kr_scoring", progress=0, message="🇰🇷 스코어링 중…")
    _scoring_total = len(candidates)
    # 공시 보너스 일괄 조회 (종목별 최근 7일 최고 점수) — 단일 SQL로 N개 처리
    _disclosure_bonus_map: dict = {}
    try:
        if _SQLITE_OK and USE_SQLITE:
            with _get_db() as _dconn:
                rows = _dconn.execute(
                    "SELECT stock_code, MAX(score) AS max_score "
                    "FROM disclosure_history "
                    "WHERE score >= 6 "
                    "  AND rcept_dt >= strftime('%Y%m%d', 'now', '-7 day') "
                    "  AND stock_code IS NOT NULL "
                    "GROUP BY stock_code"
                ).fetchall()
            for r in rows:
                ms = r["max_score"] or 0
                if ms >= 10:
                    _disclosure_bonus_map[r["stock_code"]] = (10, "공시_핵심이벤트", ms)
                elif ms >= 8:
                    _disclosure_bonus_map[r["stock_code"]] = (7, "공시_주요이벤트", ms)
                elif ms >= 6:
                    _disclosure_bonus_map[r["stock_code"]] = (4, "공시_참고이벤트", ms)
            log.info("[Stage2 KR] 공시 보너스 로드: %d종목", len(_disclosure_bonus_map))
    except Exception as exc:
        log.debug("[Stage2 KR] 공시 보너스 조회 실패: %s", exc)

    for idx, it in enumerate(candidates):
        # 스코어링 진행률도 tick 갱신 (stall 감지 방어)
        if idx % 100 == 0 or idx == _scoring_total - 1:
            _discover_set(progress=idx + 1, total=_scoring_total,
                          message=f"🇰🇷 스코어링 중 ({idx + 1}/{_scoring_total})")
        code = it["code"]
        fin = financials.get(code) or {}
        flow = flows.get(code) or {}
        chart = charts.get(code) or {}
        analysis = chart.get("analysis") if isinstance(chart, dict) else None
        highs = (chart.get("high") if isinstance(chart, dict) else None) or []
        high_180d = max(highs) if highs else None

        flow_score, flow_sub, flow_expl = _calc_flow_score_kr(flow)
        val_score,  val_sub,  val_expl  = _calc_valuation_score_kr(
            fin, it.get("price"), high_180d, sector_per_rank_pct.get(code)
        )
        tech_score, tech_sub, tech_expl = _calc_technical_score_kr(analysis)

        sp = sparklines.get(code) or []
        stock_ret_20d = ((sp[-1] / sp[0] - 1) * 100) if (len(sp) >= 20 and sp[0]) else None
        stock_ret_5d = ((sp[-1] / sp[-5] - 1) * 100) if (len(sp) >= 5 and sp[-5]) else None
        sect_key = it.get("sector") or "_"
        bonus, bonus_expl = _calc_undervalued_bonus(
            stock_ret_20d, sector_avg_ret.get(sect_key),
            stock_ret_5d, sector_avg_ret_5d.get(sect_key),
        )

        it["scores"]["flow"] = flow_score
        it["scores"]["valuation"] = val_score
        it["scores"]["technical"] = tech_score
        it["scores"]["undervalued_bonus"] = bonus
        it["sub_scores"]["flow"] = flow_sub
        it["sub_scores"]["valuation"] = val_sub
        it["sub_scores"]["technical"] = tech_sub
        it.setdefault("explanations", {})
        it["explanations"]["flow"] = flow_expl
        it["explanations"]["valuation"] = val_expl
        it["explanations"]["technical"] = tech_expl
        it["explanations"]["bonus"] = bonus_expl

        comments = (analysis or {}).get("comments", []) if analysis else []
        it["details"] = {
            "per":          fin.get("per"),
            "pbr":          fin.get("pbr"),
            "industry_per": fin.get("industry_per"),
            "bb_signal":    _find_signal(comments, "bollinger"),
            "trend_signal": _find_signal(comments, "trendline"),
            "fib_signal":   _find_signal(comments, "fibonacci"),
            "vol_signal":   _find_signal(comments, "volume"),
            "foreign_5d":   sum((flow.get("foreign_value") or [])[-5:]) if flow else None,
        }

        # ── 외국인 수급 강도 분석 ──
        foreign_tags = []
        if flow and not flow.get("error"):
            fv_arr = flow.get("foreign_value") or []
            iv_arr = flow.get("inst_value") or []
            if fv_arr:
                # 연속 순매수 일수
                f_streak = 0
                for v in reversed(fv_arr):
                    if v > 0:
                        f_streak += 1
                    else:
                        break
                it["details"]["foreign_streak"] = f_streak
                it["details"]["foreign_cum_5d"] = sum(fv_arr[-5:])
                it["details"]["foreign_cum_10d"] = sum(fv_arr[-10:]) if len(fv_arr) >= 10 else sum(fv_arr)
                it["details"]["foreign_today"] = fv_arr[-1] if fv_arr else 0

                # 기관 연속 순매수 일수
                i_streak = 0
                for v in reversed(iv_arr):
                    if v > 0:
                        i_streak += 1
                    else:
                        break
                it["details"]["inst_streak"] = i_streak
                it["details"]["inst_today"] = iv_arr[-1] if iv_arr else 0

                # 태그 생성
                if f_streak >= 5:
                    foreign_tags.append("외국인_5일연속")
                elif f_streak >= 3:
                    foreign_tags.append("외국인_3일연속")

                cum5 = sum(fv_arr[-5:])
                if cum5 > 50_000_000_000:       # 500억 이상
                    foreign_tags.append("외국인_대량매수")
                elif cum5 > 20_000_000_000:      # 200억 이상
                    foreign_tags.append("외국인_집중매수")

                # 외국인 + 기관 동시 매수 (스마트머니)
                if fv_arr[-1] > 0 and iv_arr and iv_arr[-1] > 0:
                    if f_streak >= 2 and i_streak >= 2:
                        foreign_tags.append("쌍끌이_매수")

                it["details"]["foreign_tags"] = foreign_tags

        # RSI/MACD 태그 자동 생성
        rsi_macd_tags = []
        rm = chart.get("rsi_macd") if isinstance(chart, dict) else None
        if rm:
            rsi_vals = rm.get("rsi") or []
            macd_vals = rm.get("macd") or []
            macd_sig  = rm.get("macd_signal") or []
            macd_hist_vals = rm.get("macd_hist") or []
            if rsi_vals:
                rsi_cur = rsi_vals[-1]
                if rsi_cur >= 70:   rsi_macd_tags.append("과매수_RSI")
                elif rsi_cur >= 60: rsi_macd_tags.append("상승진행_RSI")
                elif rsi_cur <= 30: rsi_macd_tags.append("과매도_RSI")
                elif rsi_cur <= 40: rsi_macd_tags.append("과매도회복_RSI")
                it["details"]["rsi"] = round(rsi_cur, 2)
            rsi_macd_tags.extend(_generate_macd_tags(macd_vals, macd_sig, macd_hist_vals))
            # 다이버전스 태그
            divs = rm.get("divergences") or []
            for dv in divs:
                dtype = dv.get("type")
                ind = dv.get("indicator", "")
                if dtype == "bearish":
                    rsi_macd_tags.append(f"베어리시_{ind}_다이버전스")
                elif dtype == "bullish":
                    rsi_macd_tags.append(f"불리시_{ind}_다이버전스")
            it["details"]["divergences"] = divs
        # 공시 보너스 (최근 7일 점수 6+ 있는 종목에 가산점)
        disc_bonus_pts, disc_tag, disc_max = _disclosure_bonus_map.get(code, (0, None, 0))
        if disc_tag:
            rsi_macd_tags.append(disc_tag)
        it["details"]["rsi_macd_tags"] = rsi_macd_tags + foreign_tags
        it["details"]["disclosure_bonus"] = disc_bonus_pts
        it["details"]["disclosure_max_score"] = disc_max
        it["scores"]["disclosure_bonus"] = disc_bonus_pts
        it["total_score"] = (
            it["scores"]["momentum"] + it["scores"]["sector"] +
            flow_score + val_score + tech_score + bonus + disc_bonus_pts
        )
        _discover_set(progress=idx + 1)

    candidates.sort(key=lambda x: x["total_score"], reverse=True)

    result = {
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "kr",
        "stage":         2,
        "total_scanned": stage1["total_scanned"],
        "kospi_count":   stage1.get("kospi_count"),
        "kosdaq_count":  stage1.get("kosdaq_count"),
        "items":         candidates,
    }

    out = BASE_DIR / "cache" / "discover_kr_stage2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    # 추천 이력 스냅샷
    try:
        top10 = sorted(candidates, key=lambda x: x.get("total_score") or 0, reverse=True)[:10]
        save_recommendation_snapshot("discover_kr", top10, market="kr")
    except Exception as exc:
        log.debug("[추천이력] kr 저장 실패: %s", exc)

    # KBJ P8 삭제: alert_discovery_new_entries 호출 — 발굴 신규 진입 알림 폐지(사용자 결정 2026-10-13)

    return candidates


def _run_stage2_us() -> list | None:
    """US Stage 2: S&P500 에서 상위 200종목 yfinance 상세 스코어링."""
    _discover_set(phase="us_stage1", message="🇺🇸 Stage 1 프리필터 실행 중…")
    stage1 = _stage1_prefilter_us()
    if "error" in stage1:
        _discover_set(status="error", error=f"US: {stage1['error']}",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    candidates = stage1["items"]
    total = len(candidates)
    if not total:
        _discover_set(status="error", error="US Stage 1 결과 없음",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    # 섹터 데이터 (us_market) — stocks 배열 포함
    us_data = _fetch_us_market_data()
    sector_by_name = {x["name"]: x for x in (us_data.get("sectors") or [])}

    try:
        import yfinance as _yf
    except ImportError:
        _discover_set(status="error", error="yfinance 미설치",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    _discover_set(phase="us_fetch", progress=0, total=total,
                  message=f"🇺🇸 yfinance 상세 수집 중 (0/{total})")

    infos:  dict[str, dict] = {}
    charts: dict[str, dict] = {}
    ret_20d: dict[str, float] = {}

    today_kst_str = now_kst().strftime("%Y%m%d")
    _us_done = [0]
    _us_db_hits = [0]
    _us_http_falls = [0]

    def _fetch_us_single(it):
        sym = it["code"]
        info = None
        chart = None

        # yinfo: SQLite → JSON file → yfinance API
        if USE_SQLITE and _SQLITE_OK:
            info = _read_yinfo_db(sym)
        if not info:
            info_cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
            if info_cache.exists():
                try:
                    age_hr = (now_kst().timestamp() - info_cache.stat().st_mtime) / 3600
                    if age_hr < 24:
                        info = json.loads(info_cache.read_text(encoding="utf-8"))
                except Exception:
                    info = None
        if info is None:
            try:
                raw = _yf.Ticker(sym).info or {}
                info = {k: v for k, v in raw.items()
                        if isinstance(v, (int, float, str, bool, type(None)))}
                info_cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
                info_cache.parent.mkdir(exist_ok=True)
                info_cache.write_text(json.dumps(info, ensure_ascii=False),
                                      encoding="utf-8")
                # SQLite 동시 기록
                if USE_SQLITE and _SQLITE_OK:
                    try:
                        with _get_db() as _conn:
                            _conn.execute(
                                "INSERT OR REPLACE INTO yinfo_cache (symbol, info_json) VALUES (?,?)",
                                (sym, json.dumps(info, ensure_ascii=False)),
                            )
                            _conn.commit()
                    except Exception:
                        pass
            except Exception as exc:
                log.debug("us yfinance info fail %s: %s", sym, exc)
                info = {}

        # chart: SQLite → HTTP 폴백 (장외 시 API 호출 스킵)
        if USE_SQLITE and _SQLITE_OK:
            chart = _read_chart_db(sym, 180, today_kst_str)
            if chart:
                _us_db_hits[0] += 1
        if not chart or not chart.get("rsi_macd"):
            if _is_us_market_hours():
                chart = _call_api_internal(f"/api/us/chart/{sym}") or {}
            else:
                chart = chart or {}
            _us_http_falls[0] += 1

        closes = (chart.get("close") if isinstance(chart, dict) else None) or []
        r20 = None
        if len(closes) >= 20 and closes[-20]:
            r20 = (closes[-1] / closes[-20] - 1) * 100

        _us_done[0] += 1
        if _us_done[0] % 20 == 0 or _us_done[0] == total:
            _discover_set(progress=_us_done[0],
                          message=f"🇺🇸 yfinance 상세 수집 중 ({_us_done[0]}/{total})")
        return sym, info, chart, r20

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_fetch_us_single, it) for it in candidates]
        for future in as_completed(futures):
            try:
                sym, info, chart, r20 = future.result(timeout=30)
                infos[sym] = info
                charts[sym] = chart
                if r20 is not None:
                    ret_20d[sym] = r20
            except Exception as exc:
                log.debug("stage2 us future fail: %s", exc)

    log.info("[Stage2 US] DB hits=%d, HTTP fallbacks=%d / total=%d",
             _us_db_hits[0], _us_http_falls[0], total)

    # 섹터별 20일 평균 수익률
    sector_rets: dict[str, list[float]] = {}
    for it in candidates:
        r = ret_20d.get(it["code"])
        if r is not None:
            sector_rets.setdefault(it.get("sector") or "_", []).append(r)
    sector_avg_ret = {k: sum(v) / len(v) for k, v in sector_rets.items() if v}

    # ── 스코어링 ──
    _discover_set(phase="us_scoring", progress=0, message="🇺🇸 스코어링 중…")
    for idx, it in enumerate(candidates):
        sym = it["code"]
        info = infos.get(sym) or {}
        chart = charts.get(sym) or {}
        analysis = chart.get("analysis") if isinstance(chart, dict) else None
        high_52w = info.get("fiftyTwoWeekHigh")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice") or it.get("price")

        flow_score, flow_sub, flow_expl = _calc_flow_score_us(info)
        val_score,  val_sub,  val_expl  = _calc_valuation_score_us(
            info, high_52w, current_price
        )
        tech_score, tech_sub, tech_expl = _calc_technical_score_kr(analysis)

        bonus, bonus_expl = _calc_undervalued_bonus(
            ret_20d.get(sym), sector_avg_ret.get(it.get("sector") or "_")
        )

        it["scores"]["flow"] = flow_score
        it["scores"]["valuation"] = val_score
        it["scores"]["technical"] = tech_score
        it["scores"]["undervalued_bonus"] = bonus
        it["sub_scores"]["flow"] = flow_sub
        it["sub_scores"]["valuation"] = val_sub
        it["sub_scores"]["technical"] = tech_sub
        it.setdefault("explanations", {})
        it["explanations"]["flow"] = flow_expl
        it["explanations"]["valuation"] = val_expl
        it["explanations"]["technical"] = tech_expl
        it["explanations"]["bonus"] = bonus_expl

        comments = (analysis or {}).get("comments", []) if analysis else []
        it["details"] = {
            "per":          info.get("trailingPE"),
            "pbr":          info.get("priceToBook"),
            "forward_per":  info.get("forwardPE"),
            "recommendation": info.get("recommendationKey"),
            "inst_pct":     info.get("heldPercentInstitutions"),
            "insider_pct":  info.get("heldPercentInsiders"),
            "bb_signal":    _find_signal(comments, "bollinger"),
            "trend_signal": _find_signal(comments, "trendline"),
            "fib_signal":   _find_signal(comments, "fibonacci"),
            "vol_signal":   _find_signal(comments, "volume"),
        }
        # US RSI/MACD 태그
        us_rsi_tags = []
        rm = chart.get("rsi_macd") if isinstance(chart, dict) else None
        if rm:
            rsi_vals = rm.get("rsi") or []
            macd_vals = rm.get("macd") or []
            macd_sig  = rm.get("macd_signal") or []
            macd_hist_vals = rm.get("macd_hist") or []
            if rsi_vals:
                rsi_cur = rsi_vals[-1]
                if rsi_cur >= 70:   us_rsi_tags.append("과매수_RSI")
                elif rsi_cur >= 60: us_rsi_tags.append("상승진행_RSI")
                elif rsi_cur <= 30: us_rsi_tags.append("과매도_RSI")
                elif rsi_cur <= 40: us_rsi_tags.append("과매도회복_RSI")
                it["details"]["rsi"] = round(rsi_cur, 2)
            us_rsi_tags.extend(_generate_macd_tags(macd_vals, macd_sig, macd_hist_vals))
            divs = rm.get("divergences") or []
            for dv in divs:
                dtype = dv.get("type")
                ind = dv.get("indicator", "")
                if dtype == "bearish":
                    us_rsi_tags.append(f"베어리시_{ind}_다이버전스")
                elif dtype == "bullish":
                    us_rsi_tags.append(f"불리시_{ind}_다이버전스")
            it["details"]["divergences"] = divs
        it["details"]["rsi_macd_tags"] = us_rsi_tags

        it["total_score"] = (
            it["scores"]["momentum"] + it["scores"]["sector"] +
            flow_score + val_score + tech_score + bonus
        )
        _discover_set(progress=idx + 1)

    candidates.sort(key=lambda x: x["total_score"], reverse=True)

    result = {
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "us",
        "stage":         2,
        "total_scanned": stage1["total_scanned"],
        "items":         candidates,
    }

    out = BASE_DIR / "cache" / "discover_us_stage2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    # 추천 이력 스냅샷
    try:
        top10 = sorted(candidates, key=lambda x: x.get("total_score") or 0, reverse=True)[:10]
        save_recommendation_snapshot("discover_us", top10, market="us")
    except Exception as exc:
        log.debug("[추천이력] us 저장 실패: %s", exc)

    return candidates


@app.route("/api/discover/scan", methods=["POST"])
def api_discover_scan():
    """Stage 2 백그라운드 스캔 시작. market ∈ {kr, us, all}."""
    market = (request.args.get("market") or "kr").lower()
    if market not in ("kr", "us", "all"):
        return jsonify({"error": "market 파라미터는 kr/us/all"}), 400

    with _discover_lock:
        if _discover_state["status"] in ("starting", "running"):
            return jsonify({
                "status": "already_running",
                "state":  dict(_discover_state),
            })
        _discover_state.update({
            "status":     "starting",
            "phase":      None,
            "market":     market,
            "progress":   0,
            "total":      0,
            "error":      None,
            "message":    "시작 중…",
            "started_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
        })

    threading.Thread(target=_stage2_scoring_worker, args=(market,),
                     daemon=True, name="discover-stage2").start()
    return jsonify({"status": "started", "state": _discover_get_state()})


@app.route("/api/discover/progress")
def api_discover_progress():
    """현재 Stage 2 스캔 진행 상태.
    Stall detection: 마지막 progress/phase 변경 이후 120초 무변화 시 고착 판정.
    절대 시간 제한: 15분 (fetch/scoring 합산 worst case)."""
    state = _discover_get_state()
    if state["status"] in ("starting", "running"):
        try:
            from datetime import datetime as _dt
            now_naive = now_kst().replace(tzinfo=None)

            # 1) 절대 시간 제한 (15분 — US yfinance 최악 케이스 고려)
            started = _dt.strptime(state["started_at"], "%Y-%m-%d %H:%M:%S") \
                if state.get("started_at") else None
            elapsed = (now_naive - started).total_seconds() if started else 0

            # 2) stall 감지 (마지막 진행 이후 120초 무변화)
            tick_str = state.get("last_tick") or state.get("started_at")
            tick = _dt.strptime(tick_str, "%Y-%m-%d %H:%M:%S") if tick_str else None
            stall = (now_naive - tick).total_seconds() if tick else 0

            if elapsed > 900:
                _discover_set(
                    status="error", phase=None,
                    error=f"15분 초과 (총 {int(elapsed)}초) — 자동 리셋",
                    finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                )
                state = _discover_get_state()
                log.warning("[discover] 15분 초과 → 자동 리셋")
            elif stall > 120:
                _discover_set(
                    status="error", phase=None,
                    error=f"진행 정체 감지 ({int(stall)}초 무변화) — 자동 리셋",
                    finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                )
                state = _discover_get_state()
                log.warning("[discover] stall 감지 (%d초) → 자동 리셋", int(stall))
        except Exception as exc:
            log.debug("[discover] progress timeout check: %s", exc)
    return jsonify(state)


@app.route("/api/discover/reset", methods=["POST"])
def api_discover_reset():
    """Stage 2 상태 강제 리셋."""
    _discover_set(
        status="idle", phase=None, progress=0, total=0,
        error=None, message=None,
        finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    )
    log.info("[discover] 수동 강제 리셋")
    return jsonify({"status": "reset"})


# ─────────────────────────────────────────────────────────────────────────
# AI 에이전트 파이프라인 (규칙 기반, 비용 $0)
# ─────────────────────────────────────────────────────────────────────────
_agent_running = [False]


# KBJ P2 삭제(설계 §5.10): api_agent_run — #44 인증 없는 발송 경로


@app.route("/api/agent/result")
def api_agent_result():
    """최신 에이전트 결과 조회. ?market=kr|us.
    주중 조회 시 final_picks의 price/change_pct를 실시간 값으로 overlay."""
    market = (request.args.get("market") or "kr").lower()
    fname = "agent_result_us_latest.json" if market == "us" else "agent_result_latest.json"
    path = BASE_DIR / "cache" / fname
    if not path.exists():
        fallback = BASE_DIR / "cache" / "agent_result_latest.json"
        if fallback.exists() and market != "us":
            path = fallback
        else:
            return jsonify({"error": "결과 없음 — 파이프라인을 먼저 실행하세요"}), 404
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # final_picks 실시간 overlay (주중만)
        patched = _apply_live_prices_to_items(data.get("final_picks") or [])
        if patched:
            data["_live_overlay"] = {"patched": patched,
                                     "at": now_kst().strftime("%Y-%m-%d %H:%M:%S")}
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/agent/status")
def api_agent_status():
    """에이전트 실행 상태."""
    return jsonify({"running": _agent_running[0]})


# ─────────────────────────────────────────────────────────────────────────
# 백테스트 엔진 (규칙 기반, OHLCV 기반)
# ─────────────────────────────────────────────────────────────────────────

# KBJ P8 삭제: _bt_calc_rsi — kbj 페이지 12 백테스트(/api/backtest — DSL 규칙 백테스트, ADR 0032)


# KBJ P8 삭제: _bt_check_entry — kbj 페이지 12 백테스트(/api/backtest — DSL 규칙 백테스트, ADR 0032)


# KBJ P8 삭제: api_backtest_available_tags — kbj 페이지 12 백테스트(/api/backtest — DSL 규칙 백테스트, ADR 0032)


# KBJ P8 삭제: _bt_check_stage2_entry — kbj 페이지 12 백테스트(/api/backtest — DSL 규칙 백테스트, ADR 0032)


# KBJ P8 삭제: _bt_simulate_trade — kbj 페이지 12 백테스트(/api/backtest — DSL 규칙 백테스트, ADR 0032)


# KBJ P8 삭제: api_backtest — kbj 페이지 12 백테스트(/api/backtest — DSL 규칙 백테스트, ADR 0032)


# ── 상관관계 매트릭스 ────────────────────────────────────
# KBJ P8 삭제: _load_server_portfolio_codes — api_correlation 전용 도우미(P8 삭제)


# KBJ P8 삭제: _load_watchlist_codes — api_correlation 전용 도우미(P8 삭제)


# KBJ P8 삭제: _compute_correlation — kbj 페이지 12 상관관계(/api/portfolio/correlation — kbj.engines.journal.correlation)


# KBJ P8 삭제: api_correlation — kbj /api/portfolio/correlation


# ── 추천 성과 검증 (recommendation_history) ────────────────────────────────────
def _init_recommendation_history():
    """추천 이력 테이블 생성. _startup에서 호출."""
    if not (_SQLITE_OK and USE_SQLITE):
        return
    try:
        with _get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recommendation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    rank INTEGER,
                    code TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    score REAL,
                    price_at_rec REAL,
                    tags_json TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(date, source, code)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendation_history(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_source ON recommendation_history(source, date DESC)")
            conn.commit()
        log.info("[추천이력] 테이블 초기화")
    except Exception as exc:
        log.warning("[추천이력] 테이블 생성 실패: %s", exc)


def save_recommendation_snapshot(source: str, picks: list, market: str = "kr",
                                 limit: int = 10) -> int:
    """추천 결과 상위 limit 종목을 recommendation_history에 저장.
    source: discover_kr | discover_us | agent_kr | agent_us"""
    if not (_SQLITE_OK and USE_SQLITE) or not picks:
        return 0
    today = now_kst().strftime("%Y-%m-%d")
    saved = 0
    try:
        with _get_db() as conn:
            for rank, pick in enumerate((picks or [])[:limit], 1):
                code = (pick.get("code") or "").strip()
                if not code:
                    continue
                name = pick.get("name") or code
                score = pick.get("total_score") or pick.get("score") or 0
                tags = pick.get("tags") or pick.get("swing_tags") or []
                if not tags:
                    det = pick.get("details") or {}
                    tags = det.get("rsi_macd_tags") or []
                price = pick.get("price") or pick.get("close") or 0
                if not price:
                    r = conn.execute(
                        "SELECT close FROM stocks WHERE code = ?", (code,)
                    ).fetchone()
                    if r:
                        price = r["close"] or 0
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO recommendation_history "
                        "(date, source, rank, code, name, market, score, price_at_rec, tags_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (today, source, rank, code, name, market, score, price,
                         json.dumps(tags, ensure_ascii=False))
                    )
                    if conn.total_changes > 0:
                        saved += 1
                except Exception:
                    pass
            conn.commit()
        log.info("[추천이력] %s (%s) %d/%d 저장", source, today, saved, min(len(picks), limit))
    except Exception as exc:
        log.debug("[추천이력] save 실패: %s", exc)
    return saved


def migrate_discover_to_recommendations() -> dict:
    """discover_results 에 있는 과거 스냅샷을 recommendation_history로 소급 저장."""
    if not (_SQLITE_OK and USE_SQLITE):
        return {"error": "SQLite 비활성"}
    total = 0; snapshots = 0
    try:
        with _get_db() as conn:
            rows = conn.execute("""
                SELECT market, items_json, DATE(updated_at) as day
                FROM discover_results
                WHERE items_json IS NOT NULL
                ORDER BY updated_at ASC
            """).fetchall()
            for row in rows:
                day = row["day"]
                mkt = (row["market"] or "").lower()
                if mkt not in ("kr", "us"):
                    continue
                source = f"discover_{mkt}"
                try:
                    items = json.loads(row["items_json"] or "[]")
                except Exception:
                    continue
                items.sort(key=lambda x: x.get("total_score") or 0, reverse=True)
                for rank, it in enumerate(items[:10], 1):
                    code = (it.get("code") or "").strip()
                    if not code:
                        continue
                    name = it.get("name") or code
                    score = it.get("total_score") or 0
                    tags = (it.get("details") or {}).get("rsi_macd_tags") or []
                    price = it.get("price") or 0
                    if not price:
                        pr = conn.execute(
                            "SELECT close FROM ohlcv WHERE code = ? AND date <= ? "
                            "ORDER BY date DESC LIMIT 1", (code, day)
                        ).fetchone()
                        if pr:
                            price = pr["close"] or 0
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO recommendation_history "
                            "(date, source, rank, code, name, market, score, price_at_rec, tags_json) "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (day, source, rank, code, name, mkt, score, price,
                             json.dumps(tags, ensure_ascii=False))
                        )
                        if conn.total_changes > 0:
                            total += 1
                    except Exception:
                        pass
                snapshots += 1
            conn.commit()
    except Exception as exc:
        log.warning("[마이그레이션] %s", exc)
    log.info("[추천이력] discover 스냅샷 %d개 → %d건 소급", snapshots, total)
    return {"snapshots": snapshots, "inserted": total}


# KBJ P8 삭제: api_recommendation_performance — kbj 페이지 12 추천 성과(/api/journal/picks)


# KBJ P8 삭제: api_recommendation_migrate — kbj legacy 이관(journal import-sd — recommendation_history)


# ── 장마감 시황 자동 요약 (순수 데이터 기반) ────────────────────────────────────

# stocks.market_cap 이 며칠 지나면 '오늘 시총' 이라고 부를 수 없는가.
# 시총은 주가 x 상장주식수라 주가가 움직인 만큼 매일 바뀐다. 며칠 전 값을
# 오늘 값인 척 내보내면 등락률(오늘)과 시총(며칠 전)이 한 줄에 섞인다.
# 7일로 잡은 이유는 주말·연휴를 한 번 건너뛰어도 정상으로 보되, 그 이상
# 묵으면 반드시 눈에 띄게 하려는 것이다. 바꾸려면 여기만 고친다.
_CAP_STALE_DAYS = 7

# 신고가 등급별로 메시지에 몇 종목까지 이름을 적는가. None = 전 종목.
#
# **세 등급 모두 전부 적는다.** 사용자가 이 메시지를 읽는 이유가 종목 이름이고,
# '외 N종목' 으로 접으면 접힌 쪽을 확인할 방법이 메시지 안에 없다.
#
# 60일만 5종목으로 접어 뒀다가 2026-09-18 에 풀었다. 접은 이유는 '등급이 가장
# 낮아 하루에 수백 종목이 설 수 있다' 였는데, 그건 길이 걱정이었지 내용 판단이
# 아니었다. 길이는 `_split_telegram_lines` 가 줄 경계에서 나눠 조각 번호를 붙여
# 보내므로 이미 해결돼 있다 — 줄이 잘리지도, 종목이 사라지지도 않는다.
# 읽는 사람이 긴 날을 긴 목록으로 보는 것과, 짧은 목록을 보고 나머지를 못 보는
# 것 중에서는 앞이 낫다. 접힌 이름은 어디에서도 볼 수 없었다.
#
# 다시 접고 싶으면 그 등급에 숫자를 넣으면 된다. 접기 코드는 그대로 살아 있고
# (`cap_n is None` 분기), 검사도 두 경우를 다 돌린다.
_NH_LIST_MAX = {"hist": None, "w52": None, "d60": None}


# 신고가 등급별로 **상위 몇 종목에 수급을 붙이는가.** 0 = 안 붙인다.
#
# 52주만 5종목이다. 사용자가 보는 자리가 거기다 — 역사적은 하루 한두 종목이라
# 따로 셀 것이 없고, 60일은 수십~수백 종목이라 다 붙이면 줄이 두 배가 된다.
# 다른 등급도 켜고 싶으면 숫자만 올리면 된다.
#
# '상위' 는 목록과 같은 기준, 즉 거래대금 순이다. 다른 기준으로 자르면 화면의
# 1~5번째 줄과 수급이 붙은 줄이 어긋난다.
_NH_FLOW_MAX = {"hist": 0, "w52": 5, "d60": 0}


def _newhigh_flow(conn, codes: list) -> tuple[dict, str | None]:
    """신고가 종목의 외국인·기관 순매수. ({코드: (외인억, 기관억)}, 최신거래일)

    flow_cache 는 종목별로 날짜·값 배열을 들고 있다. 마지막 칸이 가장 최근이다.
    **날짜를 함께 돌려준다** — 당일 확정치는 장마감 후 한참 뒤에 나오므로
    16:00 시황에서는 대개 전일 값이고, 그걸 오늘 값인 척 적으면 안 된다.

    값이 없는 종목은 딕셔너리에 넣지 않는다. 0 을 넣으면 '순매수가 0 이었다' 와
    '못 받았다' 가 같은 얼굴이 된다.
    """
    if not codes:
        return {}, None
    out, latest = {}, None
    qs = ",".join("?" * len(codes))
    try:
        rows = conn.execute(
            f"SELECT code, dates_json, foreign_value_json, inst_value_json "
            f"FROM flow_cache WHERE code IN ({qs})", list(codes)).fetchall()
    except Exception as exc:                                  # noqa: BLE001
        log.debug("[신고가 수급] 조회 실패: %s", exc)
        return {}, None
    for r in rows:
        dts = _parse_json_list(r["dates_json"])
        fv = _parse_json_list(r["foreign_value_json"])
        iv = _parse_json_list(r["inst_value_json"])
        if not dts:
            continue
        d_last = dts[-1]
        if latest is None or d_last > latest:
            latest = d_last
        f_eok = fv[-1] / 1e8 if fv else None
        i_eok = iv[-1] / 1e8 if iv else None
        if f_eok is None and i_eok is None:
            continue
        out[r["code"]] = (f_eok, i_eok, d_last)
    return out, latest


def _fmt_nh_flow(entry, latest: str | None) -> str:
    """신고가 줄 뒤에 붙일 수급 한 토막. 못 받았으면 그렇게 적는다."""
    if entry is None:
        return " · <i>수급 없음</i>"
    f_eok, i_eok, d_last = entry
    def one(v):
        return "—" if v is None else f"{v:+,.0f}억"
    tail = f" · 외인 {one(f_eok)} 기관 {one(i_eok)}"
    # 이 종목만 날짜가 다르면 그 자리에 적는다. 섹션 머리말의 날짜는 최신일
    # 하나뿐이라, 종목마다 다를 때 줄과 머리말이 어긋난다.
    if latest and d_last != latest:
        tail += f" <i>({d_last[5:].replace('-', '/')})</i>"
    return tail


def _ohlcv_scope_note(scanned: int, universe: int | None = None) -> str:
    """신고가가 **무엇을 모집단으로 한 결과인지** 한 조각.

    모집단은 ETF/ETN 을 뺀 시총 1,000억 이상 종목이다
    (ohlcv_autofill.MIN_MARKET_CAP_WON). 문구는 ohlcv_autofill.coverage_note
    한 곳에서 만든다 — 이 함수는 그것을 부르는 얇은 껍데기다.

    `scanned` 는 실제로 판정한 수(일봉이 있는 종목), `universe` 는 모집단 수다.
    둘을 같이 적는 이유: 재배포 직후 일봉을 채우는 중이거나 일부가 실패한
    날, 600종목만 보고 "시총 1,000억 이상 대상" 이라고만 쓰면 읽는 사람은
    1,400종목 기준으로 읽는다. 모자라면 '일봉 미수집 N종목' 이 붙는다.

    예전에는 거래대금 상위 300종목만 받아 두어 "222종목 대상" 이 나갔다 —
    ETF 가 상위를 차지해 주식은 그만큼만 남았던 것이다.
    """
    try:
        import ohlcv_autofill as _oa
        return _oa.coverage_note(scanned, universe)
    except Exception:                                      # noqa: BLE001
        return f"{scanned:,}종목 대상"


def _ohlcv_fill_hint() -> str:
    """일봉이 모자랄 때 '언제 마지막으로 채워졌는지' 를 덧붙인다.

    '0일뿐' 만 보면 언제부터 빈 것인지, 수집이 아예 안 도는 것인지 모른다.
    """
    try:
        import ohlcv_autofill as _oa
        st = _oa.status()
    except Exception as exc:                               # noqa: BLE001
        return f"일봉 현황 조회 실패: {type(exc).__name__}"
    if not st.get("rows"):
        return ("일봉 테이블이 비어 있다 — 평일 16:10 채움 잡과 부팅 스레드가 "
                "채운다. /api/ops/ohlcv/status 로 확인")
    return (f"일봉 {st['rows']:,}행/{st['codes']}종목, "
            f"최신 {st.get('last') or '?'}")


def _fmt_cap(cap, updated: str | None = None):
    """시가총액 표시. `updated` 는 그 값이 실제로 언제 것인지(YYYYMMDD).

    단위는 **원**이다. stocks.market_cap 에 쓰는 경로가 네이버 폴링 응답의
    marketValueFullRaw(원) 하나뿐이라 그렇다. KIS(hts_avls, 억)·KRX(MKTCAP/1e6,
    백만원) 값은 API 응답으로만 나가고 이 열에 들어오지 않는다 — 들어오게
    되면 1e8 배 어긋나므로 그때는 이 함수부터 다시 봐야 한다.

    `updated` 가 없거나 _CAP_STALE_DAYS 보다 묵었으면 `*` 를 붙인다.
    값을 지우지 않는 이유는, 낡은 시총도 자릿수를 가늠하는 데는 쓸모가 있고
    아예 안 보여 주면 '시총을 못 구했다' 와 구분되지 않기 때문이다. 대신
    낡았다는 사실을 숨기지 않는다. `*` 의 뜻은 섹션 머리에 한 번 적는다.
    """
    if not cap or cap <= 0:
        return ""
    try:
        mark = "" if _cap_is_fresh(updated) else "*"
        if cap >= 1e12:
            return f" [{cap / 1e12:.1f}조{mark}]"
        if cap >= 1e8:
            return f" [{cap / 1e8:.0f}억{mark}]"
    except Exception:
        pass
    return ""


def _cap_is_fresh(updated: str | None) -> bool:
    """market_cap_updated(YYYYMMDD) 가 _CAP_STALE_DAYS 안쪽인가.

    모르면(None/빈값/형식 불명) **낡은 것으로 본다.** 언제 것인지 모르는 값을
    오늘 값으로 쳐 주면 지금 고치려는 사고가 그대로 되돌아온다.
    """
    if not updated:
        return False
    try:
        d = datetime.strptime(str(updated).strip()[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return False
    return 0 <= (now_kst().date() - d).days <= _CAP_STALE_DAYS


# ── 시총 대비 강도 표기 헬퍼 (4-5: 수급 동향 / 거래대금 의미 부여) ────────────
def _get_market_cap(code: str) -> int:
    """종목코드 → 시가총액(원). 없거나 0 이면 0 반환."""
    if not (_SQLITE_OK and USE_SQLITE):
        return 0
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT market_cap FROM stocks "
                "WHERE code=? AND market_cap IS NOT NULL AND market_cap > 0",
                (code,)).fetchone()
        return int(row["market_cap"]) if row else 0
    except Exception:
        return 0


def _intensity_emoji(ratio_pct: float) -> str:
    """시총 대비 비율(%) → 강도. 절댓값 기준 (매수/매도 동일)."""
    a = abs(ratio_pct)
    if a >= 1.0:  return "🔥🔥🔥"
    if a >= 0.3:  return "🔥🔥"
    if a >= 0.1:  return "🔥"
    return "·"


def _turnover_emoji(turnover_pct: float) -> str:
    """거래대금 시총 회전율(%) → 강도. (거래는 더 큰 비율이 정상)"""
    if turnover_pct >= 5.0:  return "🔥🔥🔥"
    if turnover_pct >= 2.0:  return "🔥🔥"
    if turnover_pct >= 0.5:  return "🔥"
    return "·"


def _format_flow_line(code: str, name: str, flow_eok: float) -> str:
    """외국인/기관 순매수 종목 라인 — 시총 대비 비율 + 강도 추가.
    flow_eok: 순매수액 (억원)."""
    base = f"  {name} {flow_eok:+,.0f}억"
    mcap = _get_market_cap(code)
    # 시총 100억 미만 페니/소형주는 비율 왜곡 — 표기 생략
    if mcap < 10_000_000_000:
        return base
    flow_won = flow_eok * 100_000_000
    ratio_pct = (flow_won / mcap) * 100
    return f"{base} (시총 {ratio_pct:+.2f}%) {_intensity_emoji(ratio_pct)}"


def _format_volume_line(code: str, name: str, change_pct: float,
                       volume_mn: float, sector: str = "") -> str:
    """거래대금 상위 종목 라인 — 회전율(%) 추가.
    volume_mn: 거래대금 (백만원, stocks.volume_mn 단위)."""
    # 거래대금 표시는 억 단위로 변환
    vol_eok = volume_mn / 100  # 백만 → 억 (백만/100 = 억은 아니지만, 백만*1=백만이고 1억=100백만 이므로 /100)
    base = f"  {name} {change_pct:+.1f}% (거래대금 {vol_eok:,.0f}억)"
    if sector:
        sector_tail = f" — {sector}"
    else:
        sector_tail = ""
    mcap = _get_market_cap(code)
    if mcap < 10_000_000_000:
        return base + sector_tail
    volume_won = volume_mn * 1_000_000  # 백만원 → 원
    turnover_pct = (volume_won / mcap) * 100
    return f"{base} 회전율 {turnover_pct:.2f}% {_turnover_emoji(turnover_pct)}{sector_tail}"


def _parse_json_list(s):
    if not s:
        return []
    try:
        return json.loads(s)
    except Exception:
        return []


# ─── 미국 지수: 시황을 만들 때 직접 받는다 ───
# 예전에는 data.json 의 market_overview 를 읽었다. 그 값은 data_fetcher 가 돌 때만
# 갱신되고 **몇 월 며칠 종가인지 적혀 있지 않다**. 신선도는 data.json 의
# actual_date(= 한국 거래일)로 쟀는데, 한국 16:00 시황이 보여 줄 미국 값은 늘
# 하루 전 미국 거래일 종가라 이 비교는 맞을 수가 없다 — 멀쩡한 값에 ⚠️ 가 붙고,
# 며칠 묵은 값이어도 data.json 날짜만 오늘이면 표시 없이 나갔다.
# 이제 yfinance 일봉을 그 자리에서 받아 **마지막 봉의 미국 날짜**를 함께 적는다.
# 못 받으면 옛 값을 쓰지 않고 못 받았다고 적는다.
US_INDEX_TICKERS = {
    "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI",
    "Russell 2000": "^RUT", "SOX (반도체)": "^SOX", "나스닥100 선물": "NQ=F",
}


def _fetch_us_indices_live(names) -> tuple[list, list]:
    """[(이름, 값, 등락률%, 'MM/DD', 장중여부)], [실패 사유]"""
    rows, errors = [], []
    # 그냥 import 하면 다른 스레드의 첫 import 와 겹쳤을 때 반쯤 만들어진
    # 모듈이 와서 Ticker 접근이 AttributeError 로 죽는다. _yf() 가 그걸 막는다.
    _yf_mod = _yf()
    if _yf_mod is None:
        return rows, ["yfinance 사용 불가"]
    from zoneinfo import ZoneInfo
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    for name in names:
        sym = US_INDEX_TICKERS[name]
        try:
            t = _yf_mod.Ticker(sym)
            h = t.history(period="10d", interval="1d", auto_adjust=False)
            # 야후가 **마지막 일봉의 종가를 비워 두는** 날이 있다 — 2026-09-23 19:22
            # KST 러너 실측에서 ^GSPC·^IXIC 의 9/22 봉이 close=NaN 이었다(장 마감
            # 14시간 뒤). 빈 봉을 버리면 하루 묵은 9/21 값이 '09/21 종가' 로 나간다 —
            # 표기는 정직하지만 시황이 하루 늦다. 그 날짜의 종가는 시세 메타
            # (fast_info.last_price)에 있으므로 거기서 채운다. 못 채우면 묵은 값과
            # 그 날짜를 그대로 적는다.
            nan_day = None
            if h is not None and not h.empty and h["Close"].isna().iloc[-1]:
                ts_nan = h.index[-1]
                nan_day = ts_nan.date() if hasattr(ts_nan, "date") else None
            h = h[h["Close"].notna()] if h is not None and not h.empty else h
            if h is None or len(h) < 2:
                errors.append(f"{name} 일봉 부족")
                continue
            last, prev = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
            ts = h.index[-1]
            bar_day = ts.date() if hasattr(ts, "date") else None
            if nan_day and bar_day and nan_day > bar_day:
                try:
                    lp = float(t.fast_info["last_price"])
                except Exception:
                    lp = float("nan")
                if lp == lp and lp > 0:
                    last, prev, bar_day = lp, last, nan_day
            # 현물 지수는 뉴욕 09:30~16:00 사이 오늘 봉이면 아직 종가가 아니다.
            # NQ=F 는 거의 24시간 돌아 마지막 봉이 늘 진행 중이다.
            live = sym.endswith("=F") or (
                bar_day == ny_now.date() and ny_now.weekday() < 5
                and (9, 30) <= (ny_now.hour, ny_now.minute) < (16, 0))
            rows.append((name, last, (last / prev - 1) * 100 if prev else None,
                         bar_day.strftime("%m/%d") if bar_day else "?", live))
        except Exception as exc:
            errors.append(f"{name} {type(exc).__name__}")
    return rows, errors


def _us_index_lines(names) -> list:
    rows, errors = _fetch_us_indices_live(names)
    out = []
    for name, v, p, day, live in rows:
        chg = f" {'+' if p >= 0 else ''}{p:.2f}%" if p is not None else ""
        tag = f"{day} 현재" if live else f"{day} 종가"
        out.append(f"{name} {v:,.2f}{chg} <i>({tag})</i>")
    if errors:
        out.append(f"<i>⚠️ 미국 지수 수신 실패: {', '.join(errors)[:150]}</i>")
    return out


def _kospi200_futures_section() -> dict:
    """🧭 코스피200 선물 — 근월물·원월물 시가·고가·저가·종가·미결제약정(KIS)."""
    sec = {"title": "🧭 코스피200 선물", "items": []}
    try:
        from kis_api import get_kospi200_futures
        fut = get_kospi200_futures(2)
    except Exception as exc:
        sec["error"] = f"KIS 선물 조회 실패: {type(exc).__name__}: {str(exc)[:80]}"
        return sec
    # 정규장(08:45~15:45) 안이면 futs_prpr 은 종가가 아니라 현재가다.
    hm = (now_kst().hour, now_kst().minute)
    close_label = "현재" if (8, 45) <= hm < (15, 45) and now_kst().weekday() < 5 else "종가"
    for c in fut.get("contracts", []):
        def n(v, fmt="{:,.2f}"):
            return fmt.format(v) if v is not None else "—"
        p = c.get("change_pct")
        chg = f" {'+' if p >= 0 else ''}{p:.2f}%" if p is not None else ""
        oi_chg = c.get("oi_change")
        oi_chg_s = f" ({'+' if oi_chg >= 0 else ''}{oi_chg:,})" if oi_chg is not None else ""
        sec["items"].append(f"<b>{c['label']}</b> {c['name']} · {close_label} {n(c.get('close'))}{chg}")
        sec["items"].append(
            f"  시 {n(c.get('open'))} · 고 {n(c.get('high'))} · 저 {n(c.get('low'))}")
        sec["items"].append(f"  미결제약정 {n(c.get('oi'), '{:,}')}{oi_chg_s}")
    if fut.get("error"):
        if sec["items"]:
            sec["items"].append(f"<i>⚠️ {fut['error'][:150]}</i>")
        else:
            sec["error"] = fut["error"]
    return sec


def build_market_summary(dry_run: bool = False) -> dict:
    """매크로·섹터·특징주·수급·공시·AI 섹션을 DB/캐시에서 집계.

    Args:
        dry_run: True면 summary["debug"] 에 빌더별 elapsed_ms / error 기록.
                 (기존 cron/api 호출 호환을 위해 기본값 False)
    """
    summary = {
        "generated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": [],
    }
    debug_info: dict = {} if dry_run else {}

    # ── 0. ETF 표식을 새로 붙인다 ──
    # 아래 특징주·거래대금·신고가 쿼리는 전부 `is_etf = 0` 으로 ETF 를 거른다.
    # 그 표식은 03:10 cron(mark_etf_stocks)이 붙이는데, Render 무료 플랜은 그
    # 시각에 자고 있어 cron 이 거의 돌지 않는다(16:00 시황이 밀리는 것과 같은
    # 이유 — closing_brief_catchup 주석). 재배포로 DB 가 날아간 날이나 새 상장
    # 행이 들어온 날은 표식이 기본값 0 인 채로 남아, 2026-09-22 시황에 KODEX 200 ·
    # TIGER 200 · KODEX CD금리액티브 등이 거래대금 상위와 역사적 신고가(10종목 중
    # 8종목)에 섞여 나갔다. 거르는 쿼리도 패턴도 맞았고 **표식이 낡아 있었다**.
    # 읽기 직전에 붙이면 cron 이 돌았는지와 무관해진다. UPDATE 스무 몇 번이라 싸다.
    try:
        n_etf = mark_etf_stocks()
        if dry_run:
            debug_info["etf_marked"] = n_etf
    except Exception as exc:
        # 시황 전체를 막지는 않는다. 다만 삼키지 않는다 — 이 실패면 ETF 가 섞여
        # 나갈 수 있다는 뜻이라 로그와 debug 에 남긴다.
        log.warning("[summary] ETF 표식 갱신 실패 — ETF 가 섞여 나갈 수 있다: %s", exc)
        if dry_run:
            debug_info["etf_marked_error"] = f"{type(exc).__name__}: {str(exc)[:150]}"

    # ── 1. 지수 ── (4-5-2-B: KR 라이브, US 캐시+신선도)
    idx_section = {"title": "📈 지수", "items": []}
    try:
        dj = json.loads((BASE_DIR / "data.json").read_text(encoding="utf-8")) \
            if (BASE_DIR / "data.json").exists() else {}
    except Exception:
        dj = {}
    # KR: Naver 라이브 우선 (data.json stale 우회)
    kospi_obj = dj.get("kospi") or {}
    kosdaq_obj = dj.get("kosdaq") or {}
    try:
        live_kr = _fetch_kr_indices_live()
        if live_kr.get("kospi"):  kospi_obj = live_kr["kospi"]
        if live_kr.get("kosdaq"): kosdaq_obj = live_kr["kosdaq"]
    except Exception as exc:
        log.debug("[market_summary] KR live fetch fail: %s", exc)

    for name, obj in (("KOSPI", kospi_obj), ("KOSDAQ", kosdaq_obj)):
        if isinstance(obj, dict) and obj.get("value") is not None:
            v = obj["value"]; p = obj.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            idx_section["items"].append(f"{name} {v:,.2f} {sign}{p:.2f}%")
    # US: 그 자리에서 받는다 — 마지막 봉의 미국 날짜를 함께 적는다
    idx_section["items"].extend(_us_index_lines(("S&P 500", "NASDAQ")))
    summary["sections"].append(idx_section)

    # ── 2. 매크로 ──
    macro_section = {"title": "🌍 매크로", "items": []}
    try:
        md = json.loads((BASE_DIR / "cache" / "macro_data.json").read_text(encoding="utf-8"))
    except Exception:
        md = {}
    targets = ["VIX", "USD/KRW", "WTI 원유", "미국 10년물", "BTC", "금"]
    for it in md.get("items", []):
        if it.get("name") in targets:
            v = it.get("value") or 0
            p = it.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            note = ""
            if it["name"] == "VIX":
                if v < 15: note = " (안정)"
                elif v < 20: note = " (보통)"
                elif v < 25: note = " (경계)"
                elif v < 35: note = " (공포)"
                else: note = " (패닉)"
            macro_section["items"].append(f"{it['name']} {v:,.2f} {sign}{p:.2f}%{note}")
    summary["sections"].append(macro_section)

    # ── 3. 코스피200 선물 ──
    # 예전 '🔮 옵션/선물' 은 SPY·QQQ 옵션 신호와 '코스피200 야간선물' 이었는데, 뒤의
    # 것은 yfinance ^KS200(현물 지수) 두 날 종가를 선물처럼 적은 대용값이었다.
    # 사용자 요청(2026-09-23)으로 빼고 KIS 실제 선물 시세로 바꾼다.
    summary["sections"].append(_kospi200_futures_section())

    # ── 4. 섹터 ──
    _t_sector = time.time()
    sector_section = {"title": "🏭 섹터 등락", "subsections": [], "error": None}
    if _SQLITE_OK and USE_SQLITE:
        # 노이즈 필터: 페니 스톡 (close < 1000원), KR 가격 한도 외 변동 (|chg|>30)
        # — 관리종목/거래정지해제/액면병합 후 첫거래 등에서 ±60% 같은 비정상값 발생
        _NOISE_WHERE = ("close >= 1000 AND change_pct IS NOT NULL "
                        "AND ABS(change_pct) <= 30")
        try:
            with _get_db() as conn:
                sectors = conn.execute(f"""
                    SELECT sector,
                           ROUND(AVG(change_pct), 2) as avg_chg,
                           COUNT(*) as cnt
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0
                      AND sector IS NOT NULL AND sector != ''
                      AND {_NOISE_WHERE}
                    GROUP BY sector HAVING cnt >= 5
                    ORDER BY avg_chg DESC
                """).fetchall()
                if sectors:
                    top = [dict(s) for s in sectors[:5]]
                    top_items = []
                    for i, s in enumerate(top):
                        # 대장주도 페니 + 한도외 제외
                        ldr = conn.execute(f"""
                            SELECT name, change_pct FROM stocks
                            WHERE sector = ? AND (market = '' OR market LIKE 'KOS%')
                              AND COALESCE(is_etf, 0) = 0
                              AND {_NOISE_WHERE}
                            ORDER BY change_pct DESC LIMIT 1
                        """, (s["sector"],)).fetchone()
                        lead_str = (f" (대장: {ldr['name']} {ldr['change_pct']:+.1f}%)"
                                    if ldr and ldr["change_pct"] is not None else "")
                        top_items.append(f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%{lead_str}")
                    sector_section["subsections"].append(
                        {"subtitle": "🟢 강세 TOP 5", "items": top_items}
                    )
                    bot = [dict(s) for s in sectors[-3:]]; bot.reverse()
                    bot_items = [f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%"
                                 for i, s in enumerate(bot)]
                    sector_section["subsections"].append(
                        {"subtitle": "🔴 약세 TOP 3", "items": bot_items}
                    )
        except sqlite3.OperationalError as exc:
            sector_section["error"] = f"DB locked/timeout: {str(exc)[:150]}"
            log.warning("[summary] sector DB OperationalError: %s "
                        "(busy_timeout 미적용 또는 부족 가능)", exc)
        except Exception as exc:
            sector_section["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            log.warning("[summary] sector 빌더 실패 (rendered=0): %s",
                        exc, exc_info=True)
    else:
        sector_section["error"] = "SQLite unavailable (USE_SQLITE=False or import failed)"
        log.warning("[summary] sector 스킵: %s", sector_section["error"])
    summary["sections"].append(sector_section)
    if dry_run:
        debug_info["sector_ms"] = round((time.time() - _t_sector) * 1000, 1)
        debug_info["sector_error"] = sector_section["error"]
        debug_info["sector_subsections"] = len(sector_section["subsections"])

    # ── 5. 특징주 ──
    _t_feat = time.time()
    feat_section = {"title": "⚡ 특징주", "subsections": [], "error": None}
    if _SQLITE_OK and USE_SQLITE:
        # 위와 동일 노이즈 필터 (페니 + 한도외)
        _NOISE_WHERE = ("close >= 1000 AND change_pct IS NOT NULL "
                        "AND ABS(change_pct) <= 30")
        try:
            with _get_db() as conn:
                risers = conn.execute(f"""
                    SELECT code, name, change_pct, volume_mn, sector, market_cap,
                           market_cap_updated
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0 AND change_pct > 5
                      AND {_NOISE_WHERE}
                    ORDER BY change_pct DESC LIMIT 10
                """).fetchall()
                if risers:
                    items = [
                        f"  {r['name']} {r['change_pct']:+.1f}%"
                        f"{_fmt_cap(r['market_cap'], r['market_cap_updated'])}"
                        f" — {r['sector'] or '?'}"
                        for r in risers
                    ]
                    feat_section["subsections"].append(
                        {"subtitle": "🔺 급등 (+5%↑) TOP 10", "items": items}
                    )
                fallers = conn.execute(f"""
                    SELECT name, change_pct, sector
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0 AND change_pct < -5
                      AND {_NOISE_WHERE}
                    ORDER BY change_pct ASC LIMIT 5
                """).fetchall()
                if fallers:
                    items = [f"  {f['name']} {f['change_pct']:+.1f}% — {f['sector'] or '?'}"
                             for f in fallers]
                    feat_section["subsections"].append(
                        {"subtitle": "🔻 급락 (-5%↓) TOP 5", "items": items}
                    )
                vols = conn.execute(f"""
                    SELECT code, name, change_pct, volume_mn, sector
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0
                      AND ABS(COALESCE(change_pct, 0)) <= 5
                      AND COALESCE(volume_mn, 0) > 0
                      AND close >= 1000
                    ORDER BY volume_mn DESC LIMIT 5
                """).fetchall()
                if vols:
                    items = [
                        _format_volume_line(v['code'], v['name'],
                                            v['change_pct'] or 0,
                                            v['volume_mn'] or 0,
                                            v['sector'] or '?')
                        for v in vols
                    ]
                    feat_section["subsections"].append(
                        {"subtitle": "📊 거래대금 상위 (±5% 이내)", "items": items}
                    )
        except sqlite3.OperationalError as exc:
            feat_section["error"] = f"DB locked/timeout: {str(exc)[:150]}"
            log.warning("[summary] feat DB OperationalError: %s", exc)
        except Exception as exc:
            feat_section["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            log.warning("[summary] feat 빌더 실패 (rendered=0): %s",
                        exc, exc_info=True)
    else:
        feat_section["error"] = "SQLite unavailable"
        log.warning("[summary] feat 스킵: %s", feat_section["error"])
    summary["sections"].append(feat_section)
    if dry_run:
        debug_info["feat_ms"] = round((time.time() - _t_feat) * 1000, 1)
        debug_info["feat_error"] = feat_section["error"]
        debug_info["feat_subsections"] = len(feat_section["subsections"])

    # ── 5-2. 신고가 (역사적 · 52주 · 60일) ──
    # 오늘 종가(stocks.close, 라이브)를 **직전 거래일까지의 고가**와 견준다.
    # 오늘 행까지 최고가에 넣으면 모든 종목이 제 고가와 비겨 늘 신고가가 된다.
    #
    # 구간은 달력일이 아니라 ohlcv 에 실제로 있는 거래일로 센다 — 공휴일이 끼면
    # 달력 60일이 거래일 40일이 되기도 한다.
    #
    # '역사적' 은 **보유한 일봉 전 구간**이다(수집기가 5년). 상장 이후 전부가
    # 아니므로 기준 구간을 함께 적는다. 안 적으면 5년 최고가가 사상 최고가로 읽힌다.
    #
    # **종가 기준이다** — 오늘 종가를 과거 **종가**들의 최고와 견준다. 과거 고가와
    # 견주면 기준이 섞여(오늘은 종가, 과거는 장중 고가) 판정이 보수적으로 치우치고,
    # 반대로 오늘 고가까지 보면 장중에 잠깐 뚫고 하락 마감한 날도 신고가가 되어
    # '등락률 마이너스인데 신고가' 가 나온다. 신고가 보드(ETF-Traker)도 종가 기준
    # (board/config/settings.yaml default_basis: close)이라 두 화면이 같은 말을 한다.
    #
    # 한 종목은 가장 센 줄에만 담는다. 역사적 신고가면 52주·60일도 당연히 뚫은
    # 것이라, 안 가르면 세 줄에 같은 이름이 겹쳐 나온다.
    #
    # 오늘 거래가 없던 종목(volume_mn=0)은 뺀다. 체결이 없으면 종가가 어제
    # 그대로라 '오늘 신고가' 라고 부를 것이 없고, 5년치 일봉을 훑는 이 쿼리의
    # 대상만 늘린다.
    _t_nh = time.time()
    nh_section = {"title": "🏔 신고가", "subsections": [], "error": None}
    if _SQLITE_OK and USE_SQLITE:
        _KRG = "[0-9][0-9][0-9][0-9][0-9][0-9]"
        try:
            today_ymd = now_kst().strftime("%Y-%m-%d")
            with _get_db() as conn:
                days = [r[0] for r in conn.execute(
                    f"""SELECT DISTINCT date FROM ohlcv
                        WHERE code GLOB '{_KRG}' AND date < ?
                        ORDER BY date DESC LIMIT 252""", (today_ymd,)).fetchall()]
                if len(days) < 60:
                    # 왜 모자란지까지 적는다. '0일' 만 보면 고칠 데를 못 찾는다.
                    # ohlcv 는 16:10 잡과 부팅 스레드가 채운다(_fill_ohlcv_job).
                    nh_section["error"] = (
                        f"일봉 거래일이 {len(days)}일뿐 — 60일 구간을 못 만든다"
                        f" · {_ohlcv_fill_hint()}")
                else:
                    last_day, cut60, cut252 = days[0], days[59], days[-1]
                    first_day = conn.execute(
                        f"SELECT MIN(date) FROM ohlcv WHERE code GLOB '{_KRG}'"
                    ).fetchone()[0]
                    # 두 번에 나눠 묻는다. 한 번에 MAX(o.close) 를 같이 구하면
                    # 전 종목 5년 일봉(수천 종목 x 1,250봉)을 통째로 훑는다.
                    # 52주를 못 뚫은 종목은 역사적일 수 없으므로, 전 구간
                    # 최고 종가는 **52주를 뚫은 몇 종목에만** 물으면 된다.
                    # 모집단: ETF 가 아닌 시총 1,000억(원 단위 1e11) 이상.
                    # 일봉은 그보다 넓게(800억~) 받아 두지만 판정은 여기서 자른다.
                    import ohlcv_autofill as _oa
                    min_cap = _oa.MIN_MARKET_CAP_WON
                    rows = conn.execute("""
                        SELECT s.code AS code, s.name AS name, s.sector AS sector,
                               s.change_pct AS change_pct, s.close AS close,
                               s.volume_mn AS volume_mn, s.market_cap AS market_cap,
                               s.market_cap_updated AS market_cap_updated,
                               MAX(CASE WHEN o.date >= ? THEN o.close END) AS h60,
                               MAX(o.close) AS h252
                        FROM stocks s
                        JOIN ohlcv o ON o.code = s.code
                                    AND o.date >= ? AND o.date < ?
                        WHERE (s.market = '' OR s.market LIKE 'KOS%')
                          AND COALESCE(s.is_etf, 0) = 0
                          AND s.close >= 1000 AND s.change_pct IS NOT NULL
                          AND COALESCE(s.volume_mn, 0) > 0
                          AND COALESCE(s.market_cap, 0) >= ?
                        GROUP BY s.code
                    """, (cut60, cut252, today_ymd, min_cap)).fetchall()
                    # 같은 조건에서 일봉만 뺀 수 — 모집단. `rows` 가 이보다 적으면
                    # 그만큼 일봉이 없어 판정하지 못한 것이다(채우는 중·수집
                    # 실패·신규 상장 직후). 그 차이를 머리말에 그대로 적는다.
                    universe_n = conn.execute("""
                        SELECT COUNT(*) FROM stocks s
                        WHERE (s.market = '' OR s.market LIKE 'KOS%')
                          AND COALESCE(s.is_etf, 0) = 0
                          AND s.close >= 1000 AND s.change_pct IS NOT NULL
                          AND COALESCE(s.volume_mn, 0) > 0
                          AND COALESCE(s.market_cap, 0) >= ?
                    """, (min_cap,)).fetchone()[0]
                    scope = _ohlcv_scope_note(len(rows), universe_n)

                    over52 = [r for r in rows
                              if r["close"] and r["h252"] and r["close"] >= r["h252"]]
                    hall_of = {}
                    if over52:
                        codes = [r["code"] for r in over52]
                        qs = ",".join("?" * len(codes))
                        hall_of = {x[0]: x[1] for x in conn.execute(
                            f"""SELECT code, MAX(close) FROM ohlcv
                                WHERE code IN ({qs}) AND date < ?
                                GROUP BY code""", (*codes, today_ymd)).fetchall()}

                    buckets = {"hist": [], "w52": [], "d60": []}
                    for r in rows:
                        c = r["close"]
                        if not c:
                            continue
                        hall = hall_of.get(r["code"])
                        if hall and c >= hall:
                            buckets["hist"].append(r)
                        elif r["h252"] and c >= r["h252"]:
                            buckets["w52"].append(r)
                        elif r["h60"] and c >= r["h60"]:
                            buckets["d60"].append(r)

                    # 수급을 붙인 줄들의 기준일. 오늘이 아니면 섹션 머리말이
                    # 그 사실을 적는다 — 16:00 시황에서는 대개 전일 값이다.
                    nh_flow_dates: set = set()
                    for key, label, icon in (("hist", "역사적", "🏔"),
                                             ("w52", "52주", "📈"),
                                             ("d60", "60일", "📊")):
                        got = sorted(buckets[key],
                                     key=lambda x: -(x["volume_mn"] or 0))
                        if not got:
                            continue
                        cap_n = _NH_LIST_MAX.get(key)
                        shown = got if cap_n is None else got[:cap_n]
                        # 상위 몇 종목에는 수급을 붙인다(_NH_FLOW_MAX).
                        # 목록과 같은 거래대금 순이라 화면의 1~N번째 줄과 정확히
                        # 겹친다 — 다른 기준으로 자르면 둘이 어긋난다.
                        n_flow = _NH_FLOW_MAX.get(key) or 0
                        flow_map, flow_latest = ({}, None)
                        if n_flow:
                            flow_map, flow_latest = _newhigh_flow(
                                conn, [g["code"] for g in shown[:n_flow]])
                            if flow_latest:
                                nh_flow_dates.add(flow_latest)
                        items = []
                        for i, g in enumerate(shown):
                            line = (f"  {g['name']} {(g['change_pct'] or 0):+.1f}%"
                                    f"{_fmt_cap(g['market_cap'], g['market_cap_updated'])}"
                                    f" — {g['sector'] or '?'}")
                            if i < n_flow:
                                line += _fmt_nh_flow(flow_map.get(g["code"]),
                                                     flow_latest)
                            items.append(line)
                        if len(got) > len(shown):
                            items.append(f"  … 외 {len(got) - len(shown)}종목")
                        nh_section["subsections"].append(
                            {"subtitle": f"{icon} {label} 신고가 {len(got)}종목",
                             "items": items})
                    if nh_section["subsections"]:
                        # 기준은 섹션 머리에 한 번만. 줄마다 붙이면 세 번 읽힌다.
                        # **모집단을 반드시 밝힌다** — 시총 1,000억 이상만 보고,
                        # 그중 일봉이 없는 종목은 판정하지 못했다. 안 적으면
                        # "신고가 3종목" 을 전 종목 기준으로 읽는다.
                        basis = (f"  <i>{scope} · "
                                 f"종가 기준 · 오늘 종가 vs {last_day}까지 종가 · "
                                 f"역사적=일봉 {first_day}~")
                        # `*` 를 쓴 줄이 하나라도 있으면 그 뜻을 여기서 밝힌다.
                        # 범례 없는 기호는 읽는 사람에게 오타로 보인다.
                        if any("*]" in it
                               for sub in nh_section["subsections"]
                               for it in sub["items"]):
                            basis += (f" · 시총 <b>*</b> 는 {_CAP_STALE_DAYS}일 넘게 "
                                      f"갱신되지 않은 값")
                        # 수급이 오늘 것이 아니면 반드시 적는다. 당일 확정치는
                        # 장마감 후 한참 뒤에 나오므로 16:00 시황에서는 대개
                        # 전일 값이다 — 그걸 오늘 값인 척 두면 안 된다.
                        _today_ymd = now_kst().strftime("%Y-%m-%d")
                        _stale_flow = sorted(d for d in nh_flow_dates
                                             if d != _today_ymd)
                        if _stale_flow:
                            _d = _stale_flow[-1][5:].replace("-", "/")
                            basis += f" · 수급은 {_d} 기준(당일 확정 전)"
                        nh_section["items"] = [basis + "</i>"]
                    else:
                        # 없다는 말도 무엇을 훑고 없는지 밝힌다 — 절반만 훑고
                        # '없음' 이면 그건 없는 게 아니라 모르는 것이다.
                        nh_section["error"] = (
                            f"오늘 신고가 종목 없음 ({scope} · "
                            f"{last_day}까지 종가 기준)")
        except sqlite3.OperationalError as exc:
            nh_section["error"] = f"DB locked/timeout: {str(exc)[:150]}"
            log.warning("[summary] newhigh DB OperationalError: %s", exc)
        except Exception as exc:
            nh_section["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            log.warning("[summary] newhigh 빌더 실패: %s", exc, exc_info=True)
    else:
        nh_section["error"] = "SQLite unavailable"
        log.warning("[summary] newhigh 스킵: %s", nh_section["error"])
    summary["sections"].append(nh_section)
    if dry_run:
        debug_info["newhigh_ms"] = round((time.time() - _t_nh) * 1000, 1)
        debug_info["newhigh_error"] = nh_section["error"]
        debug_info["newhigh_subsections"] = len(nh_section["subsections"])

    # ── 6. 수급 (flow_cache에서 오늘 순매수 집계) ──
    _t_flow = time.time()
    flow_section = {"title": "💰 수급 동향", "subsections": [], "error": None}
    if _SQLITE_OK and USE_SQLITE:
        # flow_cache.name 이 다수 종목코드로 채워져 있어 stocks 테이블로 한글명 보강.
        # name 이 코드와 같거나 비어있으면 stocks.name 사용 → 그래도 없으면 코드.
        try:
            with _get_db() as conn:
                rows = conn.execute("""
                    SELECT f.code AS code,
                           COALESCE(NULLIF(s.name, ''), NULLIF(f.name, ''), f.code) AS name,
                           f.dates_json, f.foreign_value_json, f.inst_value_json
                    FROM flow_cache f
                    LEFT JOIN stocks s ON s.code = f.code
                    WHERE f.foreign_value_json IS NOT NULL
                """).fetchall()
                # 투자자별 순매매(외국인/기관)는 장마감 직후엔 당일치 미집계 →
                # Naver frgn 페이지가 전일까지만 제공. 캐시의 실제 최신 거래일을
                # 읽어 라벨에 표기하고, 모든 종목 값을 그 공통 최신일에 정렬한다.
                # (기존엔 종목별 마지막 행을 무조건 "(오늘)"로 표기 → 전일 데이터를
                #  당일로 오인하게 만드는 버그. 예: 삼성전자 06/04 기관 순매수를
                #  06/05 "오늘"로 표시.)
                parsed: list = []
                latest_date: str | None = None
                for r in rows:
                    name = r["name"] if r["name"] != r["code"] else r["code"]
                    dts = _parse_json_list(r["dates_json"])
                    fv = _parse_json_list(r["foreign_value_json"])
                    iv = _parse_json_list(r["inst_value_json"])
                    if not dts:
                        continue
                    d_last = dts[-1]
                    if latest_date is None or d_last > latest_date:
                        latest_date = d_last
                    parsed.append({"code": r["code"], "name": name,
                                   "d_last": d_last, "fv": fv, "iv": iv})

                foreign_today: list = []
                inst_today: list = []
                for p in parsed:
                    # 공통 최신 거래일과 일치하는 종목만 (날짜 혼재 방지)
                    if p["d_last"] != latest_date:
                        continue
                    if p["fv"]:
                        foreign_today.append({"code": p["code"], "name": p["name"], "net": p["fv"][-1]})
                    if p["iv"]:
                        inst_today.append({"code": p["code"], "name": p["name"], "net": p["iv"][-1]})

                # 라벨용 날짜 문구: 당일이면 "오늘", 아니면 "MM/DD 기준"
                today_kst_ymd = now_kst().strftime("%Y-%m-%d")
                if latest_date and latest_date != today_kst_ymd:
                    _d = latest_date[5:].replace("-", "/")
                    date_lbl = f"{_d} 기준"
                    flow_section["subsections"].append({
                        "subtitle": "ℹ️ 투자자 매매 동향",
                        "items": [f"  당일 외국인/기관 순매매는 장마감 후 집계 지연 — "
                                  f"최신 확정치 {_d} 기준"]
                    })
                else:
                    date_lbl = "오늘"

                foreign_top = sorted(foreign_today, key=lambda x: x["net"], reverse=True)[:5]
                inst_top = sorted(inst_today, key=lambda x: x["net"], reverse=True)[:5]
                if foreign_top:
                    flow_section["subsections"].append({
                        "subtitle": f"🌐 외국인 순매수 TOP 5 ({date_lbl})",
                        "items": [_format_flow_line(r['code'], r['name'],
                                                   r['net']/1e8) for r in foreign_top]
                    })
                if inst_top:
                    flow_section["subsections"].append({
                        "subtitle": f"🏛 기관 순매수 TOP 5 ({date_lbl})",
                        "items": [_format_flow_line(r['code'], r['name'],
                                                   r['net']/1e8) for r in inst_top]
                    })
                # 20일 누적 — 동일하게 stocks 한글명 우선
                agg = conn.execute("""
                    SELECT f.code AS code,
                           COALESCE(NULLIF(s.name, ''), NULLIF(f.name, ''), f.code) AS name,
                           f.foreign_sum_20
                    FROM flow_cache f
                    LEFT JOIN stocks s ON s.code = f.code
                    WHERE f.foreign_sum_20 IS NOT NULL
                    ORDER BY f.foreign_sum_20 DESC LIMIT 5
                """).fetchall()
                if agg:
                    flow_section["subsections"].append({
                        "subtitle": "🌐 외국인 20일 누적 TOP 5",
                        "items": [_format_flow_line(a['code'], a['name'],
                                                   a['foreign_sum_20']/1e8) for a in agg]
                    })
        except sqlite3.OperationalError as exc:
            flow_section["error"] = f"DB locked/timeout: {str(exc)[:150]}"
            log.warning("[summary] flow DB OperationalError: %s", exc)
        except Exception as exc:
            flow_section["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            log.warning("[summary] flow 빌더 실패 (rendered=0): %s",
                        exc, exc_info=True)
    else:
        flow_section["error"] = "SQLite unavailable"
        log.warning("[summary] flow 스킵: %s", flow_section["error"])
    summary["sections"].append(flow_section)
    if dry_run:
        debug_info["flow_ms"] = round((time.time() - _t_flow) * 1000, 1)
        debug_info["flow_error"] = flow_section["error"]
        debug_info["flow_subsections"] = len(flow_section["subsections"])

    # ── 7. DART 공시 ──
    disc_section = {"title": "📋 주요 공시", "items": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            today_str = now_kst().strftime("%Y%m%d")
            with _get_db() as conn:
                discs = conn.execute("""
                    SELECT corp_name, title, score FROM disclosure_history
                    WHERE rcept_dt = ? AND score >= 6
                    ORDER BY score DESC LIMIT 5
                """, (today_str,)).fetchall()
            for d in discs:
                emoji = "🚨" if (d["score"] or 0) >= 10 else "📢"
                disc_section["items"].append(
                    f"  {emoji} [{d['score']}점] {d['corp_name']}: {(d['title'] or '')[:42]}"
                )
        except Exception as exc:
            log.debug("[summary] disc: %s", exc)
    if not disc_section["items"]:
        disc_section["items"].append("  오늘 중요 공시 없음")
    summary["sections"].append(disc_section)

    # ── 8. AI 추천 ──
    ai_section = {"title": "🤖 AI 추천 요약", "items": []}
    cache_picks_loaded = False
    # agents/pipeline.py 가 실제로 만드는 파일은 agent_result_latest.json (KR 디폴트).
    # 이전 코드의 agent_result_kr_latest.json 은 생성 위치 없어 무의미했음.
    p = BASE_DIR / "cache" / "agent_result_latest.json"
    if p.exists():
        try:
            agent = json.loads(p.read_text(encoding="utf-8"))
            hot = (agent.get("agents", {}).get("news", {}).get("hot_themes") or [])
            if hot:
                ai_section["items"].append(f"  핫 테마: {', '.join(hot[:5])}")
            picks = (agent.get("final_picks") or [])[:5]
            if picks:
                ai_section["items"].append(f"  추천 {len(picks)}종목:")
                for pk in picks:
                    ai_section["items"].append(
                        f"    {pk.get('name')} ({pk.get('code')}) {pk.get('total_score', 0)}점"
                    )
                cache_picks_loaded = True
        except Exception as exc:
            log.debug("[summary] ai cache parse: %s", exc)

    # 폴백: 캐시에 picks 없으면 recommendation_history DB 활용
    # (캐시 7일 클린업/cron 누락 케이스 대응)
    if not cache_picks_loaded and _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                # 가장 최근 KR agent_kr 추천 + 다른 source 폴백
                row = conn.execute("""
                    SELECT MAX(date) as last_date
                    FROM recommendation_history
                    WHERE market = 'kr'
                """).fetchone()
                last_date = row["last_date"] if row else None
                if last_date:
                    # 1차: agent_kr 추천 우선
                    picks_db = conn.execute("""
                        SELECT code, name, score, source
                        FROM recommendation_history
                        WHERE market='kr' AND date=? AND source='agent_kr'
                        ORDER BY rank ASC, score DESC LIMIT 5
                    """, (last_date,)).fetchall()
                    # 2차: agent_kr 없으면 discover_kr 폴백
                    if not picks_db:
                        picks_db = conn.execute("""
                            SELECT code, name, score, source
                            FROM recommendation_history
                            WHERE market='kr' AND date=?
                            ORDER BY rank ASC, score DESC LIMIT 5
                        """, (last_date,)).fetchall()
                    if picks_db:
                        # 날짜가 오늘이 아니면 stale 마크
                        today_str = now_kst().strftime("%Y-%m-%d")
                        stale_tag = "" if last_date == today_str else f" ⚠️ ({last_date})"
                        ai_section["items"].append(
                            f"  추천 {len(picks_db)}종목 (DB 폴백{stale_tag}):"
                        )
                        for pk in picks_db:
                            score_int = int(pk["score"]) if pk["score"] else 0
                            ai_section["items"].append(
                                f"    {pk['name']} ({pk['code']}) {score_int}점"
                            )
        except Exception as exc:
            log.debug("[summary] ai db fallback: %s", exc)

    if not ai_section["items"]:
        ai_section["items"].append("  AI 추천 데이터 없음")
    summary["sections"].append(ai_section)

    # 캐시 저장
    try:
        out = BASE_DIR / "cache" / f"market_summary_{now_kst().strftime('%Y%m%d')}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if dry_run:
        summary["debug"] = debug_info
    return summary


@app.route("/api/market_summary")
def api_market_summary():
    return jsonify(build_market_summary())


def build_us_market_summary() -> dict:
    """미국 장마감 시황 요약 (DB market='US' + 매크로 + 옵션)."""
    summary = {
        "market": "us",
        "generated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": [],
    }

    # ── 1. 미국 지수 (yfinance 실시간 — _fetch_us_indices_live) ──
    idx_section = {"title": "🇺🇸 미국 지수", "items": []}
    # data.json 에는 DOW·Russell·SOX 가 채워진 적이 없어 늘 빠졌고, 있는 값도
    # 날짜가 없었다. 그 자리에서 받는다 (_fetch_us_indices_live 주석).
    idx_section["items"].extend(_us_index_lines(tuple(US_INDEX_TICKERS)))
    summary["sections"].append(idx_section)

    # ── 2. 옵션/변동성 ──
    opts_section = {"title": "🔮 옵션·변동성", "items": []}
    try:
        md = json.loads((BASE_DIR / "cache" / "macro_data.json").read_text(encoding="utf-8"))
    except Exception:
        md = {}
    for it in md.get("items", []):
        if it.get("name") == "VIX":
            v = it.get("value") or 0; p = it.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            if v < 15: note = "안정"
            elif v < 20: note = "보통"
            elif v < 25: note = "경계"
            elif v < 35: note = "공포"
            else: note = "패닉"
            opts_section["items"].append(f"VIX {v:.2f} {sign}{p:.2f}% ({note})")
            break
    for sym in ("SPY", "QQQ"):
        try:
            f = BASE_DIR / "cache" / f"options_signal_{sym}.json"
            if not f.exists(): continue
            od = json.loads(f.read_text(encoding="utf-8"))
            pcr = od.get("pcr", {})
            mp = od.get("max_pain", {})
            gex = od.get("gex", {})
            ovr = od.get("overall", {})
            opts_section["items"].append(
                f"{sym} ${od.get('spot_price', 0)} | PCR {pcr.get('volume', '—')} | "
                f"MaxPain ${mp.get('strike', '—')} ({mp.get('diff_pct', 0):+.1f}%) | "
                f"GEX {gex.get('regime', '—')} → {ovr.get('emoji', '')} {ovr.get('direction', '—')}"
            )
            cw = gex.get("call_wall"); pw = gex.get("put_wall")
            if cw and isinstance(cw, dict):
                put_str = f" | 풋벽 ${pw.get('strike')}" if pw and isinstance(pw, dict) else ""
                opts_section["items"].append(
                    f"  {sym} 콜벽(저항) ${cw.get('strike')}{put_str}"
                )
        except Exception:
            pass
    summary["sections"].append(opts_section)

    # ── 3. 매크로 (US 관점) ──
    macro_section = {"title": "🌍 매크로", "items": []}
    targets = ["미국 10년물", "USD/KRW", "USD/JPY", "EUR/USD", "WTI 원유", "금", "BTC"]
    for it in md.get("items", []):
        if it.get("name") in targets:
            v = it.get("value") or 0; p = it.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            note = ""
            if it["name"] == "미국 10년물":
                if v > 4.5: note = " (고금리)"
                elif v > 4.0: note = " (보통)"
                else: note = " (저금리)"
            elif it["name"] == "USD/KRW":
                if v > 1400: note = " (원화 약세)"
                elif v < 1300: note = " (원화 강세)"
            macro_section["items"].append(f"{it['name']} {v:,.2f} {sign}{p:.2f}%{note}")
    summary["sections"].append(macro_section)

    # ── 4. GICS 섹터 ──
    sector_section = {"title": "🏭 GICS 섹터", "subsections": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                sectors = conn.execute("""
                    SELECT sector, ROUND(AVG(change_pct), 2) as avg_chg, COUNT(*) as cnt
                    FROM stocks
                    WHERE market = 'US' AND COALESCE(is_etf, 0) = 0
                      AND sector IS NOT NULL AND sector != ''
                      AND change_pct IS NOT NULL
                    GROUP BY sector HAVING cnt >= 5
                    ORDER BY avg_chg DESC
                """).fetchall()
                if sectors:
                    top_items = []
                    for i, s in enumerate(sectors[:5]):
                        ldr = conn.execute("""
                            SELECT name, code, change_pct FROM stocks
                            WHERE market = 'US' AND sector = ?
                              AND COALESCE(is_etf, 0) = 0
                            ORDER BY change_pct DESC LIMIT 1
                        """, (s["sector"],)).fetchone()
                        lead_str = (f" (대장: {ldr['name']} {ldr['change_pct']:+.1f}%)"
                                    if ldr and ldr["change_pct"] is not None else "")
                        top_items.append(f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%{lead_str}")
                    sector_section["subsections"].append(
                        {"subtitle": "🟢 강세 TOP 5", "items": top_items}
                    )
                    bot = list(reversed(sectors[-3:]))
                    bot_items = [f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%"
                                 for i, s in enumerate(bot)]
                    sector_section["subsections"].append(
                        {"subtitle": "🔴 약세 TOP 3", "items": bot_items}
                    )
        except Exception as exc:
            log.debug("[us_summary] sector: %s", exc)
    summary["sections"].append(sector_section)

    # ── 5. 특징주 ──
    feat_section = {"title": "⚡ 특징주", "subsections": []}
    if _SQLITE_OK and USE_SQLITE:
        def _cap_us(cap):
            if not cap or cap <= 0:
                return ""
            try:
                if cap >= 1e12: return f" [${cap/1e12:.1f}T]"
                if cap >= 1e9: return f" [${cap/1e9:.1f}B]"
                if cap >= 1e6: return f" [${cap/1e6:.0f}M]"
            except Exception:
                pass
            return ""
        try:
            with _get_db() as conn:
                risers = conn.execute("""
                    SELECT code, name, change_pct, sector, market_cap
                    FROM stocks
                    WHERE market = 'US' AND COALESCE(is_etf, 0) = 0
                      AND change_pct > 3
                    ORDER BY change_pct DESC LIMIT 10
                """).fetchall()
                if risers:
                    items = [
                        f"  {r['name']} ({r['code']}) {r['change_pct']:+.1f}%{_cap_us(r['market_cap'])} — {r['sector'] or '?'}"
                        for r in risers
                    ]
                    feat_section["subsections"].append(
                        {"subtitle": "🔺 급등 (+3%↑) TOP 10", "items": items}
                    )
                fallers = conn.execute("""
                    SELECT code, name, change_pct, sector
                    FROM stocks
                    WHERE market = 'US' AND COALESCE(is_etf, 0) = 0
                      AND change_pct < -3
                    ORDER BY change_pct ASC LIMIT 10
                """).fetchall()
                if fallers:
                    items = [f"  {f['name']} ({f['code']}) {f['change_pct']:+.1f}% — {f['sector'] or '?'}"
                             for f in fallers]
                    feat_section["subsections"].append(
                        {"subtitle": "🔻 급락 (-3%↓) TOP 10", "items": items}
                    )
        except Exception as exc:
            log.debug("[us_summary] feat: %s", exc)
    summary["sections"].append(feat_section)

    # ── 6. Mag7 ──
    mag7_section = {"title": "🏆 Mag7", "items": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                for sym in ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"):
                    r = conn.execute(
                        "SELECT name, close, change_pct, market_cap FROM stocks "
                        "WHERE code = ? AND market = 'US'", (sym,)
                    ).fetchone()
                    if not r:
                        continue
                    cap = r["market_cap"] or 0
                    cap_str = f" ${cap/1e12:.1f}T" if cap >= 1e12 else (
                        f" ${cap/1e9:.0f}B" if cap >= 1e9 else ""
                    )
                    p = r["close"] or 0; c = r["change_pct"] or 0
                    sign = "+" if c >= 0 else ""
                    dot = "🔴" if c >= 0 else "🟢"
                    mag7_section["items"].append(
                        f"  {dot} {sym} ${p:,.2f} {sign}{c:.2f}%{cap_str}"
                    )
        except Exception as exc:
            log.debug("[us_summary] mag7: %s", exc)
    summary["sections"].append(mag7_section)

    # ── 7. 내일 국내 영향 ──
    dom_section = {"title": "🇰🇷 내일 국내 영향", "items": []}
    try:
        nf = json.loads((BASE_DIR / "cache" / "night_futures.json").read_text(encoding="utf-8"))
        if nf.get("night_close"):
            p = nf.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            dom_section["items"].append(
                f"코스피200 야간선물 {nf['night_close']} {sign}{p}% — {nf.get('signal', '')}"
            )
    except Exception:
        pass
    for it in md.get("items", []):
        if it.get("name") == "USD/KRW":
            p = it.get("change_pct") or 0
            if p > 0.5:
                dom_section["items"].append(
                    f"원화 약세 ({p:+.2f}%) → 수출주(반도체·조선) 유리, 내수주 부담"
                )
            elif p < -0.5:
                dom_section["items"].append(
                    f"원화 강세 ({p:+.2f}%) → 내수주 유리, 수출주 부담"
                )
            else:
                dom_section["items"].append(f"환율 보합 ({p:+.2f}%)")
            break
    # 미국 반도체 평균 → 국내 반도체 연동 시그널
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                rows = conn.execute("""
                    SELECT AVG(change_pct) as avg_chg
                    FROM stocks
                    WHERE market = 'US'
                      AND code IN ('NVDA','AMD','AVGO','QCOM','MU','INTC','TSM','ASML','LRCX','AMAT')
                      AND change_pct IS NOT NULL
                """).fetchone()
                if rows and rows["avg_chg"] is not None:
                    avg = rows["avg_chg"]
                    if abs(avg) >= 1:
                        sign = "+" if avg >= 0 else ""
                        dir_ = "상승" if avg > 0 else "하락"
                        dom_section["items"].append(
                            f"미국 반도체 평균 {sign}{avg:.2f}% → 내일 삼성전자/SK하이닉스 {dir_} 압력"
                        )
        except Exception:
            pass
    summary["sections"].append(dom_section)

    # US AI 추천 구획은 뺐다(2026-09-29 사용자 요청 — 늘 "데이터 없음" 만 나갔다)

    # 캐시 저장
    try:
        out = BASE_DIR / "cache" / f"us_market_summary_{now_kst().strftime('%Y%m%d')}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return summary


@app.route("/api/market_summary/us")
def api_market_summary_us():
    return jsonify(build_us_market_summary())


def _ensure_us_data_for_summary() -> None:
    """미국 시황 빌드 직전 데이터 readiness 보장.
    1) 오늘자 us_market 캐시 없고 가장 최근 캐시가 18h 초과 stale → 강제 재빌드
    2) stocks 테이블에 market='US' 행이 0개 → sync 트리거
    빌드/싱크 실패해도 본 함수는 silent — 텔레그램 발송은 계속 진행."""
    today = now_kst().strftime("%Y%m%d")
    cache_today = BASE_DIR / "cache" / f"us_market_{today}.json"
    if not cache_today.exists():
        try:
            files = sorted(
                BASE_DIR.glob("cache/us_market_2[0-9][0-9][0-9][0-9][0-9][0-9][0-9].json"),
                reverse=True,
            )
            if files:
                latest = files[0]
                age_h = (now_kst().timestamp() - latest.stat().st_mtime) / 3600
                if age_h > 18:
                    log.info("[US summary] 캐시 stale %.1fh — 강제 재빌드", age_h)
                    _fetch_us_market_data(force=True)
            else:
                log.info("[US summary] 캐시 없음 — 강제 빌드")
                _fetch_us_market_data(force=True)
        except Exception as exc:
            log.warning("[US summary] 빌드 시도 실패: %s", exc)

    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM stocks WHERE market='US'"
                ).fetchone()
                cnt = row["c"] if row else 0
            if cnt == 0:
                log.info("[US summary] stocks 빈 상태 — 캐시→DB 동기화 트리거")
                sync_us_market_to_db_from_cache()
        except Exception as exc:
            log.warning("[US summary] DB readiness 체크 실패: %s", exc)


# KBJ P5 삭제: send_us_market_summary_telegram


def send_market_summary_telegram(header: str | None = None):
    """장마감 시황 텔레그램 발송.
    사용자 요청으로 텔레 메시지에서 제외하는 섹션:
      - 🤖 AI 추천 요약 (대시보드에는 유지)
      - 📋 주요 공시 (별도 알림 차단됨)
      - 💰 수급 동향 (대시보드에는 유지)
    API 응답(/api/market_summary) 에는 그대로 유지 — UI 영향 X.

    header: 메시지 상단 제목 (기본 "📊 장마감 시황"). 저녁 확정판은
            "🌙 장마감 확정 시황" 등으로 구분 표기.
    """
    try:
        data = build_market_summary()
    except Exception as exc:
        log.exception("send_market_summary build")
        return
    SKIP_TITLES = {"🤖 AI 추천 요약", "📋 주요 공시", "💰 수급 동향"}
    _title = header or "📊 장마감 시황"
    lines = [f"<b>{now_kst().strftime('%m/%d')} {_title}</b>", ""]
    empty_titles: list = []
    for sec in data.get("sections", []):
        if sec.get("title") in SKIP_TITLES:
            continue
        lines.append(f"<b>{sec['title']}</b>")
        items = sec.get("items") or []
        subsections = sec.get("subsections") or []
        for it in items:
            lines.append(it)
        for sub in subsections:
            lines.append("")
            lines.append(sub["subtitle"])
            for it in sub.get("items", []):
                lines.append(it)
        # 빈 섹션: error 메시지 명시 (S-1-A: 무음 폴백 → 가시화)
        if not items and not subsections:
            err = sec.get("error")
            if err:
                lines.append("  ⚠️ 데이터 수집 실패")
                lines.append(f"     <i>{err[:120]}</i>")
            else:
                lines.append("  ⚠️ 데이터 없음 (점검 필요)")
            empty_titles.append(sec.get("title", "?"))
        lines.append("")
    if empty_titles:
        lines.append(f"<i>⚠️ 빈 섹션: {', '.join(empty_titles)} — Render Logs 확인</i>")
        lines.append("")
    lines.append(f"⏰ {now_kst().strftime('%H:%M')} KST")
    msg = "\n".join(lines)
    # 잘라서 버리지 않고 나눠 보낸다. 신고가 역사적·52주를 전 종목 싣기로 한
    # 이상(_NH_LIST_MAX) 본문이 4,096자를 넘는 날이 정상이고, 예전처럼
    # msg[:3990] 으로 자르면 뒤쪽 종목이 말없이 사라진다.
    send_telegram_long(msg)


def _closing_brief_key() -> str:
    """오늘 장마감 시황을 보냈는지 적어 두는 ops_state 키의 값(= 발송일)."""
    return "closing_brief_sent"


# 데이터가 준비되기를 기다려 주는 마지막 시각. 이 시각을 넘기면 준비가 덜 돼도
# 보낸다 — 덜 찬 시황이라도 오는 편이, 아무 말 없이 하루가 지나는 것보다 낫다.
# 각 섹션은 제 실패를 스스로 적으므로 덜 찬 채로 나가도 거짓말은 아니다.
_CLOSING_BRIEF_DEADLINE_HHMM = (20, 35)


def _brief_data_ready() -> tuple[bool, str]:
    """시황을 보낼 만큼 데이터가 찼는지. (준비됨, 사유)

    **2026-09-18 에 이걸 안 보고 보냈다가 그날 시황을 버렸다.** 배포가 나가면
    Render 무료 플랜은 디스크가 비영속이라 db/dashboard.db 가 통째로 사라진다.
    그 직후 부팅 캐치업이 깨어나 빈 DB 위에서 시황을 만들었고, 신고가 섹션이
    '일봉 거래일이 0일뿐' 으로 비었다. 게다가 '오늘 보냈음' 표시까지 찍혀
    데이터가 다 찬 뒤에도 다시 보낼 수 없었다.

    그래서 보내기 전에 두 가지를 본다. 시황의 두 기둥이다.

      일봉(ohlcv)  신고가 섹션의 입력. 재배포 후 부팅 스레드와 16:10 잡이 채운다
      stocks       섹터·특징주·거래대금 섹션의 입력

    준비가 덜 됐으면 보내지 않고 **표시도 찍지 않는다.** 30분 뒤 캐치업이 다시
    본다. 마감 시각(_CLOSING_BRIEF_DEADLINE_HHMM)을 넘기면 그때는 보낸다.
    """
    try:
        import ohlcv_autofill as _oa
        st = _oa.status()
        rows = int(st.get("rows") or 0)
    except Exception as exc:                                  # noqa: BLE001
        return False, f"일봉 현황을 못 읽었다: {type(exc).__name__}"
    if rows <= 0:
        return False, "일봉 테이블이 비어 있다 (재배포 직후면 채워지는 중)"
    # 행이 있어도 **채우는 중이면** 아직이다. 대상이 ~1,500종목이라 재배포 직후
    # 전 구간 채움이 몇 분 걸리는데, 그 사이 행 수만 보고 보내면 절반만 훑은
    # 신고가가 나간다. 부르는 쪽이 곧바로 _fill_ohlcv_job() 을 불러 그 락이
    # 풀릴 때까지 기다린 뒤 다시 본다.
    if _OHLCV_FILL_LOCK.locked():
        return False, f"일봉 채움이 진행 중이다 (지금 {rows:,}행)"
    try:
        health = _check_market_data_health()
    except Exception as exc:                                  # noqa: BLE001
        return False, f"건강도 점검 실패: {type(exc).__name__}"
    if not health.get("stocks_kr"):
        return False, "stocks 에 KR 종목이 없다 (가격 동기화 전)"
    return True, f"일봉 {rows:,}행 · KR {health['stocks_kr']}종목"


# 시황 발송은 **한 번에 하나만** 돈다. '오늘 보냈나' 를 보고 → 보내고 →
# 표시를 찍기까지가 한 덩어리여야 한다. 지금 부르는 데가 넷이다 — 16:00 cron,
# 30분 캐치업 cron, 부팅 캐치업 스레드, 수동 API. 둘이 겹치면 둘 다 '아직 안
# 보냈다' 를 보고 둘 다 보낸다. 게다가 데이터가 덜 찼을 때 일봉을 채우느라
# 몇 분을 쓰므로 겹칠 창이 그만큼 넓다.
_CLOSING_BRIEF_LOCK = threading.Lock()


def send_closing_market_summary(*, catchup: bool = False,
                                require_ready: bool = True) -> bool:
    """`_send_closing_market_summary` 를 한 번에 하나만 돌게 감싼다.

    이미 누가 보내는 중이면 기다렸다가, 하루 한 번 제한에 걸려 조용히 돌아간다.
    """
    with _CLOSING_BRIEF_LOCK:
        return _send_closing_market_summary(catchup=catchup,
                                            require_ready=require_ready)


def _send_closing_market_summary(*, catchup: bool = False,
                                 require_ready: bool = True) -> bool:
    """장마감 시황 텔레그램 발송 (평일 16:00 cron + 밀리면 캐치업).

    **하루 한 번만 나간다.** 발송한 날짜를 ops_state 에 적고, cron 과 캐치업이
    같은 날 두 번 부르면 뒤엣것은 조용히 돌아간다. 프로세스 메모리에 두면
    Render 가 재배포·재시작할 때마다 잊어버려 같은 시황이 또 나간다.

    ## 왜 16:00 인가

    사용자 요청이다. 정규장 마감(15:30) 직후 확정 종가로 그날을 정리해 받는다.

    **대신 투자자별 수급은 당일 확정치가 아니다.** KRX 확정은 통상 18:00 전후,
    네이버 반영은 ~18:30 이라 16:00 에는 전일 값밖에 없다. 예전에 이걸 이유로
    19:00 으로 미뤄 뒀었는데, 그 대가로 시황이 장 끝나고 세 시간 반 뒤에 왔다.
    수급 한 줄 때문에 나머지 전부를 늦추는 것이 맞는 거래가 아니다.

    조용히 전일 값을 오늘 값인 척 내보내지는 않는다 — `build_market_summary` 가
    수급 최신일이 오늘이 아니면 섹션에 그 날짜를 적는다(12930행 근처).
    수급 확정치를 보려면 `/시황` 을 저녁에 한 번 더 부르면 된다.

    기다리지 않는 이유도 같다. 예전 코드는 당일 수급이 뜰 때까지 10분씩 세 번
    잤는데, 16:00 에는 30분을 기다려도 안 나온다. 기다림은 발송만 늦춘다.
    """
    now = now_kst()
    today_ymd = now.strftime("%Y-%m-%d")
    if str(_ops_get(_closing_brief_key(), "")) == today_ymd:
        log.info("[장마감시황] %s 는 이미 보냈다 — 건너뛴다 (catchup=%s)",
                 today_ymd, catchup)
        return False

    # 데이터가 덜 찼으면 보내지 않고 표시도 찍지 않는다. 마감 시각 전까지는
    # 30분마다 캐치업이 다시 본다 — 위 _brief_data_ready 설명 참고.
    if require_ready:
        ready, why = _brief_data_ready()
        if not ready:
            # **기다리기만 하지 않는다.** 재배포로 DB 가 날아간 상태에서
            # 30분마다 "아직 안 찼다" 만 적고 물러나면, 채우는 주체가 그동안
            # 한 번도 안 깨어 있었을 때 하루가 그대로 지나간다 (2026-09-18).
            # 빠진 것이 일봉이면 여기서 직접 채운다 — 빈 DB 면 수 분이고,
            # 이 잡은 max_instances=1 · misfire_grace 1800 이라 막아도 된다.
            log.warning("[장마감시황] 데이터 미완 — 직접 채우고 다시 본다: %s", why)
            try:
                r = _fill_ohlcv_job()
                log.info("[장마감시황] 일봉 채움 결과: %s",
                         {k: r.get(k) for k in ("ok", "codes", "rows", "basis",
                                                "skipped", "error")})
            except Exception as exc:                          # noqa: BLE001
                log.warning("[장마감시황] 일봉 채움 실패: %s", exc)
            ready, why = _brief_data_ready()
        past_deadline = (now_kst().hour, now_kst().minute) >= _CLOSING_BRIEF_DEADLINE_HHMM
        if not ready and not past_deadline:
            log.warning("[장마감시황] 채운 뒤에도 미완 — 보내지 않는다: %s", why)
            return False
        if not ready:
            log.warning("[장마감시황] 마감 시각이라 미완인 채로 보낸다: %s", why)
        else:
            log.info("[장마감시황] 데이터 준비됨 — %s", why)

    # 1) 종가 가격 재갱신 (장 마감 확정값)
    try:
        n = _refresh_prices_from_naver()
        log.info("[장마감시황] 가격 갱신 %d종목", n)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("[장마감시황] 가격 갱신 실패: %s", exc)

    # 2) 수급 갱신 한 번. 당일치가 없으면 없는 대로 간다 — 위 설명 참고.
    try:
        r = _refresh_flow_batch(top_n=200)
        log.info("[장마감시황] 수급 갱신: %s", r)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("[장마감시황] 수급 갱신 실패: %s", exc)

    head = "🌙 장마감 시황" + (" (지연 발송)" if catchup else "")
    send_market_summary_telegram(header=head)
    _ops_set(_closing_brief_key(), today_ymd)
    # **표시를 즉시 밖으로 내보낸다.** 이 표시는 ops_state 에 있고 ops_state 는
    # 재배포로 사라지는 db/dashboard.db 안에 산다. Gist 백업은 매시 30분이라,
    # 보낸 뒤 그 정각 전에 배포가 나가면 표시만 사라지고 캐치업이 같은 시황을
    # 또 보낸다 — 2026-09-18 20:04 발송분이 20:22 배포 뒤 20:42 에 한 번 더
    # 나간 경로가 이것이다. 여기서 한 번 백업하면 그 창이 닫힌다.
    try:
        from db_backup import backup_db as _bk
        r = _bk()
        log.info("[장마감시황] 발송 표시 백업: %s",
                 "ok" if r.get("ok") else r.get("reason"))
    except Exception as exc:                                  # noqa: BLE001
        log.warning("[장마감시황] 발송 표시 백업 실패: %s — 재배포 시 중복 발송 가능",
                    exc)
    log.info("[장마감시황] %s 발송 완료 (catchup=%s)", today_ymd, catchup)
    return True


# 장마감 시황을 보냈어야 하는 시각. cron 도 캐치업도 이 값을 본다 —
# 한 곳만 고치면 둘 다 따라온다.
_CLOSING_BRIEF_HHMM = (16, 0)


def closing_brief_catchup() -> bool:
    """밀린 장마감 시황을 뒤늦게라도 보낸다.

    **이게 없으면 시황은 안 오는 날이 생긴다.** Render 무료 플랜은 15분 유휴면
    인스턴스를 재운다. 잠든 동안에는 프로세스가 아예 없으니 APScheduler 의
    16:00 cron 도 돌지 않고, 깨어난 뒤에는 그 시각이 지나 버려 다음 평일까지
    아무 일도 일어나지 않는다. 2026-09-18 에 19:00 예약분이 19:41 에 온 것이
    그 형태였다.

    그래서 '시각에 맞춰 깨어 있기' 에 기대지 않고 **깨어날 때마다 밀린 것이
    있는지 본다.** 부팅 직후와 워치독(평일 08~20시 30분 간격)이 부른다.
    하루 한 번 제한은 `send_closing_market_summary` 가 건다.

    주말·공휴일은 보내지 않는다. 휴장일 판정은 '오늘 시세가 갱신됐는가' 가
    아니라 요일로 한다 — 공휴일에 안 보내는 것보다 평일에 빠뜨리는 쪽이 나쁘다.
    """
    now = now_kst()
    if now.weekday() >= 5:
        return False
    if (now.hour, now.minute) < _CLOSING_BRIEF_HHMM:
        return False
    if str(_ops_get(_closing_brief_key(), "")) == now.strftime("%Y-%m-%d"):
        return False
    log.info("[장마감시황] 밀린 발송을 지금 보낸다 (%s)", now.strftime("%H:%M"))
    try:
        return send_closing_market_summary(catchup=True)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("[장마감시황] 캐치업 실패: %s", exc)
        return False


# ── 데이터 정합성 워치독 + 자가복구 ────────────────────────────────────────
# 시황 3개 섹션(섹터/특징주/수급)이 의존하는 stocks·flow_cache 가 비거나
# stale 해지는 사고가 반복됨 (Render 비영속 디스크 + Naver 스크랩 실패).
# 워치독이 주기적으로 건강도를 점검 → 비정상이면 자동 재갱신 + 관리자 1회 알림.
# 알림 중복 방지 상태는 ops_state(DB)에 있다 — 여기 dict 는 마지막 점검 결과와
# DB 가 막혔을 때의 임시 사본만 들고 있다(_ops_get/_ops_set).
_WATCHDOG_STATE: dict = {"last_summary": None}

# 언제부터 언제까지 따질지 — 갱신이 돌았어야 하는 시각에만 따진다.
#   가격: 평일 09:05~15:35(30분 간격) + 16:00~17:55(5분 간격)
#   수급: 평일 15:40 배치 1회
_WD_STALE_FROM, _WD_STALE_TO = 1000, 1800   # 가격 정체를 따지는 시간대(HHMM)
_WD_STALE_MAX_MIN = 120                     # 이 시간대에 이만큼 안 바뀌면 정체
_WD_FLOW_FROM = 1610                        # 수급 0행을 따지기 시작하는 시각


def _watchdog_checks_due(now=None) -> dict:
    """지금 무엇을 따질 수 있는지.

    예전 판정은 '평일이고 6시간 넘었으면 정체' 뿐이었다. 그런데 가격 동기화는
    평일 17:55 이 마지막이라 다음 날 08:00 에는 **항상** 13시간이 지나 있다 —
    워치독이 08:00 부터 도니까 평일 아침마다 한 번은 반드시 울렸다. 정상인데
    울리는 알림은 곧 안 보게 되므로, 갱신이 돌았어야 하는 시간대에만 따진다.

    수급(flow_cache)은 평일 15:40 배치가 하루 한 번 채운다. Gist 백업에서
    빠져 있어(재수집 대상) 재시작하면 0행에서 시작하므로, 배치 전 0행은
    사고가 아니라 정상이다.

    공휴일은 갱신 자체가 없는 날이니 둘 다 따지지 않는다.
    """
    now = now or now_kst()
    hhmm = now.hour * 100 + now.minute
    trading = not _is_kr_holiday(now)          # 주말도 여기서 걸러진다
    return {
        "trading_day": trading,
        "stocks_stale": trading and _WD_STALE_FROM <= hhmm <= _WD_STALE_TO,
        "flow_rows": trading and hhmm >= _WD_FLOW_FROM,
    }


def _ops_get(key: str, default=None):
    """운영 상태 읽기. DB 가 없으면 메모리로 물러난다(기능은 계속 돈다)."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT value FROM ops_state WHERE key=?", (key,)).fetchone()
        if row is not None:
            return row[0]
    except Exception as exc:
        log.debug("[워치독] 상태 읽기 실패(%s): %s", key, exc)
        return _WATCHDOG_STATE.get(key, default)
    return default


def _ops_set(key: str, value) -> None:
    """운영 상태 쓰기. 메모리에도 같이 둔다 — DB 가 막혀도 한 프로세스 안에서는 산다."""
    _WATCHDOG_STATE[key] = value
    try:
        with _get_db() as conn:
            conn.execute(
                "INSERT INTO ops_state (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "                               updated_at=excluded.updated_at",
                (key, str(value), now_kst().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
    except Exception as exc:
        log.debug("[워치독] 상태 쓰기 실패(%s): %s", key, exc)


def _check_market_data_health() -> dict:
    """KR 시황 핵심 데이터 건강도 점검.

    Returns: {
      "healthy": bool, "issues": [str, ...],
      "stocks_kr": int,        # change_pct 있는 KR 종목 수
      "flow_rows": int,        # 외인 수급 있는 flow_cache 행 수
      "flow_latest": str|None, # 수급 최신 거래일
      "stocks_age_min": float|None,  # stocks 최신 updated_at 경과(분)
    }
    """
    out = {"healthy": True, "issues": [], "stocks_kr": 0,
           "flow_rows": 0, "flow_latest": None, "stocks_age_min": None}
    if not (_SQLITE_OK and USE_SQLITE):
        out["healthy"] = False
        out["issues"].append("SQLite 비활성")
        return out
    try:
        with _get_db() as conn:
            out["stocks_kr"] = conn.execute(
                "SELECT COUNT(*) FROM stocks "
                "WHERE (market='' OR market LIKE 'KOS%') "
                "  AND COALESCE(is_etf,0)=0 AND change_pct IS NOT NULL "
                "  AND close >= 1000"
            ).fetchone()[0]
            out["flow_rows"] = conn.execute(
                "SELECT COUNT(*) FROM flow_cache WHERE foreign_value_json IS NOT NULL"
            ).fetchone()[0]
            row = conn.execute(
                "SELECT MAX(updated_at) FROM stocks "
                "WHERE (market='' OR market LIKE 'KOS%')"
            ).fetchone()
            if row and row[0]:
                from datetime import datetime as _dt
                try:
                    upd = _dt.fromisoformat(row[0])
                    # **stocks.updated_at 은 UTC 다.** UPSERT 가 SQLite 의
                    # datetime('now') 로 쓰는데 그건 UTC 를 준다. 그걸 여태
                    # now_kst() 에서 빼고 있어서 경과시간이 늘 +540분(9시간)
                    # 부풀었다. 임계값이 120분이라 **갱신 직후에도 항상
                    # '정체' 로 판정**됐고, 2026-09-29 의 "stocks 갱신 정체
                    # (540분 전)" 경보가 바로 이것이다. 데이터가 아니라
                    # 시간대 계산이 틀린 것이었다.
                    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                    age = (now_utc - upd).total_seconds() / 60
                    # 혹시 KST 로 쓰인 행이 섞여 있으면 음수가 나온다. 0 으로 본다.
                    out["stocks_age_min"] = round(max(age, 0.0), 1)
                except Exception:
                    pass
            frow = conn.execute(
                "SELECT dates_json FROM flow_cache WHERE code='005930'"
            ).fetchone()
            if frow and frow["dates_json"]:
                dts = _parse_json_list(frow["dates_json"])
                out["flow_latest"] = dts[-1] if dts else None
    except Exception as exc:
        out["healthy"] = False
        out["issues"].append(f"DB 조회 실패: {str(exc)[:80]}")
        return out

    # 판정 기준 — 갱신이 돌았어야 하는 때만 따진다(_watchdog_checks_due)
    due = _watchdog_checks_due()
    out["due"] = due
    # 종목 수는 시각과 무관하다. 비었으면 언제 봐도 사고다.
    if out["stocks_kr"] < 1000:
        out["healthy"] = False
        out["issues"].append(f"KR 종목 부족 ({out['stocks_kr']}개, 정상 ~2500)")
    if due["flow_rows"] and out["flow_rows"] < 50:
        out["healthy"] = False
        out["issues"].append(f"수급 데이터 부족 ({out['flow_rows']}행)")
    if (due["stocks_stale"] and out["stocks_age_min"] is not None
            and out["stocks_age_min"] > _WD_STALE_MAX_MIN):
        out["healthy"] = False
        out["issues"].append(f"stocks 갱신 정체 ({out['stocks_age_min']:.0f}분 전)")
    return out


def _watchdog_telegram_on() -> bool:
    """워치독 알림을 텔레그램으로 보낼지. **기본은 끔**(2026-09-29 사용자 요청 — '자동복구
    실패' 알림을 받지 않는다). 판정·자가복구·상태 저장·로그는 그대로 돈다.
    다시 켜려면 Render 환경변수 WATCHDOG_TELEGRAM=1."""
    return os.environ.get("WATCHDOG_TELEGRAM", "0").strip() == "1"


def _watchdog_notify(msg: str) -> None:
    """워치독의 텔레그램 자리. 꺼져 있으면 Render 로그에만 남긴다."""
    if _watchdog_telegram_on():
        send_telegram(msg)
    else:
        log.warning("[워치독] 텔레그램 알림 꺼짐(WATCHDOG_TELEGRAM) — %s",
                    re.sub(r"<[^>]+>", "", msg).replace("\n", " / "))


def _market_watchdog():
    """주기 워치독 — 비정상 감지 시 자동 재갱신 + 관리자 1회 알림/복구 알림.
    cron: 평일 08:00~20:00 30분 간격."""
    health = _check_market_data_health()
    today = now_kst().strftime("%Y-%m-%d")
    # 중복 방지 상태는 DB(ops_state)에 둔다. 예전엔 프로세스 메모리라
    # 재시작·재배포마다 False 로 돌아가 같은 사고를 또 알렸다.
    alerted = str(_ops_get("watchdog_alerted", "0")) == "1"

    if health["healthy"]:
        # 직전에 사고 알림을 보냈다면 복구 알림 1회
        if alerted:
            _ops_set("watchdog_alerted", 0)
            _watchdog_notify(
                f"🛠 <b>[시스템] 데이터 복구됨</b>\n"
                f"KR 종목 {health['stocks_kr']}개 · 수급 {health['flow_rows']}행 정상화"
            )
        _WATCHDOG_STATE["last_summary"] = health
        return

    log.warning("[워치독] 비정상 감지: %s", "; ".join(health["issues"]))
    # ── 자가복구 시도: 가격 → 수급 재갱신 ──
    recovered = {}
    try:
        recovered["prices"] = _refresh_prices_from_naver()
    except Exception as exc:
        log.warning("[워치독] 가격 복구 실패: %s", exc)
    try:
        # 수급 복구는 200종목을 네이버에서 다시 긁는다. 아직 따질 때가 아닌
        # 0행(15:40 배치 전)까지 긁으면 헛되이 200번을 두드리는 셈이고,
        # 차단이 의심되는 상황에서 더 두드리는 건 역효과다.
        if (health.get("due") or {}).get("flow_rows") and health["flow_rows"] < 50:
            r = _refresh_flow_batch(top_n=200)
            recovered["flow"] = r.get("success", 0) if isinstance(r, dict) else 0
    except Exception as exc:
        log.warning("[워치독] 수급 복구 실패: %s", exc)

    after = _check_market_data_health()
    if after["healthy"]:
        log.info("[워치독] 자가복구 성공 (prices=%s, flow=%s)",
                 recovered.get("prices"), recovered.get("flow"))
        # 이전에 사고 알림을 이미 보냈으면 복구 알림, 아니면 조용히 복구
        if alerted:
            _ops_set("watchdog_alerted", 0)
            _watchdog_notify(
                f"🛠 <b>[시스템] 자동 복구 완료</b>\n"
                f"KR 종목 {after['stocks_kr']}개 · 수급 {after['flow_rows']}행"
            )
    elif alerted:
        log.warning("[워치독] 비정상 지속 — 이미 알림 보냄, 다시 보내지 않는다")
    elif _ops_get("watchdog_alert_date") == today:
        # 하루 1회 상한. 고쳤다 다시 깨지기를 반복해도 하루에 한 번만 알린다.
        log.warning("[워치독] 비정상 — 오늘 이미 알렸다(상한). 로그만 남긴다: %s",
                    "; ".join(after["issues"]))
    else:
        _ops_set("watchdog_alerted", 1)
        _ops_set("watchdog_alert_date", today)
        _watchdog_notify(
            "🛠 <b>[시스템] 데이터 이상 — 자동복구 실패</b>\n"
            + "\n".join(f"  • {i}" for i in after["issues"])
            + "\n<i>Naver 차단/네트워크 의심 — Render Logs 확인</i>"
        )
    _WATCHDOG_STATE["last_summary"] = after


@app.route("/api/ops/watchdog", methods=["GET", "POST"])
def api_ops_watchdog():
    """워치독 수동 점검(GET) / 복구 실행(POST)."""
    if request.method == "POST":
        threading.Thread(target=_market_watchdog, daemon=True,
                         name="watchdog-manual").start()
        return jsonify({"ok": True, "message": "워치독 백그라운드 실행"})
    out = _check_market_data_health()
    # 손으로 열어 봤을 때 '왜 조용한지' 가 보여야 한다. due 가 꺼져 있으면
    # 값이 나빠 보여도 아직 따질 때가 아니라는 뜻이다.
    due = out.get("due") or {}
    skipped = [n for n, k in (("수급 0행", "flow_rows"), ("가격 정체", "stocks_stale"))
               if not due.get(k)]
    out["note"] = ("휴장일 — 갱신 자체가 없는 날이다" if not due.get("trading_day")
                   else (f"지금은 안 따짐: {', '.join(skipped)}" if skipped
                         else "전부 따지는 시간대"))
    # 무엇이 빠졌는지를 판정과 같이 내보낸다. '건강하지 않다' 만으로는
    # 화면도 사람도 어느 데이터를 못 믿어야 하는지 알 수 없다.
    try:
        recent = list(_COLLECT_ERRORS)[-8:]
        out["recent_collect_errors"] = list(reversed(recent))
        out["collect_error_sources"] = sorted({e.get("source", "?") for e in _COLLECT_ERRORS})
    except Exception:
        pass
    return jsonify(out)


# ── 수급 심화 시그널 (flow_cache 20일 시계열 분석) ───────────────────────────
def _flow_streak(series: list, positive: bool) -> int:
    """series 끝에서부터 연속 동일부호 일수. positive=True면 순매수 연속."""
    n = 0
    for v in reversed(series):
        if (v is None):
            break
        if (v > 0) if positive else (v < 0):
            n += 1
        else:
            break
    return n


def _analyze_flow_signals(min_eok: float = 50.0, streak_min: int = 3) -> dict:
    """flow_cache 20일 시계열 → 수급 시그널 포착.

    min_eok: 노이즈 컷 (최신일 |순매매| 억원 하한)
    Returns: {
      "date": 최신 거래일,
      "dual_buy":  [외국인+기관 동시 순매수],   "dual_sell": [동시 순매도],
      "streak_buy":[외국인 N일 연속 순매수],     "streak_sell":[연속 순매도],
      "reversal":  [최근 순매도→당일 강한 순매수 전환],
    }  각 종목: {code, name, foreign, inst, fstreak} (foreign/inst 단위: 억원)
    """
    empty = {"date": None, "dual_buy": [], "dual_sell": [],
             "streak_buy": [], "streak_sell": [], "reversal": []}
    if not (_SQLITE_OK and USE_SQLITE):
        return empty
    try:
        with _get_db() as conn:
            rows = conn.execute("""
                SELECT f.code AS code,
                       COALESCE(NULLIF(s.name,''), NULLIF(f.name,''), f.code) AS name,
                       f.dates_json, f.foreign_value_json, f.inst_value_json
                FROM flow_cache f
                LEFT JOIN stocks s ON s.code = f.code
                WHERE f.foreign_value_json IS NOT NULL
            """).fetchall()
    except Exception as exc:
        log.warning("[수급시그널] 조회 실패: %s", exc)
        return empty

    parsed: list = []
    latest_date: str | None = None
    for r in rows:
        name = r["name"] if r["name"] != r["code"] else r["code"]
        dts = _parse_json_list(r["dates_json"])
        fv = _parse_json_list(r["foreign_value_json"])
        iv = _parse_json_list(r["inst_value_json"])
        if not dts or not fv:
            continue
        if latest_date is None or dts[-1] > latest_date:
            latest_date = dts[-1]
        parsed.append({"code": r["code"], "name": name,
                       "dts": dts, "fv": fv, "iv": iv})

    thr = min_eok * 1e8  # 억 → 원
    cand: list = []
    for p in parsed:
        if p["dts"][-1] != latest_date:   # 공통 최신일만 (날짜 혼재 방지)
            continue
        f_last = p["fv"][-1] if p["fv"] else 0
        i_last = p["iv"][-1] if p["iv"] else 0
        cand.append({
            "code": p["code"], "name": p["name"],
            "foreign": f_last / 1e8, "inst": i_last / 1e8,
            "f_raw": f_last, "i_raw": i_last,
            "fstreak_buy": _flow_streak(p["fv"], True),
            "fstreak_sell": _flow_streak(p["fv"], False),
            "fv": p["fv"],
        })

    # 1) 쌍끌이 매수/매도 (외국인·기관 동시, 각 |값| >= thr)
    dual_buy = sorted(
        [c for c in cand if c["f_raw"] >= thr and c["i_raw"] >= thr],
        key=lambda c: c["f_raw"] + c["i_raw"], reverse=True)[:5]
    dual_sell = sorted(
        [c for c in cand if c["f_raw"] <= -thr and c["i_raw"] <= -thr],
        key=lambda c: c["f_raw"] + c["i_raw"])[:5]

    # 2) 외국인 연속 순매수/순매도 (streak >= streak_min, 최신일 |값| >= thr/2)
    half = thr / 2
    streak_buy = sorted(
        [c for c in cand if c["fstreak_buy"] >= streak_min and c["f_raw"] >= half],
        key=lambda c: (c["fstreak_buy"], c["f_raw"]), reverse=True)[:5]
    streak_sell = sorted(
        [c for c in cand if c["fstreak_sell"] >= streak_min and c["f_raw"] <= -half],
        key=lambda c: (c["fstreak_sell"], -c["f_raw"]), reverse=True)[:5]

    # 3) 수급 반전 — 직전 3일 외국인 순매도였다가 당일 강한 순매수 전환
    reversal: list = []
    for c in cand:
        fv = c["fv"]
        if len(fv) < 4:
            continue
        prior3 = sum(fv[-4:-1])
        if prior3 < 0 and c["f_raw"] >= thr:
            reversal.append(c)
    reversal = sorted(reversal, key=lambda c: c["f_raw"], reverse=True)[:5]

    return {"date": latest_date, "dual_buy": dual_buy, "dual_sell": dual_sell,
            "streak_buy": streak_buy, "streak_sell": streak_sell,
            "reversal": reversal}


# KBJ P5 삭제: alert_flow_signals


@app.route("/api/flow/signals")
def api_flow_signals():
    """수급 심화 시그널 JSON (쌍끌이/연속/반전)."""
    return jsonify(_analyze_flow_signals())


# ── 섹터 로테이션 2.0: DB 기반 동적 추천/회피 + 시장 국면 ────────────────────────────────────
def _detect_market_phase() -> dict:
    """VIX + USD/KRW 실제 값으로 시장 국면 동적 판정. 하드코딩 없음."""
    macro = _read_fresh_json(BASE_DIR / "cache" / "macro_data.json", 24 * 60) or {}
    items = {it.get("name"): it for it in (macro.get("items") or [])}
    vix = items.get("VIX", {}).get("price")
    usdkrw = items.get("USD/KRW", {}).get("price")
    usdkrw_chg = items.get("USD/KRW", {}).get("change_pct") or 0
    vix_chg = items.get("VIX", {}).get("change_pct") or 0

    # 국면 판정 (동적 임계치 — 최근값 기반)
    phase = "중립"
    reasons: list = []
    if vix is None:
        phase = "데이터부족"
    elif vix >= 30:
        phase = "위험회피"
        reasons.append(f"VIX {vix:.1f} 높음")
    elif vix >= 22:
        phase = "경계"
        reasons.append(f"VIX {vix:.1f} 평균 이상")
    elif vix <= 15 and vix_chg < 0:
        phase = "위험선호"
        reasons.append(f"VIX {vix:.1f} 낮음·하락")
    else:
        phase = "중립"
        reasons.append(f"VIX {vix:.1f}")

    if usdkrw and usdkrw >= 1400:
        reasons.append(f"원화 약세 (USD/KRW {usdkrw:.0f})")
        if phase in ("중립", "위험선호"):
            phase = "경계"
    elif usdkrw and usdkrw <= 1250:
        reasons.append(f"원화 강세 (USD/KRW {usdkrw:.0f})")

    return {
        "phase": phase,
        "vix": vix,
        "vix_change_pct": vix_chg,
        "usdkrw": usdkrw,
        "usdkrw_change_pct": usdkrw_chg,
        "reasons": reasons,
    }


@app.route("/api/sector_rotation/phase")
def api_sector_rotation_phase():
    """실시간 DB 기반 섹터 로테이션 + 추천/회피 섹터 + 시장 국면.
    하드코딩 없음 — sectors_raw는 /api/sector_rotation 와 동일 집계 재사용."""
    try:
        # 기존 섹터 로테이션 데이터 재사용 (HTTP 순환 호출 대신 직접 계산)
        sparklines = _load_ticker_sparklines_kr()
        uni = _load_naver_universe()
        stocks = (uni or {}).get("stocks") or {}
        if not sparklines or not stocks:
            return jsonify({"error": "캐시 데이터 부족"}), 503

        sectors_raw: dict = {}
        for code, st in stocks.items():
            sp = sparklines.get(code)
            if not sp or len(sp) < 20 or not sp[0]:
                continue
            sec_list = st.get("sectors") or []
            if not sec_list:
                continue
            sector = sec_list[0]
            try:
                ret_5d = (sp[-1] / sp[-5] - 1) * 100 if sp[-5] else None
                ret_20d = (sp[-1] / sp[0] - 1) * 100
            except Exception:
                continue
            sectors_raw.setdefault(sector, []).append({
                "code": code,
                "volume_mn": st.get("volume_mn") or 0,
                "chg_today": st.get("change_pct") or 0,
                "ret_5d": ret_5d, "ret_20d": ret_20d,
            })

        def _wavg(rows, key):
            vals = [r for r in rows if r.get(key) is not None]
            if not vals:
                return None
            tw = sum(r["volume_mn"] for r in vals) or 0
            if tw > 0:
                return round(sum((r[key] or 0) * (r["volume_mn"] or 0)
                                 for r in vals) / tw, 2)
            return round(sum(r[key] or 0 for r in vals) / len(vals), 2)

        sectors: list = []
        for name, rows in sectors_raw.items():
            if len(rows) < 3:
                continue
            # 모멘텀 스코어 = 1주 가중 + 1개월 × 2
            r5 = _wavg(rows, "ret_5d") or 0
            r20 = _wavg(rows, "ret_20d") or 0
            score = round(r5 + r20 * 2, 2)
            sectors.append({
                "name": name,
                "stock_count": len(rows),
                "ret_1w": r5, "ret_1m": r20,
                "change_today": _wavg(rows, "chg_today"),
                "momentum_score": score,
            })
        sectors.sort(key=lambda x: x["momentum_score"], reverse=True)

        # 추천/회피: 상위 3 + 양수 / 하위 3 + 음수 (조건 동적)
        recommended = [s for s in sectors[:5] if s["momentum_score"] > 0][:3]
        avoid = [s for s in sectors[-5:] if s["momentum_score"] < 0][-3:]
        avoid.reverse()

        phase_info = _detect_market_phase()

        # 국면별 상위 종목 추천 (각 추천 섹터에서 1주 모멘텀 상위 5종목)
        top_stocks_by_sector: dict = {}
        for sec in recommended:
            rows = sectors_raw.get(sec["name"]) or []
            rows_sorted = sorted(
                [r for r in rows if r.get("ret_5d") is not None],
                key=lambda x: x["ret_5d"], reverse=True
            )[:5]
            top_stocks_by_sector[sec["name"]] = [
                {
                    "code": r["code"],
                    "name": stocks.get(r["code"], {}).get("name") or r["code"],
                    "ret_5d": r["ret_5d"],
                    "ret_20d": r["ret_20d"],
                    "volume_mn": r["volume_mn"],
                }
                for r in rows_sorted
            ]

        return jsonify({
            "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "market_phase": phase_info,
            "recommended": recommended,
            "avoid": avoid,
            "top_stocks_by_sector": top_stocks_by_sector,
            "total_sectors": len(sectors),
            "all_sectors": sectors,
        })
    except Exception as exc:
        log.exception("sector rotation phase")
        return jsonify({"error": str(exc)}), 500


# ── 소셜 센티먼트 (네이버 토론방) ────────────────────────────────────
_SENTIMENT_CACHE: dict = {}  # {code: {"data": {...}, "fetched_at": float}}
_SENTIMENT_TTL = 30 * 60  # 30분


def _load_sentiment_dict() -> dict:
    f = BASE_DIR / "cache" / "sentiment_dict.json"
    if not f.exists():
        return {"positive": {}, "negative": {}, "neutral_but_meaningful": {}}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"positive": {}, "negative": {}, "neutral_but_meaningful": {}}


# KBJ P4 삭제: _fetch_naver_board_titles — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


def _score_sentiment_titles(titles: list, sdict: dict) -> dict:
    pos_dict = sdict.get("positive") or {}
    neg_dict = sdict.get("negative") or {}
    pos_score = 0; neg_score = 0
    hit_pos: dict = {}; hit_neg: dict = {}
    for t in titles:
        for kw, w in pos_dict.items():
            if kw and kw in t:
                pos_score += w
                hit_pos[kw] = hit_pos.get(kw, 0) + 1
        for kw, w in neg_dict.items():
            if kw and kw in t:
                neg_score += w
                hit_neg[kw] = hit_neg.get(kw, 0) + 1
    total = pos_score + neg_score
    ratio = round(pos_score / total, 3) if total > 0 else 0.5
    label = "중립"
    if total >= 5:
        if ratio >= 0.65:
            label = "긍정"
        elif ratio <= 0.35:
            label = "부정"
    return {
        "posts": len(titles),
        "positive_score": pos_score,
        "negative_score": neg_score,
        "ratio": ratio,
        "label": label,
        "top_positive": sorted(hit_pos.items(), key=lambda x: -x[1])[:8],
        "top_negative": sorted(hit_neg.items(), key=lambda x: -x[1])[:8],
    }


# ── 수익률 저널 (trade_journal) ─────────────
# KBJ P8 삭제: _init_trade_journal — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033)


# KBJ P8 삭제: api_journal_add — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033)


# KBJ P8 삭제: api_journal_list — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033)


# KBJ P8 삭제: api_journal_summary — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033)


# KBJ P8 삭제: api_journal_delete — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033)


# ── 🔗 밸류체인 맵 (산업별 레이어 + 뉴스 heat) ──────────────
@app.route("/api/valuechain")
def api_valuechain():
    """밸류체인 5개 테마 + 레이어별 heat + 종목 실시간가 + 외국인 수급."""
    try:
        from valuechain import VALUECHAIN_MAP, calculate_layer_heat
    except Exception as exc:
        return jsonify({"error": f"valuechain 모듈 로드 실패: {exc}"}), 500

    theme_filter = (request.args.get("theme") or "").strip()
    heat = calculate_layer_heat() or {}

    # 모든 종목 코드 수집 (실시간 가격 일괄 조회용)
    all_codes: set = set()
    for tdata in VALUECHAIN_MAP.values():
        for layer in tdata["layers"]:
            for seg in layer["segments"]:
                for c in seg.get("companies", []):
                    if c.get("code"):
                        all_codes.add(c["code"])

    # SQLite 일괄 조회 — 가격 + 외국인 일별 net (flow_cache)
    price_map: dict = {}
    flow_map: dict = {}
    if _SQLITE_OK and USE_SQLITE and all_codes:
        try:
            with _get_db() as conn:
                qmarks = ",".join(["?"] * len(all_codes))
                rows = conn.execute(
                    f"SELECT code, name, close, change_pct, market FROM stocks "
                    f"WHERE code IN ({qmarks})", list(all_codes)
                ).fetchall()
                for r in rows:
                    price_map[r["code"]] = dict(r)
                # KR 종목만 외국인 net (flow_cache)
                kr_codes = [c for c in all_codes if c.isdigit() and len(c) == 6]
                if kr_codes:
                    qm2 = ",".join(["?"] * len(kr_codes))
                    fr = conn.execute(
                        f"SELECT code, foreign_value_json, foreign_sum_20 "
                        f"FROM flow_cache WHERE code IN ({qm2})", kr_codes
                    ).fetchall()
                    for f in fr:
                        try:
                            fv = json.loads(f["foreign_value_json"] or "[]")
                            today_net = (fv[-1] / 1e8) if fv else 0  # 억원 단위
                        except Exception:
                            today_net = 0
                        flow_map[f["code"]] = {
                            "today_net_eok": round(today_net, 1),
                            "sum20_eok": round((f["foreign_sum_20"] or 0) / 1e8, 1),
                        }
        except Exception as exc:
            log.debug("[valuechain] DB query: %s", exc)

    # 결과 빌드
    out: dict = {}
    for theme_id, tdata in VALUECHAIN_MAP.items():
        if theme_filter and theme_filter != theme_id:
            continue
        # heat 매핑
        theme_heat = heat.get(theme_id) or {}
        heat_by_layer = {lh["layer_id"]: lh for lh in (theme_heat.get("layers") or [])}

        layers_out = []
        for layer in tdata["layers"]:
            lh = heat_by_layer.get(layer["id"], {})
            segs_out = []
            for seg in layer["segments"]:
                comps_out = []
                # 세그먼트 외국인 net 합계
                seg_foreign_today = 0
                for c in seg.get("companies", []):
                    p = price_map.get(c["code"]) or {}
                    f = flow_map.get(c["code"]) or {}
                    if f.get("today_net_eok"):
                        seg_foreign_today += f["today_net_eok"]
                    comps_out.append({
                        **c,
                        "price": p.get("close"),
                        "change_pct": p.get("change_pct"),
                        "name_db": p.get("name") or c["name"],
                        "foreign_today_eok": f.get("today_net_eok"),
                        "foreign_sum20_eok": f.get("sum20_eok"),
                    })
                segs_out.append({
                    **seg,
                    "companies": comps_out,
                    "foreign_today_eok": round(seg_foreign_today, 1) if seg_foreign_today else 0,
                })
            layers_out.append({
                "id":      layer["id"],
                "title":   layer["title"],
                "subtitle": layer["subtitle"],
                "color":   layer["color"],
                "segments": segs_out,
                "heat":              lh.get("heat", 0),
                "bottleneck_alert":  lh.get("bottleneck_alert", False),
                "matched_keywords":  lh.get("matched_keywords", []),
                "bottleneck_matched": lh.get("bottleneck_matched", []),
                "top_headlines":     lh.get("top_headlines", []),
            })

        out[theme_id] = {
            "theme": tdata["theme"], "icon": tdata["icon"],
            "layers": layers_out,
            "total_heat": theme_heat.get("total_heat", 0),
        }

    out["_meta"] = heat.get("_meta", {})
    return jsonify(out)


# ── 🌍 글로벌 매크로 대시보드 ──────────────
# KBJ P5 삭제: api_global_macro


# ── CNN 공포탐욕지수 (Fear & Greed) ──────────────
@app.route("/api/fear_greed")
def api_fear_greed():
    """CNN F&G 우선, 실패 시 alternative.me 폴백. 30분 캐시."""
    cache_file = BASE_DIR / "cache" / "fear_greed.json"
    try:
        if cache_file.exists():
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 30:
                return Response(cache_file.read_text(encoding="utf-8"),
                                content_type="application/json; charset=utf-8")
    except Exception:
        pass

    rating_kr = {
        "Extreme Fear": "극단적 공포", "Fear": "공포",
        "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극단적 탐욕",
    }
    result: dict = {"score": None, "rating": None, "source": None}

    # 1) CNN
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/120.0 Safari/537.36",
                     "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        fg = d.get("fear_and_greed", {}) or {}
        score = fg.get("score")
        if score is not None:
            result = {
                "score": round(float(score)),
                "rating": fg.get("rating"),
                "rating_kr": rating_kr.get(fg.get("rating"), fg.get("rating", "")),
                "previous_close": round(float(fg.get("previous_close") or 0)),
                "previous_1_week": round(float(fg.get("previous_1_week") or 0)),
                "previous_1_month": round(float(fg.get("previous_1_month") or 0)),
                "source": "cnn",
                "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            }
    except Exception as exc:
        log.debug("[fear_greed] CNN: %s", exc)

    # 2) alternative.me (크립토 F&G — CNN 실패 폴백)
    if result.get("score") is None:
        try:
            import urllib.request
            req = urllib.request.Request("https://api.alternative.me/fng/?limit=1",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8"))
            v = (d.get("data") or [{}])[0]
            score = int(v.get("value", 50))
            cls = v.get("value_classification", "")
            result = {
                "score": score,
                "rating": cls,
                "rating_kr": rating_kr.get(cls, cls),
                "source": "alternative.me (crypto fallback)",
                "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as exc:
            log.debug("[fear_greed] alt: %s", exc)

    if result.get("score") is not None:
        try:
            cache_file.parent.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return jsonify(result)


# ── 한국투자증권 API (데이터 조회 전용 — 매매 X) ─────────────
import re as _re_kis


def _kis_valid_code(code: str) -> bool:
    return bool(_re_kis.fullmatch(r"\d{6}", code or ""))


@app.route("/api/kis/minute/<code>")
def api_kis_minute(code):
    """KIS 분봉 (1·3·5·10·15·30·60). 캐시: 장중 60s, 장외 1h."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원", "candles": []}), 400
    interval = max(1, min(60, int(request.args.get("interval", 1))))
    try:
        from kis_api import get_minute_chart
        candles = get_minute_chart(code, interval)
        return jsonify({"code": code, "interval": interval,
                        "candles": candles, "count": len(candles)})
    except ImportError as exc:
        return jsonify({"error": f"kis_api 미설치: {exc}", "candles": []}), 500
    except Exception as exc:
        log.exception("kis minute %s", code)
        return jsonify({"error": str(exc), "candles": []}), 500


@app.route("/api/kis/orderbook/<code>")
def api_kis_orderbook(code):
    """KIS 호가 10단계. 캐시: 장중 5s, 장외 1h."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원"}), 400
    try:
        from kis_api import get_orderbook
        d = get_orderbook(code)
        if not d:
            return jsonify({"error": "API 실패", "code": code}), 502
        d["code"] = code
        return jsonify(d)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/kis/investor/<code>")
def api_kis_investor(code):
    """KIS 외국인/기관/개인 일별 순매수. 캐시: 10분."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원"}), 400
    try:
        from kis_api import get_investor_trading
        return jsonify({"code": code, "data": get_investor_trading(code) or []})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/kis/price/<code>")
def api_kis_price(code):
    """KIS 현재가 상세 (PER/PBR/52주/거래대금 등). 캐시: 장중 30s, 장외 1h."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원"}), 400
    try:
        from kis_api import get_price_detail
        d = get_price_detail(code)
        if not d:
            return jsonify({"error": "API 실패"}), 502
        d["code"] = code
        return jsonify(d)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# KBJ P4 삭제: api_sentiment — 네이버 전용·대체 없음(증권사 리포트·종목토론실 — 폐지, conflict_map §1.13·U4)


# ── 멀티 타임프레임 OHLCV ────────────────────────────────────
@app.route("/api/ohlcv/<code>")
def api_ohlcv_raw(code):
    """멀티 타임프레임 프론트 리샘플링용 raw 일봉 OHLCV.
    frontend에서 주/월 집계하므로 서버는 일봉만 리턴."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503
    days = int(request.args.get("days", 365))
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume FROM ohlcv "
                "WHERE code=? ORDER BY date DESC LIMIT ?", (code, days)
            ).fetchall()
        data = [dict(r) for r in reversed(rows)]
        return jsonify({"code": code, "days": days, "rows": data})
    except Exception as exc:
        log.exception("ohlcv raw")
        return jsonify({"error": str(exc)}), 500


# ============================================================
# 밸류체인 v2 API (3단 계층 + 반영도 점수) — Step 2
# ============================================================
_VC2_API_CACHE: dict = {}
_VC2_API_TTL = 300  # 5분


def _vc2_cache_get(key):
    item = _VC2_API_CACHE.get(key)
    if item and (time.time() - item["ts"]) < _VC2_API_TTL:
        return item["data"]
    return None


def _vc2_cache_set(key, data):
    _VC2_API_CACHE[key] = {"ts": time.time(), "data": data}


@app.route("/api/valuechain2/themes")
def api_vc2_themes():
    """모든 테마 메타정보 (이름/색상/레이어 수)."""
    cached = _vc2_cache_get("themes_all")
    if cached:
        return jsonify(cached)
    try:
        from valuechain import load_valuechain_map
    except Exception as exc:
        return jsonify({"error": f"valuechain 로드 실패: {exc}"}), 500
    data = load_valuechain_map()
    if not data:
        return jsonify({"error": "map not loaded"}), 500
    result = {
        "version": data.get("version"),
        "updated_at": data.get("updated_at"),
        "themes": [
            {
                "theme_id": tid,
                "name": t.get("name"),
                "color": t.get("color"),
                "layer_count": len((t.get("layers") or {})),
            }
            for tid, t in (data.get("themes") or {}).items()
        ],
    }
    _vc2_cache_set("themes_all", result)
    return jsonify(result)


@app.route("/api/valuechain2/layers/<theme_id>")
def api_vc2_layers(theme_id):
    """특정 테마의 레이어 + 세그먼트 요약 (점수 포함, 종목 디테일 X)."""
    cache_key = f"layers_{theme_id}"
    cached = _vc2_cache_get(cache_key)
    if cached:
        return jsonify(cached)
    try:
        from valuechain import aggregate_theme_overview
        overview = aggregate_theme_overview(theme_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    if not overview:
        return jsonify({"error": f"theme {theme_id} not found"}), 404
    _vc2_cache_set(cache_key, overview)
    return jsonify(overview)


@app.route("/api/valuechain2/segment/<theme_id>/<layer_id>/<segment_id>")
def api_vc2_segment(theme_id, layer_id, segment_id):
    """특정 세그먼트 상세 (모든 종목의 반영도 점수 포함)."""
    cache_key = f"seg_{theme_id}_{layer_id}_{segment_id}"
    cached = _vc2_cache_get(cache_key)
    if cached:
        return jsonify(cached)
    try:
        from valuechain import load_valuechain_map, aggregate_segment_score
        data = load_valuechain_map()
        seg_data = (data or {}).get("themes", {}).get(theme_id, {}).get("layers", {}).get(layer_id, {}).get("segments", {}).get(segment_id)
        if not seg_data:
            return jsonify({"error": "segment not found"}), 404
        agg = aggregate_segment_score(theme_id, layer_id, segment_id, seg_data)
        # 종목명 보강
        if _SQLITE_OK and USE_SQLITE:
            try:
                with _get_db() as conn:
                    for s in agg.get("stocks", []):
                        r = conn.execute("SELECT name FROM stocks WHERE code = ?",
                                         (s["code"],)).fetchone()
                        s["name"] = (r["name"] if r else s["code"])
            except Exception:
                pass
        result = {
            "theme_id": theme_id,
            "layer_id": layer_id,
            "segment_id": segment_id,
            "name_kr": seg_data.get("name_kr"),
            "name_en": seg_data.get("name_en"),
            "keyword_count": len(seg_data.get("keywords") or []),
            **agg,
        }
        _vc2_cache_set(cache_key, result)
        return jsonify(result)
    except Exception as exc:
        log.exception("vc2/segment")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/valuechain2/stock/<stock_code>")
def api_vc2_stock_reflection(stock_code):
    """단일 종목 반영도 + 소속 세그먼트 매칭."""
    cache_key = f"stock_{stock_code}"
    cached = _vc2_cache_get(cache_key)
    if cached:
        return jsonify(cached)
    try:
        from valuechain import (
            get_all_segments, calculate_reflection_score,
            _calculate_segment_heat_v2,
        )
        seg_heat = None
        matched: list = []
        for tid, lid, sid, seg in get_all_segments():
            if stock_code in ((seg.get("stocks_kr") or []) + (seg.get("stocks_us") or [])):
                matched.append({"theme_id": tid, "layer_id": lid,
                                "segment_id": sid, "name_kr": seg.get("name_kr")})
                if seg_heat is None:
                    seg_heat = _calculate_segment_heat_v2(seg)
        refl = calculate_reflection_score(stock_code, seg_heat)
        result = {**refl, "matched_segments": matched}
        _vc2_cache_set(cache_key, result)
        return jsonify(result)
    except Exception as exc:
        log.exception("vc2/stock")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/valuechain2/refresh", methods=["POST"])
def api_vc2_refresh():
    """캐시 강제 갱신 (개발/테스트)."""
    try:
        from valuechain import reload_valuechain_map, _REFLECTION_CACHE
        _VC2_API_CACHE.clear()
        _REFLECTION_CACHE.clear()
        reload_valuechain_map()
        return jsonify({"status": "refreshed"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/valuechain2/segment/<theme_id>/<layer_id>/<segment_id>/manage",
           methods=["POST"])
def api_vc2_segment_manage(theme_id, layer_id, segment_id):
    """세그먼트에 종목 편입/편출.
    body: {"action": "add"|"remove", "code": "...", "market": "kr"|"us"}
    """
    try:
        body = request.get_json(force=True) or {}
        action = (body.get("action") or "").strip().lower()
        code   = (body.get("code") or "").strip().upper()
        market = (body.get("market") or "").strip().lower()
        if action not in ("add", "remove"):
            return jsonify({"error": "action must be add|remove"}), 400
        if not code:
            return jsonify({"error": "code required"}), 400
        # market 자동 추정 (6자리 숫자=KR, 그 외=US)
        if market not in ("kr", "us"):
            market = "kr" if code.isdigit() and len(code) == 6 else "us"

        from valuechain import VALUECHAIN_MAP_PATH, reload_valuechain_map, _REFLECTION_CACHE
        with open(VALUECHAIN_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        seg = (data.get("themes", {}).get(theme_id, {})
                  .get("layers", {}).get(layer_id, {})
                  .get("segments", {}).get(segment_id))
        if not seg:
            return jsonify({"error": "segment not found"}), 404

        list_key = "stocks_kr" if market == "kr" else "stocks_us"
        seg.setdefault(list_key, [])

        before = list(seg[list_key])
        if action == "add":
            if code not in seg[list_key]:
                seg[list_key].append(code)
        else:  # remove
            seg[list_key] = [c for c in seg[list_key] if c != code]

        if seg[list_key] == before:
            return jsonify({"status": "noop", "message": f"{code} 이미 {action} 상태"}), 200

        # 디스크 저장 (atomic write)
        tmp = VALUECHAIN_MAP_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(VALUECHAIN_MAP_PATH)

        # 캐시 무효화
        _VC2_API_CACHE.clear()
        _REFLECTION_CACHE.clear()
        reload_valuechain_map()

        return jsonify({
            "status":  "ok",
            "action":  action,
            "code":    code,
            "market":  market,
            "stocks":  {"kr": seg.get("stocks_kr") or [],
                        "us": seg.get("stocks_us") or []},
        })
    except Exception as exc:
        log.exception("vc2/segment/manage")
        return jsonify({"error": str(exc)}), 500


# ============================================================
# 밸류체인 v2 — Step 4-3-D: TAM + 풀 정보 + 검토 비교
# ============================================================

# KBJ P4 삭제: api_vc2_stock_tam — kbj P4 종목 상세·공시(/api/stock/*·/api/filings/* — 페이지 8·5)


@app.route("/api/valuechain2/stock/<stock_code>/full")
def api_vc2_stock_full(stock_code):
    """종목 풀 정보: 반영도 + TAM + 매칭 세그먼트 + 검토 세션. Step 4-3-D."""
    cache_key = f"full_{stock_code}"
    cached = _vc2_cache_get(cache_key)
    if cached:
        return jsonify(cached)

    result = {"stock_code": stock_code}

    # 종목 기본 정보
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                row = conn.execute("SELECT name FROM stocks WHERE code = ?",
                                   (stock_code,)).fetchone()
                if row:
                    result["name"] = row["name"]
        except Exception:
            pass

    # 매칭 세그먼트 + 반영도
    try:
        from valuechain import (
            get_all_segments, calculate_reflection_score,
            _calculate_segment_heat_v2,
        )
        seg_heat = None
        matched_segments = []
        for tid, lid, sid, seg in get_all_segments():
            stocks = (seg.get("stocks_kr", []) or []) + (seg.get("stocks_us", []) or [])
            if stock_code in stocks:
                matched_segments.append({
                    "theme_id":      tid,
                    "layer_id":      lid,
                    "segment_id":    sid,
                    "name_kr":       seg.get("name_kr"),
                    "is_bottleneck": seg.get("is_bottleneck", False),
                })
                if seg_heat is None:
                    seg_heat = _calculate_segment_heat_v2(seg)
        result["matched_segments"] = matched_segments
        result["reflection"] = calculate_reflection_score(stock_code, seg_heat)
    except Exception as e:
        result["reflection_error"] = str(e)

    # TAM
    try:
        from tam_modeler import build_auto_tam_with_label
        result["tam"] = build_auto_tam_with_label(stock_code)
    except Exception as e:
        result["tam_error"] = str(e)

    # 검토 세션 (있으면)
    try:
        from review_validator import REVIEW_SESSION_ANALYSIS, compare_review_vs_auto
        if stock_code in REVIEW_SESSION_ANALYSIS:
            result["review_session"] = compare_review_vs_auto(stock_code)
    except Exception:
        pass

    _vc2_cache_set(cache_key, result)
    return jsonify(result)


# ============================================================
# Step 4-7-B: 유니버스 API
# ============================================================

# KBJ P4 삭제: api_earnings_universe — kbj consensus.snapshot(KIS — 분기 컨센서스는 출처 없음, ADR 0015)·/api/filings/earnings


# KBJ P4 삭제: api_earnings_universe_sync — kbj consensus.snapshot(KIS — 분기 컨센서스는 출처 없음, ADR 0015)·/api/filings/earnings


# ============================================================
# Step 4-7-C: 분기 컨센서스 API
# ============================================================

# KBJ P4 삭제: api_earnings_consensus_quarterly — kbj consensus.snapshot(KIS — 분기 컨센서스는 출처 없음, ADR 0015)·/api/filings/earnings


# KBJ P4 삭제: api_earnings_consensus_collect — kbj consensus.snapshot(KIS — 분기 컨센서스는 출처 없음, ADR 0015)·/api/filings/earnings


@app.route("/api/valuechain2/review/compare")
def api_vc2_review_compare():
    """검토 세션 6종목 일괄 비교. Step 4-3-D."""
    cached = _vc2_cache_get("review_compare_all")
    if cached:
        return jsonify(cached)
    try:
        from review_validator import compare_all_review_stocks
        result = {"comparisons": compare_all_review_stocks()}
    except Exception as e:
        log.exception("vc2/review/compare")
        return jsonify({"error": str(e)}), 500
    _vc2_cache_set("review_compare_all", result)
    return jsonify(result)


# ============================================================
# Step 4-4-J: analysis_journal Flask API
# ============================================================

# KBJ P8 삭제: api_analysis_journal_recent — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_by_stock — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_read — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_create — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_update — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_delete — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_export_markdown — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_import_review — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_prefill — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# KBJ P8 삭제: api_analysis_journal_stats — kbj 페이지 12 저널(/api/journal/* — 쓰기 모듈 kbj.services.personal.journal, ADR 0033) — 분석 일지


# ============================================================
# Step 4-5-1-C: 데이터 신선도 API
# ============================================================

# ============================================================
# Step 4-5-3: 검증 시트 기본 정보 API
# Step 4-5-4: 5단계 자동 prefill API
# ============================================================


def _verification_freshness(source_key: str, conn) -> dict:
    """data_freshness 라벨 + 색 + age. 실패 시 NO_DATA 폴백."""
    try:
        from data_freshness import get_freshness
        f = get_freshness(source_key, conn)
        return {
            'source': source_key,
            'label': f.label,
            'color': f.color,
            'age_human': f.age_human,
            'last_updated': f.last_updated_kst,
        }
    except Exception:
        return {
            'source': source_key, 'label': 'NO_DATA',
            'color': 'red', 'age_human': '알 수 없음', 'last_updated': None,
        }


def _classify_auto_reflection(per_pct, return_52w_pct):
    """자동 반영도 라벨링 (Step 5).
    valuechain.calculate_reflection_score 와 별도로, 검증 시트 전용 간소 판단:
      - per_pct: 5년 PER 백분위 (낮을수록 저평가)
      - return_52w_pct: 52주 수익률 %
    """
    reasons = []
    if per_pct is None:
        return {'label': 'UNKNOWN', 'reasons': ['밸류에이션 데이터 부족']}

    # PER 분위
    if per_pct <= 35:
        per_signal = 'LOW'
        reasons.append(f'Fwd PER P{int(per_pct)} (역사 하단)')
    elif per_pct <= 65:
        per_signal = 'MID'
        reasons.append(f'Fwd PER P{int(per_pct)} (역사 중앙)')
    else:
        per_signal = 'HIGH'
        reasons.append(f'Fwd PER P{int(per_pct)} (역사 상단)')

    # 52주 수익률
    if return_52w_pct is None:
        ret_signal = 'UNKNOWN'
    elif return_52w_pct < 30:
        ret_signal = 'LOW'
        reasons.append(f'52주 +{return_52w_pct:.0f}% (상승폭 30% 미만)')
    elif return_52w_pct < 100:
        ret_signal = 'MID'
        reasons.append(f'52주 +{return_52w_pct:.0f}%')
    else:
        ret_signal = 'HIGH'
        reasons.append(f'52주 +{return_52w_pct:.0f}% (강한 상승)')

    # 조합 → 라벨
    if per_signal == 'LOW' and ret_signal in ('LOW', 'UNKNOWN'):
        label = '미반영'
    elif per_signal == 'HIGH' and ret_signal == 'HIGH':
        label = '과열'
    elif per_signal == 'HIGH' or ret_signal == 'HIGH':
        label = '반영완료'
    else:
        label = '부분반영'

    return {'label': label, 'reasons': reasons,
            'per_signal': per_signal, 'return_signal': ret_signal}


def _get_verification_revisions(code: str, conn) -> dict:
    """검증 시트용 리비전 요약 (Step 5-1-E).

    종목의 revision_alerts 에서:
      - metric 별 최신 시그널 1건
      - 시그널별 카운트 (STRONG_UP / UP / NEUTRAL / DOWN / STRONG_DOWN)
      - 총 알림 수 + 최신 current_date

    검증 시트 step2 (펀더멘털) 옆에 컨센서스 리비전 컨텍스트로 표시.
    """
    rows = conn.execute("""
        SELECT metric, signal, revision_pct, window_days,
               period_type, period_year, period_quarter,
               baseline_value, current_value,
               "current_date" AS current_date, created_at
        FROM revision_alerts
        WHERE stock_code = ?
        ORDER BY created_at DESC
    """, (code,)).fetchall()

    if not rows:
        return {
            'total_alerts': 0,
            'latest_by_metric': {},
            'signal_counts': {},
            'latest_date': None,
        }

    latest_by_metric: dict = {}
    signal_counts = {'STRONG_UP': 0, 'UP': 0, 'NEUTRAL': 0,
                     'DOWN': 0, 'STRONG_DOWN': 0}
    for r in rows:
        if r['metric'] not in latest_by_metric:
            pq = r['period_quarter']
            latest_by_metric[r['metric']] = {
                'signal': r['signal'],
                'revision_pct': r['revision_pct'],
                'window_days': r['window_days'],
                'period_type': r['period_type'],
                'period_year': r['period_year'],
                'period_quarter': pq if pq else None,  # 0 sentinel → None
                'baseline_value': r['baseline_value'],
                'current_value': r['current_value'],
                'current_date': r['current_date'],
            }
        sig = r['signal']
        if sig in signal_counts:
            signal_counts[sig] += 1

    return {
        'total_alerts': len(rows),
        'latest_by_metric': latest_by_metric,
        'signal_counts': signal_counts,
        'latest_date': rows[0]['current_date'],
    }


@app.route('/api/verification/<code>/prefill', methods=['GET'])
def api_verification_prefill(code):
    """검증 시트 5단계 자동 prefill.
    build_prefill_data 어댑터 — step1/step2/step4/step5 구조로 재포장.
    step3 는 null (4-5-5 에서 사용자 수동 입력).
    부분 실패해도 errors[] 누적 + 가능한 step 표시."""
    if not code or not code.isdigit() or len(code) != 6:
        return jsonify({
            'error': '6자리 숫자 종목 코드만 허용', 'code': code,
        }), 400

    errors: list = []
    conn = sqlite3.connect(str(BASE_DIR / 'db' / 'dashboard.db'), timeout=10)
    conn.row_factory = sqlite3.Row

    try:
        # 1) build_prefill_data (이미 valuechain + analysis_journal + tam 통합)
        pf: dict = {}
        try:
            from analysis_journal_helper import build_prefill_data
            pf = build_prefill_data(code) or {}
            errors.extend(pf.get('errors', []))
        except Exception as exc:
            errors.append(f'build_prefill_data: {exc}')

        if not pf:
            return jsonify({
                'code': code,
                'error': 'prefill 데이터 산출 실패',
                'errors': errors,
            }), 500

        # 2) financial_quarterly 최근 4분기 평균 (직접 쿼리)
        financial = None
        try:
            rows = conn.execute("""
                SELECT year, quarter, revenue, operating_profit, opm
                FROM financial_quarterly
                WHERE stock_code = ? AND revenue IS NOT NULL
                ORDER BY year DESC, quarter DESC LIMIT 4
            """, (code,)).fetchall()
            if rows:
                # opm trend: 최근 2분기 vs 이전 2분기
                opms = [r['opm'] for r in rows if r['opm'] is not None]
                trend = 'flat'
                if len(opms) >= 4:
                    recent = sum(opms[:2]) / 2
                    prior = sum(opms[2:4]) / 2
                    if recent - prior >= 1.5: trend = 'up'
                    elif prior - recent >= 1.5: trend = 'down'
                avg_rev = sum(r['revenue'] for r in rows if r['revenue']) / len(rows)
                avg_opm = (sum(opms) / len(opms)) if opms else None
                financial = {
                    'avg_revenue_4q': round(avg_rev, 0) if avg_rev else None,
                    'avg_opm_4q': round(avg_opm, 2) if avg_opm is not None else None,
                    'opm_trend': trend,
                    'quarters': [
                        {'year': r['year'], 'quarter': r['quarter'],
                         'revenue': r['revenue'], 'opm': r['opm']}
                        for r in rows
                    ],
                }
        except Exception as exc:
            errors.append(f'financial_quarterly: {exc}')

        # 3) 최근 어닝 시그널 (earnings_surprise) — 최근 4분기 히스토리
        earnings = None
        earnings_history: list = []
        try:
            rows = conn.execute("""
                SELECT year, quarter, signal, priority,
                       revenue_surprise_pct, op_surprise_pct, note,
                       calculated_at
                FROM earnings_surprise
                WHERE stock_code = ?
                ORDER BY year DESC, quarter DESC LIMIT 4
            """, (code,)).fetchall()
            for r in rows:
                earnings_history.append({
                    'year': r['year'], 'quarter': r['quarter'],
                    'signal': r['signal'], 'priority': r['priority'],
                    'revenue_surprise_pct': r['revenue_surprise_pct'],
                    'op_surprise_pct': r['op_surprise_pct'],
                    'note': r['note'],
                    'calculated_at': r['calculated_at'],
                })
            if earnings_history:
                # 가장 최근 1건을 단일 필드에도 유지 (기존 코드 호환)
                earnings = earnings_history[0]
        except Exception as exc:
            errors.append(f'earnings_surprise: {exc}')

        # 4) OHLCV 52주 high/low/현재가 + 수익률
        price_position = None
        returns = None
        try:
            row = conn.execute("""
                SELECT MIN(close) AS low, MAX(close) AS high,
                       (SELECT close FROM ohlcv WHERE code=? ORDER BY date DESC LIMIT 1) AS current
                FROM ohlcv
                WHERE code = ? AND date >= date('now', '-365 days')
            """, (code, code)).fetchone()
            if row and row['low'] and row['high'] and row['current']:
                lo, hi, cur = row['low'], row['high'], row['current']
                pos_pct = round((cur - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0
                price_position = {
                    'week52_low': lo,
                    'week52_high': hi,
                    'current': cur,
                    'position_pct': pos_pct,
                }
            # 수익률 (52w / 6m / 3m)
            ret_rows = conn.execute("""
                SELECT date, close FROM ohlcv WHERE code=?
                ORDER BY date DESC LIMIT 250
            """, (code,)).fetchall()
            if ret_rows:
                today_close = ret_rows[0]['close']
                def _ret(days_back):
                    if days_back >= len(ret_rows): return None
                    past = ret_rows[days_back]['close']
                    if not past: return None
                    return round((today_close - past) / past * 100, 2)
                returns = {
                    'return_52w': _ret(min(249, len(ret_rows) - 1)),
                    'return_6m':  _ret(min(125, len(ret_rows) - 1)),
                    'return_3m':  _ret(min(60, len(ret_rows) - 1)),
                }
        except Exception as exc:
            errors.append(f'ohlcv: {exc}')

        # 5) 자동 반영도 분류
        auto_reflection = _classify_auto_reflection(
            pf.get('fwd_per_band_pct'),
            pf.get('return_52w'),
        )

        # 6) 검토 match
        review_session = pf.get('review_session')
        review_match = None
        if review_session and pf.get('reflection_label'):
            review_match = {
                'review_conclusion': review_session.get('conclusion'),
                'auto_label': pf.get('reflection_label'),
                'thesis': review_session.get('thesis'),
                'journal_id': None,  # analysis_journal id (없으면 None)
            }

        # 7) 신선도 라벨 attach
        fr_naver = _verification_freshness('naver_price', conn)
        fr_finq  = _verification_freshness('financial_quarterly', conn)
        fr_band  = _verification_freshness('valuation_band', conn)
        fr_earn  = _verification_freshness('earnings_pipeline', conn)
        fr_ohlcv = _verification_freshness('ohlcv_kr', conn)

        # 8) Step 정형화
        step1 = {
            'valuechain': {
                'theme_id': pf.get('theme_id'),
                'layer_id': pf.get('layer_id'),
                'segment_id': pf.get('segment_id'),
                'segments': pf.get('matched_segments') or [],
                'is_bottleneck': bool(pf.get('is_bottleneck')),
                'freshness': {'source': 'valuechain_map',
                              'label': 'ARCHIVE', 'color': 'gray',
                              'age_human': '정적 데이터', 'last_updated': None},
            },
            'review_analysis': {
                'thesis': review_session.get('thesis') if review_session else None,
                'conclusion': review_session.get('conclusion') if review_session else None,
                'priority': review_session.get('priority') if review_session else None,
                'tags': review_session.get('tags') if review_session else [],
                'session_date': review_session.get('session_date') if review_session else None,
                'has_manual_tp': bool(review_session.get('has_manual_tp')) if review_session else False,
                'freshness': {'source': 'review_session',
                              'label': 'MANUAL', 'color': 'blue',
                              'age_human': '수동 입력', 'last_updated': None},
            } if review_session else None,
        }

        # Step 5-1-E: 컨센서스 리비전 요약 (펀더멘털 컨텍스트)
        try:
            revisions = _get_verification_revisions(code, conn)
        except Exception as exc:
            log.warning("[verification] revisions fetch fail %s: %s", code, exc)
            revisions = {'total_alerts': 0, 'latest_by_metric': {},
                         'signal_counts': {}, 'latest_date': None,
                         'error': str(exc)[:100]}

        step2 = {
            'financial': {**(financial or {}),
                          'freshness': fr_finq} if financial else None,
            'valuation': {
                'fwd_per': pf.get('fwd_per'),
                'fwd_per_band_pct': pf.get('fwd_per_band_pct'),
                'opm_estimate': pf.get('opm_estimate'),
                'opm_source': pf.get('opm_source'),
                'per_band': pf.get('_per_band'),
                'opm_range': pf.get('_opm_range'),
                'freshness': fr_band,
            },
            'earnings': {**earnings, 'freshness': fr_earn} if earnings else None,
            'earnings_history': earnings_history,  # 4-5-8: 최근 4분기 시그널 배열
            'revisions': revisions,  # 5-1-E: 컨센서스 리비전 요약
        }

        step3 = None  # 4-5-5 에서 채움

        step4 = {
            'tam': {
                'bear': {
                    'eps': pf.get('bear_eps'),
                    'eps_source': pf.get('bear_eps_source'),
                    'per': pf.get('bear_per'),
                    'per_source': pf.get('bear_per_source'),
                    'tp': pf.get('bear_tp'),
                },
                'base': {
                    'eps': pf.get('base_eps'),
                    'eps_source': pf.get('base_eps_source'),
                    'per': pf.get('base_per'),
                    'per_source': pf.get('base_per_source'),
                    'tp': pf.get('base_tp'),
                },
                'bull': {
                    'eps': pf.get('bull_eps'),
                    'eps_source': pf.get('bull_eps_source'),
                    'per': pf.get('bull_per'),
                    'per_source': pf.get('bull_per_source'),
                    'tp': pf.get('bull_tp'),
                },
                'current_price': pf.get('current_price'),
                'method': pf.get('_tam_method'),
                'consensus_tp': pf.get('_consensus_tp'),
                'freshness': fr_band,  # TAM 은 valuation_band+consensus 위에서 산출
            },
        }

        step5 = {
            'price_position': {**(price_position or {}),
                               'freshness': fr_ohlcv} if price_position else None,
            'returns': returns,
            'reflection': {
                'auto_label': auto_reflection['label'],
                'reasons': auto_reflection['reasons'],
                'per_signal': auto_reflection.get('per_signal'),
                'return_signal': auto_reflection.get('return_signal'),
                # valuechain.calculate_reflection_score 결과도 병기
                'vc_score': pf.get('reflection_score'),
                'vc_label': pf.get('reflection_label'),
            },
            'review_match': review_match,
        }

        # TP 별 upside%
        cur_price = pf.get('current_price') or 0
        if cur_price:
            for k in ('bear', 'base', 'bull'):
                tp = step4['tam'][k].get('tp')
                if tp:
                    step4['tam'][k]['upside_pct'] = round((tp - cur_price) / cur_price * 100, 1)

        return jsonify({
            'code': code,
            'name': pf.get('stock_name') or code,
            'analysis_date': pf.get('analysis_date'),
            'current_price': pf.get('current_price'),
            'return_52w': pf.get('return_52w'),
            'step1': step1,
            'step2': step2,
            'step3': step3,
            'step4': step4,
            'step5': step5,
            'data_sources': pf.get('data_sources', []),
            'errors': errors,
        })
    except Exception as exc:
        log.exception('verification/prefill')
        return jsonify({
            'code': code,
            'error': f'서버 오류: {exc}',
            'errors': errors,
        }), 500
    finally:
        conn.close()


# ============================================================
# Step 4-5-7: 종합 신호 패널 API
# ============================================================

# 어닝 시그널 → 점수 매핑
_EARN_SIGNAL_SCORE = {
    'BEAT_BIG': 90, 'BEAT': 75,
    'TURNAROUND_FULL': 88, 'TURNAROUND_PARTIAL': 70,
    'REVENUE_BEAT_OP_INLINE': 60, 'REVENUE_MISS_OP_BEAT': 70,
    'REVENUE_BEAT': 60, 'REVENUE_MISS': 35,
    'INLINE': 50,
    'YOY_INLINE': 50, 'YOY_SURGE': 75, 'YOY_PLUNGE': 25,
    'YOY_TURNAROUND': 80, 'YOY_SHOCK': 15,
    'MISS': 25, 'MISS_BIG': 10,
    'SHOCK_FULL': 5,
}

# 검토 결론 × 우선순위 → 점수
_REVIEW_SCORE = {
    'BUY':   {'★★★': 90, '★★': 75, '★': 60, '—': 50, None: 60},
    'HOLD':  {'★★★': 50, '★★': 45, '★': 40, '—': 40, None: 45},
    'WATCH': {'★★★': 40, '★★': 35, '★': 30, '—': 30, None: 35},
    'SELL':  {'★★★': 15, '★★': 20, '★': 25, '—': 30, None: 22},
}


def _score_valuation(fwd_per_band_pct):
    """5년 PER 백분위 → 점수 (낮을수록 좋음 = 저평가)."""
    if fwd_per_band_pct is None:
        return None
    if fwd_per_band_pct <= 25: return 80
    if fwd_per_band_pct <= 50: return 60
    if fwd_per_band_pct <= 75: return 40
    return 20


def _score_earnings(signal):
    if not signal:
        return None
    return _EARN_SIGNAL_SCORE.get(signal, 50)


def _score_technical(position_pct):
    """52주 위치 → 점수 (저점일수록 좋음, contrarian view)."""
    if position_pct is None:
        return None
    if position_pct < 30: return 70
    if position_pct < 70: return 50
    return 30


def _score_review(conclusion, priority):
    if not conclusion:
        return None
    row = _REVIEW_SCORE.get(conclusion)
    if not row:
        return 50
    return row.get(priority, row.get(None, 50))


def _stars_from_score(s):
    if s is None: return '—'
    if s >= 81: return '★★★ BUY'
    if s >= 61: return '★★★ BUY-'
    if s >= 41: return '★★ HOLD'
    if s >= 21: return '★★ HOLD-'
    return '★ SELL/WATCH'


def _rec_from_score(s):
    if s is None: return None
    if s >= 81: return 'BUY'
    if s >= 61: return 'BUY-'
    if s >= 41: return 'HOLD'
    if s >= 21: return 'HOLD-'
    return 'SELL/WATCH'


def _confidence_label(v):
    if v >= 0.75: return '높음'
    if v >= 0.5:  return '중간'
    return '낮음'


def _composite_freshness_factors(conn) -> tuple[float, list]:
    """4개 핵심 소스 신선도 → 감점 + 요인."""
    from data_freshness import get_freshness, DATA_SOURCE_CONFIG
    SOURCES = ['naver_price', 'valuation_band', 'consensus_quarterly',
               'earnings_pipeline']
    penalty = 0.0
    factors = []
    for src in SOURCES:
        if src not in DATA_SOURCE_CONFIG:
            continue
        try:
            f = get_freshness(src, conn)
        except Exception:
            continue
        name = f.name_kr
        if f.label == 'ARCHIVE':
            penalty += 0.10
            factors.append({
                'source': src, 'name_kr': name,
                'label': f.label, 'age_human': f.age_human,
                'impact': -0.10,
            })
        elif f.label == 'DELAY':
            penalty += 0.05
            factors.append({
                'source': src, 'name_kr': name,
                'label': f.label, 'age_human': f.age_human,
                'impact': -0.05,
            })
        elif f.label == 'NO_DATA':
            penalty += 0.15
            factors.append({
                'source': src, 'name_kr': name,
                'label': f.label, 'age_human': f.age_human,
                'impact': -0.15,
            })
    return penalty, factors


def _next_quarter_label(latest_year, latest_quarter):
    """ '2026Q1' → 다음 분기 라벨 + 발표 추정일 (KR 분기 말 + 45일)."""
    if not latest_year or not latest_quarter:
        return None, None
    nq = latest_quarter + 1
    ny = latest_year
    if nq > 4:
        nq = 1
        ny += 1
    # KR 분기 말 + 45일 (대략적 발표 시점)
    quarter_end_month = {1: 3, 2: 6, 3: 9, 4: 12}[nq]
    from datetime import date
    try:
        end = date(ny, quarter_end_month, 28)
        from datetime import timedelta
        est = end + timedelta(days=45)
        return f'{ny}Q{nq}', est.isoformat()
    except Exception:
        return f'{ny}Q{nq}', None


@app.route('/api/verification/<code>/composite', methods=['GET'])
def api_verification_composite(code):
    """종합 신호 패널.
    점수(0~100) = 자동 50% + 검토 30% + 갭 페널티 20%
    신뢰도(0~1) = 1.0 - 데이터 신선도/완전성 감점
    Returns: { code, name, current_price,
               score: { composite, stars, recommendation, breakdown },
               confidence: { value, label, factors },
               actions: [...], monitoring: [...],
               errors: [...] }
    """
    if not code or not code.isdigit() or len(code) != 6:
        return jsonify({'error': '6자리 숫자 종목 코드만 허용', 'code': code}), 400

    errors: list = []
    conn = sqlite3.connect(str(BASE_DIR / 'db' / 'dashboard.db'), timeout=10)
    conn.row_factory = sqlite3.Row

    try:
        # 1) prefill 데이터
        try:
            from analysis_journal_helper import build_prefill_data
            pf = build_prefill_data(code) or {}
            errors.extend(pf.get('errors', []))
        except Exception as exc:
            return jsonify({
                'code': code, 'error': f'prefill 실패: {exc}',
                'errors': errors,
            }), 500

        # 2) 최신 검토 일지
        review_journal = None
        try:
            from analysis_journal_api import list_journals_by_stock
            journals = list_journals_by_stock(code, 1) or []
            if journals:
                review_journal = journals[0]
        except Exception as exc:
            errors.append(f'journal: {exc}')

        # 3) 최근 어닝 시그널 (직접 쿼리)
        earnings_signal = None
        earnings_year = None
        earnings_quarter = None
        try:
            row = conn.execute("""
                SELECT year, quarter, signal, priority
                FROM earnings_surprise
                WHERE stock_code = ?
                ORDER BY year DESC, quarter DESC LIMIT 1
            """, (code,)).fetchone()
            if row:
                earnings_signal = row['signal']
                earnings_year = row['year']
                earnings_quarter = row['quarter']
        except Exception as exc:
            errors.append(f'earnings_surprise: {exc}')

        # 4) 52주 위치
        position_pct = None
        try:
            r = conn.execute("""
                SELECT MIN(close) AS low, MAX(close) AS high,
                       (SELECT close FROM ohlcv WHERE code=? ORDER BY date DESC LIMIT 1) AS cur
                FROM ohlcv
                WHERE code = ? AND date >= date('now', '-365 days')
            """, (code, code)).fetchone()
            if r and r['low'] and r['high'] and r['cur'] and r['high'] > r['low']:
                position_pct = round((r['cur'] - r['low']) / (r['high'] - r['low']) * 100, 1)
        except Exception as exc:
            errors.append(f'ohlcv: {exc}')

        # 5) 점수 계산
        fwd_per_band_pct = pf.get('fwd_per_band_pct')
        val_score = _score_valuation(fwd_per_band_pct)
        earn_score = _score_earnings(earnings_signal)
        tech_score = _score_technical(position_pct)

        # 자동 = 사용 가능한 축 평균
        auto_components = {
            'valuation': val_score, 'earnings': earn_score, 'technical': tech_score,
        }
        auto_used = [v for v in auto_components.values() if v is not None]
        auto_score = round(sum(auto_used) / len(auto_used), 1) if auto_used else None

        # 검토
        review_score = None
        if review_journal:
            review_score = _score_review(
                review_journal.get('conclusion'),
                review_journal.get('priority'),
            )

        # 갭 페널티: gap-analysis 재호출 대신 인라인 계산 (간단)
        gap_penalty = 0
        gap_high_count = 0
        if review_journal:
            for sc in ('bear', 'base', 'bull'):
                a = pf.get(f'{sc}_tp')
                k = review_journal.get(f'{sc}_tp')
                if a and k:
                    diff_pct = abs((k - a) / a * 100)
                    if diff_pct >= 25:
                        gap_high_count += 1
                        gap_penalty += 5  # 1건당 -5

        # 합성
        if auto_score is None:
            composite = None
        elif review_score is not None:
            composite = round(auto_score * 0.5 + review_score * 0.3 - gap_penalty, 1)
        else:
            composite = round(auto_score - gap_penalty, 1)
        if composite is not None:
            composite = max(0.0, min(100.0, composite))

        # 6) 신뢰도
        confidence_value = 1.0
        confidence_factors = []
        pen, ff = _composite_freshness_factors(conn)
        confidence_value -= pen
        confidence_factors.extend(ff)
        if not review_journal:
            confidence_value -= 0.10
            confidence_factors.append({
                'source': 'review_journal',
                'name_kr': '검토 분석 일지',
                'label': 'MISSING', 'age_human': '미작성',
                'impact': -0.10,
            })
        if not earnings_signal:
            confidence_value -= 0.05
            confidence_factors.append({
                'source': 'earnings_surprise',
                'name_kr': '최근 어닝 시그널',
                'label': 'MISSING', 'age_human': '없음',
                'impact': -0.05,
            })
        confidence_value = round(max(0.0, min(1.0, confidence_value)), 2)

        # 7) 다음 액션
        actions: list = []
        if not review_journal:
            actions.append({
                'priority': 1,
                'text': '검토 분석 일지 작성 — 결론/TP/Step3 입력',
                'reason': '자동 산출만으로 판단 불가',
            })
        if gap_high_count >= 1:
            actions.append({
                'priority': 1,
                'text': f'자동 TAM vs 검토 TP 갭 검토 (high {gap_high_count}건)',
                'reason': '시나리오별 가정 차이 큼',
            })
        if earnings_signal and 'BIG' in earnings_signal:
            actions.append({
                'priority': 2,
                'text': f'최근 어닝 {earnings_signal} 영향 재평가',
                'reason': f'{earnings_year}Q{earnings_quarter} 강한 시그널',
            })
        # 데이터 신선도 경고
        if any(f.get('label') == 'ARCHIVE' for f in confidence_factors):
            actions.append({
                'priority': 3,
                'text': '핵심 데이터 ARCHIVE — 운영 점검 페이지 확인',
                'reason': 'naver_price/valuation_band/consensus 갱신 필요',
            })
        # 반영도 vs 결론 conflict (gap_analysis 일치성과 동일 룰)
        if review_journal:
            auto_refl = pf.get('reflection_label')
            review_concl = review_journal.get('conclusion')
            check = _check_reflection_conclusion(auto_refl, review_concl)
            if check and check[0] == 'conflict':
                actions.append({
                    'priority': 1,
                    'text': f'반영도({auto_refl}) ↔ 결론({review_concl}) 불일치 검토',
                    'reason': check[1],
                })
        actions.sort(key=lambda a: a['priority'])

        # 8) 모니터링 일정 (어닝 추정 + 검토 analysis_date 기반)
        monitoring: list = []
        if earnings_year is not None:
            nq_label, nq_date = _next_quarter_label(earnings_year, earnings_quarter)
            if nq_label:
                monitoring.append({
                    'when': nq_date,
                    'event': f'{nq_label} 어닝 (추정)',
                    'kind': 'earnings_estimate',
                })
        if review_journal and review_journal.get('analysis_date'):
            # 분석일 + 30일 → 재검토 알림
            from datetime import datetime, timedelta
            try:
                d = datetime.strptime(review_journal['analysis_date'], '%Y-%m-%d').date()
                review_date = (d + timedelta(days=30)).isoformat()
                monitoring.append({
                    'when': review_date,
                    'event': '검토 분석 30일 재검토',
                    'kind': 'review_review',
                })
            except Exception:
                pass

        return jsonify({
            'code': code,
            'name': pf.get('stock_name') or code,
            'current_price': pf.get('current_price'),
            'score': {
                'composite': composite,
                'stars': _stars_from_score(composite),
                'recommendation': _rec_from_score(composite),
                'breakdown': {
                    'auto': auto_score,
                    'auto_components': auto_components,
                    'review': review_score,
                    'gap_penalty': gap_penalty,
                    'gap_high_count': gap_high_count,
                    'has_review': bool(review_journal),
                },
            },
            'confidence': {
                'value': confidence_value,
                'label': _confidence_label(confidence_value),
                'factors': confidence_factors,
            },
            'actions': actions[:5],
            'monitoring': monitoring,
            'context': {
                'fwd_per_band_pct': fwd_per_band_pct,
                'position_pct': position_pct,
                'earnings_signal': earnings_signal,
                'earnings_year': earnings_year,
                'earnings_quarter': earnings_quarter,
                'review_conclusion': review_journal.get('conclusion') if review_journal else None,
                'review_priority': review_journal.get('priority') if review_journal else None,
                'reflection_label': pf.get('reflection_label'),
            },
            'errors': errors,
        })
    except Exception as exc:
        log.exception('verification/composite')
        return jsonify({
            'code': code, 'error': f'서버 오류: {exc}',
            'errors': errors,
        }), 500
    finally:
        conn.close()


# ============================================================
# Step 4-5-6: 자동 vs 검토 Split View — 갭 분석 API
# ============================================================

# 임계값 (Q5 = A: ±10% / ±25%)
_GAP_THRESH_MEDIUM = 10.0
_GAP_THRESH_HIGH = 25.0


def _gap_severity(pct: float) -> str:
    if pct is None:
        return 'unknown'
    a = abs(pct)
    if a >= _GAP_THRESH_HIGH: return 'high'
    if a >= _GAP_THRESH_MEDIUM: return 'medium'
    return 'low'


def _gap_direction(diff_pct: float, label: str = 'TP') -> str:
    """gap_pct (검토 - auto) / auto * 100 기준."""
    if diff_pct is None:
        return '데이터 부족'
    if diff_pct < -3:
        return f'분석가가 보수적 ({label} 낮음)'
    if diff_pct > 3:
        return f'분석가가 공격적 ({label} 높음)'
    return '대체로 일치'


# 자동 반영도 × 검토 결론 일치성 매트릭스
_REFL_CONCL_CONFLICTS = {
    ('과열', 'BUY'):    '시장은 과열로 보지만 분석가는 BUY — 갭 큼',
    ('과열', 'WATCH'):  '시장은 과열, 분석가는 관망 — 부분 일치',
    ('미반영', 'SELL'): '시장은 저평가로 보지만 분석가는 SELL — 갭 큼',
    ('미반영', 'HOLD'): '시장은 저평가, 분석가는 HOLD — 보수적 차이',
    ('반영완료', 'BUY'): '시장은 반영 완료라 보지만 분석가는 BUY — 신중 검토',
}
_REFL_CONCL_AGREEMENTS = {
    ('미반영', 'BUY'),
    ('과열', 'SELL'),
    ('과열', 'HOLD'),
    ('부분반영', 'HOLD'),
    ('반영완료', 'HOLD'),
    ('반영완료', 'SELL'),
}


def _check_reflection_conclusion(reflection: str, conclusion: str):
    """반영도-결론 일치성. Returns:
        ('match' / 'partial' / 'conflict', message) or None
    """
    if not reflection or not conclusion:
        return None
    key = (reflection, conclusion)
    if key in _REFL_CONCL_CONFLICTS:
        return ('conflict', _REFL_CONCL_CONFLICTS[key])
    if key in _REFL_CONCL_AGREEMENTS:
        return ('match', '반영도와 결론이 합리적으로 일치')
    return ('partial', f'반영도 "{reflection}" + 결론 "{conclusion}" 명확한 룰 없음')


def _build_gap_for_tp(scenario: str, auto_tp, review_tp):
    """단일 시나리오 TP 갭. auto_tp 가 0 또는 None 이면 None 반환."""
    if auto_tp is None or review_tp is None:
        return None
    if not auto_tp:
        return None
    diff_abs = review_tp - auto_tp
    diff_pct = round(diff_abs / auto_tp * 100, 1)
    sev = _gap_severity(diff_pct)
    return {
        'metric': f'{scenario}_tp',
        'auto': auto_tp,
        'review': review_tp,
        'diff_abs': round(diff_abs, 0),
        'diff_pct': diff_pct,
        'severity': sev,
        'direction': _gap_direction(diff_pct, f'{scenario.title()} TP'),
    }


@app.route('/api/verification/<code>/gap-analysis', methods=['GET'])
def api_verification_gap_analysis(code):
    """자동 vs 검토 갭 분석.
    Returns:
      { code, name, current_price,
        auto:   {base_tp, bear_tp, bull_tp, reflection_label, reasons},
        review:  {base_tp, bear_tp, bull_tp, conclusion, priority,
                 thesis, journal_id, updated_at} | null,
        gaps:   [ {metric, auto, review, diff_abs, diff_pct, severity, direction}, ... ],
        consistency: {label, message} | null,
        action: {stars, label, rationale, checklist[]},
        errors: [...] }
    """
    if not code or not code.isdigit() or len(code) != 6:
        return jsonify({'error': '6자리 숫자 종목 코드만 허용', 'code': code}), 400

    errors: list = []

    # 1) 자동 산출 — prefill 재호출 대신 내부에서 직접 build (한 번 더 함수 호출)
    try:
        from analysis_journal_helper import build_prefill_data
        pf = build_prefill_data(code) or {}
        errors.extend(pf.get('errors', []))
    except Exception as exc:
        return jsonify({
            'code': code, 'error': f'prefill 실패: {exc}',
            'errors': errors,
        }), 500

    auto = {
        'name': pf.get('stock_name') or code,
        'base_tp': pf.get('base_tp'),
        'bear_tp': pf.get('bear_tp'),
        'bull_tp': pf.get('bull_tp'),
        'base_eps': pf.get('base_eps'),
        'base_per': pf.get('base_per'),
        'reflection_label': pf.get('reflection_label'),
        'reflection_score': pf.get('reflection_score'),
        'current_price': pf.get('current_price'),
        'method': pf.get('_tam_method'),
    }
    # reflection reasons: 4-5-4의 _classify_auto_reflection 호출 (재사용)
    auto_class = _classify_auto_reflection(
        pf.get('fwd_per_band_pct'), pf.get('return_52w'))
    auto['reflection_reasons'] = auto_class.get('reasons', [])
    auto['reflection_auto'] = auto_class.get('label')  # 검증 시트 전용 분류

    # 2) 검토 — 최신 일지
    review = None
    try:
        from analysis_journal_api import list_journals_by_stock
        journals = list_journals_by_stock(code, 1) or []
        if journals:
            j = journals[0]
            review = {
                'journal_id': j.get('id'),
                'analyst': j.get('analyst'),
                'analysis_date': j.get('analysis_date'),
                'updated_at': j.get('updated_at'),
                'bear_tp': j.get('bear_tp'),
                'base_tp': j.get('base_tp'),
                'bull_tp': j.get('bull_tp'),
                'conclusion': j.get('conclusion'),
                'priority': j.get('priority'),
                'thesis': j.get('thesis'),
                'memo': j.get('memo'),
                'tags': j.get('tags') or [],
            }
    except Exception as exc:
        errors.append(f'journal/stock: {exc}')

    # 3) TP 갭 (3 시나리오)
    gaps: list = []
    if review:
        for sc in ('bear', 'base', 'bull'):
            g = _build_gap_for_tp(sc, auto.get(f'{sc}_tp'), review.get(f'{sc}_tp'))
            if g:
                gaps.append(g)

    # 4) 반영도 vs 결론 일치성
    consistency = None
    if review:
        result = _check_reflection_conclusion(
            auto.get('reflection_auto') or auto.get('reflection_label'),
            review.get('conclusion'),
        )
        if result:
            consistency = {
                'label': result[0],   # match / partial / conflict
                'message': result[1],
                'auto_reflection': auto.get('reflection_auto') or auto.get('reflection_label'),
                'review_conclusion': review.get('conclusion'),
            }

    # 5) 추천 액션 (Q4=A: 별점 + 한 줄)
    high_count = sum(1 for g in gaps if g['severity'] == 'high')
    med_count = sum(1 for g in gaps if g['severity'] == 'medium')
    conflict = consistency and consistency['label'] == 'conflict'

    if not review:
        action = {
            'stars': '—',
            'label': '검토 일지 없음',
            'rationale': '자동 산출만 사용 가능',
            'checklist': ['검토 분석 일지 작성 (Step 3 / TP / 결론)'],
        }
    elif high_count >= 2 or (high_count >= 1 and conflict):
        action = {
            'stars': '★★★',
            'label': '재검토 필요',
            'rationale': '자동과 검토 판단이 크게 다름',
            'checklist': [
                '자동 TAM 가정 검토 (PER 백분위 적정성)',
                '검토 thesis 재확인 — 최근 컨센서스 변화 반영했는지',
                '결론(BUY/HOLD/SELL/WATCH) 재고',
            ],
        }
    elif high_count >= 1 or (med_count >= 2 and conflict):
        action = {
            'stars': '★★',
            'label': '확인 권장',
            'rationale': '일부 시나리오에서 차이 큼',
            'checklist': [
                'Bull/Bear 시나리오 가정 검토',
                '반영도 자동 분류 vs 검토 결론 갭 확인',
            ],
        }
    elif med_count >= 1 or conflict:
        action = {
            'stars': '★',
            'label': '경미한 차이',
            'rationale': '대체로 일치하나 부분 차이 있음',
            'checklist': ['갭이 큰 시나리오만 점검'],
        }
    else:
        action = {
            'stars': '✓',
            'label': '정상',
            'rationale': '자동과 검토 판단이 잘 일치',
            'checklist': [],
        }

    return jsonify({
        'code': code,
        'name': auto.get('name'),
        'current_price': auto.get('current_price'),
        'auto': auto,
        'review': review,
        'gaps': gaps,
        'consistency': consistency,
        'action': action,
        'thresholds': {
            'medium_pct': _GAP_THRESH_MEDIUM,
            'high_pct': _GAP_THRESH_HIGH,
        },
        'errors': errors,
    })


@app.route('/api/verification/stock/<code>', methods=['GET'])
def api_verification_stock(code):
    """검증 시트용 종목 기본 정보.
    Returns: { found, code, name, market, sector, sectors,
               current_price, change_pct, change_amount, volume_mn,
               market_cap, week52_low, week52_high, week52_days,
               price_meta: { source, last_updated, freshness_label, ... } }
    """
    if not code or not code.isdigit() or len(code) != 6:
        return jsonify({
            'found': False,
            'error': '6자리 숫자 종목 코드만 허용',
            'code': code,
        }), 400

    conn = sqlite3.connect(str(BASE_DIR / 'db' / 'dashboard.db'), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # 1. stocks 테이블 — 기본 정보
        row = conn.execute("""
            SELECT code, name, market, sector, sectors_json,
                   close, change_pct, volume_mn, updated_at
            FROM stocks
            WHERE code = ?
        """, (code,)).fetchone()

        if not row:
            return jsonify({
                'found': False,
                'error': f'종목 코드 {code} 미존재',
                'code': code,
            }), 404

        # 2. 52주 high/low (ohlcv)
        week52 = conn.execute("""
            SELECT MIN(close) AS low, MAX(close) AS high, COUNT(*) AS days
            FROM ohlcv
            WHERE code = ? AND date >= date('now', '-365 days')
        """, (code,)).fetchone()

        # 3. 시가총액 (naver_universe 캐시; 현재 미수록이지만 후속 phase 대비 코드 유지)
        market_cap = None
        try:
            naver_files = sorted((BASE_DIR / 'cache').glob('naver_universe_*.json'),
                                 reverse=True)
            if naver_files:
                with open(naver_files[0], 'r', encoding='utf-8') as f:
                    naver_data = json.load(f)
                stocks_dict = naver_data.get('stocks') or {}
                if isinstance(stocks_dict, dict) and code in stocks_dict:
                    rec = stocks_dict[code]
                    market_cap = rec.get('mcap') or rec.get('market_cap')
        except Exception:
            pass

        # 4. 신선도 (4-5-1)
        from data_freshness import get_freshness
        is_kr = (not row['market'])  # '' / None → KR
        freshness_source = 'naver_price' if is_kr else 'stocks_us_price'
        try:
            fresh = get_freshness(freshness_source, conn)
            price_meta = {
                'source': freshness_source,
                'last_updated': row['updated_at'],
                'freshness_label': fresh.label,
                'freshness_color': fresh.color,
                'age_human': fresh.age_human,
            }
        except Exception:
            price_meta = {
                'source': freshness_source,
                'last_updated': row['updated_at'],
                'freshness_label': 'NO_DATA',
                'freshness_color': 'red',
                'age_human': '알 수 없음',
            }

        # 5. 시장 표기 — KOSPI/KOSDAQ 구분 데이터 없음 → 'KR' 또는 'US'
        market_label = 'US' if not is_kr else 'KR'

        # 6. 변동액 계산
        close = row['close'] or 0
        change_pct = row['change_pct'] or 0
        change_amount = 0
        if close and (100 + change_pct) != 0:
            change_amount = int(close * change_pct / (100 + change_pct))

        # 7. 섹터
        sector = row['sector'] or ''
        sectors_list: list = []
        if row['sectors_json']:
            try:
                sectors_list = json.loads(row['sectors_json'])
            except Exception:
                sectors_list = [sector] if sector else []

        return jsonify({
            'found': True,
            'code': code,
            'name': row['name'],
            'market': market_label,
            'sector': sector,
            'sectors': sectors_list,
            'current_price': close,
            'change_pct': change_pct,
            'change_amount': change_amount,
            'volume_mn': row['volume_mn'],
            'market_cap': market_cap,
            'week52_low': week52['low'] if week52 else None,
            'week52_high': week52['high'] if week52 else None,
            'week52_days': week52['days'] if week52 else 0,
            'price_meta': price_meta,
        })
    except Exception as exc:
        log.exception('verification/stock')
        return jsonify({
            'found': False,
            'error': f'서버 오류: {exc}',
            'code': code,
        }), 500
    finally:
        conn.close()


@app.route("/api/freshness/source/<source_key>", methods=["GET"])
def api_freshness_source(source_key):
    """단일 소스 신선도 조회. 404 = 등록되지 않은 소스."""
    try:
        from data_freshness import get_freshness, DATA_SOURCE_CONFIG
        if source_key not in DATA_SOURCE_CONFIG:
            return jsonify({
                "error": f"등록되지 않은 소스: {source_key}",
                "available_sources": list(DATA_SOURCE_CONFIG.keys()),
            }), 404
        return jsonify(get_freshness(source_key).to_dict())
    except Exception as exc:
        log.exception("freshness/source")
        return jsonify({"error": str(exc), "source": source_key}), 500


@app.route("/api/freshness/all", methods=["GET"])
def api_freshness_all():
    """전체 소스 신선도 + 요약 통계.
    Query: category=<key>, issues_only=true
    """
    try:
        from data_freshness import get_all_sources_status, get_freshness_summary
        category = request.args.get("category")
        issues_only = (request.args.get("issues_only", "false").lower() == "true")
        all_results = get_all_sources_status()
        filtered = all_results
        if category:
            filtered = [r for r in filtered if r.category == category]
        if issues_only:
            filtered = [
                r for r in filtered
                if r.label in ("DELAY", "ARCHIVE", "NO_DATA")
                and r.category != "manual"
            ]
        return jsonify({
            "sources": [r.to_dict() for r in filtered],
            "summary": get_freshness_summary(),
            "filters_applied": {
                "category": category,
                "issues_only": issues_only,
            },
        })
    except Exception as exc:
        log.exception("freshness/all")
        return jsonify({"error": str(exc)}), 500


# ============================================================
# Step 4-5-2-B: 시장 컨텍스트 API
# ============================================================

# 한국 거래소 휴장일 — KBJ P2: 2026 하드코딩(_KR_HOLIDAYS_2026)을 지우고 KBJ 정본 캘린더로
# (XKRX + config/holidays_override.yaml). 2026년에 다섯 날이 달랐다(설계 §7.3 — 05-01·06-03·
# 08-17·10-05 누락, 09-28 오기).
def _is_kr_holiday(dt: datetime) -> bool:
    """주말 또는 KRX 휴장일이면 True. naive 시각은 KST 벽시계로 읽는다."""
    return _kbj_is_kr_holiday(dt if dt.tzinfo is not None else dt.replace(tzinfo=KST))


def _next_trading_open_kst(now: datetime) -> datetime:
    """now 이후 가장 가까운 거래일 정규장 시작 KST 시각(보통 09:00, 지연 개장일은 그 시각)."""
    return _kbj_next_trading_open(now if now.tzinfo is not None else now.replace(tzinfo=KST))


def _humanize_duration(seconds: int) -> str:
    """초 → '2시간 18분' / '11h 25m'."""
    if seconds < 0:
        seconds = 0
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60
    if days >= 1:
        return f"{days}일 {hours}시간"
    if hours >= 1:
        return f"{hours}시간 {mins}분"
    return f"{mins}분"


def _market_state_kst(now: datetime) -> tuple[str, str]:
    """state, state_label 결정. now는 KST aware datetime."""
    if _is_kr_holiday(now):
        return "CLOSED", "휴장"
    t = now.hour * 100 + now.minute
    if 900 <= t <= 1530:
        return "OPEN", "장중"
    if t < 900:
        return "BEFORE", "장 시작 전"
    return "AFTER", "장후"


_STATE_META = {
    "OPEN":   {"color": "green",  "emoji": "🟢"},
    "BEFORE": {"color": "gray",   "emoji": "⏳"},
    "AFTER":  {"color": "yellow", "emoji": "🟡"},
    "CLOSED": {"color": "gray",   "emoji": "⚪"},
}


def _load_kr_indices(state: str) -> tuple[dict, dict]:
    """KOSPI/KOSDAQ value/change_pct.
    OPEN/AFTER 상태에서는 Naver 실시간 우선, 실패 시 data.json 폴백.
    그 외 상태에서도 data.json 이 stale (오늘 거래일 아님) 이면 라이브 시도."""
    kospi = {"value": None, "change": None, "is_realtime": state == "OPEN"}
    kosdaq = {"value": None, "change": None, "is_realtime": state == "OPEN"}

    # 1) data.json 기본값
    data_date = None
    try:
        if DATA_JSON.exists():
            d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
            data_date = d.get("actual_date")
            ks = d.get("kospi") or {}
            kq = d.get("kosdaq") or {}
            if ks.get("value") is not None:
                kospi["value"] = ks["value"]
                kospi["change"] = ks.get("change_pct")
            if kq.get("value") is not None:
                kosdaq["value"] = kq["value"]
                kosdaq["change"] = kq.get("change_pct")
    except Exception:
        pass

    # 2) 라이브 덮어쓰기: OPEN/AFTER 또는 data.json 이 오늘 거래일 아닐 때
    today_str = now_kst().strftime("%Y%m%d")
    is_stale = data_date != today_str
    if state in ("OPEN", "AFTER") or is_stale:
        try:
            live = _fetch_kr_indices_live()
            if live.get("kospi", {}).get("value") is not None:
                kospi["value"] = live["kospi"]["value"]
                kospi["change"] = live["kospi"]["change_pct"]
            if live.get("kosdaq", {}).get("value") is not None:
                kosdaq["value"] = live["kosdaq"]["value"]
                kosdaq["change"] = live["kosdaq"]["change_pct"]
        except Exception:
            pass

    return kospi, kosdaq


@app.route("/api/market/context", methods=["GET"])
def api_market_context():
    """사이드바 시간 컨텍스트 배지용. 30~60초 간격 호출 가정.

    Returns 예시:
      OPEN: state=OPEN, label='장중', kospi/kosdaq 실시간(또는 최근값)
      BEFORE: state=BEFORE, label='장 시작 전', time_until_open='2시간 18분'
      AFTER: state=AFTER, label='장후', next_trading_label='5/8(금)'
      CLOSED: state=CLOSED, label='휴장', time_until_next_trading='1일 14시간'
    """
    try:
        now = now_kst()
        state, label = _market_state_kst(now)
        meta = _STATE_META[state]
        weekday_kr = "월화수목금토일"[now.weekday()]
        kospi, kosdaq = _load_kr_indices(state)

        result = {
            "state": state,
            "state_label": label,
            "state_color": meta["color"],
            "state_emoji": meta["emoji"],
            "now_kst": now.strftime("%H:%M"),
            "weekday_kr": weekday_kr,
            "date": now.strftime("%Y-%m-%d"),
            "kospi": kospi,
            "kosdaq": kosdaq,
            "last_updated": now.isoformat(),
        }

        # 다음 개장 시각 — BEFORE/AFTER/CLOSED 공통
        next_open = _next_trading_open_kst(now)
        secs = int((next_open - now).total_seconds())
        if state == "BEFORE":
            result["time_until_open"] = _humanize_duration(secs)
        if state in ("AFTER", "CLOSED"):
            wd_kr = "월화수목금토일"[next_open.weekday()]
            result["next_trading_day"] = next_open.strftime("%Y-%m-%d")
            result["next_trading_label"] = f"{next_open.month}/{next_open.day}({wd_kr})"
            result["time_until_next_trading"] = _humanize_duration(secs)

        return jsonify(result)
    except Exception as exc:
        log.exception("market/context")
        return jsonify({"error": str(exc)}), 500


@app.route("/freshness-demo", methods=["GET"])
def freshness_demo_page():
    """4-5-1-D 시각 검증 페이지 — 모든 라벨 + 실제 API 호출 결과."""
    return Response(_FRESHNESS_DEMO_HTML, content_type="text/html; charset=utf-8")


_FRESHNESS_DEMO_HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Freshness 컴포넌트 데모</title>
<link rel="stylesheet" href="/static/css/style.css">
<style>
  body { font-family: 'Noto Sans KR', sans-serif; padding: 24px; max-width: 1100px; margin: 0 auto; line-height: 1.55; color: #222; }
  h1 { margin: 0 0 8px; }
  h2 { border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 28px; }
  .row { margin: 8px 0; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .label-cell { display: inline-block; width: 90px; color: #555; font-size: 12px; }
  .grid { display: grid; grid-template-columns: 220px 1fr; gap: 6px 16px; align-items: center; }
  .grid > .key { font-size: 12px; color: #555; font-family: ui-monospace, monospace; }
  .note { font-size: 12px; color: #888; }
  pre { background: #f5f5f5; padding: 8px 12px; border-radius: 6px; font-size: 12px; overflow: auto; }
</style>
</head><body>

<h1>🟢 Freshness 컴포넌트 데모</h1>
<p class="note">Step 4-5-1-D · <code>freshness.js</code> + <code>style.css</code> 시각 검증 페이지</p>

<h2>1. 라벨 5종 (정적)</h2>
<div class="grid" id="static-labels"></div>

<h2>2. 라벨 5종 — compact 모드 (점만)</h2>
<div class="grid" id="compact-labels"></div>

<h2>3. 출처 배지 (mock)</h2>
<div id="mock-source"></div>

<h2>4. 실제 API 응답 (전체 20개 소스)</h2>
<div class="note">서버에서 <code>/api/freshness/all</code> 호출 후 <code>sourceBadge()</code>로 렌더.</div>
<div id="api-rendered" style="display:flex;flex-direction:column;gap:6px;margin-top:8px;"></div>

<h2>5. data-freshness 자동 렌더 (refreshAllBadges)</h2>
<div class="note">DOM에 <code>&lt;span data-freshness="naver_price"&gt;</code> 식으로 두면 자동 채워짐.</div>
<div style="display:flex;flex-direction:column;gap:4px;margin-top:8px;">
  <div>네이버 가격: <span data-freshness="naver_price"></span></div>
  <div>분석 일지: <span data-freshness="analysis_journal"></span></div>
  <div>등록 안 된 소스: <span data-freshness="fake_source"></span></div>
  <div>compact 모드: <span data-freshness="ohlcv_kr" data-compact></span></div>
</div>

<script src="/static/js/freshness.js"></script>
<script>
const LABELS = ['LIVE','DELAY','ARCHIVE','MANUAL','NO_DATA'];

// 1. 정적 라벨
const g1 = document.getElementById('static-labels');
LABELS.forEach(l => {
  g1.insertAdjacentHTML('beforeend',
    `<div class="key">${l}</div><div>${freshnessBadge(l, {title: 'tooltip text'})}</div>`);
});

// 2. compact
const g2 = document.getElementById('compact-labels');
LABELS.forEach(l => {
  g2.insertAdjacentHTML('beforeend',
    `<div class="key">${l}</div><div>${freshnessBadge(l, {compact: true})}</div>`);
});

// 3. mock 출처 배지
const mock = {
  source: 'naver_price',
  name_kr: '네이버 가격 (KR)',
  label: 'LIVE',
  last_updated_kst: '2026-05-06 17:23:45 KST',
  age_human: '3분 전',
  description: 'stocks 테이블 KR 가격',
};
document.getElementById('mock-source').innerHTML =
  '<div class="row">기본: ' + sourceBadge(mock) + '</div>'
  + '<div class="row">신선도 표시 끔: ' + sourceBadge(mock, {withFreshness: false}) + '</div>';

// 4. 전체 API 호출
fetch('/api/freshness/all').then(r => r.json()).then(j => {
  const root = document.getElementById('api-rendered');
  for (const s of (j.sources || [])) {
    const div = document.createElement('div');
    div.innerHTML = sourceBadge(s);
    root.appendChild(div);
  }
}).catch(e => {
  document.getElementById('api-rendered').textContent = '로드 실패: ' + e;
});

// 5. data-freshness 자동
refreshAllBadges();
</script>
</body></html>
"""


@app.route("/api/freshness/categories", methods=["GET"])
def api_freshness_categories():
    """카테고리 목록 + 소속 소스 (UI 필터용)."""
    try:
        from data_freshness import DATA_SOURCE_CONFIG
        CATEGORY_NAMES_KR = {
            "realtime":  "🟢 실시간 (분 단위)",
            "frequent":  "🟡 빈번 (분~시간)",
            "hourly":    "🟠 시간 단위",
            "daily":     "🔵 일간",
            "weekly":    "⚪ 주간",
            "monthly":   "⚪ 월간",
            "quarterly": "⚪ 분기",
            "ohlcv":     "📊 일봉 (OHLCV)",
            "manual":    "👤 수동 입력",
            "static":    "🔧 정적",
        }
        cat_dict: dict[str, list] = {}
        for source, config in DATA_SOURCE_CONFIG.items():
            cat = config.get("category", "unknown")
            cat_dict.setdefault(cat, []).append({
                "source": source,
                "name_kr": config.get("name_kr", source),
            })
        cat_order = ["realtime", "frequent", "hourly", "daily", "weekly",
                     "monthly", "quarterly", "ohlcv", "manual", "static"]
        result = []
        for cat in cat_order:
            if cat in cat_dict:
                result.append({
                    "key": cat,
                    "name_kr": CATEGORY_NAMES_KR.get(cat, cat),
                    "count": len(cat_dict[cat]),
                    "sources": cat_dict[cat],
                })
        return jsonify({"categories": result})
    except Exception as exc:
        log.exception("freshness/categories")
        return jsonify({"error": str(exc)}), 500


# ============================================================
# Phase 4-5-12: 운영 대시보드 API
# ============================================================

# Cron 잡 카테고리 매핑 (id 패턴 → 그룹). UI 필터링/색상용.
_CRON_CATEGORIES = {
    "alert":     ("📨 알림", ("tg_", "alert_", "watchlist", "_check_trailing")),
    "market":    ("📈 시세/데이터", ("market_update", "_refresh_prices",
                                    "_refresh_briefing", "_refresh_global",
                                    "sync_us_market", "us_market_build",
                                    "naver_universe", "stage2", "agent",
                                    "etf", "themes_mapping", "us_universe")),
    "earnings":  ("💎 어닝/공시", ("earnings_", "consensus", "dart_",
                                  "poll_dart", "init_dart")),
    "infra":     ("⚙ 인프라", ("self_keep_alive", "db_backup",
                                "universe_sync", "options_signal")),
}


def _classify_cron_job(job_id: str) -> tuple[str, str]:
    """job id → (category_key, label_kr). 패턴 매칭 폴백."""
    jid = (job_id or "").lower()
    for key, (label, prefixes) in _CRON_CATEGORIES.items():
        if any(p.lower().strip("_") in jid for p in prefixes):
            return key, label
    return "other", "🔹 기타"


def _trigger_summary(trigger) -> str:
    """APScheduler trigger 객체 → 사람이 읽을 수 있는 요약."""
    try:
        s = str(trigger)
        # IntervalTrigger: 'interval[0:05:00]'
        # CronTrigger: 'cron[day_of_week='mon-fri', hour='9-15', minute='5,35']'
        return s[:160]
    except Exception:
        return "(unknown)"


@app.route("/api/ops/cron/jobs", methods=["GET"])
def api_ops_cron_jobs():
    """APScheduler 등록 잡 + 다음 실행시각 + 카테고리.
    Query: category=<key>, status=running|paused
    """
    try:
        if _scheduler is None or not getattr(_scheduler, "running", False):
            return jsonify({
                "scheduler_running": False,
                "jobs": [],
                "count": 0,
                "categories": {},
            })
        category_filter = request.args.get("category")
        status_filter = request.args.get("status")
        now = now_kst()
        rows = []
        for j in _scheduler.get_jobs():
            cat_key, cat_label = _classify_cron_job(j.id)
            nrt = getattr(j, "next_run_time", None)
            next_run_iso = None
            sec_until = None
            if nrt is not None:
                next_run_iso = nrt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
                sec_until = int((nrt.astimezone(KST) - now).total_seconds())
            job_status = "paused" if nrt is None else (
                "due" if sec_until is not None and sec_until <= 0 else "scheduled"
            )
            rows.append({
                "id": j.id,
                "name": j.name or j.id,
                "func": getattr(j.func_ref, "__name__", str(j.func_ref))
                        if hasattr(j, "func_ref") else str(getattr(j, "func", "")),
                "trigger": _trigger_summary(j.trigger),
                "category": cat_key,
                "category_label": cat_label,
                "next_run_at": next_run_iso,
                "next_run_in_sec": sec_until,
                "status": job_status,
                "max_instances": getattr(j, "max_instances", None),
            })
        if category_filter:
            rows = [r for r in rows if r["category"] == category_filter]
        if status_filter:
            rows = [r for r in rows if r["status"] == status_filter]
        # 다음 실행 임박순 정렬
        rows.sort(key=lambda r: (r["next_run_in_sec"] if r["next_run_in_sec"] is not None else 1 << 30))
        # 카테고리 분포
        cat_dist: dict[str, int] = {}
        for r in rows:
            cat_dist[r["category"]] = cat_dist.get(r["category"], 0) + 1
        return jsonify({
            "scheduler_running": True,
            "jobs": rows,
            "count": len(rows),
            "categories": cat_dist,
            "checked_at": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        })
    except Exception as exc:
        log.exception("ops/cron/jobs")
        return jsonify({"error": str(exc)}), 500


# KBJ P2 삭제(설계 §5.10): api_ops_cron_trigger — #45 수동 실행은 python -m kbj.services.scheduler run-once <job> --as-of …


@app.route("/api/ops/data_json/rebuild", methods=["POST"])
def api_ops_data_json_rebuild():
    """data.json 수동 재생성. 맥북 cron 을 대신하는 경로를 손으로 돌려 본다.

    동기 실행이라 결과(테마 수·스파크라인 수)를 그대로 돌려준다.
    실패해도 기존 파일은 건드리지 않는다.
    """
    res = _build_data_json()
    return jsonify(res), (200 if res.get("ok") else 503)


@app.route("/api/ops/data_json/status", methods=["GET"])
def api_ops_data_json_status():
    """data.json 이 언제 것이고 누가 만들었는지."""
    out = {"exists": DATA_JSON.exists(), "age_min": _data_json_stale_min()}
    if DATA_JSON.exists():
        try:
            d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
            out.update({
                "updated_at": d.get("updated_at"),
                "actual_date": d.get("actual_date"),
                # source=server 면 이 서버가 만든 것, 없으면 맥북 cron 이 git 으로
                # 넣어 둔 판이다. 구분이 돼야 'cron 이 멈췄다' 를 알 수 있다.
                "source": d.get("source") or "macbook_cron_or_git",
                "themes": len(d.get("themes") or []),
                "market_overview_keys": sorted(d.get("market_overview") or {}),
                "new_high_sectors": len(d.get("new_high_sectors") or []),
            })
        except Exception as exc:
            out["error"] = _mask_secrets(str(exc))[:200]
    age = out.get("age_min")
    out["stale"] = (age is None) or (age > 24 * 60)

    # 왜 아직 서버 생성본이 아닌지를 밖에서 볼 수 있어야 한다.
    # 빌더가 보는 것과 같은 수치를 그대로 싣는다.
    try:
        umap = (_load_naver_universe() or {}).get("stocks") or {}
        out["universe_stocks"] = len(umap)
        out["universe_live"] = _universe_live_count(umap)
        mf = BASE_DIR / "themes_mapping.json"
        if mf.exists():
            mapping = json.loads(mf.read_text(encoding="utf-8"))
            codes = {(x["code"] if isinstance(x, dict) else x)
                     for t in mapping for x in t.get("stocks", [])}
            out["mapped_total"] = len(codes)
            out["mapped_live"] = sum(
                1 for c in codes
                if float((umap.get(c) or {}).get("volume_mn") or 0) > 0)
            out["mapped_need"] = max(1, int(len(codes) * 0.6))
        out["build_in_progress"] = _DATA_JSON_LOCK.locked()
        if _DATA_JSON_BUILD_STARTED:
            out["build_running_sec"] = round(time.time() - _DATA_JSON_BUILD_STARTED, 1)
    except Exception as exc:
        out["diag_error"] = _mask_secrets(str(exc))[:200]
    return jsonify(out)


@app.route("/api/ops/diag/collect_errors", methods=["GET"])
def api_ops_diag_collect_errors():
    """최근 수집 실패 목록. Render 로그를 볼 수 없을 때의 유일한 창구다.

    키·토큰은 _mask_secrets 로 가려서 나간다.
    Query: ?source=naver_trend 로 걸러 볼 수 있다. ?limit=50 (기본 50).
    """
    src = (request.args.get("source") or "").strip()
    limit = max(1, min(int(request.args.get("limit", 50) or 50), 120))
    items = list(_COLLECT_ERRORS)
    if src:
        items = [e for e in items if e.get("source") == src]
    items = items[-limit:]
    items.reverse()  # 최신이 위

    by_source: dict = {}
    for e in _COLLECT_ERRORS:
        by_source[e.get("source", "?")] = by_source.get(e.get("source", "?"), 0) + 1

    return jsonify({
        "count": len(items),
        "total_buffered": len(_COLLECT_ERRORS),
        "buffer_max": _COLLECT_ERRORS.maxlen,
        "by_source": by_source,
        "errors": items,
        "note": "버퍼는 프로세스 메모리다 — 재시작하면 비워진다. "
                "비어 있다고 수집이 정상이라는 뜻은 아니다.",
    })


@app.route("/api/ops/diag/sources", methods=["GET"])
def api_ops_diag_sources():
    """지금 무엇이 들어오고 무엇이 안 들어오는지 한 화면으로.

    '데이터가 비어 있다' 와 '수집이 깨졌다' 를 구분해서 보여 준다.
    화면·텔레그램이 빠진 데이터를 숨기지 않게 하려고 만든 단일 진실 출처다.
    """
    out: dict = {"checked_at": now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
                 "trading_date": _get_trading_date(), "sources": {}, "missing": []}

    def _put(key, label, ok, detail):
        out["sources"][key] = {"label": label, "ok": bool(ok), "detail": detail}
        if not ok:
            out["missing"].append(label)

    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                n_price = conn.execute(
                    "SELECT COUNT(*) FROM stocks WHERE change_pct IS NOT NULL "
                    "AND close > 0").fetchone()[0]
                n_cap = conn.execute(
                    "SELECT COUNT(*) FROM stocks WHERE market_cap IS NOT NULL "
                    "AND market_cap > 0").fetchone()[0]
                n_flow = conn.execute(
                    "SELECT COUNT(*) FROM flow_cache "
                    "WHERE foreign_value_json IS NOT NULL").fetchone()[0]
                frow = conn.execute(
                    "SELECT dates_json, fetched_at FROM flow_cache "
                    "WHERE code='005930'").fetchone()
                n_ohlcv = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
            flow_latest = None
            if frow and frow["dates_json"]:
                d = _parse_json_list(frow["dates_json"])
                flow_latest = d[-1] if d else None
            _put("price", "가격 (KR)", n_price >= 1000, f"{n_price}종목")
            _put("market_cap", "시가총액", n_cap >= 1000, f"{n_cap}종목")
            _put("flow", "투자자 수급", n_flow >= 50,
                 f"{n_flow}행, 최신 {flow_latest or '없음'}")
            _put("ohlcv", "일봉", n_ohlcv > 0, f"{n_ohlcv}행")
        except Exception as exc:
            out["db_error"] = _mask_secrets(str(exc))[:200]
    else:
        out["db_error"] = "SQLite 비활성"

    sec_cache = BASE_DIR / "cache" / "sectors_naver_landing.json"
    _put("sectors", "업종", sec_cache.exists(),
         "캐시 있음" if sec_cache.exists() else "캐시 없음")

    # data.json — 예전엔 맥북 cron 이 git push 로 넣었다. 지금은 서버가 만든다.
    dj_age = _data_json_stale_min()
    dj_src = "?"
    try:
        if DATA_JSON.exists():
            dj_src = (json.loads(DATA_JSON.read_text(encoding="utf-8"))
                      .get("source") or "git(맥북 cron)")
    except Exception:
        pass
    _put("data_json", "data.json",
         dj_age is not None and dj_age <= 24 * 60,
         f"{'%.0f분 전' % dj_age if dj_age is not None else '읽기 실패'} · 출처 {dj_src}")

    recent = list(_COLLECT_ERRORS)[-10:]
    out["recent_errors"] = list(reversed(recent))
    out["healthy"] = not out["missing"]
    return jsonify(out)


@app.route("/api/ops/diag/stocks_schema", methods=["GET"])
def api_ops_diag_stocks_schema():
    """stocks 테이블 schema 진단 (Render 환경에서 ALTER 컬럼 부재 의심 시)."""
    try:
        with _get_db() as conn:
            cols = [dict(r) for r in conn.execute("PRAGMA table_info(stocks)")]
            cnt = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
            # 직접 INSERT 테스트 — 어떤 에러 나는지 확인
            test_result = "skipped"
            try:
                conn.execute(
                    "INSERT INTO stocks "
                    "(code, name, market, sector, market_cap, market_cap_updated, "
                    " close, change_pct, volume_mn, sectors_json, "
                    " after_hours_price, after_hours_change_pct, "
                    " after_hours_status, after_hours_time, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now')) "
                    "ON CONFLICT(code) DO UPDATE SET close=excluded.close",
                    ("__test__", "TEST", "", "", 0, None,
                     100.0, 0.0, 0.0, "[]",
                     None, None, None, None)
                )
                conn.commit()
                cnt2 = conn.execute("SELECT COUNT(*) FROM stocks WHERE code='__test__'").fetchone()[0]
                test_result = "ok" if cnt2 == 1 else "no_row"
                conn.execute("DELETE FROM stocks WHERE code='__test__'")
                conn.commit()
            except Exception as exc:
                test_result = f"FAIL: {type(exc).__name__}: {exc}"

        return jsonify({
            "columns": [{"name": c["name"], "type": c["type"],
                         "notnull": c["notnull"], "dflt": c["dflt_value"]}
                        for c in cols],
            "column_names": [c["name"] for c in cols],
            "row_count": cnt,
            "insert_test": test_result,
        })
    except Exception as exc:
        log.exception("ops/diag/stocks_schema")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/ops/diag/kr_universe", methods=["GET"])
def api_ops_diag_kr_universe():
    """KR universe 진단: 본체 파일 / lock / stocks 카운트 / 캐시 디렉토리 상태.

    Render 환경에서 stocks 테이블이 0건일 때 어디가 막혔는지 빠르게 식별.
    """
    import glob as _glob
    try:
        today = _get_trading_date()
        cache_dir = BASE_DIR / "cache"
        out_file = cache_dir / f"naver_universe_{today}.json"
        lock_file = out_file.with_suffix(".lock")

        files = sorted(_glob.glob(str(cache_dir / "naver_universe_*.json")))
        locks = sorted(_glob.glob(str(cache_dir / "naver_universe_*.lock")))

        info: dict = {
            "trading_date": today,
            "out_file": str(out_file),
            "out_exists": out_file.exists(),
            "out_size": out_file.stat().st_size if out_file.exists() else 0,
            "lock_exists": lock_file.exists(),
            "lock_age_sec": (int(time.time() - lock_file.stat().st_mtime)
                             if lock_file.exists() else None),
            "all_universe_files": [
                {"name": Path(f).name, "size": Path(f).stat().st_size}
                for f in files
            ],
            "all_locks": [
                {"name": Path(f).name,
                 "age_sec": int(time.time() - Path(f).stat().st_mtime)}
                for f in locks
            ],
        }

        # _load_naver_universe() 시도
        try:
            uni = _load_naver_universe()
            info["load_universe"] = {
                "ok": bool(uni and uni.get("stocks")),
                "stocks_count": len(uni.get("stocks") or {}),
                "fetched_at": uni.get("fetched_at") if isinstance(uni, dict) else None,
            }
        except Exception as exc:
            info["load_universe"] = {"error": str(exc)}

        # stocks 테이블 카운트
        try:
            with _get_db() as conn:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM stocks "
                    "WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
                ).fetchone()[0]
                latest = conn.execute(
                    "SELECT MAX(updated_at) FROM stocks"
                ).fetchone()[0]
            info["stocks"] = {"count_kr": cnt, "latest_updated_at": latest}
        except Exception as exc:
            info["stocks"] = {"error": str(exc)}

        return jsonify(info)
    except Exception as exc:
        log.exception("ops/diag/kr_universe")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/ops/recover/kr_stocks", methods=["POST"])
def api_ops_recover_kr_stocks():
    """수동 KR 가격 회복: stale lock 정리 → universe 동기 빌드 → 가격 갱신.

    Render daemon thread 죽음으로 stocks=0 상태에서 빠른 자동 회복.
    각 단계 elapsed/result 를 응답에 반환.
    """
    import glob as _glob
    from time import time as _t
    steps: list = []
    try:
        # 1. 모든 stale lock 정리
        removed = 0
        for lf in _glob.glob(str(BASE_DIR / "cache" / "naver_universe_*.lock")):
            try:
                Path(lf).unlink()
                removed += 1
            except Exception as exc:
                steps.append({"lock_unlink_fail": str(lf), "err": str(exc)})
        steps.append({"step": "lock_cleanup", "removed": removed})

        # 2. universe 동기 빌드
        today = _get_trading_date()
        out_file = BASE_DIR / "cache" / f"naver_universe_{today}.json"
        t0 = _t()
        try:
            _build_naver_universe_background()
            steps.append({
                "step": "universe_build",
                "elapsed_sec": round(_t() - t0, 1),
                "out_exists": out_file.exists(),
                "out_size": out_file.stat().st_size if out_file.exists() else 0,
            })
        except Exception as exc:
            steps.append({"step": "universe_build", "error": str(exc),
                          "elapsed_sec": round(_t() - t0, 1)})
            return jsonify({"ok": False, "steps": steps}), 500

        if not out_file.exists():
            steps.append({"step": "stop", "reason": "본체 빌드 실패 — 가격 갱신 스킵"})
            return jsonify({"ok": False, "steps": steps}), 500

        # 3. _UNI_CACHE 무효화 후 가격 갱신
        global _UNI_CACHE
        _UNI_CACHE["mtime"] = 0
        t0 = _t()
        try:
            n = _refresh_prices_from_naver()
            steps.append({
                "step": "refresh_prices",
                "updated": n,
                "elapsed_sec": round(_t() - t0, 1),
            })
        except Exception as exc:
            steps.append({"step": "refresh_prices", "error": str(exc),
                          "elapsed_sec": round(_t() - t0, 1)})

        # 4. 검증
        try:
            with _get_db() as conn:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM stocks "
                    "WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
                ).fetchone()[0]
            steps.append({"step": "verify", "stocks_count_kr": cnt})
            ok = cnt > 100
        except Exception as exc:
            steps.append({"step": "verify", "error": str(exc)})
            ok = False

        return jsonify({"ok": ok, "steps": steps})
    except Exception as exc:
        log.exception("ops/recover/kr_stocks")
        steps.append({"step": "fatal", "error": str(exc)})
        return jsonify({"ok": False, "steps": steps}), 500


@app.route("/api/ops/health", methods=["GET"])
def api_ops_health():
    """헬스 대시보드용 통합 메트릭.
    구성: data freshness summary + scheduler + DB + LLM cache + telegram count.
    """
    try:
        result: dict = {
            "checked_at": now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
            "uptime_sec": round(time.time() - _start_time),
        }

        # 1) 데이터 신선도 요약
        try:
            from data_freshness import get_freshness_summary
            fr = get_freshness_summary()
            result["freshness"] = {
                "health_score": fr.get("health_score"),
                "by_label": fr.get("by_label"),
                "total": fr.get("total_sources"),
                "issues_count": len(fr.get("issues", [])),
                "issues_top": fr.get("issues", [])[:5],
            }
        except Exception as exc:
            result["freshness"] = {"error": str(exc)}

        # 2) 스케줄러
        try:
            sched_running = bool(_scheduler and _scheduler.running)
            jobs = _scheduler.get_jobs() if sched_running else []
            paused = sum(1 for j in jobs if getattr(j, "next_run_time", None) is None)
            result["scheduler"] = {
                "running": sched_running,
                "jobs_total": len(jobs),
                "jobs_paused": paused,
                "jobs_active": len(jobs) - paused,
            }
        except Exception as exc:
            result["scheduler"] = {"error": str(exc)}

        # 3) DB 크기 + 핵심 테이블 row count
        try:
            db_path = BASE_DIR / "db" / "dashboard.db"
            size_mb = round(db_path.stat().st_size / (1024 * 1024), 1) if db_path.exists() else None
            tables_count = {}
            with _get_db() as conn:
                for tbl in ("stocks", "ohlcv", "disclosure_history",
                            "earnings_surprise", "alert_history_v2",
                            "analysis_journal"):
                    try:
                        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
                        tables_count[tbl] = cur.fetchone()[0]
                    except Exception:
                        tables_count[tbl] = None
            result["db"] = {"size_mb": size_mb, "rows": tables_count}
        except Exception as exc:
            result["db"] = {"error": str(exc)}

        # 4) LLM 캐시 (qwen3:14b)
        try:
            cache_db = BASE_DIR / "db" / "llm_cache.db"
            if cache_db.exists():
                conn_lc = sqlite3.connect(str(cache_db))
                row = conn_lc.execute(
                    "SELECT COUNT(*), COALESCE(SUM(hit_count),0) FROM llm_cache"
                ).fetchone()
                conn_lc.close()
                result["llm_cache"] = {
                    "entries": row[0],
                    "total_hits": row[1],
                    "size_mb": round(cache_db.stat().st_size / (1024 * 1024), 2),
                }
            else:
                result["llm_cache"] = {"entries": 0, "total_hits": 0, "size_mb": 0}
        except Exception as exc:
            result["llm_cache"] = {"error": str(exc)}

        # 5) 텔레그램 발송 통계 (alert_history_v2 활용)
        try:
            with _get_db() as conn:
                cur = conn.execute("""
                    SELECT
                        SUM(CASE WHEN sent_at >= datetime('now','-1 day') THEN 1 ELSE 0 END) AS day,
                        SUM(CASE WHEN sent_at >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS week,
                        SUM(CASE WHEN sent_status = 'SENT' THEN 1 ELSE 0 END) AS sent_total,
                        SUM(CASE WHEN sent_status != 'SENT' THEN 1 ELSE 0 END) AS fail_total
                    FROM alert_history_v2
                """)
                r = cur.fetchone()
                result["telegram"] = {
                    "last_24h": r[0] or 0,
                    "last_7d": r[1] or 0,
                    "sent_total": r[2] or 0,
                    "fail_total": r[3] or 0,
                }
        except Exception as exc:
            result["telegram"] = {"error": str(exc)}

        # 종합 점수 (0~100): freshness 가중 60% + scheduler 30% + db 10%
        try:
            f_score = result.get("freshness", {}).get("health_score") or 0
            s = result.get("scheduler", {})
            s_score = 100 if s.get("running") else 0
            if s.get("jobs_total"):
                s_score = int(round(100 * (s["jobs_active"] / s["jobs_total"])))
            db_score = 100 if (result.get("db", {}).get("size_mb") or 0) > 0 else 0
            overall = int(round(0.6 * f_score + 0.3 * s_score + 0.1 * db_score))
            result["overall_score"] = overall
        except Exception:
            result["overall_score"] = None

        return jsonify(result)
    except Exception as exc:
        log.exception("ops/health")
        return jsonify({"error": str(exc)}), 500


# ============================================================
# Step 5-1-D: 리비전 API 4개
#   signal 코드: STRONG_UP / UP / NEUTRAL / DOWN / STRONG_DOWN (revision_calculator 와 동일)
#   ⚠️ revision_alerts.current_date 는 SQLite 예약어 충돌 → "current_date" 인용 필수
# ============================================================

# KBJ P4 삭제: api_revision_for_stock — kbj consensus.snapshot·kbj.engines.consensus.revision·/api/stock/{code}/consensus(KIS — ADR 0015)


# KBJ P4 삭제: api_revision_screener — kbj consensus.snapshot·kbj.engines.consensus.revision·/api/stock/{code}/consensus(KIS — ADR 0015)


# KBJ P4 삭제: api_revision_divergence — kbj consensus.snapshot·kbj.engines.consensus.revision·/api/stock/{code}/consensus(KIS — ADR 0015)


# KBJ P4 삭제: api_revision_history — kbj consensus.snapshot·kbj.engines.consensus.revision·/api/stock/{code}/consensus(KIS — ADR 0015)


# gunicorn 이 모듈을 import 하는 시점에 자동 실행.
# SERVER_NO_STARTUP=1 이면 건너뛴다 — 스케줄러·백그라운드 스레드 없이
# 파서 함수만 import 해서 테스트하려고 둔 문이다 (scripts/test_collectors.py).
# 운영에서는 이 변수를 설정하지 않으므로 동작이 달라지지 않는다.
if os.environ.get("SERVER_NO_STARTUP") != "1":
    _startup()


if __name__ == "__main__":
    ws_tag = " + WebSocket" if _SOCKETIO_OK else ""
    print(f"""
  ┌─────────────────────────────────────────┐
  │   테마 트리맵 서버{ws_tag:16s}        │
  │   http://{HOST}:{PORT}                   │
  └─────────────────────────────────────────┘
""")
    if _SOCKETIO_OK:
        socketio.run(app, host=HOST, port=PORT, debug=False,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    else:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
