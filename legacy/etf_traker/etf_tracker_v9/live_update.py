#!/usr/bin/env python3
"""
장중 갱신 전용 진입점 — docs/base.json + 목록 API 로 대시보드만 다시 그린다.

tracker.py 를 쓰지 않는 이유: 그쪽은 collectors(pdfplumber·bs4)를 import 하므로
설치가 무겁다. 장중 잡은 하루 28번 도니까 requests 하나만 필요하게 떼어놨다.

  python live_update.py            docs/index.html 갱신
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import market as MK          # noqa: E402
import dash as DASH          # noqa: E402
import render as RENDER      # noqa: E402


def main():
    base_path = os.path.join(HERE, 'docs', 'base.json')
    if not os.path.exists(base_path):
        print('::error::docs/base.json 이 없습니다. 아침 수집(--run)을 먼저 한 번 돌리세요.')
        return 1
    with open(base_path, encoding='utf-8') as fh:
        base = json.load(fh)

    try:
        live = MK.live_rows(MK.fetch_list())
    except Exception as ex:
        # 네이버가 잠깐 죽어도 기존 페이지는 그대로 두는 게 낫다. 빈 페이지로 덮지 않는다.
        print(f'::warning::시세 조회 실패({type(ex).__name__}) — 이전 대시보드를 유지합니다')
        return 0

    D = DASH.live_view(base, live)
    out = os.path.join(HERE, 'docs', 'index.html')
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(RENDER.build(D))

    ud = re.sub(r'<[^>]+>', '', D['kpi'][1][1])
    state = '장중' if D['is_live'] else '장 마감'
    print(f'{state} {D["live_date"]} {D["live_ts"]} · {D["n_etf"]:,}종목 · '
          f'상승/하락 {ud} · 기준데이터 {D["base"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
