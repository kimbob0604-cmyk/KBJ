#!/usr/bin/env python3
"""
새 주소의 **단위와 구분 이름**을 보관된 보드와 맞대어 확정한다.

probe_market_flows 가 대체 주소를 찾았다.

    https://m.stock.naver.com/api/index/KOSPI/trend
    {"bizdate":"20260917","personalValue":"+1,234",
     "foreignValue":"-5,678","institutionalValue":"+4,444"}   (KBJ P1: 모양만 원문, 값은 합성)

여기까지는 '응답이 온다' 는 사실뿐이다. 수집기를 짜려면 두 가지를 더 알아야 한다.

  1. **단위** — +1,234 가 억원인가 백만원인가. 옛 페이지는 머리말에 단위를
     적어 줘서 읽을 수 있었는데(`flows.fetch_market` 의 '백만' / '억원' 분기)
     이 JSON 에는 단위 표기가 없다. 크기만 보고 억원이라고 **짐작하면 안 된다** —
     예전에 그렇게 기본값을 골랐다가 100배 어긋난 값이 성공으로 나갔다.
  2. **며칠치를 주는가** — 응답이 102바이트뿐이라 하루로 보인다. 종목별
     (`stock/{code}/trend`)은 pageSize 로 늘어났다. 지수도 되는지 본다.

## 어떻게 확정하나

**보관된 보드와 같은 날짜를 맞댄다.** docs/d/2026-09-16.html 머리말에 그날
코스피 값이 옛 주소에서 받은 대로 찍혀 있다.

    기타법인 +1.65조 · 기관계 +1.23조 · 외국인 -1.67조 · 개인 -1.21조

새 주소가 같은 날짜를 주면 세 값이 맞아떨어지는지 보면 된다. 맞으면 단위도
구분 이름도 확정이고, 어긋나면 그 배수가 곧 단위다. 크기 짐작이 아니라 **한
소스가 다른 소스를 검산하는 것**이다.

**아무것도 고치지 않는다.**
"""
import json
import re
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
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read(400)
    except Exception as e:                       # noqa: BLE001
        return None, f"{type(e).__name__}: {e}".encode()


def board_header(day):
    """보관된 보드 머리말에서 그날 코스피 투자자별 값을 억원으로 읽는다."""
    p = ROOT / "docs" / "d" / f"{day}.html"
    if not p.exists():
        return None
    h = p.read_text(encoding="utf-8")
    m = re.search(r'<ul class="strip">(.*?)</ul>', h, re.S)
    if not m:
        return None
    txt = re.sub(r"<[^>]+>", "|", m.group(1))
    out = {}
    for who in ("개인", "외국인", "기관계", "기타법인"):
        mm = re.search(rf"\|{who}\|([+-][\d,.]+)(조|억원)\|", txt)
        if mm:
            v = float(mm.group(1).replace(",", ""))
            out[who] = v * 10000 if mm.group(2) == "조" else v
    return out or None


def parse(js):
    """{bizdate: {구분: 원문숫자}} — 단위는 아직 모르므로 그대로 둔다."""
    rows = js if isinstance(js, list) else [js]
    out = {}
    for r in rows:
        if not isinstance(r, dict) or "bizdate" not in r:
            continue
        out[r["bizdate"]] = {
            "개인": r.get("personalValue"),
            "외국인": r.get("foreignValue"),
            "기관": r.get("institutionalValue"),
        }
    return out


def num(s):
    if s in (None, ""):
        return None
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except ValueError:
        return None


def main():
    base = "https://m.stock.naver.com/api/index/KOSPI/trend"
    print("=" * 78)
    print("1. 며칠치를 주는가 — 파라미터를 하나씩 건다")
    print("=" * 78)
    best, best_url = {}, None
    for q in ("", "?pageSize=60", "?pageSize=20", "?page=1&pageSize=60",
              "?size=60", "?count=60", "?limit=60"):
        st, body = fetch(base + q)
        if st != 200:
            print(f"  {st!s:>5}  {q or '(없음)':<22} {body[:80]!r}")
            continue
        try:
            js = json.loads(body)
        except Exception:                        # noqa: BLE001
            print(f"  {st:>5}  {q or '(없음)':<22} JSON 아님 {len(body)}B")
            continue
        got = parse(js)
        print(f"  {st:>5}  {q or '(없음)':<22} {len(body):>6}B · {len(got)}일치 "
              f"{sorted(got)[:3]}")
        if len(got) > len(best):
            best, best_url = got, base + q
    if not best:
        print("\n  쓸 만한 응답이 없다. 여기서 멈춘다.")
        return 1
    print(f"\n  가장 깊은 응답 — {best_url} ({len(best)}일치)")

    print()
    print("=" * 78)
    print("2. 보관된 보드와 같은 날짜를 맞댄다 — 단위 확정")
    print("=" * 78)
    hit = 0
    for day in sorted(best, reverse=True):
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        ref = board_header(iso)
        if not ref:
            continue
        hit += 1
        print(f"\n  {iso}")
        print(f"    {'구분':<8}{'보드(억원)':>14}{'새 주소(원문)':>16}{'배수':>12}")
        for who, key in (("개인", "개인"), ("외국인", "외국인"), ("기관계", "기관")):
            a, b = ref.get(who), num(best[day].get(key))
            if a is None or b is None:
                print(f"    {who:<8}{'-':>14}{str(b):>16}{'-':>12}")
                continue
            r = (a / b) if b else float("nan")
            print(f"    {who:<8}{a:>14,.0f}{b:>16,.0f}{r:>12,.3f}")
        print(f"    (보드에만 있는 기타법인 {ref.get('기타법인')})")
    if not hit:
        print("\n  맞댈 날짜가 없다 — 새 주소가 주는 날짜의 보관본이 없다.")
        print("  docs/d 에 있는 날:", [p.stem for p in
                                       sorted((ROOT / 'docs' / 'd').glob('*.html'))][-6:])
    print()
    print("=" * 78)
    print("읽는 법 — 배수가 셋 다 1.000 이면 단위는 억원이고 구분 이름도 맞다.")
    print("          셋 다 같은 다른 수면 그 수가 곧 환산 배수다.")
    print("          구분마다 다르면 이름 짝이 틀린 것이다.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
