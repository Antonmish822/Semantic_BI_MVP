import { useEffect, useState } from 'react';
import type { ViewStateResponse } from './api';
import { formatNumber } from './DealsDetails';

export function useRefreshRevision(title: string) {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    document.title = title;
    const refresh = () => setRevision((value) => value + 1);
    let timer = window.setInterval(refresh, 60_000);
    const stop = () => window.clearInterval(timer);
    const resume = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      refresh();
      timer = window.setInterval(refresh, 60_000);
    };
    window.addEventListener('pagehide', stop);
    window.addEventListener('pageshow', resume);
    return () => {
      stop();
      window.removeEventListener('pagehide', stop);
      window.removeEventListener('pageshow', resume);
    };
  }, [title]);
  return [revision, () => setRevision((value) => value + 1)] as const;
}

export function blockProps(view: ViewStateResponse | undefined, name: string, base: string) {
  const profile = view?.view_profile;
  const order = Array.isArray(profile?.block_order) ? profile.block_order : [];
  const highlights = Array.isArray(profile?.highlights) ? profile.highlights : [];
  const index = order.indexOf(name);
  return {
    className: base + (index >= 0 && index < 2 ? ' is-promoted' : '') +
      (highlights.includes(name) ? ' is-highlighted' : ''),
    hidden: order.length > 0 && index === -1,
    style: view ? { order: index === -1 ? Number.MAX_SAFE_INTEGER : index } : undefined,
  };
}

export function Situations({ data }: { data?: ViewStateResponse }) {
  const items = Array.isArray(data?.active_situations) ? data.active_situations : [];
  return <section id="situations-panel" className="panel situations-panel" hidden={!items.length}>
    <h3 className="situations-title">Активные ситуации</h3>
    <div id="situations-list">{items.map((item, index) => <div className="situation-item" key={item.id ?? index}>
      <div className="situation-name">{item.title || 'Ситуация'}</div>
      <div className="situation-desc">{item.description || ''}</div>
      <div className="situation-priority">Приоритет: {formatNumber(item.priority)}</div>
    </div>)}</div>
  </section>;
}
