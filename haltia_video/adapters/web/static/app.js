// SPDX-License-Identifier: GPL-2.0
// UI state stays in this tab; all library and playback rules belong to the core.
const $ = id => document.getElementById(id);
const video = $('video');
let state = null, page = {view:'home', params:{}}, history = [], events = null;
let revision = 0, renderGeneration = 0, refreshTimer = null, refreshBusy = false, refreshAgain = false;
let pendingAction = null, pendingPlay = null, playGeneration = 0, playing = null;
let reportedAt = 0, seeking = false;
const preferences = JSON.parse(localStorage.getItem('haltia-view') || '{}');
const bookmarksFilters = Object.assign({kind:'all', source:'all', state:'all', sort:'added', reverse:true}, preferences.bookmarks || {});

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
function button(text, action, cls) {
  const node = el('button', text, cls);
  node.type = 'button'; node.onclick = action; return node;
}
function select(options, value, change, label) {
  const node = el('select'); node.setAttribute('aria-label',label);
  options.forEach(([id,title]) => { const option=el('option',title); option.value=id; node.append(option); });
  node.value=value; node.onchange=()=>change(node.value); return node;
}
function time(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '';
  seconds=Math.max(0,Math.floor(seconds));
  const h=Math.floor(seconds/3600), m=Math.floor(seconds/60)%60, s=seconds%60;
  return (h ? h+':'+String(m).padStart(2,'0') : m)+':'+String(s).padStart(2,'0');
}
function sourceName(id) { return ((state && state.sources || []).find(s=>s.id===id)||{}).label || id; }
function identity(kind,obj) { return [obj.source,kind,kind==='playlist'&&obj.source==='vk'?obj.owner_id||'':'',obj.id].join('|'); }
function note(text) { $('note').textContent=text||''; }
async function api(path, body) {
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const text=await response.text(); let data;
  try { data=JSON.parse(text); } catch { data={error:text}; }
  if(response.status===401){ needPair(); throw Error('Подключите браузер по коду с ПК.'); }
  if(!response.ok) throw Error(data.error||'Не удалось выполнить запрос.');
  return data;
}
async function query(view,params={}) { return api('/api/query',{view,params}); }
async function command(name,params={}) {
  try { await api('/api/command',{name,params}); scheduleRefresh(); }
  catch(error){ note(error.message); }
}
function needPair() {
  if(events){events.close();events=null;}
  clearPlayer(); $('pair').hidden=false; $('app').hidden=true;
}
function dialog(title, choices) {
  return new Promise(resolve=>{
    const box=$('modal-body');box.replaceChildren(el('h2',title));
    function finish(value){$('modal').hidden=true;resolve(value);}
    choices.forEach(([text,value])=>box.append(button(text,()=>finish(value))));
    box.append(button('Отмена',()=>finish(null)));$('modal').hidden=false;
    box.querySelector('button').focus();
  });
}
async function confirm(text){return await dialog(text,[['Подтвердить',true]])===true;}
function savePreferences(){preferences.bookmarks=bookmarksFilters;localStorage.setItem('haltia-view',JSON.stringify(preferences));}
function navigate(view, params={}, replace=false) {
  if(!replace){history.push(page);if(history.length>20)history.shift();}
  page={view,params};pendingAction=null;pendingPlay=null;
  $('back').disabled=!history.length;
  $('query').placeholder=view==='bookmarks'?'Поиск в закладках':'Найти видео';
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('active',b.dataset.page===view));
  refresh(true);
}
function image(url){const node=el('img');node.alt='';node.loading='lazy';node.decoding='async';node.src=url;return node;}
function grid(items) {
  const box=el('div',null,'grid');
  items.forEach(item=>{
    const card=el('article',null,'card');card.tabIndex=-1;card.dataset.video=item.id;
    const picture=el('div',null,'picture');if(item.thumb)picture.append(image(item.thumb));card.append(picture);
    if(item.progress){const p=el('progress');p.max=1;p.value=item.progress;card.append(p);}
    card.append(el('h3',item.title),el('div',[item.channel,time(item.duration),sourceName(item.source)].filter(Boolean).join(' · '),'meta'));
    card.onclick=()=>play(item);card.onkeydown=e=>{if(e.key==='Enter')play(item);};
    const menu=button(item.bookmarked?'★ ⋮':'☆ ⋮',e=>{e.stopPropagation();entityMenu('video',item);});
    menu.setAttribute('aria-label','Действия с видео');card.append(menu);box.append(card);
  });return box;
}
function entityRow(kind,obj,saved=null){
  const row=el('div',null,'row');if(obj.thumb)row.append(image(obj.thumb));
  const text=el('div',null,'description');text.append(el('h3',(saved&&saved.pin!==undefined?'📌 ':'')+obj.title));
  let info=[sourceName(obj.source),obj.channel||obj.owner_title||obj.handle];
  if(saved){const links=Object.values(saved.links||{});info.push(links.length?'☁ '+links.map(l=>l.state==='synced'?'✓':l.state==='error'?'!':'↻').join(' '):'Только на устройстве');}
  text.append(el('div',info.filter(Boolean).join(' · '),'meta'));if(obj.unavailable)text.append(el('p',obj.unavailable));
  text.onclick=()=>openEntity(kind,obj);row.append(text,button('⋮',()=>entityMenu(kind,obj,saved)));return row;
}
function openEntity(kind,obj){if(kind==='video')play(obj);else navigate(kind,{entity:obj,offset:0});}
async function entityMenu(kind,obj,saved=null){
  const key=saved?saved.key:identity(kind,obj), exists=!!saved||obj.bookmarked;
  const choices=[[exists?'Удалить из закладок':'В закладки',exists?'remove':'add']];
  if(kind==='video'&&obj.channel_id)choices.push(['Открыть канал','channel'],['Подписаться на канал','follow']);
  if(kind==='channel')choices.push(['Подписаться','follow'],['Отписаться','unfollow']);
  if(saved){choices.push([saved.pin===undefined?'Закрепить':'Открепить','pin']);if(saved.pin!==undefined)choices.push(['Переместить выше','up'],['Переместить ниже','down']);if(Object.keys(saved.links||{}).length)choices.push(['Оставить только на устройстве','local']);else choices.push(['Синхронизировать с сервисом','link']);}
  const action=await dialog(obj.title,choices);
  const channel=kind==='video'?{source:obj.source,id:obj.channel_id,title:obj.channel||'Канал'}:obj;
  if(action==='add')await command('bookmark_add',{kind,entity:obj});
  if(action==='remove'&&await confirm('Удалить закладку? Синхронизированная закладка будет удалена также из сервиса.'))await command('bookmark_remove',{key});
  if(action==='local'&&await confirm('Удалить закладку из сервиса, сохранив её в приложении?'))await command('bookmark_remove',{key,local_only:true});
  if(action==='pin')await command('bookmark_pin',{key,on:saved.pin===undefined});
  if(action==='up'||action==='down')await command('bookmark_move',{key,step:action==='up'?-1:1});
  if(action==='link')await command('bookmark_link',{key});
  if(action==='channel')navigate('channel',{entity:channel});
  if(action==='follow'||action==='unfollow')await command(action,{entity:channel});
}
function pager(result){
  const row=el('div',null,'pager'),offset=page.params.offset||0;
  if(offset>0)row.append(button('← Предыдущие',()=>navigate(page.view,{...page.params,offset:Math.max(0,offset-30)},true)));
  if(result.next!=null)row.append(button('Следующие →',()=>navigate(page.view,{...page.params,offset:result.next},true)));
  return row;
}
function bookmarkTools(){
  const box=el('div'),filters=el('div',null,'tools');
  const definitions=[['kind',[['all','Все типы'],['video','Видео'],['playlist','Плейлисты'],['channel','Каналы']]],['source',[['all','Все сервисы'],...state.sources.map(s=>[s.id,s.label])]],['state',[['all','Все закладки'],['local','Локальные'],['synced','Синхронизируемые']]]];
  definitions.forEach(([name,options])=>filters.append(select(options,bookmarksFilters[name],value=>{bookmarksFilters[name]=value;savePreferences();navigate('bookmarks',{...bookmarksFilters,query:page.params.query||'',offset:0},true);},name)));
  box.append(filters);const tools=el('div',null,'tools');
  tools.append(select([['added','Время добавления'],['title','Название'],['source','Сервис']],bookmarksFilters.sort,value=>{bookmarksFilters.sort=value;savePreferences();navigate('bookmarks',{...page.params,...bookmarksFilters,offset:0},true);},'Сортировка'));
  tools.append(button(bookmarksFilters.reverse?'↓':'↑',()=>{bookmarksFilters.reverse=!bookmarksFilters.reverse;savePreferences();navigate('bookmarks',{...page.params,...bookmarksFilters},true);}),button('Добавить по ссылке',addLink),button('Обновить',()=>command('refresh')));box.append(tools);return box;
}
async function addLink(){
  const box=$('modal-body');box.replaceChildren(el('h2','Добавить закладку'));
  const input=el('input');input.type='url';input.placeholder='Ссылка на видео, плейлист или канал';
  const status=el('p');const add=button('Добавить',async()=>{add.disabled=true;status.textContent='Проверяю ссылку…';try{await api('/api/bookmark-link',{url:input.value});$('modal').hidden=true;scheduleRefresh();}catch(error){status.textContent=error.message;add.disabled=false;}});
  box.append(input,add,button('Отмена',()=>{$('modal').hidden=true;}),status);$('modal').hidden=false;
}
function resumeBlock(result){
  const saved=result.resume;if(!saved)return null;
  const box=el('section',null,'panel');box.append(el('h2','Продолжить плейлист'),el('h3',saved.video.title),el('p',[time(saved.pos),time(saved.duration)].filter(Boolean).join(' / '),'meta'));
  if(saved.duration){const progress=el('progress');progress.max=saved.duration;progress.value=saved.pos||0;box.append(progress);}
  const status=result.continuation;
  if(status&&status.kind==='finished')box.append(el('p','Плейлист просмотрен.'));
  if(status&&status.kind==='missing')box.append(el('p','Последнее видео недоступно. Предлагается первое доступное.'));
  const row=el('div',null,'tools');
  row.append(button(status&&status.kind==='finished'?'Начать сначала':'Продолжить',()=>play(null,true)),button('Показать в списке',()=>runAction('locate')),button('Сбросить прогресс',()=>runAction('reset_preview')));box.append(row);return box;
}
async function runAction(action){
  pendingAction={action,page};await attemptAction();
}
async function attemptAction(){
  const pending=pendingAction;if(!pending||pending.page!==page)return;
  try{
    const result=await query(pending.action,page.params);
    if(pendingAction!==pending)return;
    if(result.loading){note('Загружаю плейлист…');return;}
    pendingAction=null;
    if(result.failed)throw Error('Не удалось загрузить плейлист. Прогресс сохранён.');
    if(pending.action==='locate'){
      if(result.missing)throw Error('В плейлисте нет доступного видео.');
      navigate('playlist',{...page.params,offset:result.offset,target:result.target},true);
    }else if(await confirm('Сбросить прогресс '+result.count+' видео? Их общие позиции сбросятся и в других плейлистах. История, сортировка и закладки сохранятся.')){
      await command('reset_progress',{token:result.token});navigate('playlist',{...page.params,offset:0},true);
    }
  }catch(error){pendingAction=null;note(error.message);}
}
function renderSettings(root){
  root.append(el('h2','Настройки'));
  const shorts=el('label','Shorts и клипы'),check=el('input');check.type='checkbox';check.checked=state.shorts;check.onchange=()=>command('shorts',{on:check.checked});shorts.prepend(check);root.append(shorts);
  state.sources.forEach(src=>{
    const panel=el('section',null,'panel'),top=el('div',null,'tools'),enabled=el('input');enabled.type='checkbox';enabled.checked=src.enabled;enabled.onchange=()=>command('source_enabled',{source:src.id,on:enabled.checked});
    const title=el('label',src.label);title.prepend(enabled);top.append(title);panel.append(top);
    const account=src.account||{kind:'out'};panel.append(el('p',account.name?'Вход выполнен: '+account.name:account.note||src.hint||'Вход не выполнен'));
    const actions=el('div',null,'tools');
    const sign=action=>command('signin',{source:src.id,action});
    if(account.kind==='in')actions.append(button('Подписаться на каналы аккаунта',()=>command('import_subscriptions',{source:src.id})),button('Выйти',async()=>{if(await confirm('Выйти из '+src.label+' на всех устройствах приложения?'))sign('logout');}));
    else if(account.kind==='waiting'){
      const link=el('a','Открыть подтверждение VK');link.href=account.url;link.target='_blank';link.rel='noopener noreferrer';panel.append(link);
      const qr=image('/qr?source='+encodeURIComponent(src.id));qr.width=240;qr.height=240;panel.append(qr,el('p',account.code));actions.append(button('VK показал код',()=>sign('enter_code')));
    }else if(account.kind==='code'){
      const code=el('input');code.inputMode='numeric';code.placeholder='Код от VK';actions.append(code,button('Подтвердить',()=>command('signin',{source:src.id,action:'submit',code:code.value})),button('Новый QR-код',()=>sign('begin')));
    }else if(['getting','checking','submitting','confirming'].includes(account.kind))panel.append(el('p','Проверяю…'));
    else{
      (src.browsers||[]).forEach(([id,name])=>actions.append(button('Войти через '+name+' на ПК',()=>command('signin',{source:src.id,browser:id}))));
      actions.append(button(account.kind==='locked'?'Открыть связку ключей':src.login==='code'?'Войти по QR-коду':'Проверить cookies на ПК',()=>sign('begin')));
    }
    if(src.login==='cookies'){
      const file=el('input');file.type='file';file.accept='.txt';file.setAttribute('aria-label','Импорт cookies.txt');
      file.onchange=async()=>{if(!file.files.length)return;try{const response=await fetch('/api/cookies?source='+encodeURIComponent(src.id),{method:'POST',body:file.files[0]});if(!response.ok)throw Error('Не удалось импортировать cookies.');scheduleRefresh();}catch(error){note(error.message);}};
      panel.append(el('p','Можно импортировать cookies.txt, выгруженный из вашего браузера.'),file);
    }
    panel.append(actions);if(src.note)panel.append(el('p',src.note));
    if(src.sync){panel.append(el('p',src.sync.note));if(src.sync.kinds&&src.sync.kinds.length){const label=el('label','Синхронизировать закладки'),sw=el('input');sw.type='checkbox';sw.checked=src.sync.enabled;sw.onchange=()=>command('sync_enabled',{source:src.id,on:sw.checked});label.prepend(sw);panel.append(label);}}
    root.append(panel);
  });
}
async function refresh(resetScroll=false){
  if(!state)return;
  const generation=++renderGeneration,current=page;
  try{
    const result=current.view==='settings'?{}:await query(current.view,current.params);
    if(generation!==renderGeneration||page!==current)return;
    revision=Math.max(revision,result.revision||0);const root=el('div');
    const title=current.params.entity&&current.params.entity.title;$('playing-title').textContent=playing?playing.video.title:'';
    document.querySelector('header h1').textContent=title||({settings:'Настройки',search:'Поиск'}[current.view]||'Видео');
    note(result.note||(result.failed?'Не удалось загрузить данные.':result.loading?'Загружаю…':''));
    if(current.view==='settings')renderSettings(root);
    else if(current.view==='home'||current.view==='search'){
      if(current.view==='search'){
        const tools=el('div',null,'tools');tools.append(select([['search','Видео'],['channels','Каналы']],current.params.kind==='channels'?'channels':'search',kind=>navigate('search',{...current.params,kind},true),'Тип поиска'),select([['date','Сначала новые'],['relevance','Сначала подходящие']],current.params.sort||'date',sort=>navigate('search',{...current.params,sort},true),'Порядок'));root.append(tools);
      }
      (result.sections||[]).forEach(section=>{if(section.title||section.label)root.append(el('h2',section.title||section.label));if(result.kind==='channels')section.items.forEach(item=>root.append(entityRow('channel',item)));else root.append(grid(section.items));});
    }else if(current.view==='bookmarks'){
      root.append(bookmarkTools());(result.items||[]).forEach(saved=>root.append(entityRow(saved.kind,saved.object,saved)));root.append(pager(result));
    }else if(current.view==='channels'){
      root.append(button('Найти канал',()=>navigate('search',{query:$('query').value,kind:'channels',source:$('scope').value})));
      (result.items||[]).forEach(item=>root.append(entityRow('channel',item)));root.append(pager(result));
    }else if(current.view==='channel'){
      const tools=el('div',null,'tools');tools.append(button(result.followed?'Отписаться':'Подписаться',()=>command(result.followed?'unfollow':'follow',{entity:result.entity})),button('В закладки',()=>command('bookmark_add',{kind:'channel',entity:result.entity})));root.append(tools);
      if(result.playlists.length){root.append(el('h2','Плейлисты'));result.playlists.forEach(item=>root.append(entityRow('playlist',item)));}
      root.append(el('h2','Последние видео'),grid(result.items||[]));
    }else if(current.view==='playlist'){
      const tools=el('div',null,'tools');tools.append(select([['vk','Порядок сервиса'],['reverse','Обратный порядок'],['lesson','По номеру урока']],result.order,order=>navigate('playlist',{entity:result.entity,order,offset:0},true),'Сортировка плейлиста'),button('В закладки',()=>command('bookmark_add',{kind:'playlist',entity:result.entity})));root.append(tools);
      const resume=resumeBlock(result);if(resume)root.append(resume);root.append(grid(result.items||[]),pager(result));
    }else root.append(grid(result.items||[]),pager(result));
    if(!result.loading&&root.childElementCount===0)root.append(el('p','Ничего не найдено.'));
    const scroll=window.scrollY;$('content').replaceChildren(root);
    if(resetScroll)window.scrollTo(0,0);else window.scrollTo(0,scroll);
    if(current.params.target){
      const target=Array.from(root.querySelectorAll('[data-video]')).find(node=>node.dataset.video===current.params.target);
      if(target){requestAnimationFrame(()=>{if(page!==current)return;target.classList.add('located');target.focus({preventScroll:true});target.scrollIntoView({block:'center'});});}
    }
  }catch(error){if(page===current)note(error.message);}
}
function scheduleRefresh(){
  if(document.hidden)return;
  if(refreshBusy){refreshAgain=true;return;}
  if(refreshTimer)return;
  refreshTimer=setTimeout(async()=>{refreshTimer=null;refreshBusy=true;try{state=await query('state');await refresh();if(pendingAction)await attemptAction();if(pendingPlay)await attemptPlay();}catch(error){note(error.message);}finally{refreshBusy=false;if(refreshAgain){refreshAgain=false;scheduleRefresh();}}},200);
}
async function play(item,resume=false){
  const playlist=page.view==='playlist'?page.params.entity:null;
  pendingPlay={video:item,playlist,order:page.params.order,resume,request:crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random(),previous:playing&&playing.id};
  await attemptPlay();
}
async function attemptPlay(){
  const pending=pendingPlay;if(!pending||pending.busy)return;pending.busy=true;const generation=++playGeneration;
  try{
    note('Подготавливаю видео…');const result=await api('/api/play',{...pending,quality:Number($('quality').value)});
    if(generation!==playGeneration||pendingPlay!==pending){if(result.id)api('/api/stop',{id:result.id}).catch(()=>{});return;}
    if(result.loading)return;
    pendingPlay=null;await installPlayback(result);note('');
  }catch(error){if(generation===playGeneration){pendingPlay=null;note(error.message);}}
  finally{pending.busy=false;}
}
async function installPlayback(session){
  session.ready=false;playing=session;reportedAt=0;$('player').hidden=false;$('playing-title').textContent=session.video.title;
  $('service-link').href=session.service_url;$('previous').disabled=!session.previous;$('next').disabled=!session.next;
  $('player-note').textContent='';video.controls=session.mode==='direct';video.src=session.url;
  video.onloadedmetadata=()=>{if(playing!==session)return;if(session.mode==='direct'&&session.pos>0)video.currentTime=session.pos;session.ready=true;updateTimeline();};
  try{await video.play();}catch{$('player-note').textContent='Нажмите ▶, чтобы начать просмотр.';}
  $('player').scrollIntoView({block:'start'});
}
function position(){return playing?(playing.offset||0)+(video.currentTime||0):0;}
function duration(){return playing&&(playing.duration||(Number.isFinite(video.duration)?video.duration:null));}
function updateTimeline(){if(!playing)return;const d=duration(),pos=position();$('time').textContent=time(pos)+(d?' / '+time(d):'');if(!seeking){$('timeline').max=d||Math.max(1,pos);$('timeline').value=pos;}$('timeline').disabled=!d;$('toggle-play').textContent=video.paused?'▶':'⏸';}
async function report(force=false,beacon=false,ended=false){
  if(!playing||!playing.ready)return;
  const now=Date.now();if(!force&&now-reportedAt<5000)return;reportedAt=now;
  const data={id:playing.id,generation:playing.generation,pos:position(),duration:duration(),paused:video.paused,ended,force};
  if(beacon&&navigator.sendBeacon){navigator.sendBeacon('/api/progress',new Blob([JSON.stringify(data)],{type:'application/json'}));return;}
  try{const result=await api('/api/progress',data);if(!result.saved)$('player-note').textContent='Общий прогресс сохраняется на другом устройстве.';}catch{ /* Last accepted progress remains on the PC. */ }
}
async function seek(pos,quality=null){
  if(!playing)return;await report(true);const current=playing;
  try{const result=await api('/api/seek',{id:current.id,pos,quality});if(playing===current)await installPlayback(result);}catch(error){$('player-note').textContent=error.message;}
}
function clearPlayer(){playGeneration++;pendingPlay=null;playing=null;video.pause();video.removeAttribute('src');video.load();$('player').hidden=true;}
async function stopPlayer(){if(playing){const id=playing.id;await report(true);clearPlayer();try{await api('/api/stop',{id});}catch{}}}
async function adjacent(which){if(!playing||!playing[which])return;await report(true);pendingPlay={video:playing[which],playlist:playing.playlist,order:playing.order,previous:playing.id,request:String(Date.now())+Math.random()};await attemptPlay();}
video.ontimeupdate=()=>{updateTimeline();report();};video.onpause=()=>{updateTimeline();report(true);};video.onseeked=()=>report(true);
video.onended=async()=>{await report(true,false,true);if(playing&&playing.next)await adjacent('next');};
video.onerror=()=>{if(playing)$('player-note').textContent='Не удалось воспроизвести видео. Можно открыть его в сервисе.';};
$('toggle-play').onclick=()=>{if(video.paused)video.play().catch(()=>{$('player-note').textContent='Не удалось начать просмотр.';});else video.pause();};
$('previous').onclick=()=>adjacent('previous');$('next').onclick=()=>adjacent('next');$('close-player').onclick=stopPlayer;
$('timeline').oninput=()=>{seeking=true;$('time').textContent=time(Number($('timeline').value));};
$('timeline').onchange=()=>{seeking=false;const pos=Number($('timeline').value);if(playing&&playing.mode==='direct')video.currentTime=pos;else seek(pos);};
$('quality').onchange=()=>seek(position(),Number($('quality').value));
$('back').onclick=()=>{if(history.length){page=history.pop();navigate(page.view,page.params,true);}};
$('settings').onclick=()=>navigate('settings');document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>navigate(b.dataset.page,b.dataset.page==='bookmarks'?{...bookmarksFilters}:{}));
$('search-form').onsubmit=e=>{e.preventDefault();const query=$('query').value.trim();if(page.view==='bookmarks')navigate('bookmarks',{...bookmarksFilters,query,offset:0},true);else navigate('search',{query,source:$('scope').value,kind:page.view==='channels'?'channels':'search'});};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();else if(video.webkitEnterFullscreen&&!$('player').hidden)video.webkitEnterFullscreen();else note('Полноэкранный режим недоступен в этом браузере.');}catch{note('Для полноэкранного режима нажмите кнопку ещё раз.');}};
$('pair-form').onsubmit=async e=>{e.preventDefault();try{await api('/pair',{code:$('pair-code').value.trim(),name:$('device-name').value});$('pair-code').value='';await connect();}catch(error){$('pair-note').textContent=error.message;}};
async function connect(){
  try{
    state=await query('state');$('pair').hidden=true;$('app').hidden=false;
    const scope=$('scope').value||'all';
    $('scope').replaceChildren(el('option','Все сервисы'));$('scope').firstChild.value='all';state.sources.filter(s=>s.enabled).forEach(s=>{const o=el('option',s.label);o.value=s.id;$('scope').append(o);});
    if(Array.from($('scope').options).some(option=>option.value===scope))$('scope').value=scope;
    if(events)events.close();events=new EventSource('/events');
    events.onmessage=event=>{
      const data=JSON.parse(event.data);
      if(data.type==='shutdown'){events.close();events=null;clearPlayer();note('Видео на ПК отключено. Переподключитесь после запуска приложения.');return;}
      if(data.type==='device_revoked'&&data.data.device===state.device){needPair();return;}
      if(data.type==='playback_stopped'&&playing&&playing.id===data.data.id){clearPlayer();return;}
      if(data.type==='library'&&data.data.what==='position')return;
      if(data.type==='playback')return;
      if(data.type==='resync'||(data.revision||0)>revision)scheduleRefresh();
    };
    events.onerror=()=>note('Связь с ПК прервана. Восстанавливаю соединение…');
    await refresh();
  }catch(error){if(!$('pair').hidden)$('pair-note').textContent=error.message;else note('ПК недоступен. Проверьте, что приложение открыто и веб-доступ включён.');}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden){report(true,true);if(refreshTimer){clearTimeout(refreshTimer);refreshTimer=null;}}else connect();});
window.addEventListener('pagehide',()=>report(true,true));
const fragment=new URLSearchParams(location.hash.slice(1));
if(fragment.has('pair')){$('pair-code').value=fragment.get('pair');window.history.replaceState(null,'',location.pathname);needPair();$('pair-form').requestSubmit();}else connect();
