import {spawn} from 'node:child_process';
import fs from 'node:fs';
const out=process.argv[2];
const chrome=spawn('/home/qingtian/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell',['--no-sandbox','--disable-gpu','--disable-background-networking','--no-first-run','--remote-debugging-port=9241','--window-size=1600,1200','about:blank'],{stdio:['ignore','ignore',fs.openSync(out+'/browser.log','w')]});
let ws;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
try {
 let pages;
 for(let i=0;i<100;i++){try {pages=await (await fetch('http://127.0.0.1:9241/json/list')).json();break;}catch{await sleep(200)}}
 ws=new WebSocket(pages[0].webSocketDebuggerUrl); await new Promise(r=>ws.onopen=r);
 let id=0;const pending=new Map();
 ws.onmessage=e=>{const m=JSON.parse(e.data); if(m.id && pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))});
 const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true}); if(r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value};
 const until=async expression=>{for(let i=0;i<200;i++){if(await ev(expression))return;await sleep(200)} throw new Error('Timeout: '+expression)};
 await call('Page.enable');await call('Network.enable');
 await call('Network.setBlockedURLs',{urls:['https://*']});
 await call('Page.navigate',{url:'http://127.0.0.1:7872/'});
 await until("!!document.querySelector('#message-input textarea') && !!document.querySelector('#send-message') && !document.querySelector('#send-message').disabled");
 const click=async expr=>{const r=await ev(`(()=>{const e=${expr};if(!e)return null; e.scrollIntoView({block:'center'});const b=e.getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}})()`);if(!r)throw Error('Element missing '+expr);await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...r});await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...r});};
 const send=async (text,count)=>{await click("document.querySelector('#message-input textarea')");await call('Input.insertText',{text});await click("document.querySelector('#send-message')");await until(`document.querySelectorAll('#customer-chat .message.bot').length>=${count} && document.querySelector('#reply-status [data-reply-state=done]') && !document.querySelector('#send-message').disabled`);};
 // The backend callback finishes before the last control update; wait on real UI state.
 await send('我想退S20017。',1);
 await until("document.querySelector('#return-task')?.textContent.includes('S20017')");
 await send('我先确认一下，暂时不选商品。',2);
 await ev("(()=>{const c=[...document.querySelectorAll('input[type=checkbox]')].find(e=>e.closest('label')?.textContent.includes('显示开发观察'));if(c)c.click()})()");
 fs.writeFileSync(out+'/turn2_dom.html',await ev('document.documentElement.outerHTML'));
 const first=await ev("({text:document.querySelector('#customer-chat').innerText,card:document.querySelector('#return-task').innerText,trace:document.querySelector('#execution-trace')?.innerText || ''})");
 const oldOrigin=await ev('performance.timeOrigin');
 await call('Page.reload');
 await until(`performance.timeOrigin!==${oldOrigin} && document.readyState==='complete'`);
 await until("[...document.querySelectorAll('#conversation-history label')].some(e=>e.textContent.includes('我想退S20017'))");
 await click("[...document.querySelectorAll('#conversation-history label')].find(e=>e.textContent.includes('我想退S20017'))");
 await until("document.querySelector('#return-task')?.textContent.includes('S20017') && document.querySelectorAll('#customer-chat .message.bot').length>=2");
 const restored=await ev("({text:document.querySelector('#customer-chat').innerText,card:document.querySelector('#return-task').innerText})");
 await send('我想退S20017。',3);
 await until("document.querySelector('#return-task')?.textContent.includes('S20017')");
 await ev("(()=>{const c=[...document.querySelectorAll('input[type=checkbox]')].find(e=>e.closest('label')?.textContent.includes('显示开发观察'));if(c&&!c.checked)c.click()})()");
 await until("!!document.querySelector('#execution-trace')");
 const final=await ev("({text:document.querySelector('#customer-chat').innerText,card:document.querySelector('#return-task').innerText,trace:document.querySelector('#execution-trace')?.innerText || ''})");
 fs.writeFileSync(out+'/browser_evidence.json',JSON.stringify({url:'http://127.0.0.1:7872/',browser:'Chromium via CDP; real clicks and text input',turns:3,reload_and_history_restore:true,first,restored,final},null,2));
 await ev('window.scrollTo(0,0)');const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(out+'/ui.png',Buffer.from(shot.data,'base64'));
 console.log(JSON.stringify({turns:3,reload_and_history_restore:true,card:final.card}));
}catch(e){fs.writeFileSync(out+'/browser_failure.txt',String(e.stack||JSON.stringify(e)));throw e}
finally {if(ws)ws.close();chrome.kill()}
