// 화면 문구 정리 — 서버·위젯 문구에 섞인 개발용 표시(작업 이름·실측 번호·설정 파일·단계)를 사용자 문장으로 바꾼다.
// 사용자 요청(2026-10-20): "준비 중·확인 전·#40 같은 게 너무 많아 미완성 티가 난다". 사유 원문은 운영 페이지(13)·로그에 남는다.

/** 개발 단계 문구가 든 메시지는 통째로 이 한 줄로 */
export const NOT_PROVIDED = '아직 제공되지 않는 항목입니다';

// 꼬리표를 지운 뒤에도 남는 개발 단계 문구
const STAGE = /확인 전|실측|준비 중|결정 필요|모르는 (?:탭|시리즈)/;
// 등록부(config/jobs.yaml)에서 꺼 둔 작업 — 이 이름이 든 '아직 없음'은 기다려도 채워지지 않는다. 켜면 여기서 뺀다
const OFF_JOBS = /\bthemes\.monitor\b/;

// [확인 필요 — 현금 원장 없음] 은 안의 설명만 남기고, [확인 필요 #39] 처럼 번호뿐이면 지운다
const TAG = /\s*\[(?:확인 필요|실측 필요|추정|제안|결정 필요)\s*(?:—|-|:)?\s*([^\]]*)\]/g;

const STRIP: readonly RegExp[] = [
  /\s*\((?:확인 전|실측)[^)]*\)/g, // (확인 전 #43~#46)
  /\s*확인 전\s*(?=#|\))/g,
  /\s*#\d+(?:\s*[~·-]\s*#?\d+)*/g, // #6·#17·#55
  /`?\b(?:config|docs|kbj)\/[\w./-]+`?/g, // config/macro.yaml
  /\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\b/g, // market.close_collect·macro.morning
];

/** 메시지·주석 한 줄 정리. 개발 단계 문구면 NOT_PROVIDED, 지우고 남는 게 없으면 '' */
export function tidy(text: string): string {
  if (OFF_JOBS.test(text)) return NOT_PROVIDED;
  let t = text.replace(TAG, (_m, inner: string) => {
    const keep = inner.replace(/#[\w-]+(?:\s*[~·]\s*#?[\w-]+)*/g, '').replace(/^[\s—·:-]+|[\s—·:-]+$/g, '');
    return keep ? ` (${keep})` : '';
  });
  for (const re of STRIP) t = t.replace(re, '');
  if (STAGE.test(t)) return NOT_PROVIDED;
  return t
    .replace(/\(\s*\)/g, '')
    .replace(/\(\s+/g, '(')
    .replace(/\s*·\s*(?=·|$|\))/g, '')
    .replace(/(—|-)\s*·\s*/g, '$1 ')
    .replace(/\s+([,.)])/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .replace(/\s*[—·-]\s*$/, '')
    .trim();
}

/** 주석 줄 목록 정리 — 개발 단계 문구 줄과 빈 줄은 뺀다 */
export function tidyNotes(notes: readonly string[]): string[] {
  const out: string[] = [];
  for (const n of notes) {
    const t = tidy(n);
    if (t && t !== NOT_PROVIDED && !out.includes(t)) out.push(t);
  }
  return out;
}

const MSG = 'p.msg, span.msg';
const NOTE = 'p.def';

function fix(el: Element): void {
  if (el.children.length > 0) return; // 글자만 든 줄만(링크·강조가 든 줄은 위젯이 정한 대로)
  const t = el.textContent;
  const n = el.matches(NOTE) ? tidyNotes(t.split(' · ')).join(' · ') : tidy(t);
  if (n === t) return;
  if (n) el.textContent = n;
  else el.remove();
}

function scan(node: Node): void {
  if (!(node instanceof Element)) return;
  if (node.matches(`${MSG}, ${NOTE}`)) fix(node);
  for (const el of node.querySelectorAll(`${MSG}, ${NOTE}`)) fix(el);
}

/** 로그인 화면 전체의 안내 줄(.msg)·주석 줄(.def)을 그려지는 대로 정리한다. 돌려주는 함수로 멈춘다 */
export function tidyDom(root: HTMLElement): () => void {
  scan(root);
  const mo = new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'characterData') {
        const p = r.target.parentElement;
        if (p) scan(p);
      }
      for (const n of r.addedNodes) scan(n);
    }
  });
  mo.observe(root, { childList: true, subtree: true, characterData: true });
  return () => mo.disconnect();
}
