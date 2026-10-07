// 표는 서버가 그린다. 여기서는 탭 전환, 필터 토글, 히트맵만 담당한다.
(function () {
  'use strict';

  // ── 탭 ───────────────────────────────────────────────
  document.querySelectorAll('nav button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('nav button').forEach(function (x) {
        x.setAttribute('aria-selected', 'false');
      });
      document.querySelectorAll('.panel').forEach(function (x) {
        x.classList.remove('on');
      });
      b.setAttribute('aria-selected', 'true');
      document.getElementById(b.dataset.p).classList.add('on');
    });
  });

  // ── 신고가 필터 ────────────────────────────────────────
  // 라벨 종류 토글과 거래대금·시총 하한. 어느 것도 데이터를 바꾸지 않고
  // 행 표시만 끈다. 상단 카운트는 전체 기준 그대로 둔다.
  // 거래대금 하한(억원)은 **알약이 들고 온다**. 여기 숫자를 박으면 설정을 고쳐도
  // 화면이 안 따라오고, 알약 문구와 판정이 갈린다 (D-074).
  // 시총 하한은 생성 단계에서 걸리므로(D-072) 여기 토글이 아예 없다 —
  // 표에 실린 종목은 이미 전부 하한 위다.

  // 판정 기준. 고가('hi') 가 기본이고 종가('cl') 로 바꿀 수 있다.
  // 표에는 두 기준의 라벨이 이미 다 들어 있다 — 여기서는 어느 쪽을 볼지만 고른다.
  // 자바스크립트가 라벨을 만들지 않는 이유는 화면과 데이터가 갈라지지 않게 하기
  // 위해서다. 서버가 그린 것 중에서 고르기만 한다.
  // 초기값은 서버가 이미 눌러 둔 버튼에서 읽는다. 여기에 'hi' 를 박으면
  // 설정(newhigh.default_basis)을 바꿔도 화면이 안 따라온다.
  var pressed = document.querySelector('.basis-sw button[aria-pressed=true]');
  var basis = (pressed && pressed.dataset.basis) || 'hi';
  function applyBasis() {
    document.querySelectorAll('.basis-sw button').forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.basis === basis);
    });
    // 머리말의 '기준' 표기도 함께 바꾼다. 표는 종가인데 위에는 고가라고 적혀
    // 있으면 그 화면 전체를 못 믿는다.
    document.querySelectorAll('.stamp span[data-b], #p1 td.kind span[data-b]').forEach(function (sp) {
      sp.hidden = sp.dataset.b !== basis;
    });
    // 알약의 수도 함께 바꾼다. 하나만 두면 표와 어긋난 숫자가 남는다.
    document.querySelectorAll('.pill[data-kind]').forEach(function (p) {
      var n = p.dataset[basis === 'hi' ? 'cHi' : 'cCl'];
      var c = p.querySelector('.c');
      if (n !== undefined && c) c.textContent = n;
    });
    applyFilters();
  }
  document.querySelectorAll('.basis-sw button').forEach(function (b) {
    b.addEventListener('click', function () { basis = b.dataset.basis; applyBasis(); });
  });

  function applyFilters() {
    var kinds = {};
    document.querySelectorAll('.pill[data-kind]').forEach(function (p) {
      kinds[p.dataset.kind] = p.getAttribute('aria-pressed') === 'true';
    });
    var byTurn = document.querySelector('.pill[data-filter=turnover]');
    var byCommon = document.querySelector('.pill[data-filter=common]');
    var wantTurn = byTurn && byTurn.getAttribute('aria-pressed') === 'true';
    // 우선주·스팩·리츠를 끈다. 표시가 없는 행(옛 보드)은 보통주로 본다 —
    // 알 수 없는 것을 숨기면 행이 조용히 사라진다.
    var wantCommon = byCommon && byCommon.getAttribute('aria-pressed') === 'true';

    document.querySelectorAll('#p1 tbody tr').forEach(function (tr) {
      if (tr.classList.contains('theme-row')) return;
      var ok = true;
      // 라벨 알약은 **달성 표에만** 건다. 근접 표의 행은 라벨 자체가 없어서
      // 조건 없이 걸면 '라벨이 없다' 로 전부 숨겨진다 — 실제로 근접 표가
      // 로드되자마자 통째로 사라지고 있었다.
      if ('hi' in tr.dataset || 'cl' in tr.dataset) {
        // 고른 기준에서 라벨이 없는 종목은 그날 그 기준의 신고가가 아니다.
        // 알약에 없는 등급(설정의 min_display_kind 아래, 예: 20일)도 뺀다 —
        // 알약이 없으니 끌 수도 없고, 상단 카운트와 표의 행수가 어긋난다.
        var kind = tr.dataset[basis === 'hi' ? 'hi' : 'cl'];
        if (!kind || !(kind in kinds)) ok = false;
        else if (kinds[kind] === false) ok = false;
      }
      if (wantTurn) {
        ok = ok && parseFloat(tr.dataset.turnover || 0) >= parseFloat(byTurn.dataset.min || 0);
      }
      if (wantCommon) ok = ok && (tr.dataset.skind || 'common') === 'common';
      tr.style.display = ok ? '' : 'none';
    });
    // 구성 종목이 전부 숨겨진 테마 헤더도 같이 숨긴다.
    document.querySelectorAll('#p1 tr.theme-row').forEach(function (h) {
      var n = 0, x = h.nextElementSibling;
      while (x && !x.classList.contains('theme-row')) {
        if (x.style.display !== 'none') n++;
        x = x.nextElementSibling;
      }
      h.style.display = n ? '' : 'none';
    });
  }
  document.querySelectorAll('.pill').forEach(function (p) {
    p.addEventListener('click', function () {
      p.setAttribute('aria-pressed', p.getAttribute('aria-pressed') !== 'true');
      applyFilters();
    });
  });
  applyBasis();

  // ── 히트맵 ────────────────────────────────────────────
  var el = document.getElementById('hm-data');
  if (!el) return;
  var DATA = JSON.parse(el.textContent);

  function tone(v) {
    if (v === null || v === undefined) return ['#EDEFF1', '#6B7280'];
    if (v >= 3) return ['#DC6660', '#3B0F0C'];
    if (v >= 1.5) return ['#EFAAA6', '#5C1713'];
    if (v > 0) return ['#F8E0DE', '#7A231D'];
    if (v === 0) return ['#EDEFF1', '#4B5563'];
    if (v > -1.5) return ['#DCE9F8', '#123F72'];
    if (v > -3) return ['#9DC4EE', '#0E3560'];
    return ['#5A9BE0', '#082645'];
  }

  // 셀 면적은 그룹 안에서의 '순위'로 정한다. 거래대금 절대값 비율을 그대로 쓰면
  // 1등이 그리드를 통째로 먹고 나머지가 전부 한 칸이 되어 종목명이 안 읽힌다.
  // 셀은 이미 거래대금 내림차순으로 넘어온다.
  function spans(n, mono) {
    var out = [];
    for (var i = 0; i < n; i++) {
      if (mono) out.push([1, 1]);
      else if (i === 0) out.push([3, 2]);
      else if (i === 1) out.push([3, 1]);
      else if (i < 4) out.push([2, 1]);
      else out.push([1, 1]);
    }
    return out;
  }

  // 종목명·섹터명은 외부 소스에서 온 값이라 내용을 통제하지 못한다. 문자열을
  // 이어 붙여 innerHTML 로 넣으므로 여기서 막아야 한다. 서버 쪽 렌더는 모든
  // 값을 escape 하는데 여기만 안 하고 있었다.
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmt(v) {
    if (v === null || v === undefined) return '–';
    return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
  }

  // ── 재무 카드 (D-075) ──────────────────────────────────
  // 칸에 마우스를 올리면 시총과 DART 연간·분기 매출·영업이익을 억원으로 보여 준다.
  // 숫자는 서버가 실어 준 것(hm-data 의 fin)만 그대로 적는다. 여기서 계산하는 것은
  // 영업이익률 하나이고, 매출이 없으면 그것도 비운다. 없는 값은 '–'.
  var CELLS = [];
  var tipEl = document.createElement('div');
  tipEl.className = 'hm-tip';
  tipEl.hidden = true;
  document.body.appendChild(tipEl);

  function fmtEok(v) {
    if (v === null || v === undefined || isNaN(v)) return '–';
    if (Math.abs(v) >= 10000) return (v / 10000).toFixed(2) + '조';
    return Math.round(v).toLocaleString() + '억';
  }
  function fmtPct(op, rev) {
    if (op === null || op === undefined || !rev) return '–';
    return (op / rev * 100).toFixed(1) + '%';
  }
  function finHtml(c) {
    var f = c.fin;
    var h = '<div class="t"><b>' + esc(c.name) + '</b>' +
      (c.mktcap ? '<span> 시총 ' + fmtEok(c.mktcap) + '</span>' : '') +
      (c.turnover ? '<span> · 거래대금 ' + fmtEok(c.turnover) + '</span>' : '') + '</div>';
    if (!f) return h + '<div class="mut">재무 데이터 없음 (DART 미수집)</div>';
    function tbl(title, rows) {
      if (!rows.length) return '';
      return '<div class="cap">' + title + '</div><table><tr><th></th><th>매출</th><th>영업이익</th><th>OPM</th></tr>' +
        rows.map(function (r) { return r; }).join('') + '</table>';
    }
    var a = (f.a || []).map(function (r) {
      return '<tr><td>' + esc(r[0]) + '</td><td>' + fmtEok(r[1]) + '</td><td class="' +
        (r[2] < 0 ? 'down' : '') + '">' + fmtEok(r[2]) + '</td><td>' + fmtPct(r[2], r[1]) + '</td></tr>';
    });
    var q = (f.q || []).map(function (r) {
      return '<tr><td>' + esc(r[0]) + (r[3] ? '<sup title="직전 분기 보고서가 없어 차분 못 함">*</sup>' : '') +
        '</td><td>' + fmtEok(r[1]) + '</td><td class="' + (r[2] < 0 ? 'down' : '') + '">' +
        fmtEok(r[2]) + '</td><td>' + fmtPct(r[2], r[1]) + '</td></tr>';
    });
    return h + tbl('연간 (억원)', a) + tbl('분기 (억원)', q) +
      '<div class="src">DART ' + esc(f.fs === 'OFS' ? '별도' : '연결') + ' · 받은 날 ' + esc(f.d || '') + '</div>';
  }
  function placeTip(ev) {
    var x = ev.clientX + 14, y = ev.clientY + 14;
    var w = tipEl.offsetWidth, hgt = tipEl.offsetHeight;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14;
    if (y + hgt > window.innerHeight - 8) y = ev.clientY - hgt - 14;
    tipEl.style.left = Math.max(4, x) + 'px';
    tipEl.style.top = Math.max(4, y) + 'px';
  }
  var hmHost = document.getElementById('hm');
  if (hmHost) {
    hmHost.addEventListener('mouseover', function (ev) {
      var cell = ev.target.closest ? ev.target.closest('.cell') : null;
      if (!cell) return;
      var c = CELLS[+cell.dataset.k];
      if (!c) return;
      tipEl.innerHTML = finHtml(c);
      tipEl.hidden = false;
      placeTip(ev);
    });
    hmHost.addEventListener('mousemove', function (ev) { if (!tipEl.hidden) placeTip(ev); });
    hmHost.addEventListener('mouseleave', function () { tipEl.hidden = true; });
  }

  function draw() {
    CELLS = [];
    var groupBy = document.getElementById('groupBy').value;
    var sizeBy = document.getElementById('sizeBy').value;
    var colorBy = document.getElementById('colorBy').value;
    var nhOn = document.getElementById('nhOn').value === '1';
    var groups = DATA[groupBy] || [];
    var host = document.getElementById('hm');
    if (!groups.length) {
      host.innerHTML = '<div class="card empty">히트맵에 넣을 데이터가 없습니다.</div>';
      return;
    }
    host.innerHTML = groups.map(function (g) {
      // 크기 기준이 바뀌면 정렬도 같이 바뀌어야 면적 순서가 맞는다.
      var cells = g.cells.slice();
      if (sizeBy !== 'mono') {
        cells.sort(function (a, b) { return (b[sizeBy] || 0) - (a[sizeBy] || 0); });
      }
      var sp = spans(cells.length, sizeBy === 'mono');
      var body = cells.map(function (c, i) {
        var v = c[colorBy];
        var t = tone(v);
        var big = i === 0 && sizeBy !== 'mono' ? ' big' : '';
        var nh = nhOn && c.newhigh ? ' nh' : '';
        var tip = c.name + ' ' + fmt(v) +
          (c.newhigh ? ' · 신고가' : '') +
          (c.turnover ? ' · 거래대금 ' + Math.round(c.turnover).toLocaleString() + '억' : '');
        // 재무 카드가 뜨는 칸은 브라우저 기본 툴팁(title)을 끈다 — 둘이 겹쳐 뜬다.
        var key = String(CELLS.length);
        CELLS.push(c);
        return '<div class="cell' + big + nh + '" data-k="' + key + '" style="grid-column:span ' + sp[i][0] +
          ';grid-row:span ' + sp[i][1] + ';background:' + t[0] + ';color:' + t[1] +
          '"' + (c.fin ? '' : ' title="' + esc(tip) + '"') + '>' +
          '<span class="n">' + esc(c.name) + '</span>' +
          '<span class="p">' + fmt(v) + '</span></div>';
      }).join('');
      var cls = (g.chg_pct || 0) >= 0 ? 'up' : 'down';
      return '<div class="hm-group"><div class="hm-cap"><b>' + esc(g.group) + '</b>' +
        '<span class="num ' + cls + '">' + fmt(g.chg_pct) + '</span></div>' +
        '<div class="hm-cells">' + body + '</div></div>';
    }).join('');
  }

  ['sizeBy', 'colorBy', 'groupBy', 'nhOn'].forEach(function (id) {
    document.getElementById(id).addEventListener('change', draw);
  });
  draw();
})();

// ── 날짜 선택기 ──────────────────────────────────────────
// 보관 목록을 런타임에 읽는다. 페이지에 박으면 어제 만든 보관본의 선택기가
// 오늘 날짜를 모른 채 과거에 멈춰 있다.
(function () {
  'use strict';
  var sel = document.getElementById('daysel');
  if (!sel) return;
  var cur = sel.dataset.asof || '';
  // 루트(index.html)와 보관본(d/YYYY-MM-DD.html)은 깊이가 다르다.
  var inDays = /\/d\/[^/]*$/.test(location.pathname);
  var base = inDays ? '' : 'd/';
  fetch(base + 'index.json', { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) {
      if (!j || !j.dates || !j.dates.length) return;
      sel.innerHTML = '';
      j.dates.forEach(function (d, i) {
        var o = document.createElement('option');
        o.value = d;
        o.textContent = d + (i === 0 ? ' (최신)' : '');
        if (d === cur) o.selected = true;
        sel.appendChild(o);
      });
      sel.onchange = function () {
        var d = sel.value;
        if (!d) return;
        // 최신은 루트로 보낸다. 주소가 항상 같아야 링크를 공유할 수 있다.
        if (d === j.latest) location.href = inDays ? '../' : './';
        else location.href = (inDays ? '' : 'd/') + d + '.html';
      };
    })
    .catch(function () { /* 목록을 못 읽으면 선택기는 오늘 하루만 보여 준다 */ });

  // ── 표 정렬 ───────────────────────────────────────────
  // 머리(th)를 누르면 그 열로 정렬한다. 한 번 더 누르면 방향이 뒤집힌다.
  // 데이터는 서버가 그린 그대로다 — 행의 순서만 바꾼다. 수치 칸은 서버가
  // data-v 에 실어 준 원값을 쓰고, 없으면 화면 문자열에서 숫자를 읽는다
  // (2,006억 · 3.36조 · +9.65% · 1.3배 · '–'). 테마 묶음 표(달성)는 묶음 머리
  // (tr.theme-row) 를 경계로 **묶음 안에서만** 정렬한다 — 테마별로 읽는 표다.
  function cellValue(td) {
    if (!td) return NaN;
    if (td.dataset.v !== undefined) { var d = parseFloat(td.dataset.v); return isNaN(d) ? NaN : d; }
    var t = (td.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t || t === '–' || t === '—' || t === '-') return NaN;
    var m = t.match(/^[+\-]?\d[\d,]*(?:\.\d+)?/);
    if (!m) return NaN;
    var v = parseFloat(m[0].replace(/,/g, ''));
    if (t.slice(m[0].length).charAt(0) === '조') v *= 10000;   // 억 단위로 맞춘다
    return v;
  }
  function cellText(td) { return td ? (td.textContent || '').trim() : ''; }
  function sortSegment(rows, idx, dir, numeric) {
    var keyed = rows.map(function (tr, i) {
      var td = tr.children[idx];
      return { tr: tr, i: i, n: cellValue(td), s: cellText(td) };
    });
    keyed.sort(function (a, b) {
      if (numeric) {
        var an = isNaN(a.n), bn = isNaN(b.n);
        if (an && bn) return a.i - b.i;
        if (an) return 1;                   // 계산 안 된 값('–')은 항상 맨 아래
        if (bn) return -1;
        if (a.n !== b.n) return dir * (a.n - b.n);
      } else {
        var c = a.s.localeCompare(b.s, 'ko');
        if (c) return dir * c;
      }
      return a.i - b.i;                     // 같으면 원래 순서 (안정 정렬)
    });
    return keyed.map(function (k) { return k.tr; });
  }
  function sortTable(table, idx) {
    var th = table.tHead.rows[0].children[idx];
    var body = table.tBodies[0];
    if (!body) return;
    var rows = Array.prototype.slice.call(body.rows);
    // 숫자 열인지: 값이 읽히는 칸이 하나라도 있으면 숫자 열로 본다.
    var numeric = rows.some(function (tr) {
      return !tr.classList.contains('theme-row') && !isNaN(cellValue(tr.children[idx]));
    });
    var cur = th.getAttribute('aria-sort');
    // 숫자는 큰 것부터, 글자는 가나다순이 첫 클릭. 다시 누르면 반대.
    var dir = cur ? (cur === 'descending' ? 1 : -1) : (numeric ? -1 : 1);
    Array.prototype.forEach.call(table.tHead.rows[0].children, function (h) { h.removeAttribute('aria-sort'); });
    th.setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');

    var out = [], seg = [];
    function flush() { if (seg.length) { out = out.concat(sortSegment(seg, idx, dir, numeric)); seg = []; } }
    rows.forEach(function (tr) {
      if (tr.classList.contains('theme-row')) { flush(); out.push(tr); }
      else seg.push(tr);
    });
    flush();
    out.forEach(function (tr) { body.appendChild(tr); });
  }
  document.querySelectorAll('.panel table').forEach(function (table) {
    if (!table.tHead || !table.tHead.rows.length || table.classList.contains('draft-tbl')) return;
    table.classList.add('sortable');
    Array.prototype.forEach.call(table.tHead.rows[0].children, function (th, idx) {
      th.tabIndex = 0;
      th.title = '누르면 이 열로 정렬';
      th.addEventListener('click', function () { sortTable(table, idx); });
      th.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); sortTable(table, idx); }
      });
    });
  });
})();
