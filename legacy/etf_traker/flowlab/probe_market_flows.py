#!/usr/bin/env python3
"""
시장 전체 투자자별 수급 — 없어진 주소를 대신할 **실제로 응답하는** 주소를 찾는다.

## 왜 지금 이걸 찾나

KIS 쪽은 D-081 에서 닫혔다. `inquire-investor-daily-by-market` 은 파라미터를
어떻게 흔들어도(24조합) rt_cd=0 · 300행 · 순매수 칼럼 전부 0 이다. 지수 시세는
정확하고 필드 이름도 맞으니 코드로 좁힐 곳이 없다 — 남은 것은 KIS 권한이거나
다른 소스다. 이 파일이 '다른 소스' 쪽이다.

그런데 네이버 폴백도 죽었다. 2026-09-17 daily 가 남긴 사유가 그대로다.

    https://finance.naver.com/sise/investorDealTrendDay.naver 실패: HTTP 410

D-079 가 **종목별**에서 겪은 것과 같은 꼴이다(302 → SPA → 표 없음). 다만 그때
같은 러너에서 시장 전체 표는 살아 있었다(`flows.py` 주석에 그렇게 적혀 있다).
하루 이틀 사이에 바뀐 것이다.

## 무엇을 가리려는 건가

410 은 "없어졌다" 는 뜻이지만 네이버는 헤더가 부족할 때도 이상한 코드를 준다.
그래서 두 갈래를 함께 본다.

  1. **헤더 문제인가** — 같은 주소를 UA·Referer 조합을 바꿔 가며 부른다.
     맨 요청만 막히는 것이면 헤더만 고치면 된다.
  2. **주소가 옮겨갔나** — 신 증권(Next.js)이 값을 받아 오는 JSON 후보를 훑는다.
     종목별이 `m.stock.naver.com/api/stock/{code}/trend` 로 옮겨간 것과 같은 꼴을
     지수/시장 쪽에서 찾는다.

**아무것도 고치지 않고 아무 데도 쓰지 않는다.** 되는 주소와 JSON 모양이 확정된
뒤에 `board/ingest/flows.py` 를 고친다 — 짐작으로 코드에 박지 않는다.
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

OLD = "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=&sosok=01"

# 1) 같은 주소, 헤더만 바꾼다. 410 이 정말 '없어짐' 인지 '막힘' 인지 가른다.
HEADER_SETS = [
    ("맨 요청 (UA 없음)", {}),
    ("UA 만", {"User-Agent": UA_BROWSER}),
    ("UA + Referer(finance)", {"User-Agent": UA_BROWSER,
                               "Referer": "https://finance.naver.com/sise/"}),
    ("UA + Referer + Accept", {"User-Agent": UA_BROWSER,
                               "Referer": "https://finance.naver.com/sise/",
                               "Accept": "text/html,application/xhtml+xml"}),
]

# 2) 옮겨 갔을 만한 주소. 종목별이 m.stock.naver.com/api/stock/{code}/trend 로
#    갔으므로 지수·시장 쪽의 같은 꼴을 폭넓게 건다. 지어내지 않고 눌러 본다.
JSON_HEADERS = {"User-Agent": UA_BROWSER,
                "Referer": "https://m.stock.naver.com/domestic/index/KOSPI/total",
                "Accept": "application/json, text/plain, */*"}
CANDIDATES = []
for idx in ("KOSPI", "KOSDAQ"):
    CANDIDATES += [
        f"https://m.stock.naver.com/api/index/{idx}/trend",
        f"https://m.stock.naver.com/api/index/{idx}/investor",
        f"https://m.stock.naver.com/api/index/{idx}/investorTrend",
        f"https://m.stock.naver.com/api/index/{idx}/investorDealTrend",
        f"https://api.stock.naver.com/index/{idx}/trend",
        f"https://api.stock.naver.com/index/{idx}/investor",
        f"https://api.stock.naver.com/index/{idx}/investorTrend",
        f"https://api.stock.naver.com/chart/domestic/index/{idx}/investor",
    ]
# 구 주소 계열도 같이 본다. .nhn 이 아직 살아 있는 페이지가 네이버에 남아 있다.
CANDIDATES += [
    "https://finance.naver.com/sise/investorDealTrendDay.nhn?bizdate=&sosok=01",
    "https://finance.naver.com/sise/investorDealTrendTime.naver?bizdate=&sosok=01",
    "https://finance.naver.com/sise/sise_deal_trend.naver?sosok=01",
]

HINTS = ("foreign", "organ", "individual", "institution", "frgn", "invest",
         "netPurchase", "netBuy", "pureBuy", "외국", "기관", "개인")


def hr(t):
    print()
    print("─" * 72)
    print(t)
    print("─" * 72)


def fetch(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def walk_keys(obj, depth=0, out=None):
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


def try_url(url, headers, label=""):
    """부르고 결과를 한 줄로 찍는다. JSON 이면 투자자 관련 키를 찾아 준다."""
    tag = f"{label} " if label else ""
    try:
        st, ct, body = fetch(url, headers)
    except urllib.error.HTTPError as e:
        # 본문을 함께 찍는다. 410 인데 본문이 로그인 안내면 뜻이 다르다.
        try:
            head = e.read()[:160].decode("utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            head = ""
        print(f"  [{e.code}] {tag}{url}\n        {head!r}")
        return None
    except Exception as e:                                  # noqa: BLE001
        print(f"  [err] {tag}{url} — {type(e).__name__}: {str(e)[:70]}")
        return None
    print(f"  [{st}] {tag}{url}\n        {ct} · {len(body):,}B")
    if "json" in ct.lower():
        try:
            js = json.loads(body.decode("utf-8"))
        except Exception as e:                              # noqa: BLE001
            print(f"        JSON 파싱 실패: {e}")
            return None
        keys = walk_keys(js)
        rel = sorted(k for k in keys
                     if any(h.lower() in str(k).lower() for h in HINTS))
        if rel:
            print(f"        ★ 투자자 관련 키 {rel[:14]}")
            return js
        print(f"        (투자자 관련 키 없음) 키 표본 {sorted(keys)[:12]}")
        return None
    # HTML 이면 표가 있는지 본다. 표가 0개면 SPA 로 갈아탄 것이다 (D-079 와 같은 꼴).
    txt = body.decode("euc-kr", errors="replace")
    n_tr = len(re.findall("<tr", txt, re.I))
    print(f"        tr {n_tr} · td {len(re.findall('<td', txt, re.I))} · "
          f"table {len(re.findall('<table', txt, re.I))}")
    if n_tr:
        try:
            from board.ingest import flows as bf
            rows = bf._table_rows(txt)
            print(f"        board _table_rows → {len(rows)}행"
                  + (f" · 첫 행 {rows[0]}" if rows else ""))
            if rows:
                return txt
        except Exception as e:                              # noqa: BLE001
            print(f"        board 파서 실패: {type(e).__name__}: {str(e)[:70]}")
    return None


def main():
    hr("1. 410 이 '없어짐' 인가 '막힘' 인가 — 같은 주소, 헤더만 바꾼다")
    header_hit = None
    for label, h in HEADER_SETS:
        if try_url(OLD, h, f"[{label}]") is not None:
            header_hit = label
            break

    hr(f"2. 옮겨 간 주소 후보 {len(CANDIDATES)}개")
    hits = []
    for url in CANDIDATES:
        got = try_url(url, JSON_HEADERS)
        if got is not None:
            hits.append((url, got))

    for url, js in hits[:3]:
        hr(f"쓸 만한 응답 — {url}")
        if isinstance(js, str):
            print(js[:1200])
        else:
            print(json.dumps(js, ensure_ascii=False)[:1800])

    hr("결론")
    if header_hit:
        print(f"  헤더만 고치면 된다 — '{header_hit}' 조합에서 표가 왔다.")
        print("  board/ingest/flows.py 의 session() 헤더를 그 조합으로 맞춘다.")
    elif hits:
        print(f"  주소가 옮겨 갔다. 쓸 만한 응답 {len(hits)}개:")
        for url, _ in hits:
            print(f"    - {url}")
        print("  JSON 모양을 보고 flows.fetch_market 을 그 주소로 갈아탄다.")
    else:
        print("  후보 전부 실패. 네이버 쪽에는 대체 주소가 없다고 봐야 한다.")
        print("  남은 길은 KIS 권한(D-081) 이거나 유료/인증 소스다.")
        print("  **없는 값을 0 으로 채우지 않는다** — has_flows: false 를 유지한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
