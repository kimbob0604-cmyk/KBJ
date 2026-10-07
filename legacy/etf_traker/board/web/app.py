#!/usr/bin/env python3
"""
고정 셸 조립 — `app.html` + `app.css` + `app.js` 를 한 문서로 묶는다.

이 문서에는 **그날의 데이터가 한 글자도 없다.** 그래서 보드를 다시 돌리지
않아도 되고, 데이터가 바뀌어도 이 파일은 그대로다 (커밋에 안 올라온다).
화면은 열릴 때 `api/latest.json` 을 받아 그린다 — web/payload.py 참고.

CSS·JS 를 따로 내보내지 않고 인라인으로 넣는 이유는, 셸과 스크립트의 캐시가
어긋나면 **옛 스크립트가 새 셸을 그리는** 순간이 생기기 때문이다. 한 파일이면
그 상태가 존재하지 않는다. 60KB 남짓이라 매번 받아도 부담이 없다.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# board.css 와 같은 CDN 을 쓴다. 못 받아도 app.css 의 대체 글꼴로 떨어진다.
FONT_CSS = (
    'https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9'
    '/dist/web/static/pretendard.css',
    'https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:'
    'wght@400;500;600&display=swap',
)

FAVICON = ('data:image/svg+xml,'
           "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
           "%3Crect width='16' height='16' rx='3' fill='%23232B36'/%3E"
           "%3Cpath d='M3 11.5 6.5 7l3 2.5L13 4.5' stroke='%23fff' "
           "stroke-width='1.6' fill='none' stroke-linecap='round' "
           "stroke-linejoin='round'/%3E%3C/svg%3E")

# 제목에 날짜를 넣지 않는다. 셸은 매일 같은 파일이고, 기준일은 화면이 적는다.
TITLE = '국장 신고가 보드'


def _read(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return f.read()


def build():
    """완성된 셸 HTML 문자열."""
    body, css, js = _read('app.html'), _read('app.css'), _read('app.js')
    fonts = ''.join(f'<link rel="stylesheet" href="{u}" media="print" '
                    'onload="this.media=\'all\'">' for u in FONT_CSS)
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{TITLE}</title>'
        f'<link rel="icon" href="{FAVICON}">'
        + fonts
        + f'<style>{css}</style></head><body>{body}'
        f'<script>{js}</script></body></html>')


def write(path):
    """셸을 파일로 쓴다. 반환 바이트 수."""
    doc = build()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(doc)
    return len(doc.encode('utf-8'))
