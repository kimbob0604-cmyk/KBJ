// 페이지 8 머리(늘 보임) — 종목명·코드·시장·종류·상태 배지·결산월·상장일, 종가·등락·시총, 신고가 3축(docs/p4_design.md §6.2).
//
// - 로그인: 'stock/<코드>/header'. 신고가 배지는 board 산출 그대로(ADR 0017·D-P4-23 — 120일 = 120 KRX 거래일,
//   52주 = 달력 364일, 역사적 = 상장 이후). 창을 못 채운 축은 '—'. 60일·252일 표기는 쓰지 않는다.
// - 거래정지: 상태 배지 + 시세 칸 stale('정지 — 마지막 거래 YYYY-MM-DD').
// - 공개: 이름·코드·시장·결산월은 DART(corp_index·fin/<코드>.json), 시세 자리는 TradingView 위젯, 신고가는 자물쇠.
// - 관심종목 별 토글(P8 묶음 JNL — docs/p8_design.md §6): 로그인 머리에서 데이터 소스에 쓰기 채널(`put`·`del` —
//   로그인 CSRF)이 있을 때만 단추를 그린다(없으면 '관심' 배지만 — 요청 없음). 목록은 main 하나.
import { DataError, type Envelope } from '../../data/source';
import { append, h } from '../../ui/dom';
import { DASH, joEok, kstDate, pct, signClass } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { failed } from '../p1_market';
import { type CorpRow, HINT, marketLabel, type PubFin, type S, tag, type TabCtx, won } from './common';

type Header = S['StockHeader'];

export const KIND_TAG: Readonly<Record<string, string>> = {
  pref: '우선주',
  spac: '스팩',
  reit: '리츠',
  etf: 'ETF',
  etn: 'ETN',
};

export const STATUS_LABEL: Readonly<Record<string, string>> = {
  halted: '거래정지',
  managed: '관리종목',
  liquidation: '정리매매',
};

/** 신고가 한 축: true '신고가'·false '아님'·null '—'(창을 못 채움) */
export function newHighText(v: boolean | null | undefined): string {
  return v === true ? '신고가' : v === false ? '아님' : DASH;
}

function badges(d: Header): HTMLElement[] {
  const out: HTMLElement[] = [];
  if (d.market) out.push(tag(marketLabel(d.market)));
  if (d.kind && KIND_TAG[d.kind]) out.push(tag(KIND_TAG[d.kind] ?? d.kind, 'warn'));
  for (const f of d.status_flags ?? []) out.push(tag(STATUS_LABEL[f] ?? f, 'warn'));
  if (d.is_watch) out.push(tag('관심', 'up', '관심종목(main)'));
  if (!d.detail_tier) out.push(tag('주요계정 층', 'dim'));
  return out;
}

/** 관심종목 쓰기 채널(데이터 소스의 선택 기능 — 없으면 null) */
export interface WatchWriter {
  put<T>(key: string, body?: unknown): Promise<Envelope<T>>;
  del<T>(key: string): Promise<Envelope<T>>;
}

export function watchWriter(source: unknown): WatchWriter | null {
  const s = source as Partial<WatchWriter> | null | undefined;
  return typeof s?.put === 'function' && typeof s.del === 'function' ? (s as WatchWriter) : null;
}

export const STAR_ON = '★ 관심';
export const STAR_OFF = '☆ 관심 추가';

/**
 * 별 단추 — 누르면 main 목록에 더하거나 뺀다. 실패는 단추 옆 한 줄(사유만). 실패 줄 자리는 평소 숨긴 '.msg' 이고 실패한
 * 때만 '.msg.err' 다 — 빈 오류 줄을 미리 두면 데이터가 없는 종목(일봉만)에서도 패널에 오류 줄이 있는 것으로 보인다.
 */
export function watchStar(code: string, on: boolean, w: WatchWriter): HTMLElement {
  let state = on;
  const note = h('span', { class: 'msg', attrs: { role: 'status', hidden: true } });
  const say = (text: string): void => {
    note.textContent = text;
    note.hidden = text === '';
    note.classList.toggle('err', text !== '');
  };
  const btn = h('button', { class: 'btn star', attrs: { type: 'button', 'aria-pressed': on ? 'true' : 'false' } });
  const paint = (): void => {
    btn.textContent = state ? STAR_ON : STAR_OFF;
    btn.setAttribute('aria-pressed', state ? 'true' : 'false');
  };
  paint();
  btn.addEventListener('click', () => {
    const key = `watchlist/main/${code}`;
    btn.disabled = true;
    say('');
    (state ? w.del(key) : w.put(key)).then(
      () => {
        state = !state;
        paint();
        btn.disabled = false;
      },
      (err: unknown) => {
        say(err instanceof Error ? err.message : '관심종목 바꾸기 실패');
        btn.disabled = false;
      },
    );
  });
  return h('span', { class: 'watch-star' }, btn, note);
}

/** 로그인 머리 그리기 — 머리 정보를 돌려준다(다른 패널이 우선주·보통주를 알 때) */
export function drawHeader(p: Panel, env: Envelope<Header>, t: Pick<TabCtx, 'open'> & { source?: unknown }): void {
  const d = env.data;
  const last = d.last;
  const nh = d.newhigh;
  const stale = last?.stale === true;
  const priceNote = stale
    ? `${d.status_flags?.includes('halted') ? '정지' : '거래 없음'} — 마지막 거래 ${kstDate(last.last_trade)}`
    : undefined;
  const items: KvItem[] = [
    {
      label: '종가',
      value: last ? won(last.close) : null,
      quality: last ? (stale ? 'stale' : last.quality) : null,
      hint: last ? `${last.source} · ${kstDate(last.date)}` : undefined,
    },
    { label: '등락', value: last ? pct(last.chg_pct) : null, cls: signClass(last?.chg_pct) },
    { label: '거래대금', value: last ? joEok(last.turnover) : null },
    { label: '시가총액(이 종목)', value: last ? joEok(last.mktcap) : null },
    { label: '결산월', value: d.acc_mt ? `${d.acc_mt}월` : null },
    { label: '상장일', value: d.listed_on ? kstDate(d.listed_on) : null },
    {
      label: '신고가 120일',
      value: nh ? newHighText(nh.d120) : null,
      cls: nh?.d120 ? 'up' : undefined,
      hint: '120 KRX 거래일',
      quality: nh?.quality ?? null,
    },
    { label: '신고가 52주', value: nh ? newHighText(nh.w52) : null, cls: nh?.w52 ? 'up' : undefined, hint: '달력 364일' },
    { label: '신고가 역사적', value: nh ? newHighText(nh.hist) : null, cls: nh?.hist ? 'up' : undefined, hint: '상장 이후' },
  ];
  const isPref = d.kind === 'pref' && d.common_code && d.common_code !== d.code;
  const common = d.common_code;
  const w = watchWriter(t.source);
  append(p.body, [
    h(
      'p',
      { class: 'p8-title' },
      h('b', null, d.name ?? d.code),
      ' ',
      h('small', null, d.code),
      ' ',
      badges(d),
      w ? watchStar(d.code, d.is_watch, w) : '',
    ),
    priceNote ? h('p', { class: 'msg warn', data: { stale: 'true' } }, priceNote) : null,
    kv(items),
    h(
      'p',
      { class: 'def' },
      `회사 ${d.corp_name ?? DASH} (DART ${d.corp_code ?? '고유번호 없음'})`,
      d.induty_code ? ` · 업종 ${d.induty_code}` : '',
      isPref && common
        ? h(
            'span',
            null,
            ' · 재무·공시·컨센서스는 회사(보통주) 것 — ',
            h(
              'button',
              { class: 'btn', attrs: { type: 'button' }, on: { click: () => t.open(common) } },
              `보통주 ${common} 보기`,
            ),
          )
        : '',
      nh ? ` · 신고가 기준 ${nh.basis === 'high' ? '고가' : '종가'} ${kstDate(nh.date)}` : '',
    ),
  ]);
  p.setSource(env);
}

/** 로그인: 머리를 불러 그린다. 모르는 코드(404 — no_data 아님)면 false */
export async function loadHeader(p: Panel, t: TabCtx): Promise<boolean> {
  try {
    const env = await t.get<Header>(`stock/${t.code}/header`);
    p.clearBody();
    drawHeader(p, env, t);
    return true;
  } catch (err) {
    if (err instanceof DataError && err.status === 404 && err.serverMessage === null) {
      p.showMessage(`모르는 종목 코드 ${t.code}`);
      return false;
    }
    failed(p, err, HINT.market);
    return true;
  }
}

/** 공개 머리 — DART 이름·시장·결산월 + TradingView 시세 위젯(신고가는 시세 원천이라 자물쇠) */
export async function loadPublicHeader(p: Panel, t: TabCtx): Promise<boolean> {
  let corp: CorpRow | null;
  let fin: Envelope<PubFin> | null;
  try {
    corp = await t.corp();
    fin = await t.pub();
  } catch (err) {
    failed(p, err, HINT.public);
    return true;
  }
  p.clearBody();
  const name = corp?.name ?? fin?.data.corp_name ?? null;
  const market = corp?.market ?? fin?.data.market ?? null;
  const acc = corp?.acc_mt ?? fin?.data.acc_mt ?? null;
  p.body.append(
    h('p', { class: 'p8-title' }, h('b', null, name ?? t.code), ' ', h('small', null, t.code), ' ', market ? tag(marketLabel(market)) : null),
    kv([
      { label: '결산월', value: acc ? `${acc}월` : null, hint: 'DART 기업개황' },
      { label: '공시 재무', value: fin ? '있음' : '없음', cls: fin ? undefined : 'dim' },
      { label: '시세·신고가', locked: true },
    ]),
  );
  const tv = t.tradingview;
  if (tv) {
    const cell = h('div', { class: 'tile', attrs: { role: 'figure', 'aria-label': `${name ?? t.code} 시세(TradingView)` } });
    p.body.append(cell);
    // KRX 종목 심볼 [확인 필요 — R14]
    tv(cell, 'mini-symbol-overview', { symbol: `KRX:${t.code}`, width: '100%', height: 160, dateRange: '12M' });
  }
  if (fin) p.setSource({ ...fin, notes: [] });
  else p.setSrcText(corp ? 'DART 상장사 목록' : 'DART');
  return true;
}
