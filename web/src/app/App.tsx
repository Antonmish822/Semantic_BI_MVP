import { DealsPage, EmployeesPage, LeadsPage } from '../features/analytics/AnalyticsPages';
import { ChatPage } from '../features/chat/ChatPage';
import { OntologyPage } from '../features/ontology/OntologyPage';
import { PublishedQueriesPage, QueriesPage } from '../features/queries/QueriesPage';

export function App() {
  const path = window.location.pathname.replace(/^\/assets(?=\/|$)/, '') || '/';
  if (path === '/leads' || path === '/leads.html') return <LeadsPage />;
  if (path === '/employees' || path === '/employees.html') return <EmployeesPage />;
  if (path === '/agent' || path === '/agent.html') return <ChatPage />;
  if (path === '/queries' || path === '/published-queries') return <PublishedQueriesPage />;
  if (path === '/tools/queries') return <QueriesPage />;
  if (path.startsWith('/tools') || path.startsWith('/ontologies')) return <OntologyPage />;
  return <DealsPage />;
}
