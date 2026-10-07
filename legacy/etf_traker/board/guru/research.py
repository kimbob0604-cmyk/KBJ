#!/usr/bin/env python3
"""
구루 브리핑 조사 — 인물별 '당일 발언' 을 Claude 웹 검색으로 찾고, **코드가 출처를 대조한다.**

계약은 `docs/GURU.md`. 이 파일이 지키는 것:

1. **출처 URL 은 그 호출의 실제 검색 결과에 있던 것만 인정한다.** 모델이 적은
   `sources[].url` 을 `web_search_tool_result` 블록의 URL 목록과 맞춰 본다. 하나도
   맞지 않는 PASS 는 '금일 발언 없음' 으로 내리고 결손에 적는다 — 지어낸 출처를
   걸러내는 기계 검사다(CLAUDE.md 2장 4번).
2. **날짜.** PASS 는 출처 날짜가 조사 창 안(미국 날짜 기준 하루 여유)이어야 한다.
   '참고' 줄은 `note_days` 안이어야 한다. 날짜를 모르면 인정하지 않는다.
3. **명단 밖 인물은 버린다.** 모델이 명단에 없는 이름을 돌려주면 버리고 적는다.
4. **조사 못 한 사람을 '발언 없음' 이라 적지 않는다.** 호출이 실패했거나 모델이
   그 사람을 빠뜨렸으면 '확인 못 함' 으로 따로 센다 — 없음과 모름은 다르다.

수치(등락률·금리 등)는 기사 본문과 대조할 수 없다(검색 결과 본문은 암호화돼 온다).
그래서 수치는 '출처가 붙은 줄 안에서만' 쓰게 하고, 종합 단계(synth.py)가 새 수치를
만들지 못하게 막는다.
"""
import concurrent.futures as cf
import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

KST = timezone(timedelta(hours=9))
WEEKDAY_KO = '월화수목금토일'


# ─────────────────────────── 날짜 ───────────────────────────
def window(now, cfg):
    """조사 창. now 는 KST aware datetime.

    session — 검증 기준 미국장 날짜. KST 아침 기준 하루 전(화 아침 → 월 미국장).
    """
    now = now.astimezone(KST)
    asof = now.date()
    start = now - timedelta(hours=int(cfg.get('window_hours', 24)))
    return dict(asof=asof.isoformat(), session=(asof - timedelta(days=1)).isoformat(),
                start=start.isoformat(timespec='minutes'), end=now.isoformat(timespec='minutes'))


def runs_on(d, cfg):
    return d.weekday() in set(cfg.get('run_weekdays', [1, 2, 3, 4, 5]))


def next_run(d, cfg):
    for i in range(1, 8):
        n = d + timedelta(days=i)
        if runs_on(n, cfg):
            return n
    return None


def ko_date(d):
    """2026.09.29 (화) 꼴의 앞부분·요일."""
    d = d if isinstance(d, date) else date.fromisoformat(d)
    return f'{d:%Y.%m.%d}', WEEKDAY_KO[d.weekday()]


# ─────────────────────────── 응답 다루기 ───────────────────────────
def _g(o, k, default=None):
    """SDK 객체와 dict 를 같이 읽는다(시험은 dict 로 흉내 낸다)."""
    if isinstance(o, dict):
        return o.get(k, default)
    return getattr(o, k, default)


def norm_url(u):
    """비교용 URL. 스킴·호스트 소문자, 끝 슬래시·조각·추적 파라미터 제거."""
    if not u:
        return ''
    try:
        p = urlsplit(u.strip())
    except ValueError:
        return ''
    q = '&'.join(x for x in (p.query or '').split('&')
                 if x and not x.lower().startswith(('utm_', 'guccounter', 'ref=')))
    host = (p.netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return urlunsplit(('https', host, (p.path or '').rstrip('/'), q, ''))


URL_RE = re.compile(r'https?://[^\s"\'<>)\]]+')


def _plain(o):
    """SDK 객체를 dict 로 (pydantic model_dump). dict·list·원시값은 그대로."""
    if hasattr(o, 'model_dump'):
        return o.model_dump()
    return o


def _walk_urls(o, out):
    o = _plain(o)
    if isinstance(o, dict):
        for k, v in o.items():
            if k == 'url' and isinstance(v, str):
                out.setdefault(norm_url(v), (o.get('title') or '', o.get('page_age')))
            elif k == 'encrypted_content':
                continue
            else:
                _walk_urls(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk_urls(v, out)
    elif isinstance(o, str):
        for u in URL_RE.findall(o):
            out.setdefault(norm_url(u.rstrip('.,;')), ('', None))


def result_urls(blocks):
    """도구가 실제로 돌려준 URL {정규화 URL: (제목, page_age)}.

    모델이 쓴 글(`text`·`thinking`·`server_tool_use` 의 입력)은 보지 않는다 — 거기 적힌
    URL 은 모델의 말이지 검색 결과가 아니다. 보는 것은 도구 결과 블록 전부
    (`web_search_tool_result` 와, dynamic filtering 이 결과를 코드 실행으로 거를 때 오는
    코드 실행 결과 블록의 stdout)와 `text` 블록의 `citations`(API 가 검색 결과에 묶어
    붙이는 인용)다.
    """
    out = {}
    for b in blocks:
        t = _g(b, 'type') or ''
        if t == 'text':
            for c in _g(b, 'citations') or []:
                _walk_urls(c, out)
        elif t.endswith('_tool_result'):
            _walk_urls(_g(b, 'content'), out)
    out.pop('', None)
    return out


def search_errors(blocks):
    errs = []
    for b in blocks:
        if _g(b, 'type') == 'web_search_tool_result':
            c = _g(b, 'content')
            if c is not None and not isinstance(c, list):
                errs.append(str(_g(c, 'error_code') or c))
    return errs


def text_of(blocks):
    return ''.join(_g(b, 'text') or '' for b in blocks if _g(b, 'type') == 'text')


def extract_json(text):
    """마지막 ```json 블록, 없으면 마지막 최상위 {...}. 없으면 ValueError."""
    fences = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text or '', flags=re.S)
    if fences:
        return json.loads(fences[-1])
    s = text or ''
    end = s.rfind('}')
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            if s[i] == '}':
                depth += 1
            elif s[i] == '{':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[i:end + 1])
                    except ValueError:
                        break
        end = s.rfind('}', 0, end)
    raise ValueError('응답에 JSON 이 없다')


class Spend:
    """토큰·검색 횟수. 비용은 설정 단가로 코드가 계산한다(추정)."""

    def __init__(self):
        self.calls = self.input = self.output = self.searches = 0
        self._lock = threading.Lock()           # 그룹 호출이 병렬로 더한다

    def add(self, u):
        with self._lock:
            self._add(u)

    def _add(self, u):
        self.calls += 1
        self.input += (_g(u, 'input_tokens', 0) or 0) + (_g(u, 'cache_read_input_tokens', 0) or 0) \
            + (_g(u, 'cache_creation_input_tokens', 0) or 0)
        self.output += _g(u, 'output_tokens', 0) or 0
        st = _g(u, 'server_tool_use')
        self.searches += (_g(st, 'web_search_requests', 0) or 0) if st else 0

    def usd(self, price):
        return round(self.input / 1e6 * price['input_per_m'] + self.output / 1e6 * price['output_per_m']
                     + self.searches / 1e3 * price['search_per_k'], 4)

    def as_dict(self, price):
        return dict(calls=self.calls, input=self.input, output=self.output,
                    searches=self.searches, usd=self.usd(price), is_estimate=True)


def call(cl, cfg, system, prompt, max_uses, spend, max_tokens=None):
    """웹 검색을 켠 한 번의 조사. pause_turn 이면 이어 부른다. 반환: 블록 전부."""
    tools = [{'type': cfg.get('search_tool', 'web_search_20260209'), 'name': 'web_search',
              'max_uses': int(max_uses)}]
    kw = dict(model=cfg['model'], max_tokens=int(max_tokens or cfg.get('research_max_tokens', 16000)),
              system=system, tools=tools)
    if cfg.get('effort'):
        kw['output_config'] = {'effort': cfg['effort']}
    user = {'role': 'user', 'content': prompt}
    blocks = []
    for _ in range(int(cfg.get('max_continuations', 4)) + 1):
        msgs = [user] + ([{'role': 'assistant', 'content': list(blocks)}] if blocks else [])
        resp = cl.messages.create(messages=msgs, **kw)
        spend.add(_g(resp, 'usage'))
        stop = _g(resp, 'stop_reason')
        if stop == 'refusal':
            d = _g(resp, 'stop_details')
            raise RuntimeError(f'모델이 거부함: {_g(d, "category", "")}')
        blocks.extend(_g(resp, 'content') or [])
        if stop != 'pause_turn':
            if stop == 'max_tokens':
                raise RuntimeError(f'출력이 상한에서 잘림 (max_tokens {kw["max_tokens"]:,})')
            return blocks
    raise RuntimeError('pause_turn 이어 부르기 상한 초과')


# ─────────────────────────── 프롬프트 ───────────────────────────
SYSTEM = """당신은 글로벌 투자자·테크 리더의 공개 발언을 확인하는 리서처다. web_search 로
확인한 공개 보도(기사·인터뷰·방송·본인 X/블로그 게시물 보도)만 쓴다.

규칙
- '당일 발언(pass)' 은 조사 창 안에 **본인이 직접** 한 발언·게시물·인터뷰가 공개되었거나
  처음 보도된 경우만이다. 본인 발언 없이 회사·펀드 소식만 있으면 pass 가 아니다.
- 모든 pass 에는 그 발언을 전한 출처 URL 을 **검색 결과에 실제로 나온 URL 그대로** 적는다.
  검색하지 않은 URL, 기억으로 만든 URL 은 쓰지 않는다. 출처 날짜(YYYY-MM-DD)를 적는다.
- 수치·고유명사·인용은 출처 표기 그대로 옮긴다. 환산·반올림·추정하지 않는다.
- 확인하지 못한 것은 쓰지 않는다. 모르면 status 를 none 으로 둔다.
- 한국어 경어체(~했습니다)로 쓴다. 형용사로 강조하지 않는다.
- 최종 답은 마지막에 ```json 블록 하나로만 낸다."""


def people_prompt(group, win, cfg):
    names = '\n'.join(f'- {p["name"]} ({p["org"]})' for p in group['people'])
    return f"""조사 창: {win['start']} ~ {win['end']} (KST). 검증 기준 미국장: {win['session']}.

아래 인물 각각을 검색해 조사 창 안의 본인 발언을 확인하라. 사람마다 최소 한 번은
검색한다.

{names}

사람마다:
- status: "pass"(창 안 본인 발언 확인) 또는 "none"
- remark: pass 일 때 무엇을 말했는지 2~5문장. 언제(요일·매체)·무엇을·수치를 구체적으로
- implication: pass 일 때 투자 관점 시사점 1~2문장("→" 없이)
- sources: remark 의 근거 [{{"url","outlet","date"}}]
- note: 창 밖이지만 최근 {cfg.get('note_days', 30)}일 안의 주목할 발언이 있으면 1~2문장(날짜 포함),
  없으면 "". note_sources 에 근거 URL 을 같은 형식으로

```json
{{"people": [{{"name": "...", "status": "pass|none", "remark": "", "implication": "",
  "sources": [], "note": "", "note_sources": []}}]}}
```
이름은 위 목록 표기 그대로 쓴다."""


def market_prompt(win, cfg):
    return f"""검증 기준 미국장: {win['session']} (조사 창 {win['start']} ~ {win['end']} KST).
그날 미국장 마감 상황을 검색해 한 문단(3~5문장)으로 정리하라. 다룰 것: {cfg.get('market_items', '')}.
모든 수치는 출처 표기 그대로. 확인하지 못한 항목은 빼고 지어내지 않는다.

```json
{{"line": "...", "sources": [{{"url": "...", "outlet": "...", "date": "YYYY-MM-DD"}}]}}
```"""


# ─────────────────────────── 검증 ───────────────────────────
def _date_ok(s, lo, hi):
    try:
        d = date.fromisoformat(str(s)[:10])
    except ValueError:
        return False
    return lo <= d <= hi


def _keep_sources(srcs, urls, lo, hi):
    """검색 결과에 있던 URL + 날짜가 범위 안인 출처만."""
    keep = []
    for s in srcs or []:
        u = norm_url(_g(s, 'url'))
        if u and u in urls and _date_ok(_g(s, 'date'), lo, hi):
            keep.append(dict(url=_g(s, 'url'), outlet=(_g(s, 'outlet') or '').strip(),
                             date=str(_g(s, 'date'))[:10]))
    return keep


def name_key(s):
    """이름 대조 키. 모델이 '이름 (소속)' 처럼 소속을 붙여 돌려주는 일이 있다(2026-10-01
    첫 실행에서 가치투자 그룹 9명이 전부 이것으로 버려졌다). 괄호 부분·대소문자·
    유니코드 표기 차이·여분 공백을 지우고 비교한다."""
    import unicodedata
    s = re.sub(r'\s*\(.*?\)\s*', ' ', s or '')
    s = unicodedata.normalize('NFKC', s).casefold()
    return ' '.join(s.split())


def validate_people(raw, group, urls, win, cfg):
    """모델 출력 → 인물별 확정값. 반환 (people, drops)."""
    roster = {name_key(p['name']): p['name'] for p in group['people']}
    got = {}
    drops = []
    for e in (raw or {}).get('people') or []:
        raw_name = (_g(e, 'name') or '').strip()
        name = roster.get(name_key(raw_name))
        if name is None:
            drops.append(f'명단 밖 이름 버림: {raw_name[:40]}')
            continue
        got[name] = e
    start = date.fromisoformat(win['start'][:10])
    asof = date.fromisoformat(win['asof'])
    pass_lo, note_lo = start - timedelta(days=1), asof - timedelta(days=int(cfg.get('note_days', 30)))
    out = []
    for p in group['people']:
        e = got.get(p['name'])
        row = dict(name=p['name'], org=p['org'], status='unknown', remark='', implication='',
                   sources=[], note='', note_sources=[])
        if e is None:
            drops.append(f'{p["name"]} 조사 결과 없음')
            out.append(row)
            continue
        row['status'] = 'none'
        if _g(e, 'status') == 'pass':
            srcs = _keep_sources(_g(e, 'sources'), urls, pass_lo, asof)
            if srcs and (_g(e, 'remark') or '').strip():
                row.update(status='pass', remark=_g(e, 'remark').strip(),
                           implication=(_g(e, 'implication') or '').strip(), sources=srcs)
            else:
                drops.append(f'{p["name"]} 발언 출처가 검색 결과·조사 창과 맞지 않아 뺐음')
        note = (_g(e, 'note') or '').strip()
        if note:
            ns = _keep_sources(_g(e, 'note_sources'), urls, note_lo, asof)
            if ns:
                row.update(note=note, note_sources=ns)
            else:
                drops.append(f'{p["name"]} 참고 발언 출처 불일치로 뺐음')
        out.append(row)
    return out, drops


def validate_market(raw, urls, win):
    lo = date.fromisoformat(win['session']) - timedelta(days=1)
    hi = date.fromisoformat(win['asof'])
    srcs = _keep_sources(_g(raw or {}, 'sources'), urls, lo, hi)
    line = (_g(raw or {}, 'line') or '').strip()
    if not (line and srcs):
        return None, ['시장 배경 — 출처가 검색 결과와 맞지 않아 뺐음']
    return dict(line=line, sources=srcs), []


# ─────────────────────────── 실행 ───────────────────────────
def _claimed_unmatched(raw, urls):
    """모델이 출처를 단 항목이 있는데 그 URL 이 하나도 도구 결과에 없나.

    전부 어긋나면 개별 항목의 잘못이 아니라 결과를 읽는 쪽(도구 버전)의 문제일 가능성이
    크다 — dynamic filtering 이 결과를 우리가 못 읽는 모양으로 줄 때다(GURU.md 2장).
    """
    rows = (raw or {}).get('people') or [raw or {}]
    srcs = [norm_url(_g(s, 'url')) for r in rows
            for s in (_g(r, 'sources') or []) + (_g(r, 'note_sources') or [])]
    srcs = [u for u in srcs if u]
    return bool(srcs) and not any(u in urls for u in srcs)


def _one(cl, cfg, prompt, max_uses, spend, log=print):
    blocks = call(cl, cfg, SYSTEM, prompt, max_uses, spend)
    raw = extract_json(text_of(blocks))
    urls = result_urls(blocks)
    fb = cfg.get('search_tool_fallback')
    if fb and fb != cfg.get('search_tool') and _claimed_unmatched(raw, urls):
        # 출처 대조가 전부 실패 — 기본 검색 도구로 한 번만 다시 조사한다. 비용이 한 번
        # 더 든다. 어느 쪽 결과를 썼는지 호출자가 결손 줄에 남긴다.
        log(f'  출처 대조 전부 실패 — {fb} 로 다시 조사')
        cfg2 = dict(cfg, search_tool=fb)
        blocks = call(cl, cfg2, SYSTEM, prompt, max_uses, spend)
        raw = extract_json(text_of(blocks))
        urls = result_urls(blocks)
        return raw, urls, search_errors(blocks) + [f'출처 대조 전부 실패로 {fb} 로 재조사함']
    return raw, urls, search_errors(blocks)


def _debug(raw, urls):
    """출처 대조 진단 — 모델이 단 URL 과 도구 결과 URL 수·일치 수. 대조가 왜 실패했는지
    다음 실행 뒤에 볼 수 있게 남긴다(첫 실행에서 시장 배경이 이유를 모르고 빠졌다)."""
    rows = (raw or {}).get('people') or [raw or {}]
    claimed = [(_g(s, 'url') or '', _g(s, 'date')) for r in rows
               for s in (_g(r, 'sources') or []) + (_g(r, 'note_sources') or [])]
    return dict(result_urls=len(urls), claimed=len(claimed),
                matched=sum(1 for u, _ in claimed if norm_url(u) in urls),
                unmatched=[f'{u} ({d})' for u, d in claimed if norm_url(u) not in urls][:10],
                sample_results=sorted(urls)[:10])


def run(cl, cfg, win, log=print):
    """반환 dict — groups(인물별), market, drops, spend(추정 비용)."""
    spend = Spend()
    jobs = {'market': (market_prompt(win, cfg), max(3, int(cfg.get('searches_min', 6)) // 2))}
    for g in cfg['groups']:
        n = max(int(cfg.get('searches_min', 6)),
                round(len(g['people']) * float(cfg.get('searches_per_person', 1.5))))
        jobs[g['key']] = (people_prompt(g, win, cfg), n)
    res, drops = {}, []
    with cf.ThreadPoolExecutor(max_workers=int(cfg.get('workers', 3))) as ex:
        futs = {ex.submit(_one, cl, cfg, p, n, spend, log): k for k, (p, n) in jobs.items()}
        for f in cf.as_completed(futs):
            k = futs[f]
            try:
                res[k] = f.result()
            except Exception as e:                       # noqa: BLE001 — 사유를 남긴다
                res[k] = None
                drops.append(f'{k} 조사 실패 — {type(e).__name__}: {str(e)[:120]}')
                log(f'  조사 실패 [{k}]: {e}')
    groups, debug = [], {}
    for g in cfg['groups']:
        r = res.get(g['key'])
        if r is None:
            people = [dict(name=p['name'], org=p['org'], status='unknown', remark='',
                           implication='', sources=[], note='', note_sources=[])
                      for p in g['people']]
        else:
            raw, urls, errs = r
            debug[g['key']] = _debug(raw, urls)
            people, d = validate_people(raw, g, urls, win, cfg)
            drops += d
            drops += [f'{g["title"]} 검색 — {e}' for e in errs]
        groups.append(dict(key=g['key'], title=g['title'], people=people))
    market = None
    if res.get('market'):
        raw, urls, errs = res['market']
        debug['market'] = _debug(raw, urls)
        market, d = validate_market(raw, urls, win)
        drops += d
    else:
        drops.append('시장 배경 조사 실패')
    price = cfg['price']
    log(f'  조사 — 호출 {spend.calls} · 검색 {spend.searches} · 입력 {spend.input:,} · '
        f'출력 {spend.output:,} · 추정 ${spend.usd(price):.2f}')
    return dict(window=win, groups=groups, market=market, drops=drops, debug=debug,
                spend=spend.as_dict(price), source='claude-web-search',
                as_of=datetime.now(KST).isoformat(timespec='seconds'))
