#!/usr/bin/env python3
"""
KRX 화면을 진짜 브라우저로 띄워 **실제 요청을 잡는다**.

왜 이게 필요한가
--------------------------------------------------------------------------
`--check` 를 여덟 번 돌려 확인한 것(README 의 표):

  · 같은 주소·같은 세션으로 dbms/comm/finder/... 는 200 JSON 을 준다.
  · dbms/MDC/STAT/standard/... 는 넷 다 400 'LOGOUT'.
  · **있을 리 없는 MDCSTAT99999 도 똑같이 400 'LOGOUT'.**

마지막 줄이 핵심이다. 'LOGOUT' 은 '로그인하라' 가 아니라 '이 bld 는 안 준다' 는
대답이고, 그러니 어느 화면코드가 맞는지는 응답을 아무리 들여다봐도 나오지
않는다. 로더 HTML 에도 bld 가 없다 — JS 가 그린다.

남은 확실한 길은 하나다. 화면을 실제로 띄우고, 그 화면이 표를 그릴 때 보내는
요청을 그대로 베낀다. 짐작이 아니라 관측이다.

  python -m monitor.flow.capture                 # 투자자별 화면을 눌러 잡는다
  python -m monitor.flow.capture --out req.json  # 잡은 요청을 파일로

잡은 것은 bld·파라미터·헤더 이름이다. 쿠키 값과 비밀정보는 **적지 않는다.**
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from . import krx as K  # noqa: E402

LOADER = K.REFERER
TARGET = 'getJsonData.cmd'

# 화면에서 조회 버튼을 누르는 자리. KRX 는 화면마다 조금씩 다르므로 후보를 둔다.
SEARCH_BUTTONS = ('#jsSearchButton', '.btn-sch', 'a:has-text("조회")',
                  'button:has-text("조회")')

# 헤더는 **이름만** 본다. 쿠키 값이 로그로 새지 않게.
SAFE_HEADERS = ('content-type', 'x-requested-with', 'origin', 'referer',
                'accept', 'user-agent')


def parse_form(data):
    """폼 본문을 {이름: 값} 으로. 값이 빈 파라미터도 남긴다 — 화면이 무엇을
    보내는지가 중요하지 값이 찼는지가 아니다."""
    from urllib.parse import unquote_plus
    out = {}
    for part in (data or '').split('&'):
        if not part:
            continue
        k, _, v = part.partition('=')
        out[unquote_plus(k)] = unquote_plus(v)
    return out


def capture(seconds=20, log=print):
    """메인에서 **메뉴를 눌러** 화면을 띄우고 getJsonData 요청을 모은다.

    처음엔 로더 주소(`mdiLoader/index.cmd?menuId=...`)를 바로 열었는데 요청이
    한 건도 안 나갔다. 그 주소는 껍데기만 주고 화면은 SPA 가 그린다 — 사람이
    메뉴를 누르는 경로를 그대로 밟아야 표를 그리고, 그때 요청이 나간다.

    반환: [{label, bld, params, headers}]
    """
    from playwright.sync_api import sync_playwright

    seen, hits = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale='ko-KR')
        page = ctx.new_page()

        def on_request(req):
            if TARGET not in req.url:
                return
            try:
                data = req.post_data or ''
            except Exception:  # noqa: BLE001
                data = ''
            params = parse_form(data)
            seen.append({
                'url': req.url,
                'bld': params.get('bld'),
                'params': params,
                'headers': {k: v for k, v in (req.headers or {}).items()
                            if k.lower() in SAFE_HEADERS},
            })

        page.on('request', on_request)
        page.goto(K.HOME, wait_until='domcontentloaded', timeout=seconds * 1000)
        page.wait_for_timeout(3000)
        log(f'  메인 열림: {page.title()!r} · 요청 {len(seen)}건')

        # 메뉴에서 '투자자별' 이 붙은 항목을 찾는다. 접혀 있어도 DOM 에는 있다.
        links = page.locator("a:has-text('투자자별')")
        n = links.count()
        log(f'  메뉴 후보 {n}개')
        for i in range(min(n, 6)):
            a = links.nth(i)
            try:
                label = (a.inner_text(timeout=2000) or '').strip()
            except Exception:  # noqa: BLE001
                label = f'#{i}'
            before = len(seen)
            try:
                a.click(force=True, timeout=5000)
                page.wait_for_timeout(4000)
                # 화면이 뜨면 조회 버튼을 눌러 표를 그리게 한다.
                for sel in SEARCH_BUTTONS:
                    b = page.locator(sel).first
                    if b.count():
                        try:
                            b.click(force=True, timeout=3000)
                            page.wait_for_timeout(3000)
                        except Exception:  # noqa: BLE001
                            pass
                        break
            except Exception as e:  # noqa: BLE001
                log(f'  [{label}] 누르기 실패: {type(e).__name__}')
                continue
            got = seen[before:]
            for row in got:
                row['label'] = label
            hits.extend(got)
            log(f'  [{label}] 요청 {len(got)}건 · '
                f'bld {sorted({r["bld"] for r in got if r["bld"]})}')
        browser.close()
    return seen


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='잡은 요청을 JSON 으로 떨군다')
    a = ap.parse_args(argv)

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    got = capture(log=print)
    blds = sorted({r['bld'] for r in got if r['bld']})
    print(f'\n잡은 요청 {len(got)}건 · 화면코드 {len(blds)}종')
    for b in blds:
        row = next(r for r in got if r['bld'] == b)
        keys = sorted(k for k in row['params'] if k != 'bld')
        print(f'  {b}\n    화면 {row.get("label")!r} · 파라미터 {keys}')
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(got, f, ensure_ascii=False, indent=2)
        print(f'→ {a.out}')
    # 잡은 게 없으면 실패다. 조용히 0 을 내면 '됐다' 로 읽힌다.
    return 0 if blds else 1


if __name__ == '__main__':
    sys.exit(main())
