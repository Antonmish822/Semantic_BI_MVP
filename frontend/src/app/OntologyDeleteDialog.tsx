import { useEffect, useRef, useState } from 'react';
import { copyText } from './clipboard';
import type { DeletionPreview } from './ontologyApi';

export function OntologyDeleteDialog({ slug, preview, cancel, confirm }: {
  slug: string; preview: DeletionPreview; cancel: () => void; confirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState('');
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState('Fingerprint можно выделить мышью или скопировать кнопкой.');
  const fingerprint = preview.graph_fingerprint;
  useEffect(() => {
    const trigger = document.getElementById('delete-ontology');
    const target = dialog.current;
    target?.showModal();
    field.current?.focus();
    return () => { target?.close(); trigger?.focus(); };
  }, []);
  return <dialog ref={dialog} id="delete-confirm-dialog" className="ontology-confirm-dialog"
    aria-labelledby="delete-confirm-title" data-fingerprint-copied={copied}
    onCancel={(event) => { event.preventDefault(); cancel(); }}>
    <div className="ontology-confirm-content">
      <h2 id="delete-confirm-title">Подтвердить удаление</h2>
      <p id="delete-confirm-summary" className="panel-subtitle">{`Будет удалён ${slug}.ttl и ${preview.revision_count} ревизий. ${preview.graph_present
        ? 'Именованный граф будет удалён и сохранён в карантин.' : 'Опубликованный граф не найден.'} Все данные будут храниться ${preview.quarantine_days} дней.`}</p>
      <label className="fingerprint-label" htmlFor="delete-confirm-fingerprint">Fingerprint графа</label>
      <div className="fingerprint-row">
        <pre id="delete-confirm-fingerprint" tabIndex={0}>{fingerprint || 'Опубликованный граф отсутствует'}</pre>
        <button id="copy-delete-fingerprint" className="secondary-button" type="button" onClick={() => {
          if (!fingerprint) return;
          void copyText(fingerprint).then(() => {
            setCopied(true);
            setStatus('Fingerprint скопирован. Можно подтвердить удаление.');
          }, () => setStatus('Не удалось скопировать автоматически. Выделите fingerprint мышью и нажмите Ctrl+C.'));
        }}>Копировать</button>
      </div>
      <p id="copy-fingerprint-status" className="panel-subtitle" aria-live="polite">{status}</p>
      <label htmlFor="delete-confirm-slug">Введите slug для подтверждения
        <input ref={field} id="delete-confirm-slug" autoComplete="off" spellCheck={false}
          value={value} onChange={(event) => setValue(event.target.value)}/>
      </label>
      <p id="delete-confirm-hint" className="panel-subtitle">Кнопка станет активной после точного ввода slug или успешного копирования fingerprint.</p>
      <div className="header-actions ontology-actions">
        <button id="cancel-delete-ontology" className="secondary-button" type="button" onClick={cancel}>Отмена</button>
        <button id="confirm-delete-ontology" className="secondary-button" type="button" disabled={value !== slug && !copied}
          onClick={confirm}>Удалить в карантин</button>
      </div>
    </div>
  </dialog>;
}
