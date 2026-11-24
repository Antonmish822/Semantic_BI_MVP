const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const JSZip = require('../static/vendor/jszip.min.js');

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.lastElementChild = {};
    this.textContent = '';
  }
  append(...items) { this.children.push(...items); }
  addEventListener() {}
}
const ids = new Map();
const document = {
  getElementById(id) {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  },
  createElement: tag => new Element(tag),
  createTextNode: text => text
};
const ctx = vm.createContext({ document, window:{}, JSZip, parent: new Element('div') });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/workbook.js'), 'utf8'), ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/agent.js'), 'utf8'), ctx);
vm.runInContext('renderMarkdown(parent, "&gt; Строка цитаты\\n# \\n- ")', ctx, { timeout: 500 });
assert.equal(ctx.parent.children[0].tag, 'blockquote');
assert.equal(ctx.parent.children[0].children[0].children[0], 'Строка цитаты');
assert.equal(ctx.sanitizeCsvValue('=1+2'), "'=1+2");
assert.equal(ctx.sanitizeCsvValue('-17'), '-17');
assert.equal(ctx.sanitizeCsvValue('A,B'), '"A,B"');
(async () => {
  const bytes = await ctx.createWorkbook([['=1+2', '-17', 'Демо <A> & Б', '0001']]);
  const zip = await JSZip.loadAsync(bytes);
  const xml = await zip.file('xl/worksheets/sheet1.xml').async('text');
  assert.ok(xml.includes('t="inlineStr"><is><t xml:space="preserve">=1+2</t>'));
  assert.ok(xml.includes('<c r="B1"><v>-17</v></c>'));
  assert.ok(!xml.includes('<f>'));
  if (process.argv[2]) fs.writeFileSync(process.argv[2], bytes);
  console.log('Markdown, CSV and XLSX checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
