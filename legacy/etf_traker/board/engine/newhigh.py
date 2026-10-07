#!/usr/bin/env python3
"""
신고가 엔진 — 이 프로젝트의 심장.

CLAUDE.md 4장의 용어 정의를 그대로 코드로 옮긴 것이고, 정의를 바꾸려면
CLAUDE.md 와 docs/DECISIONS.md 를 함께 고쳐야 한다. 여기만 고치면 안 된다.

  60일 신고가    직전 60영업일(당일 제외) 최고가를 당일 갱신
  52주 신고가    직전 252영업일(당일 제외) 최고가를 당일 갱신
  역사적 신고가   상장 이후 전체 최고가를 당일 갱신
  종가/고가 기준  둘 다 계산해 별도로 저장. 리포트 기본값은 고가 기준
  라벨 우선순위   역사적 > 52주 > 60일. 상위가 하위를 포함
  신규/이어감    직전 영업일에 같은(또는 상위) 라벨이 없었으면 신규
  갭            (기준 최고가 - 현재가) / 기준 최고가
  5일 축소폭     5영업일 전 갭 - 현재 갭. 음수일수록 빠르게 좁혀짐
  저항두께       현재가~기준 최고가 구간의 누적 거래량 / 20일 평균 거래량
  거래량 배수    당일 거래량 / 20일 평균 거래량
  재료 반납      (고가 - 종가) / (고가 - 전일종가) >= 0.7

계산되지 않는 값은 None 으로 두고 절대 채워 넣지 않는다 (CLAUDE.md 2장 1번).
"""
from datetime import date

BASES = ('high', 'close')

# 라벨 종류는 config/settings.yaml 이 정한다. 코드에 박지 않는다.
# 'hist' 는 룩백 창이 아니라 스칼라라 lookback 에 없고 priority 에만 있다.


def kinds(cfg):
    return tuple(cfg['newhigh']['priority'])


def rank_of(cfg):
    return {k: i for i, k in enumerate(cfg['newhigh']['priority'])}


def displayable(cfg):
    """표에 올릴 라벨 집합.

    `min_display_kind` 아래 등급은 계산만 하고 표에서 뺀다. 20일이 있던 시절
    그 용도였는데 20일 자체를 없앴으므로(D-071) 지금은 계산한 셋이 모두 오른다.
    손잡이는 남겨 둔다 — 창을 다시 늘릴 때 필요하다."""
    pri = cfg['newhigh']['priority']
    cut = cfg['newhigh'].get('min_display_kind')
    return set(pri if not cut or cut not in pri else pri[:pri.index(cut) + 1])


# ─────────────────────────── 수정주가 가드 ───────────────────────────
def split_guard(rows, ratio):
    """수정주가 미반영 시계열을 잡는다 (CLAUDE.md 9장 미확정 1번).

    국내 가격제한폭은 +-30% 다. 하루 사이 종가가 그 이상 변했다면 실제 등락이
    아니라 액면분할·무상증가·주식병합이 시계열에 반영되지 않은 것이다.
    이 종목은 역사적 신고가 판정에서 빼고, 그 사실을 리포트에 노출한다.

    가장 마지막 이상 지점의 인덱스를 함께 돌려준다. 그 이전 구간은 현재 주가와
    단위가 다르므로 60일·52주 룩백 창에서도 잘라내야 한다. 자르지 않으면 분할
    직후 종목은 분할 전 가격을 최고가로 들고 있어 몇 달간 신고가가 뜨지 않는다.

    반환: (suspect, 발생일, 사유, 이후_시작_인덱스)
    """
    hit = None
    for i, (a, b) in enumerate(zip(rows, rows[1:])):
        pa, pb = a.get('close'), b.get('close')
        if not pa or not pb or pa <= 0:
            continue
        r = pb / pa - 1.0
        if abs(r) > ratio:
            hit = (b['asof'], f'{a["asof"]}→{b["asof"]} 종가 {r*100:+.1f}%', i + 1)
    if hit:
        return True, hit[0], hit[1], hit[2]
    return False, None, '', 0


# ─────────────────────────── 보조 계산 ───────────────────────────
def _key(basis):
    return 'high' if basis == 'high' else 'close'


def _win(rows, idx, n, floor=0):
    """당일 제외, 직전 n영업일 슬라이스.

    floor 는 수정주가 이상 지점 이후의 시작 인덱스다. 창이 floor 를 넘어
    과거로 가야 채워지는 경우에는 계산하지 않는다 (None). 단위가 다른 구간의
    가격을 최고가로 쓰지 않기 위해서다.
    """
    if idx - n < floor:
        return None
    return rows[idx - n:idx]


def _max(win, key):
    vals = [r[key] for r in win if r.get(key)]
    return max(vals) if vals else None


def _avg_vol(rows, idx, n, floor=0):
    """직전 n영업일 평균 거래량. 창을 못 채우면 None.

    예전에는 못 채우면 있는 만큼으로 평균을 냈다. 그러면 17~19일 정지 후
    평소와 같은 거래량으로 재개해도 거래량 배수가 6~20배로 나와 탐지기 4가
    터진다(정지 기간의 0 이 분모에 들어가서). D-006 이 '직전 20영업일'이라고
    못박았고 2장 1번은 계산 안 된 값을 내지 말라고 한다.
    """
    win = _win(rows, idx, n, floor)
    if not win:
        return None
    vals = [r['volume'] for r in win if r.get('volume') is not None]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    return m or None


def _gap(ref, cur):
    """(기준 최고가 - 현재가) / 기준 최고가, %. 이미 넘겼으면 음수."""
    if not ref or ref <= 0 or cur is None:
        return None
    return (ref - cur) / ref * 100.0


def _refs_at(rows, idx, basis, cfg, hist_ref, floor=0):
    """idx 시점(당일 제외)의 3종 기준 최고가."""
    k = _key(basis)
    out = {}
    for kind, n in cfg['newhigh']['lookback'].items():
        win = _win(rows, idx, n, floor)
        out[kind] = _max(win, k) if win else None
    out['hist'] = (hist_ref or {}).get(basis)
    return out


def _resistance(rows, idx, cfg, basis, kind, cur, ref, avg20, floor=0):
    """현재가~기준 최고가 구간에 쌓인 매물의 두께.

    **봉의 고가~저가 범위가 그 구간과 겹치면 그 봉의 거래량을 센다.**

    처음에는 종가가 구간 안에 드는 봉만 셌는데, 고가 기준에서는 그 값이 거의
    항상 0 이 나왔다 (2026-08-27 실행에서 근접 종목 대부분이 '얇음 0.0').
    당연한 결과다 — 구간이 [당일 고가, 기간 최고가]인데 종가는 체계적으로
    고가보다 아래라 그 좁은 띠에 거의 들어오지 않는다. 기준과 판정에 서로 다른
    가격을 쓴 것이 문제였다.

    범위 겹침으로 보면 기준(고가/종가)과 무관하게 같은 뜻이 되고, 매물대라는
    개념 자체와도 맞는다. 하루 안에서 어느 가격에 얼마나 체결됐는지는 일봉으로
    알 수 없으므로 봉 전체 거래량을 세는 근사다. 그래서 이 값은 추정치다.

    룩백 창은 해당 라벨의 창을 쓰고, 역사적은 DB 가 들고 있는 구간 전체를
    쓰므로 window_days 를 함께 돌려준다.
    """
    if not ref or not cur or not avg20 or ref <= cur:
        return None
    n = cfg['newhigh']['lookback'].get(kind, idx)
    win = rows[max(floor, idx - n):idx]
    if not win:
        return None
    vol = 0.0
    for r in win:
        v = r.get('volume')
        if v is None:
            continue
        lo, hi = r.get('low'), r.get('high')
        if lo is None or hi is None:
            lo = hi = r.get('close')        # 고저가가 없으면 종가 한 점으로 본다
            if lo is None:
                continue
        if hi >= cur and lo <= ref:         # 봉의 범위가 구간과 겹치면 센다
            vol += v
    return dict(value=round(vol / avg20, 2), window_days=len(win))


def _bucket(v, cfg):
    if v is None:
        return None
    r = cfg['resistance']
    return '얇음' if v < r['thin_below'] else ('두꺼움' if v > r['thick_above'] else '보통')


# ─────────────────────────── 종목 1개 평가 ───────────────────────────
def evaluate(rows, asof, cfg, hist_ref=None, hist_days=None, split_cleared=False):
    """한 종목의 기준일 신고가 지표 전부.

    rows           오름차순 일봉 [{asof,open,high,low,close,volume}]
    hist_ref       {'high':x,'close':y} 역사적 최고가 스칼라. 없으면 hist 판정 생략
    hist_days      상장 이후 누적 영업일 수. DB 가 일부 구간만 들고 있어도 되도록 분리
    split_cleared  공시 대조로 '분할이 아니다' 가 확인된 종목 (D-056). 계단은
                   실재하는 등락이므로 룩백을 자르지도, 역사적 판정을 막지도
                   않는다. 조회에 실패한 종목에는 절대 주지 않는다 — 못 본 것과
                   아닌 것은 다르다.
    반환           dict. 기준일 봉이 없으면 None
    """
    idx = next((i for i, r in enumerate(rows) if r['asof'] == asof), None)
    if idx is None:
        return None
    today = rows[idx]
    prev = rows[idx - 1] if idx > 0 else None

    # 직전 행이 달력상 얼마나 떨어져 있는지. 거래정지로 봉이 비면 그 공백
    # 전체의 수익률이 '당일 등락률'로 나간다. 15일 정지 후 재개한 종목의
    # +30% 가 당일 등락률로 섹터 시총가중 평균에 들어가는 식이다.
    gap_days = None
    if prev:
        try:
            gap_days = (date.fromisoformat(today['asof'])
                        - date.fromisoformat(prev['asof'])).days
        except (ValueError, TypeError):
            gap_days = None

    gi = cfg['integrity']
    suspect, sdate, snote, floor = split_guard(rows, gi['split_guard_ratio'])
    if suspect and split_cleared:
        # 공시가 없다고 확인된 계단이다. 실제 등락이므로 그 이전 구간도 지금
        # 주가와 같은 단위다 — 자를 이유가 없다. 사유는 남겨서 화면이 왜
        # 통과시켰는지 설명할 수 있게 한다.
        suspect, floor = False, 0
        snote = f'{snote} — 공시 대조 결과 분할이 아니다 (D-056)'
    n_days = hist_days if hist_days is not None else idx + 1
    # 이상 지점 이후로만 룩백을 허용한다. 분할 전 가격은 지금 주가와 단위가 다르다.
    usable = idx - floor

    avg20 = _avg_vol(rows, idx, cfg['volume']['avg_days'], floor)
    # 거래량 0 은 '거래 없음'이라는 사실이다. None(못 받음)과 다르다.
    vol_mult = (round(today['volume'] / avg20, 2)
                if (avg20 and today.get('volume') is not None) else None)

    chg = None
    if prev and prev.get('close') and today.get('close') is not None:
        chg = round((today['close'] / prev['close'] - 1) * 100, 2)

    # 재료 반납 — 분모가 0 이하면 그날 재료 자체가 없었던 것이라 판정하지 않는다.
    # 리포트에 쓰는 값은 비율이 아니라 %p 다. 레퍼런스 코멘트가 그렇게 쓴다:
    #   "고가 대비 종가 괴리 11.2%p" = 고가 등락률 +12.65% - 종가 등락률 +1.45%
    give = high_chg = give_pp = None
    if prev and today.get('high') is not None and prev.get('close'):
        high_chg = round((today['high'] / prev['close'] - 1) * 100, 2)
        denom = today['high'] - prev['close']
        # 종가를 못 받았으면 반납은 계산되지 않는다. 예전 판은 종가를 안 보고
        # 뺄셈에 넣어 TypeError 로 그날 평가가 통째로 죽었고, 바로 다음 줄은
        # chg 가 None 이면 0 으로 대신해 give_pp 에 high_chg 를 그대로 실었다 —
        # '고가 대비 종가 괴리 %p' 자리에 고가 등락률이 사실처럼 찍힌다.
        # 계산되지 않은 값은 출력하지 않는다 (CLAUDE.md 2장 1번).
        if denom > 0 and today.get('close') is not None:
            give = round((today['high'] - today['close']) / denom, 3)
            if chg is not None:
                give_pp = round(high_chg - chg, 2)

    out = dict(
        asof=asof, open=today.get('open'), high=today.get('high'),
        low=today.get('low'), close=today.get('close'), volume=today.get('volume'),
        chg_pct=chg, vol_mult=vol_mult, avg_vol_20=avg20,
        giveback=give, giveback_pp=give_pp, high_chg_pct=high_chg,
        prev_asof=prev['asof'] if prev else None, prev_gap_days=gap_days,
        n_days=n_days, usable_days=usable, split_floor=floor,
        suspect=suspect, suspect_date=sdate, suspect_note=snote,
        basis={})

    min_h = gi['min_history_days']
    nd = cfg['proximity']['narrow_days']
    KINDS = kinds(cfg)
    RANK = rank_of(cfg)
    lb = cfg['newhigh']['lookback']

    for basis in BASES:
        k = _key(basis)
        cur = today.get(k)
        refs = _refs_at(rows, idx, basis, cfg, hist_ref, floor)
        # 역사적은 이력이 짧거나 수정주가 의심이면 계산하지 않는다.
        if n_days < min_h['hist'] or (suspect and gi['suppress_hist_on_suspect']):
            refs['hist'] = None
        # 나머지는 쓸 수 있는 영업일이 룩백 창을 못 채우면 계산하지 않는다.
        for kind, n in lb.items():
            if usable < n:
                refs[kind] = None

        hit, gaps, resist = {}, {}, {}
        for kind in KINDS:
            ref = refs.get(kind)
            hit[kind] = bool(ref and cur is not None and cur > ref)
            g = _gap(ref, cur)      # ref 나 cur 이 없으면 None 을 돌려준다
            gaps[kind] = None if g is None else round(g, 2)
            r = _resistance(rows, idx, cfg, basis, kind, cur, ref, avg20, floor)
            resist[kind] = r

        # 5일 축소폭 — 같은 정의로 5영업일 전 갭을 다시 계산해 뺀다.
        narrow = {}
        j = idx - nd
        if j >= floor:
            # 룩백 창 기반 라벨은 j 시점에서 창을 다시 잡으므로 문제없다.
            # hist 는 다르다 — 오늘의 사상최고가를 5일 전 기준으로 쓰면
            # 그 사이에 최고가가 움직인 만큼이 통째로 '축소폭'으로 잡힌다.
            # 3일 전에 신고가를 낸 종목이 "5일 만에 20%p 좁혔다"로 나와
            # 근접 표 맨 위를 먹는다. hist 는 계산하지 않는다.
            prev_refs = _refs_at(rows, j, basis, cfg, None, floor)
            pcur = rows[j].get(k)
            for kind in KINDS:
                if kind == 'hist':
                    narrow[kind] = None
                    continue
                g0, g1 = _gap(prev_refs.get(kind), pcur), gaps.get(kind)
                narrow[kind] = (round(g1 - g0, 2)
                                if (g0 is not None and g1 is not None) else None)
        else:
            narrow = {kind: None for kind in KINDS}

        label = next((kind for kind in KINDS if hit[kind]), None)
        out['basis'][basis] = dict(
            cur=cur, refs=refs, hit=hit, gap=gaps, narrow5=narrow,
            resistance={kind: (resist[kind] or {}).get('value') for kind in KINDS},
            resistance_window={kind: (resist[kind] or {}).get('window_days') for kind in KINDS},
            resistance_label={kind: _bucket((resist[kind] or {}).get('value'), cfg)
                              for kind in KINDS},
            label=label, rank=RANK[label] if label else None)
    return out


# ─────────────────────────── 역사적 최고가 스칼라 ───────────────────────────
def roll_alltime(prev, rows, cfg):
    """역사적 최고가 스칼라 갱신. 전체 일봉을 매일 재계산하지 않는다.

    hi 만 들고 있으면 안 된다. 수집은 당일 봉까지 받아 오므로, 당일 고가가
    사상 최고가면 hi 가 이미 그 값이 되어 '당일 갱신' 판정이 영원히 거짓이 된다.
    그래서 직전 처리일까지의 최고가(prev_hi / prev_cl)를 함께 들고 있고,
    엔진은 기준일이 last_date 와 같을 때 그 값을 기준 최고가로 쓴다.

    같은 날짜를 다시 넣어도 값이 변하지 않도록 last_date 이후 행만 반영한다.
    과거 구간을 뒤늦게 채우려면(백필) 이 함수로는 안 되고 --init 으로 다시 쌓아야 한다.

    prev  기존 스칼라 dict 또는 None
    rows  오름차순 일봉
    """
    prev = prev or {}
    hi, hi_d = prev.get('hi'), prev.get('hi_date')
    cl, cl_d = prev.get('cl'), prev.get('cl_date')
    p_hi, p_cl = prev.get('prev_hi'), prev.get('prev_cl')
    first, last = prev.get('first_date'), prev.get('last_date')
    n = prev.get('n_days') or 0

    fresh = sorted({r['asof']: r for r in rows
                    if not last or r['asof'] > last}.values(),
                   key=lambda r: r['asof'])
    for r in fresh:
        # 이 행을 반영하기 직전의 값이 곧 '직전 영업일까지의 최고가'다.
        p_hi, p_cl = hi, cl
        d = r['asof']
        if first is None or d < first:
            first = d
        if r.get('high') and (hi is None or r['high'] > hi):
            hi, hi_d = r['high'], d
        if r.get('close') and (cl is None or r['close'] > cl):
            cl, cl_d = r['close'], d
        last = d
        n += 1

    suspect, sdate, snote, _ = split_guard(rows, cfg['integrity']['split_guard_ratio'])
    return dict(hi=hi, hi_date=hi_d, cl=cl, cl_date=cl_d,
                prev_hi=p_hi, prev_cl=p_cl,
                first_date=first, last_date=last, n_days=n,
                suspect=suspect, suspect_date=sdate, suspect_note=snote)


def hist_ref_for(at, asof):
    """스칼라에서 기준일의 '역사적 최고가' 기준값을 꺼낸다.

    반환 (ref dict 또는 None, 사유). 기준일이 스칼라가 아는 마지막 날보다
    과거면 스칼라만으로는 복원할 수 없으므로 판정하지 않는다.
    """
    if not at or not at.get('last_date'):
        return None, '역사적 최고가 스칼라 없음'
    last = at['last_date']
    if asof == last:
        if at.get('prev_hi') is None:
            return None, '직전일까지의 최고가 미확보 (상장 첫날이거나 최초 적재)'
        return {'high': at['prev_hi'], 'close': at.get('prev_cl')}, ''
    if asof > last:
        return {'high': at.get('hi'), 'close': at.get('cl')}, ''
    return None, f'기준일 {asof} 이 스칼라 기준일 {last} 보다 과거 — 스칼라로 복원 불가'


# ─────────────────────────── 신규 / 이어감 ───────────────────────────
def continuity(today_rank, prev_rank):
    """상위 라벨이 하위를 포함하므로 순위 비교로 판정한다.

    어제 52주였고 오늘 60일이면 '이어감'. 어제 60일이었고 오늘 52주면 '신규'.
    """
    if today_rank is None:
        return None
    if prev_rank is None:
        return '신규'
    return '이어감' if prev_rank <= today_rank else '신규'


# ─────────────────────────── 근접 ───────────────────────────
def proximity_kind(ev, basis, cfg):
    """근접으로 볼 기준을 고른다.

    hist 의 기준 최고가는 항상 w52 이상, w52 는 d60 이상이다. 따라서 임계 안에
    드는 것 중 가장 상위 라벨이 정보량이 가장 크다. 그걸 고른다.
    """
    b = ev['basis'][basis]
    lim = cfg['proximity']['max_gap_pct']
    # 표시 대상 라벨만 본다. 표에 안 올라가는 등급의 근접은 정보량이 없다.
    show = displayable(cfg)
    for kind in kinds(cfg):
        if kind not in show:
            continue
        g = b['gap'].get(kind)
        if g is not None and 0 < g <= lim and not b['hit'][kind]:
            return kind
    return None
