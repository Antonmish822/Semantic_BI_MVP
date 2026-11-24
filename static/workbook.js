// Write the single worksheet shown in chat. Cell contents never become XML markup.
function cellTextXml(value) {
  return value.replace(/_x[0-9a-f]{4}_/gi, match => '_x005F_' + match.slice(1))
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g,
      ch => '_x' + ch.charCodeAt(0).toString(16).padStart(4, '0') + '_')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function columnName(index) {
  let name = '';
  for (let number = index + 1; number > 0; number = Math.floor((number - 1) / 26)) {
    name = String.fromCharCode(65 + (number - 1) % 26) + name;
  }
  return name;
}

async function createWorkbook(rows) {
  const main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
  const rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
  const pkg = 'http://schemas.openxmlformats.org/package/2006/relationships';
  const mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml';
  const zip = new JSZip();
  zip.file('[Content_Types].xml', `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="${mime}.sheet.main+xml"/>
    <Override PartName="/xl/worksheets/sheet1.xml" ContentType="${mime}.worksheet+xml"/></Types>`);
  zip.file('_rels/.rels', `<Relationships xmlns="${pkg}"><Relationship Id="rId1" Type="${rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>`);
  zip.file('xl/workbook.xml', `<workbook xmlns="${main}" xmlns:r="${rel}"><sheets><sheet name="Данные" sheetId="1" r:id="rId1"/></sheets></workbook>`);
  zip.file('xl/_rels/workbook.xml.rels', `<Relationships xmlns="${pkg}"><Relationship Id="rId1" Type="${rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`);
  const sheet = rows.map((row, i) => {
    if (i >= 1048576 || row.length > 16384) throw new Error('Превышен размер листа Excel');
    const cells = row.map((text, j) => {
      const ref = columnName(j) + (i + 1);
      if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(text) && text.replace(/[-.]/g, '').length <= 15) {
        return `<c r="${ref}"><v>${text}</v></c>`;
      }
      if (text.length > 32767) throw new Error('Слишком длинный текст в ячейке Excel');
      return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${cellTextXml(text)}</t></is></c>`;
    });
    return `<row r="${i + 1}">${cells.join('')}</row>`;
  });
  zip.file('xl/worksheets/sheet1.xml', `<worksheet xmlns="${main}"><sheetData>${sheet.join('')}</sheetData></worksheet>`);
  return zip.generateAsync({type:'uint8array', compression:'DEFLATE'});
}
