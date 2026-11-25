import { createRoot } from 'react-dom/client';
import { DealsPage } from './app/DealsPage';
import { LeadsPage } from './app/LeadsPage';
import { EmployeesPage } from './app/EmployeesPage';
import { ChatPage } from './app/ChatPage';
import { QueryCatalogPage } from './app/QueryCatalogPage';
import { QueryDebuggerPage } from './app/QueryDebuggerPage';
import { OntologyPage } from './app/OntologyPage';
import '../static/styles.css';

const routes = new Map([
  ['/', DealsPage], ['/index.html', DealsPage],
  ['/leads', LeadsPage], ['/leads.html', LeadsPage],
  ['/employees', EmployeesPage], ['/employees.html', EmployeesPage],
  ['/agent', ChatPage], ['/agent.html', ChatPage],
  ['/queries', QueryCatalogPage], ['/published-queries', QueryCatalogPage],
  ['/tools/queries', QueryDebuggerPage],
  ['/ontologies', OntologyPage], ['/ontologies.html', OntologyPage],
  ['/tools', OntologyPage], ['/tools/ontology', OntologyPage],
]);

const Page = routes.get(window.location.pathname) ?? DealsPage;
createRoot(document.getElementById('root')!).render(<Page />);
