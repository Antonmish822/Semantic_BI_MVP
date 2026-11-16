const numberFormat = new Intl.NumberFormat('ru-RU');
const dateFormat = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit'
});
const byId = (id) => document.getElementById(id);
let activityDistributionRequest = 0;

function value(id, n) {
  byId(id).textContent = numberFormat.format(Number(n ?? 0));
}

function formatDate(raw) {
  if (!raw) return 'Нет данных';
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? 'Нет данных' : dateFormat.format(date);
}

function tail(uri) {
  return uri ? uri.substring(uri.lastIndexOf('/') + 1) : '';
}

function renderPulse(data) {
  const summary = data.summary || {};
  value('active-deals', summary.active_deals);
  value('created-24h', summary.created_24h);
  value('created-7d', summary.created_7d);
  value('created-30d', summary.created_30d);
  value('inactive-7d', summary.inactive_7d);
  byId('red-count').textContent = numberFormat.format(Number(summary.inactive_7d ?? 0));
  byId('updated').textContent = `Обновлено ${formatDate(data.as_of)}`;
  byId('connection').className = 'connection online';
  byId('connection').lastElementChild.textContent = data.backend === 'fuseki' ? 'Fuseki подключён' : 'GraphDB подключён';
  byId('error').hidden = true;
  renderCategories(data.categories || []);
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

function renderDistributionCategories(categories) {
  const select = byId('activity-distribution-category');
  const selected = select.value || 'all';
  const options = [new Option('Все категории', 'all')];
  for (const category of categories) {
    options.push(new Option(category.name || `Категория ${category.id}`, category.id));
  }
  select.replaceChildren(...options);
  select.value = categories.some((item) => item.id === selected) ? selected : 'all';
  return select.value;
}

function renderActivityDistribution(data) {
  const host = byId('activity-distribution-chart');
  const buckets = data.buckets || [];
  byId('activity-distribution-loading').hidden = true;
  host.replaceChildren();
  if (!buckets.length || Number(data.total_deals || 0) === 0) {
    host.hidden = true;
    byId('activity-distribution-empty').hidden = false;
    return;
  }
  byId('activity-distribution-empty').hidden = true;
  host.hidden = false;

  const width = 900;
  const height = 300;
  const left = 48;
  const right = 18;
  const top = 20;
  const bottom = 56;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const maxCount = Math.max(...buckets.map((bucket) => Number(bucket.deal_count || 0)), 1);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'presentation');
  svg.setAttribute('class', 'activity-distribution-svg');
  const element = (name, attrs = {}, text = '') => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text) node.textContent = text;
    return node;
  };
  const y = (value) => top + plotHeight * (1 - value / maxCount);

  for (let tick = 0; tick <= 4; tick += 1) {
    const count = maxCount * tick / 4;
    const yPos = y(count);
    svg.append(element('line', { x1: left, x2: width - right, y1: yPos, y2: yPos, class: 'chart-gridline' }));
    svg.append(element('text', { x: left - 9, y: yPos + 4, class: 'chart-axis-label', 'text-anchor': 'end' }, numberFormat.format(Math.round(count))));
  }

  const slot = plotWidth / buckets.length;
  const barWidth = Math.min(68, slot * 0.66);
  buckets.forEach((bucket, index) => {
    const count = Number(bucket.deal_count || 0);
    const barHeight = plotHeight * count / maxCount;
    const x = left + slot * index + (slot - barWidth) / 2;
    const yPos = top + plotHeight - barHeight;
    const bar = element('rect', { x, y: yPos, width: barWidth, height: barHeight, rx: 5, class: 'activity-distribution-bar' });
    bar.append(element('title', {}, `${bucket.label}: ${numberFormat.format(count)} сделок`));
    svg.append(bar);
    svg.append(element('text', { x: x + barWidth / 2, y: Math.max(top - 4, yPos - 7), class: 'chart-value-label', 'text-anchor': 'middle' }, numberFormat.format(count)));
    svg.append(element('text', { x: left + slot * index + slot / 2, y: height - 27, class: 'chart-axis-label', 'text-anchor': 'middle' }, bucket.label.replace(' дней', '')));
  });
  svg.append(element('text', { x: width / 2, y: height - 7, class: 'chart-axis-label', 'text-anchor': 'middle' }, 'Давность последней активности'));
  host.append(svg);
}

async function loadActivityDistribution() {
  const requestId = ++activityDistributionRequest;
  byId('activity-distribution-loading').hidden = false;
  byId('activity-distribution-empty').hidden = true;
  try {
    const project = byId('activity-distribution-category').value || 'all';
    const response = await fetch(`/api/deal-activity-distribution?project=${encodeURIComponent(project)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить распределение сделок');
    if (requestId !== activityDistributionRequest) return;
    if (renderDistributionCategories(data.categories || []) !== project) {
      loadActivityDistribution();
      return;
    }
    renderActivityDistribution(data);
  } catch (error) {
    if (requestId !== activityDistributionRequest) return;
    byId('activity-distribution-loading').hidden = true;
    byId('activity-distribution-chart').hidden = true;
    byId('activity-distribution-empty').textContent = error.message;
    byId('activity-distribution-empty').hidden = false;
  }
}

function td(text, className = '') {
  const cell = document.createElement('td');
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function renderRedDeals(deals) {
  const body = byId('red-rows');
  body.replaceChildren();
  byId('red-loading').hidden = true;
  if (!deals.length) {
    byId('red-table').hidden = true;
    byId('red-empty').hidden = false;
    return;
  }
  byId('red-empty').hidden = true;
  byId('red-table').hidden = false;
  for (const deal of deals) {
    const row = document.createElement('tr');
    const title = document.createElement('td');
    const label = document.createElement('span');
    label.className = 'red-title';
    label.textContent = deal.title || 'Без названия';
    const uri = document.createElement('span');
    uri.className = 'deal-uri';
    uri.textContent = tail(deal.uri);
    title.append(label, uri);
    row.append(title);
    row.append(td(deal.manager || 'Не назначен'));
    row.append(td(formatDate(deal.created_at)));
    row.append(td(formatDate(deal.last_activity_time), 'muted-cell'));
    body.append(row);
  }
}

function readableDealClasses(raw) {
  if (!raw) return 'Класс не определён';
  const labels = {
    InProgressDeal: 'В процессе',
    ActiveDeal: 'Активная',
    ClosedDeal: 'Закрыта',
    SuccessfulDeal: 'Успешно закрыта'
  };
  return raw.split(' · ').map((name) => labels[name] || name).join(' · ');
}

function renderRecentActivities(activities) {
  const body = byId('activities-rows');
  body.replaceChildren();
  byId('activities-loading').hidden = true;
  byId('toggle-activities').querySelector('.count-badge').textContent = numberFormat.format(activities.length);
  if (!activities.length) {
    byId('activities-table').hidden = true;
    byId('activities-empty').hidden = false;
    return;
  }
  byId('activities-empty').hidden = true;
  byId('activities-table').hidden = false;
  for (const activity of activities) {
    const row = document.createElement('tr');
    row.append(td(formatDate(activity.created_at)));

    const content = document.createElement('td');
    const subject = document.createElement('span');
    subject.className = 'activity-subject';
    subject.textContent = activity.subject || 'Без темы';
    const activityUri = document.createElement('span');
    activityUri.className = 'activity-uri';
    activityUri.textContent = tail(activity.uri);
    content.append(subject, activityUri);
    row.append(content);

    const deal = document.createElement('td');
    const dealClass = document.createElement('span');
    dealClass.className = 'deal-class';
    dealClass.textContent = readableDealClasses(activity.deal_classes);
    const dealTitle = document.createElement('span');
    dealTitle.className = 'activity-deal-title';
    dealTitle.textContent = activity.deal_title || 'Без названия';
    const dealUri = document.createElement('span');
    dealUri.className = 'activity-uri';
    dealUri.textContent = tail(activity.deal_uri);
    deal.append(dealClass, dealTitle, dealUri);
    row.append(deal);

    const employee = document.createElement('td');
    const employeeName = document.createElement('span');
    employeeName.className = 'activity-employee';
    employeeName.textContent = activity.employee_name || 'Сотрудник не указан';
    const employeeUri = document.createElement('span');
    employeeUri.className = 'activity-uri';
    employeeUri.textContent = tail(activity.employee_uri);
    employee.append(employeeName, employeeUri);
    row.append(employee);
    body.append(row);
  }
}

async function loadPulse() {
  const button = byId('refresh');
  button.disabled = true;
  try {
    const project = selectedProject();
    const response = await fetch(`/api/pulse?project=${encodeURIComponent(project)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить данные');
    renderPulse(data);
    loadActivityDistribution();
  } catch (error) {
    byId('connection').className = 'connection offline';
    byId('connection').lastElementChild.textContent = 'Нет соединения';
    byId('error').textContent = error.message;
    byId('error').hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function loadRedDeals() {
  byId('red-loading').hidden = false;
  byId('red-empty').hidden = true;
  byId('red-table').hidden = true;
  try {
    const response = await fetch(`/api/red-deals?project=${encodeURIComponent(selectedProject())}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить красные сделки');
    const deals = data.red_deals || [];
    renderRedDeals(deals);
  } catch (error) {
    byId('red-loading').textContent = error.message;
  }
}

async function loadRecentActivities() {
  byId('activities-loading').hidden = false;
  byId('activities-loading').textContent = 'Загружаю последние активности…';
  byId('activities-empty').hidden = true;
  byId('activities-table').hidden = true;
  try {
    const response = await fetch(`/api/recent-activities?project=${encodeURIComponent(selectedProject())}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось получить последние активности');
    renderRecentActivities(data.activities || []);
  } catch (error) {
    byId('activities-loading').textContent = error.message;
  }
}

function selectedProject() {
  return byId('project-filter').value || 'all';
}

byId('refresh').addEventListener('click', () => {
  loadPulse();
  if (byId('toggle-red').getAttribute('aria-expanded') === 'true') loadRedDeals();
  if (byId('toggle-activities').getAttribute('aria-expanded') === 'true') loadRecentActivities();
});
byId('toggle-red').addEventListener('click', () => {
  const button = byId('toggle-red');
  const open = button.getAttribute('aria-expanded') !== 'true';
  button.setAttribute('aria-expanded', String(open));
  byId('red-content').hidden = !open;
  button.firstElementChild.textContent = open ? 'Скрыть список' : 'Показать список';
  if (open) loadRedDeals();
});
byId('toggle-activities').addEventListener('click', () => {
  const button = byId('toggle-activities');
  const open = button.getAttribute('aria-expanded') !== 'true';
  button.setAttribute('aria-expanded', String(open));
  byId('activities-content').hidden = !open;
  button.firstElementChild.textContent = open ? 'Скрыть список' : 'Показать список';
  if (open) loadRecentActivities();
});
byId('project-filter').addEventListener('change', () => {
  loadPulse();
  if (byId('toggle-red').getAttribute('aria-expanded') === 'true') loadRedDeals();
  if (byId('toggle-activities').getAttribute('aria-expanded') === 'true') loadRecentActivities();
});
byId('activity-distribution-category').addEventListener('change', loadActivityDistribution);

loadPulse();
window.setInterval(() => {
  loadPulse();
  if (byId('toggle-red').getAttribute('aria-expanded') === 'true') loadRedDeals();
  if (byId('toggle-activities').getAttribute('aria-expanded') === 'true') loadRecentActivities();
}, 60_000);
