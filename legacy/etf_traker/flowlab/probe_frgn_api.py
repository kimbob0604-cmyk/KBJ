#!/usr/bin/env python3
"""
사라진 frgn 표를 대신할 **실제로 응답하는** 주소를 찾는다. 일회성 점검이다.

2차 점검이 원인을 확정했다.
  finance.naver.com/item/frgn.naver
    → 302 → stock.naver.com/domestic/stock/{code}/investmentinfo
    Next.js SPA("Npay 증권") · 119,822바이트 · script 113개 · **tr/td/table 0개**
표를 담은 HTML 이 더는 존재하지 않는다. 파서를 어떻게 고쳐도 읽을 것이 없다.

그러면 그 화면이 값을 어디서 받아 그리는지를 찾아야 한다. 후보를 **짐작으로
코드에 박지 않고** 여기서 하나씩 찔러 응답을 찍는다. 되는 주소와 그 JSON 모양이
확정된 뒤에 수집기를 고친다.

같이 확인하는 것 — board 의 시장 전체 표(investorDealTrendDay)는 아직 사는가.
보드 머리말에 개인·외국인·기관계가 나오므로 살아 있을 것으로 보이지만,
같은 도메인이라 함께 죽었는지 확인해 둔다.

**아무것도 고치지 않는다.**
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CODE = "003490"
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Referer": f"https://stock.naver.com/domestic/stock/{CODE}/investmentinfo",
    "Accept": "application/json, text/plain, */*",
}

# 후보 주소. 네이버 신 증권(Next.js)이 쓰는 API 로 알려진 꼴들을 폭넓게 건다.
CANDIDATES = [
    f"https://api.stock.naver.com/stock/{CODE}/trend",
    f"https://api.stock.naver.com/stock/{CODE}/investor",
    f"https://api.stock.naver.com/stock/{CODE}/investors",
    f"https://api.stock.naver.com/stock/{CODE}/foreignInvestor",
    f"https://api.stock.naver.com/stock/{CODE}/frgn",
    f"https://m.stock.naver.com/api/stock/{CODE}/trend",
    f"https://m.stock.naver.com/api/stock/{CODE}/investor",
    f"https://m.stock.naver.com/api/stock/{CODE}/investors",
    f"https://m.stock.naver.com/api/stock/{CODE}/frgn",
    f"https://m.stock.naver.com/api/stock/{CODE}/investmentinfo",
    f"https://api.stock.naver.com/chart/domestic/item/{CODE}/investor",
]

# 시장 전체 — board 가 쓰는 주소. 아직 표가 오는지만 본다.
MARKET = ("https://finance.naver.com/sise/investorDealTrendDay.naver"
          "?bizdate=&sosok=01")


def hr(t):
    print()
    print("─" * 72)
    print(t)
    print("─" * 72)


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def walk_keys(obj, depth=0, out=None):
    """JSON 안에서 '투자자' 로 보이는 키를 찾는다. 구조를 모르니 훑는다."""
    out = out if out is not None else set()
    if depth > 4:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            walk_keys(v, depth + 1, out)
    elif isinstance(obj, list):
        for v in obj[:3]:
            walk_keys(v, depth + 1, out)
    return out


HINTS = ("foreign", "organ", "individual", "institution", "frgn", "invest",
         "netPurchase", "netBuy", "외국", "기관", "개인")


def main():
    hr(f"후보 주소 {len(CANDIDATES)}개 — {CODE}")
    hits = []
    for url in CANDIDATES:
        try:
            st, ct, body = get(url)
        except urllib.error.HTTPError as e:
            print(f"  [{e.code}] {url}")
            continue
        except Exception as e:                              # noqa: BLE001
            print(f"  [err] {url} — {type(e).__name__}: {str(e)[:60]}")
            continue
        head = body[:120].decode("utf-8", errors="replace")
        print(f"  [{st}] {url}")
        print(f"        {ct} · {len(body):,}B · {head!r}")
        if "json" not in ct.lower():
            continue
        try:
            js = json.loads(body.decode("utf-8"))
        except Exception as e:                              # noqa: BLE001
            print(f"        JSON 파싱 실패: {e}")
            continue
        keys = walk_keys(js)
        rel = sorted(k for k in keys
                     if any(h.lower() in str(k).lower() for h in HINTS))
        print(f"        최상위 {list(js)[:8] if isinstance(js, dict) else type(js)}")
        if rel:
            print(f"        ★ 투자자 관련 키 {rel[:14]}")
            hits.append((url, js))
        else:
            print(f"        (투자자 관련 키 없음) 키 표본 {sorted(keys)[:12]}")

    for url, js in hits:
        hr(f"쓸 만한 응답 — {url}")
        print(json.dumps(js, ensure_ascii=False)[:1600])

    hr("board 시장 전체 표 — investorDealTrendDay 는 아직 사는가")
    try:
        st, ct, body = get(MARKET, headers={"User-Agent": UA["User-Agent"],
                                            "Referer": "https://finance.naver.com/"})
        txt = body.decode("euc-kr", errors="replace")
        import re
        print(f"  [{st}] {ct} · {len(body):,}B")
        print(f"  tr {len(re.findall('<tr', txt, re.I))} · "
              f"td {len(re.findall('<td', txt, re.I))} · "
              f"table {len(re.findall('<table', txt, re.I))}")
        from board.ingest import flows as bf
        print(f"  shape: {bf.shape(txt)}")
        rows = bf._table_rows(txt)
        print(f"  board _table_rows → {len(rows)}행"
              + (f" · 첫 행 {rows[0]}" if rows else ""))
    except Exception as e:                                  # noqa: BLE001
        print(f"  실패: {type(e).__name__}: {e}")

    hr("요약")
    print(f"  응답한 투자자 API 후보 {len(hits)}개")
    for url, _ in hits:
        print(f"    {url}")
    if not hits:
        print("  하나도 없다 — 후보 목록을 넓히거나 SPA 의 네트워크 호출을 직접 잡아야 한다")


if __name__ == "__main__":
    main()
