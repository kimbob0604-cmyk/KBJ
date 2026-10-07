#!/usr/bin/env python3
"""
후처리 검증 — CLAUDE.md 2장 4번.

  "LLM이 종목명·수치를 지어내면 실패로 처리한다. 서술 생성 후 리포트에 등장한
   모든 종목명이 입력 컨텍스트에 있었는지 검증하는 후처리를 반드시 통과시킨다."

여기서 두 가지를 본다.

**종목명** — 상장 종목 마스터 전체를 사전으로 쓴다. 초안에 등장하는데 그 섹션의
사실 팩에는 없는 종목명이 있으면 위반이다. 실재하는 종목명을 엉뚱한 섹션에 끌어다
쓰는 것도 잡힌다.

**수치** — 사실 팩의 수치는 이미 문자열로 렌더돼 있다. 초안의 모든 수치 토큰이 그
문자열 집합 안에 있어야 한다. `+13.63%` 를 `약 14%` 로 바꾸면 여기서 걸린다.

검증 실패는 경고가 아니라 실패다. 발송 경로를 막는다.
"""
import re
import unicodedata

# 초안에서 뽑아낼 수치 토큰. 단위가 붙은 것만 본다.
# 순번("3개"), 연도("2026년") 같은 것까지 잡으면 오탐만 늘어난다.
NUM = re.compile(
    r'[+-]?\d[\d,]*(?:\.\d+)?\s*(?:%p|%|억원|억|조원|조|배|원|주|bp|pt)')

# `한전기술(+13.63%)` 형태의 이름-값 짝. 이게 이 문서의 기본 형태다
# (문체 규칙: "종목을 처음 언급할 때는 등락률을 괄호로 붙인다").
# 짝으로 잡아야 **팩에 있는 수치를 엉뚱한 종목에 붙이는 조작**을 잡을 수 있다.
# 수치를 집합으로만 대조하면 200종목짜리 팩에서 아무 숫자나 아무 종목에 붙는다.
# 이름은 **글자로 시작**해야 한다. 안 그러면 `6,808.21(+0.97%)` 의 '808.21' 을
# 종목명으로 잡는다. 앞이 숫자·쉼표·점이면 수치 한가운데라 짝이 아니다.
PAIR = re.compile(
    r'(?<![\d,.])([가-힣A-Za-z][가-힣A-Za-z0-9&·\.\-]{1,19})\s*\('
    r'\s*([+-]?\d[\d,]*(?:\.\d+)?\s*%)')

# 사실 팩 안에서 수치 문자열을 찾을 때 쓰는 같은 패턴
PACK_NUM = re.compile(
    r'[+-]?\d[\d,]*(?:\.\d+)?\s*(?:%p|%|억원|억|조원|조|배|원|주|bp|pt)')

# 지수처럼 단위가 없는 수치는 별도로 허용 목록을 만든다 (6,808.21 같은 값)
BARE = re.compile(r'\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+')


def norm(s):
    """비교용 정규화. 공백과 표기 흔들림을 흡수한다."""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(s or '')))


def collect_strings(obj, out=None, skip=()):
    """중첩 dict/list 에서 모든 문자열을 긁는다. `skip` 키 아래는 들어가지 않는다."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip:
                continue
            collect_strings(v, out, skip)
    elif isinstance(obj, list):
        for v in obj:
            collect_strings(v, out, skip)
    elif isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, (int, float)):
        out.append(str(obj))
    return out


# 이 키 아래의 수치는 허용하지 않는다. 종목의 `trigger` 는 기사·공시 **제목**이라
# 거기 든 '영업이익 120억' 은 계산된 사실이 아니다 — 프롬프트가 "trigger 항목의
# 수치도 옮기지 마라" 고 하는데 검사 2 가 통과시키면 제목의 수치가 종목 줄에 계산값
# 처럼 실린다. 테마 `news` 의 제목 수치는 예전 그대로 허용한다(뉴스 탭과 같은 패턴).
NUMBER_SKIP_KEYS = ('trigger',)


def allowed_numbers(pack):
    """사실 팩이 허용하는 수치 문자열 집합. 종목 `trigger` 제목의 수치는 뺀다."""
    ok = set()
    for s in collect_strings(pack, skip=NUMBER_SKIP_KEYS):
        for m in PACK_NUM.findall(s):
            ok.add(norm(m))
        for m in BARE.findall(s):
            ok.add(norm(m))
        # 사실 팩 문자열 자체가 수치인 경우 ("6,808.21")
        if BARE.fullmatch(s.strip()) or PACK_NUM.fullmatch(s.strip()):
            ok.add(norm(s))
    return ok


def allowed_names(pack):
    """사실 팩이 허용하는 종목명 집합."""
    ok = set()
    _walk_names(pack, ok)
    return ok


# 이 키 아래의 문자열은 종목명(또는 종목명 목록)으로 본다.
# detected 블록은 stocks 가 문자열 배열이고, 테마 블록은 dict 배열이라 둘 다 받는다.
NAME_KEYS = ('name', 'stocks', 'tickers')


def _walk_names(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in NAME_KEYS:
                _add_names(v, out)
            _walk_names(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_names(v, out)


def _add_names(v, out):
    if isinstance(v, str):
        out.add(norm(v))
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, str):
                out.add(norm(x))
            else:
                _walk_names(x, out)


def pairs(pack):
    """사실 팩의 (종목명 -> 그 종목에 딸린 수치 문자열 집합).

    이름-값 짝을 검증하려면 어느 값이 어느 종목의 것인지 알아야 한다.
    """
    out = {}
    _walk_pairs(pack, out)
    return out


def _walk_pairs(obj, out):
    if isinstance(obj, dict):
        nm = obj.get('name')
        if isinstance(nm, str):
            vals = out.setdefault(norm(nm), set())
            for k, v in obj.items():
                if k == 'name' or not isinstance(v, str):
                    continue
                for m in PACK_NUM.findall(v):
                    vals.add(norm(m))
                if PACK_NUM.fullmatch(v.strip()) or BARE.fullmatch(v.strip()):
                    vals.add(norm(v))
        for v in obj.values():
            _walk_pairs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_pairs(v, out)


# 사실 팩의 키가 본문에 새는 것을 잡는다. snake_case 는 한국어 리포트에 나올
# 이유가 없다. 코드블록 안(`...`)은 의도적으로 키를 인용한 것일 수 있어 뺀다.
#
# 경계는 **ASCII** 로 본다. `\w` 는 유니코드라 한글 조사가 붙은 `stages_new_today가`
# 를 한 낱말로 읽어 못 잡았다 — 2026-09-21 초안에 그대로 샜다. 키 뒤에 한글이
# 오는 것은 키가 본문에 박힌 것이지 다른 낱말이 아니다.
FIELD_LEAK = re.compile(r'(?<![`A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![`A-Za-z0-9_])')

# 트리거를 모른다고 적는 문장. 2026-09-21 초안이 테마마다 "트리거는 확인되지 않음"
# 을 찍었고 "테마 전체 상승 트리거로 단정하긴 어려움" 까지 나갔다. 프롬프트로만 막으면
# 새므로 기계로 막는다 — 모르면 트리거 문장을 쓰지 않는 것이 규칙이다(CLAUDE.md
# 7장). 어휘(트리거·재료·뉴스·호재·원인·촉매) + 조사(있어도 없어도) + 부정·미확인.
#
# 예외는 문체 규칙의 다른 줄이 **요구하는** 문장이다 — "단독 보도나 미확인 루머가
# 트리거면 그 사실을 함께 적는다"(STYLE 규칙 5). "재료는 미확인 루머 — X 포워딩의
# 지분 인수설"·"재료는 확인되지 않은 지분 인수설" 은 트리거를 모른다는 말이 아니라
# 트리거가 무엇인지(미확인 루머라는 것)를 적은 것이다. 같은 절 안에서 '미확인'·
# '확인되지 않은' 뒤에 루머·보도·소식·풍문·~설 이 이어지면 통과시킨다. 술어형
# ("확인되지 않음"·"트리거 미확인.")은 그대로 걸린다.
_RUMOR = r'[^.,;—\n]*?(?:루머|보도|소식|풍문|[가-힣]설)(?![가-힣]{3})'
PHRASE = re.compile(
    r'(트리거|재료|뉴스|호재|원인|촉매)\s*(?:는|가|은|도|를|이|로|으로|의)?\s*'
    r'(?!미확인' + _RUMOR + r')(?!확인되지\s*않은' + _RUMOR + r')'
    r'(없|미확인|불명|확인되지|확인\s*안|부재|단정하긴|단정하기|알\s*수\s*없|알기\s*어려)')
# 문장 경계. 마침표·물음표·느낌표 뒤 공백. '6,938.34로' 처럼 소수점 뒤에 글자가
# 붙은 것은 경계가 아니다.
_SENT = re.compile(r'(?<=[.!?])\s+')
# 불릿 접두. 문장을 다 지운 줄은 접두만 남으므로 줄째 뺀다.
_BULLET = re.compile(r'^(\s*(?:[-*]|»)\s+)')


def _sentences(line):
    """한 줄을 (접두, [문장…]) 으로. 접두는 불릿 표식이다."""
    m = _BULLET.match(line)
    head = m.group(1) if m else ''
    body = line[len(head):]
    return head, [s for s in _SENT.split(body) if s.strip()]


def phrases(draft):
    """금지 문구가 든 문장들."""
    out = []
    for line in (draft or '').splitlines():
        _h, sents = _sentences(line)
        out.extend(s.strip() for s in sents if PHRASE.search(s))
    return out


def strip_phrases(draft):
    """금지 문구가 든 **문장만** 지운다. 반환 (본문, 지운 문장 수).

    한 번 더 불러도 남았을 때 초안 전체를 막는 대신 쓰는 최후 수단이다 — 종목명·
    수치 위조와 달리 이 문장은 지워도 없는 사실이 생기지 않는다. 문장을 다 지운
    줄은 접두만 남으므로 줄째 뺀다.

    경계는 마침표다 — 쉼표·'—' 로 이어진 절은 한 문장이라 "한전기술(+13.63%)은 …
    트리거는 확인되지 않음, 거래량 5배임" 은 등락률·거래량 사실까지 함께 나간다.
    지운 문장에 `이름(값)` 짝이 있었는지는 `phrases()` 로 미리 알 수 있고, compose 가
    결손 문구에 그 수를 따로 적는다 — 검수자가 사라진 종목 사실을 알아야 한다.
    """
    out, n = [], 0
    for line in (draft or '').splitlines():
        head, sents = _sentences(line)
        if not sents:
            out.append(line)
            continue
        keep = [s for s in sents if not PHRASE.search(s)]
        n += len(sents) - len(keep)
        if keep:
            out.append(head + ' '.join(s.strip() for s in keep))
        elif not head and line.strip():
            out.append('')          # 접두 없는 줄이 통째로 지워지면 빈 줄로 남긴다
    return '\n'.join(out), n


# 이 키 아래의 문자열은 **기사·재료의 본문**이다. 거기 등장한 상장 종목명은 초안에
# 옮겨 적혀도 '지어낸 이름' 이 아니다(제목이 그 이름을 담고 있다). 검사 3 에서만
# 관용한다 — `이름(값)` 짝(검사 1)에는 여전히 팩의 종목만 허용된다. 제목 속
# 다른 종목에 수치를 붙이면 그건 팩에 없는 종목의 값이다.
MENTION_KEYS = ('news', 'trigger')


def _spans(needle, hay):
    """hay 안에서 needle 이 선 자리 전부 [(시작, 끝)]. 겹침도 센다."""
    out, i = [], 0
    while True:
        i = hay.find(needle, i)
        if i < 0:
            return out
        out.append((i, i + len(needle)))
        i += 1


def _uncovered(n, text, longers):
    """text 안의 n 자리 중 더 긴 이름(longers) 자리에 **덮이지 않은** 것이 하나라도 있는가.

    '한국전력기술' 안의 '한국전력' 은 그 이름의 일부지 다른 종목이 아니다. 하지만
    같은 본문에 '한국전력도 동반 강세' 가 따로 서 있으면 그 자리는 덮이지 않은 것이라
    다른 종목을 부른 것이다. 이름이 있느냐가 아니라 **어느 자리**에 있느냐를 본다.
    """
    cover = [sp for m in longers for sp in _spans(m, text)]
    return any(not any(i2 <= i and j <= j2 for i2, j2 in cover)
               for i, j in _spans(n, text))


def mentioned_names(pack, universe_names):
    """news·trigger 의 제목·요약·매체에 등장하는 유니버스 이름 집합(정규화).

    부분 문자열로만 보면 제목 '한국전력기술, 웨스팅하우스 …' 하나가 '한국전력' 까지
    관용해, 팩에 없는 종목을 초안에 끌어와도 검사 3 을 통과했다. 같은 자리에서 더 긴
    **유니버스** 이름이 잡히면 짧은 이름은 그 이름의 일부라 관용하지 않는다(검사 3 의
    '더 긴 이름의 일부' 규칙과 같은 방식). 유니버스에 없는 긴 낱말('웨스팅하우스')의
    일부는 그대로 관용한다 — 초안이 그 낱말을 옮겨 적는 것은 지어낸 이름이 아니다.
    """
    texts = []
    _walk_mentions(pack, texts)
    if not texts:
        return set()
    names = {norm(n) for n in universe_names if len(norm(n)) >= 3}
    out = set()
    for raw in texts:
        t = norm(raw)
        hit = [n for n in names if n in t]
        for n in hit:
            longers = [m for m in hit if len(m) > len(n) and n in m]
            if _uncovered(n, t, longers):
                out.add(n)
    return out


def _walk_mentions(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in MENTION_KEYS:
                for it in (v if isinstance(v, list) else [v]):
                    if isinstance(it, dict):
                        for kk in ('title', 'summary', 'publisher', 'outlet'):
                            if isinstance(it.get(kk), str):
                                out.append(it[kk])
            else:
                _walk_mentions(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_mentions(v, out)


def check(draft, pack, universe_names, strict_numbers=True):
    """초안을 검증한다.

    draft            생성된 마크다운
    pack             그 섹션에 들어간 사실 팩
    universe_names   상장 전 종목명 (종목명 사전)
    반환 (ok, violations)

    세 가지를 본다.
      1. `이름(값)` 짝이 팩에서 실제로 그 종목의 값인가 — 값을 엉뚱한 종목에
         붙이는 조작과, 팩에 아예 없는 이름을 지어내는 조작을 함께 잡는다
      2. 모든 수치 토큰이 팩에 있는가 (단위 없는 지수 레벨 포함)
      3. 팩에 없는 상장 종목명이 본문에 등장하는가 (다른 섹션에서 끌어오기)
    """
    v = []
    body = norm(draft)
    ok_names = allowed_names(pack)
    by_name = pairs(pack)
    uni_norm = {norm(x) for x in universe_names}
    mentioned = mentioned_names(pack, universe_names)
    # 라벨 어휘. '52주 신고가' 의 '52주' 는 수치가 아니라 이름이다. NUM 의
    # 단위 목록에 '주'(주식 수)가 있어 여기 걸렸고, 장 흐름 섹션이 이것 때문에
    # 매번 3회 재시도 끝에 통째로 빠졌다 (2026-09-01 실측).
    vocab = {norm(k) for k in (pack.get('counts') or {})}

    # ── 1. 이름-값 짝 ────────────────────────────────
    paired = set()
    for raw_name, raw_val in PAIR.findall(draft):
        n, val = norm(raw_name), norm(raw_val)
        paired.add(n)
        if n not in ok_names:
            # 두 글자 이하 일반 단어는 산문이다 — "6,808.21로 마감(+0.97%)" 의
            # '마감' 이 종목명으로 잡혔다. 단 두 글자 상장명(기아)은 그대로
            # 잡는다. 3번 검사와 같은 기준이다.
            if len(n) < 3 and n not in uni_norm:
                continue
            v.append(dict(kind='name', value=raw_name,
                          why='사실 팩에 없는 종목명에 수치를 붙였다'))
            continue
        if val not in by_name.get(n, set()):
            v.append(dict(kind='pair', value=f'{raw_name}({raw_val})',
                          why=f'{raw_name} 의 값이 아니다 — 사실 팩의 다른 종목 값이거나 없는 값'))

    # ── 2. 수치 ──────────────────────────────────────
    if strict_numbers:
        ok_nums = allowed_numbers(pack)
        seen = set()
        for m in NUM.findall(draft) + BARE.findall(draft):
            n = norm(m)
            if n in ok_nums or n in seen or n in vocab:
                continue
            seen.add(n)
            v.append(dict(kind='number', value=m,
                          why='사실 팩에 없는 수치가 초안에 등장'))

    # ── 3. 팩 밖 종목명 ──────────────────────────────
    # 긴 이름부터 본다. '카카오페이' 가 팩에 있는데 '카카오' 로 걸리면 오탐이다.
    for name in sorted(universe_names, key=len, reverse=True):
        n = norm(name)
        # 두 글자 이하는 일반 단어와 충돌한다 ("대상", "한국", "지주")
        if len(n) < 3 or n in ok_names or n in paired:
            continue
        if n not in body:
            continue
        # 팩에 있는 더 긴 이름의 일부로만 쓰였다면 그 이름이 쓰인 것이다. 자리로 본다 —
        # '한국전력기술(+13.63%) … 한국전력도 동반 강세' 의 둘째 자리는 덮이지 않는다.
        longers = [m for m in ok_names if len(m) > len(n) and n in m]
        if longers and not _uncovered(n, body, longers):
            continue
        # 기사·재료 제목에 있는 이름은 지어낸 것이 아니다 — 여기서만 관용한다
        if n in mentioned:
            continue
        v.append(dict(kind='name', value=name,
                      why='사실 팩에 없는 종목명이 초안에 등장'))

    # ── 4. 필드 이름 유출 ────────────────────────────
    # 프롬프트가 사실 팩의 키를 설명하느라 그 이름을 적는데, 모델이 그걸 본문에
    # 그대로 옮기는 경우가 있다. 2026-08-28 초안에 "stages_new_today 기준" 이
    # 그대로 나갔다. 문체 규칙으로만 막으면 새고, 기계로 막는다.
    for m in set(FIELD_LEAK.findall(draft)):
        v.append(dict(kind='jargon', value=m,
                      why='사실 팩의 필드 이름이 본문에 그대로 나왔다 — 우리말로 풀어야 한다'))

    # ── 5. 금지 문구 ─────────────────────────────────
    # "트리거는 확인되지 않음" 류. 모르면 트리거 문장을 쓰지 않는 것이 규칙이다.
    # compose 가 이 종류만 남으면 한 번 더 부르고, 그래도 남으면 그 문장만 지운다.
    for s in phrases(draft):
        v.append(dict(kind='phrase', value=s[:80],
                      why='트리거를 모르면 트리거 문장을 쓰지 않는다 — 금지 문구'))
    return (not v), v


def report(violations, limit=20):
    if not violations:
        return '검증 통과'
    lines = [f'검증 실패 {len(violations)}건']
    for x in violations[:limit]:
        lines.append(f'  [{x["kind"]}] {x["value"]} — {x["why"]}')
    if len(violations) > limit:
        lines.append(f'  ... 외 {len(violations)-limit}건')
    return '\n'.join(lines)
