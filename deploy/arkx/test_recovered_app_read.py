"""Offline tests of the actual helper; every unmocked Docker/network call refuses."""
import ast
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

LOCAL_RUN = subprocess.run
HERE = Path(__file__).resolve().parent
m = types.ModuleType('recovered_app_read')
m.__file__ = str(HERE / 'rehearse-migration.py')
exec(compile(Path(m.__file__).read_text(), m.__file__, 'exec'), m.__dict__)


def args(**kw):
    a = m.parser().parse_args(['--stage', 'app-read', '--prefix', 'arkx-migration-rehearsal-testread', '--run-id', '8'*32])
    for k,v in kw.items(): setattr(a,k,v)
    return a


def family():
    return {'target':'/var/lib/arkx-current-restore/arkx-current-restore-fresh-smoke',
            'plan_sha256':'a'*64,'result_sha256':'b'*64,'config_sha256':'c'*64,
            'count_evidence_sha256':m.APP_PINS['current-live-account-counts.json'],
            'counts':{'User':1,'Organization':1,'UserOrganization':None,'Integration':3,'Media':1,'Post':3,'queued':0,'published':3,'missingApiKeys':0},
            'api_integrations':3,'temporal_entrypoint':'/usr/local/bin/temporal-server','write_growth_budget_bytes':1024**3}


def temporal_config(plan):
    sql=lambda db:{'sql':{'connectAddr':plan['names']['pg16']+':5432','databaseName':db,'user':'clone','pluginName':'postgres12'}}
    return {'persistence':{'defaultStore':'default','visibilityStore':'es-visibility','numHistoryShards':4,
      'datastores':{'default':sql('temporal'),'visibility':sql('temporal_visibility'),
        'es-visibility':{'elasticsearch':{'version':'v7','indices':{'visibility':'temporal_visibility_v1_dev'},'url':{'scheme':'http','host':plan['names']['es']+':9200'}}}}},
      'dynamicConfigClient':{'filepath':m.APP_TEMPORAL_DYNAMIC,'pollInterval':'60s'}}


class Docker:
    """Narrow Docker protocol fixture, constructed from real generated argv, not specs."""
    def __init__(self, plan):
        self.plan=plan; self.rows={}; self.calls=[]; self.events=[]; self.net=None; self.image_env={}; self.image_labels={}; self.omit_image_labels=set()
        self.fail=None; self.namespace_ok=True; self.session_ok=True; self.raw_fixture='private-fixture-never-echo'
    def __call__(self, a, payload=None, timeout=180, allow_failure=False):
        self.calls.append((a[:],payload)); op=tuple(a[:2])
        if self.fail and op==self.fail:
            raise m.Refusal('Docker operation failed/timed out; state unknown, retain targets')
        if op==('image','inspect'):
            value={'Id':a[2],'Architecture':'amd64','Os':'linux','Config':{'Env':[k+'='+v for k,v in self.image_env.get(a[2],{'PATH':'/usr/bin:/bin'}).items()],'Volumes':{},'Labels':self.image_labels.get(a[2],{})}}
            if a[2] in self.omit_image_labels:value['Config'].pop('Labels')
            return m.app_canonical([value])
        if op==('network','ls'):return b''
        if op==('network','create'):
            self.net={'Id':'f'*64,'Internal':True,'Driver':'bridge','Labels':{},'Containers':{},'Attachable':False}
            for i,x in enumerate(a):
                if x=='--label':k,v=a[i+1].split('=',1);self.net['Labels'][k]=v
            return b'f'*64
        if op==('network','inspect'):return m.app_canonical([self.net])
        if op==('container','ls'):
            return ('\n'.join(r['Name'].lstrip('/') if a[-1]=='{{.Names}}' else k for k,r in self.rows.items())).encode()
        if op==('container','inspect'):return m.app_canonical([self.rows[a[2]]])
        if op==('container','create'):
            def value(k):
                if k in a:return a[a.index(k)+1]
                return next(x.split('=',1)[1] for x in a if x.startswith(k+'='))
            name=value('--name'); role=next(k for k,v in self.plan['names'].items() if v==name)
            self.events.append(('create',role)); target=format(len(self.rows)+1,'064x')
            labels={};tmp={};mounts=[]
            for i,k in enumerate(a):
                if k=='--label':x,y=a[i+1].split('=',1);labels[x]=y
                if k=='--tmpfs':x,y=a[i+1].split(':',1);tmp[x]=y
                if k=='--mount':
                    parts=dict(z.split('=',1) if '=' in z else (z,True) for z in a[i+1].split(','))
                    mounts.append({'Type':parts['type'],'Source':parts['src'],'Destination':parts['dst'],'RW':'readonly' not in parts})
            index=next(i for i,x in enumerate(a) if x.startswith('sha256:'))
            labels={**(self.image_labels.get(a[index]) or {}),**labels}
            env=dict(self.image_env.get(a[index],{'PATH':'/usr/bin:/bin'}))
            if '--env-file' in a:
                assert value('--env-file')=='/dev/stdin' and payload
                env.update(x.split('=',1) for x in payload.decode().splitlines())
            # Runtime credentials may occur ONLY in the stdin env payload.
            assert self.raw_fixture not in ' '.join(a)
            self.rows[target]={'Id':target,'Name':'/'+name,'Image':a[index],
             'Config':{'Labels':labels,'Entrypoint':[value('--entrypoint')],'Cmd':a[index+1:],'User':value('--user'),'WorkingDir':value('--workdir'),'Env':[k+'='+v for k,v in env.items()],'Healthcheck':{'Test':['NONE']}},
             'HostConfig':{'NetworkMode':value('--network'),'PortBindings':{},'ExtraHosts':[],'Privileged':False,'AutoRemove':False,'ReadonlyRootfs':'--read-only' in a,'CapDrop':[value('--cap-drop')],'SecurityOpt':['no-new-privileges'],'RestartPolicy':{'Name':value('--restart=no') if '--restart=no' not in a else 'no','MaximumRetryCount':0},'LogConfig':{'Type':'none'},'PidMode':'','IpcMode':'private','Memory':int(value('--memory')),'MemorySwap':int(value('--memory-swap')),'NanoCpus':500000000,'PidsLimit':int(value('--pids-limit')),'Tmpfs':tmp},
             'NetworkSettings':{'Networks':{value('--network'): {}}},'Mounts':mounts,'State':{'Running':False,'OOMKilled':False,'ExitCode':0,'Status':'created','Pid':0,'StartedAt':'0001-01-01T00:00:00Z'}}
            return target.encode()
        if op==('container','start'):
            target=a[-1]; r=self.rows[target];role=next(k for k,v in self.plan['names'].items() if '/'+v==r['Name']);self.events.append(('start',role))
            r['State'].update(Running=True,Status='running',Pid=1000+len(self.rows),StartedAt='2026-11-07T00:00:00.000000000Z')
            if '--attach' in a:
                r['State'].update(Running=False,Status='exited',Pid=0)
                if role=='namespace':return m.app_canonical({'namespaceState':'REGISTERED' if self.namespace_ok else 'UNKNOWN','textAttributes':{'organizationId':True,'postId':True}})
                if role=='api':
                    assert json.loads(payload)=={'API_KEY':self.raw_fixture}
                    return m.app_canonical({'checks':dict.fromkeys(['apiKeyValid','integrationCount','missingKeyRejected','invalidKeyRejected','registrationClosed'],True)})
                if role=='session':return m.app_canonical({'ok':self.session_ok})
                if role=='media':return m.app_canonical({'matched':True,'files':1})
                raise AssertionError('unexpected probe')
            return target.encode()
        if op==('container','top'):
            r=self.rows[a[2]];entry=r['Config']['Entrypoint'][0]
            if r['Name'].endswith('-es'):entry='/bundled/jdk/bin/java'  # launcher execs the JVM
            return ('COMMAND\n'+entry+' '+' '.join(r['Config']['Cmd'])+'\n').encode()
        if op==('container','kill'):
            assert a[2]=='--signal=SIGTERM';r=self.rows[a[-1]];self.events.append(('stop',r['Name']));r['State'].update(Running=False,Status='exited',Pid=0);return a[-1].encode()
        if op==('container','exec'):
            if 'psql' in a:
                assert a.count('-c')==11 and a[a.index('-c')+1]=='BEGIN READ ONLY;' and a[-1]=='ROLLBACK;'
                return b'1\n1\n1\n3\n1\n3\n0\n3\n0\n'
            if 'pg_isready' in a:return b'127.0.0.1 accepting connections\n'
            if 'redis-cli' in a:return b'PONG\n'
            if 'curl' in a:return b'{"status":"yellow","timed_out":false}'
            if 'node' in a:return b'APP_READY'
        raise AssertionError('Unexpected Docker operation: '+str(a[:2]))


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=[]
        for obj,key in [(subprocess,'run'),(socket,'socket'),(socket,'create_connection'),(socket,'getaddrinfo')]:
            q=patch.object(obj,key,side_effect=AssertionError('unexpected external side effect'));q.start();self.stack.append(q)
    def tearDown(self):
        for q in reversed(self.stack):q.stop()
    def refused(self,fn):
        with self.assertRaises((m.Refusal,OSError,ValueError)):fn()
    def driver(self,cookie=False):
        plan=m.app_plan(args(),family());d=Docker(plan)
        private={'PG14_USER':'clone','PG14_DB':'clone','PG16_USER':'clone','JWT_SECRET':d.raw_fixture,'API_KEY':d.raw_fixture,'TEMPORAL_NAMESPACE':'default'}
        if cookie:private['SESSION_COOKIE']='auth=synthetic.preserved.fixture'
        r=m.AppRead(plan,private,call=d)
        r.family=lambda: (setattr(r,'media',{'synthetic.bin':'d'*64}),r.handles.update({k:1 for k in plan['trees']}))
        r.capacity=lambda:None;r.boundary=lambda:r.check_cancel()
        st=types.SimpleNamespace(st_uid=1000,st_gid=1000)
        return r,d,st
    def execute(self,r,st,checked=False):
        original_stat=m.os.fstat; original_read=m.app_read_file
        def read(path,expected=None):
            if str(path).endswith('/app-read/pg_hba.conf'):return m.APP_HBA
            if str(path).endswith('/app-read/current.yaml'):return b'{}'
            return original_read(path,expected)
        with patch.object(m.os,'fstat',side_effect=lambda fd:st if fd==1 else original_stat(fd)),patch.object(m,'app_read_file',side_effect=read):
            # Pre-start tree identity is tested separately; protocol fixture supplies exact expected tree per role.
            def tree(fd):return {},r._expected_tree
            original=r.start
            def start(role,*a,**kw):
                if role in m.APP_DATA:r._expected_tree=r.plan['trees'][m.APP_DATA[role][0]]
                return original(role,*a,**kw)
            r.start=start
            with patch.object(m,'app_tree',side_effect=tree):return r.checked_execute() if checked else r.execute()
    def test_01_actual_plan_is_offline_pinned_and_not_ready(self):
        plan=m.app_plan(args());self.assertFalse(plan['ready']);self.assertEqual(plan['source'],m.APP_SOURCE)
        self.assertEqual(plan['count_baseline']['counts'],{'users':1,'organizations':1,'integrations':3,'media':1,'posts':3})
        self.assertTrue(all('current-restore-extraction' not in k or 'restore-epochs/20261107g/' in k for k in plan['input_pins']))
        self.assertEqual(plan['memory_admission_bytes'],3087007744)
    def test_02_memory_and_disk_exact_admission(self):
        m.app_admit(3087007744,3*1024**3,1024**3)
        m.app_admit(3288936448,23616917504,1024**3)
        self.refused(lambda:m.app_admit(3087007743,23616917504))
        self.refused(lambda:m.app_admit(3288936448,3*1024**3-1,1024**3))
    def test_03_current_family_and_counts_not_old_defaults(self):
        for changed in [{'target':'/var/lib/arkx-current-restore/arkx-current-restore-20261107'+x} for x in 'abcdefghi']+[{'target':'/var/lib/arkx-current-restore/../escape'},{'counts':{**family()['counts'],'Integration':2}},{'counts':{**family()['counts'],'Media':0}},{'counts':{**family()['counts'],'Post':0}},{'counts':{**family()['counts'],'queued':1}},{'temporal_entrypoint':'/etc/temporal/auto-setup.sh'},{'extra':True}]:
            with self.subTest(changed=list(changed)):self.refused(lambda:m.app_plan(args(),{**family(),**changed}))
        self.assertTrue(m.app_plan(args(),family())['ready'])
    def test_04_input_duplicate_keys_limits_and_private_plan_refuse(self):
        self.refused(lambda:m.app_json(b'{"a":1,"a":2}'))
        self.refused(lambda:m.app_read_main(args(),io.BytesIO(b'x'*65537)))
        output=io.StringIO()
        with patch.object(m.sys,'stdout',output):self.refused(lambda:m.app_read_main(args(),io.BytesIO(b'{"private":{"JWT_SECRET":"must-not-print"}}')))
        self.assertNotIn('must-not-print',output.getvalue())
    def test_05_full_allowed_protocol_with_preserved_cookie(self):
        r,d,st=self.driver(True);result=self.execute(r,st)
        self.assertTrue(result['checks']['session']);self.assertFalse(result['checks']['fullApplicationRecovery'])
        self.assertEqual(result['counts']['Integration'],3);self.assertEqual(result['counts']['Media'],1)
        self.assertTrue(all(not x['State']['Running'] for x in d.rows.values()))
        self.assertLess(d.events.index(('start','namespace')),d.events.index(('create','backend')))
        self.assertNotIn(d.raw_fixture,m.app_canonical(result).decode())
        self.assertEqual(len(r.ids),10)
        self.assertEqual(r.temporary('es'),{'/tmp':'rw,exec,nosuid,nodev,size=130023424,mode=1777','/usr/share/elasticsearch/logs':'rw,noexec,nosuid,nodev,size=4194304,uid=1000,gid=0,mode=0700'})
    def test_06_absent_preserved_cookie_is_an_explicit_gap(self):
        r,d,st=self.driver();result=self.execute(r,st)
        self.assertTrue(result['session_gap']);self.assertFalse(result['checks']['session']);self.assertNotIn('session',r.ids)
    def test_07_bad_namespace_prevents_backend(self):
        r,d,st=self.driver();d.namespace_ok=False
        self.refused(lambda:self.execute(r,st));self.assertNotIn(('create','backend'),d.events)
        self.assertTrue(all(not x['State']['Running'] for x in d.rows.values()))
    def test_08_unknown_start_is_never_retried_and_retained_shutdown(self):
        r,d,st=self.driver();d.fail=('container','start')
        self.refused(lambda:self.execute(r,st))
        self.assertEqual(sum(a[:2]==['container','start'] for a,_ in d.calls),1)
        self.assertEqual(len(r.ids),1);self.assertTrue(all(not x['State']['Running'] for x in d.rows.values()))
        self.assertFalse(any(a[1] in ['rm','prune'] for a,_ in d.calls))
    def test_09_guard_rejects_exact_security_and_env_drift(self):
        r,d,st=self.driver();self.execute(r,st);target=r.ids['backend'];saved=copy.deepcopy(d.rows[target])
        mutations=[lambda v:v['HostConfig'].update(PortBindings={'3000/tcp':[{}]}),lambda v:v['HostConfig'].update(Privileged=True),lambda v:v['HostConfig'].update(ReadonlyRootfs=False),lambda v:v['HostConfig'].update(Memory=1),lambda v:v['NetworkSettings']['Networks'].update(bridge={}),lambda v:v['Config']['Env'].append('RUN_CRON=false'),lambda v:v['Config']['Cmd'].append('orchestrator'),lambda v:v['Mounts'].append({'Type':'bind','Source':'/live','Destination':'/bad','RW':True}),lambda v:v['State'].update(OOMKilled=True),lambda v:v['Config']['Labels'].update({m.APP_LABEL:'foreign'})]
        for mutate in mutations:
            d.rows[target]=copy.deepcopy(saved);mutate(d.rows[target]);self.refused(lambda:r.guard('backend'))
        d.rows[target]=saved;r.guard('backend')
    def test_10_bad_session_and_data_mismatch_never_succeed(self):
        r,d,st=self.driver(True);d.session_ok=False;self.refused(lambda:self.execute(r,st))
        r,d,st=self.driver();r.plan['family']['counts']['Integration']=4
        self.refused(lambda:self.execute(r,st));self.assertNotIn(('create','backend'),d.events)
    def test_11_shutdown_is_graceful_bounded_and_does_not_escalate(self):
        r,d,st=self.driver();self.execute(r,st);r.stopped.clear();row=d.rows[r.ids['backend']];row['State'].update(Running=True,Status='running')
        d.fail=('container','kill');r.shutdown();self.assertFalse(r.stopped['backend'])
        count=len(d.calls);r.shutdown();self.assertEqual(len(d.calls),count)
        self.assertFalse(any('--force' in a or 'SIGKILL' in a for a,_ in d.calls))
    def test_12_cancellation_blocks_new_resources(self):
        r,d,st=self.driver();r.cancelled=True
        self.refused(lambda:self.execute(r,st));self.assertEqual(d.rows,{})
    def test_13_actual_tree_scanner_rejects_symlink_and_external_hardlink(self):
        with tempfile.TemporaryDirectory() as t,patch.object(m,'app_mount_id',return_value='one'):
            p=Path(t);(p/'file').write_bytes(b'synthetic');fd=os.open(t,os.O_RDONLY|os.O_DIRECTORY)
            try:
                rows,meta=m.app_tree(fd);self.assertEqual(meta['entries'],2);self.assertEqual(meta['expanded_bytes'],9)
                (p/'link').symlink_to(p/'file');self.refused(lambda:m.app_tree(fd));(p/'link').unlink()
                with tempfile.TemporaryDirectory() as other:
                    os.link(p/'file',Path(other)/'linked');self.refused(lambda:m.app_tree(fd))
            finally:os.close(fd)
    def test_14_paths_mount_alias_and_temporal_config_refuse(self):
        for path in ['/a/../b','relative','/a//b','/bad\nname']:self.refused(lambda:m.app_absolute(path))
        mounts=[('1:1',Path('/'),Path('/'))];self.assertEqual(m.app_physical(Path('/a'),mounts),('1:1',Path('/a')))
        self.refused(lambda:m.app_physical(Path('/a'),mounts+mounts))
        r,_,_=self.driver()
        good=temporal_config(r.plan)
        r.temporal_config(good)
        bad=copy.deepcopy(good);bad['persistence']['datastores']['default']['sql']['password']='private';self.refused(lambda:r.temporal_config(bad))
        bad=copy.deepcopy(good);bad['persistence']['datastores']['es-visibility']['elasticsearch']['url']['host']='external.invalid:9200';self.refused(lambda:r.temporal_config(bad))
    def test_15_legacy_class_and_flags_are_preserved(self):
        node=next(n for n in ast.parse(Path(m.__file__).read_text()).body if isinstance(n,ast.ClassDef) and n.name=='Rehearsal')
        self.assertEqual(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest(),'e95af582fb7afb029bd6e9305c57dbe89338efaa56b560aeceddfd5f6fffa6f6')
        self.assertEqual(m.EXPECTED,{'User':1,'Organization':1,'Integration':2,'Media':0,'Post':0})
        a=m.parser().parse_args(['--prefix','arkx-migration-rehearsal-testread','--run-id','8'*32]);self.assertEqual(a.stage,'plan')
        self.refused(lambda:m.main(['--prefix',a.prefix,'--run-id',a.run_id,'--app-read-execute']))


    def test_16_actual_family_binding_marker_and_source_tampering(self):
        with tempfile.TemporaryDirectory() as t,patch.object(m,'app_mount_id',return_value='one'):
            t=str(Path(t).resolve());root=Path(t);plan=m.app_plan(args(),family());plan['family']['target']=t;trees={}
            for suffix in plan['trees']:
                d=root/suffix;d.mkdir(mode=0o700)
                if suffix in ('postiz-postgres','temporal-postgresql'):(d/'PG_VERSION').write_text('14' if suffix=='postiz-postgres' else '16')
                if suffix=='postiz-uploads':(d/'synthetic.bin').write_bytes(b'synthetic media')
                fd=os.open(d,os.O_RDONLY|os.O_DIRECTORY)
                try:trees[suffix]=m.app_tree(fd)[1]
                finally:os.close(fd)
            plan['trees']=trees
            saved={'plan':{'target':t,'evidence':{'current-backup-verified.json':m.APP_ACQUISITION},'executor_sha256':'bcd2e75dc5a79133cd439c5510b27d6c143e2f07e02654c61ac9491448bc33be'}}
            saved['plan_sha256']=m.app_digest(saved['plan'])
            result={'plan_sha256':saved['plan_sha256'],'storage_extraction_verified':True,'components':trees}
            for name,value,k in [('plan.json',saved,'plan_sha256'),('result.json',result,'result_sha256')]:
                data=m.app_canonical(value);(root/name).write_bytes(data);plan['family'][k]=hashlib.sha256(data).hexdigest()
            cfg=root/'app-read';cfg.mkdir(mode=0o755)
            (cfg/'pg_hba.conf').write_bytes(m.APP_HBA)
            origin=Path(m.__file__).resolve().parents[2]/'.codex/current-live-account-counts.json';(cfg/'counts.json').write_bytes(origin.read_bytes())
            conf=temporal_config(plan)
            (cfg/'current.yaml').write_bytes(m.app_canonical(conf));plan['family']['config_sha256']=hashlib.sha256((cfg/'current.yaml').read_bytes()).hexdigest()
            for f in cfg.iterdir():f.chmod(0o644)
            original_stat=m.os.fstat;original_lstat=Path.lstat
            def root_owned(st):
                v=list(st);v[4]=0;return os.stat_result(v)
            inodes={root.stat().st_ino,cfg.stat().st_ino}
            def fstat(fd):
                st=original_stat(fd);return root_owned(st) if st.st_ino in inodes else st
            def lstat(p,*a,**kw):
                st=original_lstat(p,*a,**kw);return root_owned(st) if p.parent==cfg else st
            r=m.AppRead(plan,{'PG16_USER':'clone'})
            with patch.object(m.os,'fstat',side_effect=fstat),patch.object(Path,'lstat',lstat):
                try:r.family();self.assertEqual(len(r.media),1);self.assertTrue((root/'.app-read-used').is_file())
                finally:
                    for fd in r.handles.values():os.close(fd)
                r=m.AppRead(plan,{'PG16_USER':'clone'})
                try:self.refused(r.family)
                finally:
                    for fd in r.handles.values():os.close(fd)
                (root/'.app-read-used').unlink()  # disposable local fixture only
                (root/'postiz-postgres/PG_VERSION').write_text('tampered')
                r=m.AppRead(plan,{'PG16_USER':'clone'})
                try:self.refused(r.family);self.assertFalse((root/'.app-read-used').exists())
                finally:
                    for fd in r.handles.values():os.close(fd)
    def test_17_actual_boundary_refuses_foreign_parent_alias(self):
        r,d,_=self.driver()
        with tempfile.TemporaryDirectory() as t,tempfile.TemporaryDirectory() as other:
            t=str(Path(t).resolve());other=str(Path(other).resolve());root=Path(t);(root/'app-read').mkdir();r.plan['family']['target']=t
            r.identity['root']=(root.stat().st_dev,root.stat().st_ino);r.identity['config']=((root/'app-read').stat().st_dev,(root/'app-read').stat().st_ino)
            r.handles={}
            target='e'*64;d.rows[target]={'Id':target,'Name':'/foreign','Mounts':[{'Type':'bind','Source':t,'Destination':'/foreign','RW':False}]}
            with patch.object(m,'app_mounts',return_value=[('1:1',Path('/'),Path('/'))]):
                self.refused(lambda:m.AppRead.boundary(r))
                d.rows[target]['Mounts'][0]['Source']=other;m.AppRead.boundary(r)
    def test_18_pins_confirmation_and_image_volume_fail_before_start(self):
        with patch.dict(m.APP_PINS,{'native-image-local-proof.json':'0'*64}):self.refused(lambda:m.app_plan(args()))
        self.refused(lambda:m.app_read_main(args(app_read_execute=True,confirm_app_read='0'*64),io.BytesIO(m.app_canonical({'family':family(),'private':{}}))))
        r,d,st=self.driver();original=d.__call__
        def call(a,**kw):
            raw=original(a,**kw)
            if a[:2]==['image','inspect']:
                v=json.loads(raw);v[0]['Config']['Volumes']={'/unreviewed':{}};return m.app_canonical(v)
            return raw
        r.call=call;self.refused(lambda:self.execute(r,st));self.assertFalse(d.rows)
    def test_19_embedded_node_programs_pass_actual_node22_syntax(self):
        r,d,st=self.driver(True);self.execute(r,st)
        node='/opt/homebrew/opt/node@22/bin/node'
        self.assertTrue(Path(node).is_file(),'existing Node22 required; no install fallback')
        for role in ('namespace','api','session','media'):
            code=r.specs[role]['args'][1]
            result=LOCAL_RUN([node,'--check'],input=code,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,role+': embedded JavaScript failed syntax')
        self.assertIn("main([])",r.specs['namespace']['args'][1]);self.assertIn("main(['--allow-host'",r.specs['api']['args'][1])
    def test_20_legacy_plan_allowed_without_docker(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'dump';p.write_bytes(b'PGDMPsynthetic')
            out=io.StringIO()
            with patch.object(m.sys,'stdout',out):m.main(['--prefix',args().prefix,'--run-id','8'*32,'--pg-image','sha256:'+'a'*64,'--candidate-image',m.APP_IMAGE,'--backup-dir',t,'--dump-relative','dump','--dump-sha256',hashlib.sha256(p.read_bytes()).hexdigest()])
            self.assertIn('PLAN ONLY',out.getvalue());self.assertIn('rollback-data',out.getvalue())


    def test_21_actual_node_probe_bodies_with_denied_real_transport(self):
        r,d,st=self.driver(True);self.execute(r,st)
        wrapper=r"""
const vm=require('node:vm'),fs=require('node:fs'),crypto=require('node:crypto');
const x=JSON.parse(fs.readFileSync(0,'utf8')); let output='';
const child={env:x.env,argv:[],stdin:(async function*(){yield Buffer.from(JSON.stringify(x.payload))})(),stdout:{write(v){output+=v}},exitCode:0};
const ctx={Buffer,URL,AbortSignal,module:{exports:{}},process:child,
 require(name){
  if(name==='node:dns/promises')return {lookup:async()=>[{family:4,address:'10.0.0.8'}]};
  if(name==='node:crypto')return crypto;
  if(name==='@temporalio/client')return {Connection:{connect:async()=>({workflowService:{describeNamespace:async()=>({namespaceInfo:{state:1}})},operatorService:{listSearchAttributes:async()=>({customAttributes:{organizationId:1,postId:1}})},close:async()=>{}})}};
  if(name==='node:fs')return {readFileSync(p){if(p!=='/uploads/synthetic.bin')throw Error();return Buffer.from('synthetic media')}};
  throw Error('unexpected module');
 },fetch:async(url,options)=>{
  const path=new URL(url).pathname;if(!['10.0.0.8',x.env.APP_HOST].includes(new URL(url).hostname))throw Error('real transport refused');
  if(path==='/user/self')return {ok:true,status:200,json:async()=>({id:'synthetic-account'})};
  if(path==='/auth/can-register')return {status:200,json:async()=>({register:false}),body:{cancel:async()=>{}}};
  const valid=options.headers.Authorization==='synthetic-api-value';
  return {status:valid?200:401,json:async()=>path.endsWith('/integrations')?[{},{},{}]:{connected:true},body:{cancel:async()=>{}}};
 }};
vm.runInNewContext(x.code,ctx);
setTimeout(()=>{try{process.stdout.write(JSON.stringify({output:JSON.parse(output),exitCode:child.exitCode}))}catch(_){process.exitCode=1}},25);
"""
        for role in ('namespace','api','session','media'):
            spec=r.specs[role];payload={}
            if role=='api':payload={'API_KEY':'synthetic-api-value'}
            if role=='session':payload={'cookie':'auth=synthetic.preserved.fixture'}
            if role=='media':payload={'synthetic.bin':hashlib.sha256(b'synthetic media').hexdigest()}
            value={'code':spec['args'][1],'env':spec['env'],'payload':payload}
            result=LOCAL_RUN(['/opt/homebrew/opt/node@22/bin/node','-e',wrapper],input=m.app_canonical(value),capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,role+': mocked actual Node body failed')
            actual=json.loads(result.stdout);self.assertEqual(actual['exitCode'],0)
            if role=='api':self.assertTrue(all(actual['output']['checks'].values()))
            if role=='session':self.assertEqual(actual['output'],{'ok':True})
            if role=='media':self.assertEqual(actual['output'],{'matched':True,'files':1})
            if role=='namespace':self.assertEqual(actual['output']['namespaceState'],'REGISTERED')
            self.assertNotIn(b'synthetic-api-value',result.stdout+result.stderr)


    def test_22_only_confirmed_owned_term_can_accept_143(self):
        r,d,st=self.driver();original=d.__call__
        def call(a,**kw):
            raw=original(a,**kw)
            if a[:2]==['container','kill'] and a[-1]==r.ids.get('backend'):d.rows[a[-1]]['State']['ExitCode']=143
            return raw
        r.call=call;self.assertTrue(self.execute(r,st)['retained'])
        r.stopped.clear();r.shutdown();self.assertFalse(r.stopped['backend'])  # pre-existing143 is not a graceful-stop exemption


    @contextlib.contextmanager
    def memory_fixture(self,anon=100,shmem=20,available=None,role='pg14'):
        r,d,st=self.driver();self.execute(r,st)
        target=r.ids[role];row=d.rows[target];uid,gid=map(int,r.specs[role]['user'].split(':'))
        row['State'].update(Running=True,Status='running',Pid=321,StartedAt='2026-11-07T00:00:00.000000000Z')
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp).resolve();proc=base/'proc';proc.mkdir();cgroot=base/'cgroup';cgroot.mkdir()
            cg=cgroot/'system.slice'/('docker-'+target+'.scope');cg.mkdir(parents=True)
            membership='/system.slice/docker-'+target+'.scope';proc_inodes=set();overrides={}
            def add_process(pid=321,start=1000,shared=True):
                pd=proc/str(pid);pd.mkdir();(pd/'ns').mkdir()
                (pd/'stat').write_text(str(pid)+' (synthetic) S '+' '.join(['0']*18+[str(start)]+['0']*5)+'\n')
                (pd/'cgroup').write_text('0::'+membership+'\n')
                if pid!=321 and shared:os.link(proc/'321/ns/pid',pd/'ns/pid')
                else:(pd/'ns/pid').write_bytes(b'namespace-object')
                for q in [pd,*pd.rglob('*')]:proc_inodes.add(q.stat().st_ino)
                return pd
            pd=add_process()
            (cg/'cgroup.procs').write_text('321\n');(cg/'memory.max').write_text(str(m.APP_LIMITS.get(role,64)*1024**2)+'\n')
            (cg/'memory.swap.max').write_text('0\n');(cg/'memory.stat').write_text(f'anon {anon}\nshmem {shmem}\nfile 900000000\nkernel 800000\nslab 700000\n')
            original_stat=m.os.stat;original_fstat=m.os.fstat
            def metadata(st):
                owner=overrides.get(st.st_ino,(uid,gid) if st.st_ino in proc_inodes else (0,0))
                values=list(st);values[4],values[5]=owner;return os.stat_result(values)
            with patch.object(m,'APP_PROC_ROOT',proc),patch.object(m,'APP_CGROUP_ROOT',cgroot),patch.object(m.os,'fstat',side_effect=lambda fd:metadata(original_fstat(fd))),patch.object(m.os,'stat',side_effect=lambda *a,**kw:metadata(original_stat(*a,**kw))),patch.object(m.os,'statvfs',return_value=types.SimpleNamespace(f_bavail=4*1024**3,f_frsize=1)),patch.object(m,'app_memory_available',return_value=m.APP_REQUIRED_MEMORY-anon-shmem if available is None else available):
                yield types.SimpleNamespace(r=r,d=d,row=row,cg=cg,proc=proc,pd=pd,add_process=add_process,overrides=overrides)
    def test_23_measured_anon_shmem_enables_transition_with_full_peak_unchanged(self):
        with self.memory_fixture() as f:
            m.AppRead.capacity(f.r);event=f.r.events[-1]
            self.assertEqual(event['owned_resident_credit_bytes'],120);self.assertEqual(event['effective_available_bytes'],m.APP_REQUIRED_MEMORY)
            self.assertEqual(set(event),{'state','available_bytes','owned_resident_credit_bytes','effective_available_bytes','required_bytes'})
            self.assertNotIn(str(f.row['State']['Pid']),json.dumps(event));self.assertEqual(m.APP_RESERVE,1024**3)
        with self.memory_fixture(role='namespace') as f:m.AppRead.capacity(f.r)
    def test_24_no_cache_kernel_limit_stopped_unknown_or_second_probe_credit(self):
        with self.memory_fixture(anon=0,shmem=0,available=m.APP_REQUIRED_MEMORY-1) as f:self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture(available=m.APP_REQUIRED_MEMORY-1) as f:
            f.row['State'].update(Running=False,Status='exited',Pid=0)
            with patch.object(m,'app_owned_dir',side_effect=AssertionError('stopped cgroup must not be read')):self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            f.row['State']['Pid']=0;self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture(role='namespace') as f:
            f.d.rows[f.r.ids['api']]['State'].update(Running=True,Status='running',Pid=322,StartedAt='2026-11-07T00:00:00Z')
            self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture(role='namespace') as f:
            starts=sum(a[:2]==['container','start'] for a,_ in f.d.calls)
            self.refused(lambda:f.r.start('api'))
            self.assertEqual(starts,sum(a[:2]==['container','start'] for a,_ in f.d.calls))
    def test_25_memory_stat_numeric_unique_bounded_and_cap_guards(self):
        for value in [b'anon 1\nshmem 2\nfile 1\nfile 2\n',b'anon -1\nshmem 2\n',b'anon 1\n',b'anon 18446744073709551616\nshmem 0\n',b'anon 1.0\nshmem 0\n',b'anon 1\nshmem 0\n'+b'x'*65536,b'anon 999999999\nshmem 0\n']:
            with self.subTest(shape=len(value)),self.memory_fixture() as f:
                (f.cg/'memory.stat').write_bytes(value);self.refused(lambda:m.AppRead.capacity(f.r))
    def test_26_pid_cgroup_cap_swap_and_ownership_are_mandatory(self):
        for file,value in [('memory.max','1\n'),('memory.swap.max','1\n'),('memory.max','max\n'),('cgroup.procs','999\n'),('cgroup.procs','321\n321\n')]:
            with self.subTest(file=file),self.memory_fixture() as f:
                (f.cg/file).write_text(value);self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            (f.cg/'memory.swap.max').unlink();m.AppRead.capacity(f.r)  # unsupported optional file, never a wrong value
        with self.memory_fixture() as f:
            (f.pd/'cgroup').write_text('0::/system.slice/docker-unknown.scope\n');self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            f.overrides[(f.cg/'memory.stat').stat().st_ino]=(1001,0);self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            f.overrides[(f.pd/'stat').stat().st_ino]=(0,0);self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            (f.pd/'stat').write_text('wrong process');self.refused(lambda:m.AppRead.capacity(f.r))
    def test_27_no_cgroup_or_process_content_symlinks(self):
        with self.memory_fixture() as f:
            path=f.cg/'memory.stat';moved=f.cg/'held';path.rename(moved);path.symlink_to(moved)
            self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            path=f.pd/'cgroup';moved=f.pd/'held';path.rename(moved);path.symlink_to(moved)
            self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            moved=f.cg.with_name('other.scope');f.cg.rename(moved);f.cg.symlink_to(moved,target_is_directory=True)
            self.refused(lambda:m.AppRead.capacity(f.r))
    def test_28_lower_samples_and_stable_pid_start_state_required(self):
        with self.memory_fixture() as f:
            original=m.app_metadata;stats=iter([b'anon 30\nshmem 5\n',b'anon 9\nshmem 1\n'])
            def read(fd,name,*a,**kw):return next(stats) if name=='memory.stat' else original(fd,name,*a,**kw)
            with patch.object(m,'app_metadata',side_effect=read),patch.object(m,'app_memory_available',side_effect=[m.APP_REQUIRED_MEMORY-1,m.APP_REQUIRED_MEMORY-10,m.APP_REQUIRED_MEMORY,m.APP_REQUIRED_MEMORY]):
                m.AppRead.capacity(f.r)
            self.assertEqual(f.r.events[-1]['owned_resident_credit_bytes'],10);self.assertEqual(f.r.events[-1]['available_bytes'],m.APP_REQUIRED_MEMORY-10)
        with self.memory_fixture() as f:
            original=m.app_metadata;reads=0
            def read(fd,name,*a,**kw):
                nonlocal reads
                if name=='memory.stat':
                    reads+=1
                    if reads==2:(f.pd/'stat').write_text('321 (synthetic) S '+' '.join(['0']*18+['1001']+['0']*5))
                return original(fd,name,*a,**kw)
            with patch.object(m,'app_metadata',side_effect=read):self.refused(lambda:m.AppRead.capacity(f.r))
        for key,value in [('Pid',322),('StartedAt','2026-11-07T00:00:01Z'),('Status','restarting')]:
            with self.subTest(key=key),self.memory_fixture() as f:
                original=f.r.memory_states;reads=0
                def states():
                    nonlocal reads
                    reads+=1
                    if reads==2:f.row['State'][key]=value
                    return original()
                with patch.object(f.r,'memory_states',side_effect=states):self.refused(lambda:m.AppRead.capacity(f.r))
    def test_29_other_process_namespace_or_descendant_cgroup_refuses(self):
        with self.memory_fixture() as f:
            f.add_process(322,shared=False);(f.cg/'cgroup.procs').write_text('321\n322\n');self.refused(lambda:m.AppRead.capacity(f.r))
        with self.memory_fixture() as f:
            f.add_process(322,shared=True);(f.cg/'cgroup.procs').write_text('321\n322\n');m.AppRead.capacity(f.r)
        with self.memory_fixture() as f:
            (f.cg/'nested').mkdir();self.refused(lambda:m.AppRead.capacity(f.r))
    def test_30_exact_proven_es_literal_and_verified_temporal_config(self):
        r,d,st=self.driver();self.execute(r,st)
        self.assertEqual(m.APP_ES_STARTUP,'set -eu; umask 077; mkdir -m 0700 /tmp/engine-config; cp -R /usr/share/elasticsearch/config/. /tmp/engine-config/; chmod 0700 /tmp/engine-config; test ! -e /tmp/engine-config/elasticsearch-plugins.yml; export ES_PATH_CONF=/tmp/engine-config; exec /usr/share/elasticsearch/bin/elasticsearch "$@"')
        self.assertEqual(r.specs['es']['env'],{'ES_JAVA_OPTS':'-Xms256m -Xmx256m -Djava.io.tmpdir=/tmp -Djna.tmpdir=/tmp -XX:ActiveProcessorCount=1 -XX:-HeapDumpOnOutOfMemoryError -XX:ErrorFile=/tmp/jvm-error.log -Xlog:disable'})
        self.assertEqual(r.specs['es']['args'][1],m.APP_ES_STARTUP);self.assertEqual(sum([124,4])*1024**2,128*1024**2)
        config=temporal_config(r.plan);r.temporal_config(config)
        config['dynamicConfigClient']['filepath']='config/dynamicconfig/docker.yaml';self.refused(lambda:r.temporal_config(config))
        bad=temporal_config(r.plan);bad['persistence']['visibilityStore']='visibility';self.refused(lambda:r.temporal_config(bad))
        self.assertEqual(r.plan['temporal_runtime']['entrypoint'],'/usr/local/bin/temporal-server')
        self.assertIn('temporal-read-config-basis.json',r.plan['input_pins'])


    def test_31_second_sample_descendant_refuses_before_next_transition(self):
        for transition in ('create','start'):
            with self.subTest(transition=transition),self.memory_fixture() as f:
                original=m.app_metadata;reads=0
                def read(fd,name,*a,**kw):
                    nonlocal reads
                    if name=='memory.stat':
                        reads+=1
                        if reads==2:
                            child=f.cg/'foreign-child';child.mkdir();(child/'cgroup.procs').write_text('999\n')
                    return original(fd,name,*a,**kw)
                f.r.capacity=lambda:m.AppRead.capacity(f.r)
                before=sum(a[:2] in (['container','create'],['container','start']) for a,_ in f.d.calls)
                events=len(f.r.events)
                with patch.object(m,'app_metadata',side_effect=read):
                    with self.assertRaises(m.Refusal):getattr(f.r,transition)('api')
                self.assertEqual(reads,2);self.assertTrue((f.cg/'foreign-child').is_dir())
                self.assertEqual(before,sum(a[:2] in (['container','create'],['container','start']) for a,_ in f.d.calls))
                self.assertEqual(len(f.r.events),events)  # no accepted credit/admission event
    def test_32_unchanged_no_descendant_allows_both_credit_samples(self):
        with self.memory_fixture() as f:
            original=m.app_metadata;reads=0
            def read(fd,name,*a,**kw):
                nonlocal reads
                if name=='memory.stat':reads+=1
                return original(fd,name,*a,**kw)
            before=len(f.d.calls)
            with patch.object(m,'app_metadata',side_effect=read):m.AppRead.capacity(f.r)
            self.assertEqual(reads,2);self.assertEqual(f.r.events[-1]['owned_resident_credit_bytes'],120)
            self.assertEqual(f.r.events[-1]['effective_available_bytes'],m.APP_REQUIRED_MEMORY)
            self.assertFalse(any(p.is_dir() or p.is_symlink() for p in f.cg.iterdir()))
            self.assertFalse(any(a[:2] in (['container','create'],['container','start']) for a,_ in f.d.calls[before:]))
    def test_33_second_sample_child_entries_revalidate_kind_owner_mode_and_identity(self):
        for change in ('symlink','owner','mode','fifo','replacement','new-entry'):
            with self.subTest(change=change),self.memory_fixture() as f:
                path=f.cg/'cgroup.events';path.write_text('populated 1\n');original=m.app_metadata;reads=0
                def read(fd,name,*a,**kw):
                    nonlocal reads
                    if name=='memory.stat':
                        reads+=1
                        if reads==2:
                            if change=='symlink':path.unlink();path.symlink_to(f.cg/'memory.stat')
                            elif change=='owner':f.overrides[path.stat().st_ino]=(1001,0)
                            elif change=='mode':path.chmod(0o666)
                            elif change=='fifo':path.unlink();os.mkfifo(path)
                            elif change=='replacement':
                                replacement=f.cg/'new';replacement.write_text('populated 1\n');os.replace(replacement,path)
                            else:(f.cg/'unexpected').write_text('new entry\n')
                    return original(fd,name,*a,**kw)
                with patch.object(m,'app_metadata',side_effect=read):
                    with self.assertRaises(m.Refusal):m.AppRead.capacity(f.r)
                self.assertEqual(reads,2)


    def test_34_known_stock_metadata_allowed_only_on_its_exact_image(self):
        cases={'redis':{'REDIS_VERSION':'synthetic-stock-version'},
               'temporal':{'TCTL_SHA':'a'*64,'TEMPORAL_SHA':'b'*64},
               'es':{'ELASTIC_CONTAINER':'synthetic-stock-marker'}}
        for role,metadata in cases.items():
            with self.subTest(role=role):
                r,d,st=self.driver();image=r.plan['images'][role]
                self.assertEqual(m.APP_STOCK_IMAGE_ENV[image],frozenset(metadata))
                d.image_env[image]={'PATH':'/usr/bin:/bin',**metadata}
                result=self.execute(r,st);self.assertTrue(result['checks']['api'])
                for key,value in metadata.items():
                    self.assertIn(key+'='+value,d.rows[r.ids[role]]['Config']['Env'])
                self.assertTrue(all(not row['State']['Running'] for row in d.rows.values()))
    def test_35_cross_image_and_unexpected_metadata_refuse_before_resources(self):
        cases=[('backend',key) for key in ('REDIS_VERSION','TCTL_SHA','TEMPORAL_SHA','ELASTIC_CONTAINER')]
        cases += [('temporal','REDIS_VERSION'),('es','TCTL_SHA'),('redis','TEMPORAL_SHA'),('pg14','ELASTIC_CONTAINER'),('redis','UNREVIEWED_STOCK_METADATA'),('temporal','API_KEY')]
        for role,key in cases:
            with self.subTest(role=role,key=key):
                r,d,st=self.driver();d.image_env[r.plan['images'][role]]={'PATH':'/usr/bin:/bin',key:'synthetic-only-value'}
                self.refused(lambda:self.execute(r,st));self.assertFalse(d.rows);self.assertIsNone(d.net)
        self.assertFalse({'REDIS_VERSION','TCTL_SHA','TEMPORAL_SHA','ELASTIC_CONTAINER'} & m.SAFE_IMAGE_ENV)
    def test_36_stock_allowance_preserves_duplicate_key_refusal(self):
        r,d,st=self.driver();image=r.plan['images']['redis'];d.image_env[image]={'PATH':'/usr/bin:/bin','REDIS_VERSION':'synthetic-stock-version'}
        original=d.__call__
        def call(a,**kw):
            raw=original(a,**kw)
            if a[:2]==['image','inspect'] and a[2]==image:
                value=json.loads(raw);value[0]['Config']['Env'].append('REDIS_VERSION=duplicate-fixture');return m.app_canonical(value)
            return raw
        r.call=call;self.refused(lambda:self.execute(r,st));self.assertFalse(d.rows);self.assertIsNone(d.net)


    def inherited_label_fixture(self):
        r,d,st=self.driver()
        # Public key names from the actual j proof; values are synthetic, not a live dump.
        keys=('org.label-schema.build-date','org.label-schema.license','org.label-schema.name','org.label-schema.schema-version','org.label-schema.url','org.label-schema.usage','org.label-schema.vcs-ref','org.label-schema.vcs-url','org.label-schema.vendor','org.label-schema.version','org.opencontainers.image.created','org.opencontainers.image.documentation','org.opencontainers.image.licenses','org.opencontainers.image.ref.name','org.opencontainers.image.revision','org.opencontainers.image.source','org.opencontainers.image.title','org.opencontainers.image.url','org.opencontainers.image.vendor','org.opencontainers.image.version')
        d.image_labels[r.plan['images']['es']]={k:'synthetic-stock-'+str(i) for i,k in enumerate(keys)}
        d.image_labels[m.APP_IMAGE]={'org.opencontainers.image.revision':m.APP_SOURCE,'org.opencontainers.image.source':'synthetic-candidate-source'}
        return r,d,st
    def test_37_exact_stock_plus_run_labels_allowed_for_es_and_candidate(self):
        r,d,st=self.inherited_label_fixture();result=self.execute(r,st);self.assertTrue(result['checks']['api'])
        self.assertEqual(len(r.image_labels[r.plan['images']['es']]),20)
        for role in ('es','backend','namespace','api','media'):
            image=r.plan['images'].get(role,m.APP_IMAGE);expected={**d.image_labels[image],**r.labels}
            self.assertEqual(d.rows[r.ids[role]]['Config']['Labels'],expected);r.guard(role)
        self.assertEqual(d.net['Labels'],r.labels)  # network has no image inheritance
    def test_38_added_altered_or_missing_container_labels_refuse(self):
        r,d,st=self.inherited_label_fixture();self.execute(r,st)
        for role in ('es','backend'):
            target=r.ids[role];saved=copy.deepcopy(d.rows[target]['Config']['Labels']);baked=next(iter(r.image_labels[r.plan['images'][role]]))
            for change in ('extra','baked-value','missing-baked','run-value','missing-run','missing-map','null-map'):
                with self.subTest(role=role,change=change):
                    d.rows[target]['Config']['Labels']=copy.deepcopy(saved);labels=d.rows[target]['Config']['Labels']
                    if change=='extra':labels['unexpected.runtime.label']='synthetic'
                    elif change=='baked-value':labels[baked]='altered'
                    elif change=='missing-baked':labels.pop(baked)
                    elif change=='run-value':labels[m.APP_LABEL]='altered'
                    elif change=='missing-run':labels.pop(m.APP_LABEL+'.plan')
                    elif change=='missing-map':d.rows[target]['Config'].pop('Labels')
                    else:d.rows[target]['Config']['Labels']=None
                    self.refused(lambda:r.guard(role))
            d.rows[target]['Config']['Labels']=saved;r.guard(role)
    def test_39_image_run_label_collision_refuses_even_with_matching_value(self):
        for role in ('es','backend'):
            for key in (m.APP_LABEL,m.APP_LABEL+'.plan'):
                for same in (False,True):
                    with self.subTest(role=role,key=key,same=same):
                        r,d,st=self.inherited_label_fixture();d.image_labels[r.plan['images'][role]][key]=r.labels[key] if same else 'synthetic-collision'
                        self.refused(lambda:self.execute(r,st));self.assertFalse(d.rows);self.assertIsNone(d.net)
    def test_40_malformed_image_labels_refuse_missing_and_null_stock_are_empty(self):
        for labels in (False,[],{'unsupported':None}):
            r,d,st=self.driver();d.image_labels[r.plan['images']['es']]=labels
            self.refused(lambda:self.execute(r,st));self.assertFalse(d.rows);self.assertIsNone(d.net)
        r,d,st=self.driver();original=d.__call__
        def call(a,**kw):
            raw=original(a,**kw)
            if a[:2]==['image','inspect']:
                value=json.loads(raw);value[0]['Config'].pop('Labels');return m.app_canonical(value)
            return raw
        r.call=call;self.assertTrue(self.execute(r,st)['checks']['api'])
        self.assertTrue(all(value=={} for value in r.image_labels.values()))
        r,d,st=self.driver();d.image_labels[r.plan['images']['pg14']]=None
        self.assertTrue(self.execute(r,st)['checks']['api'])


    def test_41_all_six_actual_optional_image_label_shapes_are_allowed(self):
        r,d,st=self.inherited_label_fixture()
        d.omit_image_labels={r.plan['images'][role] for role in ('redis','pg14','pg16')}
        d.image_labels[r.plan['images']['temporal']]={'synthetic.temporal.metadata':'fixture'}
        result=self.execute(r,st,checked=True);self.assertTrue(result['checks']['api'])
        self.assertEqual(len(set(r.plan['images'].values())),6)
        for role in ('redis','pg14','pg16'):self.assertEqual(r.image_labels[r.plan['images'][role]],{})
        for role in ('backend','temporal','es'):self.assertTrue(r.image_labels[r.plan['images'][role]])
        for role,target in r.ids.items():
            image=r.plan['images'].get(role,m.APP_IMAGE)
            self.assertEqual(d.rows[target]['Config']['Labels'],{**r.image_labels[image],**r.labels})
    def test_42_bounded_own_source_refusal_journal_never_formats_exception(self):
        r,d,st=self.driver();original=d.__call__
        marker='Bearer SYNTHETIC_CREDENTIAL_VALUE Cookie=auth=fixture env=JWT_PRIVATE /private/fixture-path '
        malicious=marker*2000
        def call(a,**kw):
            raw=original(a,**kw)
            if a[:2]==['container','start']:raise m.Refusal(malicious)
            return raw
        r.call=call;out=io.StringIO();err=io.StringIO()
        with tempfile.TemporaryFile() as journal:
            r.journal=journal.fileno()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
                with self.assertRaises(m.Refusal) as caught:self.execute(r,st,checked=True)
            journal.seek(0);raw=journal.read();r.journal=None
        records=[json.loads(line) for line in raw.splitlines()];refused=[v for v in records if v.get('state')=='refused']
        self.assertEqual(len(refused),1);record=refused[0]
        self.assertEqual(set(record),{'state','phase','source_function','source_line'});self.assertEqual(record['phase'],'execution')
        self.assertEqual(record['source_function'],'start')
        self.assertIn(record['source_line'],{line for _,_,line in m.AppRead.start.__code__.co_lines()})
        self.assertLessEqual(len(m.app_canonical(record)),256)
        combined=raw+out.getvalue().encode()+err.getvalue().encode()+str(caught.exception).encode()
        for value in ('SYNTHETIC_CREDENTIAL_VALUE','auth=fixture','JWT_PRIVATE','/private/fixture-path'):self.assertNotIn(value.encode(),combined)
        self.assertTrue(caught.exception.__suppress_context__);self.assertTrue(all(not v['State']['Running'] for v in d.rows.values()))
        self.assertTrue(all(r.stopped.values()))
    def test_43_shutdown_refusal_is_sanitized_and_other_owned_stops_continue(self):
        r,d,st=self.driver();original=d.__call__;marker='SYNTHETIC_STOP_CREDENTIAL /private/fixture-stop'
        def call(a,**kw):
            raw=original(a,**kw)
            if a[:2]==['container','kill'] and a[-1]==r.ids.get('backend'):raise m.Refusal(marker)
            return raw
        r.call=call
        with tempfile.TemporaryFile() as journal:
            r.journal=journal.fileno()
            with self.assertRaises(m.Refusal) as caught:self.execute(r,st,checked=True)
            journal.seek(0);raw=journal.read();r.journal=None
        records=[json.loads(line) for line in raw.splitlines()];refused=[v for v in records if v.get('state')=='refused']
        self.assertEqual({v['phase'] for v in refused},{'shutdown','execution'})
        for record in refused:
            self.assertEqual(set(record),{'state','phase','source_function','source_line'})
            self.assertIn(record['source_function'],('shutdown','execute'));self.assertIsInstance(record['source_line'],int)
        self.assertNotIn(marker.encode(),raw+str(caught.exception).encode());self.assertTrue(all(not v['State']['Running'] for v in d.rows.values()))
        self.assertFalse(r.stopped['backend']);self.assertTrue(all(v for k,v in r.stopped.items() if k!='backend'))
        before=len(d.calls);r.shutdown();self.assertEqual(len(d.calls),before)
        self.assertEqual(sum(a[:2]==['container','kill'] and a[-1]==r.ids['backend'] for a,_ in d.calls),1)
    def test_44_diagnostic_write_failure_cannot_mask_refusal_or_break_shutdown(self):
        r,d,st=self.driver();original=d.__call__;event=r.event;marker='SYNTHETIC_JOURNAL_CREDENTIAL'
        def call(a,**kw):
            raw=original(a,**kw)
            if a[:2]==['container','start']:raise m.Refusal(marker)
            return raw
        def journal(value):
            if value.get('state')=='refused':raise OSError(marker)
            event(value)
        r.call=call;r.event=journal
        with self.assertRaises(m.Refusal) as caught:self.execute(r,st,checked=True)
        self.assertNotIn(marker,str(caught.exception));self.assertTrue(all(not v['State']['Running'] for v in d.rows.values()))
        self.assertTrue(all(r.stopped.values()));self.assertFalse(any(v.get('state')=='refused' for v in r.events))


if __name__=='__main__':unittest.main(verbosity=2)
