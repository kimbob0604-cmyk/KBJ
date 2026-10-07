#!/usr/bin/env python3
"""
`docs/index.html` → Claude 아티팩트에 게시할 수 있는 조각으로 바꾼다.

왜 변환이 필요한가. 아티팩트는 올린 파일을 자기 껍데기
(`<!doctype>…<head>…</head><body>`) 안에 넣는다. 그래서 우리가 만든 완성
문서를 그대로 올리면 문서가 문서 안에 들어가고, `<head>` 안의 것들이 본문으로
새어 나온다. 껍데기 태그를 벗겨서 알맹이만 넘긴다.

그리고 아티팩트에는 CSP 가 걸려 있다. 바깥에서 가져올 수 있는 스타일시트는
`fonts.googleapis.com` 뿐이라, jsdelivr 에서 받던 Pretendard 가 조용히 막힌다.
막히면 에러도 안 나고 한글이 시스템 기본 글꼴로 떨어진다. 그래서 구글 폰트에
있는 `IBM Plex Sans KR` 로 갈아 끼운다 — 이미 쓰고 있는 IBM Plex Mono 와 같은
집안이라 화면이 어긋나지 않는다.

또 상대 경로가 없다. 아티팩트는 우리 사이트가 아니므로 `d/index.json`(날짜
선택기)과 `x/rankings-*.xlsx`(엑셀)를 못 읽는다. 눌러도 아무 일이 없는 컨트롤을
남겨 두느니 뗀다. 그 두 개는 사이트 쪽에만 있다.

쓰는 곳: `run.py --artifact`. 만들어진 파일은 `docs/artifact.html` 이고,
매일 이 파일을 같은 아티팩트 주소에 다시 올리면 링크 하나가 계속 최신이 된다.
"""
import re

# 구글 폰트만 CSP 를 통과한다. 나머지 호스트의 스타일시트는 조용히 막힌다.
FONTS_HOST = 'fonts.googleapis.com'

# Pretendard 대신 쓸 한글 본문 글꼴. 구글 폰트에 있고 IBM Plex Mono 와 짝이다.
KR_FONT = 'IBM Plex Sans KR'
KR_FONT_LINK = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2'
                '?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap">')

# 아티팩트 이름은 날짜를 빼고 고정한다. 갤러리와 브라우저 탭에서 사람이 찾는
# 이름이라 매일 바뀌면 다른 페이지처럼 보인다. 기준일은 화면 머리말에 있다.
TITLE = '국장 신고가 보드'
_DATE_SUFFIX = re.compile(r'\s*\d{4}-\d{2}-\d{2}\s*$')

_TITLE = re.compile(r'<title>(.*?)</title>', re.S)
_SHELL = re.compile(r'</?(?:html|head|body)\b[^>]*>', re.I)
_DOCTYPE = re.compile(r'<!doctype[^>]*>', re.I)
_META = re.compile(r'<meta\b[^>]*>', re.I)
_LINK = re.compile(r'<link\b[^>]*>', re.I)
_NOSCRIPT = re.compile(r'<noscript>.*?</noscript>', re.I | re.S)
_DAYPICK = re.compile(r'<label class="daypick">.*?</label>', re.S)
_DL = re.compile(r'<a class="dl"[^>]*>.*?</a>', re.S)
_HREF = re.compile(r'href="([^"]*)"', re.I)


def _keep_link(tag):
    """구글 폰트 스타일시트만 남긴다. 파비콘은 게시 인자로 따로 준다."""
    m = _HREF.search(tag)
    return bool(m) and FONTS_HOST in m.group(1)


def convert(html):
    """완성 문서를 (제목, 조각) 으로 바꾼다.

    조각은 `<title>` 로 시작한다. 아티팩트가 파일 앞 8KB 안에서 제목을 찾는다.
    """
    m = _TITLE.search(html)
    if not m:
        raise ValueError('<title> 이 없다 — render.py 가 만든 문서가 맞는지 확인하라')
    title = _DATE_SUFFIX.sub('', m.group(1).strip()) or TITLE

    s = _DOCTYPE.sub('', html)
    s = _SHELL.sub('', s)
    # charset·viewport 는 아티팩트 껍데기가 넣어 준다. 두 번 넣지 않는다.
    s = _META.sub('', s)
    # <noscript> 안은 통째로 막히는 스타일시트뿐이다.
    s = _NOSCRIPT.sub('', s)
    s = _LINK.sub(lambda mo: mo.group(0) if _keep_link(mo.group(0)) else '', s)
    # 아티팩트에는 사이트가 없다. 눌러도 안 되는 컨트롤을 남기지 않는다.
    s = _DAYPICK.sub('', s)
    s = _DL.sub('', s)

    # 막힌 Pretendard 자리를 구글 폰트로 메운다.
    s = s.replace('font-family:Pretendard,', f"font-family:'{KR_FONT}',")
    s = _TITLE.sub('', s, count=1).lstrip()

    return title, f'<title>{title}</title>\n{KR_FONT_LINK}\n{s}'


def build(src, dst):
    """`docs/index.html` 을 읽어 `docs/artifact.html` 로 쓴다. 반환 (제목, 바이트)."""
    with open(src, encoding='utf-8') as f:
        title, frag = convert(f.read())
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(frag)
    return title, len(frag.encode('utf-8'))
