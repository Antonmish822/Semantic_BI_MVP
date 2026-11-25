(() => {
  const placeholder = /\{\{([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\}\}/g;
  const datatypeAfter = /^\s*\^\^\s*(?:([A-Za-z][\w-]*):([A-Za-z][\w.-]*)|<([^<>]+)>)/;
  const xsd = 'http://www.w3.org/2001/XMLSchema#';
  const prefixes = (query) => Object.fromEntries(
    [...query.matchAll(/\bPREFIX\s+([A-Za-z][\w-]*):\s*<([^<>\s]+)>/gi)].map((m) => [m[1], m[2]]),
  );
  function typeOfDatatype(uri) {
    if (!uri?.startsWith(xsd)) return 'text';
    const name = uri.slice(xsd.length);
    if (name === 'dateTime' || name === 'dateTimeStamp') return 'dateTime';
    if (name === 'date') return 'date';
    if (name === 'time') return 'time';
    if (name === 'boolean') return 'boolean';
    if (name === 'decimal') return 'decimal';
    if (['float', 'double'].includes(name)) return 'number';
    if (/^(?:integer|nonPositiveInteger|negativeInteger|long|int|short|byte|nonNegativeInteger|unsignedLong|unsignedInt|unsignedShort|unsignedByte|positiveInteger)$/.test(name)) return 'integer';
    return 'text';
  }
  function describe(query) {
    const ns = prefixes(query);
    const details = new Map();
    for (const match of query.matchAll(placeholder)) {
      const name = match[1];
      const suffix = query.slice(match.index + match[0].length).match(datatypeAfter)?.[0];
      let type = 'text';
      if (suffix) {
        const datatype = suffix.match(datatypeAfter);
        const uri = datatype[3] || (ns[datatype[1]] ? `${ns[datatype[1]]}${datatype[2]}` : '');
        type = typeOfDatatype(uri);
      }
      const known = details.get(name);
      if (known && known !== type) throw new Error(`Параметр «${name}» используется с разными типами данных.`);
      details.set(name, type);
    }
    return [...details].map(([name, type]) => ({ name, type }));
  }
  function inputType(type) {
    return ({ dateTime: 'datetime-local', date: 'date', time: 'time', integer: 'number', decimal: 'number', number: 'number' })[type] || (type === 'boolean' ? 'select' : 'text');
  }
  function step(type) {
    return type === 'integer' ? '1' : type === 'number' || type === 'decimal' ? 'any' : undefined;
  }
  function valueForApi(input) {
    if (input.dataset.parameterType === 'dateTime') {
      const date = new Date(input.value);
      if (Number.isNaN(date.getTime())) throw new Error(`Проверьте дату и время параметра «${input.dataset.queryParameter}».`);
      return date.toISOString();
    }
    if (input.dataset.parameterType === 'time' && /^\d{2}:\d{2}$/.test(input.value)) return `${input.value}:00`;
    return input.value;
  }
  window.QueryParameters = { describe, inputType, step, valueForApi };
})();
