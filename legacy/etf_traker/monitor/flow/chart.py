#!/usr/bin/env python3
"""
수급 차트 4종 → PNG (텔레그램 전송용).

예시 워크북의 차트 4개를 그대로 옮긴다.

  1. 투자자별 누적 순매수 (억원)      선  · 개인 · 외국인 · 기관합계
  2. 일봉 (원) + 거래량               캔들 · 수정주가
  3. 외국인·기관 일별 순매수 (억원)   막대 · 2계열
  4. 기관 세부 누적 순매수 (억원)     선  · 투신 · 사모 · 연기금 등 · 금융투자

--------------------------------------------------------------------------
한글 폰트가 없으면 만들지 않는다
--------------------------------------------------------------------------
matplotlib 는 글리프가 없어도 **조용히 두부(□)를 그린다.** 차트는 만들어지고
전송도 되는데 읽을 수 없는 그림이 간다. 그래서 폰트를 고를 때 실제로 '가나힣'
글리프가 있는지 확인하고, 없으면 예외를 낸다.

CI 에는 `fonts-nanum` 을 깔아 두고, 없으면 이 환경에 있는 WenQuanYi(한글 포함)로
떨어진다.

--------------------------------------------------------------------------
색
--------------------------------------------------------------------------
dataviz 스킬의 검증된 기본 팔레트에서 슬롯 순서대로 가져왔고, 쓰는 조합을 전부
`scripts/validate_palette.js` 로 돌려 통과를 확인했다(차트1 3색 · 차트3 2색 ·
차트4 4색, light 모드).

색은 **주체에 고정**한다 — 개인은 어느 차트에서나 파랑이다. 계열 수가 달라져도
색이 옮겨 다니면 두 차트를 나란히 못 읽는다.

대비 검사에서 일부 색이 3:1 미만으로 WARN 이 났다. 스킬 규약대로 **구제 수단**을
붙인다 — 마지막 점에 값을 직접 찍고(direct label), 텔레그램 본문에 같은 숫자를
표로 싣는다.
"""
from __future__ import annotations

import glob
import os

import matplotlib
matplotlib.use('Agg')                       # 화면 없는 환경. import 순서가 중요하다
import matplotlib.pyplot as plt             # noqa: E402
import matplotlib.font_manager as fm        # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

# 주체 → 색. 슬롯 순서 고정, 절대 순환시키지 않는다.
COLOR = {
    '개인': '#2a78d6',        # 1 blue
    '외국인': '#eb6834',      # 2 orange
    '기관합계': '#1baf7a',    # 3 aqua
    '투신': '#eda100',        # 4 yellow
    '사모': '#e87ba4',        # 5 magenta
    '연기금 등': '#008300',   # 6 green
    '금융투자': '#4a3aa7',    # 7 violet
}

# 캔들 색. 국내 관행대로 오르면 빨강, 내리면 파랑이다 — 여기서 뒤집으면
# 받아 보는 사람이 매번 범례를 찾아야 한다. 수급 차트의 주체 색과는 다른
# 자리이므로 섞이지 않는다(저쪽은 개인·외국인·기관에 고정).
UP = '#c8322f'
DOWN = '#2a78d6'

SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
GRID = '#e2e5e9'

# 한글 글리프가 있는 폰트 후보. 앞에서부터 찾는다.
FONT_GLOBS = (
    '/usr/share/fonts/**/NanumGothic*.ttf',
    '/usr/share/fonts/**/NanumBarunGothic*.ttf',
    '/usr/share/fonts/**/malgun*.ttf',
    '/usr/share/fonts/**/NotoSansCJK*.ttc',
    '/usr/share/fonts/**/NotoSansKR*.otf',
    '/usr/share/fonts/**/wqy-zenhei.ttc',
    '/usr/share/fonts/**/unifont.otf',
)

_FONT = {}


def _covers_hangul(path):
    """'가나힣' 글리프가 실제로 있는가. 없으면 두부가 그려진다."""
    try:
        from fontTools.ttLib import TTFont, TTCollection
        fonts = (TTCollection(path).fonts if path.lower().endswith('.ttc')
                 else [TTFont(path, fontNumber=0)])
        for f in fonts:
            for t in f['cmap'].tables:
                if all(ord(ch) in t.cmap for ch in '가나힣'):
                    return True
    except Exception:  # noqa: BLE001 — 열리지 않는 폰트는 후보에서 뺀다
        return False
    return False


def korean_font():
    """쓸 수 있는 한글 폰트 경로. 없으면 예외.

    조용히 두부를 그리느니 만들지 않는다 — 읽을 수 없는 그림을 보내면 받은
    사람이 그게 오류인지도 모른다.
    """
    if _FONT:
        return _FONT['path']
    for pat in FONT_GLOBS:
        for path in sorted(glob.glob(pat, recursive=True)):
            if _covers_hangul(path):
                _FONT['path'] = path
                _FONT['name'] = fm.FontProperties(fname=path).get_name()
                fm.fontManager.addfont(path)
                return path
    raise RuntimeError(
        '한글 글리프가 있는 폰트를 못 찾았다. CI 에 fonts-nanum 을 설치하라 — '
        '없이 그리면 라벨이 전부 두부(□)로 나온다.')


def _setup():
    korean_font()
    plt.rcParams.update({
        'font.family': _FONT['name'],
        'axes.unicode_minus': False,          # 마이너스가 두부로 나오는 것을 막는다
        'figure.facecolor': SURFACE,
        'axes.facecolor': SURFACE,
        'axes.edgecolor': GRID,
        'axes.labelcolor': INK_2,
        'text.color': INK,
        'xtick.color': INK_2, 'ytick.color': INK_2,
        'grid.color': GRID, 'grid.linewidth': 0.8,
        'font.size': 11,
    })


def _mmdd(d):
    return f'{d[4:6]}.{d[6:8]}'


def _frame(ax, title, note=None):
    ax.set_title(title, fontsize=13, color=INK, pad=12, loc='left')
    ax.grid(True, axis='y', alpha=0.7)
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    if note:
        ax.set_xlabel(note, fontsize=9, color=INK_2)


def _thin_xticks(ax, labels, every=None):
    """날짜가 20개면 다 찍으면 겹친다. 4~5개만 남긴다."""
    n = len(labels)
    every = every or max(1, n // 5)
    ax.set_xticks(range(0, n, every))
    ax.set_xticklabels([labels[i] for i in range(0, n, every)], fontsize=9)


def _label_last(ax, xs, ys, color, text):
    """마지막 점에만 값을 찍는다. 대비 WARN 의 구제 수단이자, 모든 점에
    숫자를 찍지 않는다는 규약이기도 하다."""
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if not pts:
        return
    x, y = pts[-1]
    ax.annotate(text, (x, y), textcoords='offset points', xytext=(6, 0),
                va='center', fontsize=10, color=color, fontweight='bold')


def cum_chart(rep, path):
    """1. 투자자별 누적 순매수."""
    _setup()
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=160)
    series = ('개인', '외국인', '기관합계')
    labels = [_mmdd(d) for d, _ in rep['cum'][series[0]]]
    for who in series:
        ys = [v for _, v in rep['cum'][who]]
        ax.plot(range(len(ys)), ys, linewidth=2, color=COLOR[who], label=who)
        _label_last(ax, range(len(ys)), ys, COLOR[who], f'{ys[-1]:+,.0f}')
    ax.axhline(0, color=INK_2, linewidth=1, alpha=0.5)
    _thin_xticks(ax, labels)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:,.0f}'))
    _frame(ax, '투자자별 누적 순매수 (억원)',
           f"{_mmdd(rep['baseDate'])} 를 0 으로 놓고 누적")
    ax.legend(frameon=False, fontsize=10, loc='best')
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def _won(v):
    """원 단위 축 라벨. 만원을 넘어가면 자릿수가 길어져 축이 뭉갠다."""
    return f'{v:,.0f}'


def price_chart(rep, path):
    """2. 일봉 (캔들 + 거래량).

    예전에는 '기준일=100' 선 하나였다. 기준일이 매일 밀리니 같은 종목을 이틀
    연속 받아도 선의 모양과 눈금이 달라지고, 어제 본 그림과 겹쳐 읽을 수 없다.
    가격 자체는 언제 그려도 같은 그림이라 실제 일봉으로 바꿨다.

    일봉을 못 받았으면 종가 선으로 물러선다. 그것도 없으면 그리지 않는다 —
    빈 축을 보내면 '가격이 없었다' 로 읽힌다.
    """
    _setup()
    rows = rep.get('ohlcv') or []
    if not rows:
        return _close_line(rep, path)

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(8, 4.6), dpi=160, sharex=True,
        gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.08})

    labels = [_mmdd(r['asof'].replace('-', '')) for r in rows]
    for i, r in enumerate(rows):
        up = r['close'] >= r['open']
        c = UP if up else DOWN
        # 심지 먼저, 몸통을 위에. 시가=종가(도지)면 몸통이 사라지므로 선으로 긋는다.
        ax.vlines(i, r['low'], r['high'], color=c, linewidth=1)
        lo, hi = sorted((r['open'], r['close']))
        if hi - lo <= 0:
            ax.hlines(r['close'], i - 0.3, i + 0.3, color=c, linewidth=1.6)
        else:
            ax.add_patch(plt.Rectangle((i - 0.3, lo), 0.6, hi - lo,
                                       facecolor=c, edgecolor=c, linewidth=0.6))
        axv.bar(i, r.get('volume') or 0, width=0.6, color=c, alpha=0.65)

    # 오른쪽에 여백을 둔다 — 마지막 값 라벨이 축 밖으로 잘린다.
    ax.set_xlim(-0.8, len(rows) + 1.6)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _won(v)))
    last = rows[-1]
    _label_last(ax, [len(rows) - 1], [last['close']],
                UP if last['close'] >= last['open'] else DOWN,
                f"{last['close']:,.0f}원")
    # 구간 표기는 아래 축에 붙인다. 위 축에 달면 두 그림 사이에 끼어 보인다.
    _frame(ax, f"{rep['name']} 일봉 (원)")
    ax.tick_params(labelbottom=False)

    axv.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v / 10000:,.0f}만'))
    _thin_xticks(axv, labels)
    _frame(axv, '거래량 (주)', f'{labels[0]}~{labels[-1]} · 수정주가')
    axv.title.set_fontsize(10)
    axv.title.set_color(INK_2)

    # tight_layout 은 두 축을 붙여 놓은 이 그림과 맞지 않는다(경고를 낸다).
    fig.subplots_adjust(left=0.11, right=0.97, top=0.90, bottom=0.14)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def _close_line(rep, path):
    """일봉을 못 받았을 때. 종가만 선으로 긋는다 — 없는 값을 지어내지 않는다."""
    closes = rep.get('closes') or {}
    pts = [(d, closes.get(d)) for d in rep['dates'] if closes.get(d) is not None]
    if len(pts) < 2:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=160)
    ys = [v for _, v in pts]
    ax.plot(range(len(ys)), ys, linewidth=2, color=INK)
    _thin_xticks(ax, [_mmdd(d) for d, _ in pts])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _won(v)))
    _label_last(ax, range(len(ys)), ys, INK, f'{ys[-1]:,.0f}원')
    _frame(ax, f"{rep['name']} 종가 (원)", '일봉을 못 받아 종가만 그렸다')
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def bar_chart(rep, path):
    """3. 외국인·기관 일별 순매수. 막대 사이에 2px 틈을 둔다."""
    _setup()
    fig, ax = plt.subplots(figsize=(8, 3.8), dpi=160)
    labels = [_mmdd(d) for d, _, _ in rep['bars']]
    xs = range(len(labels))
    w = 0.38
    ax.bar([x - w / 2 - 0.01 for x in xs], [f for _, f, _ in rep['bars']],
           width=w, color=COLOR['외국인'], label='외국인')
    ax.bar([x + w / 2 + 0.01 for x in xs], [i for _, _, i in rep['bars']],
           width=w, color=COLOR['기관합계'], label='기관')
    ax.axhline(0, color=INK_2, linewidth=1)
    _thin_xticks(ax, labels)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:,.0f}'))
    _frame(ax, '외국인·기관 일별 순매수 (억원)')
    ax.legend(frameon=False, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def inst_chart(rep, path):
    """4. 기관 세부 누적. 값이 내내 0 인 구분은 그리지 않는다 — 범례만 채운다."""
    _setup()
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=160)
    drawn = 0
    labels = None
    for who in ('투신', '사모', '연기금 등', '금융투자'):
        pts = rep['cum'].get(who) or []
        if not pts:
            continue
        ys = [v for _, v in pts]
        if all(abs(v or 0) < 0.005 for v in ys):
            continue
        labels = labels or [_mmdd(d) for d, _ in pts]
        ax.plot(range(len(ys)), ys, linewidth=2, color=COLOR[who], label=who)
        _label_last(ax, range(len(ys)), ys, COLOR[who], f'{ys[-1]:+,.0f}')
        drawn += 1
    if not drawn:
        plt.close(fig)
        return None
    ax.axhline(0, color=INK_2, linewidth=1, alpha=0.5)
    _thin_xticks(ax, labels or [])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:,.0f}'))
    _frame(ax, '기관 세부 누적 순매수 (억원)',
           '움직임이 없는 구분은 생략')
    ax.legend(frameon=False, fontsize=10, loc='best')
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def render_all(rep, outdir):
    """네 장을 만들어 경로 목록을 돌려준다. 못 그린 것은 목록에서 빠진다."""
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.join(outdir, f"{rep['code']}_{rep['dates'][-1]}")
    made = []
    for fn, suffix in ((cum_chart, 'cum'), (price_chart, 'price'),
                       (bar_chart, 'bar'), (inst_chart, 'inst')):
        p = fn(rep, f'{stem}_{suffix}.png')
        if p:
            made.append(p)
    return made
