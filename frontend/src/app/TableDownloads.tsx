import type { ReactNode } from 'react';
import { useRef } from 'react';
import xlsxUrl from '../../static/vendor/xlsx.full.min.js?url';

type SheetJs = {
  utils: { table_to_book: (table: HTMLTableElement, options: { sheet: string }) => unknown };
  writeFile: (workbook: unknown, filename: string) => void;
};
const sheetJs = () => (window as Window & { XLSX?: SheetJs }).XLSX;
let pending: Promise<SheetJs> | undefined;

function loadSheetJs(): Promise<SheetJs> {
  const existing = sheetJs();
  if (existing) return Promise.resolve(existing);
  pending ??= new Promise<SheetJs>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = xlsxUrl;
    script.onload = () => {
      const library = sheetJs();
      if (library) resolve(library);
      else reject(new Error('Не удалось загрузить SheetJS'));
    };
    script.onerror = () => reject(new Error('Не удалось загрузить SheetJS'));
    document.head.append(script);
  }).finally(() => { pending = undefined; });
  return pending;
}

function toCsv(table: HTMLTableElement) {
  return Array.from(table.rows, (row) => Array.from(row.cells, (cell) => {
    const value = (cell.dataset.exportValue ?? cell.textContent ?? '').trim().replace(/\r\n?/g, '\n');
    return /["\n,;]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
  }).join(',')).join('\n');
}

export function TableDownloads({ children, prefix, onError, queryResult = false }: {
  children: ReactNode; prefix: string; onError: (error: string) => void;
  queryResult?: boolean;
}) {
  const table = useRef<HTMLTableElement>(null);
  const safePrefix = prefix.normalize('NFKD').replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, 50) || 'table';
  const filename = () => `${safePrefix}-${new Date().toISOString().replace(/[:.]/g, '-')}`;
  function csv() {
    if (!table.current) return;
    const url = URL.createObjectURL(new Blob(['\ufeff', toCsv(table.current)], { type: 'text/csv;charset=utf-8;' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${filename()}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  async function excel() {
    const node = table.current;
    if (!node) return;
    const name = filename();
    try {
      const xlsx = await loadSheetJs();
      xlsx.writeFile(xlsx.utils.table_to_book(node, { sheet: 'Данные' }), `${name}.xlsx`);
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : 'Не удалось скачать Excel');
    }
  }
  const actions = <div className="chat-table-actions">
      <button type="button" className="chat-table-download" onClick={csv}>Скачать CSV</button>
      <button type="button" className="chat-table-download chat-table-download--excel" onClick={excel}>Скачать Excel</button>
    </div>;
  if (queryResult) return <>{actions}<div className="query-result-table-scroll">
    <table ref={table} className="query-result-table">{children}</table>
  </div></>;
  return <div className="chat-table-wrap">{actions}<table ref={table}>{children}</table></div>;
}
