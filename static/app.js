import {openPlayback,releasePlayback,prefetchNext,playbackLabel} from './playback.js';
import {request as accountRequest,jsonOptions,copyLink} from './auth-client.js';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeImage = (url) => { try { const u = new URL(url); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
const idOf = (item) => String(item.book_id || item.series_id || '');
const labels = {discover:'精选剧场', rank:'人气榜单', favorites:'我的收藏', history:'观看记录', search:'搜索结果'};
function readStore(key, fallback) { try { const value = JSON.parse(localStorage.getItem(key)); return value && typeof value === 'object' && !Array.isArray(value) ? value : fallback; } catch { return fallback; } }
let favorites = {}, history = {}, storageSuffix='', currentUser=null;
const state = {view:'discover', type:'drama', topic:'', query:'', page:1, items:[], hasMore:false, offset:0, passback:'', cursor:'', loading:false};
let listController, listVersion=0, topicVersion=0, currentHero=null, toastTimer;
const player = {item:null, episodes:[], index:-1, version:0, controller:null, catalogController:null, loaded:false, lastSave:0};
const video = $('#video');
const catalogCache=new Map(),warmedBooks=new Map();let intentTimer;
function cachedCatalog(bookId,signal){
  const cached=catalogCache.get(bookId);if(cached&&Date.now()-cached.at<60000)return cached.promise;
  const entry={at:Date.now()};entry.promise=api('/api/duanju/catalog',{book_id:bookId},{signal:signal||AbortSignal.timeout(30000)}).catch(error=>{if(catalogCache.get(bookId)===entry)catalogCache.delete(bookId);throw error;});
  catalogCache.set(bookId,entry);if(catalogCache.size>30)catalogCache.delete(catalogCache.keys().next().value);return entry.promise;
}
async function warmBook(bookId){
  if(!bookId||!currentUser||player.item||navigator.connection?.saveData||Date.now()-(warmedBooks.get(bookId)||0)<60000)return;
  warmedBooks.set(bookId,Date.now());if(warmedBooks.size>30)warmedBooks.delete(warmedBooks.keys().next().value);
  try{const data=await cachedCatalog(bookId),episodes=(data.items||[]).slice().sort((a,b)=>Number(a.index)-Number(b.index)),record=history[bookId];let index=record?episodes.findIndex(e=>String(e.item_id)===String(record.itemId)):0;if(index<0)index=0;if(record&&record.duration>0&&record.duration-record.time<=3&&index<episodes.length-1)index++;const id=episodes[index]?.item_id;if(id)await fetch('/api/play/warm?'+new URLSearchParams({item_id:id}),{method:'POST',signal:AbortSignal.timeout(15000)}).then(response=>response.json());}catch{}
}
function intent(event){const button=event.target.closest('[data-open],[data-resume],#hero-play');if(!button)return;clearTimeout(intentTimer);const id=button.dataset.open||button.dataset.resume||(currentHero&&idOf(currentHero));intentTimer=setTimeout(()=>warmBook(id),event.type==='pointerdown'?0:200);}
document.addEventListener('pointerover',intent);document.addEventListener('focusin',intent);document.addEventListener('pointerdown',intent);
document.addEventListener('pointerout',event=>{const button=event.target.closest('[data-open],[data-resume],#hero-play');if(button&&!button.contains(event.relatedTarget))clearTimeout(intentTimer);});

function notify(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true, 3200); }
function persist() { try { localStorage.setItem('hongguo.favorites.v2.'+storageSuffix,JSON.stringify(favorites)); localStorage.setItem('hongguo.history.v2.'+storageSuffix,JSON.stringify(history)); } catch { notify('浏览器存储已满或不可用，本次记录无法保存'); } }
async function api(path, params={}, options={}) {
  const qs=new URLSearchParams(params); const response=await fetch(`${path}${qs.size ? '?'+qs : ''}`, options);
  if(response.status===401){stopVideo();location.assign('/login');throw new Error('登录已失效，请重新登录');}
  let data; try { data=await response.json(); } catch { throw new Error('服务返回了无效数据，请检查本地服务是否正常'); }
  if (!response.ok || (data.code !== undefined && data.code !== 0)) {
    const message=data.msg || data.detail;
    throw new Error(typeof message === 'string' ? message : `请求失败（${response.status}），请稍后重试`);
  }
  return data.data ?? data;
}
function normalized(item) { return {...item, book_id:idOf(item), title:String(item.title || '未命名短剧'), cover:safeImage(item.cover), kind:item.kind || state.type}; }
function compact(item) { const {book_id,title,cover,episode_count,abstract,category,kind}=normalized(item); return {book_id,title,cover,episode_count,abstract,category,kind}; }
function saved(id) { return Boolean(favorites[id]); }
function updateSaveButtons() {
  $('#favorite-count').textContent=Object.keys(favorites).length;
  $$('[data-save]').forEach(b=>{ const yes=saved(b.dataset.save); b.textContent=yes?'♥':'♡'; b.classList.toggle('saved',yes); b.setAttribute('aria-label',yes?'取消收藏':'收藏短剧'); b.setAttribute('aria-pressed',String(yes)); });
  if(currentHero) $('#hero-save').innerHTML=saved(idOf(currentHero))?'<span>✓</span> 已收藏':'<span>＋</span> 加入收藏';
  if(player.item) $('#player-favorite').textContent=saved(idOf(player.item))?'♥ 已收藏':'♡ 收藏';
}
function toggleFavorite(item) { const id=idOf(item); if(saved(id)){delete favorites[id];notify('已取消收藏');}else{favorites[id]={...compact(item),savedAt:Date.now()};notify('已加入我的收藏');}persist();updateSaveButtons();if(state.view==='favorites')showLibrary(); }
function poster(item, index) {
  const id=idOf(item), record=history[id];
  return `<article class="drama-card"><button class="poster" data-open="${esc(id)}" aria-label="观看 ${esc(item.title)}">${item.cover?`<img src="${esc(item.cover)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:''}${state.view==='rank'?`<span class="rank-badge">${String(index+1).padStart(2,'0')}</span>`:index<3&&state.view==='discover'?'<span class="card-tag">精选好剧</span>':''}<span class="episode-badge">${item.episode_count?`${esc(item.episode_count)} 集`:'查看剧集'}</span><span class="hover-play"><span>▶</span></span></button><button class="card-title" data-open="${esc(id)}" title="${esc(item.title)}">${esc(item.title)}</button><p class="card-subtitle">${esc(state.view==='history'&&record?`看到第 ${record.episodeIndex+1} 集 · ${formatTime(record.time)}`:(item.category || (item.kind==='manju'?'漫剧':item.kind==='ai'?'AI 剧场':'真人短剧')))}</p><button class="card-save" data-save="${esc(id)}" aria-label="收藏短剧">♡</button></article>`;
}
function handleImages(root) { root.querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>img.remove(),{once:true})); }
function renderGrid() { $('#grid').innerHTML=state.items.map(poster).join('');handleImages($('#grid'));updateSaveButtons();$('#result-count').textContent=state.items.length?`${state.items.length} 部`:''; }
function skeletons() { $('#grid').innerHTML=Array.from({length:12},()=>'<div class="skeleton"><div class="poster"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>').join(''); }
function status(title,description,retry=false) { const el=$('#catalog-status');el.hidden=false;el.innerHTML=`<span class="empty-symbol">◈</span><strong>${esc(title)}</strong>${esc(description)}${retry?'<button class="secondary" id="retry-list">重新加载</button>':''}`;$('#retry-list')?.addEventListener('click',()=>loadList(state.items.length>0)); }
function renderHero(item, items) {
  currentHero=item; $('#hero-title').textContent=item.title;
  $('#hero-description').textContent=item.abstract || '一段新的故事，一场意想不到的相遇。点击播放，开启你的短剧时光。';
  $('#hero-tags').innerHTML=[item.episode_count?`${item.episode_count} 集`:null,...(item.category_tags || (item.category?[item.category]:['精选短剧'])).slice(0,3)].filter(Boolean).map(t=>`<span>${esc(t)}</span>`).join('');
  $('#hero-art').innerHTML=items.slice(0,3).filter(i=>i.cover).map(i=>`<img src="${esc(i.cover)}" alt="" referrerpolicy="no-referrer">`).join('');handleImages($('#hero-art'));$('#hero-play').disabled=false;$('#hero-save').disabled=false;updateSaveButtons();
}
async function loadTopics() {
  const version=++topicVersion, type=state.type; $('#topics').innerHTML='';
  if(state.view!=='discover')return;
  try {const data=await api('/api/duanju/web-categories',{content_type:type}); if(version!==topicVersion||state.view!=='discover')return;
    const topics=(data.groups || []).find(g=>g.id==='topic')?.items || [];
    $('#topics').innerHTML=[{id:'',name:'全部'},...topics.filter(t=>t.id)].map(t=>`<button data-topic="${esc(t.id)}" class="${t.id===state.topic?'selected':''}">${esc(t.name)}</button>`).join('');
  }catch{if(version===topicVersion)$('#topics').innerHTML='<span class="text-button">分类暂不可用，可继续浏览全部剧目</span>';}
}
async function loadList(append=false) {
  listController?.abort(); const controller=new AbortController();listController=controller;const version=++listVersion;
  state.loading=true;$('#load-more').disabled=true;$('#refresh').disabled=true;$('#catalog-status').hidden=true;
  if(!append&&state.view==='discover'){currentHero=null;$('#hero-play').disabled=true;$('#hero-save').disabled=true;}
  if(!append){state.page=1;state.offset=0;state.passback='';state.cursor='';state.items=[];state.hasMore=false;skeletons();$('#result-count').textContent='';$('#load-more').hidden=true;}
  const timed=setTimeout(()=>controller.abort('timeout'),110000);
  try {
    let data;
    if(state.view==='search')data=await api('/api/duanju/search',{key:state.query,content_type:state.type,offset:state.offset,passback:state.passback},{signal:controller.signal});
    else if(state.view==='rank')data=await api('/api/duanju/rank',{type:{drama:'playlet',manju:'comic_series_rank',ai:'ai_playlet'}[state.type],limit:20,cursor:state.cursor},{signal:controller.signal});
    else data=await api('/api/duanju/web-category',{content_type:state.type,topic:state.topic,page:state.page},{signal:controller.signal});
    if(version!==listVersion)return;
    const incoming=(data.items || []).map(normalized).filter(i=>/^\d+$/.test(idOf(i)));
    const map=new Map((append?state.items:[]).map(i=>[idOf(i),i]));incoming.forEach(i=>map.set(idOf(i),i));state.items=[...map.values()];
    state.hasMore=Boolean(data.has_more);state.cursor=data.next_cursor || '';
    state.page=data.next_page || state.page+1;state.offset=data.next_offset ?? state.offset;state.passback=data.next_passback ?? '';
    renderGrid();$('#load-more').hidden=!state.hasMore;
    if(!state.items.length)status(state.view==='search'?'没有找到这个故事':'片单暂时为空',state.view==='search'?'换个关键词，或切换真人短剧、漫剧再试试。':'换一个分类，看看其他故事。');
    if(state.view==='discover'&&!append&&incoming.length)renderHero(incoming[0],incoming);
  }catch(error){if(version!==listVersion)return;if(controller.signal.aborted&&controller.signal.reason!=='timeout')return;renderGrid();status('片单加载遇到一点问题',controller.signal.reason==='timeout'?'连接超时，请稍后重试。':error.message,true);}
  finally{clearTimeout(timed);if(version===listVersion){state.loading=false;$('#load-more').disabled=false;$('#refresh').disabled=false;}}
}
function navigate(view) {
  listController?.abort();++listVersion;++topicVersion;state.view=view;state.topic='';state.loading=false;state.hasMore=false;$('#refresh').disabled=false;
  $$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('#breadcrumb').textContent=labels[view];
  $('#hero').hidden=view!=='discover';$('#filters').hidden=['favorites','history'].includes(view);$('#topics').hidden=view!=='discover';
  $('[data-type="ai"]').hidden=view==='search';$('#section-title').textContent=view==='discover'?'发现你的下一部好剧':view==='search'?`“${state.query}” 的搜索结果`:labels[view];
  $('#refresh').textContent=['favorites','history'].includes(view)?'↻ 刷新记录':'↻ 刷新片单';$('#load-more').hidden=true;$('#catalog-status').hidden=true;
  renderContinue(); if(['favorites','history'].includes(view))showLibrary();else{loadTopics();loadList();}
}
function showLibrary() {const source=state.view==='favorites'?favorites:history;state.items=Object.values(source).sort((a,b)=>(b.savedAt||b.updatedAt)-(a.savedAt||a.updatedAt));renderGrid();$('#catalog-status').hidden=true;$('#load-more').hidden=true;if(!state.items.length)status(state.view==='favorites'?'把喜欢的故事留在这里':'你的故事，还没开始',state.view==='favorites'?'点击剧目旁的爱心，慢慢攒出你的专属片单。':'开始观看一部短剧，下次回来就能接着看。');}
function formatTime(seconds=0){const n=Math.max(0,Math.floor(seconds));return `${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;}
function renderContinue() {const items=Object.values(history).sort((a,b)=>b.updatedAt-a.updatedAt).slice(0,3);$('#continue-section').hidden=state.view!=='discover'||!items.length;$('#continue-list').innerHTML=items.map(item=>`<button class="continue-card" data-resume="${esc(idOf(item))}">${item.cover?`<img src="${esc(safeImage(item.cover))}" alt="" referrerpolicy="no-referrer">`:''}<div class="continue-info"><strong>${esc(item.title)}</strong><small>第 ${item.episodeIndex+1} 集 · ${formatTime(item.time)} / ${formatTime(item.duration)}</small><div class="continue-progress"><i style="width:${Math.max(0,Math.min(100,(item.time/(item.duration||1))*100))}%"></i></div></div><span>▶</span></button>`).join('');handleImages($('#continue-list'));}
function saveProgress(force=false) {if(!player.item||!player.loaded||player.index<0||!Number.isFinite(video.duration))return;if(!force&&Date.now()-player.lastSave<3000)return;player.lastSave=Date.now();history[idOf(player.item)]={...compact(player.item),episodeIndex:player.index,itemId:player.episodes[player.index]?.item_id,time:video.currentTime,duration:video.duration,updatedAt:Date.now()};history=Object.fromEntries(Object.entries(history).sort((a,b)=>b[1].updatedAt-a[1].updatedAt).slice(0,100));persist();}
function playerStatus(message, loading=false, retry=null) {$('#player-status').innerHTML=`${loading?'<span class="spinner"></span>':''}<span>${esc(message)}</span>${retry?'<button id="retry-play" class="primary">重新尝试</button>':''}`;$('#retry-play')?.addEventListener('click',retry,{once:true});}
function stopVideo(){releasePlayback(video);player.loaded=false;video.pause();video.removeAttribute('src');video.load();}
function episodeButtons(){ $('#episodes').innerHTML=player.episodes.map((ep,i)=>`<button data-episode="${i}" class="${i===player.index?'active':''}" aria-label="播放第 ${i+1} 集" title="${esc(ep.title||`第 ${i+1} 集`)}" aria-pressed="${i===player.index}">${String(i+1).padStart(2,'0')}</button>`).join('');$('#previous').disabled=player.index<=0;$('#next').disabled=player.index<0||player.index>=player.episodes.length-1; }
async function openPlayer(item) {
  $('#share-box').hidden=currentUser?.role!=='admin';$('#share-url').value='';$('#share-copy').disabled=true;$('#share-message').textContent='';
  saveProgress(true);player.controller?.abort();player.catalogController?.abort();const version=++player.version;stopVideo();
  player.item=normalized(item);player.episodes=[];player.index=-1;$('#player-title').textContent=item.title;$('#player-description').textContent=item.abstract || '';$('#episodes').innerHTML='';$('#episode-count').textContent='';$('#playing-label').textContent='加载目录';episodeButtons();updateSaveButtons();
  if(!$('#player-dialog').open)$('#player-dialog').showModal();playerStatus('正在打开故事，加载剧集目录…',true);
  const controller=new AbortController();player.catalogController=controller;const timer=setTimeout(()=>controller.abort(),110000);
  try{const data=await cachedCatalog(idOf(item),controller.signal);if(version!==player.version)return;
    player.episodes=(data.items||[]).filter(e=>/^\d+$/.test(String(e.item_id))).sort((a,b)=>Number(a.index)-Number(b.index));if(!player.episodes.length)throw new Error('这部剧暂时没有可播放的剧集，请换一部试试');
    $('#episode-count').textContent=`${player.episodes.length} 集`;const record=history[idOf(item)];let idx=record?player.episodes.findIndex(e=>String(e.item_id)===String(record.itemId)):-1;
    if(idx<0)idx=Math.min(Math.max(0,record?.episodeIndex||0),player.episodes.length-1);const finished=record&&record.duration>0&&record.duration-record.time<=3;if(finished&&idx<player.episodes.length-1)idx+=1;const resume=record&&!finished?record.time:0;playEpisode(idx,resume);
  }catch(error){if(version!==player.version)return;playerStatus(controller.signal.aborted?'目录加载超时，请重试':error.message,false,()=>openPlayer(item));}finally{clearTimeout(timer);}
}
async function playEpisode(index, resume=0) {
  if(index<0||index>=player.episodes.length)return;saveProgress(true);player.controller?.abort();const version=++player.version;stopVideo();player.index=index;episodeButtons();$('#playing-label').textContent=`第 ${index+1} / ${player.episodes.length} 集`;
  playerStatus(`正在准备第 ${index+1} 集，请稍候…`,true);$('#playback-hint').textContent=$('#delivery').value==='direct'?'视频由当前设备直接获取，不经过网站服务器。':'兼容模式通过网站服务器处理和传输视频。';
  const controller=new AbortController();player.controller=controller;const timer=setTimeout(()=>controller.abort('timeout'),305000);
  try {
    await openPlayback(video,{endpoint:'/api/play/stream',fallback:'/api/play',delivery:$('#delivery').value,params:{item_id:player.episodes[index].item_id,definition:$('#quality').value},signal:controller.signal,resume,rate:Number($('#speed').value),
      onInfo:data=>{if(version===player.version)$('#playback-hint').textContent=playbackLabel(data);},
      onReady:()=>{if(version!==player.version)return;player.loaded=true;$('#player-status').innerHTML='';saveProgress(true);}
    });
    if(version===player.version)prefetchNext(video,{endpoint:'/api/play/prefetch',itemId:player.episodes[index+1]?.item_id,definition:$('#quality').value,signal:controller.signal});
  }catch(error){if(version!==player.version)return;if(error.status===401){location.assign('/login');return;}if(controller.signal.aborted&&controller.signal.reason!=='timeout')return;playerStatus(controller.signal.reason==='timeout'?'视频准备超时，请降低清晰度后重试':error.message,false,()=>playEpisode(index,resume));}
  finally{clearTimeout(timer);}
}
function closePlayer(){saveProgress(true);player.controller?.abort();player.catalogController?.abort();++player.version;stopVideo();player.item=null;$('#player-dialog').close();renderContinue();if(state.view==='history')showLibrary();}

$('.sidebar nav').addEventListener('click',e=>{const button=e.target.closest('[data-view]');if(button){$('#search-input').value='';navigate(button.dataset.view);window.scrollTo({top:0,behavior:'smooth'});}});
$('#search-form').addEventListener('submit',e=>{e.preventDefault();const query=$('#search-input').value.trim();if(!query){navigate('discover');return;}state.query=query;if(state.type==='ai'){state.type='drama';$$('[data-type]').forEach(b=>b.classList.toggle('selected',b.dataset.type===state.type));notify('搜索支持真人短剧与漫剧，已切换至真人短剧');}navigate('search');});
$('.type-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-type]');if(!b||b.dataset.type===state.type)return;state.type=b.dataset.type;state.topic='';$$('[data-type]').forEach(t=>t.classList.toggle('selected',t===b));loadTopics();loadList();});
$('#topics').addEventListener('click',e=>{const b=e.target.closest('[data-topic]');if(!b)return;state.topic=b.dataset.topic;$$('[data-topic]').forEach(t=>t.classList.toggle('selected',t===b));loadList();});
$('#grid').addEventListener('click',e=>{const b=e.target.closest('[data-open],[data-save]');if(!b)return;const item=state.items.find(i=>idOf(i)===(b.dataset.open||b.dataset.save));if(item)b.dataset.save?toggleFavorite(item):openPlayer(item);});
$('#hero-play').addEventListener('click',()=>currentHero&&openPlayer(currentHero));$('#hero-save').addEventListener('click',()=>currentHero&&toggleFavorite(currentHero));
$('#load-more').addEventListener('click',()=>{if(!state.loading&&state.hasMore)loadList(true);});$('#refresh').addEventListener('click',()=>['favorites','history'].includes(state.view)?showLibrary():loadList());
$('#continue-list').addEventListener('click',e=>{const b=e.target.closest('[data-resume]');if(b&&history[b.dataset.resume])openPlayer(history[b.dataset.resume]);});$('#all-history').addEventListener('click',()=>navigate('history'));
$('#episodes').addEventListener('click',e=>{const b=e.target.closest('[data-episode]');if(b)playEpisode(Number(b.dataset.episode));});$('#previous').addEventListener('click',()=>playEpisode(player.index-1));$('#next').addEventListener('click',()=>playEpisode(player.index+1));
$('#player-favorite').addEventListener('click',()=>player.item&&toggleFavorite(player.item));$('#close-player').addEventListener('click',closePlayer);$('#player-dialog').addEventListener('cancel',e=>{e.preventDefault();closePlayer();});
$('#delivery').addEventListener('change',()=>{if(player.index>=0)playEpisode(player.index,video.currentTime||0);});
$('#quality').addEventListener('change',()=>{if(player.index>=0)playEpisode(player.index,video.currentTime||0);});$('#speed').addEventListener('change',()=>video.playbackRate=Number($('#speed').value));
video.addEventListener('timeupdate',()=>saveProgress());video.addEventListener('pause',()=>saveProgress(true));video.addEventListener('ended',()=>{saveProgress(true);if($('#auto-next').checked&&player.index<player.episodes.length-1)playEpisode(player.index+1);else if(player.index===player.episodes.length-1)$('#playback-hint').textContent='这段故事已看完，去发现下一部好剧吧。';});
video.addEventListener('error',()=>{if(!video.getAttribute('src')||!player.item)return;playerStatus($('#delivery').value==='direct'?'这台设备暂时无法直接播放，请选择兼容模式。':'播放失败，请重新准备视频后再试。',false,()=>playEpisode(player.index));});
window.addEventListener('pagehide',()=>saveProgress(true));document.addEventListener('visibilitychange',()=>{if(document.hidden)saveProgress(true);});
async function checkHealth(){try{const result=await api('/api/health');$('#connection-dot').classList.toggle('ready',result.playback_ready);$('#connection-label').textContent=result.playback_ready?'本地服务已连接':'缺少 FFmpeg 播放组件';if(!result.playback_ready)notify('请安装 FFmpeg，或设置 HONGGUO_PLAYBACK_TOOLS_DIR 后重启服务');}catch{$('#connection-label').textContent='本地服务未连接';}}
$('#share-form').addEventListener('submit',async event=>{event.preventDefault();if(!player.item)return;const item=player.item,version=player.version;$('#share-create').disabled=true;$('#share-message').textContent='正在核对剧集目录…';try{const data=await accountRequest('/api/admin/shares',jsonOptions({book_id:idOf(item),title:item.title,cover:item.cover||'',abstract:item.abstract||'',kind:item.kind||'drama',days:Number($('#share-days').value)}));if(version!==player.version){notify('链接已生成，可在管理中心查看');return;}const url=location.origin+data.path;$('#share-url').value=url;$('#share-copy').disabled=false;$('#share-message').textContent=(data.expires?`有效期至 ${new Date(data.expires*1000).toLocaleString()}`:'永久有效')+' · 访客免登录观看这部剧，可在管理中心撤销。';}catch(error){$('#share-message').textContent=error.message;}finally{$('#share-create').disabled=false;}});
$('#share-copy').addEventListener('click',async()=>{if(await copyLink($('#share-url').value))notify('分享链接已复制');else{$('#share-url').select();notify('请手动复制选中的链接');}});
$('#logout').addEventListener('click',async()=>{saveProgress(true);try{await accountRequest('/api/auth/logout',{method:'POST'});location.replace('/login');}catch(error){notify(error.message);}});
async function initialize(){try{currentUser=(await accountRequest('/api/auth/me')).user;storageSuffix=String(currentUser.id);favorites=readStore('hongguo.favorites.v2.'+storageSuffix,{});history=readStore('hongguo.history.v2.'+storageSuffix,{});$('#account-name').textContent=currentUser.username;$('#admin-link').hidden=currentUser.role!=='admin';$('#share-box').hidden=currentUser.role!=='admin';checkHealth();updateSaveButtons();navigate('discover');}catch{location.replace('/login');}}
initialize();
