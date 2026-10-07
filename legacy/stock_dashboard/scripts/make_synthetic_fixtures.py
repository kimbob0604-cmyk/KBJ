#!/usr/bin/env python3
"""KBJ P1 합성 데이터 생성기 — stock_dashboard 검사 스크립트가 쓰는 로그인 등급 실데이터를 대신한다.

ADR 0001 U3: 네이버·KIS·Yahoo 실측 값은 공개 레포에 넣지 않는다. 같은 형태(키·필드·자료형·행 수
규모)의 합성 값으로 바꾸고, 그 값을 만든 이 스크립트를 함께 둔다. 시드 고정이라 몇 번을 돌려도
바이트 단위로 같은 결과가 나온다. 기대값은 손으로 맞추지 않고 여기서 입력값으로부터 계산한다.

만드는 것
  1. data/naver_universe_seed.json — 원본은 네이버 수집 4,063종목(제외 등급). 같은 구조
     {_note, source_date, stock_count, stocks{코드: {name, sectors[1], market_cap?}}} 의 합성 유니버스.
     - 종목 4,063개, ETF·ETN 이름 1,255개, market_cap 없는 종목 146개(그중 ETF 27개) — 원본과 같은 규모.
     - 코드는 가상 코드(9xxxxx). 이름은 가상 이름('가상전자0001', 'KODEX 가상지수0001' 등).
     - 예외 3개: 검사 스크립트가 코드로 직접 부르는 '005930'(시총 1위여야 함)·'069500'(ETF 여야 함)·
       '138930'(이름에 'BNK' 가 들어간 은행주 — ETF 오탐 회귀). 이름은 검사 스크립트 안의 리터럴과 같게 두고
       시총 등 값은 전부 합성이다.
  2. scripts/check_futures_us_index.py 안의 인라인 값 — KIS 선물 현재가 응답(output1) 2건과 그 기대 문구,
     야후 '마지막 일봉 종가 NaN' 재현 프레임(종가 2개 + last_price)과 그 기대 문구.
  3. scripts/check_ohlcv_autofill.py 안의 네이버 일봉 응답 1행과 그 기대 튜플.
  4. scripts/check_etf_marking.py 재현 표에서 거래대금 상위 5행(ETF 3·실제 종목 2)의 등락·거래대금
     (원본은 2026-09-22 시황에 실린 값 — 검증 단계에서 추가 교체, MIGRATION.md).

사용 (legacy/stock_dashboard 에서)
  python scripts/make_synthetic_fixtures.py            # 1 을 쓴다
  python scripts/make_synthetic_fixtures.py --print    # 2~4 의 인라인 값을 출력한다(검사 스크립트에 붙인 값)
  python scripts/make_synthetic_fixtures.py --check    # 1 이 파일과 같은지, 2~4 가 검사 스크립트에 그대로 있는지
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / 'data' / 'naver_universe_seed.json'
SEED = 20261007

# server.py ETF_PATTERNS 중 이름 맨 앞에 오는 운용사 브랜드(검사 스크립트의 brand 판정과 같은 꼴)
ETF_BRANDS = ('KODEX', 'TIGER', 'KBSTAR', 'KOSEF', 'HANARO', 'ARIRANG', 'KINDEX', 'TREX', 'ACE',
              'SOL', 'TIMEFOLIO', 'BNK', 'FOCUS', 'WON', 'SMART', 'PLUS', 'RISE', 'WOORI', '1Q',
              'DAISHIN343', 'HK', 'KCGI', 'KIWOOM', 'KoAct', 'MIDAS', 'TIME', 'TRUSTON', 'UNICORN',
              'VITA', '마이티', '에셋플러스', '파워')
ETF_TAILS = ('', '', '', ' 레버리지', ' 인버스', ' 선물', '(H)', ' 액티브', ' TRF')
ETN_ISSUERS = ('가상증권', '모의증권', '예시증권')
STOCK_WORDS = ('전자', '화학', '바이오', '건설', '소재', '기계', '식품', '통신', '에너지', '금융',
               '유통', '게임', '엔터', '부품', '장비', '제약', '해운', '철강', '보험', '조선')
SECTORS = tuple(f'가상업종{i:02d}' for i in range(1, 80))       # 원본 79개 업종과 같은 규모

N_TOTAL, N_ETF, N_NOCAP, N_NOCAP_ETF = 4063, 1255, 146, 27
ANCHORS = {   # 검사 스크립트가 코드로 직접 부르는 종목(이름은 그 스크립트의 리터럴)
    '005930': ('삼성전자', '가상업종01', False),
    '069500': ('KODEX 200', '기타', True),
    '138930': ('BNK금융지주', '가상업종02', False),
}


def build_seed() -> dict:
    rng = random.Random(SEED)
    codes = sorted(rng.sample(range(900000, 1000000), N_TOTAL - len(ANCHORS)))
    codes = [f'{c:06d}' for c in codes]
    etf_codes = set(rng.sample(codes, N_ETF - 1))                  # + 069500
    ord_codes = [c for c in codes if c not in etf_codes]
    nocap = set(rng.sample(sorted(etf_codes), N_NOCAP_ETF))
    nocap |= set(rng.sample(ord_codes, N_NOCAP - N_NOCAP_ETF))

    stocks: dict[str, dict] = {}
    n_etf = n_ord = 0
    for c in codes:
        if c in etf_codes:
            n_etf += 1
            if n_etf % 25 == 0:      # ETN — 이름 뒤에 ' ETN'
                name = f'{rng.choice(ETN_ISSUERS)} 가상지수{n_etf:04d} ETN'
            else:
                name = f'{rng.choice(ETF_BRANDS)} 가상지수{n_etf:04d}{rng.choice(ETF_TAILS)}'
            row = {'name': name, 'sectors': ['기타']}
            exp = rng.gauss(10.6, 0.8)
        else:
            n_ord += 1
            row = {'name': f'가상{rng.choice(STOCK_WORDS)}{n_ord:04d}', 'sectors': [rng.choice(SECTORS)]}
            exp = rng.gauss(11.0, 0.75)
        if c not in nocap:
            row['market_cap'] = int(round(10 ** min(max(exp, 9.0), 14.3), -6))
        stocks[c] = row
    caps = [v.get('market_cap', 0) for v in stocks.values()]
    for code, (name, sector, _is_etf) in ANCHORS.items():
        stocks[code] = {'name': name, 'sectors': [sector]}
    stocks['005930']['market_cap'] = max(caps) * 7 + 123_000_000    # 시총 1위, 1e14 초과
    stocks['069500']['market_cap'] = int(round(10 ** 13.2, -6))
    stocks['138930']['market_cap'] = int(round(10 ** 12.4, -6))
    stocks = dict(sorted(stocks.items()))
    return {
        '_note': 'KBJ 합성 유니버스 시드 — 원본(네이버 수집)과 같은 구조의 가상 종목·코드. '
                 'scripts/make_synthetic_fixtures.py 로 만든다(seed 20261007). 실데이터 아님.',
        'source_date': '2026-01-02 17:00:00',
        'stock_count': len(stocks),
        'stocks': stocks,
    }


def seed_bytes() -> bytes:
    return json.dumps(build_seed(), ensure_ascii=False).encode('utf-8')


# ── 검사 스크립트 인라인 값 ─────────────────────────────────────────────────

def _tick(x: float, t: float = 0.05) -> float:
    return round(round(x / t) * t, 2)


def _fut(rng: random.Random, name: str, last_tr: str, base: float, chg_sign: int, oi_sign: int,
         vol_scale: int) -> dict:
    close = _tick(base)
    change = _tick(chg_sign * rng.uniform(2.0, 18.0))
    pct = round(change / (close - change) * 100, 2)
    opn = _tick(close + rng.uniform(-12.0, 12.0))
    high = _tick(max(opn, close) + rng.uniform(0.5, 8.0))
    low = _tick(min(opn, close) - rng.uniform(0.5, 8.0))
    oi = rng.randrange(2_000, 200_000) if vol_scale > 100 else rng.randrange(1_000, 9_000)
    return {'hts_kor_isnm': name, 'futs_prpr': f'{close:.2f}', 'futs_prdy_vrss': f'{change:.2f}',
            'futs_prdy_ctrt': f'{pct:.2f}', 'acml_vol': str(rng.randrange(vol_scale // 2, vol_scale)),
            'hts_otst_stpl_qty': str(oi),
            'otst_stpl_qty_icdc': str(oi_sign * rng.randrange(1, 3_000 if vol_scale > 100 else 50)),
            'futs_oprc': f'{opn:.2f}', 'futs_hgpr': f'{high:.2f}', 'futs_lwpr': f'{low:.2f}',
            'futs_last_tr_date': last_tr}


def _sgn(v) -> str:
    return '+' if v >= 0 else ''


def build_inline() -> dict:
    rng = random.Random(SEED + 1)
    base = rng.uniform(500.0, 900.0)
    near = _fut(rng, 'F 202612', '20261210', base, +1, +1, 120_000)
    far = _fut(rng, 'F 202703', '20270311', base - rng.uniform(5.0, 20.0), +1, -1, 40)
    f = {k: float(near[k]) for k in ('futs_oprc', 'futs_hgpr', 'futs_lwpr', 'futs_prpr', 'futs_prdy_ctrt')}
    n_oi, n_oc = int(near['hts_otst_stpl_qty']), int(near['otst_stpl_qty_icdc'])
    f_oi, f_oc = int(far['hts_otst_stpl_qty']), int(far['otst_stpl_qty_icdc'])
    fut_expect = {
        'ohlc': (f['futs_oprc'], f['futs_hgpr'], f['futs_lwpr'], f['futs_prpr']),
        'oi': (n_oi, n_oc, f_oc),
        'item0': f"<b>근월물</b> F 202612 · 종가 {f['futs_prpr']:,.2f} "
                 f"{_sgn(f['futs_prdy_ctrt'])}{f['futs_prdy_ctrt']:.2f}%",
        'item1': f"  시 {f['futs_oprc']:,.2f} · 고 {f['futs_hgpr']:,.2f} · 저 {f['futs_lwpr']:,.2f}",
        'item2': f'  미결제약정 {n_oi:,} ({_sgn(n_oc)}{n_oc:,})',
        'item5': f'  미결제약정 {f_oi:,} ({_sgn(f_oc)}{f_oc:,})',
    }
    # 야후 — 마지막 일봉 종가가 NaN 인 날. 9/18·9/21 종가 + last_price
    rng = random.Random(SEED + 2)
    c18 = round(rng.uniform(4000.0, 6000.0), 1)
    c21 = round(c18 * (1 + rng.uniform(-0.02, 0.02)), 1)
    lp = round(c21 * (1 + rng.uniform(0.002, 0.015)), 1)
    us = {'closes': (c18, c21), 'last_price': lp,
          'line': f'S&P 500 {lp:,.2f} {_sgn(lp / c21 - 1)}{(lp / c21 - 1) * 100:.2f}% <i>(09/22 종가)</i>'}
    # 네이버 일봉 응답 한 행(시가·고가·저가·종가·거래량·외국인소진율)
    rng = random.Random(SEED + 3)
    c = rng.randrange(400, 900) * 100
    o = c + rng.randrange(-30, 30) * 100
    h = max(o, c) + rng.randrange(1, 40) * 100
    lo = min(o, c) - rng.randrange(1, 40) * 100
    v = rng.randrange(1_000_000, 9_000_000)
    fr = round(rng.uniform(10.0, 60.0), 2)
    naver = {'row': (o, h, lo, c, v, fr),
             'expect': ('005930', '2026-09-16', float(o), float(h), float(lo), float(c), float(v))}
    # check_etf_marking 재현 표 — 거래대금 상위 5(실제 종목 2 > ETF 3 > 나머지 9,000 이하)의 등락·거래대금.
    # 원본은 09-22 시황에 실린 값이었다. 순서 관계(상위 5 안에 ETF 가 섞임)와 |등락| < 5 만 지킨다.
    rng = random.Random(SEED + 4)
    real_v = sorted((rng.randrange(40_000, 70_000) for _ in range(2)), reverse=True)
    etf_v = sorted((rng.randrange(10_000, 30_000) for _ in range(3)), reverse=True)
    chg = [round(rng.uniform(-3.0, 3.0), 1) for _ in range(5)]
    etf_marking = {
        'leaked': [('069500', 'KODEX 200', 40000, chg[0], etf_v[0]),
                   ('122630', 'KODEX 레버리지', 20000, chg[1], etf_v[1]),
                   ('102110', 'TIGER 200', 40000, chg[2], etf_v[2])],
        'real': [('000660', 'SK하이닉스', 300000, chg[3], real_v[0]),
                 ('005930', '삼성전자', 80000, chg[4], real_v[1])],
    }
    return {'near': near, 'far': far, 'fut_expect': fut_expect, 'us': us, 'naver': naver,
            'etf_marking': etf_marking}


def inline_snippets() -> dict[str, list[str]]:
    """검사 스크립트에 그대로 있어야 하는 문자열(파일별)."""
    d = build_inline()
    fe, us, nv = d['fut_expect'], d['us'], d['naver']
    o, h, lo, c, v, fr = nv['row']
    em = d['etf_marking']
    return {
        'check_etf_marking.py': [repr(t) + ',' for t in em['leaked'] + em['real']],
        'check_futures_us_index.py': [
            f"'A01612': {d['near']!r},", f"'A01703': {d['far']!r},",
            f"== {fe['ohlc']!r},", f"== {fe['oi']!r},",
            repr(fe['item0']), repr(fe['item1']), repr(fe['item2']), repr(fe['item5']),
            f"Frame([{us['closes'][0]!r}, {us['closes'][1]!r}, nan], days(18, 21, 22))",
            f"last_price={{'^GSPC': {us['last_price']!r}}}", repr(us['line']),
        ],
        'check_ohlcv_autofill.py': [
            f"\"['20260916', {o}, {h}, {lo}, {c}, {v}, {fr}],\"",
            f"\"['20260916', {o}, {h}, {lo}, {c}, {v}],\"",
            "'2026-09-16', " + ', '.join(repr(x) for x in nv['expect'][2:5]) + ',',
            ', '.join(repr(x) for x in nv['expect'][5:]) + '),',
        ],
    }


def main(argv: list[str]) -> int:
    if '--print' in argv:
        d = build_inline()
        print(json.dumps(d, ensure_ascii=False, indent=1))
        for f, snips in inline_snippets().items():
            print(f'# {f}')
            for s in snips:
                print('   ', s)
        return 0
    if '--check' in argv:
        bad = []
        if not SEED_PATH.exists() or SEED_PATH.read_bytes() != seed_bytes():
            bad.append(f'{SEED_PATH.relative_to(ROOT)} 가 생성기 출력과 다르다')
        for f, snips in inline_snippets().items():
            text = (ROOT / 'scripts' / f).read_text(encoding='utf-8')
            bad += [f'{f}: 생성값이 없다 — {s[:60]}' for s in snips if s not in text]
        for b in bad:
            print('FAIL', b)
        print('합성 데이터 재현 확인: ' + ('실패' if bad else '통과'))
        return 1 if bad else 0
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_bytes(seed_bytes())
    s = build_seed()['stocks']
    print(f'{SEED_PATH.relative_to(ROOT)}: {len(s):,}종목')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
