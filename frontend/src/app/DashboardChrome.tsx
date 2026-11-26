import type { ReactNode } from 'react';
import { formatDate } from './DealsDetails';
import { ThemeSwitch } from './ThemeSwitch';

const pages = [
  ['/', 'Пульс сделок', 'M3 12h4l3-7 4 14 3-7h4'],
  ['/leads', 'Пульс лидов', 'M3 5h18l-7 8v5l-4 2v-7z'],
  ['/employees', 'Сотрудники', 'M15 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM5 20v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2M3 13a3 3 0 0 1 2-5m16 5a3 3 0 0 0-2-5'],
  ['/agent', 'AI Agent', 'M4 5h16v11H9l-5 4zM8 10h8m-8 3h5'],
  ['/queries', 'Запросы', 'M6 3h8l4 4v14H6zM13 3v5h5m-9 5h6m-6 4h6'],
  ['/tools/ontology', 'Инструменты', 'M4 6h16M4 12h16M4 18h16M8 4v4m8 2v4m-6 2v4'],
] as const;

const tools = [
  ['ontology', 'Онтология', 'M9 3h6v5H9zM3 16h6v5H3zm12 0h6v5h-6zM12 8v4M6 16v-4h12v4'],
  ['queries', 'Запросы', 'M4 5h16v14H4zM8 9l3 3-3 3m5 0h3'],
] as const;

function NavLink({ href, label, icon, active }: { href: string; label: string; icon: string; active: boolean }) {
  return <a className="sidebar-link" aria-current={active ? 'page' : undefined} href={href} title={label}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d={icon}/></svg>
    <span>{label}</span>
  </a>;
}

export function PageNav({ active, tool }: { active: string; tool?: 'ontology' | 'queries' }) {
  return <aside className="app-sidebar">
    <div className="sidebar-brand" aria-hidden="true">
      <span className="sidebar-mark">P</span><span>Semantix</span>
    </div>
    <nav className="sidebar-nav" aria-label="Страницы дашборда">
      {pages.map(([href, label, icon]) =>
        <NavLink key={href} href={href} label={label} icon={icon} active={href === active}/>)}
    </nav>
    {tool && <nav className="sidebar-nav sidebar-tools" aria-label="Инструменты">
      {tools.map(([id, label, icon]) =>
        <NavLink key={id} href={`/tools/${id}`} label={label} icon={icon} active={id === tool}/>)}
    </nav>}
    <div className="sidebar-settings"><ThemeSwitch/></div>
  </aside>;
}

export function DashboardHeader({ title, children }: { title: string; children: ReactNode }) {
  return <header className="topbar">
    <div className="brand">
      <div className="brand-mark" aria-hidden="true">P</div>
      <div><p className="eyebrow">SEMANTIX · BITRIX24</p><h1>{title}</h1></div>
    </div>
    <div className="header-actions"><a className="auth-link" href="/auth/logout">Выйти</a>{children}</div>
  </header>;
}

export function AnalyticsHeader({ title, tabsLabel, children, filter, data, offline, refreshing, onRefresh }: {
  title: string;
  tabsLabel: string;
  children: ReactNode;
  filter: ReactNode;
  data?: { backend?: string; as_of?: string };
  offline: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const status = offline ? 'offline' : data ? 'online' : 'pending';
  const connection = {
    offline: 'Нет соединения',
    online: data?.backend === 'fuseki' ? 'Fuseki подключён' : 'GraphDB подключён',
    pending: 'Подключение…',
  }[status];
  const asOf = data?.as_of;
  const hasTimestamp = asOf && Number.isFinite(Date.parse(asOf));

  return <header className="analytics-header">
    <div className="analytics-heading">
      <h1>{title}</h1>
      <a className="analytics-logout" href="/auth/logout">Выйти</a>
    </div>
    <div className="analytics-toolbar">
      <label className="analytics-filter"><span>Воронка</span>{filter}</label>
      <RefreshButton disabled={refreshing} onClick={onRefresh}/>
      <div className="analytics-data">
        <span id="connection" className={`connection ${status}`} role="status">
          <i aria-hidden="true"/><span>{connection}</span>
        </span>
        {hasTimestamp && <time id="updated" className="analytics-timestamp" dateTime={asOf}>Данные на {formatDate(asOf)}</time>}
      </div>
      <nav className="section-tabs" aria-label={tabsLabel}>{children}</nav>
    </div>
  </header>;
}

function RefreshButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return <button id="refresh" className="refresh" type="button" aria-label="Обновить данные"
    title="Обновить данные" disabled={disabled} onClick={onClick}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9a7 7 0 0 1 11.8-2L20 12M4 12l2.6 5a7 7 0 0 0 11.8-2"/></svg>
    <span>Обновить</span>
  </button>;
}

export function SectionTab({ id, active, panel, children, onClick }: {
  id: string; active: boolean; panel: string; children: ReactNode; onClick: () => void;
}) {
  return <button id={id} type="button" className={active ? 'section-tab active' : 'section-tab'}
    aria-pressed={active} aria-controls={panel} onClick={onClick}>{children}</button>;
}
