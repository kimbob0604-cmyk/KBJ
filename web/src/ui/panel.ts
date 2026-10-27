// 패널 — 페이지 격자의 칸 하나(미리보기 .panel). 등급 자물쇠·원천 줄·오류 격리를 여기서 한 번에 처리한다.
//
//   const p = panel(ctx.tier, { title: '시장 거래대금', tier: 'login', lock: 'krx_daily', span: 4 });
//   grid.append(p.el);
//   if (p.locked) return;            // 공개 빌드: 자물쇠만 그렸다. 데이터를 요청하지 않는다
//   try { const env = await ctx.source.get(...); p.setSource(env); p.body.append(...) } catch (e) { p.showError(e) }
//
// 오류는 이 패널 안에만 표시한다(다른 패널은 계속 — 절대 규칙 4). 문구에 응답 본문을 싣지 않는다.
import { AuthRequiredError, DataError, type Envelope, LockedError, type Tier } from '../data/source';
import { clear, h, uid } from './dom';
import { lockBox, type LockText } from './lock';
import { notesLine, sourceText } from './quality';

export type Span = 3 | 4 | 6 | 8 | 9 | 12;

export interface PanelOptions {
  title: string;
  /** 이 위젯의 등급. 'login' 이고 보는 쪽이 공개면 자물쇠 */
  tier: Tier;
  span?: Span;
  /** 원천 줄 기본 문구(데이터를 받기 전) */
  src?: string;
  /** 자물쇠 사유(LOCK_REASONS 키 또는 문구) */
  lock?: LockText;
  /** 정의 줄(.def) */
  def?: string;
  /** 아직 만들지 않은 위젯: '준비 중(P5)' 상자만 그린다 */
  pending?: string;
  /** 준비 중 상자에 덧붙일 안내(그 단계에서 들어올 것) */
  pendingNote?: string;
  id?: string;
}

export interface Panel {
  readonly el: HTMLElement;
  readonly head: HTMLHeadingElement;
  /** 위젯이 내용을 넣는 곳 */
  readonly body: HTMLDivElement;
  readonly locked: boolean;
  readonly pending: boolean;
  /** 원천·시각·품질 줄 + notes 줄 + stale 머리색 */
  setSource(env: Pick<Envelope<unknown>, 'source' | 'as_of' | 'quality' | 'notes'>, invalidRows?: number): void;
  setSrcText(text: string): void;
  setDef(text: string | null): void;
  /** 오류 한 줄(본문을 비우고) */
  showError(err: unknown): void;
  /** 빈 데이터 한 줄 */
  showMessage(text: string): void;
  clearBody(): void;
}

/** 오류 → 화면 문구. 응답 본문·스택은 싣지 않는다. */
export function errorText(err: unknown): string {
  if (err instanceof AuthRequiredError) return '로그인이 필요합니다';
  if (err instanceof LockedError) return '로그인 등급 데이터';
  if (err instanceof DataError) return err.message;
  return '불러오지 못함';
}

/**
 * 실패 한 줄 + 예정 작업 안내. 404 `no_data` 는 서버 문구가 이미 '아직 없음 — <작업 시각>'이라 그대로 쓴다 — 힌트를 또
 * 붙이면 같은 안내가 두 번 나왔다('아직 없음 — macro.morning 06:20 … — macro.morning 06:20 …', 페이지 6·3·7). 서버 문구가
 * 없는 실패(공개 파일 404·500·형식 오류)만 '오류 — 힌트'.
 */
export function failText(err: unknown, hint: string): string {
  if (err instanceof DataError && err.status === 404 && err.serverMessage) return err.serverMessage;
  return `${errorText(err)} — ${hint}`;
}

/** 404(아직 수집 전 — 데이터·입력값 없음)인가 — 안내(.msg)로 그리고, 그 밖만 오류(.msg.err) */
export function isNoDataError(err: unknown): boolean {
  return err instanceof DataError && err.status === 404;
}

export function panel(viewer: Tier, opts: PanelOptions): Panel {
  const locked = viewer === 'public' && opts.tier === 'login';
  const pending = !locked && opts.pending !== undefined;
  const titleId = uid('panel-title');
  const src = h('span', { class: 'src' }, opts.pending ? `준비 중(${opts.pending})` : (opts.src ?? ''));
  const head = h('h3', { attrs: { id: titleId } }, h('span', null, opts.title), src);
  const body = h('div', { class: 'body' });
  const notes = h('div', { class: 'notes' });
  let def: HTMLParagraphElement | null = opts.def ? h('p', { class: 'def' }, opts.def) : null;

  const el = h('section', {
    class: `panel span-${opts.span ?? 12}`,
    attrs: { 'aria-labelledby': titleId, id: opts.id },
    data: { tier: opts.tier },
  });
  el.append(head);
  if (locked) {
    el.classList.add('locked');
    el.append(lockBox(opts.lock ?? 'quote_based'));
    if (opts.pending) src.textContent = `준비 중(${opts.pending}) · 로그인`;
  } else if (pending) {
    el.classList.add('is-pending');
    // 로그인 화면은 원천이 아직 없는 자리를 그리지 않는다(미완성처럼 보인다 — 사용자 요청 2026-10-20). 공개 데모는 단계 안내를 남긴다
    if (viewer === 'login') el.hidden = true;
    el.append(
      h(
        'div',
        { class: 'pending-box' },
        h(
          'div',
          null,
          h('b', null, '준비 중'),
          `${opts.pending} 단계에서 채웁니다`,
          opts.pendingNote ? h('div', { class: 'dim' }, opts.pendingNote) : null,
        ),
      ),
    );
  } else {
    el.append(body, notes);
    if (def) el.append(def);
  }

  const live = !locked && !pending;
  return {
    el,
    head,
    body,
    locked,
    pending,
    setSource(env, invalidRows = 0) {
      if (!live) return;
      src.textContent = sourceText(env);
      el.classList.toggle('stale', env.quality === 'stale');
      clear(notes);
      const line = notesLine(env.notes, invalidRows);
      if (line) notes.append(line);
    },
    setSrcText(text) {
      src.textContent = text;
    },
    setDef(text) {
      if (!live) return;
      if (def) def.remove();
      def = text ? h('p', { class: 'def' }, text) : null;
      if (def) el.append(def);
    },
    showError(err) {
      if (!live) return;
      clear(body);
      body.append(h('p', { class: 'msg err', attrs: { role: 'status' } }, errorText(err)));
      if (!(err instanceof AuthRequiredError) && !(err instanceof DataError) && !(err instanceof LockedError)) {
        // 예상 밖 오류는 감추지 않는다 — 화면은 짧게, 콘솔에는 이름과 메시지(본문·값 없음)
        console.error('[kbj] 패널 오류', opts.title, err);
      }
    },
    showMessage(text) {
      if (!live) return;
      clear(body);
      body.append(h('p', { class: 'msg' }, text));
    },
    clearBody() {
      clear(body);
    },
  };
}
