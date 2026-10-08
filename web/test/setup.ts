// 시험 공통 준비 — 문서·스킨·해시를 시험마다 비운다(시험끼리 상태가 새지 않게).
import { afterEach } from 'vitest';

afterEach(() => {
  document.body.replaceChildren();
  delete document.documentElement.dataset.skin;
  if (window.location.hash) window.history.replaceState(null, '', window.location.pathname);
});
