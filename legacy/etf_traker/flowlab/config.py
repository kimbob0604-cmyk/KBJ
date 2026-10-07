"""경로·임계치·.env 로딩.

엔진 기준값(1,000억 / 50억 / 252일 / 60일 / 5%)을 여기 상수로 둔다.
board/config/settings.yaml 이 읽히면 값이 어긋나는지 대조해 경고만 낸다
(엔진 설정을 여기서 덮어쓰지 않는다 — 엔진은 읽기 전용이다).
"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "flowlab"
BOARD = ROOT / "board"
STATE = BOARD / "state"
DOCS = ROOT / "docs"
CACHE = PKG / "cache"
PRICE_CACHE = CACHE / "prices"
OUT = PKG / "out"

for _d in (CACHE, PRICE_CACHE, OUT):
    _d.mkdir(parents=True, exist_ok=True)

# ── 엔진과 동일한 스크리닝 기준 ────────────────────────────────
MIN_MKTCAP_EOK = 1000.0        # 시가총액 하한 (억원)
MIN_TURNOVER_EOK = 50.0        # 거래대금 하한 (억원)
W52_DAYS = 252                 # 52주 = 직전 252영업일 (당일 제외)
D60_DAYS = 60                  # 60일 = 직전 60영업일 (당일 제외)
PROXIMITY_MAX_GAP_PCT = 5.0    # 근접 판정 갭 상한

# ── 모듈 A (수급 레이어) 임계치 ────────────────────────────────
FLOW_WINDOWS = (1, 5, 20)      # 순매매 합산 구간 (영업일)
SUPPORT_INTENSITY_BP = 20.0    # supported 판정 강도 하한 (bp)
FLOW_ROWS_PER_PAGE = 20        # 네이버 frgn 표 1페이지 = 20거래일
FLOW_PAGE_MARGIN = 1           # as_of 절단 뒤에도 창이 남도록 더 받는 페이지

# ── 모듈 B (이벤트 스터디) 축 ──────────────────────────────────
STUDY_YEARS = 3
STUDY_HORIZONS = (5, 20)       # 후행 관측 구간 (영업일)
VOL_AVG_DAYS = 20              # 거래량 배수의 분모
VOL_BUCKETS = (                # (하한, 상한, 라벨) — 상한 None = 무한
    (0.0, 1.0, "<1배"),
    (1.0, 2.0, "1~2배"),
    (2.0, 4.0, "2~4배"),
    (4.0, None, "4배+"),
)
GAP_BUCKETS = (
    (0.0, 1.0, "0~1%"),
    (1.0, 2.0, "1~2%"),
    (2.0, 3.0, "2~3%"),
    (3.0, 5.0, "3~5%"),
)
STREAK_BUCKETS = ((1, 1, "1일"), (2, 3, "2~3일"), (4, 7, "4~7일"), (8, None, "8일+"))
BREAKOUT_WINDOW = 5            # 근접 → 돌파 전환을 보는 영업일 수
BREAK_MAX_ABS_CHG_PCT = 60.0   # 일간 |변동| 이 값 초과 = 액면병합·감자 단절로 본다

# ── 데이터 소스 ───────────────────────────────────────────────
SISE_URL = "https://api.finance.naver.com/siseJson.naver"

# 투자자별(기관·외인) 순매매. **2026-09 에 주소가 바뀌었다.**
#
# 옛 주소 finance.naver.com/item/frgn.naver 는 302 로 stock.naver.com 의
# Next.js 화면으로 넘어가고, 그 응답에는 tr/td/table 이 하나도 없다(러너 실측
# 119,822바이트 · script 113개). HTML 표를 긁던 파서는 무엇을 고쳐도 읽을 것이
# 없다 — 그래서 139종목 전부가 '수급 표 없음' 으로 비었다.
#
# 그 화면이 값을 받아 오는 JSON 주소로 갈아탄다. 러너에서 후보 11개를 찔러
# 이것만 200 을 줬다(.github/workflows/frgn-probe.yml).
#   {"bizdate":"20260916","closePrice":"40,550",
#    "organPureBuyQuant":"+85,620","foreignerPureBuyQuant":"+296,424",
#    "individualPureBuyQuant":"-384,210","foreignerHoldRatio":"31.13%",
#    "accumulatedTradingVolume":"1,795,331", ...}   (KBJ P1: 모양만 원문, 값은 합성)
TREND_URL = "https://m.stock.naver.com/api/stock/{code}/trend"

# 이 API 는 기본 10거래일만 준다. `pageSize` 로 늘어난다(실측: 60 → 60건,
# 20260623~20260916). size/count/limit/page 는 먹지 않는다 — 넣어도 기본 10건이
# 그대로 온다. 그래서 pages 를 pageSize 로 환산해 한 번에 받는다.
TREND_PAGESIZE_PARAM = "pageSize"
INDEX_SYMBOL = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"}
HTTP_TIMEOUT = 15
HTTP_TRIES = 3
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 합성 소스가 만든 산출물에는 이 값이 source 로 박힌다. 리포트가 그대로 노출한다.
SOURCE_NAVER = "naver"
SOURCE_DEMO = "demo"


def load_env(path: pathlib.Path | None = None) -> dict:
    """board/.env 를 읽어 os.environ 에 얹는다. 파일이 없으면 조용히 넘어간다."""
    out = {}
    for p in ([path] if path else [BOARD / ".env", ROOT / ".env"]):
        if not p or not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            out[k] = v
            os.environ.setdefault(k, v)
    return out


def settings_drift() -> list[str]:
    """엔진 settings.yaml 과 여기 상수가 어긋나면 그 목록을 돌려준다."""
    p = BOARD / "config" / "settings.yaml"
    if not p.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        s = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    pairs = [
        ("min_mktcap_eok", MIN_MKTCAP_EOK, (s.get("display") or {}).get("min_mktcap_eok")),
        ("min_turnover_eok", MIN_TURNOVER_EOK, (s.get("display") or {}).get("min_turnover_eok")),
        ("w52", W52_DAYS, ((s.get("newhigh") or {}).get("lookback") or {}).get("d60") and
         ((s.get("newhigh") or {}).get("lookback") or {}).get("w52")),
        ("d60", D60_DAYS, ((s.get("newhigh") or {}).get("lookback") or {}).get("d60")),
        ("max_gap_pct", PROXIMITY_MAX_GAP_PCT, (s.get("proximity") or {}).get("max_gap_pct")),
    ]
    return [f"{k}: flowlab={mine} vs settings.yaml={theirs}"
            for k, mine, theirs in pairs if theirs is not None and float(theirs) != float(mine)]


def docs_dir(source: str = SOURCE_NAVER) -> pathlib.Path:
    """합성 소스 산출물은 발행 폴더(docs/)에 넣지 않는다 — board --demo 와 같은 규칙."""
    d = DOCS if source != SOURCE_DEMO else ROOT / "docs-demo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_dates() -> list[str]:
    if not STATE.exists():
        return []
    return sorted(p.name for p in STATE.iterdir() if p.is_dir() and p.name.isdigit())


def state_dir(date: str | None = None) -> pathlib.Path:
    """YYYYMMDD 를 받아 state 디렉터리를 돌려준다. 없으면 최신."""
    dates = state_dates()
    if not dates:
        raise FileNotFoundError(f"state 가 없다: {STATE} — board 엔진을 먼저 돌려라")
    if date is None:
        date = dates[-1]
    date = date.replace("-", "")
    if date not in dates:
        raise FileNotFoundError(f"{date} state 없음. 있는 날짜: {', '.join(dates[-5:])}")
    return STATE / date
