#!/usr/bin/env python3
"""
실행 요약 — GitHub Actions 런 페이지에 올릴 마크다운을 만든다.

Actions 로그를 뒤지지 않고도 그날 무엇이 나왔는지 알 수 있어야 한다.
state/ 의 최신 산출을 읽어 표로 찍는다. 산출이 없으면 그 사실을 적는다.

  python3 -m board.tools.summary >> "$GITHUB_STEP_SUMMARY"

수치는 state JSON 이 이미 계산해 둔 것만 옮긴다. 여기서 다시 세지 않는다.
"""
import glob
import json
import os
import sys

from ..engine.config import ROOT


def _latest():
    ds = sorted(glob.glob(os.path.join(ROOT, 'state', '*')))
    ds = [d for d in ds if os.path.basename(d).isdigit()]
    return ds[-1] if ds else None


def _load(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def render(mode='daily', check_log=None):
    L = [f'## 신고가 보드 — `{mode}`', '']

    if check_log and os.path.exists(check_log):
        with open(check_log, encoding='utf-8') as f:
            L += ['```', f.read().rstrip(), '```', '']

    d = _latest()
    if not d:
        L += ['_단계별 산출이 없습니다. `init` 부터 돌려야 합니다._']
        return '\n'.join(L)

    nh = _load(d, 'newhigh.json')
    rk = _load(d, 'rankings.json')
    uni = _load(d, 'universe.json')

    if nh:
        lab = nh.get('labels') or {}
        n = (uni or {}).get('n') or 0
        L += [f'**기준일 {nh.get("as_of")}** · {nh.get("basis")} 기준 · '
              f'전 종목 {n:,} · 출처 `{nh.get("source")}`', '']
        L += ['| 라벨 | 달성 종목수 |', '|---|---:|']
        for k, v in (nh.get('counts') or {}).items():
            L.append(f'| {lab.get(k, k)} | {v} |')
        L += ['', f'근접 {len(nh.get("proximity") or [])}종목 · '
                  f'수정주가 의심 {nh.get("n_suspect", 0)}종목 · '
                  f'역사적 판정 보류 {nh.get("n_hist_not_evaluated", 0)}종목', '']

    if rk:
        L += [f'**랭킹** — 분류 `{rk.get("taxonomy")}` (목표 `{rk.get("taxonomy_target")}`) · '
              f'교집합 {len(rk.get("cross_codes") or [])}종목', '']
        for b in rk.get('sector_boards') or []:
            L += [f'<details><summary>{b["label"]} — 상위 5섹터</summary>', '',
                  '| # | 섹터 | 등락률 | 1등 |', '|--:|---|--:|---|']
            for x in (b.get('sectors') or [])[:5]:
                top = x.get('top') or []
                L.append(f'| {x["rank"]} | {x["name"]} | {x.get("ret") or "–"} | '
                         f'{top[0]["name"] if top else "–"} |')
            L += ['', '</details>', '']
        for b in rk.get('stock_boards') or []:
            rows = [r for r in (b.get('rows') or []) if r.get('cross')][:10]
            if not rows:
                continue
            key = b.get('sort_by')
            L += [f'<details><summary>{b["title"]} — 교집합 {len(rows)}종목</summary>', '',
                  '| # | 종목 | 값 |', '|--:|---|--:|']
            for r in rows:
                L.append(f'| {r["rank"]} | {r["name"]} | '
                         f'{(r.get("cells") or {}).get(key) or "–"} |')
            L += ['', '</details>', '']
            break

    miss = (rk or {}).get('missing') or (nh or {}).get('missing') or []
    if miss:
        L += ['**빠진 데이터**', '']
        L += [f'- {m}' for m in miss]
        L.append('')

    L += ['', '대시보드는 GitHub Pages 주소에서 바로 볼 수 있습니다 '
          '(Settings → Pages 를 켜 두었을 때). 산출물 파일은 이 페이지 아래 '
          '**Artifacts** 에서 내려받으세요.']
    return '\n'.join(L)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    mode = argv[0] if argv else 'daily'
    check = argv[1] if len(argv) > 1 else None
    print(render(mode, check))
    return 0


if __name__ == '__main__':
    sys.exit(main())
