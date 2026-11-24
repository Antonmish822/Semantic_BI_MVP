(() => {
  const $ = id => document.getElementById(id);
  const state = { slug: null, sha: null, savedText: '', dirty: false, assistantHistory: [], assistantResult: null, assistantReady: false, assistantSlug: null };
  try { localStorage.removeItem('crm-pulse-ontology-admin-token'); } catch (_) {}
  const token = () => $('admin-token').value;
  const currentSlug = () => $('new-slug').value.trim();
  const currentFileSha = () => currentSlug() === state.slug ? state.sha : null;
  const authorizationHeader = () => {
    const value=token();if(!value)return {};
    const bytes=new TextEncoder().encode(value);let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
    const encoded=btoa(binary).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
    return {Authorization:`Bearer-UTF8 ${encoded}`};
  };
  const headers = method => ({'Content-Type':'application/json', ...(method && method !== 'GET' ? authorizationHeader() : {})});
  async function api(url, options={}) {
    const response = await fetch(url, {...options, redirect: 'error', headers:{...headers(options.method), ...(options.headers||{})}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.error || `HTTP ${response.status}`); error.status=response.status;error.code=data.code;throw error; }
    return data;
  }
  function notice(message, error=false) { $('notice').textContent=message; $('notice').hidden=!message; $('notice').classList.toggle('error-banner',error); }
  function changed() { state.dirty=$('editor').value!==state.savedText; $('publish-status').textContent=state.dirty?'Статус: есть несохранённые изменения':'Статус: не опубликовано'; }
  async function sha256(text) { const data=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));return [...new Uint8Array(data)].map(b=>b.toString(16).padStart(2,'0')).join(''); }
  async function refreshList() {
    const data=await api('/api/ontologies');
    $('file-list').replaceChildren(...data.ontologies.map(item=>{const button=document.createElement('button');button.type='button';button.className='secondary-button';button.textContent=item.filename;button.onclick=()=>openFile(item.slug);return button;}));
    const quarantine=await api('/api/ontologies/quarantine');
    $('quarantine-list').replaceChildren(...(quarantine.items||[]).map(item=>{
      const card=element('div',undefined,'quarantine-item');
      card.append(element('p',`${item.slug} · ревизий: ${item.revision_count} · состояние: ${item.state} · до ${new Date(item.expires_at).toLocaleString()}`));
      const restore=element('button','Восстановить файл','secondary-button');restore.type='button';
      restore.onclick=async()=>{if(!confirm(`Восстановить ${item.slug}.ttl, архив ревизий и исходный опубликованный граф? Если в GraphDB уже находится другой граф с этим URI, восстановление остановится без перезаписи.`))return;try{const result=await api(`/api/ontologies/${encodeURIComponent(item.slug)}/restore`,{method:'POST',body:JSON.stringify({operation_id:item.operation_id})});notice(result.message);await refreshList();}catch(error){notice(error.message,true);}};
      card.append(restore);return card;
    }));
    if (!data.admin_enabled) notice('Изменение отключено: серверу не задан ONTOLOGY_ADMIN_TOKEN.',true);
  }
  async function openFile(slug) {
    if (state.dirty && !confirm('Есть несохранённые изменения. Переключиться без сохранения?')) return;
    if (state.assistantSlug && state.assistantSlug !== slug) { state.assistantHistory=[]; state.assistantSlug=null; $('assistant-history').replaceChildren(); $('assistant-state').textContent='Готов'; }
    try { const data=await api(`/api/ontologies/${encodeURIComponent(slug)}`);state.slug=data.slug;state.sha=data.sha256;state.savedText=data.text;state.dirty=false;$('editor').value=data.text;$('new-slug').value=data.slug;$('current-meta').textContent=`${data.filename} · граф ${data.uri} · SHA-256 ${data.sha256}`;const history=data.publication_history||[],latest=[...history].reverse().find(row=>row.status==='confirmed');const historyText=latest?` · последняя подтверждённая публикация ${latest.sha256.slice(0,12)} (${latest.at})`:'';$('publish-status').textContent=(({published:'Статус: опубликовано · содержимое графа совпадает',unpublished:'Статус: граф прочитан, но не совпадает с текущим файлом',diverged:'Статус: расхождение текущего файла и опубликованного графа',unsupported:'Статус: публикация доступна только в GraphDB',missing:'Статус: граф отсутствует',unavailable:'Статус: GraphDB недоступна или граф не удалось прочитать'})[data.publication_status]||'Статус: не опубликовано')+historyText;state.assistantResult=null;notice('Файл загружен.');}
    catch(error){notice(error.message,true);}
  }
  function renderClassStatistics(data) {
    const container=$('class-statistics');container.replaceChildren();
    container.append(element('p',`Классов найдено: ${data.class_count}. Пространство: ${data.namespace}`,'panel-subtitle'));
    if(!data.classes.length){container.append(element('p','В этом пространстве имён классы не найдены.','empty-state'));return;}
    renderClassRelations(container,data);
    for(const classInfo of data.classes){
      const card=element('article',undefined,'class-stat-card');
      card.append(element('h3',classInfo.label_ru||classInfo.name));
      card.append(element('code',classInfo.uri,'class-stat-uri'));
      if(classInfo.comment_ru)card.append(element('p',classInfo.comment_ru,'class-stat-comment'));
      card.append(element('p',`Экземпляров класса: ${classInfo.instance_count}`,'class-stat-count'));
      if(!classInfo.features.length){card.append(element('p','Непустые признаки не найдены.','empty-state'));container.append(card);continue;}
      const disclosure=document.createElement('details');disclosure.className='class-feature-disclosure';
      const summary=document.createElement('summary');summary.textContent=`Признаки (${classInfo.features.length})`;disclosure.append(summary);
      const table=document.createElement('table');table.className='class-feature-table';
      const head=document.createElement('thead');const heading=document.createElement('tr');
      for(const title of ['Признак','Label','Экземпляров с непустым значением','Комментарий'])heading.append(element('th',title));
      head.append(heading);table.append(head);
      const body=document.createElement('tbody');
      for(const feature of classInfo.features){
        const row=document.createElement('tr');
        const name=element('td',feature.name);name.title=feature.uri;
        row.append(name,element('td',feature.label_ru||'—'),element('td',String(feature.instances_with_value)),element('td',feature.comment_ru||'—'));
        body.append(row);
      }
      table.append(body);disclosure.append(table);card.append(disclosure);container.append(card);
    }
  }
  function renderClassRelations(container,data) {
    const section=element('section',undefined,'class-relations');
    section.append(element('h3','Связи классов по domain / range'));
    const relations=data.relations||[];
    if(!relations.length){section.append(element('p','Для классов этого пространства не найдены свойства с domain и range, ведущими к классам в этом же пространстве.','empty-state'));container.append(section);return;}
    const classByUri=new Map(data.classes.map(item=>[item.uri,item.label_ru||item.name]));
    const svgNs='http://www.w3.org/2000/svg';
    const svg=document.createElementNS(svgNs,'svg');
    svg.setAttribute('viewBox',`0 0 850 ${Math.max(100,relations.length*76+24)}`);
    svg.setAttribute('role','img');svg.setAttribute('aria-label','Диаграмма связей классов по domain и range');svg.classList.add('class-relations-svg');
    const defs=document.createElementNS(svgNs,'defs');const marker=document.createElementNS(svgNs,'marker');
    marker.setAttribute('id','class-relation-arrow');marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','8');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','7');marker.setAttribute('markerHeight','7');marker.setAttribute('orient','auto-start-reverse');
    const arrow=document.createElementNS(svgNs,'path');arrow.setAttribute('d','M 0 0 L 10 5 L 0 10 z');arrow.classList.add('class-relation-arrow');marker.append(arrow);defs.append(marker);svg.append(defs);
    const addSvgText=(x,y,value,className)=>{const text=document.createElementNS(svgNs,'text');text.setAttribute('x',String(x));text.setAttribute('y',String(y));text.setAttribute('text-anchor','middle');text.classList.add(className);text.textContent=value;svg.append(text);};
    for(const [index,relation] of relations.entries()){
      const y=14+index*76, center=y+28;
      const source=classByUri.get(relation.domain)||relation.domain.split(/[\/#]/).pop();
      const target=classByUri.get(relation.range)||relation.range.split(/[\/#]/).pop();
      const property=relation.property_label_ru||relation.property_name;
      const line=document.createElementNS(svgNs,'line');line.setAttribute('x1','240');line.setAttribute('y1',String(center));line.setAttribute('x2','610');line.setAttribute('y2',String(center));line.setAttribute('marker-end','url(#class-relation-arrow)');line.classList.add('class-relation-line');svg.append(line);
      for(const [x,label] of [[14,source],[620,target]]){
        const rect=document.createElementNS(svgNs,'rect');rect.setAttribute('x',String(x));rect.setAttribute('y',String(y));rect.setAttribute('width','216');rect.setAttribute('height','48');rect.setAttribute('rx','8');rect.classList.add('class-relation-node');svg.append(rect);
        addSvgText(x+108,center+4,label,'class-relation-node-label');
      }
      addSvgText(425,center-7,property,'class-relation-property');
      const title=document.createElementNS(svgNs,'title');title.textContent=`${source} — ${property} → ${target}`;svg.append(title);
    }
    const scrollRegion=element('div',undefined,'class-relations-scroll');scrollRegion.append(svg);
    section.append(scrollRegion);container.append(section);
  }
  $('class-statistics-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const namespace=$('class-statistics-namespace').value.trim();
    if(!namespace)return;
    const button=$('class-statistics-submit');button.disabled=true;button.textContent='Запрашиваю…';
    $('class-statistics').replaceChildren(element('p','Запрашиваю статистику из RDF-хранилища…','empty-state'));
    try{
      const data=await api('/api/ontologies/class-statistics',{method:'POST',body:JSON.stringify({namespace})});
      renderClassStatistics(data);
    }catch(error){$('class-statistics').replaceChildren(element('p',error.message,'assistant-error'));}
    finally{button.disabled=false;button.textContent='Показать статистику';}
  });
  $('editor').addEventListener('input',()=>{changed();state.assistantResult=null;});
  $('new-slug').addEventListener('input',()=>{state.assistantResult=null;const slug=currentSlug();if(slug&&slug!==state.slug)$('current-meta').textContent=`${slug}.ttl · граф urn:bitrix24:ontology:${slug} · новый файл`;});
  $('validate').onclick=async()=>{try{const slug=currentSlug();const d=await api('/api/ontologies/validate',{method:'POST',body:JSON.stringify({slug,text:$('editor').value})});notice(`Turtle корректен. URI: ${d.uri}`);}catch(e){notice(e.message,true);}};
  $('save').onclick=async()=>{try{const slug=currentSlug(), text=$('editor').value;let d;if(currentFileSha()){d=await api(`/api/ontologies/${encodeURIComponent(slug)}`,{method:'PUT',body:JSON.stringify({text,expected_sha256:currentFileSha()})});}else{d=await api('/api/ontologies',{method:'POST',body:JSON.stringify({slug,text})});}state.slug=d.slug;state.sha=d.sha256;state.savedText=text;state.dirty=false;$('new-slug').value=d.slug;$('current-meta').textContent=`${d.slug}.ttl · граф ${d.uri}`;$('publish-status').textContent='Статус: не опубликовано';state.assistantResult=null;notice('Файл сохранён. Граф не изменён.');await refreshList();}catch(e){notice(e.message,true);}};
  $('create').onclick=async()=>{const slug=currentSlug();if(currentFileSha()){notice('Файл уже существует. Используйте кнопку «Сохранить».',true);return;}try{const text=$('editor').value;const d=await api('/api/ontologies',{method:'POST',body:JSON.stringify({slug,text})});state.slug=d.slug;state.sha=d.sha256;state.savedText=text;state.dirty=false;$('new-slug').value=d.slug;$('current-meta').textContent=`${d.slug}.ttl · граф ${d.uri}`;$('publish-status').textContent='Статус: не опубликовано';state.assistantResult=null;notice('Новый файл сохранён. Граф не изменён.');await refreshList();}catch(e){notice(e.message,true);}};
  $('publish').onclick=async()=>{const slug=currentSlug();if(state.dirty){notice('Сначала сохраните текст.',true);return;}if(slug!==state.slug||!state.sha){notice('Сначала сохраните файл.',true);return;}try{const d=await api(`/api/ontologies/${encodeURIComponent(slug)}/publish`,{method:'POST',body:JSON.stringify({expected_sha256:state.sha})});$('publish-status').textContent=`Статус: опубликовано · SHA-256 ${d.sha256} · ${d.message}`;notice(d.message);}catch(e){notice(e.message,true);}};
  $('rollback').onclick=async()=>{const slug=currentSlug();if(!slug||state.dirty){notice('Сначала выберите сохранённый файл и сохраните текущие изменения.',true);return;}if(!confirm('Опубликовать предыдущую подтверждённую ревизию этого графа?'))return;try{const d=await api(`/api/ontologies/${encodeURIComponent(slug)}/rollback`,{method:'POST',body:'{}'});$('editor').value=d.text;state.assistantResult=null;changed();$('current-meta').textContent=`${slug}.ttl · открыта опубликованная ревизия ${d.sha256} · сохранённый файл ${state.sha}`;$('publish-status').textContent=`Статус: откат опубликован · в редакторе несохранённая ревизия ${d.sha256}`;notice('Предыдущая ревизия опубликована и загружена в редактор. Она отличается от текущего сохранённого файла; нажмите «Сохранить», если нужно синхронизировать файл с откатом.');}catch(e){notice(e.message,true);}};
  async function confirmOntologyDeletion(preview, slug) {
    const dialog=$('delete-confirm-dialog');
    const fingerprint=preview.graph_fingerprint||'';
    $('delete-confirm-summary').textContent=`Будет удалён ${slug}.ttl и ${preview.revision_count} ревизий. ${preview.graph_present?'Именованный граф будет удалён и сохранён в карантин.':'Опубликованный граф не найден.'} Все данные будут храниться ${preview.quarantine_days} дней.`;
    $('delete-confirm-fingerprint').textContent=fingerprint||'Опубликованный граф отсутствует';
    $('copy-fingerprint-status').textContent='Fingerprint можно выделить мышью или скопировать кнопкой.';
    $('delete-confirm-slug').value='';$('confirm-delete-ontology').disabled=true;dialog.dataset.fingerprintCopied='false';
    return new Promise(resolve=>{
      let settled=false,onInput,onCancel;
      const finish=value=>{if(settled)return;settled=true;if(onInput)$('delete-confirm-slug').removeEventListener('input',onInput);if(onCancel)dialog.removeEventListener('cancel',onCancel);dialog.close();resolve(value);};
      const input=$('delete-confirm-slug');
      const confirmButton=$('confirm-delete-ontology');
      onInput=()=>{confirmButton.disabled=input.value!==slug&&dialog.dataset.fingerprintCopied!=='true';};
      input.addEventListener('input',onInput);
      $('cancel-delete-ontology').onclick=()=>finish(false);
      confirmButton.onclick=()=>{if(input.value===slug||dialog.dataset.fingerprintCopied==='true')finish(true);};
      onCancel=event=>{event.preventDefault();finish(false);};dialog.addEventListener('cancel',onCancel);
      dialog.showModal();input.focus();
    });
  }
  $('copy-delete-fingerprint').onclick=async()=>{
    const fingerprint=$('delete-confirm-fingerprint').textContent;
    if(!fingerprint||fingerprint==='Опубликованный граф отсутствует')return;
    try{
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(fingerprint);
      else {const selection=window.getSelection();const range=document.createRange();range.selectNodeContents($('delete-confirm-fingerprint'));selection.removeAllRanges();selection.addRange(range);if(!document.execCommand('copy'))throw new Error('copy failed');selection.removeAllRanges();}
      const dialog=$('delete-confirm-dialog');dialog.dataset.fingerprintCopied='true';$('confirm-delete-ontology').disabled=false;
      $('copy-fingerprint-status').textContent='Fingerprint скопирован. Можно подтвердить удаление.';
    }catch(_){$('copy-fingerprint-status').textContent='Не удалось скопировать автоматически. Выделите fingerprint мышью и нажмите Ctrl+C.';}
  };
  $('delete-ontology').onclick=async()=>{
    const slug=currentSlug();
    if(!slug||slug!==state.slug||!state.sha){notice('Выберите сохранённую онтологию.',true);return;}
    if(state.dirty){notice('Сначала сохраните или отмените изменения редактора.',true);return;}
    const button=$('delete-ontology');button.disabled=true;
    try{
      const preview=await api(`/api/ontologies/${encodeURIComponent(slug)}/delete-preview`,{method:'POST',body:'{}'});
      if(!await confirmOntologyDeletion(preview,slug))return;
      const result=await api(`/api/ontologies/${encodeURIComponent(slug)}/delete`,{method:'POST',body:JSON.stringify(preview)});
      state.slug=null;state.sha=null;state.savedText='';state.dirty=false;state.assistantResult=null;$('editor').value='';$('current-meta').textContent='Онтология помещена в карантин.';$('publish-status').textContent=`Удалено. Файл и ревизии будут окончательно очищены ${new Date(result.expires_at).toLocaleString()}.`;$('new-slug').value='';notice(`Онтология ${slug} удалена${result.graph_deleted?' вместе с именованным графом':''} и помещена в карантин до ${new Date(result.expires_at).toLocaleString()}.`);await refreshList();
    }catch(error){notice(error.message,true);}finally{button.disabled=false;}
  };
  $('file-input').onchange=async event=>{const file=event.target.files[0];if(!file)return;const text=await file.text();$('editor').value=text;$('new-slug').value=file.name.replace(/\.ttl$/i,'');state.slug=null;state.sha=null;state.savedText='';state.dirty=true;state.assistantResult=null;$('current-meta').textContent='Локальный файл · ещё не сохранён';changed();};
  $('auth-check').onclick=async()=>{try{await api('/api/ontologies/validate',{method:'POST',body:JSON.stringify({slug:'access-check',text:'<urn:bitrix24:ontology:access-check> a <http://www.w3.org/2002/07/owl#Ontology> .'})});notice('Доступ администратора подтверждён.');}catch(e){notice(e.message,true);}};
  function addHistory(role, content) { state.assistantHistory.push({role,content}); }
  function element(tag, text, className) { const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node; }
  function renderReply(result) {
    const container=$('assistant-history');container.replaceChildren();
    for (const msg of state.assistantHistory.slice(-8)) container.append(element('p',`${msg.role==='user'?'Вы':'Помощник'}: ${msg.content.slice(0,1200)}`,'assistant-history-line'));
    const card=element('article',undefined,'assistant-result');card.append(element('h3',result.summary||'Ответ помощника'));
    if(result.question)card.append(element('p',`Уточнение: ${result.question}`));
    card.append(element('p',`Модель ${result.model} · редактор ${result.editor_sha256.slice(0,12)} · файл ${result.file_sha256?result.file_sha256.slice(0,12):'новый'}`,'assistant-provenance'));
    if(result.impact){card.append(element('h4','Предварительное влияние'));card.append(element('p',`Оценка субъектов: ${result.impact.estimated_subjects??'нет оценки'}; возможные пересечения: ${(result.impact.possible_cross_type_matches||[]).join(', ')||'не заявлены'}. ${result.impact.limits||''}`));}
    if(result.evidence?.length){card.append(element('h4','Основания'));const list=element('ul');for(const item of result.evidence)list.append(element('li',`${item.source} · ${item.reference}: ${item.finding}`));card.append(list);}
    if(result.warnings?.length){card.append(element('h4','Предупреждения'));const list=element('ul');for(const warning of result.warnings)list.append(element('li',warning));card.append(list);}
    if(result.assumptions?.length)card.append(element('p',`Предположения: ${result.assumptions.join(' · ')}`));
    if(result.rdf_diff){card.append(element('h4','Изменения RDF'));for(const [kind,label] of [['added','Добавлено'],['removed','Удалено']]){card.append(element('p',`${label}: ${result.rdf_diff[kind]?.length||0} аксиом`));if(result.rdf_diff[kind]?.length)card.append(element('pre',result.rdf_diff[kind].map(row=>row.join(' ')).join('\n'),'assistant-diff'));}}
    if(result.diff){const details=element('details');details.append(element('summary','Текстовое сравнение TTL'));details.append(element('pre',result.diff,'assistant-diff'));card.append(details);}
    if(result.proposed_ttl){const details=element('details');details.append(element('summary','Полный предлагаемый Turtle'));details.append(element('pre',result.proposed_ttl,'assistant-diff'));card.append(details);}
    if(result.proposed_ttl){const actions=element('div',undefined,'assistant-result-actions');const apply=element('button','Применить в редактор','secondary-button');apply.type='button';apply.disabled=!result.applicable;apply.onclick=()=>applySuggestion(result,apply);actions.append(apply);
      const fix=element('button','Попросить исправить','secondary-button');fix.type='button';fix.onclick=()=>{const note=$('assistant-prompt').value.trim();sendSuggestion(`Исправь предыдущее предложение${note?`: ${note}`:'. Учти обнаруженные предупреждения и сохрани исходную цель.'}`,state.assistantResult?.proposed_ttl||null);};actions.append(fix);
      const recalc=element('button','Пересчитать по текущему тексту','secondary-button');recalc.type='button';recalc.onclick=()=>sendSuggestion($('assistant-prompt').value||'Пересчитай предложение по текущему содержимому редактора.');actions.append(recalc);card.append(actions);}
    container.append(card);container.scrollTop=container.scrollHeight;
  }
  async function applySuggestion(result,button) {
    try {
      if(currentSlug()!==result.slug)throw new Error('Выбран другой slug. Пересчитайте предложение.');
      if(await sha256($('editor').value)!==result.editor_sha256)throw new Error('Текст редактора изменился. Пересчитайте предложение.');
      const version=await api(`/api/ontology-assistant/version?slug=${encodeURIComponent(result.slug)}`);
      if(version.file_sha256!==result.file_sha256)throw new Error('Сохранённый файл изменился. Пересчитайте предложение.');
      if(version.published_schema_fingerprint!==result.published_schema_fingerprint||version.published_status!==result.published_schema_status)throw new Error('Опубликованная схема изменилась. Пересчитайте предложение.');
      if(!result.applicable||!result.proposed_ttl)throw new Error('Предложение не прошло локальную проверку Turtle.');
      $('editor').value=result.proposed_ttl;changed();state.assistantResult=null;button.disabled=true;notice('Предложение перенесено только в редактор. Проверьте и сохраните его отдельно.');
    } catch(e) {button.disabled=true;notice(`${e.message} Старое предложение заблокировано.`,true);}
  }
  async function sendSuggestion(message, previousProposal=null) {
    const slug=currentSlug();if(!slug){notice('Укажите slug онтологии, прежде чем обращаться к помощнику.',true);return;}
    if(state.assistantSlug&&state.assistantSlug!==slug){state.assistantHistory=[];$('assistant-history').replaceChildren();}state.assistantSlug=slug;
    if(!message||!message.trim()){notice('Введите задачу для помощника.',true);return;}
    if(!state.assistantReady){notice('ИИ-помощник не настроен. Установите POLZA_API_KEY на сервере.',true);return;}
    const editorText=$('editor').value, editorSha=await sha256(editorText), fileSha=currentFileSha(), history=state.assistantHistory.slice(-8);
    addHistory('user',message.trim());$('assistant-send').disabled=true;$('assistant-state').textContent='Работает…';
    try {
      const result=await api('/api/ontology-assistant/suggest',{method:'POST',body:JSON.stringify({slug,editor_text:editorText,editor_sha256:editorSha,file_sha256:fileSha,message:message.trim(),history,previous_proposal:previousProposal})});
      state.assistantResult=result;$('assistant-prompt').value='';addHistory('assistant',`${result.summary}${result.question?` Уточнение: ${result.question}`:''}${result.proposed_ttl?` Предложен TTL (${result.proposed_ttl.length} символов).`:''}`);renderReply(result);$('assistant-state').textContent=result.applicable?'Предложение проверено':result.proposed_ttl?'Предложение требует проверки':result.question?'Нужны уточнения':'Анализ готов';
    } catch(e) {addHistory('assistant',`Ошибка: ${e.message}`);renderError(e);$('assistant-state').textContent='Ошибка';}
    finally {$('assistant-send').disabled=false;}
  }
  function renderError(error) {const area=$('assistant-history');area.append(element('p',`${error.code||'Ошибка'}: ${error.message}`,'assistant-error'));}
  $('assistant-send').onclick=()=>sendSuggestion($('assistant-prompt').value);
  $('assistant-prompt').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();$('assistant-send').click();}});
  document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{$('assistant-prompt').value=button.dataset.prompt;sendSuggestion(button.dataset.prompt);});
  window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  refreshList().catch(e=>notice(e.message,true));
  api('/api/ontology-assistant/config').then(config=>{state.assistantReady=config.enabled;$('assistant-model').textContent=`Модель: ${config.model} · смена через ONTOLOGY_AI_MODEL на сервере`;$('assistant-state').textContent=config.enabled?'Готов':'Не настроен';if(!config.enabled)renderError({message:'Задайте POLZA_API_KEY в окружении сервера; редактор останется доступен.'});}).catch(()=>{$('assistant-state').textContent='Конфигурация недоступна';});
})();
