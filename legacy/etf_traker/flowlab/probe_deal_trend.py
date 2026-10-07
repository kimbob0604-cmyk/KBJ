#!/usr/bin/env python3
"""
`integration` 의 `dealTrendInfo` 를 통째로 본다. 여기에 답이 있을 것이다.

2차 진단이 형제 엔드포인트를 훑다가 걸렸다.

    [200] https://m.stock.naver.com/api/index/KOSPI/integration  16,804B
          키 ['dealTrendInfo', 'programTrendInfo', 'totalInfos', 'upDownStockInfo', ...]

`dealTrendInfo` — 이름 그대로 매매동향이다. 없어진 옛 페이지 이름이
`investorDealTrendDay` 였으니 같은 것을 담고 있을 가능성이 높다. 2차가 이걸
놓친 이유는 내가 건 금액 키워드(VALUE·AMOUNT·TRADING…)에 'DEAL' 이 없어서다 —
**필터가 답을 가렸다.** 그래서 이번에는 거르지 않고 통째로 찍는다.

`totalInfos` 도 함께 본다. 이름으로 보아 지수·거래대금 요약이고, 거기 거래대금이
단위와 함께 들어 있으면 그것이 곧 `trend` 숫자의 단위를 정해 준다.

확인할 것 셋.

  1. dealTrendInfo 가 개인·외국인·기관을 주는가. **기타법인도 주는가** —
     `trend` 에는 셋뿐이라 머리말이 넷에서 셋으로 줄어야 하는데, 여기 넷이 다
     있으면 줄이지 않아도 된다.
  2. 며칠치인가. `trend` 는 당일치뿐이었다.
  3. 단위를 알 수 있는 필드가 있는가. 없으면 totalInfos 의 거래대금과
     보드가 아는 값을 맞대는 길이 남는다.

**아무것도 고치지 않는다.**
"""
import json
import sys
import urllib.error
import urllib.request

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
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(400000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(500)
    except Exception as e:                       # noqa: BLE001
        return None, f"{type(e).__name__}: {e}".encode()


def dump(label, obj, limit=6000):
    print(f"\n  ── {label} ──")
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    if len(s) <= limit:
        print("  " + s.replace("\n", "\n  "))
    else:
        print("  " + s[:limit].replace("\n", "\n  "))
        print(f"  … (전체 {len(s):,}자 중 앞 {limit:,}자)")


def main():
    for mkt in ("KOSPI", "KOSDAQ"):
        url = f"https://m.stock.naver.com/api/index/{mkt}/integration"
        st, body = fetch(url)
        print("=" * 78)
        print(f"{mkt} integration — [{st}] {len(body):,}B")
        print("=" * 78)
        if st != 200:
            print(f"  {body[:300].decode('utf-8', 'replace')}")
            continue
        try:
            js = json.loads(body)
        except Exception as e:                   # noqa: BLE001
            print(f"  JSON 아님: {e}")
            continue
        print(f"  최상위 키 {sorted(js)}")
        for k in ("dealTrendInfo", "totalInfos", "programTrendInfo"):
            if k in js:
                dump(f"{mkt}.{k}", js[k])
            else:
                print(f"\n  ── {mkt}.{k} — 없다")
        # 남은 키도 이름만 적어 둔다. 다음에 또 필터로 가리지 않으려고.
        rest = [k for k in sorted(js)
                if k not in ("dealTrendInfo", "totalInfos", "programTrendInfo")]
        print(f"\n  나머지 키(내용 생략) {rest}")
        print()


if __name__ == "__main__":
    main()
