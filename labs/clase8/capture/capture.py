"""Live capture only. No Kubernetes, builds, application test suites or GCP."""
from pathlib import Path
import base64,datetime,hashlib,json,os,secrets,subprocess,sys,time,signal,shutil
from urllib.request import Request,urlopen
from urllib.parse import urlencode
ROOT=Path.cwd();E=ROOT/'evidence-grafana';E.mkdir(exist_ok=True)
SOURCE=ROOT/'source';SHA='d4b159c57e208a8a433ff6ac97b3b94098518aa0'
RID=os.environ.get('GITHUB_RUN_ID','unauthorized')
PREFIX='unlu-lab8-capture-'+RID
NAMES=[PREFIX+'-prometheus',PREFIX+'-grafana']
TEMP=Path(os.environ.get('RUNNER_TEMP','/tmp'))/PREFIX
PROCS=[];HANDLES=[]
def save(name,data):
    (E/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def cmd(args,timeout=60,check=True):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if check and p.returncode:raise RuntimeError('Command failed: '+args[0]+' '+p.stderr[:1000])
    return p
def http(url,data=None,auth=None):
    headers={'Content-Type':'application/json'}
    if auth:headers['Authorization']='Basic '+base64.b64encode(auth.encode()).decode()
    with urlopen(Request(url,data=None if data is None else json.dumps(data).encode(),headers=headers),timeout=10) as r:return json.load(r)
def ready(url,auth=None):
    for _ in range(60):
        try:return http(url,auth=auth)
        except Exception:time.sleep(2)
    raise RuntimeError('HTTP readiness timeout '+url)
def guard():
    assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
    assert os.environ.get('GITHUB_REPOSITORY')=='silvitagomez572-alt/unlu-lab4-infra-cloud-template'
    assert os.environ.get('GITHUB_REF')=='refs/heads/clase8/captura-grafana'
def cleanup():
    guard()
    for p in PROCS:
        if p.poll() is None:p.terminate()
    for p in PROCS:
        try:p.wait(timeout=10)
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    for h in HANDLES:h.close()
    other_owned=[]
    pidfile=TEMP/'owned-pids.json'
    if not PROCS and pidfile.exists():
        for item in json.loads(pidfile.read_text()):
            pid=item['pid'];proc=Path('/proc')/str(pid)
            if not proc.exists():other_owned.append({'pid':pid,'status':'already absent'});continue
            argv=(proc/'cmdline').read_bytes().split(b'\0')
            assert (proc/'cwd').resolve()==SOURCE/'pow'
            assert b'uvicorn' in argv and str(item['port']).encode() in argv
            os.kill(pid,signal.SIGTERM)
            for _ in range(20):
                if not proc.exists():break
                time.sleep(.2)
            if proc.exists():
                # Recheck identity before terminating an owned process that ignored SIGTERM.
                assert (proc/'cmdline').read_bytes().split(b'\0')==argv
                os.kill(pid,signal.SIGKILL)
            other_owned.append({'pid':pid,'status':'terminated verified owned process'})
    records=[]
    for name in NAMES:
        found=cmd(['docker','ps','-a','--filter','name=^/'+name+'$','--format','{{.Names}}']).stdout.strip()
        if found:
            assert found==name
            stop=cmd(['docker','stop','--time','10',name],timeout=30,check=False)
            records.append({'container':name,'stop_exit':stop.returncode})
    running=cmd(['docker','ps','--filter','name='+PREFIX+'-','--format','{{.Names}}']).stdout.strip()
    prior=E/'APAGADO_CAPTURA.json'
    previous=json.loads(prior.read_text()) if prior.exists() else {}
    record={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'only_owned_names':NAMES,'stop_results':previous.get('stop_results',[])+records,'running_after':running,'processes':previous.get('processes',[])+[{'pid':p.pid,'exit':p.poll()} for p in PROCS]+other_owned,'result':'PASS' if not running and all(p.poll() is not None for p in PROCS) else 'FAIL'}
    save('APAGADO_CAPTURA.json',record)
    files=[p for p in E.rglob('*') if p.is_file() and p.name!='SHA256.json']
    assert sum(p.stat().st_size for p in files)<2*1024**2,'Evidence exceeds 2 MiB; no upload'
    save('SHA256.json',{'files':[{'path':p.relative_to(E).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)]})
    if 'GITHUB_OUTPUT' in os.environ:
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('upload_allowed=true\n')
    assert record['result']=='PASS'
def run():
    guard();TEMP.mkdir(mode=0o700)
    assert cmd(['git','-C',str(SOURCE),'rev-parse','HEAD']).stdout.strip()==SHA
    memory=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
    disk=shutil.disk_usage(TEMP).free
    save('capacity.json',{'available_bytes':memory,'free_disk_bytes':disk})
    assert memory>=3*1024**3 and disk>=3*1024**3,'Insufficient capacity; no processes started'
    env_keys=['LANG','LANGUAGE','LC_ALL','LC_CTYPE','LC_MESSAGES']
    save('runner-locale.json',{'environment':{k:os.environ.get(k) for k in env_keys},'locale':cmd(['locale'],check=False).stdout,'source_sha':SHA})
    baseline=json.loads((ROOT/'labs/clase8/capture/state-baseline.json').read_text())
    owned=[]
    for i,item in enumerate(baseline['nodes']):
        assert item['pod']=='pow-'+str(i)
        raw=json.dumps(item['chain'],allow_nan=False).encode()
        assert hashlib.sha256(raw).hexdigest()==item['sha256']
        folder=TEMP/f'pow-{i}';folder.mkdir();(folder/'chain.json').write_bytes(raw)
        env=dict(os.environ,DATA_DIR=str(folder),PEERS=','.join('http://127.0.0.1:'+str(18090+k) for k in range(3)))
        log=(E/f'pow-{i}.log').open('w');HANDLES.append(log)
        p=subprocess.Popen([sys.executable,'-m','uvicorn','node:app','--host','127.0.0.1','--port',str(18090+i),'--workers','1'],cwd=SOURCE/'pow',env=env,stdout=log,stderr=log)
        PROCS.append(p);owned.append({'pid':p.pid,'port':18090+i})
        (TEMP/'owned-pids.json').write_text(json.dumps(owned))
        ready(f'http://127.0.0.1:{18090+i}/healthz')
        save(f'chain-before-{i}.json',http(f'http://127.0.0.1:{18090+i}/chain'))
    promcfg={'global':{'scrape_interval':'5s'},'scrape_configs':[{'job_name':'pow-live','static_configs':[{'targets':['127.0.0.1:'+str(18090+i)],'labels':{'pod':'pow-'+str(i),'namespace':'bc-clase8','environment':'standalone-capture'}} for i in range(3)]}]}
    config=TEMP/'prometheus.yml';config.write_text(json.dumps(promcfg));config.chmod(0o644)
    promimage='quay.io/prometheus/prometheus:v3.15.0-distroless';grafimage='grafana/grafana:13.2.3-distroless'
    # Same pinned versions as the prior chart; record the registry digests actually pulled.
    for label,image in [('prometheus',promimage),('grafana',grafimage)]:
        cmd(['docker','pull',image],timeout=150)
        info=json.loads(cmd(['docker','image','inspect',image]).stdout)[0]
        save(label+'-image.json',{k:info.get(k) for k in ['Id','RepoDigests','Os','Architecture']})
    cmd(['docker','run','-d','--name',NAMES[0],'--network','host','--memory','512m','--cpus','1','--mount','type=bind,src='+str(config)+',dst=/etc/prometheus/prometheus.yml,readonly',promimage,'--config.file=/etc/prometheus/prometheus.yml','--web.listen-address=127.0.0.1:19090','--storage.tsdb.retention.time=1h'])
    password=secrets.token_urlsafe(24);secret=TEMP/'grafana-password';secret.write_text(password);secret.chmod(0o444)
    # Parent is mode 0700. Only the explicit read-only mount exposes this file to Grafana.
    cmd(['docker','run','-d','--name',NAMES[1],'--network','host','--memory','512m','--cpus','1','--env','GF_SERVER_HTTP_ADDR=127.0.0.1','--env','GF_SERVER_HTTP_PORT=13000','--env','GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/admin-password','--env','GF_PLUGINS_PREINSTALL_DISABLED=true','--mount','type=bind,src='+str(secret)+',dst=/run/secrets/admin-password,readonly',grafimage])
    prom='http://127.0.0.1:19090';graf='http://127.0.0.1:13000';auth='admin:'+password
    ready(prom+'/api/v1/status/buildinfo')
    ready(graf+'/api/health')
    for _ in range(30):
        targets=http(prom+'/api/v1/targets')['data']['activeTargets']
        if len(targets)==3 and all(x['health']=='up' for x in targets):break
        time.sleep(2)
    else:raise RuntimeError('Three targets not UP')
    save('prometheus-targets.json',targets)
    http(graf+'/api/datasources',{'name':'Prometheus','type':'prometheus','uid':'prometheus','url':prom,'access':'proxy','isDefault':True},auth)
    dash=json.loads((SOURCE/'labs/clase8/dashboard-pow.json').read_text())
    dash['panels'][0]['datasource']={'type':'prometheus','uid':'prometheus'}
    dash['panels'][0]['gridPos']={'x':0,'y':0,'w':24,'h':12}
    dash['panels'][1]['gridPos']={'x':0,'y':12,'w':24,'h':5}
    dash['panels'][1]['title']='Entorno de captura y procedencia'
    dash['panels'][1]['options']['content']='Run '+RID+' | 3 procesos PoW reales; NO Kubernetes. Estado recuperado del run 41532100541. Prometheus consulta /metrics en vivo. Fuente '+SHA
    imported=http(graf+'/api/dashboards/db',{'dashboard':dash,'overwrite':False},auth);assert imported['status']=='success'
    save('grafana-import.json',imported)
    expr='pow_block_height{namespace="bc-clase8"}';start=int(time.time())-10;samples=[]
    for i in range(3):
        samples.append(http(prom+'/api/v1/query?'+urlencode({'query':expr})))
        save('mine-'+str(i)+'.json',http('http://127.0.0.1:18090/mine',{}));time.sleep(15)
    samples.append(http(prom+'/api/v1/query?'+urlencode({'query':expr})));end=int(time.time())
    heights=[next(float(v['value'][1]) for v in q['data']['result'] if v['metric']['pod']=='pow-0') for q in samples]
    assert heights==[4,5,6,7],heights
    save('height-samples.json',{'heights':heights,'raw':samples})
    save('height-range.json',http(prom+'/api/v1/query_range?'+urlencode({'query':expr,'start':start,'end':end,'step':5})))
    save('grafana-dashboard-export.json',http(graf+'/api/dashboards/uid/unlu-lab8-pow',auth=auth))
    from playwright.sync_api import sync_playwright
    diagnostics={'system_locale':{k:os.environ.get(k) for k in env_keys},'errors':[]}
    probe="""() => {const r={language:navigator.language,languages:navigator.languages};try {r.formatted=new Intl.NumberFormat(navigator.language).format(1234.5)}catch(e){r.error=String(e)}return r}"""
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        default=browser.new_context();page0=default.new_page();diagnostics['default_context']=page0.evaluate(probe);default.close()
        context=browser.new_context(viewport={'width':1600,'height':1100},locale='en-US')
        page=context.new_page();diagnostics['corrected_context']=page.evaluate(probe)
        assert diagnostics['corrected_context']['language']=='en-US' and 'error' not in diagnostics['corrected_context']
        page.on('console',lambda m: diagnostics['errors'].append(m.text.replace(password,'[REDACTED]')[:1500]) if m.type=='error' else None)
        try:
            login=context.request.post(graf+'/login',data={'user':'admin','password':password});assert login.ok
            identity=context.request.get(graf+'/api/user');assert identity.ok and identity.json().get('login')=='admin'
            diagnostics['session_verified']=True
            response=page.goto(graf+'/d/unlu-lab8-pow?from='+str(start*1000)+'&to='+str(end*1000),wait_until='networkidle',timeout=30000)
            diagnostics['dashboard_http']=response.status;diagnostics['url']=page.url
            page.get_by_text('Block height per node',exact=True).first.wait_for(timeout=30000)
            for i in range(3):page.get_by_text('pow-'+str(i),exact=True).first.wait_for(state='visible',timeout=15000)
            page.wait_for_timeout(3000);page.screenshot(path=str(E/'CAPTURA_GRAFANA_LAB8.png'),full_page=True)
            diagnostics['capture_saved']=True
        except Exception:
            page.screenshot(path=str(E/'GRAFANA_ERROR_ORIGINAL.png'),full_page=True);raise
        finally:
            save('browser-locale.json',diagnostics);context.close();browser.close()
    save('RESULTADO_CAPTURA.json',{'result':'Browser and metrics assertions PASS; PNG needs visual review','run':RID,'kubernetes_recreated':False,'pow_source':SHA,'heights':heights})
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='cleanup':cleanup()
    else:
        try:run()
        finally:
            for i in range(3):
                p=TEMP/f'pow-{i}'/'chain.json'
                if p.exists():(E/f'chain-final-{i}.json').write_bytes(p.read_bytes())
            cleanup()
