export async function request(path, options={}) {
  const response=await fetch(path,{credentials:'same-origin',...options});
  const data=await response.json().catch(()=>({detail:'服务器暂时不可用'}));
  if(response.status===401&&!path.startsWith('/api/auth/')){
    location.assign('/login');throw new Error('登录已失效，请重新登录');
  }
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'操作失败，请检查填写内容');
  return data;
}
export const jsonOptions=(body,method='POST')=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export async function copyLink(value){
  try{await navigator.clipboard.writeText(value);return true;}catch{return false;}
}
export const escapeText=(value)=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
