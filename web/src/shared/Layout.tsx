import type { ReactNode } from 'react';

const links = [
  ['/', 'Пульс сделок'], ['/leads', 'Пульс лидов'], ['/employees', 'Сотрудники'],
  ['/agent', 'AI Agent'], ['/queries', 'Запросы'], ['/tools/ontology', 'Онтология'],
];

export function Layout({ title, children }: { title: string; children: ReactNode }) {
  const path = window.location.pathname;
  return <main className="shell react-shell">
    <header className="topbar">
      <div className="brand"><div className="brand-mark" aria-hidden="true">P</div><div><p className="eyebrow">SEMANTIC BI · BITRIX24</p><h1>{title}</h1></div></div>
      <div className="header-actions"><span className="connection online"><i /><span>Сервисы подключены</span></span><a className="auth-link" href="/auth/logout">Выйти</a></div>
    </header>
    <nav className="page-nav" aria-label="Страницы дашборда">{links.map(([href, label]) => <a key={href} href={href} className={path === href || (href === '/tools/ontology' && path.startsWith('/tools')) ? 'active' : ''}>{label}</a>)}</nav>
    {children}
    <footer><span>CRM Pulse · Semantic BI</span><span>Данные поступают через защищённые сервисные API.</span></footer>
  </main>;
}

export function Panel({ title, eyebrow, children }: { title: string; eyebrow?: string; children: ReactNode }) {
  return <section className="panel react-panel"><div className="panel-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2>{title}</h2></div></div>{children}</section>;
}

export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`react-notice${error ? ' is-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}
