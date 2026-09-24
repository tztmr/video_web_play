import {request,jsonOptions} from './auth-client.js';
const $=s=>document.querySelector(s);
let token=location.pathname==='/setup'?location.hash.slice(1):'';
if(token)history.replaceState(null,'','/setup');
const setup=location.pathname==='/setup';
if(setup){$('#auth-title').textContent='开启你的小剧场';$('#auth-description').textContent='创建唯一的初始管理员账号。之后可以在管理中心添加观众账号。';$('#submit-auth').textContent='创建管理员并进入 →';$('#confirm-field').hidden=false;$('#confirm-password').required=true;$('#password').autocomplete='new-password';$('#auth-footer').textContent='初始化链接只可使用一次，请设置你自己的账号和密码。';if(!token){$('#submit-auth').disabled=true;$('#auth-error').textContent='请使用安装时生成的完整初始化链接。';}}
else request('/api/auth/status').then(data=>{if(data.setup_required){$('#auth-description').textContent='网站尚未初始化，请管理员使用安装时生成的初始化链接创建账号。';$('#submit-auth').disabled=true;}}).catch(error=>$('#auth-error').textContent=error.message);
$('#auth-form').addEventListener('submit',async event=>{event.preventDefault();$('#auth-error').textContent='';const password=$('#password').value;if(setup&&password!==$('#confirm-password').value){$('#auth-error').textContent='两次输入的密码不一致';return;}$('#submit-auth').disabled=true;try{await request(setup?'/api/auth/setup':'/api/auth/login',jsonOptions({username:$('#username').value.trim(),password,...(setup?{token}:{})}));token='';location.replace('/');}catch(error){$('#auth-error').textContent=error.message;$('#submit-auth').disabled=false;}});
