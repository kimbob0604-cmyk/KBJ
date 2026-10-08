// 빈 자리 페이지 — 패널 1개: 제목·'준비 중(Pn)'·등급(§6.5). 공개판에서 로그인 등급 페이지는 자물쇠.
// P3 페이지(1·2·4·10)도 위젯 모듈(W2)이 아직 없으면 이 자리로 그린다.
import type { PageContext } from '../app/types';
import { panel } from '../ui/panel';

export function mountPlaceholder(ctx: PageContext): void {
  const def = ctx.page;
  const p = panel(ctx.tier, {
    title: `${def.n} ${def.title}`,
    tier: def.tier === 'login' ? 'login' : 'public',
    lock: def.lock ?? 'quote_based',
    pending: def.phase,
    pendingNote: def.phase === 'P3' ? `${def.plan} — 위젯 연결 전` : def.plan,
    span: 12,
  });
  ctx.root.append(p.el);
}
