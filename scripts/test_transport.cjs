// Execute the production transport/form functions with deterministic network failures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
let resets = 0, value = 'unsent diagnostic', messages = [], responses = [], calls = [];
const submit = {disabled:false};
const form = {querySelector:()=>submit, reset:()=>{resets++;value='';}};
const context = {
 document:{querySelector:s=>s==='#issue-form'?form:{},querySelectorAll:()=>[]},
 location:{search:'?room=ABC234',hash:''},localStorage:{getItem:()=> 'test-token'},
 URLSearchParams, AbortController, setTimeout, clearTimeout,
 crypto:{randomUUID:require('node:crypto').randomUUID},
 FormData: class { get(){return value;} },
 fetch:async(path, options)=>{calls.push({path,body:options.body});return await responses.shift()();}
};
vm.createContext(context);
vm.runInContext(source.slice(0, source.indexOf('function download(')),context);
context.render=()=>{};
context.toast=message=>messages.push(message);
const ok=body=>async()=>({ok:true,json:async()=>body});
const fail=async()=>{throw Error('offline');};
const event={preventDefault(){}};

(async()=>{
 // Failed form submission must not reset the input or leave submit disabled.
 context.bindForm('#issue-form',async f=>{assert.equal(f.get('title'),value);throw Error('offline');});
 await form.onsubmit(event);
 assert.equal(value,'unsent diagnostic');assert.equal(resets,0);assert.equal(submit.disabled,false);
 assert.equal(messages.pop(),'offline');

 // Same business operation reuses its request ID after a lost response.
 responses=[fail];
 await assert.rejects(context.command('issue.add',{step:'s1',title:value}),/offline/);
 responses=[ok({id:'issue1'}),ok({closed:false})];
 await context.command('issue.add',{step:'s1',title:value});
 const writes=calls.filter(c=>c.path.endsWith('/commands')).map(c=>JSON.parse(c.body));
 assert.equal(writes.length,2);assert.equal(writes[0].request_id,writes[1].request_id);
 assert.equal(writes[1].data.title,value);

 // A saved write plus failed readback is not presented as an unsaved operation.
 responses=[ok({id:'issue2'}),fail];
 const result=await context.command('issue.add',{step:'s1',title:'different issue'});
 assert.equal(result.id,'issue2');assert.match(messages.pop(),/操作已保存/);

 // A conflict refreshes authoritative state and does not reset the user's form.
 responses=[async()=>({ok:false,status:409,json:async()=>({detail:'step advanced'})}),ok({closed:false})];
 await assert.rejects(context.command('status',{step:'old',state:'done'}),/step advanced/);
 assert.equal(value,'unsent diagnostic');assert.equal(resets,0);
 console.log('PASS: failed input preserved; retry request ID reused; saved/readback failure distinguished; conflict readback preserves input');
})().catch(e=>{console.error(e);process.exitCode=1;});
