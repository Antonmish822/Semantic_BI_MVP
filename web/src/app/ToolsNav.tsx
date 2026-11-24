export function ToolsNav({ active }: { active: 'ontology' | 'queries' }) {
  return <nav className="section-tabs" aria-label="Инструменты">
    {([['ontology', 'Онтология'], ['queries', 'Запросы']] as const).map(([route, title]) =>
      <a key={route} href={`/tools/${route}`} className={active === route ? 'section-tab active' : 'section-tab'}
        aria-current={active === route ? 'page' : undefined}>{title}</a>)}
  </nav>;
}
