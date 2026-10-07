#!/usr/bin/env python3
"""
정적 사이트 조립 — 링크 하나로 들어가면 최신 보드가 보이게 한다.

구조

    docs/
      .nojekyll             GitHub Pages 의 Jekyll 처리를 끈다
      index.html            **최신 보드.** 링크는 이 주소 하나다
      artifact.html         같은 보드의 아티팩트 게시용 조각 (web/artifact.py)
      d/2026-08-27.html     날짜별 보관본
      d/index.json          보관 목록 (헤더의 날짜 선택기가 읽는다)
      x/rankings-<날짜>.xlsx 랭킹 엑셀

왜 루트에 두는가. `docs/board/index.html` 로 두면 주소가 한 단계 깊어지고,
호스트를 옮길 때마다 경로가 달라진다. 루트에 두면 어느 정적 호스트에 올리든
도메인만 붙이면 끝이다.

날짜 목록을 페이지에 박지 않고 `d/index.json` 을 런타임에 읽는 이유는, 어제
만든 보관본이 오늘 날짜를 모르기 때문이다. 파일에 박으면 옛 페이지의 선택기가
과거에 멈춰 있다.

보관본은 KEEP_DAYS 만큼만 남긴다. 하루 200KB 남짓이라 그냥 두면 레포가 계속 큰다.
"""
import json
import os
import re
import shutil

KEEP_DAYS = 90

# 예전 주소용 리다이렉트. meta refresh 는 자바스크립트 없이도 동작한다.
REDIRECT = (
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    '<meta http-equiv="refresh" content="0; url=../">'
    '<link rel="canonical" href="../">'
    '<title>국장 신고가 보드</title></head>'
    '<body style="font:15px system-ui;padding:40px">'
    '주소가 바뀌었습니다. <a href="../">최신 보드로 이동</a>'
    '</body></html>')
DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.html$')
JSON_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.json$')


def paths(root):
    """사이트 루트 아래의 고정 경로들."""
    return dict(root=root,
                index=os.path.join(root, 'index.html'),
                artifact=os.path.join(root, 'artifact.html'),
                days=os.path.join(root, 'd'),
                files=os.path.join(root, 'x'),
                api=os.path.join(root, 'api'),
                api_days=os.path.join(root, 'api', 'd'),
                nojekyll=os.path.join(root, '.nojekyll'))


def write_data(root, asof, payload, keep_days=KEEP_DAYS, log=print):
    """실시간 화면이 읽는 JSON 을 쓴다. 반환 (latest 경로, 보관 날짜 수).

    구조

        api/latest.json      최신 보드 데이터
        api/d/<날짜>.json    날짜별 보관본
        api/index.json       보관 목록 + 최신 생성 시각

    화면은 `index.json` 만 주기적으로 다시 받아 `generated_at` 이 바뀌었는지
    본다. 본문(수백 KB)을 반복해서 받지 않기 위해서다 — 목록은 수백 바이트다.
    """
    from . import payload as P

    p = paths(root)
    os.makedirs(p['api_days'], exist_ok=True)
    body = P.dump(payload)
    for path in (os.path.join(p['api'], 'latest.json'),
                 os.path.join(p['api_days'], f'{asof}.json')):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(body)

    dates = sorted((m.group(1) for m in
                    (JSON_RE.match(f) for f in os.listdir(p['api_days'])) if m),
                   reverse=True)
    for old in dates[keep_days:]:
        os.remove(os.path.join(p['api_days'], f'{old}.json'))
    dates = dates[:keep_days]

    with open(os.path.join(p['api'], 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(latest=dates[0] if dates else asof, dates=dates,
                       generated_at=payload.get('generated_at')), f,
                  ensure_ascii=False)
    log(f'  데이터 → {p["api"]}/latest.json ({len(body) // 1024:,}KB) · 보관 {len(dates)}일')
    return os.path.join(p['api'], 'latest.json'), len(dates)


# flowlab(부가 모듈)이 사이트 루트에 두는 리포트. 파일이 **있을 때만** 헤더에 링크한다.
# 렌더는 어디에 무엇이 있는지 모르므로(render.py 문서화 문자열) 자리만 남기고,
# 존재 여부는 루트를 아는 여기서 본다. 없는 파일에 링크를 달아 404 를 내지 않는다.
# {asof} 는 YYYYMMDD 로 치환된다.
REPORTS = (('flows-{asof}.html', '수급'),
           ('flows-history.html', '수급 누적'),
           ('eventstudy.html', '이벤트 스터디'))
REPORT_SLOT = '<!--reports-->'


REPORT_RE = re.compile(r'<!--reports-->.*?<!--/reports-->', re.S)


def report_links(root, asof, prefix=''):
    """루트에 있는 부가 리포트의 링크 HTML. prefix 는 보관본(d/)에서 '../'."""
    ymd = (asof or '').replace('-', '')
    out = []
    for pat, label in REPORTS:
        fn = pat.format(asof=ymd)
        if os.path.exists(os.path.join(root, fn)):
            out.append(f'<a class="dl" href="{prefix}{fn}">{label}</a>')
    return ''.join(out)


def _region(root, asof, prefix=''):
    # 표식을 남겨 두어야 나중에 리포트가 생겼을 때 relink 가 이 자리만 바꿀 수 있다.
    return f'<!--reports-->{report_links(root, asof, prefix)}<!--/reports-->'


def _with_reports(html, root, asof, prefix=''):
    # 이미 채워진 영역이 있으면 그 영역을 통째로 바꾼다. 영역의 여는 표식이 빈 자리
    # 표식과 같은 문자열이라, 자리 치환을 먼저 하면 영역이 겹겹이 쌓인다.
    if REPORT_RE.search(html):
        return REPORT_RE.sub(lambda _: _region(root, asof, prefix), html, count=1)
    if REPORT_SLOT in html:
        return html.replace(REPORT_SLOT, _region(root, asof, prefix), 1)
    return html


def relink(root, asof, log=print):
    """이미 올라간 보드의 부가 리포트 링크 영역만 다시 채운다.

    엔진(--daily)이 index.html 을 쓴 **뒤에** flowlab 이 리포트를 만들므로, 그 시점에
    링크가 없다. 렌더를 다시 돌리지 않고 표식 사이만 바꾼다. 반환: 고친 파일 수.
    """
    p = paths(root)
    n = 0
    for path, prefix in ((p['index'], ''), (os.path.join(p['days'], f'{asof}.html'), '../')):
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            html = f.read()
        if f'data-asof="{asof}"' not in html and path == p['index']:
            continue                      # 루트가 다른 날짜면 그 날짜의 링크를 달지 않는다
        new = _with_reports(html, root, asof, prefix)
        if new != html:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new)
            n += 1
    if n:
        log(f'  보드 링크 갱신 {n}개 파일 ← {root}')
    return n


def _dates(days_dir):
    if not os.path.isdir(days_dir):
        return []
    out = []
    for f in os.listdir(days_dir):
        m = DATE_RE.match(f)
        if m:
            out.append(m.group(1))
    return sorted(out, reverse=True)


def publish(root, asof, html, xlsx=None, keep_days=KEEP_DAYS, log=print,
            index_html=None):
    """보드 HTML 을 사이트에 올린다. 반환 (index 경로, 보관 날짜 수).

    `index_html` 은 루트(index.html)에만 놓을 문서다. 실시간 화면으로 바뀐 뒤로
    루트에 놓는 것은 **데이터가 없는 고정 셸**(web/app.py)이고, `html` 로 받는
    구운 문서는 보관본(d/)과 아티팩트 조각에만 쓴다. 안 주면 예전처럼 같은
    문서를 두 곳에 놓는다.
    """
    p = paths(root)
    os.makedirs(p['days'], exist_ok=True)
    os.makedirs(p['files'], exist_ok=True)

    # Jekyll 이 돌면 밑줄로 시작하는 파일을 무시하고 빌드가 끼어든다. 끈다.
    if not os.path.exists(p['nojekyll']):
        open(p['nojekyll'], 'w').close()

    # 루트(index.html)와 보관본(d/)은 깊이가 달라 부가 리포트 링크의 상대경로가 다르다.
    with open(p['index'], 'w', encoding='utf-8') as f:
        f.write(_with_reports(index_html if index_html is not None else html,
                              root, asof))
    day = os.path.join(p['days'], f'{asof}.html')
    with open(day, 'w', encoding='utf-8') as f:
        f.write(_with_reports(html, root, asof, prefix='../'))

    # 같은 보드를 Claude 아티팩트 게시용 조각으로도 남긴다. 링크 하나로 보는
    # 경로가 사이트 말고 하나 더 있고(board/docs/DASHBOARD.md), 매일 이 파일을
    # 같은 주소에 다시 올리면 그 링크가 계속 최신이 된다.
    # 아티팩트는 **구운 문서**에서 나온다. 루트가 고정 셸로 바뀐 뒤로 index.html
    # 에는 데이터가 없어서, 그걸 변환하면 표가 한 줄도 없는 조각이 올라간다.
    # 아티팩트에는 fetch 할 사이트가 없으므로 데이터가 박힌 쪽이 맞다.
    from . import artifact as A
    _, frag = A.convert(_with_reports(html, root, asof))
    with open(p['artifact'], 'w', encoding='utf-8') as f:
        f.write(frag)
    log(f'  아티팩트 조각 → {p["artifact"]} ({len(frag.encode("utf-8")) // 1024:,}KB)')

    if xlsx and os.path.exists(xlsx):
        dst = os.path.join(p['files'], os.path.basename(xlsx))
        if os.path.abspath(xlsx) != os.path.abspath(dst):
            shutil.copy2(xlsx, dst)

    # 예전 주소(docs/board/index.html)로 들어오는 링크를 새 루트로 보낸다.
    # 이미 공유했거나 북마크한 주소가 404 가 되면 안 된다.
    legacy = os.path.join(root, 'board')
    os.makedirs(legacy, exist_ok=True)
    with open(os.path.join(legacy, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(REDIRECT)

    dates = _dates(p['days'])
    for old in dates[keep_days:]:
        os.remove(os.path.join(p['days'], f'{old}.html'))
    dates = dates[:keep_days]

    with open(os.path.join(p['days'], 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(latest=dates[0] if dates else asof, dates=dates), f)

    log(f'  사이트 → {p["index"]} · 보관 {len(dates)}일')
    return p['index'], len(dates)


def prune_files(root, keep=KEEP_DAYS, log=print):
    """오래된 엑셀 정리. 보관본과 같은 기준으로 자른다."""
    p = paths(root)
    if not os.path.isdir(p['files']):
        return 0
    xs = sorted(os.listdir(p['files']), reverse=True)
    n = 0
    for f in xs[keep:]:
        os.remove(os.path.join(p['files'], f))
        n += 1
    if n and log:
        log(f'  오래된 엑셀 {n}건 삭제')
    return n
