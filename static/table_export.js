(() => {
  function safeFilenamePart(value) {
    return String(value || 'table').normalize('NFKD').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'table';
  }

  function timestamp() {
    return new Date().toISOString().replace(/[:.]/g, '-');
  }

  function csvCell(cell) {
    const text = String(cell.dataset.exportValue ?? cell.textContent ?? '').trim().replace(/\r\n?/g, '\n');
    return /["\n,;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCsv(table) {
    return Array.from(table.querySelectorAll('tr')).map((row) => (
      Array.from(row.querySelectorAll('th, td')).map(csvCell).join(',')
    )).join('\n');
  }

  function downloadCsv(table, filename) {
    const blob = new Blob(["\ufeff", toCsv(table)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function ensureSheetJs() {
    if (window.XLSX) return Promise.resolve();
    if (window.sheetJsLoading) return window.sheetJsLoading;
    window.sheetJsLoading = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/vendor/xlsx.full.min.js';
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Не удалось загрузить SheetJS'));
      document.head.append(script);
    }).finally(() => { window.sheetJsLoading = null; });
    return window.sheetJsLoading;
  }

  async function downloadXlsx(table, filename) {
    await ensureSheetJs();
    const workbook = window.XLSX.utils.table_to_book(table, { sheet: 'Данные' });
    window.XLSX.writeFile(workbook, filename);
  }

  function createButtons(table, filenamePrefix, onError) {
    const actions = document.createElement('div');
    actions.className = 'chat-table-actions';
    const csv = document.createElement('button');
    csv.type = 'button';
    csv.className = 'chat-table-download';
    csv.textContent = 'Скачать CSV';
    csv.addEventListener('click', () => {
      downloadCsv(table, `${safeFilenamePart(filenamePrefix)}-${timestamp()}.csv`);
    });
    const excel = document.createElement('button');
    excel.type = 'button';
    excel.className = 'chat-table-download chat-table-download--excel';
    excel.textContent = 'Скачать Excel';
    excel.addEventListener('click', async () => {
      try {
        await downloadXlsx(table, `${safeFilenamePart(filenamePrefix)}-${timestamp()}.xlsx`);
      } catch (error) {
        if (typeof onError === 'function') onError(error);
      }
    });
    actions.append(csv, excel);
    return actions;
  }

  window.TableExport = { toCsv, downloadCsv, downloadXlsx, createButtons };
})();
