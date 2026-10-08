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
