// 주기 실행기 — 탭이 안 보이면 멈춘다(§6.9 "탭이 안 보이면(document.hidden) 멈춤").
import type { Cleanup } from './types';

export interface Scheduler {
  every(ms: number, fn: () => void, opts?: { immediate?: boolean }): Cleanup;
  /** 모두 멈춘다 */
  stop(): void;
}

export function createScheduler(win: Window = window, doc: Document = document): Scheduler {
  const jobs = new Set<{ id: number; fn: () => void }>();
  let stopped = false;

  function run(fn: () => void): void {
    try {
      fn();
    } catch (err) {
      // 한 작업의 실패가 다른 작업을 멈추지 않게(절대 규칙 4) — 감추지 않고 콘솔에 남긴다
      console.error('[kbj] 주기 작업 오류', err);
    }
  }

  const onVisible = (): void => {
    if (!doc.hidden) for (const j of jobs) run(j.fn);
  };
  doc.addEventListener('visibilitychange', onVisible);

  return {
    every(ms, fn, opts) {
      // 앱이 내려간 뒤(stop) 늦게 끝난 mount 가 거는 주기 작업은 받지 않는다 — 타이머가 새지 않게
      if (stopped) return () => undefined;
      const job = {
        id: win.setInterval(() => {
          if (!doc.hidden) run(fn);
        }, ms),
        fn,
      };
      jobs.add(job);
      if (opts?.immediate) run(fn);
      return () => {
        win.clearInterval(job.id);
        jobs.delete(job);
      };
    },
    stop() {
      stopped = true;
      for (const j of jobs) win.clearInterval(j.id);
      jobs.clear();
      doc.removeEventListener('visibilitychange', onVisible);
    },
  };
}

// ── 장중 슬롯 맞춤 새로 고침(2026-10-27 실시간 점검 — docs/realtime_audit.md) ──
// 장중 수집은 10분 슬롯(flows.intraday·market.intraday·rules.intraday — 슬롯 시작에 돌아 1~2분 뒤 쓴다).
// 마운트 시각부터 630초마다 다시 읽으면 슬롯과 어긋나 새 값이 최대 10분 넘게 늦게 보였다. 벽시계 5분
// 주기(슬롯 시작 + 2분 30초·+ 7분 30초 — 늦게 끝난 슬롯도 한 번 더 잡는다)에 맞춰 다시 읽는다.
/** 슬롯 맞춤 주기(밀리초) */
export const SLOT_PERIOD_MS = 300_000;
/** 슬롯 시작 뒤 여유(수집이 끝나 쓰기까지) */
export const SLOT_OFFSET_MS = 150_000;
/** 경계를 넘었는지 보는 간격 — 주기 실행기(탭이 숨으면 멈춤, 다시 보이면 바로 한 번)에 건다 */
export const SLOT_TICK_MS = 30_000;

/**
 * 벽시계 경계(`offsetMs` 를 뺀 시각을 `periodMs` 로 나눈 몫)가 바뀔 때만 `fn` 을 부르는 함수. 처음 만든 때의 구간은
 * 건너뛴다(마운트가 방금 읽었다). 탭이 숨은 사이 경계를 넘었으면 다시 보일 때 한 번 부른다.
 */
export function onSlot(
  now: () => Date,
  fn: () => void,
  periodMs: number = SLOT_PERIOD_MS,
  offsetMs: number = SLOT_OFFSET_MS,
): () => void {
  const key = (): number => Math.floor((now().getTime() - offsetMs) / periodMs);
  let last = key();
  return () => {
    const k = key();
    if (k === last) return;
    last = k;
    fn();
  };
}
