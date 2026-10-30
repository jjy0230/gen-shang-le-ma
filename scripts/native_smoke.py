"""Fresh native install, real HTTP/SSE, restart and backup/restore smoke test."""
import argparse,json,os,pathlib,socket,sqlite3,subprocess,sys,tempfile,time,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main(output):
    with tempfile.TemporaryDirectory(prefix='pulse-native-') as tmp:
        tmp=pathlib.Path(tmp);venv=tmp/'venv'
        subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
        python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        install=subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt'),'-c',str(ROOT/'requirements-lock.txt')],capture_output=True,text=True)
        if install.returncode:raise RuntimeError('Fresh install failed: '+install.stderr[-2000:])
        database=tmp/'workshop.sqlite3';env={**os.environ,'DATABASE_PATH':str(database)}
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        def call(path,body=None,token=''):
            req=urllib.request.Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':token,'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
        def start():
            p=subprocess.Popen([str(python),'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--log-level','error'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for _ in range(80):
                try:
                    if call('/health')['ok']:return p
                except Exception:pass
                time.sleep(.25)
            p.terminate();p.wait();raise RuntimeError('Native server did not start')
        def stop(p):p.terminate();p.wait(timeout=15)
        p=start()
        try:
            room=call('/api/rooms',{'title':'Native verification'});base='/api/rooms/'+room['code'];host=room['token']
            person=call(base+'/join',{'name':'Synthetic device'})['token']
            call(base+'/steps',{'title':'Smoke checkpoint'},host)
            view=call(base,token=host);sid=view['current']
            call(base+'/status',{'step':sid,'state':'done'},person)
            req=urllib.request.Request(origin+base+'/events',headers={'Authorization':host})
            with urllib.request.urlopen(req,timeout=10) as r:
                line=r.readline().decode();assert line.startswith('data: ')
                assert json.loads(line[6:])['steps'][0]['counts']['done']==1
        finally:stop(p)
        p=start()
        try:assert call(base,token=host)['steps'][0]['counts']['done']==1
        finally:stop(p)
        backup=tmp/'backup.sqlite3'
        with sqlite3.connect(database) as src,sqlite3.connect(backup) as dest:src.backup(dest)
        env['DATABASE_PATH']=str(backup);p=start()
        try:
            assert call(base,token=host)['steps'][0]['counts']['done']==1
            assert call(base,token=person)['steps'][0]['mine']=='done'
            bad=urllib.request.Request(origin+base,headers={'Authorization':'not-a-valid-token'})
            try:urllib.request.urlopen(bad,timeout=5);raise AssertionError('Invalid credentials accepted')
            except urllib.error.HTTPError as exc:assert exc.code==403
        finally:stop(p)
        report={'fresh_venv_install':True,'http_join_status':True,'sse_observed_done':True,'restart_persisted':True,'backup_restore_persisted':True,'roles_preserved':True,'invalid_token_rejected':True,'platform':sys.platform,'python':sys.version.split()[0],'limits':'Loopback synthetic client. Physical phone and public-network access not tested.'}
        pathlib.Path(output).parent.mkdir(parents=True,exist_ok=True);pathlib.Path(output).write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='data/native-smoke.json');main(parser.parse_args().output)
