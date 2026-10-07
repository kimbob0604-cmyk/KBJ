#!/usr/bin/env python3
"""
게시 전 점검 — 17:09 루틴 "국장 신고가 보드 — 아티팩트 갱신" 의 3번 검사를 코드로 옮긴 것.

    python3 -m board.tools.artifact_check            # docs/api/latest.json
    python3 -m board.tools.artifact_check --asof 2026-09-28

틀린 보드를 올리느니 어제 것을 두는 편이 낫다(루틴 프롬프트). 로컬 실행
(board/scripts/local_daily.sh)이 커밋 전에 이것을 돌려, 루틴이 거부할 보드를
미리 알린다. 판정은 docs/api/latest.json(보드가 그리는 바로 그 데이터)으로 한다.

검사
  1. 종목 수(universe_n) 2,000 이상
  2. 섹터 미배정 300종목 미만 — '집계 범위' 의 '섹터 미배정 N종목' 줄
  3. 투자자별 수급 — 개인·외국인·기관계·기타법인 네 값.
     **기타법인은 D-082 이후 소스가 주지 않는다.** 세 값 + '기타법인 빠져 있습니다'
     안내가 붙은 보드는 '셋(설계상)' 으로 통과시키고 그 사실을 찍는다. 세 값인데
     안내가 없거나, 셋보다 적으면 실패다.
  4. '빠진 데이터' 에 '덜 받았습니다' · '줄었습니다' 가 없다
  (+) --asof 를 주면 보드 기준일이 그 날짜인지
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LATEST = os.path.join(ROOT, 'docs', 'api', 'latest.json')
WHO = ('개인', '외국인', '기관계', '기타법인')


def check(d, asof=None):
    """[(이름, 통과, 사유)]"""
    out = []
    n = d.get('universe_n') or 0
    out.append(('종목 수', n >= 2000, f'{n:,}'))

    notes = d.get('notices') or {}
    scope, miss = notes.get('scope') or [], notes.get('miss') or []
    um = None
    for s in scope:
        m = re.search(r'섹터 미배정 ([\d,]+)종목', s)
        if m:
            um = int(m.group(1).replace(',', ''))
    out.append(('섹터 미배정', um is not None and um < 300,
                f'{um}종목' if um is not None else "'섹터 미배정' 줄이 없다"))

    flows = (d.get('market') or {}).get('flows') or []
    have = [f['who'] for f in flows if f.get('value') is not None]
    lack = [w for w in WHO if w not in have]
    if not lack:
        out.append(('투자자 수급', True, '네 값'))
    elif lack == ['기타법인'] and any('기타법인' in s for s in scope):
        out.append(('투자자 수급', True, '세 값 — 기타법인은 소스가 주지 않는다(D-082, 안내 있음)'))
    else:
        out.append(('투자자 수급', False, f'없음: {", ".join(lack)}'))

    bad = [s for s in miss if '덜 받았습니다' in s or '줄었습니다' in s]
    out.append(("'덜 받았습니다'·'줄었습니다'", not bad, bad[0][:120] if bad else '없음'))

    if asof:
        out.append(('기준일', d.get('as_of') == asof, f'보드 {d.get("as_of")} / 기대 {asof}'))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', default=LATEST)
    ap.add_argument('--asof', default=None)
    a = ap.parse_args(argv)
    with open(a.path, encoding='utf-8') as f:
        d = json.load(f)
    res = check(d, a.asof)
    print(f'게시 전 점검 — 기준일 {d.get("as_of")} · 생성 {d.get("generated_at")}')
    for name, ok, why in res:
        print(f'  {"PASS" if ok else "FAIL"}  {name}: {why}')
    return 0 if all(ok for _, ok, _ in res) else 1


if __name__ == '__main__':
    sys.exit(main())
