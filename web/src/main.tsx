import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { readPage } from './app/pages';
import '../../static/styles.css';

const page = readPage(window.location.pathname);
createRoot(document.getElementById('root')!).render(<App page={page} />);
