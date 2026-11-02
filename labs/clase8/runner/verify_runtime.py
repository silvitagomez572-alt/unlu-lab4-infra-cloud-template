"""Proposed integration evidence, restricted to a new GitHub-hosted runner.
No local invocation, GCP operation, wallet, consensus or approved unit-suite rerun.
"""
import base64,datetime,hashlib,json,os,secrets,subprocess,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

assert os.environ.get('GITHUB_ACTIONS')=='true', 'GitHub Actions only'
assert os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted', 'Never use a self-hosted runner'
assert os.environ.get('GITHUB_REPOSITORY')=='silvitagomez572-alt/unlu-lab4-infra-cloud-template'
assert os.environ.get('GITHUB_REF')=='refs/heads/clase8/validacion-aislada'
assert os.environ.get('LAB8_AUTHORIZED_RUN')=='1'
ROOT=Path.cwd(); E=ROOT/'evidence'; E.mkdir(exist_ok=True)
CTX='k3d-unlu-lab8-ci'; NS='bc-clase8'; SOURCE=ROOT/'source'
EXPECTED='d4b159c57e208a8a433ff6ac97b3b94098518aa0'
K=['kubectl','--context',CTX,'--request-timeout=15s']

def write(name,data):
    (E/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def run(name,args,timeout=120,input_text=None,check=True):
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    p=subprocess.run(args,input=input_text,text=True,capture_output=True,timeout=timeout)
    row={'utc':started,'command':args,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    # Secret manifest is passed on stdin and is never logged.
    write(name+'.json',row)
    print(name+': exit '+str(p.returncode),flush=True)
    if check and p.returncode:raise RuntimeError(name+' failed; see preserved output')
    return p.stdout

def obj(name,args):return json.loads(run(name,args))
def kube(name,args,ns=NS,**kw):return run(name,K+['-n',ns]+args,**kw)
def kobj(name,args,ns=NS):return json.loads(kube(name,args,ns))

HTTP_CODE='''import json,sys,urllib.request,urllib.error
req=urllib.request.Request('http://127.0.0.1:8090'+sys.argv[2],data=sys.argv[3].encode() if sys.argv[3] else None,headers={'Content-Type':'application/json'},method=sys.argv[1])
try:
 r=urllib.request.urlopen(req,timeout=30); code=r.status; body=r.read().decode()
except urllib.error.HTTPError as e:
 code=e.code; body=e.read().decode()
print(json.dumps({'status':code,'body':body}))
'''
def endpoint(label,pod,path,method='GET',body=None,status=200):
    data=json.dumps(body) if body is not None else ''
    answer=json.loads(kube(label,['exec',pod,'--','python','-c',HTTP_CODE,method,path,data],timeout=45))
    write(label+'-http.json',answer)
    assert answer['status']==status,(label,answer['status'])
    try:return json.loads(answer['body'])
    except ValueError:return answer['body']

def chart(stage,n):
    args=['helm','upgrade','--install','pow',str(SOURCE/'helm/charts/pow'),'--kube-context',CTX,'-n',NS,
          '-f',str(SOURCE/'helm/charts/pow/values-k3s.yaml'),'-f',str(SOURCE/'labs/clase8/values-local.yaml'),
          '--set',f'replicaCount={n}','--wait','--timeout','180s']
    run(stage+'-helm',args,timeout=210)
    kube(stage+'-rollout',['rollout','status','statefulset/pow','--timeout=120s'],timeout=140)
    pods=kobj(stage+'-pods',['get','pods','-l','app=pow','-o','json'])['items']
    assert len(pods)==n
    for p in pods:assert all(c['ready'] for c in p['status']['containerStatuses'])
    pvcs=kobj(stage+'-pvcs',['get','pvc','-o','json'])['items']
    assert len(pvcs)==n and all(p['status']['phase']=='Bound' for p in pvcs)
    return pods,pvcs

def runtime():
    assert run('source-commit',['git','-C',str(SOURCE),'rev-parse','HEAD']).strip()==EXPECTED
    run('versions-k3d',['k3d','version']);run('versions-helm',['helm','version','--short'])
    # Refuse to overwrite a previous namespace, even in the disposable runner.
    existing=json.loads(run('initial-namespaces',K+['get','namespaces','-o','json']))
    assert NS not in {x['metadata']['name'] for x in existing['items']}
    run('namespace',K+['apply','-f',str(SOURCE/'labs/clase8/namespace.yaml')])
    pods,pvcs=chart('one',1)
    old_pod=pods[0]['metadata']['uid'];old_pvc=pvcs[0]['metadata']['uid']
    assert endpoint('initial-health','pow-0','/healthz')['status']=='ok'
    chain0=endpoint('initial-chain','pow-0','/chain');assert len(chain0)==1
    endpoint('initial-metrics','pow-0','/metrics')
    endpoint('synthetic-tx','pow-0','/tx','POST',{'sender':'lab8-synthetic-A','to':'lab8-synthetic-B','amount':1},201)
    mined=endpoint('first-mine','pow-0','/mine','POST',{});assert mined['elapsed_seconds']>=0
    before=endpoint('chain-before-recreation','pow-0','/chain');assert len(before)==2
    # This exact recreation is included in the requested authorization scope.
    kube('recreate-pow-0',['delete','pod','pow-0','--wait=true','--timeout=60s'],timeout=80)
    for attempt in range(60):
        state=kobj('recreation-state-'+str(attempt),['get','pods','-l','app=pow','-o','json'])['items']
        if state and state[0]['metadata']['uid']!=old_pod and all(c.get('ready') for c in state[0].get('status',{}).get('containerStatuses',[])) and state[0].get('status',{}).get('containerStatuses'):break
        time.sleep(2)
    else:raise RuntimeError('Recreated pod did not become Ready')
    after=endpoint('chain-after-recreation','pow-0','/chain');assert after==before
    pvc=kobj('pvc-after-recreation',['get','pvc','data-pow-0','-o','json'])
    assert pvc['metadata']['uid']==old_pvc and pvc['status']['phase']=='Bound'
    write('persistence-result.json',{'result':'PASS','old_pod_uid':old_pod,'new_pod_uid':state[0]['metadata']['uid'],'preserved_pvc_uid':old_pvc,'height':after[-1]['index'],'tip_hash':after[-1]['hash']})
    chart('two',2)
    assert endpoint('two-chain-0','pow-0','/chain')==before
    assert len(endpoint('two-chain-1','pow-1','/chain'))==1
    chart('three',3)
    assert endpoint('three-chain-0','pow-0','/chain')==before
    for i in (1,2):
        assert len(endpoint('three-initial-'+str(i),f'pow-{i}','/chain'))==1
        endpoint('tx-'+str(i),f'pow-{i}','/tx','POST',{'sender':f'synthetic-node-{i}','to':'synthetic-lab8','amount':i},201)
        endpoint('mine-'+str(i),f'pow-{i}','/mine','POST',{})
    tips=[]
    for i in range(3):
        p=f'pow-{i}'
        ch=endpoint('independent-chain-'+str(i),p,'/chain');tips.append(ch[-1]['hash'])
        peer=kube('peers-'+str(i),['exec',p,'--','printenv','PEERS']).strip()
        assert peer==','.join(f'http://pow-{j}.pow-headless:8090' for j in range(3))
        endpoint('sync-404-'+str(i),p,'/peers/sync','POST',{},404)
    assert len(set(tips))==3
    write('independence-result.json',{'result':'PASS','tips':tips,'synchronization':'NOT_IMPLEMENTED; 404 verified','source_commit':EXPECTED})

def http(url,data=None,auth=None):
    headers={'Content-Type':'application/json'}
    if auth:headers['Authorization']='Basic '+base64.b64encode(auth.encode()).decode()
    with urlopen(Request(url,data=None if data is None else json.dumps(data).encode(),headers=headers),timeout=15) as r:return json.load(r)

def wait_http(url,auth=None):
    for _ in range(45):
        try:return http(url,auth=auth)
        except (URLError,TimeoutError):time.sleep(2)
    raise RuntimeError('HTTP service unavailable: '+url)

def observability():
    run('monitor-namespace',K+['create','namespace','observability'])
    password=secrets.token_urlsafe(24)
    secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':'lab8-grafana-auth','namespace':'observability'},'type':'Opaque','stringData':{'admin-user':'admin','admin-password':password}}
    run('temporary-grafana-auth',K+['apply','-f','-'],input_text=json.dumps(secret))
    run('helm-repo',['helm','repo','add','prometheus-community','https://prometheus-community.github.io/helm-charts'])
    run('helm-repo-update',['helm','repo','update'],timeout=120)
    run('monitor-install',['helm','upgrade','--install','kube-prom','prometheus-community/kube-prometheus-stack','--version','91.9.0','--kube-context',CTX,'-n','observability','-f',str(SOURCE/'observability/kube-prometheus-stack-values.yaml'),'-f',str(ROOT/'labs/clase8/runner/observability-values.yaml'),'--wait','--timeout','420s'],timeout=450)
    run('service-monitor',K+['apply','-f',str(SOURCE/'labs/clase8/servicemonitor.yaml')])
    forwards=[];handles=[]
    try:
        for name,service,ports in [('prometheus','kube-prom-kube-prometheus-prometheus','19090:9090'),('grafana','kube-prom-grafana','13000:80')]:
            handle=(E/(name+'-forward.log')).open('w');handles.append(handle)
            forwards.append(subprocess.Popen(K+['-n','observability','port-forward','--address=127.0.0.1','service/'+service,ports],stdout=handle,stderr=handle))
        prom='http://127.0.0.1:19090';graf='http://127.0.0.1:13000';auth='admin:'+password
        wait_http(prom+'/api/v1/status/buildinfo');wait_http(graf+'/api/health')
        for _ in range(60):
            targets=http(prom+'/api/v1/targets')
            selected=[t for t in targets['data']['activeTargets'] if t['labels'].get('namespace')==NS and t['labels'].get('service')=='pow']
            if len(selected)==3 and all(t['health']=='up' for t in selected):break
            time.sleep(3)
        else:raise RuntimeError('Not all three PoW targets are UP')
        write('prometheus-targets.json',targets)
        from urllib.parse import urlencode
        expr='pow_block_height{namespace="bc-clase8"}'
        start=int(time.time())-30
        samples=[]
        for i in range(3):
            samples.append(http(prom+'/api/v1/query?'+urlencode({'query':expr})))
            endpoint('observed-mine-'+str(i),'pow-0','/mine','POST',{})
            time.sleep(20)
        samples.append(http(prom+'/api/v1/query?'+urlencode({'query':expr})))
        heights=[]
        for s in samples:
            values=[float(x['value'][1]) for x in s['data']['result'] if x['metric'].get('pod')=='pow-0']
            assert len(values)==1;heights.append(values[0])
        assert all(b>a for a,b in zip(heights,heights[1:])),heights
        write('prometheus-height-samples.json',{'result':'PASS','heights':heights,'raw':samples})
        datasource=next(x for x in http(graf+'/api/datasources',auth=auth) if x['type']=='prometheus')
        dashboard=json.loads((SOURCE/'labs/clase8/dashboard-pow.json').read_text())
        dashboard['panels'][0]['datasource']={'type':'prometheus','uid':datasource['uid']}
        dashboard['panels'][0]['gridPos']={'x':0,'y':0,'w':24,'h':11}
        dashboard['panels'][1]['gridPos']={'x':0,'y':11,'w':24,'h':5}
        dashboard['panels'][1]['title']='Entorno verificado de Lab 8'
        dashboard['panels'][1]['options']['content']='Run '+os.environ['GITHUB_RUN_ID']+' | unlu-lab8-ci | bc-clase8 | source '+EXPECTED+' | Independent chains, no synchronization.'
        imported=http(graf+'/api/dashboards/db',{'dashboard':dashboard,'overwrite':False},auth)
        assert imported['status']=='success';write('grafana-import.json',imported)
        end=int(time.time())+15
        write('grafana-dashboard-export.json',http(graf+'/api/dashboards/uid/unlu-lab8-pow',auth=auth))
        write('prometheus-height-range.json',http(prom+'/api/v1/query_range?'+urlencode({'query':expr,'start':start,'end':end,'step':15})))
        from playwright.sync_api import sync_playwright
        browser_diagnostics={'requested_url':graf+'/login','console':[],'page_errors':[],'responses':[]}
        def scrub(value):
            import re
            value=str(value).replace(password,'[REDACTED]')
            return re.sub(r'(?:ya29[.][A-Za-z0-9_-]+|eyJ[A-Za-z0-9_-]+[.][A-Za-z0-9_-]+[.][A-Za-z0-9_-]+)','[REDACTED]',value)[:1500]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1600,'height':1100})
            page=context.new_page()
            page.on('console',lambda m: browser_diagnostics['console'].append({'type':m.type,'text':scrub(m.text)}))
            page.on('pageerror',lambda e: browser_diagnostics['page_errors'].append(scrub(e)))
            page.on('response',lambda r: browser_diagnostics['responses'].append({'url':r.url.split('?')[0],'status':r.status}) if r.url.startswith(graf) else None)
            try:
                response=page.goto(graf+'/login',wait_until='networkidle',timeout=30000)
                browser_diagnostics['login']={'status':response.status if response else None,'final_url':page.url.split('?')[0]}
                browser_diagnostics['form']=page.evaluate("""() => ({title:document.title,readyState:document.readyState,inputs:[...document.querySelectorAll('input')].map(e=>({type:e.type,name:e.name,placeholder:e.placeholder,visible:!!e.getClientRects().length}))})""")
                page.screenshot(path=str(E/'GRAFANA_LOGIN_DIAGNOSTICO.png'),full_page=True)
                # Session cookie stays only in this ephemeral browser context; no secret output.
                login=context.request.post(graf+'/login',data={'user':'admin','password':password},timeout=15000)
                browser_diagnostics['session_login_status']=login.status
                assert login.ok,'Grafana session login failed'
                identity=context.request.get(graf+'/api/user',timeout=15000)
                assert identity.ok and identity.json().get('login')=='admin','Authenticated Grafana identity not verified'
                browser_diagnostics['session_verified']=True
                response=page.goto(graf+'/d/unlu-lab8-pow?from='+str(start*1000)+'&to='+str(end*1000),wait_until='networkidle',timeout=30000)
                browser_diagnostics['dashboard']={'status':response.status if response else None,'url':page.url}
                page.get_by_text('Block height per node',exact=True).first.wait_for(timeout=30000)
                for pod in ['pow-0','pow-1','pow-2']:page.get_by_text(pod,exact=True).first.wait_for(state='visible',timeout=20000)
                page.wait_for_timeout(3000)
                page.screenshot(path=str(E/'CAPTURA_GRAFANA_LAB8.png'),full_page=True)
                browser_diagnostics['capture_saved']=True
            except Exception as exc:
                browser_diagnostics['error']=scrub(exc)
                page.screenshot(path=str(E/'GRAFANA_FALLO_ORIGINAL.png'),full_page=True,timeout=10000)
                raise
            finally:
                write('grafana-browser-diagnostics.json',browser_diagnostics)
                context.close();browser.close()
        write('observability-result.json',{'result':'PASS for metric and browser assertions; original PNG still requires visual review','height_samples':heights,'dashboard_uid':'unlu-lab8-pow','duration_metric':'elapsed_seconds is not a Prometheus histogram'})
    finally:
        for proc in forwards:proc.terminate()
        for proc in forwards:proc.wait(timeout=10)
        for handle in handles:handle.close()

def collect():
    for label,args in [('final-pods',['get','pods','-A','-o','wide']),('final-pvc',['get','pvc','-A','-o','wide']),('final-events',['get','events','-A','--sort-by=.lastTimestamp'])]:
        run(label,K+args,check=False)
    for i in range(3):kube('pow-log-'+str(i),['logs',f'pow-{i}','--tail=150'],check=False)
    snapshots=[]
    for i in range(3):
        result=run('state-snapshot-'+str(i),K+['-n',NS,'exec',f'pow-{i}','--','python','-c',"from pathlib import Path; import hashlib,json; p=Path('/data/chain.json'); b=p.read_bytes(); assert len(b)<1048576; print(json.dumps({'path':'/data/chain.json','bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'chain':json.loads(b)}))"],check=False)
        try:
            value=json.loads(result);assert value['chain'];snapshots.append({'pod':f'pow-{i}','sha256':value['sha256'],'height':value['chain'][-1]['index']})
        except (ValueError,KeyError,AssertionError):pass
    write('state-preservation.json',{'result':'PASS' if len(snapshots)==3 else 'PARTIAL','snapshots':snapshots,'note':'chain.json copied before cluster stop; baseline pending transactions are in memory, not persistent.'})
    paths=[p for p in E.rglob('*') if p.is_file() and p.name!='SHA256.json']
    assert sum(p.stat().st_size for p in paths)<19*1024**2,'Artifact exceeds safety bound; review before upload'
    write('SHA256.json',{'files':[{'path':p.relative_to(E).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(paths)],'note':'Hash integrity only; inspect each result before assigning academic PASS.'})
    assert sum(p.stat().st_size for p in E.rglob('*') if p.is_file())<19*1024**2
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('upload_allowed=true\n')


def stop_created_cluster():
    assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
    C=ROOT/'cleanup-evidence';C.mkdir(exist_ok=True)
    before=subprocess.run(['docker','ps','--filter','name=k3d-unlu-lab8-ci-','--format','{{.Names}}'],capture_output=True,text=True,timeout=15)
    stopped=subprocess.run(['k3d','cluster','stop','unlu-lab8-ci'],capture_output=True,text=True,timeout=90)
    after=subprocess.run(['docker','ps','--filter','name=k3d-unlu-lab8-ci-','--format','{{.Names}}'],capture_output=True,text=True,timeout=15)
    record={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cluster':'unlu-lab8-ci','action':'stop only; no explicit PVC deletion','before':before.stdout,'stop_exit_code':stopped.returncode,'stdout':stopped.stdout,'stderr':stopped.stderr,'running_after':after.stdout,'result':'PASS' if stopped.returncode==0 and after.returncode==0 and not after.stdout.strip() else 'FAIL','host_disposal':'GitHub discards this temporary hosted runner after job completion.'}
    (C/'APAGADO_VERIFICADO.json').write_text(json.dumps(record,indent=2))
    assert record['result']=='PASS','Cluster stop could not be verified'

if __name__=='__main__':
    {'runtime':runtime,'observability':observability,'collect':collect,'stop':stop_created_cluster}[sys.argv[1]]()
