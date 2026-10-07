/* 실시간 보드 — 고정 셸의 동작부.
 *
 * 이 파일은 **데이터를 하나도 들고 있지 않다.** 열릴 때 api/latest.json 을 받아
 * 그리고, 열어 둔 채로 주기적으로 api/index.json(작다) 만 다시 받아 생성 시각이
 * 바뀌었는지 본다. 바뀌었으면 그 자리에서 갈아 끼운다 — 사람이 새로고침을
 * 누르지 않아도 최신이 된다.
 *
 * 숫자 표기는 render.py 의 pct/eok/mult/plain 과 **같은 규칙**이다. 한쪽만 고치면
 * 같은 값이 화면과 엑셀·텔레그램에서 다르게 보인다.
 *
 * 값을 만들지 않는다. 계산 안 된 값은 0 이 아니라 '–' 로 둔다 (CLAUDE.md 2장). */
(function () {
  'use strict';

  var POLL_MS = 60000;          // 갱신 확인 주기. 확인은 index.json(수백 바이트)만 받는다.
  var API = 'api/';

  var S = {
    data: null,
    date: null,                 // null 이면 최신. 값이 있으면 그 날짜 보관본.
    latest: null,
    dates: [],
    basis: 'hi',
    tab: 'nh',
    kinds: {},                  // 라벨 알약 on/off
    onlyTurn: false,
    onlyCommon: false,
    sortBy: {},                 // 표별 정렬 상태 (다시 그려도 유지된다)
    timer: null
  };

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ── 표기 ───────────────────────────────────────── */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function num(v) { return typeof v === 'number' && isFinite(v); }
  function cls(v) { return !num(v) || v === 0 ? '' : (v > 0 ? 'up' : 'down'); }
  var MUT = '<span class="mut">–</span>';

  function pct(v, d) {
    if (!num(v)) return MUT;
    return '<span class="num ' + cls(v) + '">' + (v >= 0 ? '+' : '') +
      v.toFixed(d === undefined ? 2 : d) + '%</span>';
  }
  function plain(v, d) {
    if (!num(v)) return MUT;
    return '<span class="num">' + v.toFixed(d === undefined ? 2 : d) + '%</span>';
  }
  function eokRaw(v) {
    if (!num(v)) return null;
    return Math.abs(v) >= 10000
      ? (v / 10000).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '조'
      : Math.round(v).toLocaleString('en-US') + '억';
  }
  function eok(v) {
    var t = eokRaw(v);
    return t === null ? MUT : '<span class="num">' + t + '</span>';
  }
  function mult(v) { return num(v) ? '<span class="num">' + v.toFixed(1) + '배</span>' : MUT; }
  /* 5일 축소폭만 부호와 색이 반대다 — 갭이 좁혀지면 음수인데 그게 좋은 값이다. */
  function narrow(v) {
    if (!num(v)) return MUT;
    var c = v < 0 ? 'up' : (v > 0 ? 'down' : '');
    return '<span class="num ' + c + '">' + (v >= 0 ? '+' : '') + v.toFixed(1) + '%p</span>';
  }
  function label(k) { return (S.data && S.data.labels && S.data.labels[k]) || k || ''; }
  function tagCls(k) { return k === 'hist' ? 'tag-hist' : (k === 'w52' ? 'tag-52' : 'tag-60'); }
  function tag(k) {
    return k ? '<span class="tag ' + tagCls(k) + '">' + esc(label(k)) + '</span>' : MUT;
  }
  function other(k, word) {
    return k
      ? '<span class="tag tag-mut" title="' + word + ' 기준으로는 ' + esc(label(k)) +
        '까지 갱신했다">' + word + ' ' + esc(label(k)) + '</span>'
      : '<span class="tag tag-mut" title="' + word + ' 기준으로는 기준 최고가를 넘지 못했다">' +
        word + ' 미달</span>';
  }
  function naver(code) { return 'https://finance.naver.com/item/main.naver?code=' + encodeURIComponent(code); }
  function stock(x) {
    var k = x.kind && x.kind !== 'common'
      ? '<span class="tag tag-mut">' + esc((S.data.kind_labels || {})[x.kind] || x.kind) + '</span>' : '';
    var sus = x.suspect
      ? '<span class="tag tag-warn" title="수정주가 미반영 의심 — 역사적 판정 제외">주의</span>' : '';
    return '<a href="' + naver(x.code) + '" target="_blank" rel="noopener">' + esc(x.name) +
      '</a><span class="code">' + esc(x.code) + '</span>' + k + sus;
  }
  function breadth(b) {
    b = b || {};
    var t = b.total || ((b.up || 0) + (b.flat || 0) + (b.down || 0)) || 1;
    return '<span class="breadth" title="상승 ' + (b.up || 0) + ' / 보합 ' + (b.flat || 0) +
      ' / 하락 ' + (b.down || 0) + '">' +
      '<i class="b-up" style="width:' + ((b.up || 0) / t * 100).toFixed(0) + '%"></i>' +
      '<i class="b-flat" style="width:' + ((b.flat || 0) / t * 100).toFixed(0) + '%"></i>' +
      '<i class="b-dn" style="width:' + ((b.down || 0) / t * 100).toFixed(0) + '%"></i></span>';
  }
  /* 정렬이 화면 문자열을 다시 파싱하지 않도록 원값을 함께 싣는다. */
  function dv(v) { return num(v) ? ' data-v="' + v + '"' : ''; }

  /* 순매수 금액(억원). 1억 미만을 '억' 으로 반올림하면 '-0억' 이 되어 부호만
   * 남고 값이 사라진다 — 그 구간은 만원으로 내려 적는다. */
  function netAmt(v) {
    var a = Math.abs(v), sign = v > 0 ? '+' : (v < 0 ? '-' : '');
    if (a >= 10000) return sign + (a / 10000).toFixed(2) + '조';
    if (a >= 100) return sign + Math.round(a).toLocaleString('en-US') + '억';
    if (a >= 1) return sign + a.toFixed(1) + '억';
    return sign + Math.round(a * 10000).toLocaleString('en-US') + '만';
  }

  /* 종목별 순매수. 52주 이상 신고가만 받으므로 나머지 행은 빈칸이 맞다 —
   * '0' 으로 채우면 '안 샀다' 는 없는 사실이 된다. */
  function flowCell(f) {
    if (!f) return '<td class="r"></td>';
    var unit = f.unit, parts = [];
    ['기관', '외국인', '개인'].forEach(function (who) {
      var v = f[who];
      if (!num(v)) return;
      var t = unit === '주' ? Math.round(v).toLocaleString('en-US') + '주' : netAmt(v);
      parts.push('<span class="fl"><i>' + who.charAt(0) + '</i><b class="' + cls(v) +
        '">' + t + '</b></span>');
    });
    if (!parts.length) return '<td class="r"></td>';
    var tip = [];
    var stale = !!(f.as_of && f.as_of !== S.data.as_of);
    if (stale) tip.push(esc(f.as_of) + ' 자 값입니다 — 기준일 값이 아직 없습니다');
    /* 네이버 폴백은 순매매량(주)이고 금액은 종가 환산 '추정' 이다 (D-083). 칸에
     * 출처를 적고 추정 금액은 툴팁에 '추정' 을 붙여 둔다 — 텔레그램과 같은 규칙.
     * 조용히 바꾸지 않는다. */
    var src = f.source === 'naver' ? '네이버' : '';
    if (src) {
      var est = f.amt_est && f.amt_est.is_estimate ? f.amt_est : {}, ests = [];
      ['기관', '외국인', '개인'].forEach(function (who) {
        if (num(est[who])) ests.push(who + ' ' + netAmt(est[who]));
      });
      tip.push('네이버 순매매량(주)' + (ests.length ? ' · 종가 환산 추정 ' + ests.join(' · ') : ''));
    }
    return '<td class="r flows"' + (tip.length ? ' title="' + tip.join(' — ') + '"' : '') + '>' +
      parts.join('') + (stale ? '<span class="mut">*</span>' : '') +
      (src ? '<span class="mut src">(' + src + ')</span>' : '') + '</td>';
  }

  /* 종목별 재료 — 당일 기사·공시·X 포워딩 (D-085). 52주 이상만 모으므로 없는 행에는
   * 아무것도 없다. 텔레그램은 종목당 두 줄을 링크 없이 싣고 '전체는 대시보드' 라
   * 적는다 — 그 전체가 여기다. 접두는 텔레그램(TRIGGER_PREFIX)과 같다. */
  var TRIG_PREFIX = { dart: '공시', telegram_x: 'X' };
  function trigRow(items) {
    var li = items.map(function (t) {
      var pre = TRIG_PREFIX[t.source] || '재료';
      var who = t.publisher || (t.source === 'dart' ? 'DART' : '출처 미상');
      var title = t.link
        ? '<a href="' + esc(t.link) + '" target="_blank" rel="noopener">' + esc(t.title) + '</a>'
        : esc(t.title);
      return '<li><i>' + pre + '</i>' + title + '<span class="mut">(' + esc(who) +
        (t.date ? ' · ' + esc(t.date) : '') + ')</span></li>';
    });
    return '<tr class="trig-row"><td colspan="8"><ul>' + li.join('') + '</ul></td></tr>';
  }

  /* ── 머리말 ─────────────────────────────────────── */
  function renderHead() {
    var d = S.data, m = d.market || {};
    var li = [];
    (m.indices || []).forEach(function (x) {
      li.push('<li><span class="k">' + esc(x.label) + '</span>' +
        (num(x.close)
          ? '<span class="v">' + x.close.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '</span>'
          : '<span class="v mut">수집 실패</span>') +
        '<span class="d">' + pct(x.chg_pct) + '</span></li>');
    });
    li.push(m.fx && num(m.fx.value)
      ? '<li><span class="k">USD/KRW</span><span class="v">' +
        m.fx.value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) +
        '</span><span class="d">' + pct(m.fx.chg_pct) + '</span></li>'
      : '<li><span class="k">USD/KRW</span><span class="v mut">수집 실패</span></li>');
    if ((m.flows || []).length) {
      m.flows.forEach(function (f) {
        var t = Math.abs(f.value) >= 10000
          ? (f.value / 10000).toFixed(2) + '조'
          : Math.round(f.value).toLocaleString('en-US') + (m.unit || '억원');
        li.push('<li><span class="k">' + esc(f.who) + '</span><span class="v ' + cls(f.value) + '">' +
          (f.value >= 0 ? '+' : '') + t + '</span></li>');
      });
    } else {
      li.push('<li><span class="k">투자자별 수급</span><span class="v mut">' +
        (m.has_flows ? '값 없음' : '소스 미확보') + '</span></li>');
    }
    $('#strip').innerHTML = li.join('');

    /* 종가가 KRX 정규장 확정치인가 (D-080). render.py 의 구운 보드와 **같은
       문구·같은 3값 처리**를 쓴다 — 옛 state 에는 키가 없으므로 그때는 아무것도
       적지 않는다. 모르는 것을 '확정' 이라고 쓰지 않는다. */
    var cc = d.close_confirmed;
    var ccHtml = (cc === undefined || cc === null) ? ''
      : (cc
        ? '<span class="close-src ok" title="KRX 정규장 확정 시세로 덮었습니다">종가 확정</span> · '
        : '<span class="close-src bad" title="KRX 정규장 확정 시세를 못 받아 16:07 '
          + '네이버 값입니다. NXT 애프터마켓·시간외 단일가가 끝날 때까지 움직입니다">'
          + '종가 잠정</span> · ');
    $('#asof').innerHTML = esc(d.as_of || '') +
      ' · ' + (S.basis === 'hi' ? '고가' : '종가') + ' 기준 · ' + ccHtml +
      (d.universe_n ? '종목 ' + d.universe_n.toLocaleString('en-US') : '');
    var dl = $('#xlsx');
    if (d.xlsx) { dl.hidden = false; dl.href = 'x/' + d.xlsx; } else { dl.hidden = true; }
  }

  /* ── 배너 ───────────────────────────────────────── */
  function banner(items, title, kind) {
    if (!items || !items.length) return '';
    return '<div class="banner ' + (kind || '') + '"><b>' + esc(title) + '</b><ul>' +
      items.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul></div>';
  }

  /* ── 탭 1 · 신고가 ───────────────────────────────── */
  function renderNewhigh() {
    var d = S.data;
    var th = d.thresholds || {}, px = th.proximity || {}, lb = th.lookback || {};
    var out = [];

    out.push(banner((d.notices || {}).miss, '빠진 데이터', 'warn'));
    out.push(banner((d.notices || {}).scope, '집계 범위', ''));
    // 못 받은 종목만이 아니다 — KIS 대신 네이버로 채운 종목의 안내도 여기 실린다
    // (D-083). 제목이 '못 받은 종목' 이면 '폴백 11종목 성공' 이 그 아래 서서 어긋난다.
    out.push(banner(d.flow_missing, '종목별 수급 — 못 받은 것·대체한 것', 'warn'));

    // 알약
    var p = [];
    (d.displayed || []).forEach(function (k) {
      var n = (S.basis === 'hi' ? d.counts_high : d.counts_close) || {};
      p.push('<button class="pill" data-kind="' + esc(k) + '" aria-pressed="' +
        (S.kinds[k] !== false) + '">' + esc(label(k)) +
        '<span class="c">' + (n[k] || 0) + '</span></button>');
    });
    if (d.min_turnover_eok) {
      p.push('<button class="pill" data-f="turn" aria-pressed="' + S.onlyTurn + '">거래대금 ' +
        Math.round(d.min_turnover_eok).toLocaleString('en-US') + '억↑</button>');
    }
    var ok = d.other_kinds || {};
    var nOther = Object.keys(ok).reduce(function (a, k) { return a + ok[k]; }, 0);
    if (nOther) {
      var why = Object.keys(ok).sort().map(function (k) {
        return ((d.kind_labels || {})[k] || k) + ' ' + ok[k];
      }).join(' · ');
      p.push('<button class="pill" data-f="common" aria-pressed="' + S.onlyCommon +
        '" title="' + esc(why) + ' 를 숨깁니다">보통주만<span class="c">' + nOther + '</span></button>');
    }
    p.push('<span class="spacer"></span><div class="seg" role="group" aria-label="신고가 판정 기준">' +
      '<button data-basis="hi" aria-pressed="' + (S.basis === 'hi') + '">고가 기준</button>' +
      '<button data-basis="cl" aria-pressed="' + (S.basis === 'cl') + '">종가 기준</button></div>');
    out.push('<div class="pills">' + p.join('') + '</div>');

    if ((d.clusters || []).length) {
      out.push('<div class="banner hit"><b>돌파 임박</b>' +
        d.clusters.map(function (c) { return esc(c.name) + ' 근접 ' + c.n + '종목'; }).join(' · ') +
        ' — 탐지 9 근접 클러스터</div>');
    }

    // 달성
    var lbTxt = Object.keys(lb).map(function (k) { return label(k) + ' ' + lb[k] + '영업일'; }).join(' / ');
    out.push('<div class="card"><div class="card-h"><h2>달성</h2>' +
      '<span class="note">' + esc(lbTxt) + ' · 거래량 배수는 20일 평균 대비 · ' +
      '순매수는 ' + esc(label(d.flow_kind || 'w52')) + ' 이상만 받습니다(기관·외국인·개인)</span>' +
      '<span class="spacer"></span><span class="note" id="ach-n"></span></div>' +
      '<div class="card-b flush">' + achievedTable(d.achieved || []) + '</div></div>');

    // 근접
    out.push('<div class="card"><div class="card-h"><h2>근접</h2>' +
      '<span class="note">갭 ' + esc(px.max_gap_pct) + '% 이내 · 시총 ' +
      (num(px.min_mktcap_eok) ? Math.round(px.min_mktcap_eok).toLocaleString('en-US') : '?') +
      '억 이상 · ' + esc(px.narrow_days) + '일 축소폭 순 정렬</span></div>' +
      '<div class="card-b flush">' + proximityTable(d.proximity || []) + '</div></div>');

    return out.join('');
  }

  function keep(x) {
    /* 알약 필터. 라벨 조건은 달성 표에만 건다 — 근접 행은 라벨 자체가 없어서
     * 조건 없이 걸면 표가 통째로 사라진다. */
    if ('high_label' in x || 'close_label' in x) {
      var k = S.basis === 'hi' ? x.high_label : x.close_label;
      if (!k || (S.data.displayed || []).indexOf(k) < 0) return false;
      if (S.kinds[k] === false) return false;
    }
    if (S.onlyTurn && !((x.turnover || 0) >= (S.data.min_turnover_eok || 0))) return false;
    if (S.onlyCommon && (x.kind || 'common') !== 'common') return false;
    return true;
  }

  function achievedTable(rows) {
    rows = rows.filter(keep);
    if (!rows.length) return '<div class="empty">조건에 맞는 종목이 없습니다.</div>';
    var by = {};
    rows.forEach(function (x) {
      var t = x.theme_name || '미분류';
      (by[t] = by[t] || []).push(x);
    });
    var order = Object.keys(by).sort(function (a, b) {
      if ((a === '미분류') !== (b === '미분류')) return a === '미분류' ? 1 : -1;
      var sa = by[a].reduce(function (s, y) { return s + (y.turnover || 0); }, 0);
      var sb = by[b].reduce(function (s, y) { return s + (y.turnover || 0); }, 0);
      return sb - sa;
    });
    var body = [];
    order.forEach(function (name) {
      var items = by[name].slice().sort(function (a, b) { return (b.turnover || 0) - (a.turnover || 0); });
      var turn = items.reduce(function (s, y) { return s + (y.turnover || 0); }, 0);
      body.push('<tr class="group-row"><td colspan="8">' + esc(name) +
        '<span>' + items.length + '종목 · ' + (eokRaw(turn) || '–') + '</span></td></tr>');
      items.forEach(function (x) {
        var hi = x.high_label, cl = x.close_label;
        var basisCell = S.basis === 'hi' ? tag(hi) + other(cl, '종가') : tag(cl) + other(hi, '고가');
        body.push('<tr><td>' + stock(x) + '</td>' +
          '<td class="stage">' + esc(x.stage || '') + '</td>' +
          '<td>' + basisCell + '</td>' +
          '<td>' + (x.status ? '<span class="tag tag-new">' + esc(x.status) + '</span>' : '') + '</td>' +
          '<td class="r"' + dv(x.chg_pct) + '>' + pct(x.chg_pct) + '</td>' +
          '<td class="r"' + dv(x.turnover) + '>' + eok(x.turnover) + '</td>' +
          '<td class="r"' + dv(x.vol_mult) + '>' + mult(x.vol_mult) + '</td>' +
          flowCell(x.flow) + '</tr>');
        if ((x.trigger || []).length) body.push(trigRow(x.trigger));
      });
    });
    return '<div class="tbl"><table><thead><tr><th>종목</th><th>단계</th><th>구분</th>' +
      '<th>상태</th><th class="r">등락률</th><th class="r">거래대금</th>' +
      '<th class="r">거래량</th><th class="r">순매수</th></tr></thead><tbody>' +
      body.join('') + '</tbody></table></div>';
  }

  function proximityTable(rows) {
    rows = rows.filter(keep);
    if (!rows.length) return '<div class="empty">근접 조건을 만족한 종목이 없습니다.</div>';
    var body = rows.map(function (x) {
      var res = num(x.resistance)
        ? esc(x.resistance_label || '') + ' <span class="code num">' + x.resistance.toFixed(1) + '</span>'
        : MUT;
      var theme = [x.theme_name, x.stage].filter(Boolean).join(' · ') || '미분류';
      return '<tr><td>' + stock(x) + '</td>' +
        '<td class="stage">' + esc(theme) + '</td>' +
        '<td>' + tag(x.near_kind) + '</td>' +
        '<td class="r"' + dv(x.near_gap) + '>' + plain(x.near_gap, 1) + '</td>' +
        '<td class="r"' + dv(x.near_narrow5) + '>' + narrow(x.near_narrow5) + '</td>' +
        '<td class="r"' + dv(x.resistance) + '>' + res + '</td>' +
        '<td class="r"' + dv(x.vol_mult) + '>' + mult(x.vol_mult) + '</td></tr>';
    });
    return '<div class="tbl"><table><thead><tr><th>종목</th><th>테마 · 단계</th><th>기준</th>' +
      '<th class="r">갭</th><th class="r">5일 축소</th><th class="r">저항두께</th>' +
      '<th class="r">거래량</th></tr></thead><tbody>' + body.join('') + '</tbody></table></div>';
  }

  /* ── 탭 2 · 섹터 랭킹 ────────────────────────────── */
  function rankDelta(x, hasPrev) {
    if (!hasPrev) return '';
    var d = x.rank_delta;
    if (d === null || d === undefined) return '<span class="rk mut" title="어제 이 표에 없던 섹터입니다">–</span>';
    if (d === 0) return '<span class="rk mut" title="전일과 같은 순위">—</span>';
    return '<span class="rk ' + (d > 0 ? 'up' : 'down') + '" title="전일 ' + x.rank_prev +
      '위 → 오늘 ' + x.rank + '위">' + (d > 0 ? '▲' : '▼') + Math.abs(d) + '</span>';
  }

  function renderSectors() {
    var d = S.data, sb = d.sector_boards || [], out = [];
    if (!sb.length) {
      out.push('<div class="card"><div class="empty">랭킹이 아직 없습니다.</div></div>');
    } else {
      var cur = S.sb || sb[0].key;
      if (!sb.some(function (b) { return b.key === cur; })) cur = sb[0].key;
      S.sb = cur;
      out.push('<div class="pills">' + sb.map(function (b) {
        return '<button class="pill" data-sb="' + esc(b.key) + '" aria-pressed="' +
          (b.key === cur) + '">' + esc(b.label) + '</button>';
      }).join('') + '</div>');
      var b = sb.filter(function (x) { return x.key === cur; })[0];
      var w = b.weighting === 'mktcap' ? '시가총액 가중' : '단순 평균';
      out.push('<div class="card"><div class="card-h"><h2>' + esc(b.ret_label) + ' 순위</h2>' +
        '<span class="note">' + esc(d.taxonomy || '') + ' 분류 ' + b.sectors.length +
        '개 · 등락률은 구성종목 ' + w + ' 계산값 · 종목명 옆 숫자는 구성 종목 수</span></div>' +
        '<div class="card-b flush">' + sectorBoard(b) + '</div></div>');
    }
    out.push('<div class="card"><div class="card-h"><h2>테마별 등락</h2>' +
      '<span class="note">knowledge/themes.yaml 2층 분류 · 다대다라 한 종목이 여러 줄에 등장합니다</span></div>' +
      '<div class="card-b flush">' + themeTable(d.themes || []) + '</div></div>');
    return out.join('');
  }

  function sectorBoard(b) {
    var hasPrev = !!b.has_prev_rank;
    var head = '<tr><th>섹터</th><th class="r">' + esc(b.ret_label) + ' 순위</th>' +
      '<th class="r">' + esc(b.ret_label) + '</th><th>브레드스</th>' +
      [1, 2, 3, 4, 5].map(function (i) { return '<th>' + i + '등</th>'; }).join('') + '</tr>';
    var body = b.sectors.map(function (x) {
      var tops = (x.top || []).slice(0, 5);
      var tds = [];
      for (var i = 0; i < 5; i++) {
        tds.push(i < tops.length
          ? '<td><a href="' + naver(tops[i].code) + '" target="_blank" rel="noopener" title="' +
            esc(tops[i].name) + ' ' + esc(tops[i].ret || '') + '">' + esc(tops[i].name) + '</a></td>'
          : '<td class="mut">–</td>');
      }
      return '<tr><td>' + esc(x.name) + '<span class="code">' + x.n + '</span></td>' +
        '<td class="r num"' + dv(x.rank) + '>' + x.rank + rankDelta(x, hasPrev) + '</td>' +
        '<td class="r num ' + cls(x.ret_raw) + '"' + dv(x.ret_raw) + '>' + esc(x.ret || '–') + '</td>' +
        '<td>' + breadth(x.breadth) + '</td>' + tds.join('') + '</tr>';
    });
    return '<div class="tbl"><table><thead>' + head + '</thead><tbody>' + body.join('') + '</tbody></table></div>';
  }

  function themeTable(rows) {
    if (!rows.length) return '<div class="empty">집계할 데이터가 없습니다.</div>';
    var body = rows.map(function (x) {
      var b = x.breadth || {}, ld = x.leader || {};
      var lead = num(ld.chg_pct)
        ? esc(ld.name) + ' ' + (ld.chg_pct >= 0 ? '+' : '') + ld.chg_pct.toFixed(1) + '%'
        : esc(ld.name || '');
      return '<tr><td>' + esc(x.name) + '</td>' +
        '<td class="r"' + dv(x.chg_pct) + '>' + pct(x.chg_pct) + '</td>' +
        '<td class="r"' + dv(x.ret_5d) + '>' + pct(x.ret_5d) + '</td>' +
        '<td class="r"' + dv(x.ret_21d) + '>' + pct(x.ret_21d) + '</td>' +
        '<td>' + breadth(b) + '</td>' +
        '<td class="r num">' + (b.up || 0) + '/' + (b.flat || 0) + '/' + (b.down || 0) + '</td>' +
        '<td class="r num"' + dv(x.n_newhigh) + '>' + (x.n_newhigh || 0) + '</td>' +
        '<td class="r"' + dv(x.turnover_mult) + '>' + mult(x.turnover_mult) + '</td>' +
        '<td class="stage">' + lead + '</td></tr>';
    });
    return '<div class="tbl"><table><thead><tr><th>테마</th><th class="r">일간</th>' +
      '<th class="r">주간</th><th class="r">월간</th><th>브레드스</th><th class="r">상/보/하</th>' +
      '<th class="r">신고가</th><th class="r">거래대금 배수</th><th>대표 종목</th></tr></thead>' +
      '<tbody>' + body.join('') + '</tbody></table></div>';
  }

  /* ── 탭 3 · 종목 랭킹 ────────────────────────────── */
  function renderStocks() {
    var d = S.data, kb = d.stock_boards || [];
    if (!kb.length) return '<div class="card"><div class="empty">랭킹이 아직 없습니다.</div></div>';
    var out = ['<div class="banner hit"><b>양쪽 동시 등장 ' + (d.cross_n || 0) + '종목</b>' +
      '수익률 상위이면서 거래량도 며칠째 붙어 있는 종목. 표에서 파란 줄로 표시했고, ' +
      '텔레그램은 이것만 보냅니다.</div>'];
    kb.forEach(function (b) {
      var cols = b.columns || [];
      var head = '<tr><th class="r">#</th><th>종목명</th>' +
        cols.map(function (c) { return '<th class="r">' + esc(c.label) + '</th>'; }).join('') + '</tr>';
      var body = (b.rows || []).map(function (r) {
        var cells = cols.map(function (c) {
          var txt = (r.cells || {})[c.key], raw = (r.cells_raw || {})[c.key];
          if (txt === null || txt === undefined) return '<td class="r mut">–</td>';
          var k = c.kind === 'pct' ? cls(raw) : (c.kind === 'ratio'
            ? (raw > 100 ? 'up' : (raw < 100 ? 'down' : '')) : '');
          return '<td class="r num ' + k + '"' + dv(raw) + '>' + esc(txt) + '</td>';
        }).join('');
        return '<tr' + (r.cross ? ' class="cross"' : '') + '><td class="r num mut">' + r.rank + '</td>' +
          '<td><a href="' + naver(r.code) + '" target="_blank" rel="noopener">' + esc(r.name) +
          '</a></td>' + cells + '</tr>';
      });
      out.push('<div class="card"><div class="card-h"><h2>' + esc(b.title) + '</h2></div>' +
        '<div class="card-b flush"><div class="tbl"><table><thead>' + head + '</thead><tbody>' +
        body.join('') + '</tbody></table></div></div></div>');
    });
    return out.join('');
  }

  /* ── 탭 4 · 히트맵 ──────────────────────────────── */
  function tone(v) {
    if (!num(v)) return ['#EDEFF1', '#6B7280'];
    if (v >= 3) return ['#DC6660', '#3B0F0C'];
    if (v >= 1.5) return ['#EFAAA6', '#5C1713'];
    if (v > 0) return ['#F8E0DE', '#7A231D'];
    if (v === 0) return ['#EDEFF1', '#4B5563'];
    if (v > -1.5) return ['#DCE9F8', '#123F72'];
    if (v > -3) return ['#9DC4EE', '#0E3560'];
    return ['#5A9BE0', '#082645'];
  }
  /* 면적은 그룹 안 '순위'로 정한다. 거래대금 절대비를 그대로 쓰면 1등이 그리드를
   * 통째로 먹고 나머지가 한 칸이 되어 종목명이 안 읽힌다. */
  function spans(n, mono) {
    var out = [];
    for (var i = 0; i < n; i++) {
      out.push(mono ? [1, 1] : (i === 0 ? [3, 2] : (i === 1 ? [3, 1] : (i < 4 ? [2, 1] : [1, 1]))));
    }
    return out;
  }
  function fmt1(v) { return num(v) ? (v > 0 ? '+' : '') + v.toFixed(1) + '%' : '–'; }

  var CELLS = [], tipEl = null;

  function finHtml(c) {
    var f = c.fin;
    var h = '<div class="t"><b>' + esc(c.name) + '</b>' +
      (c.mktcap ? '<span> 시총 ' + eokRaw(c.mktcap) + '</span>' : '') +
      (c.turnover ? '<span> · 거래대금 ' + eokRaw(c.turnover) + '</span>' : '') + '</div>';
    if (!f) return h + '<div class="mut">재무 데이터 없음 (DART 미수집)</div>';
    function opm(op, rev) { return num(op) && rev ? (op / rev * 100).toFixed(1) + '%' : '–'; }
    function cell(v) { return eokRaw(v) || '–'; }
    function tbl(title, rows) {
      if (!rows.length) return '';
      return '<div class="cap">' + title + '</div><table><tr><th></th><th>매출</th>' +
        '<th>영업이익</th><th>OPM</th></tr>' + rows.join('') + '</table>';
    }
    var a = (f.a || []).map(function (r) {
      return '<tr><td>' + esc(r[0]) + '</td><td>' + cell(r[1]) + '</td><td class="' +
        (r[2] < 0 ? 'down' : '') + '">' + cell(r[2]) + '</td><td>' + opm(r[2], r[1]) + '</td></tr>';
    });
    var q = (f.q || []).map(function (r) {
      return '<tr><td>' + esc(r[0]) + (r[3] ? '<sup title="직전 분기 보고서가 없어 차분 못 함">*</sup>' : '') +
        '</td><td>' + cell(r[1]) + '</td><td class="' + (r[2] < 0 ? 'down' : '') + '">' +
        cell(r[2]) + '</td><td>' + opm(r[2], r[1]) + '</td></tr>';
    });
    return h + tbl('연간 (억원)', a) + tbl('분기 (억원)', q) +
      '<div class="src">DART ' + esc(f.fs === 'OFS' ? '별도' : '연결') +
      ' · 받은 날 ' + esc(f.d || '') + '</div>';
  }

  function drawHeatmap() {
    var host = $('#hm');
    if (!host) return;
    CELLS = [];
    var groupBy = $('#groupBy').value, sizeBy = $('#sizeBy').value;
    var colorBy = $('#colorBy').value, nhOn = $('#nhOn').value === '1';
    var groups = (S.data.heatmap || {})[groupBy] || [];
    if (!groups.length) { host.innerHTML = '<div class="empty">히트맵에 넣을 데이터가 없습니다.</div>'; return; }
    host.innerHTML = groups.map(function (g) {
      var cells = (g.cells || []).slice();
      if (sizeBy !== 'mono') cells.sort(function (a, b) { return (b[sizeBy] || 0) - (a[sizeBy] || 0); });
      var sp = spans(cells.length, sizeBy === 'mono');
      var body = cells.map(function (c, i) {
        var v = c[colorBy], t = tone(v);
        var tip = c.name + ' ' + fmt1(v) + (c.newhigh ? ' · 신고가' : '') +
          (c.turnover ? ' · 거래대금 ' + Math.round(c.turnover).toLocaleString('en-US') + '억' : '');
        var key = CELLS.length;
        CELLS.push(c);
        return '<div class="cell' + (i === 0 && sizeBy !== 'mono' ? ' big' : '') +
          (nhOn && c.newhigh ? ' nh' : '') + '" data-k="' + key +
          '" style="grid-column:span ' + sp[i][0] + ';grid-row:span ' + sp[i][1] +
          ';background:' + t[0] + ';color:' + t[1] + '"' +
          (c.fin ? '' : ' title="' + esc(tip) + '"') + '>' +
          '<span class="n">' + esc(c.name) + '</span>' +
          '<span class="p">' + fmt1(v) + '</span></div>';
      }).join('');
      return '<div class="hm-group"><div class="hm-cap"><b>' + esc(g.group) + '</b>' +
        '<span class="num ' + ((g.chg_pct || 0) >= 0 ? 'up' : 'down') + '">' + fmt1(g.chg_pct) +
        '</span></div><div class="hm-cells">' + body + '</div></div>';
    }).join('');
  }

  function bindHeatmap() {
    var host = $('#hm');
    if (!host || host.dataset.bound) return;
    host.dataset.bound = '1';
    if (!tipEl) {
      tipEl = document.createElement('div');
      tipEl.className = 'hm-tip';
      tipEl.hidden = true;
      document.body.appendChild(tipEl);
    }
    function place(ev) {
      var x = ev.clientX + 14, y = ev.clientY + 14;
      if (x + tipEl.offsetWidth > window.innerWidth - 8) x = ev.clientX - tipEl.offsetWidth - 14;
      if (y + tipEl.offsetHeight > window.innerHeight - 8) y = ev.clientY - tipEl.offsetHeight - 14;
      tipEl.style.left = Math.max(4, x) + 'px';
      tipEl.style.top = Math.max(4, y) + 'px';
    }
    host.addEventListener('mouseover', function (ev) {
      var cell = ev.target.closest ? ev.target.closest('.cell') : null;
      if (!cell) return;
      var c = CELLS[+cell.dataset.k];
      if (!c) return;
      tipEl.innerHTML = finHtml(c);
      tipEl.hidden = false;
      place(ev);
    });
    host.addEventListener('mousemove', function (ev) { if (!tipEl.hidden) place(ev); });
    host.addEventListener('mouseleave', function () { tipEl.hidden = true; });
    ['sizeBy', 'colorBy', 'groupBy', 'nhOn'].forEach(function (id) {
      $('#' + id).addEventListener('change', drawHeatmap);
    });
  }

  /* ── 탭 5 · 뉴스 ────────────────────────────────── */
  function narrHtml(text) {
    var out = (text || '').split('\n').map(function (l) { return l.trim(); })
      .filter(Boolean).map(function (l) {
        var c = l.charAt(0) === '»' ? 'n2' : 'n1';
        var t = esc(l.replace(/^[-»\s]+/, ''));
        return '<li class="' + c + '">' + t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>') + '</li>';
      });
    return out.length ? '<ul class="narr">' + out.join('') + '</ul>' : '';
  }

  function renderNews() {
    var ts = S.data.news || [];
    if (!ts.length) {
      return '<div class="card"><div class="empty"><b>섹터 뉴스 — 없음</b><br>' +
        '<code>python3 -m board.run --news</code> 로 수집합니다. 네이버 검색 API 자격증명이 필요합니다.</div></div>';
    }
    return '<div class="news-grid">' + ts.map(function (t) {
      var arts = t.articles || [];
      var body = arts.length
        ? '<ul class="news">' + arts.map(function (a) {
          return '<li><a href="' + esc(a.url) + '" target="_blank" rel="noopener">' + esc(a.title) + '</a>' +
            (a.outlet ? '<span class="src">' + esc(a.outlet) + '</span>' : '') +
            (a.query && a.query !== t.name ? '<span class="q">' + esc(a.query) + '</span>' : '') +
            (a.summary ? '<p>' + esc(a.summary) + '</p>' : '') + '</li>';
        }).join('') + '</ul>'
        : '<p class="mut">' + esc(S.data.as_of || '') + ' 자 기사를 찾지 못했습니다 (검색어 ' +
          esc((t.queries || []).join(' · ')) + ').</p>';
      return '<div class="news-card"><div class="news-h"><b>' + esc(t.name) + '</b>' +
        pct(t.chg_pct) +
        (t.n_newhigh ? '<span class="tag tag-new">신고가 ' + t.n_newhigh + '</span>' : '') +
        '<span class="spacer"></span>' +
        (t.n_found > arts.length ? '<span class="mut">' + t.n_found + '건 중 ' + arts.length + '건</span>' : '') +
        '</div>' + narrHtml(t.narrative) + body + '</div>';
    }).join('') + '</div>';
  }

  /* ── 탭 6 · 코멘트 ──────────────────────────────── */
  function inline(t) {
    return esc(t).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }
  function md(text) {
    var out = [], inUl = false, inTbl = false, quote = [];
    function flushQuote() {
      if (quote.length) { out.push('<p class="cite">' + quote.join('<br>') + '</p>'); quote = []; }
    }
    function closeUl() { if (inUl) { out.push('</ul>'); inUl = false; } }
    function closeTbl() { if (inTbl) { out.push('</tbody></table>'); inTbl = false; } }
    (text || '').split('\n').forEach(function (raw) {
      var line = raw.replace(/\s+$/, '');
      if (line.charAt(0) !== '>') flushQuote();
      if (!line.trim()) { closeUl(); closeTbl(); return; }
      if (line.charAt(0) === '|') {
        var cs = line.replace(/^\||\|$/g, '').split('|').map(function (c) { return c.trim(); });
        if (cs.every(function (c) { return /^[-: ]*$/.test(c); })) return;
        if (!inTbl) { out.push('<table><tbody>'); inTbl = true; }
        out.push('<tr>' + cs.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>');
        return;
      }
      closeTbl();
      if (line.charAt(0) === '#') {
        closeUl();
        out.push('<h3>' + inline(line.replace(/^#+\s*/, '')) + '</h3>');
      } else if (line.charAt(0) === '>') {
        closeUl();
        quote.push(inline(line.replace(/^>\s*/, '')));
      } else if (/^\s*(-|»)\s/.test(line)) {
        if (!inUl) { out.push('<ul>'); inUl = true; }
        var sub = line.trim().charAt(0) === '»' ? ' class="sub"' : '';
        out.push('<li' + sub + '>' + inline(line.replace(/^[\s\-»]+/, '')) + '</li>');
      } else if (line.indexOf('---') === 0) {
        return;
      } else {
        closeUl();
        out.push('<p>' + inline(line) + '</p>');
      }
    });
    flushQuote();
    closeUl();
    closeTbl();
    return out.join('');
  }

  function renderDraft() {
    var d = S.data.draft;
    if (!d) {
      return '<div class="card"><div class="empty"><b>일간 코멘트 — 아직 없음</b><br>' +
        '<code>python3 -m board.run --write</code> 로 생성합니다. ANTHROPIC_API_KEY 가 필요합니다.<br>' +
        '생성된 초안은 리포트에 등장한 모든 종목명과 수치가 입력 사실 팩에 있었는지 검증을 ' +
        '통과한 것만 나옵니다 (CLAUDE.md 2장 4번).</div></div>';
    }
    return '<div class="draft-bar"><span class="st">초안 · 검증 통과분만 표시</span>' +
      '<button class="btn" id="copy-draft">본문 복사</button></div>' +
      '<article class="doc">' + md(d) + '</article>';
  }

  /* ── 표 정렬 ────────────────────────────────────── */
  function cellValue(td) {
    if (!td) return NaN;
    if (td.dataset.v !== undefined) { var d = parseFloat(td.dataset.v); return isNaN(d) ? NaN : d; }
    var t = (td.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t || t === '–' || t === '—' || t === '-') return NaN;
    var m = t.match(/^[+\-]?\d[\d,]*(?:\.\d+)?/);
    if (!m) return NaN;
    var v = parseFloat(m[0].replace(/,/g, ''));
    if (t.slice(m[0].length).charAt(0) === '조') v *= 10000;
    return v;
  }
  function sortSegment(rows, idx, dir, numeric) {
    return rows.map(function (tr, i) {
      var td = tr.children[idx];
      return { tr: tr, i: i, n: cellValue(td), s: td ? (td.textContent || '').trim() : '' };
    }).sort(function (a, b) {
      if (numeric) {
        var an = isNaN(a.n), bn = isNaN(b.n);
        if (an && bn) return a.i - b.i;
        if (an) return 1;                    // 계산 안 된 값은 항상 맨 아래
        if (bn) return -1;
        if (a.n !== b.n) return dir * (a.n - b.n);
      } else {
        var c = a.s.localeCompare(b.s, 'ko');
        if (c) return dir * c;
      }
      return a.i - b.i;
    }).map(function (k) { return k.tr; });
  }
  function sortTable(table, idx) {
    var th = table.tHead.rows[0].children[idx], body = table.tBodies[0];
    if (!body) return;
    var rows = Array.prototype.slice.call(body.rows);
    var numeric = rows.some(function (tr) {
      return !tr.classList.contains('group-row') && !isNaN(cellValue(tr.children[idx]));
    });
    var cur = th.getAttribute('aria-sort');
    var dir = cur ? (cur === 'descending' ? 1 : -1) : (numeric ? -1 : 1);
    $$('th', table.tHead).forEach(function (h) { h.removeAttribute('aria-sort'); });
    th.setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
    // 테마 묶음 표(달성)는 묶음 머리를 경계로 묶음 안에서만 정렬한다.
    var out = [], seg = [];
    function flush() { if (seg.length) { out = out.concat(sortSegment(seg, idx, dir, numeric)); seg = []; } }
    rows.forEach(function (tr) {
      if (tr.classList.contains('group-row')) { flush(); out.push(tr); } else seg.push(tr);
    });
    flush();
    out.forEach(function (tr) { body.appendChild(tr); });
  }
  function bindSort(root) {
    $$('table', root).forEach(function (table) {
      if (!table.tHead || !table.tHead.rows.length) return;
      Array.prototype.forEach.call(table.tHead.rows[0].children, function (th, idx) {
        th.tabIndex = 0;
        th.title = '누르면 이 열로 정렬';
        th.addEventListener('click', function () { sortTable(table, idx); });
        th.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); sortTable(table, idx); }
        });
      });
    });
  }

  /* ── 그리기 ─────────────────────────────────────── */
  var TABS = [['nh', '신고가'], ['sector', '섹터 랭킹'], ['stock', '종목 랭킹'],
              ['hm', '히트맵'], ['news', '섹터 뉴스'], ['draft', '일간 코멘트']];

  function renderTabs() {
    var d = S.data;
    var n = {
      nh: (d.achieved || []).length,
      sector: ((d.sector_boards || [])[0] || {}).sectors ? d.sector_boards[0].sectors.length : 0,
      stock: (d.stock_boards || []).length,
      hm: 0, news: (d.news || []).length, draft: 0
    };
    $('#tabbar').innerHTML = TABS.map(function (t) {
      return '<button data-tab="' + t[0] + '" aria-selected="' + (t[0] === S.tab) + '">' + t[1] +
        (n[t[0]] ? '<span class="n">' + n[t[0]] + '</span>' : '') + '</button>';
    }).join('');
  }

  function renderBody() {
    var host = $('#body');
    var html;
    if (S.tab === 'nh') html = renderNewhigh();
    else if (S.tab === 'sector') html = renderSectors();
    else if (S.tab === 'stock') html = renderStocks();
    else if (S.tab === 'news') html = renderNews();
    else if (S.tab === 'draft') html = renderDraft();
    else html = heatmapShell();
    host.innerHTML = html;
    bindSort(host);
    if (S.tab === 'hm') { bindHeatmap(); drawHeatmap(); }
    if (S.tab === 'draft') {
      var b = $('#copy-draft');
      if (b) b.addEventListener('click', function () {
        navigator.clipboard.writeText(S.data.draft || '');
        b.textContent = '복사됨';
        setTimeout(function () { b.textContent = '본문 복사'; }, 1500);
      });
    }
  }

  function heatmapShell() {
    return '<div class="tools">' +
      '<label class="tool">크기 <select id="sizeBy">' +
      '<option value="turnover">거래대금</option><option value="mktcap">시가총액</option>' +
      '<option value="mono">모노 사이즈</option></select></label>' +
      '<label class="tool">색상 <select id="colorBy">' +
      '<option value="chg_pct">일간 등락률</option><option value="ret_5d">주간</option>' +
      '<option value="ret_21d">월간</option><option value="ret_250d">1년(250영업일)</option></select></label>' +
      '<label class="tool">그룹 <select id="groupBy">' +
      '<option value="theme">자체 테마</option><option value="sector">KRX 업종</option></select></label>' +
      '<label class="tool">신고가 강조 <select id="nhOn">' +
      '<option value="1">켬</option><option value="0">끔</option></select></label></div>' +
      '<div id="hm"></div>' +
      '<div class="legend"><span>면적 = 선택한 크기 기준</span><span>색 = 선택한 기간 등락률</span>' +
      '<span><span class="swatch"></span> 당일 신고가</span>' +
      '<span class="scale"><i style="background:#5A9BE0"></i><i style="background:#9DC4EE"></i>' +
      '<i style="background:#DCE9F8"></i><i style="background:#F8E0DE"></i>' +
      '<i style="background:#EFAAA6"></i><i style="background:#DC6660"></i></span>' +
      '<span>-3% → +3%</span></div>';
  }

  function renderFooter() {
    var d = S.data;
    $('#foot').innerHTML =
      '시세·업종은 네이버 금융에서 수집합니다. 인증키가 필요 없는 대신 공개 API 가 아니라 ' +
      '사이트 개편이면 깨집니다.<br>신고가·갭·저항두께 정의는 CLAUDE.md 4장 고정 정의를 ' +
      '그대로 따릅니다. 거래대금 20일 평균과 저항두께는 종가×거래량 추정치입니다.<br>' +
      '전 종목 ' + (d.universe_n || 0).toLocaleString('en-US') + ' · 탐지 이벤트 ' +
      (d.n_events || 0) + '건 · 기준일 ' + esc(d.as_of || '') + ' · 생성 ' + esc(d.generated_at || '');
  }

  function renderAll() {
    renderHead();
    renderTabs();
    renderBody();
    renderFooter();
  }

  /* ── 데이터 ─────────────────────────────────────── */
  function setLive(state, text) {
    var el = $('#live');
    el.dataset.state = state;
    $('#live-t').textContent = text;
  }

  function url(p) { return API + p + (p.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now(); }

  function load(date) {
    setLive('loading', '받는 중');
    var p = date ? 'd/' + date + '.json' : 'latest.json';
    return fetch(url(p), { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status + ' ' + p);
        return r.json();
      })
      .then(function (j) {
        // 기본 기준은 **처음 한 번만** 데이터에서 읽는다. 갱신할 때마다 덮으면
        // 사람이 종가 기준으로 바꿔 둔 화면이 60초마다 고가로 되돌아간다.
        if (!S.data) S.basis = j.basis || 'hi';
        S.data = j;
        S.date = date || null;
        $('#boot').hidden = true;
        $('#app').hidden = false;
        renderAll();
        setLive(date ? 'stale' : 'ok', date ? date + ' 보관본' : '최신 · ' + shortTime(j.generated_at));
        return j;
      })
      .catch(function (err) {
        setLive('error', '갱신 실패');
        if (!S.data) {
          $('#boot').innerHTML = '<div class="err">데이터를 받지 못했습니다 — ' + esc(err.message) +
            '</div><div>보드가 아직 한 번도 돌지 않았을 수 있습니다. ' +
            '<code>python3 -m board.run --daily</code></div>';
        }
        throw err;
      });
  }

  function shortTime(s) {
    // '2026-09-03 16:12:40' → '16:12'
    var m = /(\d{2}:\d{2})/.exec(s || '');
    return m ? m[1] : (s || '');
  }

  function loadIndex() {
    return fetch(url('index.json'), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return null;
        S.dates = j.dates || [];
        S.latest = j.latest;
        var sel = $('#daysel');
        sel.innerHTML = S.dates.map(function (d, i) {
          return '<option value="' + esc(d) + '"' + (d === (S.date || S.latest) ? ' selected' : '') +
            '>' + esc(d) + (i === 0 ? ' (최신)' : '') + '</option>';
        }).join('');
        return j;
      })
      .catch(function () { return null; });
  }

  /* 갱신 확인. 목록 파일만 받아 생성 시각을 비교하고, 바뀌었을 때만 본문을 받는다.
   * 보관본을 보는 중이면 화면을 건드리지 않는다 — 보던 날짜가 밑에서 바뀌면 안 된다. */
  function poll() {
    if (document.hidden) return;
    loadIndex().then(function (j) {
      if (!j || S.date) return;
      if (S.data && j.generated_at && j.generated_at === S.data.generated_at) {
        setLive('ok', '최신 · ' + shortTime(S.data.generated_at));
        return;
      }
      var keepTab = S.tab, y = window.scrollY;
      load(null).then(function () {
        S.tab = keepTab;
        renderTabs();
        renderBody();
        window.scrollTo(0, y);
        flash();
      }).catch(function () {});
    });
  }

  function flash() {
    var el = $('#live');
    el.animate
      ? el.animate([{ opacity: .3 }, { opacity: 1 }], { duration: 600 })
      : null;
  }

  /* ── 이벤트 ─────────────────────────────────────── */
  document.addEventListener('click', function (ev) {
    var t = ev.target.closest ? ev.target.closest('button') : null;
    if (!t) return;
    if (t.dataset.tab) {
      S.tab = t.dataset.tab;
      location.hash = t.dataset.tab;
      renderTabs();
      renderBody();
      window.scrollTo(0, 0);
    } else if (t.dataset.basis) {
      S.basis = t.dataset.basis;
      renderHead();
      renderBody();
    } else if (t.dataset.kind) {
      S.kinds[t.dataset.kind] = t.getAttribute('aria-pressed') !== 'true';
      renderBody();
    } else if (t.dataset.f === 'turn') {
      S.onlyTurn = !S.onlyTurn;
      renderBody();
    } else if (t.dataset.f === 'common') {
      S.onlyCommon = !S.onlyCommon;
      renderBody();
    } else if (t.dataset.sb) {
      S.sb = t.dataset.sb;
      renderBody();
    }
  });

  $('#daysel').addEventListener('change', function () {
    var d = this.value;
    load(d === S.latest ? null : d).catch(function () {});
  });

  $('#refresh').addEventListener('click', function () {
    load(S.date).then(flash).catch(function () {});
  });

  /* 테마는 사람이 고른 값을 기억한다. 못 읽어도 화면은 성립해야 한다. */
  var THEME_KEY = 'board-theme';
  try {
    var saved = localStorage.getItem(THEME_KEY);
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (e) { /* 사생활 보호 모드 등 — 그냥 시스템 설정을 따른다 */ }
  $('#theme').addEventListener('click', function () {
    var cur = document.documentElement.dataset.theme;
    var next = cur === 'dark' ? 'light' : (cur === 'light' ? 'dark'
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark'));
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* 저장 못 해도 이번 세션은 바뀐다 */ }
  });

  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });

  /* ── 시작 ───────────────────────────────────────── */
  var q = new URLSearchParams(location.search);
  var d0 = q.get('d');
  if (location.hash) {
    var h = location.hash.slice(1);
    if (TABS.some(function (t) { return t[0] === h; })) S.tab = h;
  }
  loadIndex().then(function () {
    return load(d0 && d0 !== S.latest ? d0 : null);
  }).catch(function () { /* load 가 화면에 이미 적었다 */ });
  S.timer = setInterval(poll, POLL_MS);
})();
