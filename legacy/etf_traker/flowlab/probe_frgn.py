#!/usr/bin/env python3
"""
투자자별(frgn) 표가 왜 0행인지 원자료로 가른다. 일회성 점검이다.

증상 — flowlab verify 가 두 줄을 띄웠다.
  · flows.json 139행 전부 `수급 표 없음` (n_with_flows=0, coverage 0.0%)
  · investor_flows('003490', pages=3) 이 0행 (구간 nan~nan)
반면 신고가·종가는 멀쩡하다(종가 대조 2,682종목 불일치 0). **투자자별만** 빈다.

가를 것은 셋이다.
  (A) 응답이 빈다        — 차단·리다이렉트·파라미터 문제
  (B) 파싱이 깨졌다      — 표 구조가 바뀌어 셀 조건에 안 걸린다
  (C) 판정에서 떨어진다  — 표는 읽혔는데 flow_grade 를 못 붙인다

(C) 는 flows.json 이 이미 부정한다 — missing 사유가 전부 '수급 표 없음' 이라
결합 이전 단계에서 비었다. 그래서 여기서는 (A)와 (B)를 가른다.

**같은 HTML 에 두 파서를 태운다.** flowlab(BeautifulSoup)과 board(정규식)의
날짜 조건이 다르기 때문이다.
  flowlab  ^\\d{4}[.\\-/]\\d{2}[.\\-/]\\d{2}$      4자리 연도만, 공백 불허
  board    ^\\s*(\\d{2}|\\d{4})[.\\-/](\\d{1,2})…   2자리 연도도, 공백 허용
한쪽만 0행이면 (B)이고 범인은 그 조건이다. 둘 다 0행이면 (A)다.

개발 환경은 네이버가 조직 프록시에 403 이라 러너에서만 돈다.
**아무것도 고치지 않는다 — 받아서 있는 그대로 찍는다.**
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CODES = ["003490"]      # 원문 확인이 목적이라 한 종목이면 충분하다
FRGN = "https://finance.naver.com/item/frgn.naver"

# 두 저장소의 날짜 조건을 **그대로 옮겨 온다**. 어느 쪽이 걸러 내는지 보려는 것이다.
_FLOWLAB_DATE = re.compile(r"^\d{4}[.\-/]\d{2}[.\-/]\d{2}$")
_BOARD_DATE = re.compile(r"^\s*(\d{2}|\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*$")


def hr(t):
    print()
    print("─" * 72)
    print(t)
    print("─" * 72)


def fetch(code, page=1):
    """flowlab 과 **같은 방식**으로 받는다(세션·헤더·인코딩)."""
    from flowlab import naver as fn
    return fn._get(FRGN, {"code": code, "page": page}, encoding="euc-kr")


def identify(code, page=1):
    """응답이 '무엇인지' 를 먼저 밝힌다.

    1차 점검에서 119,368자인데 tr 0개가 나왔다. 비지도 않았고 로그인 페이지도
    아니다. 그러면 이 페이지가 대체 무엇인지부터 알아야 한다 — 최종 URL(리다이렉트
    여부), 상태, 실제 인코딩, title, 앞머리 원문, 프레임/스크립트 유무를 찍는다.
    **원문을 보지 않고 파서를 고치면 또 헛다리를 짚는다.**
    """
    from flowlab import naver as fn
    r = fn.session().get(FRGN, {"code": code, "page": page}, timeout=20)
    print(f"  요청 URL  {r.request.url}")
    print(f"  최종 URL  {r.url}")
    print(f"  상태 {r.status_code} · 리다이렉트 {len(r.history)}회"
          + (f" → {[h.status_code for h in r.history]}" if r.history else ""))
    print(f"  Content-Type {r.headers.get('Content-Type')!r}")
    print(f"  apparent_encoding {r.apparent_encoding!r} · 선언 {r.encoding!r}")
    print(f"  바이트 {len(r.content):,}")
    for enc in ("euc-kr", "utf-8"):
        try:
            t = r.content.decode(enc, errors="replace")
        except Exception:                                   # noqa: BLE001
            continue
        title = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
        print(f"  [{enc}] title {title.group(1).strip()[:80]!r}" if title
              else f"  [{enc}] title 없음")
        print(f"  [{enc}] tr {len(re.findall('<tr', t, re.I))} · "
              f"td {len(re.findall('<td', t, re.I))} · "
              f"table {len(re.findall('<table', t, re.I))} · "
              f"iframe {len(re.findall('<iframe', t, re.I))} · "
              f"script {len(re.findall('<script', t, re.I))}")
    t = r.content.decode("euc-kr", errors="replace")
    print(f"  앞머리 {t[:400]!r}")
    # 표가 다른 주소에 있을 수 있다 — 페이지가 가리키는 후보를 긁어 본다.
    srcs = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']*(?:frgn|invest|flow)[^"\']*)["\']',
                      t, re.I)
    if srcs:
        print(f"  frgn/invest 관련 링크 {srcs[:6]}")
    return r


def raw_cells(html, n=3):
    """표의 첫 몇 행을 td 원문 그대로. 날짜 칸이 실제로 어떻게 생겼는지 본다."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    out = []
    for tr in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 2:
            continue
        txt = [re.sub(r"<[^>]+>", "", t).replace("&nbsp;", " ").strip()
               for t in tds]
        if not any(txt):
            continue
        out.append(txt)
        if len(out) >= n:
            break
    return out


def main():
    for code in CODES:
        hr(f"{code} — 응답 정체")
        try:
            identify(code)
        except Exception as e:                              # noqa: BLE001
            print(f"  정체 확인 실패: {type(e).__name__}: {e}")

        hr(f"{code} — 파싱")
        try:
            html = fetch(code)
        except Exception as e:                              # noqa: BLE001
            print(f"  받기 실패: {type(e).__name__}: {e}")
            print("  → (A) 응답이 비거나 막혔다")
            continue

        # 1) 페이지가 어떻게 생겼나 — board 의 진단 함수를 그대로 쓴다
        try:
            from board.ingest import flows as bf
            print(f"  shape: {bf.shape(html)}")
        except Exception as e:                              # noqa: BLE001
            print(f"  shape 실패: {type(e).__name__}: {e}")
            bf = None

        # 2) td 원문 — 날짜 칸의 진짜 형식
        cells = raw_cells(html)
        print(f"  td 원문 {len(cells)}행:")
        for c in cells:
            print(f"    {c[:9]}")
        if cells:
            d = cells[0][0]
            fl_ok = bool(_FLOWLAB_DATE.match(d))
            bd_ok = bool(_BOARD_DATE.match(d))
            print(f"  날짜 칸 원문 {d!r} (길이 {len(d)})")
            print(f"    flowlab 정규식 통과? {fl_ok}")
            print(f"    board  정규식 통과? {bd_ok}")
            print(f"    td 개수(첫 행) {len(cells[0])} "
                  f"— flowlab 은 9개 이상을 요구한다")

        # 3) 두 파서를 같은 HTML 에 태운다
        try:
            from flowlab import naver as fn
            got = fn.parse_frgn(html)
            print(f"  flowlab parse_frgn → {len(got)}행"
                  + (f" · 열 {list(got.columns)}" if len(got) else ""))
            if len(got):
                print(f"    첫 행: {got.iloc[0].to_dict()}")
        except Exception as e:                              # noqa: BLE001
            print(f"  flowlab parse_frgn 실패: {type(e).__name__}: {e}")

        if bf is not None:
            try:
                rows = bf._table_rows(html)
                print(f"  board  _table_rows → {len(rows)}행")
                if rows:
                    print(f"    첫 행: {rows[0]}")
            except Exception as e:                          # noqa: BLE001
                print(f"  board _table_rows 실패: {type(e).__name__}: {e}")

    # 4) 실제 진입점 — investor_flows 가 몇 행을 주나
    hr("investor_flows('003490', pages=3) — 검증기가 부른 그대로")
    try:
        from flowlab import naver as fn
        df = fn.investor_flows("003490", pages=3)
        print(f"  {len(df)}행")
        if len(df):
            print(f"  구간 {df['date'].min()} ~ {df['date'].max()}")
            print(f"  첫 행: {df.iloc[0].to_dict()}")
    except Exception as e:                                  # noqa: BLE001
        print(f"  실패: {type(e).__name__}: {e}")

    hr("판정 안내")
    print("  두 파서 모두 0행  → (A) 응답 문제. shape 의 tr/th/로그인 여부를 볼 것")
    print("  board 만 행이 있음 → (B) flowlab 파서 조건이 범인")
    print("  둘 다 행이 있음   → 받는 단계(_get/페이지)를 다시 볼 것")


if __name__ == "__main__":
    main()
