const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/service-worker.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const utterance = (i, overrides = {}) => ({ id: `u${i}`, text: `確認用の発言${i}`, start_sec: i * 2, speaker: '相手', is_final: true, ...overrides });
const five = (offset=0) => Array.from({length:5}, (_,i)=>utterance(offset+i));
const results = [];
function setup() {
  const calls = { revocations: [], broadcasts: [], analyzes: [], saves: [], alerts: [], transcriptions: [], sessions: [] };
  let offscreen = false;
  const state = { incoming: [], analyze: async payload => ({ ok: true, result: { is_suspicious: false, risk_level: 'low', quote: null } }) };
  const chrome = {
    action: { setBadgeBackgroundColor(){}, setBadgeText(){}, async setIcon(){} },
    runtime: {
      id: 'test-extension', OnInstalledReason: {INSTALL:'install'},
      getURL: p=>`chrome-extension://test-extension/${p}`,
      async getContexts(){ return offscreen ? [{}] : []; },
      async sendMessage(message){if(message.type==='STATUS_UPDATE')calls.broadcasts.push(structuredClone(message)); return {ok:true}; },
      onMessage: {addListener(){}}, onStartup: {addListener(){}}, onInstalled: {addListener(){}},
    },
    tabs: { async get(id){return {id, url:'https://meet.google.com/test'};}, async create(){},
      async sendMessage(tabId,message){ calls.alerts.push({tabId,...structuredClone(message)}); }, onRemoved:{addListener(){}} },
    tabCapture: { async getMediaStreamId(){return 'stream';} },
    offscreen: { async createDocument(){offscreen=true;}, async closeDocument(){offscreen=false;} },
    downloads: {onChanged:{addListener(){}, removeListener(){}}},
  };
  const context = vm.createContext({chrome, Blob, FormData, AbortController, AbortSignal, setTimeout, clearTimeout, URL: {}, Date, Math, console:{log(){},warn(){},error(){}},
    importScripts(){},
    async fetch(url,options={}){
      if (url.startsWith('blob:')) return {async blob(){return new Blob(['test'],{type:'audio/webm'});}};
      if (url.endsWith('/transcribe')) {
        const entry = state.incoming.shift();
        calls.transcriptions.push(options.body.get('session_id'));
        const list = typeof entry === 'function' ? await entry() : entry;
        return {ok:true, async json(){return {utterances:list,has_opponent_speech:true};}};
      }
      if (url.endsWith('/detections')) calls.saves.push({url,body:JSON.parse(options.body)});
      else if (url.endsWith('/sessions')) calls.sessions.push(JSON.parse(options.body));
      return {ok:true,status:200};
    },
    async analyzeUtterances(payload){calls.analyzes.push(structuredClone(payload)); return state.analyze(payload);},
  });
  vm.runInContext(source+'\nglobalThis.controllerUnderTest = controller;', context);
  const controller = context.controllerUnderTest;
  const ingest = async list => {
    state.incoming.push(list);
    await controller.handleAudioChunk({blobUrl:'blob:test',chunkIndex:0,durationSec:3,startSec:0});
    await flush();
  };
  return {controller,calls,state,ingest,context};
}
async function test(name, fn) {
  try {await fn(); results.push({name,status:'passed'});}
  catch(error){results.push({name,status:'failed',detail:error.message});}
}


(async()=>{
for(const level of ['low','medium','high'])await test(level+'：件数・Popup同期・highのみ警告',async()=>{
 const {controller,calls,state,ingest}=setup();await controller.start(7);state.analyze=async p=>({ok:true,result:{is_suspicious:true,risk_level:level,quote:p.utterances[2].text,category:'C1',reason:'synthetic',advice:'synthetic'}});await ingest(five());assert.equal((await controller.getStatus()).detectionCount,1);assert.equal(calls.broadcasts.at(-1).detectionCount,1);assert.equal(calls.alerts.length,level==='high'?1:0);assert.equal(calls.saves.length,0);
});
await test('通常lowは数えず警告しない',async()=>{const {controller,calls,ingest}=setup();await controller.start(7);await ingest(five());assert.equal((await controller.getStatus()).detectionCount,0);assert.equal(calls.alerts.length,0);});
await test('異なるバッチのmedium・lowはそれぞれ1件',async()=>{const {controller,calls,state,ingest}=setup();await controller.start(7);let n=0;state.analyze=async p=>({ok:true,result:{is_suspicious:true,risk_level:++n===1?'medium':'low',quote:p.utterances[0].text}});await ingest(five());await ingest(five(5));assert.equal((await controller.getStatus()).detectionCount,2);assert.equal(calls.broadcasts.at(-1).detectionCount,2);assert.equal(calls.alerts.length,0);});
await test('受信IDの再送は再分析・再加算しない',async()=>{const {controller,calls,state,ingest}=setup();await controller.start(7);state.analyze=async p=>({ok:true,result:{is_suspicious:true,risk_level:'high',quote:p.utterances[0].text}});await ingest(five());await ingest(five().reverse());assert.equal((await controller.getStatus()).detectionCount,1);assert.equal(calls.analyzes.length,1);assert.equal(calls.alerts.length,1);});
for(const level of ['low','medium','high'])await test('フォールバック後'+level+'復旧で重複加算・警告なし',async()=>{const {controller,calls,state,ingest}=setup();await controller.start(7);let n=0;state.analyze=async p=>++n===1?{ok:false,error:'api-network-error'}:{ok:true,result:{is_suspicious:true,risk_level:level,quote:p.utterances[2].text}};const batch=five();batch[2].text='暗証番号を教えてください';await ingest(batch);await ingest([]);assert.equal(calls.analyzes.length,2);assert.equal((await controller.getStatus()).detectionCount,1);assert.equal(calls.alerts.length,1);assert.equal(calls.saves.length,1);});
await test('フォールバック連続失敗でも1件のまま',async()=>{const {controller,calls,state,ingest}=setup();await controller.start(7);state.analyze=async()=>({ok:false,error:'api-network-error'});const batch=five();batch[2].text='至急、振込をお願いします';await ingest(batch);await ingest([]);await ingest([]);assert.equal((await controller.getStatus()).detectionCount,1);assert.equal(calls.alerts.length,1);assert.equal(calls.saves.length,1);});
await test('フォールバック2発言を数え、復旧時は追加しない',async()=>{const {controller,calls,state,ingest}=setup();await controller.start(7);let n=0;state.analyze=async p=>++n===1?{ok:false,error:'api-network-error'}:{ok:true,result:{is_suspicious:true,risk_level:'high',quote:p.utterances[2].text}};const batch=five();batch[1].text='暗証番号を教えてください';batch[2].text='至急、振込をお願いします';await ingest(batch);await ingest([]);assert.equal((await controller.getStatus()).detectionCount,2);assert.equal(calls.alerts.length,2);});
await test('停止後のレポートIDと新規通話の件数リセット',async()=>{const {controller,state,ingest}=setup();await controller.start(7);const first=(await controller.getStatus()).sessionId;state.analyze=async p=>({ok:true,result:{is_suspicious:true,risk_level:'medium',quote:p.utterances[0].text}});await ingest(five());await controller.stop();assert.equal((await controller.getStatus()).lastSessionId,first);await controller.start(8);assert.equal((await controller.getStatus()).detectionCount,0);assert.notEqual((await controller.getStatus()).sessionId,first);});
console.log(JSON.stringify({scope:'Actual Service Worker in VM; mocked Chrome and network',passed:results.filter(r=>r.status==='passed').length,failed:results.filter(r=>r.status==='failed').length,results},null,2));if(results.some(r=>r.status==='failed'))process.exitCode=1;
})();
