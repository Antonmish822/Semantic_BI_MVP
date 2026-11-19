(() => {
  const $ = id => document.getElementById(id);
  const state = { slug: null, sha: null, savedText: '', dirty: false, assistantHistory: [], assistantResult: null, assistantReady: false, assistantSlug: null };
  const token = () => $('admin-token').value;
  const currentSlug = () => $('new-slug').value.trim();
  const currentFileSha = () => currentSlug() === state.slug ? state.sha : null;
  const authorizationHeader = () => {
    const value=token();if(!value)return {};
    const bytes=new TextEncoder().encode(value);let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
    const encoded=btoa(binary).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
    return {Authorization:`Bearer-UTF8 ${encoded}`};
  };
  const headers = () => ({'Content-Type':'application/json', ...authorizationHeader()});
  async function api(url, options={}) {
    const response = await fetch(url, {...options, headers:{...headers(), ...(options.headers||{})}});
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
    if (!data.admin_enabled) notice('Изменение отключено: серверу не задан ONTOLOGY_ADMIN_TOKEN.',true);
  }
  async function openFile(slug) {
    if (state.dirty && !confirm('Есть несохранённые изменения. Переключиться без сохранения?')) return;
    if (state.assistantSlug && state.assistantSlug !== slug) { state.assistantHistory=[]; state.assistantSlug=null; $('assistant-history').replaceChildren(); $('assistant-state').textContent='Готов'; }
    try { const data=await api(`/api/ontologies/${encodeURIComponent(slug)}`);state.slug=data.slug;state.sha=data.sha256;state.savedText=data.text;state.dirty=false;$('editor').value=data.text;$('new-slug').value=data.slug;$('current-meta').textContent=`${data.filename} · граф ${data.uri}`;$('publish-status').textContent=({published:'Статус: опубликовано · содержимое графа совпадает',unpublished:'Статус: не опубликовано',unsupported:'Статус: публикация доступна только в GraphDB',missing:'Статус: граф отсутствует',unavailable:'Статус: GraphDB недоступна или граф не удалось прочитать'})[data.publication_status]||'Статус: не опубликовано';state.assistantResult=null;notice('Файл загружен.');}
    catch(error){notice(error.message,true);}
  }
  $('editor').addEventListener('input',()=>{changed();state.assistantResult=null;});
  $('new-slug').addEventListener('input',()=>{state.assistantResult=null;const slug=currentSlug();if(slug&&slug!==state.slug)$('current-meta').textContent=`${slug}.ttl · граф urn:bitrix24:ontology:${slug} · новый файл`;});
  $('validate').onclick=async()=>{try{const slug=currentSlug();const d=await api('/api/ontologies/validate',{method:'POST',body:JSON.stringify({slug,text:$('editor').value})});notice(`Turtle корректен. URI: ${d.uri}`);}catch(e){notice(e.message,true);}};
  $('save').onclick=async()=>{try{const slug=currentSlug(), text=$('editor').value;let d;if(currentFileSha()){d=await api(`/api/ontologies/${encodeURIComponent(slug)}`,{method:'PUT',body:JSON.stringify({text,expected_sha256:currentFileSha()})});}else{d=await api('/api/ontologies',{method:'POST',body:JSON.stringify({slug,text})});}state.slug=d.slug;state.sha=d.sha256;state.savedText=text;state.dirty=false;$('new-slug').value=d.slug;$('current-meta').textContent=`${d.slug}.ttl · граф ${d.uri}`;$('publish-status').textContent='Статус: не опубликовано';state.assistantResult=null;notice('Файл сохранён. Граф не изменён.');await refreshList();}catch(e){notice(e.message,true);}};
  $('create').onclick=async()=>{const slug=currentSlug();if(currentFileSha()){notice('Файл уже существует. Используйте кнопку «Сохранить».',true);return;}try{const text=$('editor').value;const d=await api('/api/ontologies',{method:'POST',body:JSON.stringify({slug,text})});state.slug=d.slug;state.sha=d.sha256;state.savedText=text;state.dirty=false;$('new-slug').value=d.slug;$('current-meta').textContent=`${d.slug}.ttl · граф ${d.uri}`;$('publish-status').textContent='Статус: не опубликовано';state.assistantResult=null;notice('Новый файл сохранён. Граф не изменён.');await refreshList();}catch(e){notice(e.message,true);}};
  $('publish').onclick=async()=>{const slug=currentSlug();if(state.dirty){notice('Сначала сохраните текст.',true);return;}if(slug!==state.slug||!state.sha){notice('Сначала сохраните файл.',true);return;}try{const d=await api(`/api/ontologies/${encodeURIComponent(slug)}/publish`,{method:'POST',body:'{}'});$('publish-status').textContent=`Статус: опубликовано · ${d.message}`;notice(d.message);}catch(e){notice(e.message,true);}};
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
    if(result.diff){card.append(element('h4','Diff'));card.append(element('pre',result.diff,'assistant-diff'));}
    if(result.proposed_ttl){const details=element('details');details.append(element('summary','Полный предлагаемый Turtle'));details.append(element('pre',result.proposed_ttl,'assistant-diff'));card.append(details);}
    const actions=element('div',undefined,'assistant-result-actions');const apply=element('button','Применить в редактор','secondary-button');apply.type='button';apply.disabled=!result.applicable;apply.onclick=()=>applySuggestion(result,apply);actions.append(apply);
    const fix=element('button','Попросить исправить','secondary-button');fix.type='button';fix.onclick=()=>{const note=$('assistant-prompt').value.trim();sendSuggestion(`Исправь предыдущее предложение${note?`: ${note}`:'. Учти обнаруженные предупреждения и сохрани исходную цель.'}`,state.assistantResult?.proposed_ttl||null);};actions.append(fix);
    const recalc=element('button','Пересчитать по текущему тексту','secondary-button');recalc.type='button';recalc.onclick=()=>sendSuggestion($('assistant-prompt').value||'Пересчитай предложение по текущему содержимому редактора.');actions.append(recalc);
    card.append(actions);container.append(card);container.scrollTop=container.scrollHeight;
  }
  async function applySuggestion(result,button) {
    try {
      if(currentSlug()!==result.slug)throw new Error('Выбран другой slug. Пересчитайте предложение.');
      if(await sha256($('editor').value)!==result.editor_sha256)throw new Error('Текст редактора изменился. Пересчитайте предложение.');
      const version=await api(`/api/ontology-assistant/version?slug=${encodeURIComponent(result.slug)}`);
      if(version.file_sha256!==result.file_sha256)throw new Error('Сохранённый файл изменился. Пересчитайте предложение.');
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
      state.assistantResult=result;$('assistant-prompt').value='';addHistory('assistant',`${result.summary}${result.question?` Уточнение: ${result.question}`:''}${result.proposed_ttl?` Предложен TTL (${result.proposed_ttl.length} символов).`:''}`);renderReply(result);$('assistant-state').textContent=result.applicable?'Предложение проверено':'Нужны уточнения';
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
