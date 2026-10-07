"""probe 공통 — 실행 문맥, 결과 기록, 순수 판정 함수.

probe 는 실측만 한다. 아무 데도 쓰지 않고, 비밀정보를 출력하지 않는다.
결과는 `probe_out/<name>.json` 과 `probe_out/summary.md` 로 남기고, CI 에서는
아티팩트와 런 요약으로 올린다. 사람이 그 결과를 `docs/probe_results.md` 에 옮긴다.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from config.settings import Settings
from data.kis.rest import KisClient, KisResponse, redact

KST = ZoneInfo("Asia/Seoul")
# 예약 실행이 서로 덮어쓰지 않게 폴더를 바꿀 수 있다 (PROBE_OUT_DIR=probe_out/runs/…)
OUT_DIR = Path(os.environ.get("PROBE_OUT_DIR", "probe_out"))

# KIS 엔드포인트 (KIS 공식 open-trading-api 샘플 기준)
P_CALLPUT = "/uapi/domestic-futureoption/v1/quotations/display-board-callput"
P_OPTION_LIST = "/uapi/domestic-futureoption/v1/quotations/display-board-option-list"
P_FUT_BOARD = "/uapi/domestic-futureoption/v1/quotations/display-board-futures"
P_TOP = "/uapi/domestic-futureoption/v1/quotations/display-board-top"
P_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
P_MINUTE = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
P_INVESTOR = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"

TR_CALLPUT = "FHPIF05030100"
TR_OPTION_LIST = "FHPIO056104C0"
TR_FUT_BOARD = "FHPIF05030200"
TR_TOP = "FHPIF05030000"
TR_PRICE = "FHMIF10000000"
TR_MINUTE = "FHKIF03020200"
TR_INVESTOR = "FHPTJ04030000"

Session = Literal["day", "night", "closed"]


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def session_of(ts: datetime) -> Session:
    """시계만 보고 세션을 판정한다 (휴장일은 모른다 — probe 기록용)."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime 금지")
    t = ts.astimezone(KST)
    hm = t.hour * 60 + t.minute
    wd = t.weekday()  # 월=0
    if wd < 5 and 8 * 60 + 45 <= hm < 15 * 60 + 45:
        return "day"
    # 야간: 월~금 18:00 ~ 익일 06:00 (금요일 야간은 토요일 06:00 까지)
    if wd < 5 and hm >= 18 * 60:
        return "night"
    if 1 <= wd <= 5 and hm < 6 * 60:
        return "night"
    return "closed"


MtrtFormat = Literal["YYYYMM", "YYMMWW", "unknown"]


def classify_mtrt(code: str) -> MtrtFormat:
    """만기 코드 형식. 월물은 YYYYMM, 위클리는 YYMMWW(PLAN §3 #12)."""
    c = code.strip()
    if len(c) != 6 or not c.isdigit():
        return "unknown"
    if c.startswith("20") and 1 <= int(c[4:6]) <= 12:
        return "YYYYMM"
    if 1 <= int(c[2:4]) <= 12 and 1 <= int(c[4:6]) <= 5:
        return "YYMMWW"
    return "unknown"


@dataclass
class BurstLevel:
    target_rps: int
    sent: int
    ok: int
    rate_limited: int
    other_error: int
    wall_s: float

    @property
    def achieved_rps(self) -> float:
        return self.sent / self.wall_s if self.wall_s > 0 else 0.0


def max_clean_rps(levels: list[BurstLevel]) -> int | None:
    """한도 초과 응답이 한 건도 없던 가장 높은 목표 초당 호출 수.

    더 낮은 단계에서 한 번이라도 걸렸으면 그 위는 믿지 않는다.
    """
    best: int | None = None
    for lv in sorted(levels, key=lambda x: x.target_rps):
        if lv.rate_limited or lv.other_error:
            break
        best = lv.target_rps
    return best


def last_business_days(today: date, n: int) -> list[date]:
    """오늘 이전 평일 n 개 (휴장일 무시 — 빈 응답이면 호출자가 다음 날짜로 넘어간다)."""
    out: list[date] = []
    d = today
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def rows(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    v = body.get(key)
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(v, dict):
        return [v]  # pyright: ignore[reportUnknownVariableType]
    return []


def brief(
    resp: KisResponse, keys: tuple[str, ...] = ("output", "output1", "output2")
) -> dict[str, Any]:
    """응답 요약: 상태, 메시지, 출력별 행 수·필드·앞 2행."""
    d: dict[str, Any] = {
        "http": resp.status,
        "rt_cd": resp.rt_cd,
        "msg_cd": resp.msg_cd,
        "msg1": resp.body.get("msg1"),
        "tr_cont": resp.tr_cont,
        "elapsed_ms": round(resp.elapsed_ms, 1),
    }
    for k in keys:
        if k in resp.body:
            rs = rows(resp.body, k)
            d[k] = {"rows": len(rs), "fields": sorted(rs[0].keys()) if rs else [], "head": rs[:2]}
    if "_text" in resp.body:
        d["_text"] = resp.body["_text"]
    return d


@dataclass
class Ctx:
    settings: Settings
    kis: KisClient
    findings: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    _last_call: float = 0.0

    def call(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        min_gap: float = 1.05,
        tr_cont: str = "",
    ) -> KisResponse:
        """probe 기본 호출. 전광판 권장(1초 1건)을 지키도록 간격을 둔다."""
        wait = self._last_call + min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        return self.kis.get(path, tr_id, params, tr_cont=tr_cont)

    def note(self, line: str) -> None:
        self.notes.append(line)


def find_key(row: dict[str, Any], *needles: str) -> str | None:
    for k in row:
        if all(n in k for n in needles):
            return k
    return None


def write_result(name: str, ctx: Ctx, started: datetime, error: str | None) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "probe": name,
        "started_kst": started.isoformat(),
        "session_by_clock": session_of(started),
        "error": error,
        "notes": ctx.notes,
        "findings": ctx.findings,
    }
    text = redact(json.dumps(result, ensure_ascii=False, indent=2, default=str), ctx.kis)
    (OUT_DIR / f"{name}.json").write_text(text, encoding="utf-8")
    return json.loads(text)


MASTER_URL = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"


def futures_codes(ctx: Ctx) -> list[str]:
    """코스피200 선물 단축코드(근월물 우선). 선물 전광판 → 실패 시 KIS 마스터 파일."""
    r = ctx.call(
        P_FUT_BOARD,
        TR_FUT_BOARD,
        {
            "FID_COND_MRKT_DIV_CODE": "F",
            "FID_COND_SCR_DIV_CODE": "20503",
            "FID_COND_MRKT_CLS_CODE": "",
        },
    )
    ctx.findings.setdefault("futures_board", brief(r))
    codes: list[str] = []
    for row in rows(r.body, "output"):
        k = find_key(row, "shrn_iscd")
        if k and row.get(k):
            codes.append(str(row[k]).strip())
    if codes:
        return codes
    return _master_codes(ctx)


def _master_codes(ctx: Ctx) -> list[str]:
    import io
    import zipfile

    import httpx

    raw = httpx.get(MASTER_URL, timeout=40).content
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    found: list[tuple[int, str]] = []
    # 형식(2026 개편 후 실측): '1|A01612|KR4A016C0004|F 202612| |...|1|2001|KOSPI200'
    for ln in text.splitlines():
        f = ln.split("|")
        if len(f) > 8 and f[0] == "1" and f[8].strip() == "KOSPI200" and f[6].strip().isdigit():
            found.append((int(f[6]), f[1].strip()))
    found.sort()
    ctx.note(f"선물코드를 마스터 파일에서 얻음: {[c for _, c in found]}")
    return [c for _, c in found]
