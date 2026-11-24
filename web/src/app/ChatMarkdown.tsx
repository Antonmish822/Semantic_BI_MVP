import { createElement, Fragment, type ReactNode } from 'react';
import { TableDownloads } from './TableDownloads';

function inline(text: string): ReactNode[] {
  const pattern = /\x60([^\x60]+)\x60|\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\n]+)\*|_([^_\n]+)_|~~([^~]+)~~/g;
  const result: ReactNode[] = [];
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index;
    result.push(text.slice(offset, index));
    let node: ReactNode = match[0];
    if (match[1] !== undefined) node = <code>{match[1]}</code>;
    else if (match[2] !== undefined) {
      try {
        const url = new URL(match[3], window.location.href);
        if (['http:', 'https:', 'mailto:'].includes(url.protocol)) {
          node = <a href={url.href} rel="noopener noreferrer">{match[2]}</a>;
        }
      } catch { /* Display the source text if the link cannot be parsed. */ }
    } else if (match[4] !== undefined || match[5] !== undefined) node = <strong>{match[4] ?? match[5]}</strong>;
    else if (match[6] !== undefined || match[7] !== undefined) node = <em>{match[6] ?? match[7]}</em>;
    else node = <del>{match[8]}</del>;
    result.push(<Fragment key={index}>{node}</Fragment>);
    offset = index + match[0].length;
  }
  result.push(text.slice(offset));
  return result;
}

const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
const fence = /^\s*\x60{3}/;
const heading = /^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$/;
const rule = /^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,})$/;
const list = /^\s*(?:([-+*])\s+|(\d+)[.)]\s+)(.+)$/;
const quote = /^\s*(?:>|&gt;)\s?/;

export function ChatMarkdown({ text, onError }: { text: string; onError: (message: string) => void }) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  const tableStart = (index: number) => lines[index]?.includes('|') && index + 1 < lines.length &&
    cells(lines[index + 1]).every((cell) => /^:?-{3,}:?$/.test(cell));
  let index = 0;
  while (index < lines.length) {
    const start = index;
    const line = lines[index++];
    if (!line.trim()) continue;
    const title = line.match(heading);
    const item = line.match(list);
    if (fence.test(line)) {
      const code: string[] = [];
      while (index < lines.length && !fence.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index++;
      blocks.push(<pre key={start}><code>{code.join('\n')}</code></pre>);
    } else if (title) {
      blocks.push(createElement('h' + (title[1].length + 1), { key: start }, inline(title[2])));
    } else if (rule.test(line)) {
      blocks.push(<hr key={start}/>);
    } else if (tableStart(start)) {
      index++;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].trim() && lines[index].includes('|')) rows.push(cells(lines[index++]));
      blocks.push(<TableDownloads key={start} prefix="agent-table" onError={onError}>
        <thead><tr>{cells(line).map((cell, position) => <th key={position}>{inline(cell)}</th>)}</tr></thead>
        <tbody>{rows.map((row, position) => <tr key={position}>
          {row.map((cell, column) => <td key={column}>{inline(cell)}</td>)}
        </tr>)}</tbody>
      </TableDownloads>);
    } else if (item) {
      const Tag = item[2] === undefined ? 'ul' : 'ol';
      const items = [item[3]];
      while (index < lines.length) {
        const next = lines[index].match(list);
        if (!next || (next[2] === undefined) !== (item[2] === undefined)) break;
        items.push(next[3]);
        index++;
      }
      blocks.push(<Tag key={start}>{items.map((value, index) => <li key={index}>{inline(value)}</li>)}</Tag>);
    } else if (quote.test(line)) {
      const quoted = [line];
      while (index < lines.length && quote.test(lines[index])) quoted.push(lines[index++]);
      blocks.push(<blockquote key={start}>{quoted.map((value, index) =>
        <p key={index}>{inline(value.replace(quote, ''))}</p>)}</blockquote>);
    } else {
      const paragraph = [line.trim()];
      while (index < lines.length && lines[index].trim() && !fence.test(lines[index]) &&
        !heading.test(lines[index]) && !list.test(lines[index]) && !quote.test(lines[index]) &&
        !rule.test(lines[index]) && !tableStart(index)) paragraph.push(lines[index++].trim());
      blocks.push(<p key={start}>{inline(paragraph.join(' '))}</p>);
    }
  }
  return <>{blocks}</>;
}
