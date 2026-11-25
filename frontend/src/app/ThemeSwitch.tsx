import { useEffect, useState } from 'react';

const storageKey = 'crm-pulse-theme';

export function ThemeSwitch() {
  const root = document.documentElement;
  const [light, setLight] = useState(() => root.dataset.theme === 'light');
  const themeLabel = light ? 'Светлая тема' : 'Тёмная тема';

  useEffect(() => {
    function syncTheme(event: StorageEvent) {
      if (event.key !== storageKey && event.key !== null) return;
      const next = event.newValue === 'light';
      root.dataset.theme = next ? 'light' : 'dark';
      setLight(next);
    }

    window.addEventListener('storage', syncTheme);
    return () => window.removeEventListener('storage', syncTheme);
  }, [root]);

  function toggle() {
    const next = !light;
    const theme = next ? 'light' : 'dark';
    root.dataset.theme = theme;
    setLight(next);
    try {
      localStorage.setItem(storageKey, theme);
    } catch {
      // The switch still works when the browser blocks persistent storage.
    }
  }

  return <button type="button" className="theme-switch" role="switch"
    aria-label="Светлая тема" aria-checked={light} title={themeLabel} onClick={toggle}>
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {light ? <>
        <circle cx="12" cy="12" r="4"/>
        <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19m0-14-1.4 1.4M6.4 17.6 5 19"/>
      </> : <path d="M20.5 13a8.5 8.5 0 0 1-9.5-9.5A8.5 8.5 0 1 0 20.5 13Z"/>}
    </svg>
    <span className="theme-switch-label">{themeLabel}</span>
    <span className="theme-switch-track" aria-hidden="true"/>
  </button>;
}
