import { useEffect, useState } from 'react';
import { apiJson, dealsApi, type Category, type ViewStateResponse } from './api';
import { blockProps, Situations, useRefreshRevision } from './AnalyticsView';
import { Connection, DashboardHeader, PageNav, RefreshButton, SectionTab } from './DashboardChrome';
import { formatDate, formatNumber } from './DealsDetails';
import { formatDuration, TrendChart } from './TrendChart';
import { useApiRequest, type RequestState } from './useApiRequest';

type Employee = { id: string; name?: string };
type Activity = {
  uri?: string; created_at?: string; subject?: string; owner_title?: string;
  owner_uri?: string; employee_name?: string;
};
type EmployeePoint = {
  date: string; average_response_minutes?: number | null; average_conversion_minutes?: number | null;
  response_sample_size?: number; conversion_sample_size?: number;
};
const loadActivities = (key: string, signal: AbortSignal) =>
  apiJson<{ activities?: Activity[]; employees?: Employee[] }>(`/api/employee-recent-activities?${key}`, { signal });
const loadEmployeePulse = (employee: string, signal: AbortSignal) =>
  apiJson<{ employee_trend?: EmployeePoint[]; employees?: Employee[] }>(`/api/lead-employee-pulse?employee=${encodeURIComponent(employee)}`, { signal });
const loadView = (key: string, signal: AbortSignal) =>
  apiJson<ViewStateResponse>(`/api/view-state?page=employees&${key}`, { signal });
const tail = (uri?: string) => uri?.substring(uri.lastIndexOf('/') + 1) ?? '';

function useEmployeeOptions(result: RequestState<{ employees?: Employee[] }>) {
  const [options, setOptions] = useState<Employee[]>([]);
  useEffect(() => {
    if (result.status !== 'success') return;
    const available = (result.data.employees ?? []).filter(({ id }) => id != null);
    setOptions(available);
  }, [result]);
  return options;
}

function EmployeeFilter({ id, label, selected, change, options, fallback }: {
  id: string; label: string; selected: string; change: (value: string) => void;
  options: Employee[]; fallback: (id: string) => string;
}) {
  return <label className="team-filter">
    <span>Сотрудник</span>
    <select id={id} className="project-filter" aria-label={label} value={selected}
      onChange={(event) => change(event.target.value)}>
      <option value="all">Все сотрудники</option>
      {options.map(({ id, name }) => <option key={id} value={id}>{name || fallback(id)}</option>)}
    </select>
  </label>;
}

export function EmployeesPage() {
  const [project, setProject] = useState('all');
  const [categories, setCategories] = useState<Category[]>([]);
  const [summaries, setSummaries] = useState(false);
  const [activitiesOpen, setActivitiesOpen] = useState(false);
  const [activityRevision, setActivityRevision] = useState(0);
  const [summaryRevision, setSummaryRevision] = useState(0);
  const [activityCount, setActivityCount] = useState(10);
  const [revision, refresh] = useRefreshRevision('Сотрудники — Semantic BI');
  const portfolio = useApiRequest(project, dealsApi.pulse, revision);
  const [activityEmployee, setActivityEmployee] = useState('all');
  const [chartEmployee, setChartEmployee] = useState('all');
  const activitiesKey = new URLSearchParams({ project, employee: activityEmployee }).toString();
  const viewKey = `project=${encodeURIComponent(project)}&employee=${encodeURIComponent(chartEmployee)}`;
  const activities = useApiRequest(activitiesKey, loadActivities, revision + activityRevision);
  const chart = useApiRequest(chartEmployee, loadEmployeePulse, revision + summaryRevision);
  const view = useApiRequest(viewKey, loadView, revision);
  const activityOptions = useEmployeeOptions(activities);
  const chartOptions = useEmployeeOptions(chart);

  useEffect(() => {
    if (portfolio.status !== 'success') return;
    const next = portfolio.data.categories ?? [];
    setCategories(next);
    if (project !== 'all' && !next.some(({ id }) => id === project)) setProject('all');
  }, [portfolio, project]);
  useEffect(() => {
    if (activities.status !== 'success') return;
    if (activityEmployee !== 'all' && !activities.data.employees?.some(({ id }) => id === activityEmployee)) {
      setActivityEmployee('all');
      return;
    }
    setActivityCount(activities.data.activities?.length ?? 0);
  }, [activities, activityEmployee]);
  useEffect(() => {
    if (chart.status === 'success' && chartEmployee !== 'all' &&
      !chart.data.employees?.some(({ id }) => id === chartEmployee)) setChartEmployee('all');
  }, [chart, chartEmployee]);

  const offline = portfolio.status === 'error';
  const entries = portfolio.data?.employees ?? [];
  const activityRows = activities.data?.activities ?? [];
  const activityReady = activities.status === 'success' && (activityEmployee === 'all' ||
    activities.data.employees?.some(({ id }) => id === activityEmployee));
  const points = chart.status === 'error' ? [] : chart.data?.employee_trend ?? [];
  const series = (key: 'average_response_minutes' | 'average_conversion_minutes',
    sample: 'response_sample_size' | 'conversion_sample_size', unit: string) => points.map((point) => ({
    date: point.date, value: point[key],
    tooltip: (label: string) => `${label}: ${formatDuration(point[key])} · ${formatNumber(point[sample])} ${unit}`,
  }));

  return <main className="shell">
    <DashboardHeader title="CRM Pulse">
      <label className="team-filter global-filter">
        <span>Воронка</span>
        <select id="project-filter" className="project-filter" aria-label="Общий фильтр по воронке"
          value={project} onChange={(event) => setProject(event.target.value)}>
          <option value="all">Все категории</option>
          {categories.map(({ id, name }) => <option key={id} value={id}>{name || `Категория ${id}`}</option>)}
        </select>
      </label>
      <Connection status={offline ? 'offline' : portfolio.data ? 'online' : 'pending'}>
        {offline ? 'Нет соединения' : portfolio.data
          ? portfolio.data.backend === 'fuseki' ? 'Fuseki подключён' : 'GraphDB подключён'
          : 'Подключение…'}
      </Connection>
      <span id="updated" className="updated">{portfolio.data ? `Обновлено ${formatDate(portfolio.data.as_of)}` : '—'}</span>
      <RefreshButton disabled={portfolio.status === 'loading'} onClick={refresh}/>
    </DashboardHeader>
    <PageNav active="/employees"/>
    <nav className="section-tabs" aria-label="Разделы сотрудников">
      <SectionTab id="employees-tab" panel="employees-tab-panel" active={!summaries} onClick={() => setSummaries(false)}>Сотрудники</SectionTab>
      <SectionTab id="summaries-tab" panel="summaries-tab-panel" active={summaries} onClick={() => {
        setSummaries(true);
        setSummaryRevision((value) => value + 1);
      }}>Сводка</SectionTab>
    </nav>
    <div id="error" className="error-banner" role="alert" hidden={!offline}>{portfolio.status === 'error' && portfolio.error}</div>
    <Situations data={view.status === 'error' ? undefined : view.data}/>
    <div id="employees-tab-panel" className="tab-content" hidden={summaries}>
      <section className="panel activities-panel" aria-labelledby="employee-activities-title">
        <div className="panel-heading">
          <div><p className="eyebrow">ПОСЛЕДНИЕ СОБЫТИЯ</p><h2 id="employee-activities-title">10 последних активностей по сотруднику</h2></div>
          <div className="red-controls">
            <EmployeeFilter id="employee-activities-filter" label="Фильтр активностей по сотруднику"
              selected={activityEmployee} change={setActivityEmployee} options={activityOptions}
              fallback={(id) => `Сотрудник ${tail(id)}`}/>
            <button id="toggle-employee-activities" className="secondary-button" type="button"
              aria-expanded={activitiesOpen} aria-controls="employee-activities-content" onClick={() => {
                setActivitiesOpen(!activitiesOpen);
                if (!activitiesOpen) setActivityRevision((value) => value + 1);
              }}>
              <span>{activitiesOpen ? 'Скрыть список' : 'Показать список'}</span><span id="employee-activities-count" className="count-badge">{formatNumber(activityCount)}</span>
            </button>
          </div>
        </div>
        <div id="employee-activities-content" className="red-content" hidden={!activitiesOpen}>
          <div id="employee-activities-loading" className="empty-state" hidden={activityReady || activities.status === 'error'}>Загружаю активности…</div>
          <div className="table-wrap">
            <table id="employee-activities-table" hidden={!activityReady || !activityRows.length}>
              <thead><tr><th>Дата</th><th>Активность</th><th>CRM-сущность</th><th>Сотрудник</th></tr></thead>
              <tbody id="employee-activities-rows">{activityRows.map((item, index) => <tr key={item.uri ?? index}>
                <td>{formatDate(item.created_at)}</td><td className="employee-name">{item.subject || 'Без темы'}</td>
                <td>{item.owner_title || 'Без названия'}<span className="employee-uri">{tail(item.owner_uri)}</span></td>
                <td>{item.employee_name || 'Сотрудник не указан'}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <div id="employee-activities-empty" className="empty-state"
            hidden={activities.status !== 'error' && (!activityReady || activityRows.length > 0)}>
            {activities.status === 'error' ? activities.error : 'Активностей по выбранным условиям нет.'}
          </div>
        </div>
      </section>
      <div id="page-blocks" className="page-blocks">
        <section id="employees-table" data-block="employees_table" aria-labelledby="team-title" {...blockProps(view.data, 'employees_table', 'panel team-panel block')}>
          <div className="panel-heading team-heading"><div>
            <p className="eyebrow">НАГРУЗКА И РЕАКЦИЯ</p>
            <h2 id="team-title">Сотрудники с наибольшим портфелем</h2>
            <p className="panel-subtitle">Только сотрудники с активностями за 30 дней в выбранной воронке; активности считаются по ответственному CRM-дела, сделки — по назначенному сотруднику.</p>
          </div></div>
          <div id="portfolio-loading" className="empty-state" hidden={portfolio.status !== 'loading'}>Загружаю данные…</div>
          <div className="table-wrap">
            <table id="employee-table" hidden={!entries.length}>
              <thead><tr><th>Сотрудник</th><th className="number">Сделки P</th><th className="number">Активности · 24 ч</th><th className="number">7 дн.</th><th className="number">30 дн.</th><th className="number">Сделки без активности · 7 дн.</th></tr></thead>
              <tbody id="employee-rows">{entries.map((item, index) => <tr key={item.uri ?? index}>
                <td className="employee-name">{item.name || 'Сотрудник не указан'}<span className="employee-uri">{tail(item.uri)}</span></td>
                {(['active_deals', 'activities_24h', 'activities_7d', 'activities_30d', 'inactive_deals_7d'] as const).map((field) =>
                  <td key={field} className="number">{formatNumber(item[field])}</td>)}
              </tr>)}</tbody>
            </table>
          </div>
          <div id="portfolio-empty" className="empty-state" hidden={!portfolio.data || entries.length > 0}>Нет сотрудников с активными сделками P.</div>
        </section>
      </div>
    </div>
    <section id="summaries-tab-panel" className="tab-content page-blocks" aria-labelledby="summaries-tab" hidden={!summaries}>
      <section id="employees-pulse" data-block="employee_pulse" aria-labelledby="employee-pulse-title" {...blockProps(view.data, 'employee_pulse', 'panel block')}>
        <div className="panel-heading">
          <div>
            <p className="eyebrow">ЭФФЕКТИВНОСТЬ КОМАНДЫ</p>
            <h2 id="employee-pulse-title">Пульс сотрудников</h2>
            <p className="panel-subtitle">Средняя реакция на лид и время конвертации · скользящее окно 30 дней · последние 90 дней</p>
          </div>
          <EmployeeFilter id="lead-employee-filter" label="Фильтр графика по сотруднику"
            selected={chartEmployee} change={setChartEmployee} options={chartOptions}
            fallback={() => 'Сотрудник не указан'}/>
        </div>
        <div className="employee-pulse-legend">
          <span><i className="legend-response"/>Средняя скорость реакции</span>
          <span><i className="legend-conversion"/>Среднее время конвертации</span>
        </div>
        <TrendChart id="employee-pulse" label="График скорости реакции и конвертации по дням"
          empty={chart.status === 'error' ? chart.error : chart.status === 'loading' && !chart.data
            ? 'Загружаю данные…' : 'Недостаточно данных по выбранному сотруднику.'}
          points={series('average_response_minutes', 'response_sample_size', 'лидов')}
          secondary={series('average_conversion_minutes', 'conversion_sample_size', 'сделок')}/>
      </section>
    </section>
    <footer>
      <span>Только чтение · GraphDB · bitrix24_v2_parallel</span>
      <span>Пульс рассчитывается по ответственному лида (assignedTo).</span>
    </footer>
  </main>;
}
