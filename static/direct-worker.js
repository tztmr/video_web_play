import {decryptMp4} from './mp4-decrypt.js';
const LIMIT=256*1024*1024;
let lastProgress=0;
function progress(stage,value){const now=performance.now();if(value===1||now-lastProgress>200){lastProgress=now;postMessage({stage,progress:value});}}
async function download(url){
  const parsed=new URL(url);
  if(parsed.protocol!=='https:'||parsed.username||parsed.password||!(parsed.hostname==='qznovelvod.com'||parsed.hostname.endsWith('.qznovelvod.com')))throw new Error('视频地址不可用，请重新打开');
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);
  try{
    const response=await fetch(url,{signal:controller.signal,credentials:'omit',referrerPolicy:'no-referrer',cache:'no-store'});
    if(!response.ok||!response.body)throw new Error('视频源连接失败');
    const length=Number(response.headers.get('content-length'))||0;
    if(length>LIMIT)throw new Error('视频较大，请选择兼容模式');
    const reader=response.body.getReader(),parts=[];let total=0;
    try{while(true){const {value,done}=await reader.read();if(done)break;total+=value.length;if(total>LIMIT)throw new Error('视频较大，请选择兼容模式');parts.push(value);progress('download',length?Math.min(.99,total/length):0);}}
    finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    if(total<1024||(length&&length!==total))throw new Error('视频源返回的文件不完整');
    const data=new Uint8Array(total);let offset=0;for(const part of parts){data.set(part,offset);offset+=part.length;}
    progress('download',1);return data;
  }finally{clearTimeout(timer);controller.abort();}
}
onmessage=async({data:descriptor})=>{
  try{
    let bytes;
    for(const url of descriptor.urls){try{bytes=await download(url);break;}catch{/* Try the provider's alternate CDN; never a website media endpoint. */}}
    if(!bytes)throw new Error('无法直连视频源，请检查本地网络，或手动选择兼容模式');
    const clear=await decryptMp4(bytes,descriptor.key,value=>progress('prepare',value));
    postMessage({buffer:clear.buffer},[clear.buffer]);
  }catch(error){postMessage({error:error.message||'浏览器准备视频失败，请选择兼容模式'});}
};
