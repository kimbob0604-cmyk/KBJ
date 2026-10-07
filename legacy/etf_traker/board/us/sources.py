#!/usr/bin/env python3
"""
미국장 수집기 — 유니버스 스냅샷 + 일봉.

무료·무키 조합이 기본이다 (D-076).

  유니버스  api.nasdaq.com 스크리너. 한 번에 전 종목의 종가·등락률·거래량·
            시가총액·섹터·산업을 준다. 레퍼런스 화면이 '나스닥 스크리너' 라고
            적은 그 소스다.
  일봉      stooq.com 의 CSV (`AAPL.US`). 키가 필요 없고 수정주가다.
            yahoo 차트 API 를 대체 어댑터로 함께 둔다 — 한쪽이 막히면 설정
            한 줄로 갈아 끼운다.

이 파일은 **받아서 정규화만 한다.** 판정·집계는 engine 이 한다.
실패를 조용히 삼키지 않는다 — 빈 값 대신 예외를 올리거나 사유 문자열을 함께 낸다
(CLAUDE.md 2장 6번).
"""
import csv
import io
import json
import re
import urllib.parse

from ..ingest import http as H

NASDAQ_SCREENER = 'https://api.nasdaq.com/api/screener/stocks'
NASDAQ_HISTORY = 'https://api.nasdaq.com/api/quote/{sym}/historical'
STOOQ_CSV = 'https://stooq.com/q/d/l/'
YAHOO_CHART = 'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'

# 스크리너는 브라우저 헤더가 아니면 403 을 준다. 사람이 보는 화면과 같은 데이터다.
NASDAQ_REFERER = 'https://www.nasdaq.com/market-activity/stocks/screener'


def _num(v):
    """'$226.80' · '1,234,567' · '0.666%' · '' → float | None.

    못 읽은 값을 0 으로 채우지 않는다. 0 은 '거래가 없었다' 는 사실이고
    None 은 '못 받았다' 는 사실이라 둘을 섞으면 브레드스가 통째로 틀린다.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ('--', 'N/A', 'NA', 'null'):
        return None
    neg = s.startswith('(') and s.endswith(')')
    s = re.sub(r'[\$,%()\s]', '', s)
    if not s or s in ('-', '+'):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def _is_fund(name, cfg):
    if not cfg['universe'].get('exclude_funds'):
        return False
    n = f' {name or ""} '
    return any(tok.lower() in n.lower() for tok in cfg['universe'].get('exclude_name_tokens') or ())


# ─────────────────────────── 유니버스 ───────────────────────────
def parse_screener(payload, cfg):
    """스크리너 응답 → 정규화된 행. 파서를 따로 두어 픽스처로 시험한다.

    반환 (rows, dropped) — dropped 는 왜 빠졌는지별 카운트다. 배너에 그대로 올린다.
    """
    data = (payload or {}).get('data') or {}
    raw = data.get('rows') or data.get('table', {}).get('rows') or []
    rows, drop = [], dict(fund=0, no_price=0, no_mktcap=0)
    for r in raw:
        name = r.get('name') or ''
        tic = (r.get('symbol') or '').strip().upper()
        if not tic:
            continue
        if _is_fund(name, cfg):
            drop['fund'] += 1
            continue
        close = _num(r.get('lastsale'))
        mcap = _num(r.get('marketCap'))
        if close is None:
            drop['no_price'] += 1
            continue
        if mcap is None:
            drop['no_mktcap'] += 1
        vol = _num(r.get('volume'))
        rows.append(dict(
            ticker=tic, name=name.strip(), close=close,
            chg_pct=_num(r.get('pctchange')), net_change=_num(r.get('netchange')),
            volume=vol, mktcap=mcap,
            turnover=(close * vol) if (vol is not None) else None,
            turnover_is_estimate=True,      # 종가×거래량 추정. 실 거래대금이 아니다
            sector_raw=(r.get('sector') or '').strip(),
            industry_raw=(r.get('industry') or '').strip(),
            country=(r.get('country') or '').strip(),
            exchange=(r.get('exchange') or '').strip().upper() or None,
            source='nasdaq-screener'))
    return rows, drop


def screener(cfg, sess=None, exchanges=None):
    """전 종목 스냅샷. 거래소별로 나눠 받아 합친다."""
    sess = sess or H.session(referer=NASDAQ_REFERER)
    out, drop = [], dict(fund=0, no_price=0, no_mktcap=0)
    seen = set()
    for ex in (exchanges or cfg['universe']['exchanges']):
        params = dict(tableonly='true', limit='10000', offset='0',
                      exchange=ex, download='true')
        r = sess.get(NASDAQ_SCREENER, params=params, timeout=H.TIMEOUT)
        if r.status_code != 200:
            raise H.Fetch(f'나스닥 스크리너 {ex} HTTP {r.status_code} — {H.why(r)[:200]}')
        try:
            payload = r.json()
        except ValueError as e:
            raise H.Fetch(f'나스닥 스크리너 {ex} 응답이 JSON 이 아니다: {e}') from e
        rows, d = parse_screener(payload, cfg)
        if not rows:
            raise H.Fetch(f'나스닥 스크리너 {ex} 가 0행을 줬다 — 응답 형식 변경 의심')
        for k, v in d.items():
            drop[k] = drop.get(k, 0) + v
        for row in rows:
            if row['ticker'] in seen:
                continue
            seen.add(row['ticker'])
            row['exchange'] = row.get('exchange') or ex
            out.append(row)
    return out, drop


# ─────────────────────────── 일봉 ───────────────────────────
def nasdaq_symbol(ticker):
    """나스닥 API 의 심볼 표기. 클래스주는 마침표가 슬래시다 (BRK.B → BRK/B).

    **경로에 넣을 때는 인코딩해야 한다.** 스크리너가 주는 티커 자체가 이미
    `BRK/B` 라서 그대로 붙이면 URL 이 .../quote/BRK/B/historical 로 갈라져
    엉뚱한 경로가 된다 — run #2 에서 이 한 종목만 수집에 실패했다.
    """
    return urllib.parse.quote(ticker.upper().replace('.', '/'), safe='')


def nasdaq_symbol_forms(ticker):
    """클래스주 표기 후보를 순서대로.

    인코딩만으로는 안 됐다 — run #4 에서 BRK/A·BRK/B 가 HTTP 404 였다.
    스크리너는 `BRK/B` 로 주는데 히스토리 쪽이 같은 표기를 받는다는 보장이 없다.
    **추측해서 하나를 고르지 말고 순서대로 물어본다.** 되는 표기가 답이다.
    """
    t = ticker.upper()
    forms = [t, t.replace('.', '/'), t.replace('/', '.'), t.replace('/', '-'),
             t.replace('.', '-')]
    out = []
    for f in forms:
        q = urllib.parse.quote(f, safe='')
        if q not in out:
            out.append(q)
    return out


def stooq_symbol(ticker):
    """stooq 심볼. 마침표가 하이픈이고 `.us` 가 붙는다 (BRK.B → brk-b.us)."""
    return ticker.lower().replace('.', '-') + '.us'


def parse_nasdaq_history(payload):
    """나스닥 히스토리 응답 → 오름차순 일봉.

    스크리너와 **같은 호스트**다. 러너에서 stooq 는 빈 응답, yahoo 는 429 를
    주는데 이 호스트는 스크리너가 통과하므로 여기가 가장 확실한 일봉 경로다
    (run #1 실측, D-077).

    날짜는 `MM/DD/YYYY`, 가격은 `$226.80` 꼴이다. 거래량이 빠진 행이 섞여 오므로
    없는 값은 None 으로 둔다 — 0 으로 채우면 거래량 배수가 거짓이 된다.
    """
    tbl = (((payload or {}).get('data') or {}).get('tradesTable') or {})
    rows = []
    for r in tbl.get('rows') or []:
        d = (r.get('date') or '').strip()
        try:
            mm, dd, yy = d.split('/')
            asof = f'{int(yy):04d}-{int(mm):02d}-{int(dd):02d}'
        except (ValueError, AttributeError):
            continue
        close = _num(r.get('close'))
        if close is None:
            continue
        rows.append(dict(asof=asof, open=_num(r.get('open')), high=_num(r.get('high')),
                         low=_num(r.get('low')), close=close,
                         volume=_num(r.get('volume')), source='nasdaq'))
    rows.sort(key=lambda r: r['asof'])
    return rows


def nasdaq_daily(ticker, sess=None, start=None, end=None, assetclass='stocks'):
    """한 종목의 일봉. 스크리너와 같은 호스트라 헤더도 같게 간다.

    ETF(SPY 등)는 assetclass='etf' 로 물어야 한다."""
    from datetime import date, timedelta
    sess = sess or H.session(referer=NASDAQ_REFERER)
    end = end or date.today().isoformat()
    start = start or (date.today() - timedelta(days=760)).isoformat()
    params = dict(assetclass=assetclass, fromdate=start, todate=end, limit='9999')
    # 대부분은 첫 표기에서 끝난다. 클래스주만 두세 번 더 물어본다.
    last = None
    for sym in nasdaq_symbol_forms(ticker):
        r = sess.get(NASDAQ_HISTORY.format(sym=sym), params=params, timeout=H.TIMEOUT)
        if r.status_code == 200:
            break
        last = f'HTTP {r.status_code} ({sym})'
    else:
        raise H.Fetch(f'나스닥 히스토리 {ticker} 모든 심볼 표기 실패 — {last}')
    try:
        payload = r.json()
    except ValueError as e:
        raise H.Fetch(f'나스닥 히스토리 {ticker} 응답이 JSON 이 아니다: {e}') from e
    rows = parse_nasdaq_history(payload)
    if not rows:
        # 사유는 응답이 들고 있다. 빈 리스트로 돌려주면 '거래가 없었다' 로 읽힌다.
        why = (payload or {}).get('status', {}).get('bCodeMessage') or payload.get('message')
        raise H.Fetch(f'나스닥 히스토리 {ticker} 0행 — {str(why)[:120]}')
    return rows


def parse_stooq(text):
    """stooq CSV → 오름차순 일봉. 헤더가 없거나 'N/D' 면 빈 리스트."""
    rows = []
    for rec in csv.DictReader(io.StringIO(text or '')):
        d = (rec.get('Date') or '').strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
            continue
        def f(k):
            try:
                return float(rec[k])
            except (TypeError, ValueError, KeyError):
                return None
        rows.append(dict(asof=d, open=f('Open'), high=f('High'), low=f('Low'),
                         close=f('Close'), volume=f('Volume'), source='stooq'))
    rows.sort(key=lambda r: r['asof'])
    return rows


def stooq_daily(ticker, sess=None, start=None):
    """한 종목의 일봉."""
    sess = sess or H.session()
    params = {'s': stooq_symbol(ticker), 'i': 'd'}
    if start:
        params['d1'] = start.replace('-', '')
    r = sess.get(STOOQ_CSV, params=params, timeout=H.TIMEOUT)
    if r.status_code != 200:
        raise H.Fetch(f'stooq {ticker} HTTP {r.status_code}')
    txt = r.text or ''
    if txt.strip().lower().startswith('no data') or 'Exceeded the daily hits limit' in txt:
        raise H.Fetch(f'stooq {ticker} 데이터 없음 — {txt.strip()[:80]}')
    rows = parse_stooq(txt)
    if not rows:
        # 러너에서 실제로 이랬다 — HTTP 200 에 본문이 비어 있다(run #1).
        # 빈 리스트로 넘기면 '상장 첫날' 처럼 보이므로 사유를 올린다.
        raise H.Fetch(f'stooq {ticker} 0행 (HTTP 200, 본문 {len(txt)}바이트) '
                      f'— 데이터센터 IP 차단이나 한도로 보인다')
    return rows


def parse_yahoo(payload):
    """yahoo v8 차트 → 오름차순 일봉. 조정 종가가 아니라 원 종가를 쓴다.

    yahoo 는 `adjclose` 를 따로 준다. 신고가 판정은 분할이 반영된 시계열을
    요구하므로 분할 조정은 필요하지만 배당 조정은 필요 없다. v8 의 `close` 는
    이미 분할 조정값이고 배당은 반영되지 않는다 — 우리가 원하는 그것이다.
    """
    res = (((payload or {}).get('chart') or {}).get('result') or [None])[0]
    if not res:
        return []
    ts = res.get('timestamp') or []
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    import datetime as _dt
    rows = []
    for i, t in enumerate(ts):
        def g(k):
            v = (q.get(k) or [])
            return v[i] if i < len(v) else None
        c = g('close')
        if c is None:
            continue
        rows.append(dict(asof=_dt.datetime.utcfromtimestamp(t).date().isoformat(),
                         open=g('open'), high=g('high'), low=g('low'), close=c,
                         volume=g('volume'), source='yahoo'))
    rows.sort(key=lambda r: r['asof'])
    return rows


def yahoo_daily(ticker, sess=None, rng='2y'):
    sess = sess or H.session()
    r = sess.get(YAHOO_CHART.format(sym=ticker.upper()),
                 params={'range': rng, 'interval': '1d'}, timeout=H.TIMEOUT)
    if r.status_code != 200:
        raise H.Fetch(f'yahoo {ticker} HTTP {r.status_code}')
    return parse_yahoo(r.json())


def history(ticker, cfg, sess=None, start=None):
    """설정이 고른 어댑터로 일봉을 받는다."""
    which = cfg['sources'].get('history', 'nasdaq')
    if which == 'nasdaq':
        return nasdaq_daily(ticker, sess, start)
    if which == 'yahoo':
        return yahoo_daily(ticker, sess)
    if which == 'stooq':
        return stooq_daily(ticker, sess, start)
    raise ValueError(f'모르는 일봉 소스: {which}')


# ─────────────────────────── 점검 ───────────────────────────
# 수정주가 여부를 확인할 대조군. 둘 다 최근 2년 안에 분할이 있었다.
#   NVDA 2024-06-10 10:1 · AAPL 은 분할이 없다(대조군의 대조군)
SPLIT_PROBE = (('NVDA', '2024-06-10', '10:1 분할'),)


def adjust_probe(cfg, sess=None):
    """일봉이 수정주가인지 본다. 판정하지 말고 **사실만** 돌려준다.

    분할일 근처에 하루 −50% 넘는 계단이 있으면 미조정이다. 미조정이면 그 종목의
    52주 창이 분할 지점에서 잘려(D-001 가드) 한동안 52주 라벨이 안 뜬다.
    어느 쪽인지 알아야 배너에 적을 수 있다.
    """
    out = []
    for tic, when, note in SPLIT_PROBE:
        try:
            rows = history(tic, cfg, sess)
        except Exception as e:                       # noqa: BLE001
            out.append((f'수정주가 {tic}', False, H.scrub(e)[:120]))
            continue
        step = None
        for a, b in zip(rows, rows[1:]):
            if a.get('close') and b.get('close'):
                r = b['close'] / a['close'] - 1
                if abs(r) > 0.45:
                    step = (b['asof'], round(r * 100, 1))
        if step:
            out.append((f'수정주가 {tic}', False,
                        f'{step[0]} 하루 {step[1]:+.1f}% 계단 — **미조정**({note}). '
                        f'분할 종목은 그 지점부터 룩백이 잘린다'))
        else:
            out.append((f'수정주가 {tic}', True,
                        f'{when} {note} 근처에 계단 없음 — 수정주가로 보인다 '
                        f'({len(rows)}행)'))
    return out


def probe(cfg):
    """--us-check 가 부른다. 무엇이 되고 무엇이 안 되는지 한 줄씩."""
    out = []
    try:
        rows, drop = screener(cfg, exchanges=['NASDAQ'])
        mc = sum(1 for r in rows if r.get('mktcap'))
        out.append(('나스닥 스크리너', bool(rows),
                    f'{len(rows)}종목 · 시총 있는 것 {mc} · 제외 {drop}'))
    except Exception as e:                       # noqa: BLE001
        out.append(('나스닥 스크리너', False, H.scrub(e)[:200]))
    for name, fn in (('nasdaq 일봉', lambda: nasdaq_daily('AAPL')),
                     ('stooq 일봉', lambda: stooq_daily('AAPL')),
                     ('yahoo 일봉', lambda: yahoo_daily('AAPL', rng='1mo'))):
        try:
            bars = fn()
            last = bars[-1] if bars else None
            out.append((name, bool(bars),
                        f'{len(bars)}행 · 마지막 {last["asof"] if last else "-"} '
                        f'{last["close"] if last else "-"}'))
        except Exception as e:                   # noqa: BLE001
            out.append((name, False, H.scrub(e)[:200]))
    out.extend(adjust_probe(cfg))
    return out
