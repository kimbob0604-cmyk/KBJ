#!/usr/bin/env python3
"""
신고가 종목의 **종목별** 투자자 수급 — 누가 샀는지.

시장 전체 수급(`kis.market_flows`)은 그날 장이 어땠는지를 말한다. 어떤 종목이
52주 신고가를 냈는데 **기관이 샀는지 개인이 샀는지**는 그것으로 알 수 없다.
레퍼런스 코멘트가 종목마다 한 줄씩 적는 그 값이다 —

    "두산에너빌리티는 기관 960억 순매수로 이 날 기관 매수 1위,
     외국인도 64억 순매수로 방향 일치."

대상은 **52주 이상 신고가(역사적 포함)를 낸 전 종목**이다. 거래량 문턱 같은 것을
여기서 걸지 않는다 — 문턱은 보여 줄 것을 고르는 자리(monitor/flow)의 일이고,
여기는 "신고가를 낸 종목의 수급"을 빠짐없이 받는 자리다.

못 받은 종목은 **빼지 않고 사유와 함께 남긴다.** 조용히 빠지면 받는 쪽은 그
종목이 신고가를 안 냈다고 읽는다 (CLAUDE.md 2장 6번).

## 주 소스는 KIS, 폴백은 네이버 (D-083)

KIS 실서버가 **23시 전후에 접속 자체가 안 되는** 시간대가 있다. 2026-09-21
예약 실행(run 109)이 크론 지연으로 22:59 KST 에 돌았고 11종목 전부
`ConnectTimeout(connect timeout=25)` 였다 — 같은 날 17:20 수동 실행은 11/11
성공. 원인은 KIS 쪽 점검 시간대로 **추정**한다(단정하지 않는다).

그래서 둘을 한다.
  1. **빠른 실패.** 접속 단계에서 실패하면 그 자리에서 'KIS 접속 불가' 로 판정하고
     나머지 종목은 KIS 를 부르지 않는다. 종목당 25초 × 11종목 = 9분을 버리지 않는다.
  2. **네이버 폴백.** `m.stock.naver.com/api/stock/{code}/trend` 의 기관·외국인
     순매매**량(주)**을 쓴다. 개인은 응답에 있는 날과 없는 날이 있다
     (flows.STOCK_FIELD) — 없으면 만들지 않는다. 금액은 종가를 곱한 **추정치**라
     별도 키(`amt_est`)에 `is_estimate` 를 달아 넣는다.
     KIS 가 종목 단위로 실패한 종목(rt_cd 오류·파싱 실패)에도 같은 폴백을 건다.
"""
from datetime import datetime, timedelta, timezone

from . import creds
from . import flows as NV
from . import kis

# 주 소스. 파일 머리의 `source` 는 이 값이 아니라 **실제로 쓴 소스**다 (collect).
SOURCE = 'kis'
# 라벨 우선순위. 상위가 하위를 포함한다 (CLAUDE.md 4장).
ORDER = ('d120', 'w52', 'hist')  # KBJ ADR 0017(d60 → d120)
# 받을 최소 등급. 52주 이상이면 역사적도 든다.
MIN_KIND = 'w52'
# 투자자 구분. 저장·표기 순서다.
WHO = ('기관', '외국인', '개인')
# 네이버 폴백의 단위. 순매매량이다 — 금액이 아니다.
NV_UNIT = '주'

# KIS 종목 수급의 http.get **시도 횟수** — 2회(재시도 1회). 접속이 안 되는 시간대에는
# 한 시도가 25초라, 첫 종목에서 51초를 쓰고 `connect_failed` 로 멈춘다(그 뒤 종목은
# 부르지 않는다). 1회로 두면 정상 시간대의 일시적 접속 실패 한 번(패킷 유실)에
# 나머지 전 종목이 추정치 폴백으로 간다 — 25초 한 번과 하루치 추정치를 바꾸지 않는다.
KIS_RETRIES = 2
# 네이버 폴백이 받을 거래일 수. 기준일과 직전 며칠이면 되지만, 지난 기준일을 다시
# 만들 때(재발송·아카이브 복구)는 응답 창이 그 뒤 날짜로 차 있으므로 넉넉히 잡는다.
# `_latest` 는 기준일 뒤 날짜를 쓰지 않는다.
NAVER_DAYS = 20
# 사유 한 줄의 길이 상한. 예전에는 requests 스택 500자가 그대로 배너에 실렸다.
REASON_MAX = 160
# 배너에 덧붙일 네이버 실패 사유 수. 같은 사유는 한 번만.
NV_WHY_MAX = 2

KST = timezone(timedelta(hours=9))
# 접속 불가가 이 시각대에 났으면 점검 시간대로 **추정** 한다고 적는다. 실측은
# 22:59~23:09 KST 하나뿐이라(run 109) 폭은 넉넉히 잡았다. 그 밖의 시각이면
# 이유를 모르는 것이므로 아무 추정도 붙이지 않는다.
MAINT_HOURS = (22, 23, 0)


def hits(row, kind=MIN_KIND, key='close_basis'):
    """그 종목이 `kind` 이상의 신고가를 냈는가.

    기준은 **이름으로** 읽는다. `label` 은 설정(default_basis)이 가리키는 기본
    기준이라, 그것을 보면 설정이 바뀌는 날 대상이 조용히 갈린다.
    """
    lab = (row.get(key) or {}).get('label')
    if not lab or lab not in ORDER:
        return False
    return ORDER.index(lab) >= ORDER.index(kind)


def targets(newhigh, kind=MIN_KIND):
    """수급을 받을 종목. 고가·종가 어느 기준으로든 섰으면 대상이다.

    한쪽 기준만 보면 '고가로만 뚫은 종목' 이 통째로 빠진다. 화면은 두 기준을
    토글로 오가므로, 한쪽에서만 보이는 종목의 수급이 비어 있으면 안 된다.
    """
    out, seen = [], set()
    for r in newhigh.get('achieved') or []:
        if r.get('code') in seen:
            continue
        if hits(r, kind, 'close_basis') or hits(r, kind, 'high_basis'):
            seen.add(r['code'])
            out.append(r)
    return out


def _has_value(rec):
    """투자자 구분이 하나라도 있는 행인가. close·volume 만 든 행은 값이 아니다."""
    return isinstance(rec, dict) and any(rec.get(w) is not None for w in WHO)


def _latest(by_date, asof):
    """기준일 값. 그날이 없으면 **기준일 이전** 가장 최근 것을 쓰되 그 날짜를 함께 낸다.

    KIS 가 당일치를 아직 안 준 날이 있다. 전일 수급을 당일이라고 적으면 다른
    날의 사실이 같은 날로 읽힌다.

    후보에서 빼는 것 둘.
      - 기준일 **뒤** 날짜. 지난 기준일을 다시 만들 때 응답 창은 그 뒤 날짜로
        차 있다. 그걸 고르면 8일 뒤 값을 '이전 영업일 값' 이라 부르게 된다.
      - 투자자 구분이 하나도 없는 행. 파서는 close·volume 만 든 행도 돌려준다
        (키가 바뀐 날, 당일치가 아직 안 채워진 행). 그건 값이 아니다 — 0 도 아니다.
    없으면 (None, None). 사유는 `_no_value_reason` 이 만든다.
    """
    cands = [d for d, rec in (by_date or {}).items()
             if d <= asof and _has_value(rec)]
    if not cands:
        return None, None
    d = max(cands)
    return by_date[d], d


def _no_value_reason(by_date, asof):
    """`_latest` 가 빈손일 때의 한 줄 사유. 무엇이 왔는지 적어야 고칠 수 있다."""
    if not by_date:
        return '날짜별 값이 비었다'
    before = [d for d in by_date if d <= asof]
    if not before:
        return f'기준일 이전 행이 없다(받은 날짜 {min(by_date)}~{max(by_date)})'
    keys = sorted(k for k in (by_date[max(before)] or {}) if not str(k).startswith('_'))
    return f'투자자 구분이 없다(받은 키 {keys})'


def short(e, limit=REASON_MAX):
    """예외를 한 줄 사유로. 스택·URL 을 싣지 않는다.

    http.get 은 `<url> 실패: <사유>` 로 올린다. 배너와 텔레그램에는 사유만
    있으면 되고 URL 은 로그에 있다.
    """
    msg = ' '.join(str(e).split())
    if ' 실패: ' in msg and msg.startswith('http'):
        msg = msg.split(' 실패: ', 1)[1]
    return msg[:limit]


def down_reason(e, now=None):
    """접속 단계 실패를 한 줄로. 시각을 적고, 점검 시간대면 그렇게 **추정**한다.

    붙지 못한 것(ConnectTimeout)과 붙었는데 답이 없는 것(ReadTimeout)은 다른
    증상이다. 둘 다 빠른 실패의 대상이지만 사유는 구분해 적는다 — '연결 시간
    초과' 라고만 적으면 응답 지연이 접속 문제처럼 읽힌다.
    """
    msg = str(e)
    if 'ReadTimeout' in msg or 'Read timed out' in msg:
        what = '응답 시간 초과'
    elif 'timed out' in msg or 'Timeout' in msg:
        what = '연결 시간 초과'
    elif 'refused' in msg.lower():
        what = '연결 거부'
    elif 'reset' in msg.lower() or 'RemoteDisconnected' in msg:
        what = '연결 끊김'
    else:
        what = '연결 실패'
    now = now or datetime.now(KST)
    hint = (' — 23:00 KST 전후 점검 시간대로 추정'
            if now.hour in MAINT_HOURS else '')
    return f'KIS 접속 불가({what}, {now:%H:%M} KST){hint}'


def naver_flows(code):
    """네이버 폴백 — 기관·외국인 순매매량(주). 시험에서 이 이름을 갈아 끼운다."""
    return NV.fetch_stock(code, days=NAVER_DAYS)


def _kis_entry(code, name, rec, d):
    entry = dict(code=code, name=name, as_of=d, unit=rec.get('_unit'), source='kis')
    for who in WHO:
        if rec.get(who) is not None:
            entry[who] = rec[who]
    return entry


def _naver_entry(code, name, rec, d, kis_error=None):
    """네이버 값 한 건. 수량은 사실, 금액은 추정 — 키를 나눠 담는다."""
    entry = dict(code=code, name=name, as_of=d, unit=NV_UNIT, source='naver')
    for who in WHO:                       # 응답에 없는 구분(개인)은 만들지 않는다
        if rec.get(who) is not None:
            entry[who] = rec[who]
    close = rec.get('close')
    if close:
        est = {}
        for who in WHO:
            if who in entry:
                a = NV.to_amount(entry[who], close)
                if a is not None:
                    # 만원 자리(억원 소수 4자리)까지 둔다. 1자리에서 자르면 1천만원
                    # 미만이 0.0/-0.0 이 되어 값이 사라지고 부호까지 뒤집힌다.
                    # 표기 자릿수는 표기하는 쪽(telegram._eok · render.net_amt)이
                    # 정한다. `or 0.0` 은 -0.0 을 0.0 으로.
                    est[who] = round(a, 4) or 0.0
        if est:
            entry['close'] = close
            # 종가 × 순매매량이다. 실제 순매수 금액은 체결가 가중이라 이 값과
            # 다르다. 별도 키에 두고 is_estimate 를 단다 (CLAUDE.md 2장 1번).
            entry['amt_est'] = dict(est, unit='억원', is_estimate=True,
                                    basis='순매매량 × 종가')
    if kis_error:
        entry['kis_error'] = kis_error
    return entry


def _naver_why(items):
    """둘 다 실패한 종목들의 네이버 사유를 중복 없이 한두 개. 배너용.

    failed 에는 종목마다 있지만 배너(flow_missing)만 보는 쪽은 왜 비었는지 알 수
    없다 — 'HTTP 500' 인지 '키가 바뀜' 인지가 다음 행동을 가른다.
    """
    whys = list(dict.fromkeys(w for _, _, w, _ in items if w))
    if not whys:
        return ''
    more = f' 외 {len(whys) - NV_WHY_MAX}' if len(whys) > NV_WHY_MAX else ''
    return ' · 네이버 사유: ' + ' / '.join(whys[:NV_WHY_MAX]) + more


def collect(newhigh, asof, kind=MIN_KIND, log=print, now=None, rows=None):
    """대상 종목의 수급을 받아 `{by_code, failed, missing, ...}` 로 돌려준다.

    한 종목이 한 번의 호출이다. 52주 이상은 하루 몇 종목 수준이라 한도에 닿지
    않는다. 60일까지 넓히면 수십 종목이 되므로 그때는 호출 간격을 봐야 한다.

    반환 구조
      source    실제로 쓴 소스 — 'kis' | 'naver' | 'mixed'. 받은 값이 없으면 None
      by_code   {code: {code, name, as_of, unit, source('kis'|'naver'), 기관, 외국인,
                 (개인), (close, amt_est{…, is_estimate}), (kis_error)}}
      failed    {code: 한 줄 사유} — 두 소스 다 못 받은 종목. 값은 없다(0 금지)
      missing   배너·flow_missing 에 나갈 사람용 문장 목록. 못 받은 것과 **다른
                소스로 대체한 것** 둘 다 적는다 — 후자도 조용히 바꾸면 안 된다
      kis_down  접속 불가 사유 한 줄. 정상이면 None
      n_naver   네이버 폴백으로 채운 종목 수
    """
    # `rows` 를 주면 그것이 대상이다. 신고가와 무관하게 **아무 종목이나** 한 번
    # 물어보는 길(`--flows`)을 내려고 열었다. 받는 방법(KIS → 네이버 폴백,
    # 실패 의미, 추정 금액 표기)은 하나뿐이어야 하므로 두 번째 구현을 만들지
    # 않고 대상만 갈아 끼운다.
    rows = targets(newhigh, kind) if rows is None else list(rows)
    out = dict(source=None, as_of=asof, kind=kind, unit=None,
               by_code={}, failed={}, missing=[], n_target=len(rows),
               kis_down=None, n_naver=0)
    if not rows:
        log(f'  종목 수급 — {kind} 이상 신고가가 없어 받을 대상이 없다')
        return out

    kis_down = None
    if not creds.has('KIS_APP_KEY', 'KIS_APP_SECRET'):
        # 앱키가 없어도 네이버는 인증이 없으니 받을 수 있다. 사유는 그대로 남긴다.
        kis_down = 'KIS 앱키 없음'
        log(f'  종목 수급 — 앱키 없음, {len(rows)}종목 네이버 폴백')

    s = kis.session() if not kis_down else None
    ok_kis, rescued = 0, []
    # 둘 다 못 받은 종목 — (code, name, 네이버 사유, 접속 불가 뒤였는가)
    both_failed = []
    # 접속 불가 **뒤에** 처리한 종목 수와 그중 네이버 성공 수. 앞서 KIS 로 받은
    # 종목을 실패한 것처럼 세지 않으려면 전체 수가 아니라 이 수를 적어야 한다.
    n_down = n_nv_down = 0
    for r in rows:
        code, name = r['code'], r.get('name') or r['code']
        rec, d, err = None, None, None
        if kis_down is None:
            try:
                got = kis.stock_flows(code, s=s, retries=KIS_RETRIES)
            except Exception as e:                      # noqa: BLE001
                if kis.connect_failed(e):
                    # 서버가 안 받는 것이다. 다음 종목을 불러도 25초씩 더 버릴 뿐.
                    kis_down = down_reason(e, now)
                    log(f'  {kis_down} — 나머지 종목은 KIS 를 부르지 않는다')
                else:
                    err = short(e)                      # 이 종목만의 실패
            else:
                by = got.get('by_date')
                rec, d = _latest(by, asof)
                if not rec:
                    # 행은 왔는데 구분이 없다(키가 바뀐 날). 빈 항목을 '성공' 으로
                    # 세지 않고 종목 단위 실패로 두어 네이버로 간다.
                    err = _no_value_reason(by, asof)
        if rec:
            entry = _kis_entry(code, name, rec, d)
            ok_kis += 1
        else:
            if kis_down:
                n_down += 1
            why_kis = kis_down or f'KIS {err}'
            try:
                nv = naver_flows(code)
                by = nv.get('by_date')
                rec, d = _latest(by, asof)
                if not rec:
                    raise NV.Fetch(_no_value_reason(by, asof))
            except Exception as e:                      # noqa: BLE001
                why_nv = short(e)
                out['failed'][code] = f'{why_kis} · 네이버 폴백도 실패: {why_nv}'
                both_failed.append((code, name, why_nv, bool(kis_down)))
                continue
            entry = _naver_entry(code, name, rec, d,
                                 kis_error=None if kis_down else err)
            out['n_naver'] += 1
            if kis_down:
                n_nv_down += 1
            else:
                rescued.append((code, name, err))
        # 단위가 종목마다 다르면(금액/수량) 한 줄에 합쳐 적을 수 없다. 섞였다는
        # 사실을 남기고, 화면·메시지는 종목마다 제 단위로 적는다.
        unit = entry.get('unit')
        out['unit'] = unit if out['unit'] in (None, unit) else 'mixed'
        out['by_code'][code] = entry

    out['kis_down'] = kis_down
    # 머리의 출처는 실제로 쓴 소스다. 항목이 전부 네이버인데 머리가 'kis' 면 파일만
    # 읽는 쪽(CLAUDE.md 3장)이 출처를 잘못 읽는다. unit 의 mixed 규칙과 같다.
    srcs = sorted({v.get('source') for v in out['by_code'].values()})
    out['source'] = None if not srcs else (srcs[0] if len(srcs) == 1 else 'mixed')
    n_fail = len(both_failed)
    stale_dates = sorted({v['as_of'] for v in out['by_code'].values()
                          if v.get('as_of') != asof})
    stale = sum(1 for v in out['by_code'].values() if v.get('as_of') != asof)
    log(f'  종목 수급 — KIS {ok_kis}/{len(rows)}종목'
        + (f' · 네이버 폴백 {out["n_naver"]}종목' if out['n_naver'] else '')
        + (f' · 기준일 밖 {stale}종목' if stale else '')
        + (f' · 실패 {n_fail}종목' if n_fail else ''))

    # ── 배너 문장. 같은 사유는 한 줄로 묶는다 ──
    if kis_down:
        under = [x for x in both_failed if x[3]]
        line = f'{kis_down} — '
        if ok_kis:
            # 접속 불가가 몇 종목 뒤에 왔다. 앞서 받은 종목까지 폴백 수에 넣으면
            # '4종목 중 3종목 성공' 이 되어 한 종목이 어디 갔는지 알 수 없다.
            line += f'KIS 로 {ok_kis}종목 받은 뒤 '
        line += f'{n_down}종목 → 네이버 폴백 {n_nv_down}종목 성공'
        if under:
            line += (f' · {len(under)}종목 실패('
                     + ', '.join(f'{nm}({c})' for c, nm, _, _ in under) + ')'
                     + _naver_why(under))
        out['missing'].append(line)
    # 접속 불가 **전에** 종목 단위로 실패하고 네이버도 못 받은 종목. kis_down 과
    # 무관하게 종목마다 사유를 적는다.
    for c, nm, _, under_down in both_failed:
        if not under_down:
            out['missing'].append(f'{nm}({c}) 수급 못 받음 — {out["failed"][c]}')
    if rescued:
        # kis_down 이 뒤에 서도 이 줄은 빠지지 않는다 — rt_cd 사유가 by_code 의
        # kis_error 에만 남으면 배너만 보는 쪽은 그 종목이 왜 네이버 값인지 모른다.
        out['missing'].append(
            f'KIS 종목 단위 실패 {len(rescued)}종목 → 네이버 값으로 대체: '
            + ' / '.join(f'{nm}({c}) {e}' for c, nm, e in rescued))
    if out['n_naver']:
        # 조용히 바꾸지 않는다 (2장 6번). 값의 성격이 KIS(금액)와 다르다.
        # 구분은 **실제로 받은 것**을 적는다. 개인은 응답에 있는 날과 없는 날이
        # 있어(flows.STOCK_FIELD), '없습니다' 라고 단정하면 같은 보드의 텔레그램
        # 줄('개인 -74,126주')과 모순된다.
        nv = [v for v in out['by_code'].values() if v.get('source') == 'naver']
        got = [w for w in WHO if any(w in v for v in nv)]
        absent = [w for w in WHO if w not in got]
        out['missing'].append(
            f'네이버 폴백 {out["n_naver"]}종목은 {"·".join(got)} 순매매량(주)'
            + (f'이고 {"·".join(absent)}은 응답에 없습니다' if absent else '입니다')
            + ' — 억원 표기는 종가 환산 추정치입니다')
    if stale:
        # `_latest` 가 기준일 뒤 날짜는 고르지 않으므로 이 값은 늘 기준일 이전이다.
        # 어느 날짜인지도 적는다 — '이전 영업일' 만으로는 며칠 전인지 모른다.
        out['missing'].append(
            f'종목별 수급 {stale}종목은 {asof} 자 값이 아직 없어 이전 영업일 '
            f'값입니다({", ".join(stale_dates)}) — 각 줄에 그 날짜를 함께 적었습니다')
    return out


def probe():
    """--check 용. 삼성전자 trend 를 받아 파싱 행수와 최신 bizdate 를 본다.

    KIS 가 막힌 시각에 이 경로마저 죽어 있으면 그날 종목 수급이 통째로 빈다.
    점검에 없으면 파서가 깨져도 다음 daily 의 배너에서야 안다.

    행이 와도 투자자 구분이 없으면 FAIL 이다 — 키가 바뀐 날 PASS 를 찍으면 그날
    daily 는 값 없는 항목을 '성공' 으로 센다.
    """
    try:
        r = naver_flows('005930')
    except Exception as e:                              # noqa: BLE001
        return [('종목 수급 폴백', False, short(e))]
    by = r.get('by_date') or {}
    if not by:
        return [('종목 수급 폴백', False, '005930 파싱 행이 0 이다')]
    last = max(by)
    valued = [d for d, rec in by.items() if _has_value(rec)]
    if not valued:
        return [('종목 수급 폴백', False,
                 f'005930 {len(by)}행 · 최신 {last} · 투자자 구분이 한 행에도 없다'
                 f'(받은 키 {sorted(by[last])}) — 응답 키가 바뀌었을 수 있다')]
    d = max(valued)
    rec = by[d]
    got = [f'{w} {rec[w]:+,.0f}주' for w in WHO if rec.get(w) is not None]
    return [('종목 수급 폴백', True,
             f'005930 {len(by)}행 · 최신 {last} · ' + ', '.join(got)
             + (f' ({d} 자 — 최신 행에는 구분이 없다)' if d != last else '')
             + ('' if '개인' in rec else ' · 개인 없음(응답에 없다)'))]
