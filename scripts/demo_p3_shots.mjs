// P3 데모 화면 확인 — 로그인 → 페이지 1·2·4·10 을 열어 스크린샷과 콘솔 오류를 모은다.
// scripts/demo_p3.sh 가 부른다(합성 데이터 데모 서버가 떠 있어야 한다). 비밀번호는 환경변수로만 받는다.
//
//   KBJ_DEMO_URL=http://127.0.0.1:8765/ KBJ_DEMO_PASSWORD=... [KBJ_DEMO_NOW=<서버 시계 ISO>] \
//     node scripts/demo_p3_shots.mjs <출력 폴더>
//
// playwright 는 레포 의존성이 아니다(공개 레포 공급망 — web/package.json 에 넣지 않는다). 전역 설치본을
// 찾는다(`npm root -g`/playwright, 또는 PLAYWRIGHT_MODULE). 없으면 종료 코드 3(데모 스크립트가 건너뜀).
// 종료 코드: 0 = 화면 4개 모두 그려지고 콘솔 오류·실패 요청 0, 1 = 문제 있음(요약에 적는다).
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';

const PAGES = [
  { n: 1, name: 'p1_market', title: '시장' },
  { n: 2, name: 'p2_board', title: '신고가 보드' },
  { n: 4, name: 'p4_flows', title: '수급·스크리닝' },
  { n: 10, name: 'p10_etf', title: 'ETF 수급' },
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

const outDir = resolve(process.argv[2] ?? 'p3shots');
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
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });
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
  await page.screenshot({ path: join(outDir, 'p0_after_login.png') });

  const summary = [];
  for (const p of PAGES) {
    await page.evaluate((n) => {
      window.location.hash = `#/p/${n}`;
    }, p.n);
    await page.waitForTimeout(2000); // 처음 보일 때 mount → API 응답 → 그리기
    const file = join(outDir, `${p.name}.png`);
    await page.screenshot({ path: file });
    // 보이는 페이지 안의 패널 상태(그려짐·자물쇠·오류·안내 문구)
    const stats = await page.evaluate((n) => {
      const root = document.getElementById(`page-${n}`) ?? document;
      const panels = [...root.querySelectorAll('.panel')];
      const err = [...root.querySelectorAll('.msg.err')].map((e) => e.textContent?.trim() ?? '');
      const msgs = [...root.querySelectorAll('.msg:not(.err)')].map((e) => e.textContent?.trim() ?? '');
      return { panels: panels.length, errors: err, notes: msgs, docWidth: document.documentElement.scrollWidth, vw: window.innerWidth };
    }, p.n);
    if (stats.docWidth > stats.vw + 1) problems.push(`페이지 ${p.n}: 문서가 가로로 넘친다(${stats.docWidth}px > ${stats.vw}px)`);
    if (stats.errors.length) problems.push(`페이지 ${p.n} 패널 오류: ${stats.errors.join(' | ')}`);
    summary.push({ page: p.n, title: p.title, file, ...stats });
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
