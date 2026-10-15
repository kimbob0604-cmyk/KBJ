// KBJ 데모 화면 확인 — 로그인 → 페이지 1~13 을 열어 스크린샷과 콘솔 오류를 모은다.
// scripts/demo.sh 가 부른다(합성 데이터 데모 서버가 떠 있어야 한다). 비밀번호는 환경변수로만 받는다.
//
//   KBJ_DEMO_URL=http://127.0.0.1:8765/ KBJ_DEMO_PASSWORD=... [KBJ_DEMO_NOW=<서버 시계 ISO>] \
//     node scripts/demo_shots.mjs [--gif] <출력 폴더>
//
// --gif: README 미디어용 장면(docs/p9_design.md §4.3) — 뷰포트 1280×800·애니메이션 끔(reducedMotion)·
// 로그인 → 페이지 1·2·3·4·8·10·11·12 각 1장 + 페이지 3 GEX 패널 확대 1장을 <출력>/gif/NN_이름.png 로,
// 정지 화면 4장 이름을 <출력>/gif/stills.txt 에 적는다(GIF 는 scripts/make_demo_gif.py 가 만든다).
// 콘솔 오류·패널 오류(.msg.err) 검사는 같다 — 문제가 있으면 종료 1(미디어를 만들지 않는다).
//
// playwright 는 레포 의존성이 아니다(공개 레포 공급망 — web/package.json 에 넣지 않는다). 전역 설치본을
// 찾는다(`npm root -g`/playwright, 또는 PLAYWRIGHT_MODULE). 없으면 종료 코드 3(데모 스크립트가 건너뜀).
// 종료 코드: 0 = 화면 모두 그려지고 콘솔 오류·실패 요청 0, 1 = 문제 있음(요약에 적는다).
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';

const PAGES = [
  { n: 1, name: 'p1_market', title: '시장' },
  { n: 2, name: 'p2_board', title: '신고가 보드' },
  { n: 3, name: 'p3_diag', title: '진단·GEX' },
  { n: 4, name: 'p4_flows', title: '수급·스크리닝' },
  { n: 5, name: 'p5_filings', title: '일정·공시' },
  { n: 6, name: 'p6_macro', title: '매크로' },
  { n: 8, name: 'p8_stock', title: '종목 상세' },
  { n: 7, name: 'p7_trade', title: '수출입' },
  { n: 9, name: 'p9_themes', title: '테마·밸류체인' }, // ADR 0039 — demo_server._load_p9(합성 테마 마스터)
  { n: 10, name: 'p10_etf', title: 'ETF 수급' },
  { n: 11, name: 'p11_rules', title: '규칙 알림' },
  { n: 12, name: 'p12_journal', title: '백테스트·저널' },
  { n: 13, name: 'p13_ops', title: '운영' }, // P9 WRAP — demo_server.attach_ops(합성 1주 운영 기록)
];

function loadPlaywright() {
  const candidates = [];
  if (process.env.PLAYWRIGHT_MODULE) candidates.push(process.env.PLAYWRIGHT_MODULE);
  try {
    const root = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim();
    candidates.push(join(root, 'playwright'));
  } catch {
    // npm 이 없으면 아래 후보만
  }
  candidates.push('playwright');
  const req = createRequire(import.meta.url);
  for (const c of candidates) {
    try {
      return req(c);
    } catch {
      // 다음 후보
    }
  }
  return null;
}

// GIF 장면 순서(§4.3) — 페이지 3 은 전체 1장 + GEX 패널(프로필·레벨) 확대 1장
const GIF_PAGES = [
  { n: 1, name: 'p1_market' },
  { n: 2, name: 'p2_board' },
  { n: 3, name: 'p3_diag' },
  { n: 3, name: 'p3_gex', zoom: ['GEX 프로필', 'GEX 레벨'] },
  { n: 4, name: 'p4_flows' },
  { n: 8, name: 'p8_stock', topStock: true }, // 기본 필터 스크리너 첫 종목의 상세(빈 검색 화면 대신)
  { n: 10, name: 'p10_etf' },
  { n: 11, name: 'p11_rules' },
  { n: 12, name: 'p12_journal' },
];
// README 정지 화면 4장(GIF 장면 이름 중에서)
const GIF_STILLS = ['p1_market', 'p3_gex', 'p4_flows', 'p8_stock'];

const argv = process.argv.slice(2);
const gifMode = argv.includes('--gif');
const outDir = resolve(argv.find((a) => !a.startsWith('--')) ?? 'demo_shots');
const base = process.env.KBJ_DEMO_URL ?? 'http://127.0.0.1:8765/';
const password = process.env.KBJ_DEMO_PASSWORD ?? '';
if (!password) {
  console.error('KBJ_DEMO_PASSWORD 가 필요하다');
  process.exit(2);
}
const pw = loadPlaywright();
if (!pw) {
  console.error('playwright 를 찾지 못했다 — 전역 설치(npm i -g playwright) 또는 PLAYWRIGHT_MODULE');
  process.exit(3);
}
mkdirSync(outDir, { recursive: true });

const problems = [];
const browser = await pw.chromium.launch();
try {
  const viewport = gifMode ? { width: 1280, height: 800 } : { width: 1440, height: 900 };
  const context = await browser.newContext({ viewport, locale: 'ko-KR', timezoneId: 'Asia/Seoul', reducedMotion: gifMode ? 'reduce' : 'no-preference' });
  const page = await context.newPage();
  // 브라우저 시계를 데모 서버 시계(합성 날짜)에 맞춘다 — 띠의 '마감까지'·금통위 D-n 이 서버 응답과 같은 시각으로
  // 계산되게(맞추지 않으면 고정된 서버 시각이 지난 것으로 보여 남은 시간이 0:00 이 된다). 시계는 그 시각부터 흐른다.
  if (process.env.KBJ_DEMO_NOW) await page.clock.install({ time: new Date(process.env.KBJ_DEMO_NOW) });
  // 로그인 전 세션 확인(/api/auth/me)은 401 이 정상 응답이다 — 브라우저가 그 401 을 콘솔 오류로 남기므로
  // 로그인 전의 그 한 줄만 문제에서 뺀다(로그인 뒤의 401 은 문제다).
  // 마찬가지로 데이터가 아직 없는 자리는 API 가 404 `no_data`(안내 문구)로 답한다 — 그 404 수만큼의
  // 'status of 404' 콘솔 줄은 정상이다(패널에 안내 문구가 나온다). 그 밖의 404 는 문제다.
  let loggedIn = false;
  let noData = 0;
  const console404 = [];
  page.on('console', (msg) => {
    if (msg.type() !== 'error' && msg.type() !== 'warning') return;
    if (!loggedIn && msg.text().includes('status of 401')) return;
    if (msg.text().includes('status of 404')) {
      console404.push(msg.text());
      return;
    }
    problems.push(`콘솔 ${msg.type()}: ${msg.text()}`);
  });
  page.on('pageerror', (err) => problems.push(`페이지 오류: ${err.message}`));
  page.on('requestfailed', (req) => problems.push(`요청 실패: ${req.method()} ${new URL(req.url()).pathname} ${req.failure()?.errorText ?? ''}`));
  const apiStatus = [];
  const pending = [];
  page.on('response', (res) => {
    const path = new URL(res.url()).pathname;
    if (path.startsWith('/api/')) apiStatus.push(`${res.status()} ${path}`);
    if (res.status() >= 500) problems.push(`서버 오류: ${res.status()} ${path}`);
    if (res.status() === 404) {
      pending.push(
        res
          .json()
          .then((b) => {
            if (path.startsWith('/api/') && b && b.code === 'no_data') noData += 1;
            else problems.push(`404: ${path}`);
          })
          .catch(() => problems.push(`404(본문 없음): ${path}`)),
      );
    }
  });

  await page.goto(base, { waitUntil: 'load' });
  await page.waitForSelector('input[name=password]', { timeout: 10_000 });
  await page.fill('input[name=username]', 'demo');
  await page.fill('input[name=password]', password);
  await Promise.all([page.waitForResponse((r) => r.url().endsWith('/api/auth/login')), page.click('button[type=submit]')]);
  loggedIn = true;
  await page.waitForSelector('.clock', { timeout: 10_000 });
  await page.waitForTimeout(1500);
  const summary = [];
  if (gifMode) {
    await gifScenes(page, summary);
  } else {
    await page.screenshot({ path: join(outDir, 'p0_after_login.png') });
    await allPages(page, summary);
  }
  await Promise.all(pending);
  if (console404.length > noData) problems.push(`no_data 가 아닌 404 콘솔 줄 ${console404.length - noData}건`);
  writeFileSync(join(outDir, 'summary.json'), `${JSON.stringify({ base, summary, api: apiStatus, noData, problems }, null, 2)}\n`);
  for (const s of summary) console.log(`페이지 ${s.page} ${s.title}: 패널 ${s.panels}, 오류 ${s.errors.length}, 안내 ${s.notes.length} → ${s.file}`);
} finally {
  await browser.close();
}
if (problems.length) {
  console.error(`문제 ${problems.length}건:`);
  for (const x of problems) console.error(`  - ${x}`);
  process.exit(1);
}
console.log('콘솔 오류·실패 요청 0');

/** 보이는 페이지 안의 패널 상태(그려짐·자물쇠·오류·안내 문구) */
async function pageStats(page, n) {
  return page.evaluate((k) => {
    const root = document.getElementById(`page-${k}`) ?? document;
    const panels = [...root.querySelectorAll('.panel')];
    const err = [...root.querySelectorAll('.msg.err')].map((e) => e.textContent?.trim() ?? '');
    const msgs = [...root.querySelectorAll('.msg:not(.err)')].map((e) => e.textContent?.trim() ?? '');
    return { panels: panels.length, errors: err, notes: msgs, docWidth: document.documentElement.scrollWidth, vw: window.innerWidth };
  }, n);
}

async function gotoPage(page, n) {
  await page.evaluate((k) => {
    window.location.hash = `#/p/${k}`;
  }, n);
  await page.waitForTimeout(2000); // 처음 보일 때 mount → API 응답 → 그리기
  // 앞 장면(확대)이 내린 스크롤을 되돌린다 — 안 그러면 다음 페이지가 빈 아래쪽부터 찍힌다
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(200);
}

/** --gif: 장면마다 1장(1280×800) — 파일 이름 앞 두 자리가 GIF 순서 */
async function gifScenes(page, summary) {
  const dir = join(outDir, 'gif');
  mkdirSync(dir, { recursive: true });
  for (const [i, s] of GIF_PAGES.entries()) {
    await gotoPage(page, s.n);
    if (s.topStock) {
      const code = await page.evaluate(async () => {
        const q = 'mode=value&period=5&market=all&share_class=common&min_avg_turnover=30000000000&include_flagged=false';
        const r = await fetch(`/api/flows/screen?${q}`, { credentials: 'same-origin' });
        if (!r.ok) return null;
        const b = await r.json();
        return b?.data?.rows?.[0]?.code ?? null;
      });
      if (!code) problems.push('페이지 8: 스크리너 첫 종목을 찾지 못했다');
      else {
        await page.evaluate((c) => {
          window.location.hash = `#/p/8/${c}`;
        }, code);
        await page.waitForTimeout(2500);
        await page.evaluate(() => window.scrollTo(0, 0));
      }
    }
    const file = join(dir, `${String(i + 1).padStart(2, '0')}_${s.name}.png`);
    if (s.zoom) {
      // 확대: 두 패널(제목으로 찾음)을 화면 가운데로 → 두 패널을 감싸는 16:10 영역만 자른다(GIF 가 같은 크기로 키운다)
      const boxes = [];
      for (const title of s.zoom) {
        const el = page.locator(`#page-${s.n} .panel`, { hasText: title }).first();
        if ((await el.count()) === 0) {
          problems.push(`페이지 ${s.n}: 확대할 패널 '${title}' 이 없다`);
          continue;
        }
        await el.scrollIntoViewIfNeeded();
        boxes.push(el);
      }
      await page.waitForTimeout(500);
      const rects = [];
      for (const el of boxes) {
        const b = await el.boundingBox();
        if (b) rects.push(b);
      }
      if (rects.length === 0) {
        await page.screenshot({ path: file });
      } else {
        const vp = page.viewportSize() ?? { width: 1280, height: 800 };
        const x0 = Math.min(...rects.map((r) => r.x)) - 8;
        const y0 = Math.max(0, Math.min(...rects.map((r) => r.y)) - 8);
        const x1 = Math.max(...rects.map((r) => r.x + r.width)) + 8;
        const y1 = Math.min(vp.height, Math.max(...rects.map((r) => r.y + r.height)) + 8);
        let w = Math.min(vp.width, x1 - x0);
        let h = Math.min(vp.height - y0, y1 - y0);
        if (w / h > 1.6) h = Math.min(vp.height - y0, w / 1.6);
        else w = Math.min(vp.width, h * 1.6);
        const x = Math.max(0, Math.min(vp.width - w, x0));
        await page.screenshot({ path: file, clip: { x, y: y0, width: w, height: h } });
      }
    } else {
      await page.screenshot({ path: file });
    }
    const st = await pageStats(page, s.n);
    if (st.docWidth > st.vw + 1) problems.push(`페이지 ${s.n}: 문서가 가로로 넘친다(${st.docWidth}px > ${st.vw}px)`);
    if (st.errors.length) problems.push(`페이지 ${s.n} 패널 오류: ${st.errors.join(' | ')}`);
    summary.push({ page: s.n, title: s.name, file, ...st });
  }
  const stills = GIF_STILLS.map((name) => {
    const i = GIF_PAGES.findIndex((s) => s.name === name);
    return `${String(i + 1).padStart(2, '0')}_${name}.png`;
  });
  writeFileSync(join(dir, 'stills.txt'), `${stills.join('\n')}\n`);
}

/** 기본 모드: 모든 페이지 + 페이지 4·7·8 하위 장면 + 휴대폰 폭 */
async function allPages(page, summary) {
  for (const p of PAGES) {
    await gotoPage(page, p.n);
    const file = join(outDir, `${p.name}.png`);
    await page.screenshot({ path: file });
    const stats = await pageStats(page, p.n);
    if (stats.docWidth > stats.vw + 1) problems.push(`페이지 ${p.n}: 문서가 가로로 넘친다(${stats.docWidth}px > ${stats.vw}px)`);
    if (stats.errors.length) problems.push(`페이지 ${p.n} 패널 오류: ${stats.errors.join(' | ')}`);
    summary.push({ page: p.n, title: p.title, file, ...stats });
    if (p.n === 8) {
      // 일봉만 있는 종목(데모 시드 990001 — 일지 픽스처 종목: 재무·컨센서스·공시·수급이 없다)도 탭마다 패널 오류
      // (.msg.err) 없이 안내(.msg — 404 no_data·빈 데이터)로 그려야 한다. 탭을 차례로 연다
      await page.evaluate(() => {
        window.location.hash = '#/p/8/990001';
      });
      await page.waitForTimeout(2000);
      const tabNames = await page.locator('#page-8 [role="tab"]').allTextContents();
      for (const [i, name] of tabNames.entries()) {
        await page.locator('#page-8 [role="tab"]').nth(i).click();
        await page.waitForTimeout(1500);
        const file = join(outDir, `p8_daily_only_tab${i}.png`);
        await page.screenshot({ path: file }); // 보이는 화면만 — 세로 전체 캡처는 가로 페이저를 1쪽으로 되돌린다
        const st = await page.evaluate(() => ({
          errors: [...document.querySelectorAll('#page-8 .msg.err')].map((e) => e.textContent?.trim() ?? ''),
          notes: [...document.querySelectorAll('#page-8 .msg:not(.err):not([hidden])')].map((e) => e.textContent?.trim() ?? ''),
        }));
        if (st.errors.length) problems.push(`페이지 8 일봉만 있는 종목(990001) ${name} 탭 패널 오류: ${st.errors.join(' | ')}`);
        summary.push({ page: 8, title: `종목 상세 990001(일봉만) ${name} 탭`, file, panels: null, ...st });
      }
      await page.locator('#page-8 [role="tab"]').first().click();
    }
    if (p.n === 7) {
      // 페이지 7 수출입 — 탭 6개를 차례로 열어 탭마다 스크린샷·패널 오류·가로 넘침을 본다(첫 탭은 위에서 찍었다)
      const tabNames = await page.locator('#page-7 [role="tab"]').allTextContents();
      for (const [i, name] of tabNames.entries()) {
        if (i === 0) continue;
        await page.locator('#page-7 [role="tab"]').nth(i).click();
        await page.waitForTimeout(1500);
        const file = join(outDir, `p7_tab${i}.png`);
        await page.screenshot({ path: file }); // 보이는 화면만 — 세로 전체 캡처는 가로 페이저를 1쪽으로 되돌린다
        const st = await page.evaluate(() => ({
          errors: [...document.querySelectorAll('#page-7 .msg.err')].map((e) => e.textContent?.trim() ?? ''),
          docWidth: document.documentElement.scrollWidth,
          vw: window.innerWidth,
        }));
        if (st.docWidth > st.vw + 1) problems.push(`페이지 7 ${name} 탭: 문서가 가로로 넘친다(${st.docWidth}px > ${st.vw}px)`);
        if (st.errors.length) problems.push(`페이지 7 ${name} 탭 패널 오류: ${st.errors.join(' | ')}`);
        console.log(`페이지 7 ${name} 탭: 오류 ${st.errors.length} → ${file}`);
      }
    }
    if (p.n === 4) {
      // 기본 필터(일평균 300억·5일·보통주) 그대로 첫 종목의 상세까지 연다 — 기본 화면이 비면 문제
      const row = page.locator('#page-4 .panel tbody tr[aria-selected], #page-4 .panel tbody tr.row').first();
      const anyRow = (await row.count()) > 0 ? row : page.locator('#page-4 .panel tbody tr:not(:has(td[colspan]))').first();
      if ((await anyRow.count()) > 0) {
        await anyRow.click();
        await page.waitForTimeout(1500);
      } else problems.push('페이지 4: 기본 필터 스크리너가 비었다');
      const detail = join(outDir, 'p4_flows_detail.png');
      await page.screenshot({ path: detail, fullPage: true }); // 세로 전체(가로 넘침은 위에서 따로 검사)
      const det = await page.evaluate(() => ({
        rows: document.querySelectorAll('#page-4 .panel tbody tr').length,
        svg: document.querySelectorAll('#page-4 .panel svg').length,
        errors: [...document.querySelectorAll('#page-4 .msg.err')].map((e) => e.textContent?.trim() ?? ''),
      }));
      if (det.errors.length) problems.push(`페이지 4 상세 오류: ${det.errors.join(' | ')}`);
      summary.push({ page: 4, title: '수급·스크리닝(기본 필터 + 종목 상세)', file: detail, panels: det.rows, errors: det.errors, notes: [`svg ${det.svg}`] });
    }
  }
  // 휴대폰 폭(390px)에서 페이지 1 — 가로 넘침 없음(문서 폭 = 화면 폭)
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => {
    window.location.hash = '#/p/1';
  });
  await page.waitForTimeout(1500);
  const mobile = join(outDir, 'p1_market_mobile.png');
  await page.screenshot({ path: mobile });
  const mw = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, vw: window.innerWidth }));
  if (mw.doc > mw.vw + 1) problems.push(`휴대폰 폭: 문서가 가로로 넘친다(${mw.doc}px > ${mw.vw}px)`);
  summary.push({ page: 1, title: '시장(휴대폰 폭 390px)', file: mobile, panels: null, errors: [], notes: [`문서 폭 ${mw.doc}px`] });
}
