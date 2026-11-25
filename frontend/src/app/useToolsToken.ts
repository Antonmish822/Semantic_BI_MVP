import { useState } from 'react';
import { toolsTokenKey } from './browserStorage';

export function useToolsToken(migrateOntologyToken = false) {
  const [token, setToken] = useState(() => {
    try {
      const shared = localStorage.getItem(toolsTokenKey) || '';
      if (!migrateOntologyToken) return shared;
      const previousKey = 'crm-pulse-ontology-admin-token';
      const value = shared || localStorage.getItem(previousKey) || '';
      if (value) localStorage.setItem(toolsTokenKey, value);
      localStorage.removeItem(previousKey);
      return value;
    } catch { return ''; }
  });
  return [token, (next: string) => {
    setToken(next);
    try {
      if (next) localStorage.setItem(toolsTokenKey, next);
      else localStorage.removeItem(toolsTokenKey);
    } catch { /* The value can still be used in the current page. */ }
  }] as const;
}
