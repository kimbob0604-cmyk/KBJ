#!/usr/bin/env python3
"""
DART 주요계정 — 히트맵 툴팁용 연간·분기 매출·영업이익 (D-075).

API: fnlttMultiAcnt.json — corp_code 를 쉼표로 최대 100개 묶어 한 번에 받는다.
     한 보고서(bsns_year × reprt_code)마다 매출액·영업이익·당기순이익·자산/부채/자본총계가
     당기(thstrm)·전기(frmtrm)·전전기(bfefrmtrm)로 온다. 분기·반기 보고서에는
     누적(thstrm_add_amount)이 함께 온다.

지뢰 (dart-report/CLAUDE.md 에서 가져옴)
  - 손익은 누적이다. Q2 = 반기 − 1Q, Q3 = 3Q누적 − 반기, Q4 = 연간 − 3Q누적.
    누적 컬럼(thstrm_add_amount)이 있으면 그것이 정답이고, 없으면 thstrm_amount.
  - 연결(CFS)이 없는 회사는 별도(OFS)로 폴백한다. 어느 쪽을 썼는지 fs_div 로 남긴다.
  - 계정명은 회사마다 다르다. 동의어 사전은 dart-report/dartreport/statements.py 의
    TAGS 와 같은 목록을 쓴다 (그쪽이 원본. 바뀌면 여기도 맞춘다).

값은 억원(float)으로 저장하고, 모든 항목에 source='dart' 와 받은 날짜(as_of)가 붙는다.
캐시는 state/financials.json 하나다 — 날짜별이 아니라 종목별이고, stale_days 지나면 갱신.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from ..engine.config import ROOT
from . import dart
from .http import Fetch, get

SOURCE = 'dart'
CACHE = os.path.join(ROOT, 'state', 'financials.json')
EOK = 100_000_000

# reprt_code → 분기. 11011 사업보고서 = 연간(4Q 누적).
REPRT = {'11013': '1Q', '11012': '2Q', '11014': '3Q', '11011': '4Q'}
Q_ORDER = ['1Q', '2Q', '3Q', '4Q']
NAMES = {
    # 보험사는 '보험수익', 증권·은행은 '영업수익'/'순영업수익' 으로 온다. 매출 개념이
    # 제조업과 다르지만 DART 주요계정이 '매출' 자리에 주는 값을 그대로 적는다.
    'rev': ('매출액', '수익(매출액)', '영업수익', '매출', '수익', '보험수익', '순영업수익'),
    'op': ('영업이익', '영업이익(손실)', '영업손익'),
    'ni': ('당기순이익', '당기순이익(손실)', '분기순이익', '반기순이익', '당기순손익'),
}
IS_DIV = {'IS', 'CIS'}


def _norm(s):
    return ''.join(str(s or '').split())


def _num(v):
    if v is None:
        return None
    t = str(v).replace(',', '').strip()
    if t in ('', '-'):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _pick(rows, key, col):
    """계정 동의어로 값 하나. 손익표(IS/CIS) 행을 먼저, 없으면 이름만 본다."""
    names = {_norm(n) for n in NAMES[key]}
    for only_is in (True, False):
        for r in rows:
            if only_is and r.get('sj_div') not in IS_DIV:
                continue
            if _norm(r.get('account_nm')) in names:
                v = _num(r.get(col))
                if v is not None:
                    return v
    return None


def _cum(rows, key):
    """누적값. 누적 컬럼이 있으면 그것, 없으면 당기금액."""
    v = _pick(rows, key, 'thstrm_add_amount')
    return v if v is not None else _pick(rows, key, 'thstrm_amount')


def build_corp(rows, fs_pref='CFS'):
    """한 종목의 모든 보고서 행 → {annual:[…], quarterly:[…], fs_div}.

    rows 는 fnlttMultiAcnt 가 준 dict 들(bsns_year, reprt_code, fs_div, sj_div,
    account_nm, thstrm_amount, thstrm_add_amount, frmtrm_amount, bfefrmtrm_amount).
    """
    by_rep = {}
    for r in rows:
        try:
            y = int(r.get('bsns_year'))
        except (TypeError, ValueError):
            continue
        by_rep.setdefault((y, str(r.get('reprt_code'))), []).append(r)

    used_div = None
    cum, annual, own, is_names = {}, {}, {}, set()
    for (y, rc), rs in by_rep.items():
        q = REPRT.get(rc)
        if not q:
            continue
        pref = [r for r in rs if r.get('fs_div') == fs_pref]
        pick = pref or [r for r in rs if r.get('fs_div') and r.get('fs_div') != fs_pref] or rs
        used_div = used_div or (pick[0].get('fs_div') if pick else None)
        vals = {k: _cum(pick, k) for k in NAMES}
        if any(v is not None for v in vals.values()):
            cum[(y, q)] = vals
            # DART 가 분기 보고서에 함께 주는 3개월값. 차분 결과의 대조용 — 화면에는 안 나간다.
            if rc != '11011':
                own[(y, q)] = {k: _pick(pick, k, 'thstrm_amount') for k in NAMES}
        is_names.update(_norm(r.get('account_nm')) for r in pick if r.get('sj_div') in IS_DIV)
        if rc == '11011':
            # 사업보고서 한 장에 당기·전기·전전기 연간이 다 있다
            for col, yy in (('thstrm_amount', y), ('frmtrm_amount', y - 1),
                            ('bfefrmtrm_amount', y - 2)):
                a = {k: _pick(pick, k, col) for k in NAMES}
                if any(v is not None for v in a.values()) and yy not in annual:
                    annual[yy] = a

    quarterly = []
    for (y, q) in sorted(cum, key=lambda k: (k[0], Q_ORDER.index(k[1]))):
        i = Q_ORDER.index(q)
        prev = cum.get((y, Q_ORDER[i - 1])) if i > 0 else None
        o = own.get((y, q)) or {}
        row = {'year': y, 'q': q, 'label': f'{q}{str(y)[2:]}', 'derived': False}
        for k in NAMES:
            c = cum[(y, q)].get(k)
            # 회사가 분기·반기 보고서에 직접 적은 3개월값. 1Q 는 누적과 같고, 누적 컬럼이
            # 없어 thstrm_amount 를 누적으로 쓴 경우도 같은 값이라 대조 의미가 없다.
            own_v = o.get(k)
            three = (own_v / EOK) if (i > 0 and own_v is not None and c != own_v) else None
            diff = None
            if c is not None:
                if i == 0:
                    diff = c / EOK
                elif prev is not None and prev.get(k) is not None:
                    diff = (c - prev[k]) / EOK
            # 3개월값이 있으면 그것이 회사가 말한 분기다. 차분은 두 보고서 사이의 정정을
            # 타기 때문에(실데이터 163종목 중 8종목이 1% 넘게 어긋남) 폴백으로만 쓴다.
            row[k] = three if three is not None else diff
            row[f'{k}_3m'], row[f'{k}_diff'] = three, diff
            if row[k] is None and c is not None:
                row['derived'] = True   # 보고서는 있는데 값을 못 만들었다 — 화면은 '–' 와 *
        # 보고서는 있는데 직전 누적이 없어 전부 차분 불가여도 분기 자체는 남긴다 —
        # 화면이 '–' 와 * 로 그 사실을 보여 준다. 조용히 빼면 분기가 사라진 이유를 모른다.
        quarterly.append(row)

    ann = [{'year': y, **{k: (v[k] / EOK if v.get(k) is not None else None) for k in NAMES}}
           for y, v in sorted(annual.items())]
    return {'fs_div': used_div, 'annual': ann, 'quarterly': quarterly,
            'is_names': sorted(is_names)}


def _round(d):
    if isinstance(d, dict):
        return {k: _round(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_round(x) for x in d]
    if isinstance(d, float):
        return round(d, 1)
    return d


def fetch_batch(s, corps, year, reprt):
    """fnlttMultiAcnt 한 번. 013(데이터 없음)은 빈 목록이다."""
    js = dart._check(get(s, f'{dart.BASE}/fnlttMultiAcnt.json', params={
        'crtfc_key': dart._key(), 'corp_code': ','.join(corps),
        'bsns_year': str(year), 'reprt_code': reprt}))
    return js.get('list') or []


def collect(codes, cfg, asof=None, log=print):
    """codes 의 재무를 받아 {code: {...}} 로. 실패는 로그로 남기고 받은 것만 돌려준다."""
    f = cfg['financials']
    today = date.today()
    asof = asof or today.isoformat()
    corp_of = dart.corp_codes()
    pairs, missing, via_common = [], [], []
    for c in dict.fromkeys(codes):
        corp = corp_of.get(c)
        if not corp:
            # 우선주(끝자리 5·7·9 등)는 DART 에 corp_code 가 없다. 같은 회사의 보통주
            # (끝자리 0) 재무를 쓴다 — 발행 회사가 같으니 값은 같고, 어느 코드로 받았는지 남긴다.
            common = c[:5] + '0'
            if c[-1] != '0' and common in corp_of:
                corp = corp_of[common]
                via_common.append(c)
        if corp:
            pairs.append((c, corp))
        else:
            missing.append(c)
    if via_common:
        log(f'  우선주 {len(via_common)}종목은 보통주 재무를 쓴다: {", ".join(via_common[:5])}')
    if missing:
        log(f'  DART corp_code 없음 {len(missing)}종목 — 툴팁에서 빈다: {", ".join(missing[:5])}')
    Y = int(str(asof)[:4])
    # 연간 3년은 사업보고서 한 장(당기·전기·전전기)으로 나온다. 3월 말 전에는 전년
    # 사업보고서가 아직 없으니 전전년 것도 받는다. 분기는 올해·작년 것을 받는다.
    reports = [(Y - 2, '11011'), (Y - 1, '11011'), (Y - 1, '11013'), (Y - 1, '11012'),
               (Y - 1, '11014'), (Y, '11013'), (Y, '11012'), (Y, '11014')]
    out, n_calls, n_fail = {}, 0, 0
    size = int(f.get('batch_size', 100))
    for i in range(0, len(pairs), size):
        chunk = pairs[i:i + size]
        # 우선주가 보통주 corp 를 같이 쓰면 corp 하나에 코드가 둘이다.
        codes_of = {}
        for code, corp in chunk:
            codes_of.setdefault(corp, []).append(code)
        rows_by_code = {}

        def one(rep):
            year, rc = rep
            t0 = time.time()
            # 세션은 스레드마다 따로. KBJ P2: DART 는 KBJ 브리지 세션(`dart:` — 키·리미터는 브리지가)
            rows = fetch_batch(dart.session(), list(codes_of), year, rc)
            return rep, rows, time.time() - t0

        with ThreadPoolExecutor(max_workers=int(f.get('workers', 3))) as ex:
            futs = [ex.submit(one, rep) for rep in reports]
            for fu in as_completed(futs):
                try:
                    (year, rc), rows, dt = fu.result()
                    n_calls += 1
                except Fetch as e:
                    n_fail += 1
                    log(f'  DART 호출 실패: {e}')
                    continue
                if dt > 20:
                    log(f'  DART {year}/{rc} {len(rows)}행 · {dt:.0f}초')
                for r in rows:
                    for code in codes_of.get(r.get('corp_code')) or []:
                        rows_by_code.setdefault(code, []).append(r)
        for code, corp in chunk:
            built = build_corp(rows_by_code.get(code, []), fs_pref=f.get('fs_div', 'CFS'))
            if not built['annual'] and not built['quarterly']:
                continue
            built['check'] = quarters_vs_dart(built)
            built['check']['code'] = code
            built['annual'] = built['annual'][-int(f.get('annual_years', 3)):]
            built['quarterly'] = built['quarterly'][-int(f.get('quarters', 4)):]
            out[code] = _round(dict(code=code, corp_code=corp, source=SOURCE,
                                    as_of=today.isoformat(), **built))
    for x in out.values():
        for q in x['quarterly']:
            for k in NAMES:
                q.pop(f'{k}_3m', None)
                q.pop(f'{k}_diff', None)
    checks = [x['check'] for x in out.values() if x.get('check') and x['check'].get('n')]
    bad = [x for x in checks if abs(x['max_diff_pct']) > 1.0]
    log(f'  DART 주요계정 {len(out)}/{len(pairs)}종목 · 호출 {n_calls}회'
        + (f' · 실패 {n_fail}회' if n_fail else ''))
    if checks:
        log(f'  자체 대조(차분 분기 vs DART 3개월 영업이익) {len(checks)}종목 중 1% 초과 {len(bad)}종목'
            + (': ' + ', '.join(f"{x['code']} {x['worst']} {x['max_diff_pct']:+.1f}%" for x in bad[:5]) if bad else ''))
    no_rev = [x for x in out.values() if x['quarterly'] and all(q.get('rev') is None for q in x['quarterly'])]
    if no_rev:
        names = sorted({n for x in no_rev for n in x.get('is_names') or []})
        log(f'  매출 계정을 못 찾은 종목 {len(no_rev)} — 손익 계정명: {", ".join(names[:12])}')
    return out


def quarters_vs_dart(built, key='op'):
    """차분(반기누적 − 1Q누적)으로 구한 분기값 vs 회사가 그 보고서에 직접 적은 3개월값.

    같아야 정상이다. 어긋나면 두 보고서 사이에 정정이 있었다는 신호다 — 화면에는
    3개월값을 쓰고, 여기서는 그 어긋남의 크기를 남긴다. 분기 4개 합 = 연간은
    Q4 = 연간 − 3Q누적 이라 항등식이어서 검증이 아니다.
    반환 {n, max_diff_pct, worst}. 비교할 분기가 없으면 n=0.
    """
    n, worst, worst_pct = 0, None, 0.0
    for q in built['quarterly']:
        d, own = q.get(f'{key}_diff'), q.get(f'{key}_3m')
        if d is None or own is None:
            continue
        n += 1
        pct = (d - own) / abs(own) * 100 if own else (0.0 if abs(d) < 1e-9 else 100.0)
        if abs(pct) > abs(worst_pct):
            worst_pct, worst = pct, q['label']
    return {'n': n, 'max_diff_pct': worst_pct if n else None, 'worst': worst}


def load(path=CACHE):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except ValueError:
        return {}


def save(data, path=CACHE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def refresh(codes, cfg, asof=None, log=print, path=CACHE, force=False):
    """캐시에 없거나 stale_days 지난 종목만 다시 받아 합친다. 반환: 갱신 종목 수."""
    cache = load(path)
    by = cache.get('by_code') or {}
    today = date.today()
    limit = int(cfg['financials'].get('stale_days', 7))

    def stale(code):
        x = by.get(code)
        if not x or force:
            return True
        try:
            return (today - date.fromisoformat(x['as_of'])).days >= limit
        except (KeyError, ValueError):
            return True

    todo = [c for c in dict.fromkeys(codes) if stale(c)]
    if not todo:
        log(f'  재무 캐시 최신 — {len(codes)}종목 그대로')
        return 0
    got = collect(todo, cfg, asof=asof, log=log)
    by.update(got)
    save({'source': SOURCE, 'updated_at': today.isoformat(), 'by_code': by}, path)
    return len(got)
