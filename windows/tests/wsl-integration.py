import json, os, subprocess, time, tempfile
from pathlib import Path
root=Path(os.environ['KILIX_TEST_APP_WSL'])
cli=str(root/'wsl-bin/kilix')
if os.environ.get('KILIX_TEST_ISOLATED') != '1':
 raise SystemExit('Run only in an isolated test instance with KILIX_TEST_ISOLATED=1')
out=Path(os.environ['KILIX_TEST_RESULT_WSL'])/'feature-results.json'
results=[]; owned=[]
def run(args, env=None):
 p=subprocess.run([cli,*args],env=env,capture_output=True,text=True,timeout=25)
 if p.returncode: raise RuntimeError(str(args)+': '+p.stderr[:1500])
 return p.stdout
def ids():
 return {w['id'] for o in json.loads(run(['@','ls'])) for t in o['tabs'] for w in t['windows']}
try:
 baseline=ids(); results.append('authenticated list')
 for help_args in (['@','--help'], ['@','ls','--help'], ['@','launch','--help'],
                   ['@','launch','--type=tab','--help'], ['pane','--help'],
                   ['tab','--help'], ['tab','new','--help'], ['fullscreen','--help']):
  help_env=os.environ.copy()
  for name in ('KITTY_LISTEN_ON','KITTY_RC_PASSWORD','KITTY_WINDOW_ID'): help_env.pop(name,None)
  assert 'usage' in run(help_args,help_env).lower()
 results.append('remote help without credentials')
 with tempfile.TemporaryDirectory(prefix='kilix argv café ') as directory:
  report=Path(directory)/'report.json'
  argv=['', 'a b', 'quote " and \'', 'backslash\\', 'line\nbreak', 'café 日本語', '$HOME;$(false)', '--help']
  code='import json,os,sys,time; from pathlib import Path; Path("report.json").write_text(json.dumps([os.getcwd(),os.getuid(),os.getenv("VALUE"),os.getenv("EMPTY"),os.getenv("PATH"),sys.argv[1:]])); time.sleep(15)'
  pane=int(run(['@','launch','--cwd',directory,'--env=VALUE=a b; $HOME','--env=EMPTY=','--env=PATH','--','/usr/bin/python3','-c',code,*argv])); owned.append(pane)
  for _ in range(30):
   if report.exists(): break
   time.sleep(0.2)
  assert json.loads(report.read_text())==[directory,os.getuid(),'a b; $HOME','',None,argv]
  run(['@','close-window','--match=id:'+str(pane)]); owned.remove(pane)
 results.append('Linux user, cwd, literal env, empty env and removal')
 run(['pane','list','--json']); results.append('pane SDK')
 for _ in range(2):
  run(['fullscreen']); time.sleep(3)
 results.append('fullscreen round trip')
 for password in ('','incorrect-test-password'):
  env=os.environ.copy(); env['KITTY_RC_PASSWORD']=password
  denied=subprocess.run([str(root/'kitty/launcher/kitten.exe'),'@','--use-password','always','ls'],env=env,capture_output=True,text=True,timeout=10)
  assert denied.returncode != 0, 'unauthenticated request succeeded'
 results.append('missing/wrong credentials rejected')
 print('RC_START',flush=True)
 time.sleep(2)
 for i in range(20): ids()
 print('RC_DONE',flush=True)
 time.sleep(2)
 results.append('20 remote requests')
 pane=int(run(['pane','right','--porcelain','--','sh','-c','printf WSL_LAUNCH_READY; sleep 30']).strip()); owned.append(pane)
 time.sleep(2)
 assert 'WSL_LAUNCH_READY' in run(['@','get-text','--match=id:'+str(pane)])
 run(['@','close-window','--match=id:'+str(pane)]); owned.remove(pane)
 results.append('WSL argv launch and close')
 before=ids()
 reply=json.loads(run(['needle','--agent','--json','--yes','split right']))
 assert reply['status']==0, str(reply)
 new=ids()-before; assert len(new)==1
 owned.extend(new)
 for pane in new: run(['@','close-window','--match=id:'+str(pane)]); owned.remove(pane)
 results.append('Needle model split')
 before=ids()
 reply=json.loads(run(['needle','apps','--agent','--json','--yes','open pong']))
 assert reply['status']==0, str(reply)
 new=ids()-before; assert len(new)==1
 owned.extend(new)
 time.sleep(8)
 assert new <= ids(), 'Needle Pong exited early'
 results.append('Needle apps Pong launch')
 needle_game_pane=next(iter(new))
 for pane in new: run(['@','close-window','--match=id:'+str(pane)]); owned.remove(pane)
 pane=int(run(['--launch','kilix-pong']).strip()); owned.append(pane)
 time.sleep(8)
 assert pane in ids(), 'direct Pong exited early'
 results.append('direct Pong launch')
 out.write_text(json.dumps({'passed':results,'game_pane':pane,'needle_game_pane':needle_game_pane}))
 print('FEATURE_TEST_PASS')
except Exception as e:
 out.write_text(json.dumps({'passed':results,'error':repr(e)})); print('FEATURE_TEST_FAIL',e)
finally:
 for pane in owned:
  try: run(['@','close-window','--match=id:'+str(pane)])
  except Exception: pass

if 'error' in json.loads(out.read_text()): raise SystemExit(1)
