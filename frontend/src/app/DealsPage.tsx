import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { dealsApi, type Category } from './api';
import { blockProps, Situations, useRefreshRevision } from './AnalyticsView';
import { AnalyticsHeader, SectionTab } from './DashboardChrome';
import { ActivityRow, DistributionChart, RedDealRow, formatNumber } from './DealsDetails';
import { useApiRequest } from './useApiRequest';

const metrics = [
  ['active_deals', 'active-deals', 'Всего активных', 'Семантика стадии P', ' metric-primary'],
  ['created_24h', 'created-24h', 'Создано за 24 часа', 'Новые сделки в работе', ''],
  ['created_7d', 'created-7d', 'Создано за 7 дней', 'Новые сделки в работе', ''],
  ['created_30d', 'created-30d', 'Создано за 30 дней', 'Новые сделки в работе', ''],
  ['red_deals_count', 'red-deals-count', 'Красные сделки', 'Старше 10 дней · активность только в первые сутки', ' metric-danger'],
] as const;

function ListContent({ id, open, status, error, loading, empty, columns, children }: {
  id: string; open: boolean; status: string; error?: string; loading: string; empty: string;
  columns: string[]; children: ReactNode[];
}) {
  const ready = status === 'success';
  return <div id={`${id}-content`} className={`${id}-content`} hidden={!open}>
    <div id={`${id}-loading`} className="empty-state" hidden={ready}>{error || loading}</div>
    <div className="table-wrap">
      <table id={`${id}-table`} hidden={!ready || !children.length}>
        <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody id={`${id}-rows`}>{children}</tbody>
      </table>
    </div>
    <div id={`${id}-empty`} className="empty-state" hidden={!ready || children.length > 0}>{empty}</div>
  </div>;
}

export function DealsPage() {
  const [project, setProject] = useState('all');
  const [summaries, setSummaries] = useState(false);
  const [redOpen, setRedOpen] = useState(false);
  const [activitiesOpen, setActivitiesOpen] = useState(false);
  const [activityCount, setActivityCount] = useState(20);
  const [revision, refresh] = useRefreshRevision('Пульс сделок — Semantic BI');

  const pulse = useApiRequest(project, dealsApi.pulse, revision);
  const view = useApiRequest(project, dealsApi.viewState, revision);
  const red = useApiRequest(redOpen ? project : null, dealsApi.redDeals, revision);
  const recent = useApiRequest(activitiesOpen ? project : null, dealsApi.recentActivities, revision);
  const distribution = useApiRequest(summaries ? project : null, dealsApi.distribution, revision);
  const [categories, setCategories] = useState<Category[]>();
  const profile = view.data?.view_profile;

  useEffect(() => {
    if (pulse.status !== 'success') return;
    const options = pulse.data.categories ?? [];
    setCategories(options);
    if (project !== 'all' && !options.some(({ id }) => id === project)) setProject('all');
    if (project === 'all' && view.status === 'success' &&
      profile?.default_filters?.project === 'nonempty' && options.length) {
      setProject(options[0].id);
    }
  }, [pulse, project, profile, view.status]);

  useEffect(() => {
    if (recent.status === 'success') setActivityCount(recent.data.activities?.length ?? 0);
  }, [recent]);

  const block = (name: string, base: string) => blockProps(view.data, name, base);
  const summary = pulse.data?.summary;
  const metric = (value?: number) => pulse.data ? formatNumber(value) : '—';
  const buckets = distribution.data?.buckets ?? [];
  const chartVisible = distribution.status !== 'error' &&
    Number(distribution.data?.total_deals || 0) > 0 && buckets.length > 0;
  const offline = pulse.status === 'error';

  return <main className="shell">
    <AnalyticsHeader title="Пульс сделок" tabsLabel="Разделы сделок"
      data={pulse.data} offline={offline} refreshing={pulse.status === 'loading'} onRefresh={refresh}
      filter={
        <select id="project-filter" className="project-filter" aria-label="Фильтр дашборда по воронке"
          value={project} onChange={(event) => setProject(event.target.value)}>
          <option value="all">Все воронки</option>
          {categories?.map(({ id, name }) => <option key={id} value={id}>{name || `Категория ${id}`}</option>)}
        </select>
      }>
      <SectionTab id="deals-tab" panel="deals-tab-panel" active={!summaries} onClick={() => setSummaries(false)}>Сделки</SectionTab>
      <SectionTab id="summaries-tab" panel="summaries-tab-panel" active={summaries} onClick={() => setSummaries(true)}>Сводки</SectionTab>
    </AnalyticsHeader>

    <div id="deals-tab-panel" className="tab-content" hidden={summaries}>
      <div id="error" className="error-banner" role="alert" hidden={!offline}>{pulse.status === 'error' && pulse.error}</div>
      <Situations data={view.status === 'error' ? undefined : view.data}/>
      <div id="page-blocks" className="page-blocks">
        <section id="deals-kpi" data-block="kpi" aria-labelledby="pulse-title" {...block('kpi', 'block')}>
          <div className="section-heading">
            <div>
              <p className="eyebrow">ТЕКУЩАЯ ВОРОНКА</p>
              <h2 id="pulse-title">Активные сделки (сводные показатели) <span className="stage-pill">P · В работе</span></h2>
            </div>
            <p className="section-note">Срез обновляется автоматически · все интервалы считаются от текущего времени</p>
          </div>
          <div className="metric-grid">{metrics.map(([field, id, label, foot, modifier]) =>
            <article key={field} className={`metric-card${modifier}`}>
              <div className="metric-label">{label}</div>
              <div id={id} className="metric-value">{metric(summary?.[field])}</div>
              <div className="metric-foot">{foot}</div>
            </article>)}</div>
        </section>

        <section id="deals-red" data-block="red_deals" aria-labelledby="red-title" {...block('red_deals', 'panel red-panel block')}>
          <div className="panel-heading">
            <div>
              <p className="eyebrow danger-eyebrow">ТРЕБУЮТ ВНИМАНИЯ</p>
              <h2 id="red-title">Красные сделки</h2>
              <p className="panel-subtitle">Старше 10 дней · активности только в первые сутки · показаны до 30 сделок</p>
            </div>
            <div className="red-controls">
              <button id="toggle-red" className="secondary-button" type="button" aria-expanded={redOpen}
                aria-controls="red-content" onClick={() => setRedOpen(!redOpen)}>
                <span>{redOpen ? 'Скрыть список' : 'Показать список'}</span><span id="red-count" className="count-badge">{metric(summary?.red_deals_count)}</span>
              </button>
            </div>
          </div>
          <ListContent id="red" open={redOpen} status={red.status} error={'error' in red ? red.error : undefined}
            loading="Загружаю список сделок…" empty="Сделок по этому критерию нет."
            columns={['Сделка', 'Ответственный', 'Создана', 'lastActivityTime', 'Последняя по bindingDeal', 'Последняя по owner']}>
            {(red.data?.red_deals ?? []).map((deal, index) => <RedDealRow key={deal.uri ?? index} deal={deal}/>)}
          </ListContent>
        </section>

        <section id="deals-activities" data-block="recent_activities" aria-labelledby="activities-title" {...block('recent_activities', 'panel activities-panel block')}>
          <div className="panel-heading">
            <div>
              <p className="eyebrow">ПОСЛЕДНИЕ СОБЫТИЯ</p>
              <h2 id="activities-title">20 последних активностей по сделкам</h2>
              <p className="panel-subtitle">Тема активности, семантический класс и название сделки, ответственный сотрудник.</p>
            </div>
            <button id="toggle-activities" className="secondary-button" type="button" aria-expanded={activitiesOpen}
              aria-controls="activities-content" onClick={() => setActivitiesOpen(!activitiesOpen)}>
              <span>{activitiesOpen ? 'Скрыть список' : 'Показать список'}</span><span className="count-badge activity-count">{formatNumber(activityCount)}</span>
            </button>
          </div>
          <ListContent id="activities" open={activitiesOpen} status={recent.status} error={'error' in recent ? recent.error : undefined}
            loading="Загружаю последние активности…" empty="Активностей по сделкам пока нет."
            columns={['Дата', 'Содержание активности', 'Сделка', 'Сотрудник']}>
            {(recent.data?.activities ?? []).map((activity, index) => <ActivityRow key={activity.uri ?? index} activity={activity}/>)}
          </ListContent>
        </section>
      </div>
    </div>

    <section id="summaries-tab-panel" className="tab-content" aria-labelledby="summaries-tab" hidden={!summaries}>
      <section id="deals-activity-distribution" className="panel" aria-labelledby="activity-distribution-title">
        <div className="panel-heading"><div>
          <p className="eyebrow">ДАВНОСТЬ АКТИВНОСТИ</p>
          <h2 id="activity-distribution-title">Распределение активных сделок по последней активности</h2>
          <p className="panel-subtitle">Интервалы по 5 дней · сделки стадии P</p>
        </div></div>
        <div id="activity-distribution-loading" className="empty-state" hidden={distribution.status !== 'loading'}>Загружаю распределение…</div>
        <DistributionChart buckets={buckets} hidden={!chartVisible}/>
        <div id="activity-distribution-empty" className="empty-state" hidden={chartVisible || distribution.status === 'loading'}>
          {distribution.status === 'error' ? distribution.error : 'Нет сделок для распределения.'}
        </div>
      </section>
    </section>
    <footer>
      <span>Только чтение · GraphDB · bitrix24_v2_parallel</span>
      <span>Активность считается по дате создания записи CRM Activity.</span>
    </footer>
  </main>;
}
