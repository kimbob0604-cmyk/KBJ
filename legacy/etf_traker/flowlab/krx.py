"""공매도 — KRX 계정이 있을 때만 동작한다.

data.krx.co.kr 은 로그인 없이 JSON 을 주지 않는다(모든 엔드포인트가 LOGOUT 반환).
pykrx 의 투자자별·공매도 함수도 같은 이유로 빈 표를 돌려준다.
KRX Data Marketplace OpenAPI 는 지수·주식·채권·파생만 제공하고 공매도가 없다.

board/.env 에 아래를 넣으면 활성화된다.

    KRX_ID=아이디
    KRX_PW=비밀번호

계정이 없으면 아무 값도 만들지 않고 그 사실만 남긴다.
"""
from __future__ import annotations

import os

from . import config as C

LOGIN_URL = "https://data.krx.co.kr/comm/login/loginProcess.cmd"
GEN_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
SHORT_BLD = "dbms/MDC/STAT/srt/MDCSTAT30101"   # 공매도 종목별 거래대금


def creds() -> tuple[str | None, str | None]:
    C.load_env()
    return os.environ.get("KRX_ID"), os.environ.get("KRX_PW")


def available() -> bool:
    a, b = creds()
    return bool(a and b)


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": C.USER_AGENT,
                      "Referer": "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"})
    uid, pw = creds()
    r = s.post(LOGIN_URL, data={"userId": uid, "userPw": pw}, timeout=C.HTTP_TIMEOUT)
    r.raise_for_status()
    if "LOGOUT" in r.text.upper() and "LOGIN" not in r.text.upper():
        raise RuntimeError("KRX 로그인 실패 — 계정·비밀번호 확인")
    return s


def shorts(as_of: str) -> dict[str, dict]:
    """{종목코드: {short_value_eok, short_ratio_pct}} — 실패하면 예외를 올린다."""
    import pandas as pd
    s = _session()
    ymd = str(as_of).replace("-", "")
    r = s.post(GEN_URL, data={"bld": SHORT_BLD, "locale": "ko_KR", "trdDd": ymd,
                              "mktId": "ALL", "share": "1", "money": "1"},
               timeout=C.HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    rows = js.get("OutBlock_1") or js.get("output") or []
    if not rows:
        raise RuntimeError(f"KRX 공매도 응답 비어 있음 ({as_of}) — 로그인 상태 확인")
    df = pd.DataFrame(rows)
    out = {}
    for _, x in df.iterrows():
        code = str(x.get("ISU_SRT_CD") or "").strip()
        if not code:
            continue

        def num(k):
            v = str(x.get(k, "")).replace(",", "")
            try:
                return float(v)
            except ValueError:
                return None
        val, tot = num("CVSRTSELL_TRDVAL"), num("ACC_TRDVAL")
        out[code] = {
            "short_value_eok": round(val / 1e8, 1) if val is not None else None,
            "short_ratio_pct": (round(val / tot * 100, 1)
                                if val is not None and tot else None),
        }
    return out


def enrich(rows: list[dict], as_of: str, log=print) -> dict:
    """flows 행에 공매도를 붙인다. 계정이 없거나 실패하면 붙이지 않는다."""
    if not available():
        return {"available": False,
                "note": "KRX 계정 없음 — board/.env 에 KRX_ID/KRX_PW 를 넣으면 활성화"}
    try:
        table = shorts(as_of)
    except Exception as e:
        log(f"  공매도 실패: {type(e).__name__}: {e}")
        return {"available": False, "note": f"KRX 호출 실패: {type(e).__name__}: {e}"}
    hit = 0
    for r in rows:
        s = table.get(str(r.get("code")))
        if not s:
            continue
        r.update(s)
        hit += 1
    return {"available": True, "as_of": as_of, "attached": hit, "source": "KRX"}
