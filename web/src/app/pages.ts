const templates = import.meta.glob<string>('../../../static/*.html', {
  eager: true,
  import: 'default',
  query: '?raw',
});

const scripts = import.meta.glob<string>([
  '../../../static/*.js',
  '../../../static/vendor/xlsx.full.min.js',
], {
  eager: true,
  import: 'default',
  query: '?url&no-inline',
});

const routes: Record<string, string> = {
  '/': 'index.html',
  '/index.html': 'index.html',
  '/leads': 'leads.html',
  '/leads.html': 'leads.html',
  '/employees': 'employees.html',
  '/employees.html': 'employees.html',
  '/agent': 'agent.html',
  '/agent.html': 'agent.html',
  '/queries': 'published_queries.html',
  '/published-queries': 'published_queries.html',
  '/ontologies': 'ontologies.html',
  '/ontologies.html': 'ontologies.html',
  '/tools': 'ontologies.html',
  '/tools/ontology': 'ontologies.html',
  '/tools/queries': 'queries.html',
};

export function readPage(path: string) {
  const filename = Object.hasOwn(routes, path) ? routes[path] : routes['/'];
  const document = new DOMParser().parseFromString(templates[`../../../static/${filename}`], 'text/html');
  return {
    title: document.title,
    markup: document.querySelector('main')!.outerHTML,
    scripts: Array.from(document.querySelectorAll<HTMLScriptElement>('body script[src]'),
      (script) => script.getAttribute('src')!),
  };
}

export async function startPage(sources: string[]) {
  // Publish the classic scripts in template order so their shared globals
  // are available before the page controller runs.
  for (const source of sources) {
    const url = scripts[`../../../static${source}`];
    if (!url) throw new Error(`Не найден скрипт страницы: ${source}`);
    await new Promise<void>((resolve, reject) => {
      const script = document.createElement('script');
      script.src = url;
      if (source === '/table_export.js') {
        script.dataset.xlsxSrc = scripts['../../../static/vendor/xlsx.full.min.js'];
      }
      script.onload = () => resolve();
      script.onerror = () => {
        script.remove();
        reject(new Error(`Не удалось загрузить скрипт страницы: ${source}`));
      };
      document.body.append(script);
    });
  }
}
