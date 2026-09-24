// Browser direct playback by default; the server's MediaSource flow is opt-in.
const sessions=new WeakMap();
export function playbackLabel(data){
  const quality=String(data.definition||'').toUpperCase();
  if(data.delivery==='direct'){
    const action=data.stage==='ready'?'当前设备直接播放':data.stage==='prepare'?'正在准备画面':'正在直连取片';
    return `${quality} · 源站直连 · ${action}${data.stage!=='ready'&&data.progress?` ${Math.round(data.progress*100)}%`:''}`;
  }
  return `${quality} · 兼容模式（通过服务器） · ${data.cached?'缓存已就绪':data.streaming?'后续画面加载中':'视频已准备好'}`;
}
export function prefetchNext(video,{endpoint,itemId,definition,signal}){
  if(!itemId||signal.aborted||navigator.connection?.saveData||video.dataset.playbackTransport==='direct')return;
  let sent=false,timer;
  const clear=()=>{clearTimeout(timer);video.removeEventListener('playing',schedule);signal.removeEventListener('abort',clear);};
  const send=()=>{if(signal.aborted||video.paused||video.ended||sent)return;sent=true;clear();void fetch(endpoint+'?'+new URLSearchParams({item_id:itemId,definition}),{method:'POST',signal,cache:'no-store'}).then(response=>response.json()).catch(()=>{});};
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(send,1500);};
  video.addEventListener('playing',schedule);signal.addEventListener('abort',clear,{once:true});
  if(!video.paused)schedule();
}
export function releasePlayback(video){sessions.get(video)?.();sessions.delete(video);}
function event(target,name,signal,action){return new Promise((resolve,reject)=>{
  const cleanup=()=>{target.removeEventListener(name,done);target.removeEventListener('error',failed);signal.removeEventListener('abort',abort);};
  const done=()=>{cleanup();resolve();},failed=()=>{cleanup();reject(new Error('视频解码失败，请重试'));},abort=()=>{cleanup();reject(signal.reason||new DOMException('Aborted','AbortError'));};
  target.addEventListener(name,done,{once:true});target.addEventListener('error',failed,{once:true});signal.addEventListener('abort',abort,{once:true});
  if(signal.aborted)abort();else if(action){try{action();}catch(error){cleanup();reject(error);}}
});}
export async function openPlayback(video,{endpoint,fallback,params,signal,resume=0,rate=1,delivery='direct',onReady=()=>{},onInfo=()=>{}}){
  releasePlayback(video);let objectURL=null,reader=null,worker=null;
  video.dataset.playbackTransport=delivery;
  const ready=()=>{if(signal.aborted)return;onReady();video.play().catch(()=>{});};
  const metadata=()=>{if(signal.aborted)return;video.playbackRate=rate;if(resume>0&&Number.isFinite(video.duration))video.currentTime=Math.min(resume,Math.max(0,video.duration-1));};
  const cleanup=()=>{worker?.terminate();worker=null;video.removeEventListener('canplay',ready);video.removeEventListener('loadedmetadata',metadata);signal.removeEventListener('abort',cleanup);void reader?.cancel().catch(()=>{});if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}};
  sessions.set(video,cleanup);signal.addEventListener('abort',cleanup,{once:true});
  video.addEventListener('canplay',ready,{once:true});video.addEventListener('loadedmetadata',metadata,{once:true});
  async function fetchResponse(path,extra={}){const response=await fetch(path+'?'+new URLSearchParams({...params,...extra}),{method:'POST',signal,cache:'no-store'});if(!response.ok){const data=await response.json().catch(()=>({}));const error=new Error(data.detail||data.msg||`视频加载失败（${response.status}）`);error.status=response.status;throw error;}return response;}
  if(delivery==='direct'){
    if(typeof Worker==='undefined'||!globalThis.crypto?.subtle)throw new Error('此浏览器暂不支持直接播放，请使用 HTTPS 或选择兼容模式');
    const hevc=Boolean(video.canPlayType('video/mp4; codecs="hvc1.1.6.L120.B0"')||video.canPlayType('video/mp4; codecs="hev1.1.6.L120.B0"'));
    const descriptor=await (await fetchResponse(endpoint.replace(/\/stream$/,'/source'),{hevc})).json();signal.throwIfAborted();
    onInfo({...descriptor,stage:'download',progress:0});
    const buffer=await new Promise((resolve,reject)=>{
      worker=new Worker('/static/direct-worker.js',{type:'module'});
      const abort=()=>{worker?.terminate();reject(new DOMException('Aborted','AbortError'));};
      const finish=()=>{signal.removeEventListener('abort',abort);worker?.terminate();worker=null;};
      worker.onmessage=({data})=>{if(data.error){finish();reject(new Error(data.error));}else if(data.buffer){finish();resolve(data.buffer);}else onInfo({...descriptor,...data});};
      worker.onerror=()=>{finish();reject(new Error('浏览器准备视频失败，请选择兼容模式'));};
      signal.addEventListener('abort',abort,{once:true});
      if(signal.aborted)abort();else worker.postMessage(descriptor);
    });
    signal.throwIfAborted();objectURL=URL.createObjectURL(new Blob([buffer],{type:'video/mp4'}));
    try{await event(video,'canplay',signal,()=>{video.src=objectURL;video.load();});}
    catch(error){if(signal.aborted)throw error;throw new Error('此设备无法直接播放该格式，请手动选择兼容模式');}
    onInfo({...descriptor,stage:'ready'});return;
  }
  const useMSE=typeof MediaSource!=='undefined';
  let response=await fetchResponse(useMSE?endpoint:fallback);signal.throwIfAborted();
  if(response.headers.get('content-type')?.includes('application/json')){const data=await response.json();signal.throwIfAborted();onInfo({...data,streaming:false});video.src=data.url;video.load();return;}
  const mime=response.headers.get('x-playback-mime');
  if(!mime||!MediaSource.isTypeSupported(mime)||!response.body){await response.body?.cancel();response=await fetchResponse(fallback);const data=await response.json();signal.throwIfAborted();onInfo({...data,streaming:false});video.src=data.url;video.load();return;}
  onInfo({definition:response.headers.get('x-duanju-definition')||params.definition,cached:false,streaming:true});
  const source=new MediaSource(),opened=event(source,'sourceopen',signal);void opened.catch(()=>{});
  reader=response.body.getReader();objectURL=URL.createObjectURL(source);video.src=objectURL;video.load();
  try{await opened;signal.throwIfAborted();const buffer=source.addSourceBuffer(mime);const duration=Number(response.headers.get('x-playback-duration'));if(duration>0&&Number.isFinite(duration))source.duration=duration;
    let received=0;
    while(true){const {done,value}=await reader.read();signal.throwIfAborted();if(done)break;received+=value.byteLength;
      while(true){try{await event(buffer,'updateend',signal,()=>buffer.appendBuffer(value));break;}catch(error){if(error.name!=='QuotaExceededError')throw error;const end=video.currentTime-30;if(buffer.buffered.length&&end>buffer.buffered.start(0))await event(buffer,'updateend',signal,()=>buffer.remove(0,end));else await new Promise(resolve=>setTimeout(resolve,200));signal.throwIfAborted();}}
    }
    if(!received)throw new Error('视频内容为空，请重试');
    if(source.readyState==='open')source.endOfStream();
    onInfo({definition:response.headers.get('x-duanju-definition')||params.definition,cached:false,streaming:false});
  }finally{await reader.cancel().catch(()=>{});reader.releaseLock();reader=null;}
}
