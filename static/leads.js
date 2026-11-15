const numberFormat = new Intl.NumberFormat('ru-RU');
const dateFormat = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit'
});
const byId = (id) => document.getElementById(id);
let conversionTrendRequest = 0;

function formatDate(raw) {
  if (!raw) return 'Нет активности';
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? 'Нет данных' : dateFormat.format(date);
}

function formatDuration(minutes) {
  if (minutes == null || !Number.isFinite(Number(minutes))) return 'Нет данных';
  const value = Math.max(0, Math.round(Number(minutes)));
  if (value < 60) return `${numberFormat.format(value)} мин`;
  const hours = Math.floor(value / 60);
  if (hours < 48) return `${numberFormat.format(hours)} ч ${value % 60} мин`;
  const days = Math.floor(hours / 24);
  return `${numberFormat.format(days)} дн ${hours % 24} ч`;
}

function formatDays(days) {
  if (days == null || !Number.isFinite(Number(days))) return 'Нет данных';
  const value = Number(days);
  if (value < 1) return formatDuration(value * 1440);
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value)} дн`;
}

function td(text) {
  const cell = document.createElement('td');
  cell.textContent = text;
  return cell;
}

function leadSourceCell(lead) {
  const cell = document.createElement('td');
  const source = document.createElement('span');
  source.className = 'lead-meta-line';
  const sourceValue = lead.source_name || lead.source_code || 'Не указан';
  source.textContent = `Источник: ${sourceValue}${lead.source_name && lead.source_code ? ` (${lead.source_code})` : ''}`;
  if (lead.source_code) source.title = `sourceCode: ${lead.source_code}`;
  cell.append(source);
  return cell;
}

function renderTrendChart(points, hostId, emptyId, valueKey, unitLabel) {
  const host = byId(hostId);
  host.replaceChildren();
  const valid = points.filter((point) => point[valueKey] != null);
  byId(emptyId).hidden = valid.length > 0;
  host.hidden = valid.length === 0;
  if (!valid.length) return;

  const width = 900;
  const height = 300;
  const left = 76;
  const right = 18;
  const top = 14;
  const bottom = 38;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const maxValue = Math.max(...valid.map((point) => Number(point[valueKey])), 1);
  const yMax = maxValue * 1.08;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'presentation');
  svg.setAttribute('class', 'response-trend-svg');

  const element = (name, attrs = {}, text = '') => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text) node.textContent = text;
    return node;
  };
  const y = (value) => top + plotHeight * (1 - value / yMax);
  const x = (index) => left + (points.length <= 1 ? 0 : plotWidth * index / (points.length - 1));

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = yMax * tick / 4;
    const yPos = y(value);
    svg.append(element('line', { x1: left, x2: width - right, y1: yPos, y2: yPos, class: 'chart-gridline' }));
    svg.append(element('text', { x: left - 10, y: yPos + 4, class: 'chart-axis-label', 'text-anchor': 'end' }, formatDuration(value)));
  }

  let segment = [];
  const flushSegment = () => {
    if (segment.length > 1) {
      svg.append(element('polyline', {
        points: segment.map(({ px, py }) => `${px},${py}`).join(' '),
        class: 'response-trend-line'
      }));
    }
    segment = [];
  };
  points.forEach((point, index) => {
    if (point[valueKey] == null) {
      flushSegment();
      return;
    }
    const value = Number(point[valueKey]);
    const px = x(index);
    const py = y(value);
    segment.push({ px, py });
    const date = new Date(`${point.date}T00:00:00`);
    const label = Number.isNaN(date.getTime()) ? point.date : new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' }).format(date);
    const circle = element('circle', { cx: px, cy: py, r: 2.4, class: 'response-trend-point' });
    circle.append(element('title', {}, `${label}: ${formatDuration(value)} · ${numberFormat.format(point.sample_size || 0)} ${unitLabel}`));
    svg.append(circle);
  });
  flushSegment();

  const dateIndices = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  for (const index of dateIndices) {
    const date = new Date(`${points[index].date}T00:00:00`);
    const label = Number.isNaN(date.getTime()) ? points[index].date : new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' }).format(date);
    svg.append(element('text', { x: x(index), y: height - 9, class: 'chart-axis-label', 'text-anchor': index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle' }, label));
  }
  host.append(svg);
}

function renderCategories(categories) {
  const select = byId('lead-conversion-category');
  const selected = select.value || 'all';
  const options = [new Option('Все воронки', 'all')];
  for (const category of categories) {
    options.push(new Option(category.name || `Воронка ${category.id}`, category.id));
  }
  select.replaceChildren(...options);
  select.value = categories.some((item) => item.id === selected) ? selected : 'all';
}

function render(data) {
  const summary = data.summary || {};
  byId('created-1d').textContent = numberFormat.format(summary.created_1d || 0);
  byId('created-7d').textContent = numberFormat.format(summary.created_7d || 0);
  byId('created-30d').textContent = numberFormat.format(summary.created_30d || 0);
  byId('average-response').textContent = formatDuration(summary.average_response_minutes);
  byId('response-sample').textContent = `До первой активности · выборка ${numberFormat.format(summary.response_sample_size || 0)} лидов за 30 дней`;
  byId('average-conversion').textContent = formatDays(summary.average_conversion_days);
  byId('conversion-sample').textContent = `До связанной сделки P · выборка ${numberFormat.format(summary.conversion_sample_size || 0)} лидов`;
  renderTrendChart(data.response_trend || [], 'response-trend-chart', 'response-trend-empty', 'average_response_minutes', 'лидов');
  byId('updated').textContent = `Обновлено ${formatDate(data.as_of)}`;
  byId('connection').className = 'connection online';
  byId('connection').lastElementChild.textContent = data.backend === 'fuseki' ? 'Fuseki подключён' : 'GraphDB подключён';
  byId('error').hidden = true;

  const body = byId('lead-rows');
  body.replaceChildren();
  byId('leads-loading').hidden = true;
  const leads = data.red_leads || [];
  if (!leads.length) {
    byId('leads-table').hidden = true;
    byId('leads-empty').hidden = false;
    return;
  }
  byId('leads-empty').hidden = true;
  byId('leads-table').hidden = false;
  for (const lead of leads) {
    const row = document.createElement('tr');
    row.append(td(formatDate(lead.created_at)));
    const title = td(lead.title || 'Без названия');
    title.className = 'employee-name';
    row.append(title);
    row.append(leadSourceCell(lead));
    row.append(td(lead.manager || 'Не назначен'));
    body.append(row);
  }
}

async function loadConversionTrend() {
  const requestId = ++conversionTrendRequest;
  const category = byId('lead-conversion-category').value || 'all';
  try {
    const response = await fetch(`/api/lead-conversion-trend?category=${encodeURIComponent(category)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить пульс конверсии');
    if (requestId !== conversionTrendRequest) return;
    renderCategories(data.categories || []);
    renderTrendChart(data.conversion_trend || [], 'conversion-trend-chart', 'conversion-trend-empty', 'average_conversion_minutes', 'сделок');
  } catch (error) {
    if (requestId !== conversionTrendRequest) return;
    byId('conversion-trend-chart').hidden = true;
    byId('conversion-trend-empty').hidden = false;
    byId('conversion-trend-empty').textContent = error.message;
  }
}

async function load() {
  byId('refresh').disabled = true;
  try {
    const response = await fetch('/api/lead-pulse', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить пульс лидов');
    render(data);
  } catch (error) {
    byId('connection').className = 'connection offline';
    byId('connection').lastElementChild.textContent = 'Нет соединения';
    byId('error').textContent = error.message;
    byId('error').hidden = false;
    byId('leads-loading').hidden = true;
  } finally {
    byId('refresh').disabled = false;
  }
}

byId('refresh').addEventListener('click', () => {
  load();
  loadConversionTrend();
});
byId('lead-conversion-category').addEventListener('change', loadConversionTrend);
load();
loadConversionTrend();
window.setInterval(() => {
  load();
  loadConversionTrend();
}, 60_000);
