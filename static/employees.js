const numberFormat = new Intl.NumberFormat('ru-RU');
const dateFormat = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit'
});
const byId = (id) => document.getElementById(id);
let employeePulseRequest = 0;

function updateSituationsPanel(payload) {
  const panel = byId('situations-panel');
  const list = byId('situations-list');
  if (!panel || !list) return;
  list.replaceChildren();
  const situations = payload?.active_situations || [];
  if (!Array.isArray(situations) || situations.length === 0) {
    panel.hidden = true;
    return;
  }
  situations.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'situation-item';

    const title = document.createElement('div');
    title.className = 'situation-name';
    title.textContent = item.title || 'Ситуация';

    const desc = document.createElement('div');
    desc.className = 'situation-desc';
    desc.textContent = item.description || '';

    const priority = document.createElement('div');
    priority.className = 'situation-priority';
    priority.textContent = `Приоритет: ${numberFormat.format(Number(item.priority || 0))}`;

    card.append(title, desc, priority);
    list.append(card);
  });
  panel.hidden = false;
}

function applyViewProfile(payload) {
  const container = byId('page-blocks');
  if (!container) return;
  const profile = payload?.view_profile || {};
  const order = Array.isArray(profile.block_order) ? profile.block_order : [];
  const highlights = new Set(Array.isArray(profile.highlights) ? profile.highlights : []);
  const blocks = Array.from(container.querySelectorAll('[data-block]'));
  const orderIndex = new Map(order.map((key, index) => [String(key), index]));
  blocks.forEach((block) => {
    const name = block.dataset.block || '';
    const index = orderIndex.has(name) ? orderIndex.get(name) : Number.MAX_SAFE_INTEGER;
    block.style.order = String(index);
    block.classList.toggle('is-promoted', index < 2 && order.length > 0);
    block.classList.toggle('is-highlighted', highlights.has(name));
    block.hidden = order.length > 0 && !orderIndex.has(name);
  });
}

async function loadViewState() {
  try {
    const project = byId('project-filter').value || 'all';
    const employee = byId('lead-employee-filter').value || 'all';
    const response = await fetch(`/api/view-state?page=employees&project=${encodeURIComponent(project)}&employee=${encodeURIComponent(employee)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить состояние вида');
    applyViewProfile(data);
    updateSituationsPanel(data);
    const defaults = data?.view_profile?.default_filters || {};
    if (defaults.project === 'nonempty' && project === 'all') {
      const select = byId('project-filter');
      if (select && select.options.length > 1) {
        select.value = select.options[1].value;
        loadPortfolio();
      }
    }
  } catch (error) {
    const panel = byId('situations-panel');
    if (panel) panel.hidden = true;
  }
}

function formatDate(raw) {
  if (!raw) return 'Нет данных';
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

function tail(uri) {
  return uri ? uri.substring(uri.lastIndexOf('/') + 1) : '';
}

function renderCategories(categories) {
  const select = byId('project-filter');
  const selected = select.value || 'all';
  const options = [new Option('Все категории', 'all')];
  for (const category of categories) {
    options.push(new Option(category.name || `Категория ${category.id}`, category.id));
  }
  select.replaceChildren(...options);
  select.value = categories.some((item) => item.id === selected) ? selected : 'all';
}

function renderPortfolio(employees) {
  const body = byId('employee-rows');
  body.replaceChildren();
  byId('portfolio-loading').hidden = true;
  if (!employees.length) {
    byId('employee-table').hidden = true;
    byId('portfolio-empty').hidden = false;
    return;
  }
  byId('portfolio-empty').hidden = true;
  byId('employee-table').hidden = false;
  for (const employee of employees) {
    const row = document.createElement('tr');
    const name = document.createElement('td');
    name.className = 'employee-name';
    name.textContent = employee.name || 'Сотрудник не указан';
    const uri = document.createElement('span');
    uri.className = 'employee-uri';
    uri.textContent = tail(employee.uri);
    name.append(uri);
    row.append(name);
    row.append(numberCell(employee.active_deals));
    row.append(numberCell(employee.activities_24h));
    row.append(numberCell(employee.activities_7d));
    row.append(numberCell(employee.activities_30d));
    row.append(numberCell(employee.inactive_deals_7d));
    body.append(row);
  }
}

function numberCell(value) {
  const cell = document.createElement('td');
  cell.className = 'number';
  cell.textContent = numberFormat.format(Number(value || 0));
  return cell;
}

function renderEmployees(employees) {
  const select = byId('lead-employee-filter');
  const selected = select.value || 'all';
  const options = [new Option('Все сотрудники', 'all')];
  for (const employee of employees) {
    options.push(new Option(employee.name || 'Сотрудник не указан', employee.id));
  }
  select.replaceChildren(...options);
  select.value = employees.some((item) => item.id === selected) ? selected : 'all';
}

function renderEmployeePulse(points) {
  const host = byId('employee-pulse-chart');
  host.replaceChildren();
  const responseValues = points.filter((point) => point.average_response_minutes != null).map((point) => Number(point.average_response_minutes)).filter(Number.isFinite);
  const conversionValues = points.filter((point) => point.average_conversion_minutes != null).map((point) => Number(point.average_conversion_minutes)).filter(Number.isFinite);
  const hasValues = responseValues.length || conversionValues.length;
  byId('employee-pulse-empty').hidden = Boolean(hasValues);
  host.hidden = !hasValues;
  if (!hasValues) return;

  const width = 900;
  const height = 300;
  const left = 84;
  const right = 84;
  const top = 14;
  const bottom = 38;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const responseMax = Math.max(...responseValues, 1) * 1.08;
  const conversionMax = Math.max(...conversionValues, 1) * 1.08;
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
  const x = (index) => left + (points.length <= 1 ? 0 : plotWidth * index / (points.length - 1));
  const yFor = (value, maxValue) => top + plotHeight * (1 - value / maxValue);

  for (let tick = 0; tick <= 4; tick += 1) {
    const ratio = tick / 4;
    const y = top + plotHeight * (1 - ratio);
    svg.append(element('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'chart-gridline' }));
    svg.append(element('text', { x: left - 10, y: y + 4, class: 'chart-axis-label chart-response-axis', 'text-anchor': 'end' }, formatDuration(responseMax * ratio)));
    svg.append(element('text', { x: width - right + 10, y: y + 4, class: 'chart-axis-label chart-conversion-axis', 'text-anchor': 'start' }, formatDuration(conversionMax * ratio)));
  }

  const drawSeries = (valueKey, sampleKey, maxValue, lineClass, pointClass, unit) => {
    let segment = [];
    const flush = () => {
      if (segment.length > 1) {
        svg.append(element('polyline', { points: segment.map(({ px, py }) => `${px},${py}`).join(' '), class: lineClass }));
      }
      segment = [];
    };
    points.forEach((point, index) => {
      if (point[valueKey] == null || !Number.isFinite(Number(point[valueKey]))) {
        flush();
        return;
      }
      const value = Number(point[valueKey]);
      const px = x(index);
      const py = yFor(value, maxValue);
      segment.push({ px, py });
      const date = new Date(`${point.date}T00:00:00`);
      const dateLabel = Number.isNaN(date.getTime()) ? point.date : new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' }).format(date);
      const circle = element('circle', { cx: px, cy: py, r: 2.4, class: pointClass });
      circle.append(element('title', {}, `${dateLabel}: ${formatDuration(value)} · ${numberFormat.format(point[sampleKey] || 0)} ${unit}`));
      svg.append(circle);
    });
    flush();
  };
  drawSeries('average_response_minutes', 'response_sample_size', responseMax, 'employee-response-line', 'employee-response-point', 'лидов');
  drawSeries('average_conversion_minutes', 'conversion_sample_size', conversionMax, 'employee-conversion-line', 'employee-conversion-point', 'сделок');

  const dateIndices = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  for (const index of dateIndices) {
    const date = new Date(`${points[index].date}T00:00:00`);
    const label = Number.isNaN(date.getTime()) ? points[index].date : new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' }).format(date);
    svg.append(element('text', { x: x(index), y: height - 9, class: 'chart-axis-label', 'text-anchor': index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle' }, label));
  }
  host.append(svg);
}

async function loadPortfolio() {
  byId('refresh').disabled = true;
  byId('portfolio-loading').hidden = false;
  try {
    const category = byId('project-filter').value || 'all';
    const response = await fetch(`/api/pulse?project=${encodeURIComponent(category)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось загрузить портфель сотрудников');
    renderCategories(data.categories || []);
    renderPortfolio(data.employees || []);
    byId('connection').className = 'connection online';
    byId('connection').lastElementChild.textContent = data.backend === 'fuseki' ? 'Fuseki подключён' : 'GraphDB подключён';
    byId('updated').textContent = `Обновлено ${formatDate(data.as_of)}`;
    byId('error').hidden = true;
    loadViewState();
  } catch (error) {
    byId('connection').className = 'connection offline';
    byId('connection').lastElementChild.textContent = 'Нет соединения';
    byId('error').textContent = error.message;
    byId('error').hidden = false;
    byId('portfolio-loading').hidden = true;
  } finally {
    byId('refresh').disabled = false;
  }
}

async function loadEmployeePulse() {
  const requestId = ++employeePulseRequest;
  const employee = byId('lead-employee-filter').value || 'all';
  try {
    const response = await fetch(`/api/lead-employee-pulse?employee=${encodeURIComponent(employee)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить пульс сотрудников');
    if (requestId !== employeePulseRequest) return;
    renderEmployees(data.employees || []);
    renderEmployeePulse(data.employee_trend || []);
  } catch (error) {
    if (requestId !== employeePulseRequest) return;
    byId('employee-pulse-chart').hidden = true;
    byId('employee-pulse-empty').hidden = false;
    byId('employee-pulse-empty').textContent = error.message;
  }
}

byId('refresh').addEventListener('click', () => {
  loadPortfolio();
  loadEmployeePulse();
  loadViewState();
});
byId('project-filter').addEventListener('change', () => {
  loadPortfolio();
  loadViewState();
});
byId('lead-employee-filter').addEventListener('change', () => {
  loadEmployeePulse();
  loadViewState();
});
loadPortfolio();
loadEmployeePulse();
loadViewState();
window.setInterval(() => {
  loadPortfolio();
  loadEmployeePulse();
  loadViewState();
}, 60_000);
