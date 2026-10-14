// 페이지 13 '운영' 최소판(묶음 OPS — docs/p9_design.md §6.2, §11 Q5). 합성 픽스처(tests/unit/api/web_fixtures_p13/ops.py
// 가 만든 test/fixtures/api/ops_*.json). 보기 전용 표 셋: 서비스 상태(전체 폭)·오늘 작업·발송 기록(반폭). 404 는 '.msg',
// 그 밖 실패만 '.msg.err'. 공개판은 자물쇠만(요청 0). 1분마다 다시 읽는다.
import { describe, expect, it } from 'vitest';
import { createApiSource } from '../../src/data/api';
import type { DataSource } from '../../src/data/source';
import p13, { P13_PANELS, REFRESH_MS } from '../../src/pages/p13_ops';
import { ageText, fillJobs, fillNotify, fillStatus, LEVEL_LABEL } from '../../src/pages/p13/ops';
import { panel } from '../../src/ui/panel';
import { type FakeCall, fakeFetch, json } from '../helpers';
import { flush, pageCtx, panelByTitle, text } from './support';

const FX = import.meta.glob<unknown>('../fixtures/api/ops_*.json', { eager: true, import: 'default' });

function fx(name: string): unknown {
  const v = FX[`../fixtures/api/${name}.json`];
  if (v === undefined) throw new Error(`운영 픽스처 없음: ${name}`);
  return structuredClone(v);
}

type Over = Record<string, (() => Response) | undefined>;

function harness(over: Over = {}): { source: DataSource; calls: FakeCall[]; paths: () => string[] } {
  const routes: Record<string, string> = { 'ops/status': 'ops_status', 'ops/jobs': 'ops_jobs', 'ops/notify': 'ops_notify' };
  const { fetch, calls } = fakeFetch((url) => {
    const path = (url.replace(/^\/api\//, '').split('?')[0] ?? '');
    const o = over[path];
    if (o) return o();
    const name = routes[path];
    return name ? json(fx(name)) : json(fx('ops_status_none'), 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

function mkPanel(title: string) {
  const p = panel('login', { title, tier: 'login', lock: 'personal', span: 6 });
  document.body.append(p.el);
  return p;
}

const NOW = () => new Date('2026-10-16T12:00:00Z');

describe('페이지 13 — 배치', () => {
  it('패널 순서·칸 폭 — 서비스 상태만 전체 폭, 표 둘은 반폭', async () => {
    const hz = harness();
    const { ctx, root } = pageCtx(13, 'login', hz.source, { now: NOW });
    await p13.mount(ctx);
    await flush();
    const panels = [...root.querySelectorAll('section.panel')];
    expect(panels.map((p) => p.querySelector('h3 > span')?.textContent)).toEqual([
      P13_PANELS.status.title,
      P13_PANELS.jobs.title,
      P13_PANELS.notify.title,
    ]);
    expect(panels[0]?.className).toContain('span-12');
    for (const p of panels.slice(1)) expect(p.className).toContain('span-6');
    expect(new Set(hz.paths())).toEqual(new Set(['ops/status', 'ops/jobs', 'ops/notify']));
    expect(text(root)).not.toMatch(/NaN|undefined/);
  });

  it('공개판은 자물쇠만 — 요청 0', async () => {
    const hz = harness();
    const { ctx, root, every } = pageCtx(13, 'public', hz.source, { now: NOW });
    await p13.mount(ctx);
    await flush();
    expect(hz.calls).toHaveLength(0);
    expect(every).toHaveLength(0);
    expect(root.querySelectorAll('.lock, [data-locked], .locked').length + root.querySelectorAll('section.panel').length).toBeGreaterThan(0);
    expect(text(root)).toContain('개인 기능 — 로그인');
  });

  it('1분마다 다시 읽는다', async () => {
    const hz = harness();
    const { ctx, every } = pageCtx(13, 'login', hz.source, { now: NOW });
    await p13.mount(ctx);
    await flush();
    expect(every.map((e) => e.ms)).toEqual([REFRESH_MS]);
    const before = hz.calls.length;
    every[0]?.fn();
    await flush();
    expect(hz.calls.length).toBe(before + 3);
  });
});

describe('페이지 13 — 표 셋', () => {
  it('① 서비스 상태: 하트비트 나이·기준·상태, 점검 항목 줄', async () => {
    const p = mkPanel('서비스 상태');
    const { ctx } = pageCtx(13, 'login', harness().source, { now: NOW });
    await fillStatus(ctx, p);
    const body = text(p.el);
    expect(body).toContain('점검 주의'); // WARN
    expect(body).toContain('notifier');
    expect(body).toContain('75초');
    expect(body).toContain('하트비트 끊김 >10분(기록)');
    const warn = [...p.el.querySelectorAll('td.warn')].map((td) => td.textContent);
    expect(warn).toContain(LEVEL_LABEL.WARN);
    expect(p.el.querySelector('.msg.err')).toBeNull();
  });

  it('① FAIL 픽스처 — 실패 칸은 down 색', async () => {
    const p = mkPanel('서비스 상태');
    const hz = harness({ 'ops/status': () => json(fx('ops_status_fail')) });
    const { ctx } = pageCtx(13, 'login', hz.source, { now: NOW });
    await fillStatus(ctx, p);
    expect(text(p.el)).toContain('점검 실패');
    expect([...p.el.querySelectorAll('td.down')].map((td) => td.textContent)).toContain(LEVEL_LABEL.FAIL);
  });

  it('② 오늘 작업: 마지막 시도·재시도 표시', async () => {
    const p = mkPanel('오늘 작업');
    const { ctx } = pageCtx(13, 'login', harness().source, { now: NOW });
    await fillJobs(ctx, p);
    const body = text(p.el);
    expect(body).toContain('board.daily');
    expect(body).toContain('성공 (재시도)');
    expect(body).toContain('재시도 성공 1');
  });

  it('② 빈 날은 표 안내', async () => {
    const p = mkPanel('오늘 작업');
    const hz = harness({ 'ops/jobs': () => json(fx('ops_jobs_empty')) });
    const { ctx } = pageCtx(13, 'login', hz.source, { now: NOW });
    await fillJobs(ctx, p);
    expect(text(p.el)).toContain('이날 실행 기록 없음');
  });

  it('③ 발송 기록: 종류·상태·가린 사유, 본문 없음', async () => {
    const p = mkPanel('발송 기록');
    const { ctx } = pageCtx(13, 'login', harness().source, { now: NOW });
    await fillNotify(ctx, p);
    const body = text(p.el);
    expect(body).toContain('brief.closing');
    expect(body).toContain('실패 · telegram 429');
    expect(p.el.querySelectorAll('td.down').length).toBeGreaterThan(0);
  });

  it('404 는 .msg(오류 아님), 그 밖 실패만 .msg.err — 한 패널 실패가 다른 패널을 막지 않는다', async () => {
    const hz = harness({
      'ops/status': () => json(fx('ops_status_none'), 404),
      'ops/jobs': () => json({ code: 'internal' }, 500),
    });
    const { ctx, root } = pageCtx(13, 'login', hz.source, { now: NOW });
    await p13.mount(ctx);
    await flush();
    const st = panelByTitle(root, P13_PANELS.status.title);
    expect(st.querySelector('.msg')).not.toBeNull();
    expect(st.querySelector('.msg.err')).toBeNull();
    expect(text(st)).toContain('운영 기록 없음');
    expect(panelByTitle(root, P13_PANELS.jobs.title).querySelector('.msg.err')).not.toBeNull();
    expect(text(panelByTitle(root, P13_PANELS.notify.title))).toContain('brief.morning');
  });

  it('하트비트 없음 표기', () => {
    expect(ageText({ name: 'x', heartbeat_s: 60, age_s: null, level: 'FAIL' })).toBe('없음');
  });
});
