#!/usr/bin/env python3
"""종목 종류 — 보통주 / 우선주 / 스팩 / 리츠.

신고가 표에 이 넷이 섞여 있으면 읽는 사람이 매번 손으로 걸러낸다. 우선주는
유동성이 얇아 하루 몇 %가 쉽게 나오고, 스팩은 합병 기대만으로 오르며, 리츠는
배당락 주기로 움직인다. 셋 다 '오늘 이 산업에 무슨 일이 있었나' 와 무관하다.

**빼지 않고 나눈다.** 보통주만 보고 싶은 날과 리츠를 보고 싶은 날이 둘 다 있다.
화면에서 끄고 켤 수 있게 표시만 붙이고, 판정 근거를 함께 남긴다.

## 판정 근거

- **우선주** — 종목코드 여섯 자리의 끝자리가 0 이 아니고(보통주는 0),
  이름이 `우` · `우B` · `2우B` 처럼 끝난다. **둘 다 맞을 때만** 우선주로 본다.
  한쪽만 맞으면 보통주로 둔다 — 확신이 없으면 단정하지 않는다(CLAUDE.md 2장 1번).
  코드만 보면 신주인수권증서·구형 코드가 섞이고, 이름만 보면 '한국테크놀로지'
  같은 이름의 끝 글자를 잘못 읽는다.
- **스팩** — 이름에 `스팩` 이 들어간다. 국내 SPAC 는 예외 없이 상품명에 넣는다.
- **리츠** — 이름이 `리츠` 로 끝난다. 국내 상장 리츠의 표기 관례다.

순서는 스팩 > 리츠 > 우선주 > 보통주. 겹칠 일은 없지만 정해 두면 결과가 흔들리지
않는다.
"""
import re

COMMON, PREF, SPAC, REIT = 'common', 'pref', 'spac', 'reit'

LABEL = {COMMON: '보통주', PREF: '우선주', SPAC: '스팩', REIT: '리츠'}

# '삼성전자우', '현대차2우B', 'LG화학우'. 앞에 숫자가 붙는 것은 2우선·3우선이다.
_PREF_NAME = re.compile(r'\d?우[A-Z]?$')
_SPAC = re.compile(r'스팩')
_REIT = re.compile(r'리츠$')


def of(code, name):
    """종목 종류를 돌려준다. 판단이 서지 않으면 보통주다."""
    nm = (name or '').strip()
    if _SPAC.search(nm):
        return SPAC
    if _REIT.search(nm):
        return REIT
    cd = (code or '').strip()
    if len(cd) == 6 and cd.isdigit() and cd[-1] != '0' and _PREF_NAME.search(nm):
        return PREF
    return COMMON


def common_name(name):
    """우선주 이름에서 접미('우'·'우B'·'2우B')를 뗀 보통주 이름. 접미가 없으면 그대로.

    종목별 트리거(ingest/triggers.py)가 우선주를 보통주 이름으로 질의할 때 쓴다.
    판정은 `of()` 가 코드까지 보고 하고, 여기는 이름만 다듬는다 — '현대차2우B' 의
    보통주는 '현대차2' 가 아니라 '현대차' 다.
    """
    return _PREF_NAME.sub('', (name or '').strip())


def counts(rows):
    """{종류: 종목수}. 보통주가 아닌 것만 센다 — 화면에 몇 개를 끄는지 적기 위해서."""
    out = {}
    for x in rows:
        k = x.get('kind') or of(x.get('code'), x.get('name'))
        if k != COMMON:
            out[k] = out.get(k, 0) + 1
    return out
