import { useEffect, useState } from 'react';
import { apiJson, type Category, type ViewStateResponse } from './api';
import { blockProps, Situations, useRefreshRevision } from './AnalyticsView';
import { Connection, DashboardHeader, PageNav, RefreshButton, SectionTab } from './DashboardChrome';
import { formatDate, formatNumber } from './DealsDetails';
import { formatDuration, TrendChart } from './TrendChart';
import { useApiRequest } from './useApiRequest';

type LeadSummary = Partial<Record<
  'created_1d' | 'created_7d' | 'created_30d' | 'average_response_minutes' | 'response_sample_size' |
  'sla_within_2h_ratio' | 'sla_within_2h_count' | 'sla_eligible_count', number | null>>;
type Lead = { uri?: string; created_at?: string; title?: string; source_name?: string; source_code?: string; manager?: string };
type LeadPulse = {
  backend?: string; as_of?: string; summary?: LeadSummary; red_leads?: Lead[];
  response_trend?: Array<LeadSummary & { date: string }>;
};
type Conversion = {
  categories?: Category[];
  conversion_trend?: Array<{ date: string; average_conversion_minutes?: number | null; sample_size?: number }>;
};
const loadPulse = (_: string, signal: AbortSignal) => apiJson<LeadPulse>('/api/lead-pulse', { signal });
const loadConversion = (category: string, signal: AbortSignal) =>
  apiJson<Conversion>(`/api/lead-conversion-trend?category=${encodeURIComponent(category)}`, { signal });
const loadView = (category: string, signal: AbortSignal) =>
  apiJson<ViewStateResponse>(`/api/view-state?page=leads&category=${encodeURIComponent(category)}`, { signal });
const percentage = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 });
function leadSource(lead: Lead) {
  const name = lead.source_name || lead.source_code || 'Не указан';
  const code = lead.source_name && lead.source_code ? ` (${lead.source_code})` : '';
  return `Источник: ${name}${code}`;
}

export function LeadsPage() {
  const [category, setCategory] = useState('all');
  const [categories, setCategories] = useState<Category[]>([]);
  const [summaries, setSummaries] = useState(false);
  const [revision, refresh] = useRefreshRevision('Пульс лидов — Semantic BI');
  const [tabRevision, setTabRevision] = useState(0);
  const pulse = useApiRequest('all', loadPulse, revision);
  const conversion = useApiRequest(category, loadConversion, revision + tabRevision);
  const view = useApiRequest(category, loadView, revision);
  useEffect(() => {
    if (conversion.status !== 'success') return;
    const next = conversion.data.categories ?? [];
    setCategories(next);
    if (category !== 'all' && !next.some(({ id }) => id === category)) setCategory('all');
  }, [category, conversion]);

  const summary = pulse.data?.summary ?? {};
  const leads = pulse.data?.red_leads ?? [];
  const offline = pulse.status === 'error';
  const conversionPoints = (conversion.status === 'error' ? [] : conversion.data?.conversion_trend ?? []).map((point) => ({
    date: point.date,
    value: point.average_conversion_minutes,
    tooltip: (label: string) => `${label}: ${formatDuration(point.average_conversion_minutes)} · ${formatNumber(point.sample_size)} сделок`,
  }));
  const responsePoints = (pulse.data?.response_trend ?? []).map((point) => ({
    date: point.date,
    value: point.sla_within_2h_ratio,
    tooltip: (label: string) => `${label}: ${formatNumber(point.sla_within_2h_count ?? 0)} из ${formatNumber(point.sla_eligible_count ?? 0)} · ${point.sla_within_2h_ratio == null ? 'нет данных' : percentage.format(point.sla_within_2h_ratio)}`,
  }));
  return <main className="shell">
    <DashboardHeader title="CRM Pulse">
      <label className="team-filter global-filter">
        <span>Воронка</span>
        <select id="lead-conversion-category" className="project-filter" aria-label="Фильтр по воронке"
          value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="all">Все воронки</option>
          {categories.map(({ id, name }) => <option key={id} value={id}>{name || `Воронка ${id}`}</option>)}
        </select>
      </label>
      <Connection status={offline ? 'offline' : pulse.data ? 'online' : 'pending'}>
        {offline ? 'Нет соединения' : pulse.data
          ? pulse.data.backend === 'fuseki' ? 'Fuseki подключён' : 'GraphDB подключён'
          : 'Подключение…'}
      </Connection>
      <span id="updated" className="updated">{pulse.data ? `Обновлено ${pulse.data.as_of ? formatDate(pulse.data.as_of) : 'Нет активности'}` : '—'}</span>
      <RefreshButton disabled={pulse.status === 'loading'} onClick={refresh}/>
    </DashboardHeader>
    <PageNav active="/leads"/>
    <nav className="section-tabs" aria-label="Разделы лидов">
      <SectionTab id="leads-tab" panel="leads-tab-panel" active={!summaries} onClick={() => setSummaries(false)}>Лиды</SectionTab>
      <SectionTab id="summaries-tab" panel="summaries-tab-panel" active={summaries} onClick={() => {
        setSummaries(true);
        setTabRevision((value) => value + 1);
      }}>Сводки</SectionTab>
    </nav>
    <div id="error" className="error-banner" role="alert" hidden={!offline}>{pulse.status === 'error' && pulse.error}</div>
    <div id="leads-tab-panel" className="tab-content" hidden={summaries}>
      <Situations data={view.status === 'error' ? undefined : view.data}/>
      <div id="page-blocks" className="page-blocks">
        <section id="leads-kpi" data-block="kpi" aria-labelledby="leads-title" {...blockProps(view.data, 'kpi', 'block')}>
          <div className="section-heading">
            <div><p className="eyebrow">ВХОДЯЩИЙ ПОТОК</p><h2 id="leads-title">Лиды (сводные показатели)</h2></div>
            <p className="section-note">Даты и интервалы считаются по текущему времени</p>
          </div>
          <div className="metric-grid lead-metric-grid">
            {([['1d', '1 день'], ['7d', '7 дней'], ['30d', '30 дней']] as const).map(([suffix, period]) =>
              <article key={suffix} className={suffix === '1d' ? 'metric-card metric-primary' : 'metric-card'}>
                <div className="metric-label">Поступило за {period}</div>
                <div id={`created-${suffix}`} className="metric-value">{pulse.data ? formatNumber(summary[`created_${suffix}`] ?? 0) : '—'}</div>
                <div className="metric-foot">По дате создания лида</div>
              </article>)}
            <article className="metric-card">
              <div className="metric-label">Средняя скорость реакции</div>
              <div id="average-response" className="metric-value metric-value-text">{pulse.data ? formatDuration(summary.average_response_minutes) : '—'}</div>
              <div id="response-sample" className="metric-foot">{pulse.data
                ? `До первой активности · выборка ${formatNumber(summary.response_sample_size ?? 0)} лидов за 30 дней`
                : 'Время до первой активности · лиды за 30 дней'}</div>
            </article>
            <article className="metric-card metric-primary">
              <div className="metric-label">Первая активность за 2 часа</div>
              <div id="sla-within-2h" className="metric-value">{pulse.data
                ? summary.sla_within_2h_ratio == null ? 'Нет данных' : percentage.format(summary.sla_within_2h_ratio)
                : '—'}</div>
              <div id="sla-sample" className="metric-foot">{pulse.data
                ? `${formatNumber(summary.sla_within_2h_count ?? 0)} из ${formatNumber(summary.sla_eligible_count ?? 0)} лидов · прошло не менее 2 часов`
                : 'Среди лидов, для которых прошло 2 часа'}</div>
            </article>
          </div>
        </section>
        <section id="leads-red" data-block="red_leads" aria-labelledby="red-leads-title" {...blockProps(view.data, 'red_leads', 'panel block')}>
          <div className="panel-heading"><div>
            <p className="eyebrow">КОНТРОЛЬ РЕАКЦИИ</p>
            <h2 id="red-leads-title">Красные лиды</h2>
            <p className="panel-subtitle">Лиды без активности в первые 2 часа, без связанной сделки и без статуса «Конвертирован» · вся история</p>
          </div></div>
          <div id="leads-loading" className="empty-state" hidden={!!pulse.data || offline}>Загружаю данные…</div>
          <div className="table-wrap">
            <table id="leads-table" hidden={!leads.length}>
              <thead><tr><th>Создан</th><th>Лид</th><th>Источник</th><th>Ответственный</th></tr></thead>
              <tbody id="lead-rows">{leads.map((lead, index) => <tr key={lead.uri ?? index}>
                <td>{lead.created_at ? formatDate(lead.created_at) : 'Нет активности'}</td>
                <td className="employee-name">{lead.title || 'Без названия'}</td>
                <td><span className="lead-meta-line" title={lead.source_code ? `sourceCode: ${lead.source_code}` : undefined}>{leadSource(lead)}</span></td>
                <td>{lead.manager || 'Не назначен'}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <div id="leads-empty" className="empty-state" hidden={!pulse.data || leads.length > 0}>Красных лидов нет.</div>
        </section>
      </div>
    </div>
    <section id="summaries-tab-panel" className="tab-content page-blocks" aria-labelledby="summaries-tab" hidden={!summaries}>
      <section id="leads-conversion-trend" data-block="conversion_trend" aria-labelledby="conversion-trend-title" {...blockProps(view.data, 'conversion_trend', 'panel block')}>
        <div className="panel-heading"><div>
          <p className="eyebrow">СКОРОСТЬ КОНВЕРСИИ</p>
          <h2 id="conversion-trend-title">Пульс конвертации лида в сделку</h2>
          <p className="panel-subtitle">Среднее время до связанной сделки P · скользящее окно 30 дней · последние 90 дней</p>
        </div></div>
        <TrendChart id="conversion-trend" label="График среднего времени конвертации лидов в сделки по дням"
          empty={conversion.status === 'error' ? conversion.error : conversion.status === 'loading' && !conversion.data
            ? 'Загружаю данные…' : 'Нет данных о конверсиях в выбранной воронке.'}
          points={conversionPoints}/>
      </section>
      <section id="leads-response-trend" data-block="response_trend" aria-labelledby="response-trend-title" {...blockProps(view.data, 'response_trend', 'panel block')}>
        <div className="panel-heading"><div>
          <p className="eyebrow">СКОРОСТЬ ОБРАБОТКИ</p>
          <h2 id="response-trend-title">Доля лидов с первой активностью за 2 часа</h2>
          <p className="panel-subtitle">SLA для созревших лидов из 30-дневной когорты · последние 90 дней</p>
        </div></div>
        <TrendChart id="sla-trend" label="График доли лидов с первой активностью за 2 часа по дням"
          empty="Недостаточно данных для графика." points={responsePoints} ceiling={1} tickLabel={(value) => `${Math.round(value * 100)}%`}/>
      </section>
      <footer>
        <span>Только чтение · GraphDB · bitrix24_v2_parallel</span>
        <span>Конверсия определяется по originLead связанной сделки со stageSemantic = P.</span>
      </footer>
    </section>
  </main>;
}
