const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const directory=require('node:path').join(__dirname, '../src/');
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate);};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function setup(){
 const live=new Map(),revoked=[],sent=[],downloadListeners=new Set();let serial=0,swListener,offListener;
 const state={downloadState:'in_progress',chunk:async()=>{},downloadReject:false};
 const fakeConsole={log(){},warn(){},error(){}};
 const dispatch=(listener,message)=>new Promise((resolve,reject)=>{try{const keep=listener(message,{id:'test-extension',url:'chrome-extension://test-extension/src/offscreen.html'},resolve);if(keep!==true)resolve(undefined);}catch(e){reject(e);}});
 const runtime=role=>({id:'test-extension',OnInstalledReason:{INSTALL:'install'},getURL:p=>'chrome-extension://test-extension/'+p,async getContexts(){return [{}];},onMessage:{addListener(fn){if(role==='sw')swListener=fn;else offListener=fn;}},onStartup:{addListener(){}},onInstalled:{addListener(){}},sendMessage(message){sent.push(message);if(role==='off'&&message.target==='service-worker')return dispatch(swListener,message);if(role==='sw'&&message.target==='offscreen')return dispatch(offListener,message);return Promise.resolve();}});
 const swChrome={runtime:runtime('sw'),action:{async setIcon(){},setBadgeBackgroundColor(){},setBadgeText(){}},offscreen:{async closeDocument(){}},tabs:{onRemoved:{addListener(){}}},downloads:{async download(o){assert.ok(live.has(o.url),'download URL must remain valid');if(state.downloadReject)throw Error('download failed');return 7;},async search(){return [{id:7,state:state.downloadState}];},onChanged:{addListener(fn){downloadListeners.add(fn);},removeListener(fn){downloadListeners.delete(fn);}}}};
 const swContext=vm.createContext({chrome:swChrome,URL:{},AbortController,Blob,FormData,Date,Math,console:fakeConsole,setTimeout,clearTimeout,importScripts(){}});
 vm.runInContext(fs.readFileSync(directory+'service-worker.js','utf8')+'\nglobalThis.controllerTest=controller;',swContext);
 // Exercise the actual message response lifecycle and actual download completion handling.
 swContext.controllerTest.handleAudioChunk=async payload=>{assert.ok(live.has(payload.blobUrl),'chunk URL must remain valid');await state.chunk(payload);};
 swContext.controllerTest.stop=async()=>{};
 let media,processor;
 class MediaRecorder {constructor(){media=this;this.state='inactive';}start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>this.onstop?.());}}
 const track={stop(){},addEventListener(){}};const stream={getTracks:()=>[track],getAudioTracks:()=>[track]};
 const node=()=>({connect(){},stream});
 class AudioContext {constructor(){this.destination={};this.sampleRate=48000;}createMediaStreamSource(){return node();}createChannelMerger(){return node();}createGain(){return {...node(),gain:{value:1},disconnect(){}};}createMediaStreamDestination(){return node();}createScriptProcessor(){processor={onaudioprocess:null,connect(){},disconnect(){}};return processor;}close(){}}
 const offContext=vm.createContext({chrome:{runtime:runtime('off')},URL:{createObjectURL(blob){const url='blob:test-'+(++serial);live.set(url,blob);return url;},revokeObjectURL(url){assert.ok(live.has(url),'URL must be released once');live.delete(url);revoked.push(url);}},navigator:{mediaDevices:{async getUserMedia(){return stream;}}},AudioContext,MediaRecorder,Blob,Date,console:fakeConsole,clearInterval,setTimeout,clearTimeout,queueMicrotask});
 vm.runInContext(fs.readFileSync(directory+'offscreen.js','utf8')+'\nglobalThis.recordTest=recorder;globalThis.finishTest=finishRecording;',offContext);
 return {state,live,revoked,sent,downloadListeners,async start(){await offContext.recordTest.start('stream');},emit(){processor.onaudioprocess?.({inputBuffer:{getChannelData(){return new Float32Array(144000);}}});media.ondataavailable({data:new Blob(['synthetic audio'])});},finish(){return offContext.finishTest();},complete(status='complete'){state.downloadState=status;for(const fn of [...downloadListeners])fn({id:7,state:{current:status}});}};
}
const results=[];async function test(name,fn){try{await fn();results.push({name,status:'passed'});}catch(e){results.push({name,status:'failed',detail:e.message});}}
(async()=>{
 await test('チャンク処理完了まではURLを保持し、応答後に解放',async()=>{const c=setup(),wait=deferred();c.state.chunk=()=>wait.promise;await c.start();c.emit();await flush();assert.equal(c.live.size,1);wait.resolve();await flush();assert.equal(c.live.size,0);assert.equal(c.revoked.length,1);});
 await test('チャンク処理の失敗応答でもURLを解放',async()=>{const c=setup();c.state.chunk=async()=>{throw Error('synthetic failure');};await c.start();c.emit();await flush();assert.equal(c.live.size,0);assert.equal(c.revoked.length,1);});
 await test('全体録音URLをダウンロード完了まで保持し、完了後に解放',async()=>{const c=setup();await c.start();const done=c.finish();await flush();assert.equal(c.live.size,1);assert.equal(c.downloadListeners.size,1);c.complete();await done;assert.equal(c.live.size,0);assert.equal(c.downloadListeners.size,0);});
 await test('ダウンロード中断でも全体録音URLを解放',async()=>{const c=setup();await c.start();const done=c.finish();const rejected=assert.rejects(done);await flush();assert.equal(c.live.size,1);c.complete('interrupted');await rejected;assert.equal(c.live.size,0);assert.equal(c.downloadListeners.size,0);});
 await test('ダウンロード開始失敗でも全体録音URLを解放',async()=>{const c=setup();c.state.downloadReject=true;await c.start();await assert.rejects(c.finish());assert.equal(c.live.size,0);});
 await test('停止時は送信中チャンクの完了を待って全体録音を送る',async()=>{const c=setup(),wait=deferred();c.state.chunk=()=>wait.promise;await c.start();c.emit();const done=c.finish();await flush();assert.ok(!c.sent.some(m=>m.type==='recording-ready'));wait.resolve();await flush();assert.ok(c.sent.some(m=>m.type==='recording-ready'));assert.equal(c.live.size,1);c.complete();await done;assert.equal(c.live.size,0);assert.equal(c.revoked.length,2);});
 await test('チャンク失敗でも録音ファイルを保存し、URLを解放する',async()=>{const c=setup();c.state.chunk=async()=>{throw Error('failed chunk');};await c.start();c.emit();await flush();c.state.downloadState='complete';await c.finish();assert.ok(c.sent.some(m=>m.type==='recording-ready'));assert.equal(c.live.size,0);});
 await test('二重停止でも録音URLの生成・解放は1回',async()=>{const c=setup();await c.start();const first=c.finish(),second=c.finish();await flush();assert.equal(c.sent.filter(m=>m.type==='recording-ready').length,1);c.complete();await Promise.all([first,second]);assert.equal(c.revoked.length,1);assert.equal(c.live.size,0);});

 await test('チャンクが無応答でも録音停止は期限後にファイル保存へ進む',async()=>{
   const c=setup(),wait=deferred();c.state.chunk=()=>wait.promise;
   await c.start();c.emit();const done=c.finish();
   await new Promise(resolve=>setTimeout(resolve,1100));
   assert.ok(c.sent.some(m=>m.type==='recording-ready'));
   c.complete();await done;wait.resolve();await flush();assert.equal(c.live.size,0);
 });
 console.log(JSON.stringify({scope:'actual Offscreen/SW scripts; mocked Chrome, audio and download APIs',gemini_calls:0,firestore_calls:0,passed:results.filter(r=>r.status==='passed').length,failed:results.filter(r=>r.status==='failed').length,results},null,2));if(results.some(r=>r.status==='failed'))process.exitCode=1;
})();
