import { createRoot } from 'react-dom/client';
import { DealsPage } from './app/DealsPage';
import { LeadsPage } from './app/LeadsPage';
import { EmployeesPage } from './app/EmployeesPage';
import { ChatPage } from './app/ChatPage';
import { QueryCatalogPage } from './app/QueryCatalogPage';
import { QueryDebuggerPage } from './app/QueryDebuggerPage';
import { OntologyPage } from './app/OntologyPage';
import { PageNav } from './app/DashboardChrome';
import '../static/styles.css';
import './dashboard.css';

const pages = [
  { paths: ['/', '/index.html'], section: '/', Page: DealsPage },
  { paths: ['/leads', '/leads.html'], section: '/leads', Page: LeadsPage },
  { paths: ['/employees', '/employees.html'], section: '/employees', Page: EmployeesPage },
  { paths: ['/agent', '/agent.html'], section: '/agent', Page: ChatPage },
  { paths: ['/queries', '/published-queries'], section: '/queries', Page: QueryCatalogPage },
  { paths: ['/tools/ontology', '/tools', '/ontologies', '/ontologies.html'], section: '/tools/ontology', tool: 'ontology', Page: OntologyPage },
  { paths: ['/tools/queries'], section: '/tools/ontology', tool: 'queries', Page: QueryDebuggerPage },
] as const;
const routes = new Map<string, (typeof pages)[number]>(
  pages.flatMap(page => page.paths.map(path => [path, page] as const)),
);
const route = routes.get(window.location.pathname) ?? pages[0];
const { Page, section } = route;

createRoot(document.getElementById('root')!).render(
  <div className="app-layout">
    <PageNav active={section} tool={'tool' in route ? route.tool : undefined}/>
    <Page/>
  </div>,
);
