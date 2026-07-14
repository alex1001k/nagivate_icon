/* ---------------------------------------------------------------------
   Дэшборд показателей — макет. Вся логика на чистом JS, данные — из
   встроенного JSON (см. #dashboard-data в HTML).
--------------------------------------------------------------------- */
(function () {
  'use strict';

  var DATA = JSON.parse(document.getElementById('dashboard-data').textContent);

  // Порядок групп "по умолчанию" -- под синтетические демо-данные. Если в реальных
  // table_rows встречаются другие значения screen/category (почти наверняка так и
  // будет), они не пропадают -- см. uniqueOrdered(): известные идут первыми в этом
  // порядке, всё остальное, что реально есть в данных, добавляется следом по алфавиту.
  var SCREEN_ORDER = ['Онлайн', 'Инвестиции', 'Кидс', 'Цифровой ассистент'];
  var CATEGORY_ORDER = ['Клиентские', 'Финансовые', 'Вовлечённость', 'Удовлетворённость'];
  var HUES = ['#2a78d6', '#1baf7a', '#eda100', '#008300', '#4a3aa7', '#e34948', '#e87ba4', '#eb6834'];

  function uniqueOrdered(rows, key, preferredOrder) {
    var seen = {};
    var extra = [];
    rows.forEach(function (r) {
      var v = r[key];
      if (v === undefined || v === null || v === '') return;
      if (!seen[v]) {
        seen[v] = true;
        if (preferredOrder.indexOf(v) === -1) extra.push(v);
      }
    });
    extra.sort(function (a, b) { return String(a).localeCompare(String(b), 'ru'); });
    return preferredOrder.filter(function (v) { return seen[v]; }).concat(extra);
  }

  var MONTH_FULL = {1:'январь',2:'февраль',3:'март',4:'апрель',5:'май',6:'июнь',7:'июль',
    8:'август',9:'сентябрь',10:'октябрь',11:'ноябрь',12:'декабрь'};

  var ICON_STAR = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.9 6.26L22 9.27l-5 4.87L18.18 21 12 17.27 5.82 21 7 14.14l-5-4.87 7.1-1.01L12 2z"/></svg>';
  var ICON_TARGET = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/></svg>';
  var ICON_CHEVRON = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 9l6 6 6-6"/></svg>';

  // -----------------------------------------------------------------
  // Индексы для быстрого доступа
  // -----------------------------------------------------------------
  var rowsById = {};
  DATA.table_rows.forEach(function (r) { rowsById[r.id_pokaz] = r; });

  var briefsByMetric = {};
  DATA.briefs.forEach(function (b) {
    (briefsByMetric[b.id_pokaz] = briefsByMetric[b.id_pokaz] || []).push(b);
  });
  Object.keys(briefsByMetric).forEach(function (k) {
    briefsByMetric[k].sort(function (a, b) { return a.date_ < b.date_ ? 1 : -1; });
  });

  var factsByMetric = {};
  DATA.facts.forEach(function (f) {
    (factsByMetric[f.id_pokaz] = factsByMetric[f.id_pokaz] || []).push(f);
  });
  Object.keys(factsByMetric).forEach(function (k) {
    factsByMetric[k].sort(function (a, b) { return a.date_ < b.date_ ? -1 : 1; });
  });

  var factsByKey = {};
  DATA.facts.forEach(function (f) { factsByKey[f.id_pokaz + '|' + f.date_] = f; });

  // -----------------------------------------------------------------
  // Состояние
  // -----------------------------------------------------------------
  var initialId = (DATA.summary.top_deviation && DATA.summary.top_deviation.id_pokaz) || DATA.table_rows[0].id_pokaz;
  var initialBriefIndex = 0;
  if (DATA.summary.top_deviation && briefsByMetric[initialId]) {
    var idx = briefsByMetric[initialId].findIndex(function (b) { return b.date_ === DATA.summary.top_deviation.date_; });
    if (idx >= 0) initialBriefIndex = idx;
  }

  var state = {
    groupMode: 'screen',
    filterImportant: false,
    filterTarget: false,
    selectedId: initialId,
    briefIndex: initialBriefIndex,
    collapsed: {}
  };

  // -----------------------------------------------------------------
  // Форматирование
  // -----------------------------------------------------------------
  function fmtNum(v, unit) {
    if (v === null || v === undefined) return '—';
    if (unit === '%') return v.toFixed(1) + '%';
    if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString('ru-RU');
    return v.toFixed(1);
  }

  var MOM_NEUTRAL_BAND = 1.5;   // |МоМ%| ниже — считаем "на уровне пред. месяца", не красим
  var PLAN_NEUTRAL_BAND = 4;    // |откл. от плана| ниже — считаем "план выполнен", не красим

  function judgeGood(pct, direction, band) {
    if (pct === null || pct === undefined) return null;
    if (Math.abs(pct) < band) return null; // нейтрально: слишком маленькое отклонение, чтобы красить
    return direction === 'up' ? pct > 0 : pct < 0;
  }

  function fmtPct1(v) {
    if (v === null || v === undefined) return '—';
    return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
  }

  function monthLabelFull(date_) {
    var d = new Date(date_);
    return MONTH_FULL[d.getMonth() + 1] + ' ' + d.getFullYear();
  }

  // -----------------------------------------------------------------
  // Группировка таблицы
  // -----------------------------------------------------------------
  function buildGroups() {
    var rows = DATA.table_rows.filter(function (r) {
      if (state.filterImportant && !r.is_important) return false;
      if (state.filterTarget && !r.has_target) return false;
      return true;
    });

    var outerKey = state.groupMode === 'screen' ? 'screen' : 'category';
    var innerKey = state.groupMode === 'screen' ? 'category' : 'screen';
    // порядок считаем по ПОЛНОМУ набору строк (DATA.table_rows), а не по уже
    // отфильтрованным rows -- иначе список групп/подгрупп будет "прыгать" при
    // переключении фильтров "только важные"/"только с целью"
    var outerOrder = uniqueOrdered(DATA.table_rows, outerKey, state.groupMode === 'screen' ? SCREEN_ORDER : CATEGORY_ORDER);
    var innerOrder = uniqueOrdered(DATA.table_rows, innerKey, state.groupMode === 'screen' ? CATEGORY_ORDER : SCREEN_ORDER);

    var groups = outerOrder.map(function (ok, i) {
      var outerRows = rows.filter(function (r) { return r[outerKey] === ok; });
      var subgroups = innerOrder.map(function (ik) {
        return { key: ik, rows: outerRows.filter(function (r) { return r[innerKey] === ik; }) };
      }).filter(function (sg) { return sg.rows.length > 0; });
      return { key: ok, color: HUES[i % HUES.length], subgroups: subgroups, count: outerRows.length };
    }).filter(function (g) { return g.count > 0; });

    return groups;
  }

  // -----------------------------------------------------------------
  // Рендер таблицы
  // -----------------------------------------------------------------
  function deltaChip(pct, good) {
    if (pct === null || pct === undefined) return '<span class="muted">—</span>';
    var cls = good === true ? 'good' : (good === false ? 'bad' : 'flat');
    var arrow = pct > 0 ? '▲' : (pct < 0 ? '▼' : '·');
    return '<span class="delta ' + cls + '">' + arrow + ' ' + fmtPct1(pct) + '</span>';
  }

  function planMeterCell(row) {
    if (row.plan_pct === null || row.plan_pct === undefined) return '<span class="muted" title="Нет плана на этот месяц">—</span>';
    var completion = 100 + row.plan_pct; // plan_pct = (fact/plan-1)*100 -> completion = fact/plan*100
    var pct = Math.max(0, Math.min(130, completion));
    var judge = judgeGood(row.plan_pct, row.direction, PLAN_NEUTRAL_BAND);
    var cls = judge === true ? 'good' : (judge === false ? 'bad' : 'flat');
    return '' +
      '<div class="meter-wrap">' +
        '<div class="meter ' + cls + '"><span style="width:' + Math.min(100, pct) + '%"></span></div>' +
        '<span class="delta ' + cls + '" style="padding:1px 5px;">' + completion.toFixed(0) + '%</span>' +
      '</div>';
  }

  function monthShort(date_) {
    var MS = {1:'янв',2:'фев',3:'мар',4:'апр',5:'май',6:'июн',7:'июл',8:'авг',9:'сен',10:'окт',11:'ноя',12:'дек'};
    var d = new Date(date_);
    return MS[d.getMonth() + 1] + ' ' + String(d.getFullYear()).slice(2);
  }

  var COLSPAN = 12;

  function renderTableHead() {
    var fm = DATA.table_months;   // [мес-3, мес-2, мес-1, текущий]
    var pm = DATA.forecast_months; // [мес-1, текущий]
    var groupRow = '' +
      '<tr class="thead-group">' +
        '<th colspan="3"></th>' +
        '<th colspan="' + fm.length + '" class="grp-fact">Факт</th>' +
        '<th colspan="' + pm.length + '" class="grp-forecast">Прогноз</th>' +
        '<th colspan="3"></th>' +
      '</tr>';
    var colsRow = '' +
      '<tr class="thead-cols">' +
        '<th style="width:26px"></th><th style="width:26px"></th><th>Показатель</th>' +
        fm.map(function (m) { return '<th class="num">' + m.label + '</th>'; }).join('') +
        pm.map(function (m) { return '<th class="num">' + m.label + '</th>'; }).join('') +
        '<th class="num">Динамика</th><th class="num">План</th><th class="num">Вып. плана</th>' +
      '</tr>';
    document.getElementById('tableHead').innerHTML = groupRow + colsRow;
  }

  function renderTableRow(row) {
    var factsHtml = row.facts_by_month.map(function (v) {
      if (v === null || v === undefined) return '<td class="num muted">—</td>';
      return '<td class="num">' + fmtNum(v, row.unit) + '</td>';
    }).join('');

    var forecastHtml = row.forecast_by_month.map(function (v) {
      if (v === null || v === undefined) return '<td class="num muted">—</td>';
      return '<td class="num prognoz-val">' + fmtNum(v, row.unit) + '</td>';
    }).join('');

    var planHtml = (row.plan_last === null || row.plan_last === undefined) ?
      '<td class="num muted">—</td>' :
      '<td class="num' + (row.latest_is_prognoz ? ' prognoz-val' : '') + '">' + fmtNum(row.plan_last, row.unit) + '</td>';

    var selected = row.id_pokaz === state.selectedId ? ' selected' : '';

    return '' +
      '<tr class="metric-row' + selected + '" data-id="' + row.id_pokaz + '">' +
        '<td>' + '<span class="icon-star ' + (row.is_important ? '' : 'off') + '" title="' + (row.is_important ? 'Важный показатель' : '') + '">' + ICON_STAR + '</span>' + '</td>' +
        '<td>' + '<span class="icon-target ' + (row.has_target ? '' : 'off') + '" title="' + (row.has_target ? 'Есть цель' : 'Цели нет') + '">' + ICON_TARGET + '</span>' + '</td>' +
        '<td><div class="name-cell"><span class="metric-name">' + row.pokaz_name + '</span><span class="metric-sub">' + row.unit + '</span></div></td>' +
        factsHtml +
        forecastHtml +
        '<td class="num">' + deltaChip(row.mom_pct, judgeGood(row.mom_pct, row.direction, MOM_NEUTRAL_BAND)) + '</td>' +
        planHtml +
        '<td class="num">' + planMeterCell(row) + '</td>' +
      '</tr>';
  }

  function renderTable() {
    var groups = buildGroups();
    var html = groups.map(function (g) {
      var gKey = state.groupMode + '|' + g.key;
      var collapsed = !!state.collapsed[gKey];
      var rowsHtml = '';
      if (!collapsed) {
        rowsHtml = g.subgroups.map(function (sg) {
          var subHeader = '<tr class="subgroup-row"><td colspan="' + COLSPAN + '">' + sg.key + ' · ' + sg.rows.length + '</td></tr>';
          var body = sg.rows.map(renderTableRow).join('');
          return subHeader + body;
        }).join('');
      }
      return '' +
        '<tr class="group-row' + (collapsed ? ' collapsed' : '') + '" data-gkey="' + gKey + '">' +
          '<td colspan="' + COLSPAN + '">' +
            '<span class="collapse-caret">' + ICON_CHEVRON + '</span>' +
            '<span class="grp-dot" style="background:' + g.color + '"></span>' +
            g.key + ' <span class="muted">· ' + g.count + '</span>' +
          '</td>' +
        '</tr>' + rowsHtml;
    }).join('');

    document.getElementById('tableBody').innerHTML = html || '<tr><td colspan="' + COLSPAN + '" class="empty-state">Нет показателей под текущие фильтры</td></tr>';
  }

  // -----------------------------------------------------------------
  // KPI-плашки
  // -----------------------------------------------------------------
  function renderKpis() {
    var s = DATA.summary;
    var tiles = [
      { label: 'Всего метрик', value: s.total_metrics, sub: '<b>' + s.meeting_plan + '</b> из ' + s.plan_base + ' выполняют план' },
      { label: 'Всего целей', value: s.total_with_target, sub: '<b>' + s.on_track_count + '</b> идут по графику' },
      { label: 'Важных показателей', value: s.total_important, sub: '<b>' + s.important_with_deviation + '</b> с отклонением сейчас' },
      { label: 'Отклонений за 3 мес.', value: s.deviations_recent_3m, sub: 'по ' + s.total_metrics + ' метрикам' }
    ];
    document.getElementById('kpiRow').innerHTML = tiles.map(function (t) {
      return '<div class="card kpi-tile"><div class="kpi-label">' + t.label + '</div><div class="kpi-value">' + t.value + '</div><div class="kpi-sub">' + t.sub + '</div></div>';
    }).join('');
  }

  function renderMethodology() {
    var rows = DATA.rules_meta.map(function (r) {
      return '<tr><td>' + r.label + '</td><td>' + r.rule + '</td></tr>';
    }).join('');
    document.getElementById('methodologyPanel').innerHTML =
      '<div style="font-weight:600;color:var(--text-primary);margin-bottom:2px;">Как определяются отклонения</div>' +
      '<div>Каждый месяц по каждому показателю независимо проверяется 7 статистических правил. Если хотя бы одно сработало — показатель получает брифы с деталями; сила самого сильного правила определяет серьёзность (низкая / заметная / критичная).</div>' +
      '<table>' + rows + '</table>';
  }

  document.getElementById('methodologyToggle').addEventListener('click', function () {
    document.getElementById('methodologyPanel').classList.toggle('open');
  });

  // -----------------------------------------------------------------
  // Парсинг брифов: формат хранения -- plain text, строки через "\n",
  // буллеты "• ", раскраска смысла через {color:ColorWarningRed}...{color} /
  // {color:ColorGreen}...{color} (тот же формат уходит в BI-CSV, здесь просто
  // превращаем его в подсвеченный HTML для превью).
  // -----------------------------------------------------------------
  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  var COLOR_MAP = { ColorWarningRed: 'var(--critical)', ColorGreen: 'var(--good-text)' };

  function renderBriefText(text) {
    var lines = text.split('\n').filter(Boolean);
    var items = lines.map(function (line) {
      var body = line.replace(/^•\s*/, '');
      body = escapeHtml(body);
      body = body.replace(/\{color:(\w+)\}([\s\S]*?)\{color\}/g, function (_, colorName, inner) {
        var css = COLOR_MAP[colorName] || 'inherit';
        return '<span style="color:' + css + ';font-weight:600;">' + inner + '</span>';
      });
      return '<li>' + body + '</li>';
    });
    return '<ul>' + items.join('') + '</ul>';
  }

  // -----------------------------------------------------------------
  // Правая карточка: выбранный показатель + брифы (с листанием)
  // -----------------------------------------------------------------
  function renderHighlight() {
    var row = rowsById[state.selectedId];
    var el = document.getElementById('highlightCard');
    if (!row) { el.innerHTML = ''; return; }

    var briefs = briefsByMetric[row.id_pokaz] || [];
    if (!briefs.length) {
      el.innerHTML = '' +
        '<div class="eyebrow"><span>' + row.pokaz_name + ' · ' + row.screen + '</span></div>' +
        '<h3>Значимых отклонений не зафиксировано</h3>' +
        '<div class="brief-text">По выбранному показателю за отслеживаемый период нет отклонений, требующих внимания.</div>';
      return;
    }

    if (state.briefIndex >= briefs.length) state.briefIndex = 0;
    var b = briefs[state.briefIndex];
    var rec = factsByKey[b.id_pokaz + '|' + b.date_];

    var chips = '';
    if (rec) {
      if (rec.mom_pct !== null && rec.mom_pct !== undefined) chips += deltaChip(rec.mom_pct, judgeGood(rec.mom_pct, row.direction, MOM_NEUTRAL_BAND));
      if (rec.plan_pct !== null && rec.plan_pct !== undefined) chips += ' ' + deltaChip(rec.plan_pct, judgeGood(rec.plan_pct, row.direction, PLAN_NEUTRAL_BAND));
    }

    el.innerHTML = '' +
      '<div class="eyebrow"><span>' + row.pokaz_name + ' · ' + row.screen + ' · ' + monthLabelFull(b.date_) + '</span>' +
        '<span>' + (b.severity === 'high' ? 'Критично' : b.severity === 'medium' ? 'Заметно' : b.severity === 'low' ? 'Незначительно' : 'Инфо') + '</span></div>' +
      '<h3>' + b.headline + '</h3>' +
      '<div class="detail-chips">' + chips + '</div>' +
      '<div class="brief-text">' + renderBriefText(b.text) + '</div>' +
      '<div class="brief-pager">' +
        '<button id="briefPrev" ' + (state.briefIndex === 0 ? 'disabled' : '') + '>‹</button>' +
        '<span class="count">' + (state.briefIndex + 1) + ' / ' + briefs.length + '</span>' +
        '<button id="briefNext" ' + (state.briefIndex === briefs.length - 1 ? 'disabled' : '') + '>›</button>' +
      '</div>';

    var prevBtn = document.getElementById('briefPrev');
    var nextBtn = document.getElementById('briefNext');
    if (prevBtn) prevBtn.addEventListener('click', function () { state.briefIndex--; renderHighlight(); });
    if (nextBtn) nextBtn.addEventListener('click', function () { state.briefIndex++; renderHighlight(); });
  }

  // -----------------------------------------------------------------
  // График: строка % выполнения плана + столбцы факт/план
  // -----------------------------------------------------------------
  function buildChartSvg(records, direction) {
    var W = 620, H = 210;
    var padL = 34, padR = 10, padTop = 6;
    var lineH = 46, gap = 10, barH = 118, axisH = 18;
    var plotW = W - padL - padR;

    var n = records.length;
    var slot = plotW / n;
    var barW = Math.min(24, slot * 0.32);

    // --- line strip: % выполнения плана ---
    var completions = records.map(function (r) {
      return r.plan !== null && r.plan !== undefined && r.plan !== 0 ? (r.fact / r.plan) * 100 : null;
    });
    var haveCompletion = completions.some(function (v) { return v !== null; });
    var cVals = completions.filter(function (v) { return v !== null; });
    var cMin = cVals.length ? Math.min.apply(null, cVals.concat([100])) : 80;
    var cMax = cVals.length ? Math.max.apply(null, cVals.concat([100])) : 120;
    var cPad = Math.max(8, (cMax - cMin) * 0.15);
    var cLo = cMin - cPad, cHi = cMax + cPad;
    function cY(v) { return padTop + lineH - ((v - cLo) / (cHi - cLo)) * lineH; }
    function cX(i) { return padL + slot * i + slot / 2; }

    var linePath = '';
    var lineSegs = [];
    var curSeg = [];
    completions.forEach(function (v, i) {
      if (v === null) {
        if (curSeg.length) { lineSegs.push(curSeg); curSeg = []; }
        return;
      }
      curSeg.push([cX(i), cY(v)]);
    });
    if (curSeg.length) lineSegs.push(curSeg);
    linePath = lineSegs.map(function (seg) {
      return '<path d="M' + seg.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' L') + '" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
    }).join('');
    var lineDots = lineSegs.map(function (seg) {
      return seg.map(function (p) {
        return '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3" fill="var(--series-1)" stroke="var(--surface-1)" stroke-width="2"/>';
      }).join('');
    }).join('');
    var refLineY = cY(100);
    var refLine = '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + refLineY.toFixed(1) + '" y2="' + refLineY.toFixed(1) + '" stroke="var(--axis)" stroke-width="1" stroke-dasharray="2,3"/>' +
      '<text x="' + (W - padR) + '" y="' + (refLineY - 3).toFixed(1) + '" text-anchor="end" font-size="9" fill="var(--text-muted)">план 100%</text>';

    // --- bar chart: факт / план ---
    var barTop = padTop + lineH + gap;
    var maxVal = 0;
    records.forEach(function (r) {
      maxVal = Math.max(maxVal, r.fact || 0, r.plan || 0);
    });
    maxVal = maxVal * 1.15 || 1;
    function bY(v) { return barTop + barH - (v / maxVal) * barH; }

    var bars = records.map(function (r, i) {
      var cx = cX(i);
      var out = '';
      if (r.plan !== null && r.plan !== undefined) {
        var ph = barTop + barH - bY(r.plan);
        out += '<rect x="' + (cx - barW / 2 - 1).toFixed(1) + '" y="' + bY(r.plan).toFixed(1) + '" width="' + (barW / 2 - 1).toFixed(1) + '" height="' + ph.toFixed(1) + '" rx="3" fill="var(--series-2)" opacity="0.85"/>';
      }
      var fh = barTop + barH - bY(r.fact);
      var op = r.prognoz_flg ? 0.45 : 1;
      out += '<rect x="' + (cx + 1).toFixed(1) + '" y="' + bY(r.fact).toFixed(1) + '" width="' + (barW / 2 - 1).toFixed(1) + '" height="' + fh.toFixed(1) + '" rx="3" fill="var(--series-1)" opacity="' + op + '"/>';
      return out;
    }).join('');

    var baseline = '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + (barTop + barH) + '" y2="' + (barTop + barH) + '" stroke="var(--axis)" stroke-width="1"/>';

    var xLabels = records.map(function (r, i) {
      return '<text x="' + cX(i).toFixed(1) + '" y="' + (barTop + barH + axisH - 4) + '" text-anchor="middle" font-size="9.5" fill="var(--text-muted)">' + monthShort(r.date_) + (r.prognoz_flg ? '*' : '') + '</text>';
    }).join('');

    return '' +
      '<svg viewBox="0 0 ' + W + ' ' + (barTop + barH + axisH) + '" width="100%" style="display:block;">' +
        (haveCompletion ? refLine + linePath + lineDots : '<text x="' + padL + '" y="' + (padTop + lineH / 2) + '" font-size="10" fill="var(--text-muted)">нет плана для расчёта % выполнения</text>') +
        baseline + bars + xLabels +
      '</svg>';
  }

  function renderChart() {
    var row = rowsById[state.selectedId];
    var el = document.getElementById('chartCard');
    if (!row) { el.innerHTML = ''; return; }

    var allRecords = (factsByMetric[row.id_pokaz] || []).slice(-9);
    var lastClosed = null;
    for (var i = allRecords.length - 1; i >= 0; i--) {
      if (!allRecords[i].prognoz_flg) { lastClosed = allRecords[i]; break; }
    }

    var head = '' +
      '<div class="chart-head">' +
        '<div><div class="metric-title">' + row.pokaz_name + '</div><div class="metric-screen">' + row.screen + ' · ' + row.category + '</div></div>' +
        '<div class="chart-stats">' +
          '<div class="stat"><div class="lbl">Факт</div><div class="val">' + (lastClosed ? fmtNum(lastClosed.fact, row.unit) : '—') + '</div></div>' +
          '<div class="stat"><div class="lbl">План</div><div class="val">' + (lastClosed && lastClosed.plan !== null ? fmtNum(lastClosed.plan, row.unit) : '—') + '</div></div>' +
          '<div class="stat"><div class="lbl">Δ пред. мес.</div><div class="val">' + (lastClosed ? deltaChip(lastClosed.mom_pct, judgeGood(lastClosed.mom_pct, row.direction, MOM_NEUTRAL_BAND)) : '—') + '</div></div>' +
        '</div>' +
      '</div>';

    var svg = allRecords.length ? buildChartSvg(allRecords, row.direction) : '<div class="empty-state">Недостаточно данных для графика</div>';

    var legend = '' +
      '<div class="legend-row">' +
        '<span><span class="sw" style="background:var(--series-1)"></span>Факт</span>' +
        '<span><span class="sw" style="background:var(--series-2)"></span>План</span>' +
        '<span class="muted">линия сверху — % выполнения плана; полупрозрачные столбцы (*) — месяц ещё не закрыт, значение предварительное</span>' +
      '</div>';

    el.innerHTML = head + svg + legend;
  }

  // -----------------------------------------------------------------
  // Полный рендер / события
  // -----------------------------------------------------------------
  function renderAll() {
    renderTableHead();
    renderTable();
    renderKpis();
    renderMethodology();
    renderHighlight();
    renderChart();
  }

  document.getElementById('groupToggle').addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-mode]');
    if (!btn) return;
    state.groupMode = btn.getAttribute('data-mode');
    Array.prototype.forEach.call(this.querySelectorAll('button'), function (b) { b.classList.remove('active'); });
    btn.classList.add('active');
    renderTable();
  });

  function wireChip(id, key) {
    var wrap = document.getElementById(id);
    var input = wrap.querySelector('input');
    wrap.addEventListener('click', function (e) {
      e.preventDefault(); // избегаем двойного тоггла (клик по label форвардится в input)
      state[key] = !state[key];
      input.checked = state[key];
      wrap.classList.toggle('on', state[key]);
      renderTable();
    });
  }
  wireChip('filterImportant', 'filterImportant');
  wireChip('filterTarget', 'filterTarget');

  document.getElementById('tableBody').addEventListener('click', function (e) {
    var groupRow = e.target.closest('tr.group-row');
    if (groupRow) {
      var gKey = groupRow.getAttribute('data-gkey');
      state.collapsed[gKey] = !state.collapsed[gKey];
      renderTable();
      return;
    }
    var metricRow = e.target.closest('tr.metric-row');
    if (metricRow) {
      state.selectedId = metricRow.getAttribute('data-id');
      state.briefIndex = 0;
      renderTable();
      renderHighlight();
      renderChart();
    }
  });

  renderAll();
})();
