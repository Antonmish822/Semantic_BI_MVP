import { useEffect, useState } from 'react';
import { api, number, rows, text, type Row } from '../../shared/api';
import { Layout, Notice, Panel } from '../../shared/Layout';

function useData<T extends Row>(path: string, dependencies: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError('');
    api<T>(path, { signal: controller.signal }).then(setData).catch((cause: unknown) => {
      if (cause instanceof DOMException && cause.name === 'AbortError') return;
      setError(cause instanceof Error ? cause.message : 'Не удалось загрузить данные');
    }).finally(() => setBusy(false));
    return () => controller.abort();
    // Path and explicit dependency values define each request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...dependencies]);
  return { data, error, busy, refresh: () => setNonce((value) => value + 1) };
}

function MetricGrid({ values }: { values: Array<[string, unknown, string?]> }) {
  return <div className="react-metrics">{values.map(([label, value, detail]) => <article className="react-metric" key={label}><span>{label}</span><strong>{number(value)}</strong>{detail && <small>{detail}</small>}</article>)}</div>;
}

function DataTable({ items, empty = 'Нет данных для отображения.' }: { items: Row[]; empty?: string }) {
  if (!items.length) return <p className="empty-state">{empty}</p>;
  const columns = Object.keys(items[0]).filter((key) => !key.startsWith('_') && typeof items[0][key] !== 'object').slice(0, 6);
  return <div className="table-wrap"><table><thead><tr>{columns.map((key) => <th key={key}>{key.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{items.map((item, index) => <tr key={String(item.uri || item.id || index)}>{columns.map((key) => <td key={key}>{text(item[key], typeof item[key] === 'number' ? number(item[key]) : '—')}</td>)}</tr>)}</tbody></table></div>;
}

function Trend({ values, metric, label }: { values: Row[]; metric: string; label: string }) {
  const points = values.slice(-30);
  const numbers = points.map((point) => typeof point[metric] === 'number' ? point[metric] as number : 0);
  const maximum = Math.max(1, ...numbers);
  return <div className="react-trend" role="img" aria-label={label}>{points.map((point, index) => <span key={String(point.date || index)} title={`${text(point.date)} · ${number(point[metric])}`} style={{ height: `${Math.max(3, ((numbers[index] || 0) / maximum) * 100)}%` }} />)}</div>;
}

function Filter({ value, onChange, categories }: { value: string; onChange: (value: string) => void; categories: Row[] }) {
  return <label className="team-filter"><span>Воронка</span><select className="project-filter" value={value} onChange={(event) => onChange(event.target.value)}><option value="all">Все воронки</option>{categories.map((item) => <option value={text(item.id)} key={text(item.id)}>{text(item.name)}</option>)}</select></label>;
}

export function DealsPage() {
  const [project, setProject] = useState('all');
  const pulse = useData(`/api/pulse?project=${encodeURIComponent(project)}`, [project]);
  const red = useData(`/api/red-deals?project=${encodeURIComponent(project)}`, [project]);
  const activities = useData(`/api/recent-activities?project=${encodeURIComponent(project)}`, [project]);
  const distribution = useData(`/api/deal-activity-distribution?project=${encodeURIComponent(project)}`, [project]);
  const view = useData(`/api/view-state?page=deals&project=${encodeURIComponent(project)}`, [project]);
  const summary = (pulse.data?.summary || {}) as Row;
  const categories = rows(pulse.data?.categories);
  const error = pulse.error || red.error || activities.error || distribution.error || view.error;
  return <Layout title="Пульс сделок"><div className="react-toolbar"><p className="eyebrow">ТЕКУЩАЯ ВОРОНКА · ОБНОВЛЯЕТСЯ ИЗ RDF</p><Filter value={project} onChange={setProject} categories={categories} /><button className="refresh" onClick={() => [pulse, red, activities, distribution, view].forEach((item) => item.refresh())}>Обновить</button></div>
    {error && <Notice error>{error}</Notice>}
    <MetricGrid values={[["Активные сделки", summary.active_deals], ["Создано за 24 часа", summary.created_24h], ["Создано за 7 дней", summary.created_7d], ["Создано за 30 дней", summary.created_30d], ["Без активности", summary.red_deals_count]]} />
    <div className="react-grid"><Panel title="Красные сделки" eyebrow="ТРЕБУЮТ ВНИМАНИЯ"><DataTable items={rows(red.data?.red_deals)} empty={red.busy ? 'Загружаю сделки…' : 'Красных сделок нет.'} /></Panel>
      <Panel title="Активности по давности" eyebrow="РАСПРЕДЕЛЕНИЕ"><DataTable items={rows(distribution.data?.buckets)} empty={distribution.busy ? 'Загружаю распределение…' : 'Распределение пусто.'} /></Panel></div>
    <Panel title="Последние активности" eyebrow="ПОСЛЕДНИЕ СОБЫТИЯ"><DataTable items={rows(activities.data?.activities)} empty={activities.busy ? 'Загружаю активности…' : 'Активностей нет.'} /></Panel>
    <Panel title="Адаптивный вид" eyebrow="СХЕМА И ПРАВИЛА"><div className="react-status-row"><span>Состояние данных</span><b>{view.data?.available === false ? 'Недоступно' : view.busy ? 'Загрузка' : 'Готово'}</b></div><div className="react-tags">{rows(view.data?.active_situations).map((item, index) => <span key={String(item.id || index)}>{text(item.title)}</span>)}</div></Panel>
  </Layout>;
}

export function LeadsPage() {
  const [category, setCategory] = useState('all');
  const lead = useData('/api/lead-pulse');
  const trend = useData(`/api/lead-conversion-trend?category=${encodeURIComponent(category)}`, [category]);
  const view = useData(`/api/view-state?page=leads&category=${encodeURIComponent(category)}`, [category]);
  const summary = (lead.data?.summary || {}) as Row;
  const categories = rows(trend.data?.categories || lead.data?.categories);
  const responseTrend = rows(lead.data?.response_trend);
  const conversionTrend = rows(trend.data?.conversion_trend);
  const errors = [lead.error, trend.error, view.error].filter(Boolean);
  return <Layout title="Пульс лидов"><div className="react-toolbar"><p className="eyebrow">СКОРОСТЬ РЕАКЦИИ · SLA 2 ЧАСА</p><Filter value={category} onChange={setCategory} categories={categories} /></div>
    {errors.map((error) => <Notice error key={error}>{error}</Notice>)}
    <MetricGrid values={[["Поступило за день", summary.created_1d], ["За 7 дней", summary.created_7d], ["За 30 дней", summary.created_30d], ["Средняя реакция, мин", summary.average_response_minutes], ["SLA до 2 часов, %", typeof summary.sla_within_2h_ratio === 'number' ? Math.round(summary.sla_within_2h_ratio * 100) : null, `Созревшая когорта: ${number(summary.sla_eligible_count)}`]]} />
    <div className="react-grid"><Panel title="Красные лиды" eyebrow="НЕТ АКТИВНОСТИ В ПЕРВЫЕ 2 ЧАСА"><DataTable items={rows(lead.data?.red_leads)} empty={lead.busy ? 'Загружаю лиды…' : 'Красных лидов нет.'} /></Panel>
      <Panel title="Первая активность за 2 часа" eyebrow="СКОЛЬЗЯЩЕЕ ОКНО 30 ДНЕЙ"><Trend values={responseTrend} metric="sla_within_2h_ratio" label="Динамика SLA первой активности" /><p className="panel-subtitle">90 дневных точек · показатель доли от 0 до 1</p></Panel></div>
    <Panel title="Время конвертации" eyebrow="ЛИД → СДЕЛКА P"><Trend values={conversionTrend} metric="average_conversion_minutes" label="Среднее время конвертации" /><DataTable items={conversionTrend.slice(-7)} empty={trend.busy ? 'Загружаю тренд…' : 'Недостаточно данных.'} /></Panel>
  </Layout>;
}

export function EmployeesPage() {
  const [project, setProject] = useState('all');
  const [employee, setEmployee] = useState('all');
  const pulse = useData(`/api/pulse?project=${encodeURIComponent(project)}`, [project]);
  const activities = useData(`/api/employee-recent-activities?project=${encodeURIComponent(project)}&employee=${encodeURIComponent(employee)}`, [project, employee]);
  const trend = useData(`/api/lead-employee-pulse?employee=${encodeURIComponent(employee)}`, [employee]);
  const view = useData(`/api/view-state?page=employees&project=${encodeURIComponent(project)}&employee=${encodeURIComponent(employee)}`, [project, employee]);
  const employees = rows(pulse.data?.employees);
  const options = rows(trend.data?.employees);
  const errors = [pulse.error, activities.error, trend.error, view.error].filter(Boolean);
  return <Layout title="Сотрудники"><div className="react-toolbar"><p className="eyebrow">НАГРУЗКА И РЕАКЦИЯ</p><Filter value={project} onChange={setProject} categories={rows(pulse.data?.categories)} /><label className="team-filter"><span>Сотрудник</span><select className="project-filter" value={employee} onChange={(event) => setEmployee(event.target.value)}><option value="all">Все сотрудники</option>{options.map((item) => <option key={text(item.uri)} value={text(item.uri)}>{text(item.name)}</option>)}</select></label></div>
    {errors.map((error) => <Notice error key={error}>{error}</Notice>)}
    <Panel title="Портфель активных сделок" eyebrow="ТОП СОТРУДНИКОВ"><DataTable items={employees} empty={pulse.busy ? 'Загружаю сотрудников…' : 'Нет активных сотрудников.'} /></Panel>
    <div className="react-grid"><Panel title="Последние активности" eyebrow="10 СОБЫТИЙ"><DataTable items={rows(activities.data?.activities)} empty={activities.busy ? 'Загружаю активности…' : 'Активностей нет.'} /></Panel>
      <Panel title="Пульс лидов команды" eyebrow="РЕАКЦИЯ И КОНВЕРСИЯ"><Trend values={rows(trend.data?.employee_trend)} metric="average_response_minutes" label="Среднее время реакции сотрудников" /><p className="panel-subtitle">Реакция по дням · последние 30 значений</p></Panel></div>
  </Layout>;
}
