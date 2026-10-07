#!/usr/bin/env python3
"""
국내 ETF 구성종목 일일 변동 트래커 — 테마별 리포트판

  python tracker.py --init                    유니버스 구축 + 오늘자 스냅샷
  python tracker.py --run                     수집 → 비교 → 텔레그램 발송
  python tracker.py --run --no-send           발송 없이 콘솔 확인
  python tracker.py --run --date 2026-08-03 --prev 2026-07-28
  python tracker.py --themes                  테마 분류 현황 보기
  python tracker.py --research 조선            특정 테마 심층 조회
  python tracker.py --backfill 2026-07-01 2026-08-03   과거 구간 채우기
  python tracker.py --check                   설정·소스·텔레그램 연결 점검
  python tracker.py --verify                  데이터 품질 10개 항목 실측 검증

설정: 같은 폴더의 .env 파일을 자동으로 읽는다 (.env.example 참고).
      TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 만 채우면 된다.
"""
import argparse, json, os, re, sqlite3, statistics as st, sys, time
import concurrent.futures as cf
from collections import defaultdict
from datetime import date, datetime, timedelta
import requests
from collectors import ADAPTERS
import themes as TH
import market as MK
import report as REPORT
import dash as DASH
import render as RENDER

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'etf.db')


def _load_env():
    """.env 파일이 있으면 환경변수로 올린다 (이미 설정된 값은 덮어쓰지 않는다)."""
    path = os.path.join(HERE, '.env')
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


_load_env()


def _envnum(key, default, cast=float):
    try:
        return cast(os.environ[key])
    except (KeyError, ValueError):
        return default


WORKERS   = _envnum('WORKERS', 6, int)       # 동시 요청 수. 운용사 서버 배려해서 6 이하 권장
QTY_FLOOR = _envnum('QTY_FLOOR', 100, int)   # 이 수량 미만은 1주 단위 반올림 노이즈로 보고 제외
ACTION_PP = _envnum('ACTION_PP', 2.0)        # CU 재산정 보정 후 이 %p 이상 움직여야 '실제 액션'
RETRIES   = _envnum('RETRIES', 3, int)       # 수집 실패 시 재시도 횟수
EMPTY_LIMIT = _envnum('EMPTY_LIMIT', 3, int) # 국내종목 0개가 이만큼 연속되면 추적 제외
KEEP_DAYS = _envnum('KEEP_DAYS', 120, int)   # 이보다 오래된 스냅샷은 자동 삭제 (0=보관)
NAVER_ETF = 'https://finance.naver.com/api/sise/etfItemList.nhn'

DDL = """
CREATE TABLE IF NOT EXISTS fund(
  fund_id TEXT PRIMARY KEY, issuer TEXT, fund_key TEXT, ticker TEXT,
  name TEXT, theme TEXT, is_active INT, mktcap REAL, depth TEXT DEFAULT 'full', track INT DEFAULT 1, empty_streak INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS holding(
  fund_id TEXT, asof TEXT, code TEXT, name TEXT, qty REAL, wt REAL, val REAL,
  PRIMARY KEY(fund_id, asof, code));
CREATE INDEX IF NOT EXISTS ix_h_asof ON holding(asof);
CREATE TABLE IF NOT EXISTS etf_ticker(code TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS change_log(
  run_date TEXT, fund_id TEXT, code TEXT, kind TEXT, name TEXT,
  asof TEXT, prev_asof TEXT, gap_days INT,
  prev_qty REAL, cur_qty REAL, prev_wt REAL, cur_wt REAL, qty_pct REAL, qty_pct_adj REAL,
  PRIMARY KEY(run_date, fund_id, code, kind));
CREATE INDEX IF NOT EXISTS ix_c_run ON change_log(run_date);
"""


def db():
    c = sqlite3.connect(DB)
    # 구버전 change_log(전역 날짜쌍 기준)는 스키마가 달라 재생성한다. 원본 holding 이 있으니 손실 없다.
    cols = [r[1] for r in c.execute("PRAGMA table_info(change_log)")]
    if cols and 'run_date' not in cols:
        c.execute('DROP TABLE change_log')
        c.commit()
    c.executescript(DDL)
    c.executescript(MK.DDL)
    return c


# ────────────────────── 유니버스: 어댑터 + 네이버 종목명 조인 ──────────────────────
def naver_names():
    """전체 상장 ETF 1,100여개의 종목코드 → (종목명, 시총, 탭코드). 종목명 마스터 소스."""
    r = requests.get(NAVER_ETF, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/sise/etf.naver'})
    return {x['itemcode']: (x['itemname'], x.get('marketSum') or 0, x.get('etfTabCode'))
            for x in r.json()['result']['etfItemList']}


def build_universe(conn, issuers=None):
    """전용 어댑터를 먼저 채우고, 남은 ETF는 네이버 TOP10 폴백으로 메운다."""
    nv = naver_names()
    # 커버드콜·재간접 ETF는 다른 ETF를 담는다. 그건 종목 시그널이 아니므로 걸러내야 한다.
    conn.executemany('INSERT OR REPLACE INTO etf_ticker VALUES(?,?)',
                     [(c, v[0]) for c, v in nv.items()])
    conn.commit()

    def accept(nm, tab):
        """추적 대상인지 판정 → (theme, is_active) 또는 None"""
        if tab not in (1, 2):          # 네이버 탭 1=국내지수, 2=국내업종/테마
            return None
        th = TH.classify(nm)
        if th is None:                 # 레버리지·인버스·채권 등 파생/구조화
            return None
        return th, (1 if TH.is_active(nm) else 0)

    rows, covered, skipped = [], set(), 0
    dedicated = [k for k in ADAPTERS if k != 'naver']
    for key in dedicated:
        C = ADAPTERS[key]
        if issuers and key not in issuers:
            continue
        uni, last = None, None
        for attempt in range(RETRIES):      # 여기서 실패하면 그 운용사가 통째로 폴백으로 밀린다
            try:
                uni = C().universe()
                break
            except Exception as e:
                last = type(e).__name__
                if attempt < RETRIES - 1:
                    time.sleep(2.0 * (attempt + 1))
        if uni is None:
            print(f'  [{C.NAME}] universe 실패({last}) — 네이버 TOP10 폴백으로 대체됨')
            continue
        n_ok = 0
        for fund_key, ticker, name in uni:
            meta = nv.get(ticker or '')
            nm = (meta[0] if meta else None) or name
            if not nm:
                skipped += 1; continue
            a = accept(nm, meta[2] if meta else None)
            if not a:
                skipped += 1; continue
            rows.append((f'{key}:{fund_key}', key, fund_key, ticker, nm, a[0], a[1],
                         float(meta[1] if meta else 0), C.DEPTH, 1, 0))
            covered.add(ticker); n_ok += 1
        print(f'  [{C.NAME}] {n_ok}개 (전체 {len(uni)})')

    # 폴백: 전용 어댑터가 커버하지 못한 국내 ETF 전부
    if not issuers or 'naver' in issuers:
        C = ADAPTERS['naver']
        n_ok = 0
        for code, (nm, cap, tab) in nv.items():
            if code in covered:
                continue
            a = accept(nm, tab)
            if not a:
                continue
            rows.append((f'naver:{code}', 'naver', code, code, nm, a[0], a[1],
                         float(cap or 0), C.DEPTH, 1, 0))
            n_ok += 1
        print(f'  [{C.NAME}] {n_ok}개 (전용 어댑터 미커버분)')

    conn.executemany('INSERT OR REPLACE INTO fund VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
    conn.commit()
    return len(rows), skipped


# ─────────────────────────────── 스냅샷 수집 ───────────────────────────────
_pool = {}


def adapter(key):
    if key not in _pool:
        _pool[key] = ADAPTERS[key]()
    return _pool[key]


def snapshot(conn, asof, only=None, limit=None, quiet=False, drop_empty=False):
    q = 'SELECT fund_id, issuer, fund_key, name FROM fund WHERE track=1'
    if only:
        q += ' AND issuer IN (%s)' % ','.join(f"'{x}'" for x in only)
    q += ' ORDER BY mktcap DESC'
    funds = conn.execute(q).fetchall()
    if limit:
        funds = funds[:limit]

    def job(f):
        fid, issuer, key, name = f
        last = None
        for attempt in range(RETRIES):          # 일시적 네트워크 오류로 하루치가 통째로 사라지지 않게
            try:
                rows, real = adapter(issuer).holdings(key, asof)
                if not rows:
                    return fid, None, '국내종목없음'
                real = real or asof
                payload = [(fid, real, c, v['name'], v['qty'], v['wt'], v['val'])
                           for c, v in rows.items()]
                # 소스가 아직 이전 영업일 기준이면 그 날짜로 적재한다(가짜 날짜를 만들지 않는다)
                return fid, payload, (None if real == asof else f'기준일{real}로적재')
            except Exception as e:
                last = type(e).__name__
                if attempt < RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
        return fid, None, last

    ok, err, buf, errs = 0, 0, [], defaultdict(int)
    got, empty = [], []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fid, payload, e in ex.map(job, funds):
            if payload:
                buf.extend(payload); ok += 1; got.append(fid)
                if e:
                    errs[e] += 1
            else:
                err += 1; errs[e] += 1
                if e == '국내종목없음':
                    empty.append(fid)      # 네트워크 오류는 streak 에 세지 않는다
    conn.executemany('INSERT OR REPLACE INTO holding VALUES(?,?,?,?,?,?,?)', buf)
    conn.commit()
    # 국내 종목이 계속 0개면 네이버 탭 분류가 잘못된 해외형이다.
    # 단, 당일 미공시·일시 오류로 한 번 비었다고 끊으면 멀쩡한 ETF 를 죽인다 →
    # EMPTY_LIMIT 회 연속 빈 경우에만 추적에서 뺀다.
    conn.executemany('UPDATE fund SET empty_streak=0 WHERE fund_id=?', [(f,) for f in got])
    conn.executemany('UPDATE fund SET empty_streak=empty_streak+1 WHERE fund_id=?',
                     [(f,) for f in empty])
    conn.commit()
    if drop_empty:
        n = conn.execute('UPDATE fund SET track=0 WHERE track=1 AND empty_streak>=?',
                         (EMPTY_LIMIT,)).rowcount
        conn.commit()
        if n and not quiet:
            print(f'   국내종목 0개가 {EMPTY_LIMIT}회 연속 → 추적 제외 {n}개 (해외형 오분류)')
        elif empty and not quiet:
            print(f'   국내종목 0개 {len(empty)}개 (연속 {EMPTY_LIMIT}회가 되면 자동 제외)')
    if not quiet and errs:
        print('   수집 메모:', dict(errs))
    return ok, err, len(buf)


# ──────────────────────── 변동 산출 (노이즈 제거가 핵심) ────────────────────────
def fund_pairs(conn, max_gap=14):
    """펀드별로 '가장 최근 스냅샷 두 개'를 짝지어 돌려준다.

    운용사마다 공시 PDF 의 기준일이 제각각이라(같은 날 받아도 A사는 8/4, B사는 8/3),
    전체를 하나의 날짜쌍으로 묶어 비교하면 대부분의 펀드가 비교 대상에서 통째로 빠진다.
    실측에서 401개 중 0개만 비교됐다. 그래서 비교는 반드시 펀드 단위로 한다.
    """
    seq = defaultdict(list)
    for fid, a in conn.execute(
            'SELECT fund_id, asof FROM holding GROUP BY fund_id, asof ORDER BY fund_id, asof DESC'):
        seq[fid].append(a)
    out = []
    for fid, dates in seq.items():
        if len(dates) < 2:
            continue
        cur, prev = dates[0], dates[1]
        gap = (date.fromisoformat(cur) - date.fromisoformat(prev)).days
        if gap > max_gap:      # 공백이 너무 길면 '하루 변동'이 아니라 누적이라 신호가 흐려진다
            continue
        out.append((fid, cur, prev, gap))
    return out


def analyze(conn, run_date, max_gap=14):
    out = []
    # ETF가 다른 ETF를 담은 건(커버드콜의 모ETF, 재간접) 종목 시그널이 아니라 제외
    etfs = {r[0] for r in conn.execute('SELECT code FROM etf_ticker')}
    depth = dict(conn.execute('SELECT fund_id, depth FROM fund'))
    for fid, asof, prev_asof, gap in fund_pairs(conn, max_gap):
        is_top10 = depth.get(fid) == 'top10'
        cur = {r[0]: r for r in conn.execute(
            'SELECT code,name,qty,wt FROM holding WHERE fund_id=? AND asof=?', (fid, asof))
            if r[0] not in etfs}
        prev = {r[0]: r for r in conn.execute(
            'SELECT code,name,qty,wt FROM holding WHERE fund_id=? AND asof=?', (fid, prev_asof))
            if r[0] not in etfs}
        if not prev or not cur:
            continue
        # CU 재산정(설정단위 변경) 보정계수 = 유의미 수량 종목들의 변동률 중앙값
        # TOP10 소스는 CU 재산정 효과를 추정할 표본이 부족하므로 보정하지 않는다
        base = [c for c in cur.keys() & prev.keys() if prev[c][2] >= QTY_FLOOR]
        med = 0.0 if is_top10 else (
            st.median([(cur[c][2] / prev[c][2] - 1) * 100 for c in base]) if len(base) >= 5 else 0.0)

        for c in cur.keys() - prev.keys():
            out.append((run_date, fid, c, 'IN10' if is_top10 else 'NEW', cur[c][1],
                        asof, prev_asof, gap,
                        None, cur[c][2], None, cur[c][3], None, None))
        for c in prev.keys() - cur.keys():
            out.append((run_date, fid, c, 'OUT10' if is_top10 else 'DROP', prev[c][1],
                        asof, prev_asof, gap,
                        prev[c][2], None, prev[c][3], None, None, None))
        for c in cur.keys() & prev.keys():
            if prev[c][2] < QTY_FLOOR:
                continue
            pct = (cur[c][2] / prev[c][2] - 1) * 100
            adj = pct - med
            if abs(adj) >= ACTION_PP:
                out.append((run_date, fid, c, 'ADD' if adj > 0 else 'CUT', cur[c][1],
                            asof, prev_asof, gap,
                            prev[c][2], cur[c][2], prev[c][3], cur[c][3],
                            round(pct, 2), round(adj, 2)))
    conn.executemany(
        'INSERT OR REPLACE INTO change_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', out)
    conn.commit()
    return out


# ─────────────────────────── 테마별 텔레그램 리포트 ───────────────────────────
ICON = {'NEW': '🆕', 'DROP': '❌', 'ADD': '📈', 'CUT': '📉', 'IN10': '🔼', 'OUT10': '🔽'}
LABEL = {'NEW': '신규편입', 'DROP': '전량제외', 'ADD': '비중확대', 'CUT': '비중축소',
         'IN10': 'TOP10 진입', 'OUT10': 'TOP10 이탈'}
KIND_ORDER = ['NEW', 'DROP', 'IN10', 'OUT10', 'ADD', 'CUT']
# 지수를 그대로 복제하는 테마 — 변동이 운용사 판단이 아니라 지수 이벤트라 후순위로 밀어둔다
PASSIVE_THEMES = {'시장대표', '코스닥', '팩터', 'ESG', '기타'}


def build_report(conn, run_date, min_funds=1, include_passive=False):
    meta = {r[0]: r for r in conn.execute(
        'SELECT fund_id, name, theme, is_active, issuer FROM fund')}
    rows = conn.execute(
        'SELECT fund_id, code, name, kind, qty_pct_adj, cur_wt FROM change_log '
        'WHERE run_date=?', (run_date,)).fetchall()
    span = conn.execute('SELECT MIN(prev_asof), MAX(asof), COUNT(DISTINCT fund_id) '
                        'FROM change_log WHERE run_date=?', (run_date,)).fetchone()
    n_pairs = len(fund_pairs(conn))
    head = f'*ETF 구성종목 변동*  `{span[0]} → {span[1]}`' if rows else '*ETF 구성종목 변동*'
    if not rows:
        if n_pairs == 0:
            return (head + '\n\n아직 비교할 이전 스냅샷이 없습니다.\n'
                            '다음 영업일부터 변동이 잡힙니다.'), 0
        return head + f'\n\n{n_pairs}개 ETF 비교 — 감지된 변동 없음', 0

    # 테마 → (종목코드, 종목명, kind) → [(ETF명, 액티브여부, 변동률)]
    tree = defaultdict(lambda: defaultdict(list))
    for fid, code, cname, kind, adj, cwt in rows:
        m = meta.get(fid)
        if not m:
            continue
        _, fname, theme, act, _ = m
        tree[theme][(code, cname, kind)].append((fname, act, adj, cwt))

    order = sorted(tree.keys(), key=lambda t: (t in PASSIVE_THEMES, -len(tree[t])))
    L = [head, f'_{span[2]}개 ETF에서 변동 감지 (비교 대상 {n_pairs}개)_', '']
    total = 0
    for theme in order:
        items = tree[theme]
        if theme in PASSIVE_THEMES and not include_passive:
            # 지수 복제 테마는 기본 숨김. 단 액티브 ETF가 낸 변동은 운용사 판단이므로 남긴다
            items = {k: [f for f in v if f[1]] for k, v in items.items()}
            items = {k: v for k, v in items.items() if v}
            if not items:
                continue
        # 여러 ETF에서 동시 발생 → 강한 시그널이므로 위로
        ranked = sorted(items.items(),
                        key=lambda kv: (-len(kv[1]),
                                        KIND_ORDER.index(kv[0][2])))
        ranked = [kv for kv in ranked if len(kv[1]) >= min_funds]
        if not ranked:
            continue
        n_etf = conn.execute('SELECT COUNT(*) FROM fund WHERE theme=? AND track=1',
                             (theme,)).fetchone()[0]
        L.append(f'*{TH.tag(theme)}*  _{n_etf}개 ETF 추적_')
        for (code, cname, kind), funds in ranked[:10]:
            act = any(f[1] for f in funds)
            head = f'{ICON[kind]} {cname} `{code}`'
            if kind in ('ADD', 'CUT'):
                mv = max(funds, key=lambda f: abs(f[2] or 0))
                head += f'  {mv[2]:+.1f}%'
            if act:
                head += ' ⚡'
            L.append(head)
            L.append('   └ ' + ', '.join(sorted(set(_short(f[0]) for f in funds))[:5])
                     + (f' 외 {len(funds)-5}' if len(funds) > 5 else ''))
            total += 1
        L.append('')
    if total == 0:
        return head + f'\n\n{n_pairs}개 ETF 비교 — 유의미한 변동 없음', 0
    L.append('⚡ 액티브 ETF · 🔼🔽 TOP10 진입·이탈(폴백 소스)')
    return '\n'.join(L), total


def _short(fund_name, n=22):
    return fund_name if len(fund_name) <= n else fund_name[:n - 1] + '…'


def send_telegram(text, parse='HTML'):
    """단일 메시지 발송. ETF 이름에 * _ ( ) 가 흔해서 Markdown 대신 HTML 을 쓴다."""
    tok, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not tok or not chat:
        print('[skip] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정')
        return False
    ok = True
    for chunk in _chunks(text, 3900):
        r = requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                          json={'chat_id': chat, 'text': chunk, 'parse_mode': parse,
                                'disable_web_page_preview': True}, timeout=40)
        if not r.ok:
            print('[텔레그램] 발송 실패:', r.text[:200])
            ok = False
        time.sleep(0.4)              # 연속 발송 시 429 를 피한다
    return ok


def send_report(msgs):
    """리포트 전문을 여러 통으로 나눠 보낸다. 한 통이 실패해도 나머지는 계속 보낸다."""
    sent = 0
    for i, m in enumerate(msgs, 1):
        if send_telegram(m):
            sent += 1
        else:
            print(f'[텔레그램] {i}/{len(msgs)}번째 메시지 실패')
    print(f'[텔레그램] {sent}/{len(msgs)}통 발송')
    return sent == len(msgs)


def send_telegram_file(path, caption=''):
    """대시보드 HTML 을 문서로 보낸다.

    비공개 저장소는 GitHub Pages 가 유료 플랜이라 URL 을 못 만든다. 파일로 보내면
    호스팅 없이도 휴대폰·PC 어디서든 열린다. Pages 를 켠 경우엔 링크도 함께 간다.
    """
    tok, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not tok or not chat or not os.path.exists(path):
        return False
    try:
        with open(path, 'rb') as fh:
            r = requests.post(
                f'https://api.telegram.org/bot{tok}/sendDocument',
                data={'chat_id': chat, 'caption': caption[:1000], 'parse_mode': 'Markdown'},
                files={'document': (os.path.basename(path), fh, 'text/html')}, timeout=90)
        if not r.ok:
            print('[텔레그램] 파일 전송 실패:', r.text[:200])
        return r.ok
    except Exception as ex:
        print('[텔레그램] 파일 전송 예외:', type(ex).__name__)
        return False


def _chunks(text, size):
    """테마 블록(빈 줄) 경계에서 자른다"""
    parts, cur = [], ''
    for block in text.split('\n\n'):
        if len(cur) + len(block) + 2 > size and cur:
            parts.append(cur); cur = ''
        cur += (('\n\n' if cur else '') + block)
    if cur:
        parts.append(cur)
    return parts


# ─────────────────────────── 테마 심층 조회 (리서치) ───────────────────────────
def research(conn, theme, run_date):
    funds = conn.execute(
        'SELECT fund_id, name, issuer, is_active FROM fund WHERE theme=? AND track=1 '
        'ORDER BY mktcap DESC', (theme,)).fetchall()
    print(f'\n=== #{theme}ETF — 추적 {len(funds)}개 ===')
    for fid, nm, iss, act in funds:
        n = conn.execute('SELECT COUNT(*) FROM holding WHERE fund_id=? AND '
                         'asof=(SELECT MAX(asof) FROM holding WHERE fund_id=?)',
                         (fid, fid)).fetchone()[0]
        note = f'{n}종목' if n else '데이터없음(해당일 미수집)'
        print(f'  {nm:<34} [{ADAPTERS[iss].NAME}]{" 액티브" if act else ""}  {note}')
    print(f'\n--- 변동 (기준 {run_date}) ---')
    rows = conn.execute(
        'SELECT c.kind, c.name, c.code, f.name, c.qty_pct_adj, c.prev_wt, c.cur_wt, f.is_active '
        'FROM change_log c JOIN fund f ON f.fund_id=c.fund_id '
        'WHERE f.theme=? AND c.run_date=? '
        'ORDER BY c.kind, ABS(COALESCE(c.qty_pct_adj,999)) DESC', (theme, run_date))
    cur = None
    for kind, cname, code, fname, adj, pw, cw, act in rows:
        if kind != cur:
            print(f'\n[{LABEL[kind]}]'); cur = kind
        d = f'{adj:+.1f}%' if adj is not None else ''
        w = f'비중 {pw or 0:.2f}→{cw or 0:.2f}%' if (pw or cw) else ''
        print(f'  {cname:<14} {code}  {d:>8}  {w:<22} ← {fname}{" ⚡" if act else ""}')
    print()

    # 종목별 집계 — 여러 ETF에서 동시 발생한 것 찾기
    agg = conn.execute(
        'SELECT c.name, c.code, c.kind, COUNT(*) n FROM change_log c '
        'JOIN fund f ON f.fund_id=c.fund_id WHERE f.theme=? AND c.run_date=? '
        'GROUP BY c.name, c.code, c.kind HAVING n>=2 ORDER BY n DESC', (theme, run_date))
    a = list(agg)
    if a:
        print('--- 복수 ETF 동시 발생 (강한 시그널) ---')
        for nm, code, kind, n in a:
            print(f'  {ICON[kind]} {nm} ({code}) — {n}개 ETF에서 {LABEL[kind]}')


def prune(conn):
    """오래된 스냅샷 삭제. 안 하면 DB가 하루 25,000행씩 무한히 커진다."""
    if KEEP_DAYS <= 0:
        return
    cut = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    n = conn.execute('DELETE FROM holding WHERE asof < ?', (cut,)).rowcount
    conn.execute('DELETE FROM change_log WHERE asof < ?', (cut,))
    conn.commit()
    if n:
        conn.execute('VACUUM')
        print(f'   {KEEP_DAYS}일 이전 스냅샷 정리: {n:,} 레코드 삭제')


# ─────────────────────────── 설정 점검 (--check) ───────────────────────────
def doctor(conn):
    ok = True
    print('■ 어댑터')
    for k, C in sorted(ADAPTERS.items()):
        print(f'   {k:<10} {C.NAME:<18} 깊이={C.DEPTH:<6} 과거조회={"O" if C.HISTORY else "X"}')

    print('\n■ 데이터베이스')
    n_fund = conn.execute('SELECT COUNT(*) FROM fund WHERE track=1').fetchone()[0]
    if n_fund == 0:
        print('   ETF 미등록 → `python tracker.py --init` 을 먼저 실행하세요')
        ok = False
    else:
        full = conn.execute("SELECT COUNT(*) FROM fund WHERE track=1 AND depth='full'").fetchone()[0]
        print(f'   추적 {n_fund}개 (전체종목 {full} / TOP10 {n_fund - full})')
        snaps = [r[0] for r in conn.execute(
            'SELECT DISTINCT asof FROM holding ORDER BY asof DESC LIMIT 5')]
        print(f'   스냅샷 {len(snaps)}개 보유: {", ".join(snaps) if snaps else "없음"}')
        if len(snaps) < 2:
            print('   비교하려면 스냅샷이 2일치 이상 필요합니다')

    print('\n■ 텔레그램')
    tok, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not tok or not chat:
        print('   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정')
        print('   .env.example 을 .env 로 복사하고 값을 채우세요')
        ok = False
    else:
        try:
            r = requests.get(f'https://api.telegram.org/bot{tok}/getMe', timeout=10).json()
            if r.get('ok'):
                print(f'   봇 연결 성공: @{r["result"]["username"]}')
                s = requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                                  json={'chat_id': chat,
                                        'text': 'ETF 트래커 연결 테스트 성공'}, timeout=10).json()
                if s.get('ok'):
                    print('   테스트 메시지 발송 성공 — 텔레그램을 확인하세요')
                else:
                    print(f'   발송 실패: {s.get("description")}')
                    print('   CHAT_ID 가 맞는지, 봇에게 먼저 말을 걸었는지 확인하세요')
                    ok = False
            else:
                print(f'   봇 인증 실패: {r.get("description")}')
                ok = False
        except Exception as e:
            print(f'   연결 오류: {type(e).__name__}')
            ok = False

    print('\n' + ('전부 정상입니다. `python tracker.py --run` 으로 시작하세요.'
                  if ok else '위 항목을 먼저 해결하세요.'))
    return ok


# ─────────────────────────────── CLI ───────────────────────────────
def prev_trading_day(conn, asof):
    r = conn.execute('SELECT MAX(asof) FROM holding WHERE asof < ?', (asof,)).fetchone()[0]
    return r


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--init', action='store_true')
    p.add_argument('--run', action='store_true')
    p.add_argument('--live', action='store_true', help='장중 시세만 갱신 (빠름)')
    p.add_argument('--themes', action='store_true')
    p.add_argument('--research', metavar='THEME')
    p.add_argument('--backfill', nargs=2, metavar=('FROM', 'TO'))
    p.add_argument('--date'); p.add_argument('--prev')
    p.add_argument('--issuer', action='append')
    p.add_argument('--limit', type=int)
    p.add_argument('--min-funds', type=int, default=_envnum('MIN_FUNDS', 1, int))
    p.add_argument('--passive', action='store_true', help='시장대표/팩터 등 패시브 테마도 포함')
    p.add_argument('--no-send', action='store_true')
    p.add_argument('--check', action='store_true', help='설정·소스·텔레그램 연결 점검')
    p.add_argument('--verify', action='store_true', help='데이터 품질 10개 항목 실측 검증')
    a = p.parse_args()
    conn = db()
    asof = a.date or date.today().isoformat()

    if a.check:
        return doctor(conn)

    if a.verify:
        import subprocess
        return subprocess.call([sys.executable, os.path.join(HERE, 'verify.py')])

    if a.themes:
        rows = conn.execute('SELECT theme, COUNT(*) n, SUM(is_active) act FROM fund '
                            'WHERE track=1 GROUP BY theme ORDER BY n DESC')
        print(f'{"테마":<16}{"ETF":>5}{"액티브":>7}')
        for t, n, act in rows:
            print(f'#{t+"ETF":<16}{n:>4}{act or 0:>7}')
        return

    if a.init:
        n, sk = build_universe(conn, set(a.issuer) if a.issuer else None)
        print(f'\n추적 대상 {n}개 등록 (제외 {sk}개: 해외형·파생·채권형)')
        ok, err, rec = snapshot(conn, asof, a.issuer, a.limit, drop_empty=True)
        print(f'스냅샷 {asof}: 성공 {ok} / 실패 {err} / {rec:,} 레코드')
        return

    if a.backfill:
        f, t = a.backfill
        d0, d1 = datetime.fromisoformat(f).date(), datetime.fromisoformat(t).date()
        d = d0
        while d <= d1:
            if d.weekday() < 5:
                ok, err, rec = snapshot(conn, d.isoformat(), a.issuer, a.limit, quiet=True)
                print(f'  {d} — {ok}개 / {rec:,} 레코드')
            d += timedelta(days=1)
        return

    if a.research:
        research(conn, a.research, asof)
        return

    if a.live:
        # 장중 갱신: 목록 API 1회(1.7초) + base.json 만으로 대시보드를 다시 그린다.
        # DB 를 열지 않으므로 아침 잡과 동시에 돌아도 충돌하지 않는다.
        bp = os.path.join(HERE, 'docs', 'base.json')
        if not os.path.exists(bp):
            print('::error::docs/base.json 이 없습니다. 먼저 --run 을 한 번 돌리세요.')
            return 1
        with open(bp, encoding='utf-8') as fh:
            B = json.load(fh)
        D = DASH.live_view(B, MK.live_rows(MK.fetch_list()))
        out = os.path.join(HERE, 'docs', 'index.html')
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(RENDER.build(D))
        state = '장중' if D['is_live'] else '장 마감'
        ud = re.sub(r'<[^>]+>', '', D['kpi'][1][1])
        print(f'{state} {D["live_date"]} {D["live_ts"]} · {D["n_etf"]:,}종목 · 상승/하락 {ud} → {out}')
        return

    if a.run:
        t0 = time.time()
        prune(conn)
        ok, err, rec = snapshot(conn, asof, a.issuer, a.limit, drop_empty=True)
        print(f'구성종목 {asof}: {ok}개 수집 / {err} 실패 / {rec:,} 레코드 / {time.time()-t0:.0f}s')

        # 시세·순자산. 실패해도 구성종목 리포트는 나가야 하므로 통째로 감싼다.
        try:
            t1 = time.time()
            rows = MK.fetch_list()
            MK.sync_meta_aum(conn, rows, asof)
            codes = [r[0] for r in conn.execute('SELECT code FROM etf_meta')]
            have = conn.execute('SELECT COUNT(*) FROM etf_px').fetchone()[0]
            back = 400 if have == 0 else 45      # 최초 1회만 1년치, 이후는 최근분만
            start = (date.fromisoformat(asof) - timedelta(days=back)).isoformat()
            n, f = MK.sync_px(conn, codes, start, asof, log=(print if back > 100 else None))
            print(f'시세: {len(rows)}종목 순자산 / {n:,}행 일봉 / 실패 {f} / {time.time()-t1:.0f}s')
        except Exception as ex:
            print(f'[경고] 시세 수집 실패({type(ex).__name__}) — 구성종목만으로 진행합니다')

        ch = analyze(conn, asof)
        print(f'구성종목 변동 {len(ch)}건 / 비교 {len(fund_pairs(conn))}개 ETF')

        os.makedirs(os.path.join(HERE, 'docs'), exist_ok=True)
        B = DASH.export_base(conn, asof)
        if B:
            with open(os.path.join(HERE, 'docs', 'base.json'), 'w', encoding='utf-8') as fh:
                json.dump(B, fh, ensure_ascii=False)
            print(f'기준 데이터 → docs/base.json ({len(B["etf"])}종목)')

        D = DASH.live_view(B, MK.live_rows(MK.fetch_list())) if B else DASH.collect(conn, asof, asof)
        if not D:
            msg, _ = build_report(conn, asof, a.min_funds, a.passive)
            print(msg)
            if not a.no_send:
                send_telegram(msg, parse=None)
            return

        # 웹 대시보드는 부가물이다. 본체는 텔레그램 전문 리포트.
        out = os.path.join(HERE, 'docs', 'index.html')
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(RENDER.build(D))
        print(f'대시보드 → {out}')

        msgs = REPORT.build(D, os.getenv('DASH_URL'))
        total = sum(len(m) for m in msgs)
        print(f'리포트 {len(msgs)}통 / {total:,}자\n')
        for m in msgs:
            print(re.sub(r'<[^>]+>', '', m))
            print('─' * 50)
        if not a.no_send:
            send_report(msgs)
        return
    p.print_help()


if __name__ == '__main__':
    main()
