"""
Отчёт: выполнение целей по юнитам.
Источник данных: колонки unit_name, metric_name, d_date, n_dynamic, dynamic_type,
                  s_measure, s_measure_diff, fact_value, execution_percent,
                  execution_flag, prev_fact_value, sign_delta

Рассчитан на большое число показателей (100+), поэтому детальный список
показан таблицей со светофорами (сортировка + поиск), а не карточками.

Показывает:
- фильтры по отчётной дате и юниту
- текстовое саммари по ключевым отклонениям
- блок автоматических инсайтов (рост, падение, аномалии, смена тренда,
  срыв/восстановление плана)
- бублики с количеством выполненных целей (общий + по юнитам)
- таблица метрик со светофорами и сортировкой/поиском
- рейтинг юнитов по среднему проценту выполнения
"""

import sys
import csv
import html
import json

data_path = sys.argv[1] if len(sys.argv) > 1 else None

if not data_path:
    print("<p style='color:red'>Не задан источник данных.</p>")
    sys.exit(0)

# ── Чтение CSV ────────────────────────────────────────────────────────────────
rows = []
try:
    for sep in (";", ","):
        with open(data_path, encoding="utf-8-sig", newline="") as f:
            reader = list(csv.DictReader(f, delimiter=sep))
        if reader and len(reader[0]) > 1:
            break
    rows = reader
except Exception as e:
    print(f"<p style='color:red'>Ошибка чтения файла: {html.escape(str(e))}</p>")
    sys.exit(0)

if not rows:
    print("<p style='color:var(--text-muted)'>Нет данных для отображения.</p>")
    sys.exit(0)

# ── Нормализация типов ─────────────────────────────────────────────────────────
def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

norm_rows = []
for r in rows:
    ep_raw = r.get("execution_percent")
    ep = _to_float(ep_raw, None) if ep_raw not in (None, "") else None
    norm_rows.append({
        "unit_name": r.get("unit_name", "—"),
        "metric_name": r.get("metric_name", "—"),
        "d_date": r.get("d_date", ""),
        "n_dynamic": _to_float(r.get("n_dynamic")),
        "dynamic_type": r.get("dynamic_type", "m"),
        "s_measure": r.get("s_measure", ""),
        "s_measure_diff": r.get("s_measure_diff", "%"),
        "fact_value": _to_float(r.get("fact_value")),
        "execution_percent": ep,
        "execution_flag": int(_to_float(r.get("execution_flag"), 0)),
        "prev_fact_value": _to_float(r.get("prev_fact_value")),
        "sign_delta": int(_to_float(r.get("sign_delta"), 1)),
    })

# Ограничиваем объём, передаваемый в JS, на случай очень большого файла
if len(norm_rows) > 20000:
    norm_rows = norm_rows[:20000]

dates = sorted({r["d_date"] for r in norm_rows if r["d_date"]}, reverse=True)
units = sorted({r["unit_name"] for r in norm_rows})

js_rows = json.dumps(norm_rows, ensure_ascii=False)
js_dates = json.dumps(dates, ensure_ascii=False)
js_units = json.dumps(units, ensure_ascii=False)

print(f"""
<style>
  select.rpt-sel, .search-input {{ padding:6px 10px;border-radius:8px;border:1px solid var(--border);
                    background:var(--surface);color:var(--text);font-size:0.88rem; }}
  select.rpt-sel {{ cursor:pointer; }}
  .search-input {{ min-width:240px; }}
  .filters-row {{ display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px;align-items:flex-end; }}
  .filter-label {{ font-size:0.78rem;color:var(--text-muted);margin-bottom:4px; }}
  .summary-banner {{ background:var(--surface-strong);border:1px solid var(--border);
                     border-radius:16px;padding:18px 22px;margin-bottom:26px;line-height:1.6;font-size:0.95rem; }}
  .section-title {{ font-size:1.05rem;font-weight:700;margin:30px 0 14px; }}

  .donuts-grid {{ display:flex;gap:16px;flex-wrap:wrap; }}
  .donut-card {{ background:var(--surface);border:1px solid var(--border);border-radius:18px;
                padding:14px;flex:1 1 220px;min-width:220px;text-align:center;
                box-shadow:0 14px 40px -34px var(--shadow); }}
  .donut-card h4 {{ margin:0 0 4px;font-size:0.88rem;color:var(--text-muted);font-weight:600; }}

  .insights-grid {{ display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px; }}
  .insight-card {{ background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--accent);
                   border-radius:12px;padding:14px 16px;font-size:0.85rem;line-height:1.5;
                   box-shadow:0 14px 40px -34px var(--shadow); }}
  .insight-card.success {{ border-left-color:#16a34a; }}
  .insight-card.danger  {{ border-left-color:#dc2626; }}
  .insight-card.warn    {{ border-left-color:#f59e0b; }}
  .insight-card.info    {{ border-left-color:#3b82f6; }}
  .insight-title {{ font-weight:700;margin-bottom:6px;display:flex;align-items:center;gap:6px; }}
  .insight-card ul {{ margin:2px 0 0;padding-left:18px; }}
  .insight-card li {{ margin-bottom:2px; }}

  .table-toolbar {{ display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:10px; }}
  .table-scroll {{ max-height:560px;overflow-y:auto;border:1px solid var(--border);border-radius:14px; }}
  table.rpt-table {{ width:100%;border-collapse:collapse;font-size:0.85rem; }}
  table.rpt-table thead th {{ position:sticky;top:0;background:var(--surface-strong);padding:9px 10px;
                              text-align:left;border-bottom:2px solid var(--border);cursor:pointer;
                              white-space:nowrap;user-select:none;z-index:1; }}
  table.rpt-table thead th:hover {{ color:var(--accent); }}
  table.rpt-table td {{ padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap; }}
  table.rpt-table tbody tr:hover {{ background:var(--surface-strong); }}
  .sort-arrow {{ font-size:0.7rem;margin-left:3px;color:var(--text-muted); }}
  .table-count {{ font-size:0.8rem;color:var(--text-muted); }}

  .light-dot {{ display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle; }}
  .light-green {{ background:#16a34a; }}
  .light-yellow {{ background:#f59e0b; }}
  .light-red {{ background:#dc2626; }}
  .light-gray {{ background:#9ca3af; }}
  .dyn-up   {{ color:#16a34a;font-weight:600; }}
  .dyn-down {{ color:#dc2626;font-weight:600; }}
  .dyn-warn {{ color:#92400e;font-weight:600; }}

  .leaderboard-row {{ display:flex;align-items:center;gap:12px;padding:9px 0;border-bottom:1px solid var(--border); }}
  .leaderboard-row:last-child {{ border-bottom:none; }}
  .medal {{ font-size:1.2rem;width:28px;text-align:center;flex:none; }}
  .lb-unit {{ width:190px;flex:none;font-weight:600;font-size:0.9rem; }}
  .lb-bar-track {{ flex:1;background:var(--border);border-radius:999px;height:10px;overflow:hidden; }}
  .lb-bar-fill {{ height:100%;border-radius:999px;background:var(--accent); }}
  .lb-pct {{ width:64px;flex:none;text-align:right;font-weight:700; }}
  .lb-delta {{ width:70px;flex:none;font-size:0.78rem;color:var(--text-muted);text-align:right; }}
</style>

<div class="filters-row">
  <div>
    <div class="filter-label">Отчётная дата</div>
    <select class="rpt-sel" id="sel-date" onchange="rebuildAll()"></select>
  </div>
  <div>
    <div class="filter-label">Юнит</div>
    <select class="rpt-sel" id="sel-unit" onchange="rebuildAll()"></select>
  </div>
</div>

<div id="summary-container"></div>

<div class="section-title">🔎 Инсайты</div>
<div id="insights-container" class="insights-grid"></div>

<div class="section-title">🍩 Выполнение целей</div>
<div id="donuts-container" class="donuts-grid"></div>

<div class="section-title">📋 Показатели</div>
<div class="table-toolbar">
  <input type="text" class="search-input" id="search-metric" placeholder="Поиск по метрике или юниту…" oninput="renderTable()">
  <div class="table-count" id="table-count"></div>
</div>
<div class="table-scroll">
  <table class="rpt-table">
    <thead>
      <tr>
        <th data-col="light">Статус</th>
        <th data-col="unit_name">Юнит</th>
        <th data-col="metric_name">Метрика</th>
        <th data-col="fact_value">Факт</th>
        <th data-col="execution_percent">% выполнения</th>
        <th data-col="m_dynamic">МоМ</th>
        <th data-col="y_dynamic">ГоГ</th>
      </tr>
    </thead>
    <tbody id="metrics-tbody"></tbody>
  </table>
</div>

<div class="section-title">🏆 Рейтинг юнитов по выполнению</div>
<div id="leaderboard-container"></div>

<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
const ALL_ROWS = {js_rows};
const DATES = {js_dates};
const UNITS = {js_units};

const DYN_LABEL = {{ m: 'МоМ', y: 'ГоГ', w: 'НоН' }};
let sortState = {{ col: 'execution_percent', dir: 'asc' }};

function fmtNum(v, d) {{
  return (+v).toLocaleString('ru-RU', {{ minimumFractionDigits: d||0, maximumFractionDigits: d||1 }});
}}

function esc(s) {{
  return String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function cssVar(name) {{
  return getComputedStyle(document.body).getPropertyValue(name).trim() || '#888';
}}

function lightClass(flag, pct) {{
  if (!flag) return 'light-gray';
  if (pct >= 100) return 'light-green';
  if (pct >= 90) return 'light-yellow';
  return 'light-red';
}}

function execBadgeText(flag, pct) {{
  if (!flag) return 'нет плана';
  return fmtNum(pct, 1) + '%';
}}

function dynCellHtml(row) {{
  if (!row) return '<span style="color:var(--text-muted)">—</span>';
  const label = esc(DYN_LABEL[row.dynamic_type] || row.dynamic_type);
  const measureDiff = esc(row.s_measure_diff);
  if (row.sign_delta === -1) {{
    return `<span class="dyn-warn" title="Текущий или предыдущий факт отрицателен">⚠ ${{fmtNum(row.n_dynamic,1)}}${{measureDiff}}</span>`;
  }}
  const up = row.n_dynamic >= 0;
  return `<span class="${{up ? 'dyn-up' : 'dyn-down'}}">${{up ? '▲' : '▼'}} ${{fmtNum(Math.abs(row.n_dynamic),1)}}${{measureDiff}}</span>`;
}}

function canonicalRows(dateSel, unitSel) {{
  // dynamic_type='m' как канонический снимок факта/плана метрики (без дублей по типам динамики)
  return ALL_ROWS.filter(r => r.d_date === dateSel && r.dynamic_type === 'm'
    && (unitSel === 'Все' || r.unit_name === unitSel));
}}

function findRow(dateSel, unit, metric, dynType) {{
  return ALL_ROWS.find(r => r.d_date === dateSel && r.unit_name === unit && r.metric_name === metric && r.dynamic_type === dynType);
}}

function prevDateOf(dateSel) {{
  const idx = DATES.indexOf(dateSel);
  return idx >= 0 && idx + 1 < DATES.length ? DATES[idx + 1] : null;
}}

// ── Саммари ────────────────────────────────────────────────────────────────
function renderSummary(dateSel, unitSel) {{
  const rows = canonicalRows(dateSel, unitSel);
  const withPlan = rows.filter(r => r.execution_flag === 1);
  const fulfilled = withPlan.filter(r => r.execution_percent >= 100);
  const noPlan = rows.filter(r => r.execution_flag === 0);
  const pctFulfilled = withPlan.length ? (fulfilled.length / withPlan.length * 100) : 0;

  const trustworthy = withPlan.filter(r => r.sign_delta !== -1);
  const best = trustworthy.length ? trustworthy.reduce((a,b) => b.n_dynamic > a.n_dynamic ? b : a) : null;
  const worst = trustworthy.length ? trustworthy.reduce((a,b) => b.n_dynamic < a.n_dynamic ? b : a) : null;
  const attention = rows.filter(r => r.sign_delta === -1);

  let out = `<div class="summary-banner">`;
  out += `За <strong>${{esc(dateSel)}}</strong>${{unitSel!=='Все' ? ' по юниту <strong>'+esc(unitSel)+'</strong>' : ' по всем юнитам'}}: `;
  out += `из <strong>${{withPlan.length}}</strong> метрик с планом выполнено <strong>${{fulfilled.length}}</strong> (${{fmtNum(pctFulfilled,0)}}%).`;
  if (noPlan.length) out += ` Ещё ${{noPlan.length}} метрик(и) без установленного плана.`;
  if (best) out += ` Быстрее всего растёт «<strong>${{esc(best.metric_name)}}</strong>» (${{esc(best.unit_name)}}, МоМ ${{best.n_dynamic>=0?'+':''}}${{fmtNum(best.n_dynamic,1)}}${{esc(best.s_measure_diff)}}).`;
  if (worst && worst.n_dynamic < 0) out += ` Сильнее всего просела «<strong>${{esc(worst.metric_name)}}</strong>» (${{esc(worst.unit_name)}}, МоМ ${{fmtNum(worst.n_dynamic,1)}}${{esc(worst.s_measure_diff)}}).`;
  if (attention.length) out += ` ⚠ У ${{attention.length}} метрик(и) значение ушло в отрицательную зону — проценты динамики по ним нужно трактовать с осторожностью.`;
  out += `</div>`;
  document.getElementById('summary-container').innerHTML = out;
}}

// ── Инсайты ───────────────────────────────────────────────────────────────
function insightCard(cls, title, items) {{
  if (!items.length) return '';
  const lis = items.map(t => `<li>${{t}}</li>`).join('');
  return `<div class="insight-card ${{cls}}"><div class="insight-title">${{title}}</div><ul>${{lis}}</ul></div>`;
}}

function renderInsights(dateSel, unitSel) {{
  const rows = canonicalRows(dateSel, unitSel);
  const trustworthy = rows.filter(r => r.sign_delta !== -1);

  const mean = trustworthy.length ? trustworthy.reduce((s,r) => s + r.n_dynamic, 0) / trustworthy.length : 0;
  const variance = trustworthy.length ? trustworthy.reduce((s,r) => s + (r.n_dynamic - mean) ** 2, 0) / trustworthy.length : 0;
  const std = Math.sqrt(variance);

  const growth = trustworthy.filter(r => r.n_dynamic > 0).sort((a,b) => b.n_dynamic - a.n_dynamic).slice(0, 4)
    .map(r => `▲ ${{fmtNum(r.n_dynamic,1)}}${{esc(r.s_measure_diff)}} — <strong>${{esc(r.metric_name)}}</strong> (${{esc(r.unit_name)}})`);

  const decline = trustworthy.filter(r => r.n_dynamic < 0).sort((a,b) => a.n_dynamic - b.n_dynamic).slice(0, 4)
    .map(r => `▼ ${{fmtNum(r.n_dynamic,1)}}${{esc(r.s_measure_diff)}} — <strong>${{esc(r.metric_name)}}</strong> (${{esc(r.unit_name)}})`);

  const anomalies = std > 0 ? trustworthy
    .map(r => ({{ r, z: (r.n_dynamic - mean) / std }}))
    .filter(x => Math.abs(x.z) > 2)
    .sort((a,b) => Math.abs(b.z) - Math.abs(a.z))
    .slice(0, 5)
    .map(x => `⚡ ${{fmtNum(x.r.n_dynamic,1)}}${{esc(x.r.s_measure_diff)}} (z=${{fmtNum(x.z,1)}}) — <strong>${{esc(x.r.metric_name)}}</strong> (${{esc(x.r.unit_name)}})`)
    : [];

  const negBase = rows.filter(r => r.sign_delta === -1).slice(0, 5)
    .map(r => `<strong>${{esc(r.metric_name)}}</strong> (${{esc(r.unit_name)}}) — факт ${{fmtNum(r.fact_value,1)}} ${{esc(r.s_measure)}}`);

  let reversals = [], planFailed = [], planRecovered = [];
  const prevDate = prevDateOf(dateSel);
  if (prevDate) {{
    rows.forEach(r => {{
      const prevRow = findRow(prevDate, r.unit_name, r.metric_name, 'm');
      if (!prevRow) return;
      if (r.sign_delta !== -1 && prevRow.sign_delta !== -1 && r.n_dynamic !== 0 && prevRow.n_dynamic !== 0
          && Math.sign(r.n_dynamic) !== Math.sign(prevRow.n_dynamic)) {{
        const dir = r.n_dynamic > 0 ? 'на рост' : 'на падение';
        reversals.push(`<strong>${{esc(r.metric_name)}}</strong> (${{esc(r.unit_name)}}) развернулась ${{dir}}: ${{fmtNum(prevRow.n_dynamic,1)}}% → ${{fmtNum(r.n_dynamic,1)}}%`);
      }}
      if (r.execution_flag === 1 && prevRow.execution_flag === 1) {{
        if (prevRow.execution_percent >= 100 && r.execution_percent < 100) {{
          planFailed.push(`<strong>${{esc(r.metric_name)}}</strong> (${{esc(r.unit_name)}}) — было ${{fmtNum(prevRow.execution_percent,1)}}%, стало ${{fmtNum(r.execution_percent,1)}}%`);
        }} else if (prevRow.execution_percent < 100 && r.execution_percent >= 100) {{
          planRecovered.push(`<strong>${{esc(r.metric_name)}}</strong> (${{esc(r.unit_name)}}) — было ${{fmtNum(prevRow.execution_percent,1)}}%, стало ${{fmtNum(r.execution_percent,1)}}%`);
        }}
      }}
    }});
  }}

  let html = '';
  html += insightCard('success', '🚀 Рост', growth);
  html += insightCard('danger', '📉 Падение', decline);
  html += insightCard('warn', '⚡ Аномалии (выброс относительно типичного диапазона)', anomalies);
  html += insightCard('info', '🔄 Смена тренда к предыдущему периоду', reversals.slice(0, 5));
  html += insightCard('danger', '🆘 Перестали выполнять план', planFailed.slice(0, 5));
  html += insightCard('success', '✅ Вернулись к выполнению плана', planRecovered.slice(0, 5));
  html += insightCard('warn', '⚠ Отрицательная база (%-динамику трактовать осторожно)', negBase);

  document.getElementById('insights-container').innerHTML = html || "<p style='color:var(--text-muted)'>Существенных отклонений не найдено.</p>";
}}

// ── Бублики ───────────────────────────────────────────────────────────────
function plotDonut(containerId, fulfilled, total) {{
  const notFulfilled = total - fulfilled;
  const textColor = cssVar('--text');
  const data = [{{
    type: 'pie',
    hole: 0.62,
    labels: ['Выполнено', 'Не выполнено'],
    values: [fulfilled, notFulfilled],
    marker: {{ colors: ['#16a34a', '#dc2626'] }},
    textinfo: 'percent',
    textfont: {{ color: '#ffffff', size: 12 }},
    hoverinfo: 'label+value+percent',
    sort: false,
  }}];
  const layout = {{
    height: 200,
    margin: {{ t: 10, b: 10, l: 10, r: 10 }},
    showlegend: false,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: {{ color: textColor }},
    annotations: [{{
      text: `${{fulfilled}}/${{total}}`,
      showarrow: false,
      font: {{ size: 20, color: textColor, family: 'Inter, sans-serif' }},
    }}],
  }};
  Plotly.newPlot(containerId, data, layout, {{ displayModeBar: false, responsive: true }});
}}

function renderDonuts(dateSel, unitSel) {{
  const container = document.getElementById('donuts-container');
  const rows = canonicalRows(dateSel, unitSel).filter(r => r.execution_flag === 1);
  const unitsToShow = unitSel === 'Все' ? UNITS : [unitSel];

  let cardsHtml = `<div class="donut-card"><h4>Итого</h4><div id="donut-total"></div></div>`;
  unitsToShow.forEach((u, i) => {{
    cardsHtml += `<div class="donut-card"><h4>${{esc(u)}}</h4><div id="donut-u${{i}}"></div></div>`;
  }});
  container.innerHTML = cardsHtml;

  const totalFulfilled = rows.filter(r => r.execution_percent >= 100).length;
  plotDonut('donut-total', totalFulfilled, rows.length || 1);

  unitsToShow.forEach((u, i) => {{
    const uRows = rows.filter(r => r.unit_name === u);
    const uFulfilled = uRows.filter(r => r.execution_percent >= 100).length;
    plotDonut('donut-u' + i, uFulfilled, uRows.length || 1);
  }});
}}

// ── Таблица со светофорами ──────────────────────────────────────────────────
function sortBy(col) {{
  if (sortState.col === col) {{
    sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
  }} else {{
    sortState.col = col;
    sortState.dir = 'asc';
  }}
  renderTable();
}}

function sortValue(item, col) {{
  switch (col) {{
    case 'light': return {{ 'light-green': 2, 'light-yellow': 1, 'light-red': 0, 'light-gray': -1 }}[item.light];
    case 'unit_name': return item.unit_name;
    case 'metric_name': return item.metric_name;
    case 'fact_value': return item.fact_value;
    case 'execution_percent': return item.execution_percent === null ? -1 : item.execution_percent;
    case 'm_dynamic': return item.mRow ? item.mRow.n_dynamic : -Infinity;
    case 'y_dynamic': return item.yRow ? item.yRow.n_dynamic : -Infinity;
    default: return 0;
  }}
}}

function renderTable() {{
  const dateSel = document.getElementById('sel-date').value;
  const unitSel = document.getElementById('sel-unit').value;
  const search = (document.getElementById('search-metric').value || '').trim().toLowerCase();

  let items = canonicalRows(dateSel, unitSel).map(r => ({{
    unit_name: r.unit_name,
    metric_name: r.metric_name,
    fact_value: r.fact_value,
    s_measure: r.s_measure,
    execution_flag: r.execution_flag,
    execution_percent: r.execution_percent,
    light: lightClass(r.execution_flag, r.execution_percent),
    mRow: findRow(dateSel, r.unit_name, r.metric_name, 'm'),
    yRow: findRow(dateSel, r.unit_name, r.metric_name, 'y'),
  }}));

  if (search) {{
    items = items.filter(it => it.metric_name.toLowerCase().includes(search) || it.unit_name.toLowerCase().includes(search));
  }}

  items.sort((a, b) => {{
    const va = sortValue(a, sortState.col), vb = sortValue(b, sortState.col);
    let cmp = typeof va === 'string' ? va.localeCompare(vb) : (va - vb);
    return sortState.dir === 'asc' ? cmp : -cmp;
  }});

  document.querySelectorAll('table.rpt-table thead th').forEach(th => {{
    const base = th.textContent.replace(/[▲▼]\\s*$/, '').trim();
    if (th.dataset.col === sortState.col) {{
      th.innerHTML = `${{base}}<span class="sort-arrow">${{sortState.dir === 'asc' ? '▲' : '▼'}}</span>`;
    }} else {{
      th.innerHTML = base;
    }}
  }});

  const trs = items.map(it => `
    <tr>
      <td><span class="light-dot ${{it.light}}"></span>${{execBadgeText(it.execution_flag, it.execution_percent)}}</td>
      <td>${{esc(it.unit_name)}}</td>
      <td>${{esc(it.metric_name)}}</td>
      <td>${{fmtNum(it.fact_value,1)}} ${{esc(it.s_measure)}}</td>
      <td>${{it.execution_flag ? fmtNum(it.execution_percent,1)+'%' : '<span style="color:var(--text-muted)">—</span>'}}</td>
      <td>${{dynCellHtml(it.mRow)}}</td>
      <td>${{dynCellHtml(it.yRow)}}</td>
    </tr>`).join('');

  document.getElementById('metrics-tbody').innerHTML = trs || `<tr><td colspan="7" style="color:var(--text-muted)">Нет данных для выбранных фильтров.</td></tr>`;
  document.getElementById('table-count').textContent = `Показано: ${{items.length}}`;
}}

document.querySelectorAll('table.rpt-table thead th').forEach(th => {{
  th.addEventListener('click', () => sortBy(th.dataset.col));
}});

// ── Рейтинг юнитов (всегда по всем юнитам, для выбранной даты) ────────────
function renderLeaderboard(dateSel) {{
  const prevDate = prevDateOf(dateSel);

  const byUnit = UNITS.map(u => {{
    const rowsU = ALL_ROWS.filter(r => r.d_date === dateSel && r.dynamic_type === 'm' && r.unit_name === u && r.execution_flag === 1);
    const avg = rowsU.length ? rowsU.reduce((s,r) => s + r.execution_percent, 0) / rowsU.length : null;

    let prevAvg = null;
    if (prevDate) {{
      const prevRows = ALL_ROWS.filter(r => r.d_date === prevDate && r.dynamic_type === 'm' && r.unit_name === u && r.execution_flag === 1);
      if (prevRows.length) prevAvg = prevRows.reduce((s,r) => s + r.execution_percent, 0) / prevRows.length;
    }}
    return {{ unit: u, avg, prevAvg }};
  }}).filter(x => x.avg !== null).sort((a,b) => b.avg - a.avg);

  const medals = ['🥇','🥈','🥉'];
  let html = '';
  byUnit.forEach((x, i) => {{
    const barW = Math.max(2, Math.min(100, Math.round(x.avg)));
    let delta = '';
    if (x.prevAvg !== null) {{
      const d = x.avg - x.prevAvg;
      delta = `${{d >= 0 ? '▲' : '▼'}} ${{fmtNum(Math.abs(d),1)}} п.п.`;
    }}
    html += `
    <div class="leaderboard-row">
      <div class="medal">${{medals[i] || '·'}}</div>
      <div class="lb-unit">${{esc(x.unit)}}</div>
      <div class="lb-bar-track"><div class="lb-bar-fill" style="width:${{barW}}%"></div></div>
      <div class="lb-pct">${{fmtNum(x.avg,0)}}%</div>
      <div class="lb-delta">${{delta}}</div>
    </div>`;
  }});
  document.getElementById('leaderboard-container').innerHTML = html || "<p style='color:var(--text-muted)'>Нет данных.</p>";
}}

function rebuildAll() {{
  const dateSel = document.getElementById('sel-date').value;
  const unitSel = document.getElementById('sel-unit').value;
  renderSummary(dateSel, unitSel);
  renderInsights(dateSel, unitSel);
  renderDonuts(dateSel, unitSel);
  renderTable();
  renderLeaderboard(dateSel);
}}

function init() {{
  const dateSelect = document.getElementById('sel-date');
  dateSelect.innerHTML = DATES.map(d => `<option value="${{esc(d)}}">${{esc(d)}}</option>`).join('');
  const unitSelect = document.getElementById('sel-unit');
  unitSelect.innerHTML = ['Все'].concat(UNITS).map(u => `<option value="${{esc(u)}}">${{esc(u)}}</option>`).join('');
  rebuildAll();
}}

document.addEventListener('DOMContentLoaded', init);
init();
</script>
""")
