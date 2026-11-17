// Ensaio do WebView instalado: CDP local, IPC real, dados/instalação exclusivos.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs';

const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const [root,channel,baseline]=process.argv.slice(2);
assert(root && channel && baseline,'Informe sandbox, canal e instalador padrão');
for (const installer of [baseline, path.join(channel,'0.1.0/setup.exe'), path.join(channel,'0.1.1/setup.exe')]) {
  const identity=spawnSync('powershell',['-NoProfile','-Command',
    '(Get-Item -LiteralPath $env:HORUS_TEST_INSTALLER).VersionInfo.ProductName'],
    {env:{...process.env,HORUS_TEST_INSTALLER:installer},encoding:'utf8',windowsHide:true});
  assert.equal(identity.status,0,identity.stderr);
  assert.equal(identity.stdout.trim(),'HORUS Test','Ensaio exige identidade isolada HORUS Test');
}
const python=path.join(repo,'backend/.venv/Scripts/python.exe');
const support=path.join(repo,'scripts/updater-e2e-support.py');
const install=path.join(root,'app'), data=path.join(root,'data');
assert(path.basename(root).startsWith('horus-updater-e2e-'));
helper('stop');
// Reexecução preserva os resultados anteriores em vez de sobrescrevê-los.
const previous=path.join(root,`previous-${Date.now()}`);
for(const name of ['data','update-backups','restored','evidence','tauri','healthy-data','restored-healthy-data','future-health-data','wrong-signature']) {
  const source=path.join(root,name);
  if(fs.existsSync(source)){fs.mkdirSync(previous,{recursive:true});fs.renameSync(source,path.join(previous,name));}
}
for(const name of ['stop-watch','stop-writer','writer-ready','writer-result.json','restore-result.json','process-evidence.json','watch-scenario.txt']) {
  const target=path.join(root,name);if(fs.existsSync(target))fs.unlinkSync(target);
}
const report=path.join(root,'evidence');fs.mkdirSync(report,{recursive:true});
const evidence={cases:{},requests:[],events:[]};
let browser,page,app,writer,mode='valid',manifest=JSON.parse(fs.readFileSync(path.join(channel,'latest.json')));
let manifestGate,releaseManifest,artifactGate,releaseArtifact;
const original=structuredClone(manifest);
const hash=async file=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(action,message,timeout=60000) {
  const end=Date.now()+timeout;let last;
  while(Date.now()<end) {try {const result=await action();if(result)return result;} catch(error){last=error;} await delay(250);}
  throw Error(`${message}${last?`: ${last.message}`:''}`);
}
function helper(command) {
  const result=spawnSync(python,[support,command,root],{encoding:'utf8',windowsHide:true});
  assert.equal(result.status,0,result.stderr||result.stdout);return result.stdout.trim();
}
async function executable(file,args) {
  const child=spawn(file,args,{windowsHide:true,stdio:'ignore'});
  const code=await new Promise((resolve,reject)=>{child.once('exit',resolve);child.once('error',reject);});
  assert.equal(code,0,`Instalador retornou ${code}`);
}
async function attach(requireBackend=true) {
  browser=await until(async()=>{try{return await chromium.connectOverCDP('http://127.0.0.1:19431',{timeout:1500});}catch{return null;}},'CDP indisponível');
  page=await until(()=>browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().includes('tauri.localhost')),'WebView Hórus ausente');
  await page.waitForFunction(()=>Boolean(window.__TAURI_INTERNALS__?.invoke),{timeout:30000});
  // Mantém somente os estados públicos, nunca conexão/token em artefatos.
  await page.evaluate(async()=>{
    window.__e2eStates=[];
    const internals=window.__TAURI_INTERNALS__;
    const callback=internals.transformCallback(event=>window.__e2eStates.push(event.payload),false);
    await internals.invoke('plugin:event|listen',{event:'update://state',target:{kind:'Any'},handler:callback});
  });
  if(requireBackend)await until(()=>page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('backend_connection').then(()=>true)),'Backend não iniciou');
}
const invoke=(command,args={})=>page.evaluate(({command,args})=>window.__TAURI_INTERNALS__.invoke(command,args),{command,args});
async function api(route,method='GET',payload) {
  return page.evaluate(async({route,method,payload})=>{
    const c=await window.__TAURI_INTERNALS__.invoke('backend_connection');
    const r=await fetch(c.baseUrl+route,{method,headers:{Authorization:'Bearer '+c.token,'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:payload===undefined?undefined:JSON.stringify(payload)});
    if(!r.ok)throw Error(`API ${r.status}`);return r.json();
  },{route,method,payload});
}
async function start(requireBackend=true) {
  const env={...process.env,HORUS_DATA_DIR:data,WEBVIEW2_USER_DATA_FOLDER:path.join(root,'tauri'),
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=19431',
    HORUS_HERMES_CONFIG:path.join(root,'missing-hermes.json'),HORUS_HERMES_PROFILE:path.join(root,'hermes-profile'),FABRICA_HOME:path.join(root,'factory')};
  app=spawn(path.join(install,'horus-desktop.exe'),[],{env,cwd:root,windowsHide:true,stdio:'ignore'});
  await attach(requireBackend);
}
async function close() {helper('close');if(browser)await browser.close().catch(()=>{});browser=null;page=null;await delay(300);}
async function states() {return page.evaluate(()=>window.__e2eStates||[]);}
const packages=()=>evidence.requests.filter(r=>r.path.endsWith('/setup.exe'));
function save() {fs.writeFileSync(path.join(report,'result.json'),JSON.stringify(evidence,null,2));}
const server=http.createServer(async(req,res)=>{
  evidence.requests.push({path:req.url,method:req.method,at:new Date().toISOString(),mode});
  if(req.url==='/latest.json') {if(manifestGate)await manifestGate;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(manifest));return;}
  if(!/^\/\d+\.\d+\.\d+\/setup\.exe$/.test(req.url)){res.writeHead(404);res.end();return;}
  const target=path.join(channel,req.url.slice(1));
  if(!fs.existsSync(target)){res.writeHead(404);res.end();return;}
  if(artifactGate)await artifactGate;
  const bytes=fs.readFileSync(target);if(mode==='corrupt')bytes[bytes.length-1]^=1;
  res.setHeader('Content-Length',bytes.length);res.end(bytes);
});
const watcher=spawn(python,[support,'watch',root],{windowsHide:true,stdio:'ignore'});
try {
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(19430,'127.0.0.1',resolve);});
  helper('init');fs.mkdirSync(path.join(data,'updater'),{recursive:true});
  await executable(baseline,['/S',`/D=${install}`]);await start();
  let state=await invoke('update_get_state');assert.equal(state.phase,'disabled');assert.equal(state.auto_check,true);
  await delay(2000);assert.equal(evidence.requests.length,0);
  evidence.cases.default_disabled={passed:true,state};await close();
  fs.writeFileSync(path.join(data,'updater/preferences.json'),JSON.stringify({auto_check:false}));
  await executable(path.join(channel,'0.1.0/setup.exe'),['/S',`/D=${install}`]);await start();
  state=await invoke('update_get_state');assert.equal(state.current_version,'0.1.0');assert.equal(state.auto_check,false);
  await delay(2500);assert.equal(evidence.requests.length,0);
  evidence.cases.auto_check_false={passed:true,requests:0};
  const seed=spawnSync(python,[path.join(repo,'scripts/check-installer-data.py'),data,'seed'],{encoding:'utf8',windowsHide:true});assert.equal(seed.status,0,seed.stderr);
  const refusedVersions=[];
  for(const version of ['0.0.9','0.1.0']) {
    manifest={...original,version};
    state=await invoke('update_check');assert.equal(state.error.code,'downgrade_refused');assert.equal(state.available,null);
    refusedVersions.push(version);
  }
  evidence.cases.downgrade={passed:true,state,refused_versions:refusedVersions};
  manifest=structuredClone(original);state=await invoke('update_check');assert.equal(state.phase,'available');
  assert.equal(packages().length,0);assert(!fs.existsSync(path.join(data,'updater/installers')));
  evidence.cases.no_download_before_confirmation={passed:true,package_requests:0};
  await api('/v1/update/reservation','POST');
  state=await invoke('update_install',{confirmed_version:'0.1.1'});
  assert.equal(state.blocked_reason,'job_running');assert.equal(packages().length,0);
  await api('/v1/update/reservation','DELETE');
  evidence.cases.S1_reservation_denied={passed:true,state,package_requests:0};
  const beforeChecks=evidence.requests.length;
  manifestGate=new Promise(resolve=>releaseManifest=resolve);
  const checking=invoke('update_check');
  await until(()=>evidence.requests.length>beforeChecks,'Consulta não chegou ao canal');
  const simultaneousCheck=await invoke('update_check');
  const simultaneousInstall=await invoke('update_install',{confirmed_version:'0.1.1'});
  assert.equal(simultaneousCheck.phase,'checking');assert.equal(simultaneousInstall.phase,'checking');
  assert.equal(evidence.requests.length-beforeChecks,1);
  releaseManifest();manifestGate=null;await checking;
  evidence.cases.S4_concurrent_check={passed:true,manifest_requests:1,package_requests:0};
  const job=await api('/v1/jobs','POST',{operation:'factory.profile'});
  await until(async()=>{const j=await api(`/v1/jobs/${job.id}`);if(['failed','blocked'].includes(j.status))throw Error(JSON.stringify(j));return j.status==='running';},'Job fixture não ficou ativo',12000);
  state=await invoke('update_install',{confirmed_version:'0.1.1'});assert.equal(state.phase,'blocked');assert.equal(state.blocked_reason,'job_running');assert.equal(packages().length,0);
  evidence.cases.job_running={passed:true,state,job_id:job.id};
  await api(`/v1/jobs/${job.id}/cancel`,'POST');await until(async()=>!(await api('/v1/update/activity')).busy,'Cancelamento não encerrou job');
  mode='corrupt';await invoke('update_check');
  const binaryBefore=await hash(path.join(install,'horus-desktop.exe'));
  const dataBefore=helper('fingerprint'),backupsBefore=fs.existsSync(path.join(root,'update-backups'))?fs.readdirSync(path.join(root,'update-backups')).length:0;
  artifactGate=new Promise(resolve=>releaseArtifact=resolve);
  const attempted=invoke('update_install',{confirmed_version:'0.1.1'});
  await until(()=>packages().length===1,'Download adulterado não iniciou');
  const concurrent=await invoke('update_install',{confirmed_version:'0.1.1'});
  assert.equal(concurrent.phase,'downloading');assert.equal(packages().length,1);
  releaseArtifact();artifactGate=null;state=await attempted;
  assert.equal(state.error.code,'signature_invalid');assert.equal(state.current_version,'0.1.0');
  assert.equal(await hash(path.join(install,'horus-desktop.exe')),binaryBefore);assert.equal(helper('fingerprint'),dataBefore);helper('verify');
  assert.equal(fs.readdirSync(path.join(root,'update-backups')).length-backupsBefore,1);
  evidence.cases.S4_concurrent_install={passed:true,downloads:1,backups:1};
  evidence.cases.signature_invalid={passed:true,state,binary_unchanged:true,data_unchanged:true,package_requests:packages().length};
  mode='valid';
  const wrong=path.join(root,'wrong-signature');fs.mkdirSync(wrong,{recursive:true});
  const wrongKey=path.join(path.dirname(channel),'horus-updater-wrong-test.key');
  const cli=path.join(repo,'frontend/node_modules/@tauri-apps/cli/tauri.js');
  const signEnv={...process.env,CI:'1',TAURI_SIGNING_PRIVATE_KEY_PASSWORD:''};
  delete signEnv.TAURI_SIGNING_PRIVATE_KEY;
  if(!fs.existsSync(wrongKey))assert.equal(spawnSync(process.execPath,[cli,'signer','generate','--ci','--write-keys',wrongKey],{env:signEnv,stdio:'ignore',windowsHide:true}).status,0);
  fs.copyFileSync(path.join(channel,'0.1.1/setup.exe'),path.join(wrong,'setup.exe'));
  assert.equal(spawnSync(process.execPath,[cli,'signer','sign','--private-key-path',wrongKey,'--app-version','0.1.1',path.join(wrong,'setup.exe')],{env:signEnv,cwd:path.join(repo,'frontend'),stdio:'ignore',windowsHide:true}).status,0);
  manifest=structuredClone(original);manifest.platforms['windows-x86_64'].signature=fs.readFileSync(path.join(wrong,'setup.exe.sig'),'utf8').trim();
  mode='wrong_signature';
  await invoke('update_check');state=await invoke('update_install',{confirmed_version:'0.1.1'});
  assert.equal(state.error.code,'signature_invalid');assert.equal(await hash(path.join(install,'horus-desktop.exe')),binaryBefore);assert.equal(helper('fingerprint'),dataBefore);helper('verify');
  evidence.cases.S5_wrong_signature={passed:true,state,binary_unchanged:true,data_unchanged:true};
  manifest=structuredClone(original);
  mode='valid';await invoke('update_check');
  writer=spawn(python,[support,'writer',root],{windowsHide:true,stdio:'ignore'});
  await until(()=>fs.existsSync(path.join(root,'writer-ready')),'Escritor WAL não iniciou');
  await page.getByRole('button',{name:'Ajustes',exact:true}).click();
  await page.getByTestId('update-install-button').click();
  await page.getByTestId('update-confirm-dialog').waitFor({state:'visible'});
  await page.screenshot({path:path.join(report,'confirmation.png'),fullPage:true});
  const positiveBefore=packages().filter(r=>r.mode==='valid').length;
  assert.equal(positiveBefore,0,'Download positivo antes da confirmação');
  evidence.events.push({kind:'before_confirmation',at:new Date().toISOString(),states:await states(),positive_package_requests:positiveBefore});save();
  const before=packages().length;
  const currentApp=app;
  const exit=new Promise(resolve=>currentApp.once('exit',code=>resolve(code)));
  await page.getByTestId('update-confirm-install-button').click().catch(error=>{if(!/closed|destroyed/i.test(error.message))throw error;});
  let timer;const oldCode=await Promise.race([exit,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('App N não encerrou após confirmar')),90000);})]).finally(()=>clearTimeout(timer));assert.equal(oldCode,0);
  fs.writeFileSync(path.join(root,'stop-writer'),'');await new Promise(resolve=>writer.once('exit',resolve));writer=null;
  if(browser)await browser.close().catch(()=>{});browser=null;page=null;
  await delay(1500);await attach();
  state=await until(async()=>{const s=await invoke('update_get_state');return s.current_version==='0.1.1'&&s.phase!=='installing'?s:null;},'Saúde pós-reinício não concluiu');
  assert.equal(state.current_version,'0.1.1');assert.equal(state.error,null);
  const health=await api('/v1/update/health');assert.equal(health.ok,true);assert.equal(health.integrity_check,'ok');
  const receipt=JSON.parse(fs.readFileSync(path.join(data,'updater/last-update-result.json')));assert.equal(receipt.healthy,true);
  assert(packages().length>before);assert(fs.existsSync(path.join(data,'updater/installers/0.1.1/setup.exe')));
  const cachedHash=await hash(path.join(data,'updater/installers/0.1.1/setup.exe'));
  assert.equal(cachedHash,await hash(path.join(channel,'0.1.1/setup.exe')));
  evidence.cases.real_update={passed:true,confirmation:'real_settings_dialog',state,health,receipt,cached_sha256:cachedHash,backup:JSON.parse(helper('backups')),data:JSON.parse(helper('verify'))};
  await page.getByRole('button',{name:'Ajustes',exact:true}).click();
  await until(async()=>(await page.getByTestId('update-current-version').textContent()).includes('0.1.1'),'Versão nova não apareceu em Ajustes');
  await page.screenshot({path:path.join(report,'updated.png'),fullPage:true});
  await close();
  evidence.cases.S2_restore=JSON.parse(helper('restore'));
  fs.renameSync(data,path.join(root,'healthy-data'));
  fs.renameSync(path.join(root,'restored'),data);
  assert(!fs.existsSync(path.join(data,'exports/example.mp4')));
  // Recursos excluídos do backup continuam preservados separadamente.
  for(const file of ['fixture.txt','exports/example.mp4','installer-fixture-hashes.json','updater/preferences.json']) {
    fs.mkdirSync(path.dirname(path.join(data,file)),{recursive:true});fs.copyFileSync(path.join(root,'healthy-data',file),path.join(data,file));
  }
  for(const file of ['setup.exe','setup.exe.sig']) {
    const relative=`updater/installers/0.1.1/${file}`;
    fs.mkdirSync(path.dirname(path.join(data,relative)),{recursive:true});
    fs.copyFileSync(path.join(root,'healthy-data',relative),path.join(data,relative));
  }
  await start();assert.equal((await invoke('update_get_state')).current_version,'0.1.1');assert.equal((await api('/v1/update/health')).ok,true);helper('verify');await close();
  evidence.cases.S2_restore.installed_backend_healthy=true;
  // Falha real de saúde: cópia sintética com schema futuro, sem tocar no banco preservado.
  fs.renameSync(data,path.join(root,'restored-healthy-data'));
  fs.mkdirSync(path.join(data,'updater'),{recursive:true});
  const future=spawnSync(python,['-c',
    'import sqlite3,sys;from pathlib import Path;r=Path(sys.argv[1]);s=sqlite3.connect(r/"restored-healthy-data/horus.sqlite3");d=sqlite3.connect(r/"data/horus.sqlite3");s.backup(d);v=d.execute("PRAGMA user_version").fetchone()[0];d.execute(f"PRAGMA user_version={v+1}");d.commit();s.close();d.close()',root],{encoding:'utf8',windowsHide:true});
  assert.equal(future.status,0,future.stderr);
  fs.writeFileSync(path.join(data,'updater/preferences.json'),'{"auto_check":false}');
  fs.writeFileSync(path.join(data,'updater/pending-health.json'),'{"target_version":"0.1.1"}');
  fs.writeFileSync(path.join(root,'watch-scenario.txt'),'health_failure_expected');
  await start(false);
  state=await until(async()=>{const s=await invoke('update_get_state');return s.phase==='error'?s:null;},'Falha de saúde não foi registrada');
  assert.equal(state.error.code,'install_failed');
  const failedReceipt=JSON.parse(fs.readFileSync(path.join(data,'updater/last-update-result.json')));
  assert.equal(failedReceipt.healthy,false);assert(fs.existsSync(path.join(data,'updater/pending-health.json')));
  evidence.cases.S6_post_health_failure={passed:true,state,receipt:failedReceipt};await close();
  fs.writeFileSync(path.join(root,'watch-scenario.txt'),'normal');
  fs.renameSync(data,path.join(root,'future-health-data'));
  fs.renameSync(path.join(root,'restored-healthy-data'),data);
  // Recuperação manual explícita, usando o instalador N guardado antes do ensaio.
  await executable(path.join(channel,'0.1.0/setup.exe'),['/S',`/D=${install}`]);await start();
  state=await invoke('update_get_state');assert.equal(state.current_version,'0.1.0');
  evidence.cases.rollback={passed:true,state,data:JSON.parse(helper('verify'))};await close();
  fs.writeFileSync(path.join(root,'watch-scenario.txt'),'future_schema_expected');
  const refused=spawnSync(python,[path.join(repo,'scripts/check-installer-data.py'),data,'future','--executable',path.join(install,'backend/horus-backend.exe')],{encoding:'utf8',windowsHide:true});
  assert.equal(refused.status,0,refused.stderr);evidence.cases.S3_future_schema=JSON.parse(refused.stdout);
  fs.writeFileSync(path.join(root,'watch-scenario.txt'),'normal');
  evidence.success=true;
} catch(error) {
  evidence.success=false;evidence.error=error.stack;console.error(error.message);process.exitCode=1;
} finally {
  if(releaseManifest)releaseManifest();if(releaseArtifact)releaseArtifact();
  if(writer){fs.writeFileSync(path.join(root,'stop-writer'),'');await Promise.race([new Promise(resolve=>writer.once('exit',resolve)),delay(5000)]);}
  try {helper('stop');}catch(error){evidence.cleanup_error=error.message;evidence.success=false;process.exitCode=1;}
  if(browser)await browser.close().catch(()=>{});
  server.close();fs.writeFileSync(path.join(root,'stop-watch'),'');
  await Promise.race([new Promise(resolve=>watcher.once('exit',resolve)),delay(5000)]);
  if(fs.existsSync(path.join(root,'process-evidence.json')))evidence.processes=JSON.parse(fs.readFileSync(path.join(root,'process-evidence.json')));
  if(evidence.success){try{assert(evidence.processes.some(p=>p.kind==='nsis_update'&&p.command.includes('/UPDATE')),'NSIS /UPDATE não observado');assert(evidence.processes.filter(p=>p.kind==='backend_exit'&&!p.scenario.endsWith('_expected')).every(p=>p.code===0),'Backend encerrou anormalmente');}catch(error){evidence.success=false;evidence.error=error.stack;process.exitCode=1;}}
  save();console.log(JSON.stringify({success:evidence.success,cases:Object.keys(evidence.cases),report}));
}
