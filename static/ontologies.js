(() => {
  const $ = id => document.getElementById(id);
  const token = () => $('admin-token').value;
  const state = { slug: null, sha: null, savedText: '', dirty: false };
  const headers = () => ({'Content-Type':'application/json', ...(token() ? {Authorization:`Bearer ${token()}`} : {})});
  async function api(url, options={}) {
    const response = await fetch(url, {...options, headers:{...headers(), ...(options.headers||{})}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function notice(message, error=false) { $('notice').textContent=message; $('notice').classList.toggle('error-banner',error); }
  function changed() { state.dirty=$('editor').value!==state.savedText; $('publish-status').textContent=state.dirty?'Статус: есть несохранённые изменения':'Статус: не опубликовано'; }
  async function refreshList() {
    const data=await api('/api/ontologies');
    $('file-list').replaceChildren(...data.ontologies.map(item=>{const button=document.createElement('button');button.type='button';button.className='secondary-button';button.textContent=item.filename;button.onclick=()=>openFile(item.slug);return button;}));
    if (!data.admin_enabled) notice('Изменение отключено: серверу не задан ONTOLOGY_ADMIN_TOKEN.',true);
  }
  async function openFile(slug) {
    if (state.dirty && !confirm('Есть несохранённые изменения. Переключиться без сохранения?')) return;
    try { const data=await api(`/api/ontologies/${encodeURIComponent(slug)}`); state.slug=data.slug;state.sha=data.sha256;state.savedText=data.text;state.dirty=false;$('editor').value=data.text;$('new-slug').value=data.slug;$('current-meta').textContent=`${data.filename} · граф ${data.uri}`;$('publish-status').textContent=({published:'Статус: опубликовано · содержимое графа совпадает',unpublished:'Статус: не опубликовано',unsupported:'Статус: публикация поддерживается только в GraphDB',missing:'Статус: граф отсутствует',unavailable:'Статус: GraphDB недоступна или граф не удалось прочитать'})[data.publication_status]||'Статус: не опубликовано';notice('Файл загружен.'); }
    catch(error){notice(error.message,true);}
  }
  $('editor').addEventListener('input',changed);
  $('validate').onclick=async()=>{try{const d=await api('/api/ontologies/validate',{method:'POST',body:JSON.stringify({slug:$('new-slug').value||state.slug,text:$('editor').value})});notice(`Turtle корректен. URI: ${d.uri}`);}catch(e){notice(e.message,true);}};
  $('save').onclick=async()=>{try{const d=await api(`/api/ontologies/${encodeURIComponent(state.slug)}`,{method:'PUT',body:JSON.stringify({text:$('editor').value,expected_sha256:state.sha})});state.sha=d.sha256;state.savedText=$('editor').value;state.dirty=false;$('publish-status').textContent='Статус: не опубликовано';notice('Файл сохранён. Граф не изменён.');await refreshList();}catch(e){notice(e.message,true);}};
  $('create').onclick=async()=>{try{const d=await api('/api/ontologies',{method:'POST',body:JSON.stringify({slug:$('new-slug').value,text:$('editor').value})});state.slug=d.slug;state.sha=d.sha256;state.savedText=$('editor').value;state.dirty=false;$('current-meta').textContent=`${d.slug}.ttl · граф ${d.uri}`;$('publish-status').textContent='Статус: не опубликовано';notice('Новый файл сохранён. Граф не изменён.');await refreshList();}catch(e){notice(e.message,true);}};
  $('publish').onclick=async()=>{if(state.dirty){notice('Сначала сохраните текст.',true);return;}try{const d=await api(`/api/ontologies/${encodeURIComponent(state.slug)}/publish`,{method:'POST',body:'{}'});$('publish-status').textContent=`Статус: опубликовано · ${d.message}`;notice(d.message);}catch(e){notice(e.message,true);}};
  $('file-input').onchange=async event=>{const file=event.target.files[0];if(!file)return;const text=await file.text();$('editor').value=text;$('new-slug').value=file.name.replace(/\.ttl$/i,'');state.slug=null;state.sha=null;state.savedText='';state.dirty=true;changed();};
  $('auth-check').onclick=async()=>{try{await api('/api/ontologies/validate',{method:'POST',body:JSON.stringify({slug:'access-check',text:'<urn:bitrix24:ontology:access-check> a <http://www.w3.org/2002/07/owl#Ontology> .'})});notice('Доступ администратора подтверждён.');}catch(e){notice(e.message,true);}};
  window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  refreshList().catch(e=>notice(e.message,true));
})();
