#!/usr/bin/env python3
"""
단위를 **거래대금으로 검산한다**. 1차에서 막힌 자리를 다른 길로 연다.

## 1차가 왜 막혔나

`m.stock.naver.com/api/index/KOSPI/trend` 는 응답이 오는데 단위 표기가 없다.
보관된 보드와 같은 날짜를 맞대려 했으나 **당일치만 준다**(파라미터 7종 전부
103바이트·1일치). 오늘 보드에는 수급이 없으니(소스가 끊긴 날) 맞댈 상대가
영영 생기지 않는다.

공식 소스도 닫혔다. KRX 오픈API 는 대조군(`stk_bydd_trd`)이 200 인데 투자자별
후보 10개가 전부 404 — 그 포털에 그 서비스가 없다. 공공데이터는 대조군까지
403(`SERVICE_KEY_IS_NOT_REGISTERED_ERROR`)이라 **경로가 아니라 키 문제**였는데,
1차 진단이 `key_forms()[0]` 만 써서 그렇다. datago 는 두 형태를 눌러 보는
`call()` 이 따로 있다 — 진단이 그걸 안 쓴 것이 결함이다.

## 이번에 여는 두 길

**A. 응답 원문을 통째로 찍는다.** 1차는 키 세 개(personal·foreign·
institutional)만 파싱했다. 단위 힌트나 다른 필드가 있는데 못 본 것일 수 있다.
고를 것이 아니라 있는 그대로 본다.

**B. 같은 API 계열에서 거래대금을 찾는다.** 이게 결정적이다. 보드는 코스피
거래대금을 이미 알고 있다(네이버 시세에서 받아 `universe.json` 에 억원으로
들어 있다). 형제 엔드포인트가 거래대금을 **같은 단위로** 주면, 둘의 비가 곧
환산 배수다. 크기 짐작이 아니라 아는 값으로 모르는 값을 나누는 것이다.

**C. 공공데이터는 datago.call 을 그대로 쓴다.** 진단이 본 코드와 다른 방식으로
부르면 무엇을 확인한 것인지 알 수 없다.

**아무것도 고치지 않는다.**
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/domestic/index/KOSPI/total",
}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read(20000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(500)
    except Exception as e:                       # noqa: BLE001
        return None, f"{type(e).__name__}: {e}".encode()


def section(t):
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def a_raw():
    section("A. trend 응답 원문 — 고르지 않고 있는 그대로")
    for mkt in ("KOSPI", "KOSDAQ"):
        url = f"https://m.stock.naver.com/api/index/{mkt}/trend"
        st, body = fetch(url)
        print(f"\n  [{st}] {url}  ({len(body)}B)")
        print(f"  {body.decode('utf-8', 'replace')}")


def b_siblings():
    section("B. 형제 엔드포인트 — 거래대금을 같은 단위로 주는 곳을 찾는다")
    tails = ["basic", "integration", "overall", "total", "price", "summary",
             "marketValue", "tradingValue", "investor", "index"]
    money = ("VALUE", "AMOUNT", "AMT", "TRADING", "TRDVAL", "ACCUMULATED")
    for mkt in ("KOSPI",):
        for t in tails:
            url = f"https://m.stock.naver.com/api/index/{mkt}/{t}"
            st, body = fetch(url)
            if st != 200:
                print(f"  {st!s:>5}  /{t}")
                continue
            try:
                js = json.loads(body)
            except Exception:                    # noqa: BLE001
                print(f"  {st:>5}  /{t}  JSON 아님 {len(body)}B")
                continue
            d = js[0] if isinstance(js, list) and js else js
            keys = sorted(d) if isinstance(d, dict) else []
            print(f"  {st:>5}  /{t}  {len(body)}B · 키 {keys[:14]}")
            hit = {k: d[k] for k in keys
                   if any(w in k.upper() for w in money)}
            if hit:
                print(f"         ★ 금액성 키 {json.dumps(hit, ensure_ascii=False)[:400]}")


def b_compare():
    section("B-2. 보드가 아는 코스피 거래대금과 맞댄다 — 배수가 곧 단위")
    # 보드는 universe.json 에 종목별 거래대금을 억원으로 갖고 있다. 코스피만
    # 합치면 그날 코스피 거래대금이다. state 가 러너에 복원돼 있지 않을 수
    # 있으므로 없으면 그 사실을 적는다.
    import glob
    cands = sorted(glob.glob(str(ROOT / "board" / "state" / "*" / "universe.json")))
    if not cands:
        print("  board/state 에 universe.json 이 없다 — 이 러너에는 캐시가 없다.")
        print("  (frgn-probe 워크플로는 DB·state 를 복원하지 않는다. 그래서")
        print("   여기서는 못 맞댄다 — 필요하면 board.yml 쪽에서 돌려야 한다.)")
        return
    p = cands[-1]
    with open(p, encoding="utf-8") as f:
        uni = json.load(f)
    tot = sum(x.get("turnover") or 0 for x in uni.get("stocks") or []
              if (x.get("market") or "").upper().startswith("KOSPI"))
    print(f"  {p.split('/')[-2]} 코스피 거래대금 합계 = {tot:,.0f}억원 (universe.json)")
    st, body = fetch("https://m.stock.naver.com/api/index/KOSPI/trend")
    if st == 200:
        print(f"  같은 날 trend 원문: {body.decode('utf-8', 'replace')}")
        print("  → 거래대금과 순매수는 다른 값이므로 직접 나누면 안 된다.")
        print("    다만 순매수 절대값이 거래대금을 넘으면 단위가 억원일 수 없다.")


def c_datago():
    section("C. 공공데이터 — datago.call 을 그대로 쓴다 (키 형태 둘 다)")
    try:
        from board.ingest import datago as DG
        from board.ingest.http import session
    except Exception as e:                       # noqa: BLE001
        print(f"  import 실패: {type(e).__name__}: {e}")
        return
    try:
        forms = DG.key_forms()
    except Exception as e:                       # noqa: BLE001
        print(f"  키를 못 읽었다: {e}")
        return
    print(f"  키 형태 {len(forms)}가지 — {[f[0] for f in forms]}")
    s = session()
    import datetime as dt
    d = dt.date.today() - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    bas = d.strftime("%Y%m%d")
    base = "https://apis.data.go.kr/1160100/service"
    for svc, op in (("GetStockSecuritiesInfoService", "getStockPriceInfo"),
                    ("GetStockSecuritiesInfoService", "getStockInvestorInfo"),
                    ("GetInvestorInfoService", "getInvestorInfo"),
                    ("GetMarketInvestorService", "getMarketInvestor")):
        url = f"{base}/{svc}/{op}"
        try:
            js = DG.call(s, url, {"resultType": "json", "numOfRows": 3,
                                  "pageNo": 1, "basDt": bas})
            body = js.get("response", js) if isinstance(js, dict) else js
            print(f"  OK     {svc}/{op}")
            print(f"         {json.dumps(body, ensure_ascii=False)[:300]}")
        except Exception as e:                   # noqa: BLE001
            print(f"  실패   {svc}/{op} — {type(e).__name__}: {str(e)[:150]}")


def main():
    a_raw()
    b_siblings()
    b_compare()
    c_datago()
    section("읽는 법")
    print("  A 원문에 단위 힌트 필드가 있으면 거기서 끝난다.")
    print("  B 에서 거래대금을 주는 형제가 있으면, 보드가 아는 억원 값과의 비가")
    print("    곧 이 API 의 환산 배수다 — 순매수도 같은 배수를 쓴다고 보면 된다.")
    print("  C 대조군이 통하면 공공데이터 키는 멀쩡하다는 뜻이고, 투자자별만")
    print("    NO_OPENAPI_SERVICE_ERROR 면 그 포털에 그 서비스가 없는 것이다.")


if __name__ == "__main__":
    main()
