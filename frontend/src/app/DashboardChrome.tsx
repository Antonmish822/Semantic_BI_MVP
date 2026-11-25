import type { ReactNode } from 'react';

const pages = [
  ['/', 'Пульс сделок'], ['/leads', 'Пульс лидов'], ['/employees', 'Сотрудники'],
  ['/agent', 'AI Agent'], ['/queries', 'Запросы'], ['/tools/ontology', 'Инструменты'],
];

export function PageNav({ active }: { active: string }) {
  return <nav className="page-nav" aria-label="Страницы дашборда">
    {pages.map(([href, label]) => <a key={href} className={href === active ? 'active' : undefined}
      aria-current={href === active ? 'page' : undefined} href={href}>{label}</a>)}
  </nav>;
}

export function DashboardHeader({ title, children }: { title: string; children: ReactNode }) {
  return <header className="topbar">
    <div className="brand">
      <div className="brand-mark" aria-hidden="true">P</div>
      <div><p className="eyebrow">SEMANTIC BI · BITRIX24</p><h1>{title}</h1></div>
    </div>
    <div className="header-actions"><a className="auth-link" href="/auth/logout">Выйти</a>{children}</div>
  </header>;
}

export function RefreshButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return <button id="refresh" className="refresh" type="button" aria-label="Обновить данные"
    title="Обновить данные" disabled={disabled} onClick={onClick}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9a7 7 0 0 1 11.8-2L20 12M4 12l2.6 5a7 7 0 0 0 11.8-2"/></svg>
    <span>Обновить</span>
  </button>;
}

export function Connection({ status, children }: {
  status: 'pending' | 'online' | 'offline'; children: ReactNode;
}) {
  return <span id="connection" className={`connection ${status}`}><i/><span>{children}</span></span>;
}

export function SectionTab({ id, active, panel, children, onClick }: {
  id: string; active: boolean; panel: string; children: ReactNode; onClick: () => void;
}) {
  return <button id={id} type="button" className={active ? 'section-tab active' : 'section-tab'}
    aria-pressed={active} aria-controls={panel} onClick={onClick}>{children}</button>;
}
