// Same MediaSource flow as the downloader: append fragmented MP4 as it arrives.
const sessions=new WeakMap();
export function prefetchNext(video,{endpoint,itemId,definition,signal}){
  if(!itemId||signal.aborted||navigator.connection?.saveData)return;
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
export async function openPlayback(video,{endpoint,fallback,params,signal,resume=0,rate=1,onReady=()=>{},onInfo=()=>{}}){
  releasePlayback(video);let objectURL=null,reader=null;
  const ready=()=>{if(signal.aborted)return;onReady();video.play().catch(()=>{});};
  const metadata=()=>{if(signal.aborted)return;video.playbackRate=rate;if(resume>0&&Number.isFinite(video.duration))video.currentTime=Math.min(resume,Math.max(0,video.duration-1));};
  const cleanup=()=>{video.removeEventListener('canplay',ready);video.removeEventListener('loadedmetadata',metadata);signal.removeEventListener('abort',cleanup);void reader?.cancel().catch(()=>{});if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}};
  sessions.set(video,cleanup);signal.addEventListener('abort',cleanup,{once:true});
  video.addEventListener('canplay',ready,{once:true});video.addEventListener('loadedmetadata',metadata,{once:true});
  async function fetchResponse(path){const response=await fetch(path+'?'+new URLSearchParams(params),{method:'POST',signal,cache:'no-store'});if(!response.ok){const data=await response.json().catch(()=>({}));const error=new Error(data.detail||data.msg||`视频加载失败（${response.status}）`);error.status=response.status;throw error;}return response;}
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
