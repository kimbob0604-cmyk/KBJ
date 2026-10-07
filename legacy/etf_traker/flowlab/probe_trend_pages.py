#!/usr/bin/env python3
"""
대체 주소의 **깊이**를 잰다. 그리고 board 시장 전체 표가 정말 사는지 제대로 본다.

3차에서 대체 주소를 찾았다.
  https://m.stock.naver.com/api/stock/{code}/trend  → 200 · JSON 배열
  {"bizdate":"20260916","foreignerPureBuyQuant":"+296,424",
   "organPureBuyQuant":"+85,620","individualPureBuyQuant":"-384,210",
   "closePrice":"40,550","accumulatedTradingVolume":"1,795,331",
   "foreignerHoldRatio":"31.13%", ...}   (KBJ P1: 모양만 원문, 값은 합성)

그런데 응답이 3,436바이트뿐이라 **며칠치인지** 모른다. flowlab 은 20일 창을
쓰고 as_of 로 자른 뒤에도 창이 남아야 해서 60거래일 안팎이 필요하다. 한 번에
며칠을 주는지, 페이지·개수 파라미터가 먹는지 확정해야 수집기를 제대로 짤 수 있다.
**이걸 모르고 짜면 20일 창이 조용히 반쪽이 된다.**

같이 바로잡는 것 — 3차에서 board 시장 전체 표를 `bizdate=` 를 비워 찔렀다가
0행을 받았는데, 그건 내 요청이 잘못된 것이지 표가 죽었다는 증거가 아니다.
여기서는 board 자신의 fetch_market 을 그대로 불러 확인한다.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CODE = "003490"
BASE = f"https://m.stock.naver.com/api/stock/{CODE}/trend"
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Referer": f"https://stock.naver.com/domestic/stock/{CODE}/investmentinfo",
    "Accept": "application/json, text/plain, */*",
}


def hr(t):
    print()
    print("─" * 72)
    print(t)
    print("─" * 72)


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def dates(js):
    return [x.get("bizdate") for x in js if isinstance(x, dict)]


def main():
    hr("기본 응답 — 한 번에 며칠을 주나")
    base = get(BASE)
    d = dates(base)
    print(f"  {len(base)}건 · {d[-1]} ~ {d[0]}" if d else "  비었다")
    print(f"  키 {sorted(base[0])}" if base else "")

    hr("파라미터가 먹는지 — 페이지·개수")
    tries = [
        "?pageSize=60", "?size=60", "?count=60", "?limit=60",
        "?page=2", "?pageSize=20&page=2", "?bizdate=20260901",
    ]
    for q in tries:
        try:
            js = get(BASE + q)
        except urllib.error.HTTPError as e:
            print(f"  [{e.code}] {q}")
            continue
        except Exception as e:                              # noqa: BLE001
            print(f"  [err] {q} — {type(e).__name__}: {str(e)[:50]}")
            continue
        dd = dates(js)
        same = (dd == d)
        print(f"  [200] {q:24} → {len(js)}건"
              + (f" · {dd[-1]} ~ {dd[0]}" if dd else "")
              + ("  (기본과 동일 — 안 먹는다)" if same else "  ★ 달라졌다"))

    hr("board 시장 전체 표 — 이번엔 board 자신의 함수로")
    try:
        from board.ingest import flows as bf
        for m in ("KOSPI", "KOSDAQ"):
            try:
                r = bf.fetch_market(m)
                by = r["by_date"]
                last = sorted(by)[-1] if by else None
                print(f"  {m}: {len(by)}일 · 최신 {last} · 구분 {len(r['columns'])}개")
                print(f"      {r['columns'][:6]}")
                if last:
                    print(f"      {last} → {by[last]}")
            except Exception as e:                          # noqa: BLE001
                print(f"  {m}: 실패 — {type(e).__name__}: {str(e)[:160]}")
    except Exception as e:                                  # noqa: BLE001
        print(f"  import 실패: {e}")

    hr("board 종목별(fetch_stock) — 같은 증상인지 확인")
    try:
        from board.ingest import flows as bf
        r = bf.fetch_stock("005930")
        print(f"  성공 {len(r['by_date'])}일 — 예상 밖이다")
    except Exception as e:                                  # noqa: BLE001
        print(f"  실패(예상대로): {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    main()
