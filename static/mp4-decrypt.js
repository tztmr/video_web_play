// Browser implementation of the bundled core/mp4_decrypt.py CENC sample flow.
// Keep media byte offsets unchanged while removing encryption metadata.
const DROP=new Set(['senc','saio','saiz','sinf','schi','tenc','schm','frma','uuid']);
const CONTAINERS=new Set(['moov','trak','mdia','minf','stbl','stsd','edts','mvex']);
const SAMPLES=new Set(['encv','enca','avc1','avc3','mp4a','hvc1','hev1']);
const fail=()=>{throw new Error('视频结构暂不支持直接播放，请选择兼容模式');};
function u32(data,offset){if(offset<0||offset+4>data.length)fail();return new DataView(data.buffer,data.byteOffset+offset,4).getUint32(0);}
function text(data,start,end){return String.fromCharCode(...data.subarray(start,end));}
function printable(data,offset){return offset+8<=data.length&&data.subarray(offset+4,offset+8).every(v=>v>=32&&v<=126);}
function find(data,type,start,end){let p=start;while(p+8<=end){const size=u32(data,p);if(size<8||size>end-p)break;if(text(data,p+4,p+8)===type)return {offset:p,size,data:data.subarray(p+8,p+size)};p+=size;}return null;}
function all(data,type,start,end){const result=[];let p=start;while(p+8<=end){const size=u32(data,p);if(size<8||size>end-p)break;if(text(data,p+4,p+8)===type)result.push({offset:p,size,data:data.subarray(p+8,p+size)});p+=size;}return result;}
function nextBox(data,start,end){for(let p=start;p+8<=end;p++){const size=u32(data,p);if(size>=8&&size<=end-p&&printable(data,p))return p;}return -1;}
function concat(parts){const result=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let offset=0;for(const part of parts){result.set(part,offset);offset+=part.length;}return result;}
function box(type,payload){const result=new Uint8Array(8+payload.length);new DataView(result.buffer).setUint32(0,result.length);result.set([...type].map(c=>c.charCodeAt(0)),4);result.set(payload,8);return result;}
function originalCodec(data,offset,size){
  const end=offset+size;let position=offset+16;
  while(position<end){const p=nextBox(data,position,end);if(p<0)break;const length=u32(data,p);
    if(text(data,p+4,p+8)==='sinf'&&length>=16){let inner=p+8;while(inner<p+length){const child=nextBox(data,inner,p+length);if(child<0)break;const childSize=u32(data,child);if(text(data,child+4,child+8)==='frma'&&childSize>=12)return text(data,child+8,child+12);inner=child+childSize;}}
    position=p+length;
  }return '';
}
function cleanEntries(data,start,end,depth){
  if(depth>24)fail();const parts=[];let p=start;
  while(p<end){if(p+8>end){parts.push(data.subarray(p,end));break;}
    const size=u32(data,p);
    if(size<8||size>end-p||!printable(data,p)){const next=nextBox(data,p+1,end);if(next<0){parts.push(data.subarray(p,end));break;}parts.push(data.subarray(p,next));p=next;continue;}
    const type=text(data,p+4,p+8);
    if(DROP.has(type)){p+=size;continue;}
    if(type==='encv'||type==='enca')parts.push(box(originalCodec(data,p,size)||(type==='encv'?'avc1':'mp4a'),cleanTree(data,p,size,depth+1)));
    else if(CONTAINERS.has(type))parts.push(box(type,cleanTree(data,p,size,depth+1)));
    else parts.push(data.subarray(p,p+size));
    p+=size;
  }return concat(parts);
}
function cleanTree(data,offset,size,depth=0){
  const type=text(data,offset+4,offset+8);
  if(type==='stsd'||SAMPLES.has(type))return concat([data.subarray(offset+8,offset+16),cleanEntries(data,offset+16,offset+size,depth)]);
  return cleanEntries(data,offset+8,offset+size,depth);
}
function replaceCodecs(data,offset,size,depth=0){
  if(depth>24)fail();const type=text(data,offset+4,offset+8);
  if(type==='encv'||type==='enca'){const codec=originalCodec(data,offset,size)||(type==='encv'?'avc1':'mp4a');data.set([...codec].map(c=>c.charCodeAt(0)),offset+4);}
  let p=offset+8,remaining=Infinity;
  if(type==='stsd'){remaining=u32(data,offset+12);p=offset+16;}
  while(remaining-->0&&p+8<=offset+size){const childSize=u32(data,p);if(childSize<8||childSize>offset+size-p)break;replaceCodecs(data,p,childSize,depth+1);p+=childSize;}
}
function table(data,offset,count,width){if(count>1000000||offset+count*width>data.length)fail();}
function samplesForTrack(data,track){
  const mdia=find(data,'mdia',track.offset+8,track.offset+track.size);if(!mdia)return [];
  const minf=find(data,'minf',mdia.offset+8,mdia.offset+mdia.size);if(!minf)return [];
  const stbl=find(data,'stbl',minf.offset+8,minf.offset+minf.size);if(!stbl)return [];
  const start=stbl.offset+8,end=stbl.offset+stbl.size;
  const sz=find(data,'stsz',start,end),sc=find(data,'stsc',start,end),co=find(data,'stco',start,end)||find(data,'co64',start,end);
  const se=find(data,'senc',start,end)||find(data,'senc',track.offset+8,track.offset+track.size);
  if(!sz||!sc||!co||!se)return [];
  const fixed=u32(sz.data,4),count=u32(sz.data,8),entries=u32(sc.data,4),chunks=u32(co.data,4),ivCount=u32(se.data,4);
  if(count>1000000||ivCount<count||u32(se.data,0)!==0)fail();
  if(!fixed)table(sz.data,12,count,4);
  table(sc.data,8,entries,12);table(se.data,8,ivCount,8);
  const wide=text(data,co.offset+4,co.offset+8)==='co64';table(co.data,8,chunks,wide?8:4);
  const result=[];let index=0,entry=0;
  for(let chunk=0;chunk<chunks&&index<count;chunk++){
    while(entry+1<entries&&chunk+1>=u32(sc.data,8+(entry+1)*12))entry++;
    if(!entries||chunk+1<u32(sc.data,8+entry*12))continue;
    const perChunk=u32(sc.data,12+entry*12);
    let offset=wide?Number(new DataView(co.data.buffer,co.data.byteOffset+8+chunk*8,8).getBigUint64(0)):u32(co.data,8+chunk*4);
    if(!Number.isSafeInteger(offset))fail();
    for(let i=0;i<perChunk&&index<count;i++,index++){
      const size=fixed||u32(sz.data,12+index*4);
      if(size<=0||offset<0||offset+size>data.length)fail();
      const iv=new Uint8Array(16);iv.set(se.data.subarray(8+index*8,16+index*8));
      result.push({offset,size,iv});offset+=size;
    }
  }
  if(index!==count)fail();return result;
}

export async function decryptMp4(input,keyHex,onProgress=()=>{}){
  if(!/^[0-9a-f]{32}$/i.test(keyHex))throw new Error('视频播放凭据无效，请重新打开');
  const data=input instanceof Uint8Array?input:new Uint8Array(input);
  if(!data.length||data.length>256*1024*1024)fail();
  const moov=find(data,'moov',0,data.length);if(!moov)fail();
  const jobs=all(data,'trak',moov.offset+8,moov.offset+moov.size).flatMap(track=>samplesForTrack(data,track));
  if(!jobs.length)fail();
  // Reject overlapping/out-of-media samples before changing any bytes.
  const mdats=all(data,'mdat',0,data.length);let previous=0;
  for(const job of [...jobs].sort((a,b)=>a.offset-b.offset)){
    if(job.offset<previous||!mdats.some(m=>job.offset>=m.offset+8&&job.offset+job.size<=m.offset+m.size))fail();
    previous=job.offset+job.size;
  }
  if(!globalThis.crypto?.subtle)throw new Error('此浏览器缺少安全播放能力，请通过 HTTPS 打开或选择兼容模式');
  const key=await crypto.subtle.importKey('raw',Uint8Array.from(keyHex.match(/../g),v=>parseInt(v,16)),{name:'AES-CTR'},false,['decrypt']);
  let cursor=0,done=0;
  await Promise.all(Array.from({length:Math.min(16,jobs.length)},async()=>{
    while(cursor<jobs.length){const job=jobs[cursor++];
      const clear=await crypto.subtle.decrypt({name:'AES-CTR',counter:job.iv,length:64},key,data.subarray(job.offset,job.offset+job.size));
      data.set(new Uint8Array(clear),job.offset);done++;
      if(done%256===0||done===jobs.length)onProgress(done/jobs.length);
    }
  }));
  let clean=box('moov',cleanTree(data,moov.offset,moov.size));
  const padding=moov.size-clean.length;
  if(padding>=8)clean=concat([clean,box('free',new Uint8Array(padding-8))]);
  else if(padding!==0){replaceCodecs(data,moov.offset,moov.size);return data;}
  data.set(clean,moov.offset);return data;
}
