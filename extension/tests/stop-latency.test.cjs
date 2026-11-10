const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/service-worker.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const utterance = (i, overrides = {}) => ({ id: `u${i}`, text: `確認用の発言${i}`, start_sec: i * 2, speaker: '相手', is_final: true, ...overrides });
const five = (offset=0) => Array.from({length:5}, (_,i)=>utterance(offset+i));
function setup() {
  const calls = { analyzes: [], saves: [], alerts: [], transcriptions: [], sessions: [] };
  let offscreen = false;
  let messageListener;
  const state = { incoming: [], analyze: async payload => ({ ok: true, result: { is_suspicious: false, risk_level: 'low', quote: null } }) };
  const chrome = {
    action: { setBadgeBackgroundColor(){}, setBadgeText(){}, async setIcon(){} },
    runtime: {
      id: 'test-extension', OnInstalledReason: {INSTALL:'install'},
      getURL: p=>`chrome-extension://test-extension/${p}`,
      async getContexts(){ return offscreen ? [{}] : []; },
      async sendMessage(){ return {ok:true}; },
      onMessage: {addListener(fn){messageListener = fn;}}, onStartup: {addListener(){}}, onInstalled: {addListener(){}},
    },
    tabs: { async get(id){return {id, url:'https://meet.google.com/test'};}, async create(){},
      async sendMessage(tabId,message){ calls.alerts.push({tabId,...structuredClone(message)}); }, onRemoved:{addListener(){}} },
    tabCapture: { async getMediaStreamId(){return 'stream';} },
    offscreen: { async createDocument(){offscreen=true;}, async closeDocument(){offscreen=false;} },
    downloads: {onChanged:{addListener(){}, removeListener(){}}},
  };
  const context = vm.createContext({chrome, Blob, FormData, AbortController, Date, Math, setTimeout, clearTimeout, AbortSignal, console:{log(){},warn(){},error(){}},
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
  return {controller,calls,state,ingest,chrome,context,
    dispatch: message => new Promise(resolve => messageListener(message, {id: chrome.runtime.id}, resolve)),
  };
}

// These are desired-behavior regression tests. Failures document current limitations.
// No browser, cloud API, credentials, or real network requests are used.
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
async function within(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
const benign = { ok: true, result: { is_suspicious: false, risk_level: 'low', quote: null } };

test('analysis delay: chunk acknowledgement does not wait for analysis', async () => {
  const h = setup();
  await h.controller.start(7);
  const response = deferred();
  h.state.analyze = () => response.promise;
  const chunk = h.ingest(five());
  try {
    await within(chunk, 100, 'Audio chunk is blocked by the pending analysis response');
    assert.equal(h.calls.analyzes.length, 1);
  } finally {
    response.resolve(benign);
    await chunk;
    await h.controller.stop();
  }
});

test('analysis delay: stop completes within 3 seconds', async () => {
  const h = setup();
  await h.controller.start(7);
  const response = deferred();
  h.state.analyze = () => response.promise;
  const chunk = h.ingest(five());
  await flush();
  // Offscreen acknowledges promptly here to isolate the controller's own analysis wait.
  const stop = h.controller.stop();
  try {
    await within(stop, 3000, 'stop() still waits for analysis after 3 seconds');
  } finally {
    response.resolve(benign);
    await Promise.allSettled([chunk, stop]);
  }
});

test('transcription delay: final chunk wait cannot block stop indefinitely', async () => {
  const h = setup();
  await h.controller.start(7);
  const response = deferred();
  const originalFetch = h.context.fetch;
  h.context.fetch = (url, options) => url.endsWith('/transcribe')
    ? response.promise : originalFetch(url, options);
  const chunk = h.ingest([]);
  await flush();
  // Mirrors Offscreen waiting for its in-flight sendMessage acknowledgement on stop.
  h.chrome.runtime.sendMessage = async message => {
    if (message.target === 'offscreen' && message.type === 'stop') await chunk;
    return { ok: true };
  };
  const stop = h.controller.stop();
  try {
    await within(stop, 3000, 'Final transcription acknowledgement blocks stop beyond 3 seconds');
  } finally {
    response.resolve({ ok: true, json: async () => ({ utterances: [] }) });
    await Promise.allSettled([chunk, stop]);
  }
});

test('partial transcription failure: attempt session end and open saved report', async () => {
  const h = setup();
  await h.controller.start(7);
  const events = [];
  const originalFetch = h.context.fetch;
  h.context.fetch = (url, options) => {
    if (url.endsWith('/transcribe')) return Promise.reject(new Error('Synthetic network interruption'));
    if (url.endsWith('/end')) events.push('end');
    return originalFetch(url, options);
  };
  h.chrome.tabs.create = async ({ url }) => events.push(url);
  await h.ingest([]);
  let stopError;
  try { await h.controller.stop(); } catch (error) { stopError = error.message; }
  assert.ok(events.includes('end'), 'Session end API was skipped: ' + stopError);
  assert.ok(events.some(e => e.includes('/report/')), 'Saved report was not opened');
});

test('normal flow: end succeeds before opening exactly one report', async () => {
  const h = setup();
  await h.controller.start(7);
  const events = [];
  const originalFetch = h.context.fetch;
  h.context.fetch = async (url, options) => {
    if (url.endsWith('/end')) events.push('end');
    return originalFetch(url, options);
  };
  h.chrome.tabs.create = async ({ url }) => events.push(url);
  await h.ingest(five());
  await h.controller.stop();
  assert.equal(events.length, 2);
  assert.equal(events[0], 'end');
  assert.match(events[1], /\/report\/meet-/);
});

test('late analysis after stop cannot warn or affect the next call', async () => {
  const h = setup();
  await h.controller.start(7);
  const delayed = deferred();
  h.state.analyze = () => delayed.promise;
  await h.ingest(five());
  await h.controller.stop();
  await h.controller.start(8);
  delayed.resolve({ ok: true, result: {
    is_suspicious: true, risk_level: 'high', quote: '確認用の発言0',
  } });
  await flush();
  assert.equal(h.calls.alerts.length, 0);
  assert.equal((await h.controller.getStatus()).detectionCount, 0);
  await h.controller.stop();
});

test('analysis request receives the session abort signal on stop', async () => {
  const h = setup();
  await h.controller.start(7);
  let signal;
  h.context.analyzeUtterances = async (_payload, options) => {
    signal = options.signal;
    return benign;
  };
  await h.ingest(five());
  assert.equal(signal.aborted, false);
  await h.controller.stop();
  assert.equal(signal.aborted, true);
});

test('background download keeps Offscreen alive without blocking the next call', async () => {
  const h = setup();
  await h.controller.start(7);
  const first = (await h.controller.getStatus()).sessionId;
  const download = deferred();
  let closes = 0;
  const originalClose = h.chrome.offscreen.closeDocument;
  h.chrome.offscreen.closeDocument = async () => { closes += 1; await originalClose(); };
  h.chrome.downloads.download = async () => 1;
  h.controller.waitForDownload = () => download.promise;
  const saving = h.controller.handleRecordingReady('blob:recording');
  await flush();
  await h.controller.stop();
  assert.equal(closes, 0);
  await h.controller.start(8);
  assert.notEqual((await h.controller.getStatus()).sessionId, first);
  download.resolve();
  await saving;
  assert.equal(closes, 0, 'old download completion must not close the new recorder');
  await h.controller.stop();
  assert.equal(closes, 1);
});


test('late recording-ready acknowledgement cannot stop the next session', async () => {
  const h = setup();
  await h.controller.start(7);
  const download = deferred();
  h.controller.handleRecordingReady = () => download.promise;
  const ready = h.dispatch({ target: 'service-worker', type: 'recording-ready', blobUrl: 'blob:old' });
  await flush();
  await h.controller.stop();
  await h.controller.start(8);
  const session = (await h.controller.getStatus()).sessionId;
  download.resolve();
  await ready;
  await flush();
  assert.equal((await h.controller.getStatus()).sessionId, session);
  assert.equal((await h.controller.getStatus()).status, 'recording');
  await h.controller.stop();
});
