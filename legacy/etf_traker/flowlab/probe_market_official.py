#!/usr/bin/env python3
"""
시장 전체 투자자별 수급을 **공식 API** 에서 받을 수 있는지 본다.

## 왜 네이버로 안 끝내나

probe_market_flows 가 대체 주소를 찾았다 —
`m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/trend` 가 200 을 준다. 그런데
probe_market_unit 이 두 가지를 확인하면서 막혔다.

  1. **당일치만 준다.** pageSize·page·size·count·limit 일곱 가지를 다 걸어도
     응답이 103바이트·1일치다. 종목별(`stock/{code}/trend`)은 pageSize 가
     먹었는데 지수는 안 먹는다.
  2. **그래서 단위를 검산할 수 없다.** 보관된 보드와 같은 날짜를 맞대려 했는데,
     새 주소는 오늘 값만 주고 오늘 보드에는 수급이 없다(소스가 끊긴 날이라
     '소스 미확보' 로 비어 있다). 어제 이전 값은 새 주소가 아예 안 준다.
     **영영 못 맞댄다** — 시간이 지나도 이 대조는 성립하지 않는다.

크기만 보고 억원이라 찍으면 안 된다. 예전에 그렇게 기본값을 골랐다가 100배
어긋난 값이 성공으로 나갔다(`flows.fetch_market` 의 단위 분기가 그 교훈이다).

## 그래서 무엇을 보나

**단위가 문서로 정해져 있는 소스**를 찾는다. 둘 다 인증키가 이미 통한다.

  1. **KRX 오픈API** (`data-dbg.krx.co.kr`) — `stk_bydd_trd` 가 2,686종목을
     주고 있다(D-011). 투자자별 거래실적 경로가 있으면 그게 근본 해결이다.
     공식 API 라 네이버처럼 하루아침에 410 이 되지 않는다.
  2. **공공데이터포털** (`apis.data.go.kr/1160100`) — 시세·지수가 통한다.
     같은 서비스군에 투자자별이 있는지 본다.

찾으면 네이버 대체 주소는 필요 없다. 못 찾으면 네이버를 쓰되 그 값을 무엇으로
검산할지 여기 결과를 보고 정한다.

**아무것도 고치지 않는다.**
"""
import datetime as dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) probe/1.0",
      "Accept": "application/json, */*"}


def last_bizday(back=1):
    d = dt.date.today() - dt.timedelta(days=back)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(6000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(600)
    except Exception as e:                       # noqa: BLE001
        return None, f"{type(e).__name__}: {e}".encode()


def brief(body, n=240):
    return body[:n].decode("utf-8", "replace").replace("\n", " ")


def show_json(body):
    """응답에서 투자자 관련 키를 찾아 보여 준다. 없으면 키 목록만."""
    try:
        js = json.loads(body)
    except Exception:                            # noqa: BLE001
        print(f"         JSON 아님 · {brief(body, 160)}")
        return
    rows = js
    for k in ("OutBlock_1", "OutBlock", "body", "response", "items", "item"):
        if isinstance(rows, dict) and k in rows:
            rows = rows[k]
    if isinstance(rows, dict):
        print(f"         객체 키 {sorted(rows)[:16]}")
        rows = rows.get("items") or rows.get("item") or []
    if isinstance(rows, list) and rows:
        first = rows[0]
        keys = sorted(first) if isinstance(first, dict) else []
        print(f"         배열 {len(rows)}건 · 첫 건 키 {keys[:18]}")
        hit = [k for k in keys
               if any(w in k.upper() for w in
                      ("INVR", "FORN", "ORGN", "PRSN", "INVEST", "NTBY", "TRDVAL"))]
        if hit:
            print(f"         ★ 투자자 관련 키 {hit}")
            print(f"         {json.dumps({k: first[k] for k in hit[:8]}, ensure_ascii=False)[:280]}")
    elif isinstance(rows, list):
        print("         배열 0건")


def krx():
    print("=" * 78)
    print("1. KRX 오픈API — 투자자별 거래실적 경로")
    print("=" * 78)
    try:
        from board.ingest import creds
        key = creds.get("KRX_API_KEY")
    except Exception as e:                       # noqa: BLE001
        print(f"  자격증명을 못 읽었다: {type(e).__name__}")
        return
    if not key:
        print("  KRX_API_KEY 가 없다 — 건너뛴다")
        return
    print("  키 있음 (값은 찍지 않는다)")
    base = "https://data-dbg.krx.co.kr/svc/apis"
    bas = last_bizday()
    paths = [
        # 대조군 — 이건 통하는 걸 안다. 통하면 키·헤더가 맞다는 뜻이다.
        "/sto/stk_bydd_trd",
        # 투자자별로 있을 법한 이름들. 짐작이므로 폭넓게 건다.
        "/sto/stk_invr_trd", "/sto/stk_bydd_invr_trd", "/sto/stk_isu_invr_trd",
        "/sto/ksq_invr_trd", "/sto/ksq_bydd_invr_trd",
        "/inv/stk_invr_trd", "/inv/invr_trd", "/inv/bydd_invr_trd",
        "/sto/stk_invr_dd_trd", "/sto/mkt_invr_trd",
    ]
    for p in paths:
        url = f"{base}{p}?basDd={bas}"
        st, body = fetch(url, {"AUTH_KEY": key})
        print(f"  {st!s:>5}  {p}")
        if st == 200:
            show_json(body)
        else:
            print(f"         {brief(body, 150)}")


def datago():
    print()
    print("=" * 78)
    print("2. 공공데이터포털 — 같은 서비스군에 투자자별이 있는가")
    print("=" * 78)
    try:
        from board.ingest import datago as DG
        forms = DG.key_forms()
    except Exception as e:                       # noqa: BLE001
        print(f"  키 형태를 못 만들었다: {type(e).__name__}: {e}")
        return
    if not forms:
        print("  DATAGO_KEY 가 없다 — 건너뛴다")
        return
    print(f"  키 형태 {len(forms)}가지 (값은 찍지 않는다)")
    base = "https://apis.data.go.kr/1160100/service"
    bas = last_bizday()
    cands = [
        # 대조군
        ("GetStockSecuritiesInfoService", "getStockPriceInfo"),
        # 투자자별로 있을 법한 서비스·오퍼레이션
        ("GetStockSecuritiesInfoService", "getStockInvestorInfo"),
        ("GetInvestorInfoService", "getInvestorInfo"),
        ("GetMarketInvestorService", "getMarketInvestor"),
        ("GetStockMarketInvestorService", "getStockMarketInvestor"),
        ("GetMarketIndexInfoService", "getStockMarketIndex"),
    ]
    key = forms[0]
    for svc, op in cands:
        q = urllib.parse.urlencode(
            {"serviceKey": key, "resultType": "json", "numOfRows": 5,
             "pageNo": 1, "basDt": bas}, safe="%")
        url = f"{base}/{svc}/{op}?{q}"
        st, body = fetch(url)
        print(f"  {st!s:>5}  {svc}/{op}")
        if st == 200:
            show_json(body)
        else:
            print(f"         {brief(body, 150)}")


def main():
    print(f"기준일 후보 — 직전 영업일 {last_bizday()}")
    print()
    krx()
    datago()
    print()
    print("=" * 78)
    print("읽는 법 — ★ 가 붙은 줄이 있으면 그 경로가 투자자별을 준다.")
    print("          대조군(stk_bydd_trd · getStockPriceInfo)이 200 인데 나머지가")
    print("          전부 404 면, 그 포털에는 투자자별 서비스가 없는 것이다.")
    print("          대조군까지 막히면 키·헤더 문제지 경로 문제가 아니다.")
    print("=" * 78)


if __name__ == "__main__":
    main()
