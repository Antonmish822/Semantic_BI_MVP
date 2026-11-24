import { useEffect, useState } from 'react';
import parse from 'html-react-parser';
import { readPage, startPage } from './pages';

export function App({ page }: { page: ReturnType<typeof readPage> }) {
  const [content] = useState(() => parse(page.markup));
  const [error, setError] = useState('');

  useEffect(() => {
    document.title = page.title;
    // The page scripts attach their handlers to the DOM committed by React.
    startPage(page.scripts).catch((cause: Error) => setError(cause.message));
  }, [page]);

  return <>
    {content}
    {error && <p className="error-banner" role="alert">{error}</p>}
  </>;
}
