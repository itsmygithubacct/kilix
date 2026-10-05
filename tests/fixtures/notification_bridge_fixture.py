"""Native notification protocol exercised on two owned temporary buses."""
import json,os,signal,subprocess,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _env_support import sandbox_env
from gi.repository import Gio,GLib
NAME='org.freedesktop.Notifications'
PATH='/org/freedesktop/Notifications'
PORTAL='org.freedesktop.portal.Desktop'
ROOT='/org/freedesktop/portal/desktop'
CONTROL='org.freedesktop.portal.NotificationFixture'
XML='''<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg direction="in" type="s"/><arg direction="in" type="u"/><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="in" type="as"/><arg direction="in" type="a{sv}"/><arg direction="in" type="i"/><arg direction="out" type="u"/></method>
<method name="CloseNotification"><arg direction="in" type="u"/></method>
<method name="GetCapabilities"><arg direction="out" type="as"/></method>
<method name="GetServerInformation"><arg direction="out" type="s"/><arg direction="out" type="s"/><arg direction="out" type="s"/><arg direction="out" type="s"/></method>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
</interface></node>'''
CXML='''<node><interface name="org.freedesktop.portal.NotificationFixture">
<method name="Control"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="u"/></method>
</interface></node>'''

def call(bus,method,args=None):
 return bus.call_sync(NAME,PATH,NAME,method,args,None,Gio.DBusCallFlags.NONE,5000,None)
def notification_args(title,replace=0):
 return GLib.Variant('(susssasa{sv}i)',('Fixture',replace,'',title,'body',
  ['default','Open'],{'image-data':GLib.Variant('(iiibiiay)',(1,1,3,False,8,3,b'\x00\xfe\xff')),
  'window-xid':GLib.Variant('u',0x123456)},10000))
def notify(bus,title,replace=0):
 return call(bus,'Notify',notification_args(title,replace)).unpack()[0]
def control(bus,action,title=''):
 return bus.call_sync(PORTAL,ROOT,CONTROL,'Control',GLib.Variant('(ss)',(action,title)),None,Gio.DBusCallFlags.NONE,5000,None).unpack()[0]
def wait(predicate):
 deadline=time.monotonic()+4
 while time.monotonic()<deadline:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  if predicate():return
  time.sleep(.005)
 raise AssertionError('Fixture signal did not arrive')
def denied(action):
 try:action()
 except GLib.Error as error:
  assert 'AccessDenied' in str(error),str(error)
 else:raise AssertionError('Foreign notification control was allowed')

def client():
 a=Gio.bus_get_sync(Gio.BusType.SESSION,None)
 b=Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],
  Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
 b.set_exit_on_close(False)
 signals_a=[];signals_b=[]
 for bus,signals in ((a,signals_a),(b,signals_b)):
  bus.signal_subscribe(NAME,NAME,None,PATH,None,Gio.DBusSignalFlags.NONE,
   lambda _bus,_sender,_path,_iface,member,body,values:values.append((member,body.unpack())),signals)
 assert call(a,'GetCapabilities').unpack()[0]==['body','actions']
 assert call(a,'GetServerInformation').unpack()==('Owned fixture','Kilix','1','1.2')
 proof={'native_protocol_information':True}
 first=notify(a,'first');second=notify(b,'second')
 assert first!=second and first>0 and second>0
 assert first<100 and second<100 # physical daemon uses IDs starting at 100.
 proof['private_ids_hide_the_physical_id_namespace']=True
 assert notify(a,'first replacement',first)==first
 proof['replacement_preserves_private_identifier']=True
 denied(lambda:call(b,'CloseNotification',GLib.Variant('(u)',(first,))))
 denied(lambda:notify(b,'foreign replacement',first))
 assert control(a,'count')==2
 proof['foreign_close_and_replace_are_denied']=True
 control(a,'action','first replacement')
 wait(lambda:('ActionInvoked',(first,'default')) in signals_a)
 assert not signals_b
 proof['actions_are_unicast_to_the_owner']=True
 call(a,'CloseNotification',GLib.Variant('(u)',(first,)))
 wait(lambda:('NotificationClosed',(first,3)) in signals_a)
 denied(lambda:call(a,'CloseNotification',GLib.Variant('(u)',(first,))))
 proof['closure_invalidates_private_identifier']=True
 early=notify(a,'early')
 wait(lambda:('ActionInvoked',(early,'default')) in signals_a)
 proof['signal_before_notify_reply_is_preserved']=True
 # Restart invalidates IDs without disturbing the private portal route.
 control(a,'restart')
 wait(lambda:('NotificationClosed',(second,4)) in signals_b)
 denied(lambda:notify(b,'stale replacement',second))
 fresh=notify(a,'after restart')
 assert fresh not in (first,second,early)
 proof['daemon_restart_invalidates_old_ids_and_allows_new_calls']=True
 other=notify(b,'outlives sender')
 b.close_sync(None)
 time.sleep(.1)
 assert control(a,'count')==2
 proof['fire_and_forget_notifications_outlive_their_sender']=True
 closing=notify(a,'before replacement closes')
 assert notify(a,'closes during replacement',closing)==closing
 wait(lambda:('NotificationClosed',(closing,1)) in signals_a)
 denied(lambda:notify(a,'replace closed notification',closing))
 proof['closure_before_replacement_reply_does_not_resurrect_id']=True
 # Later calls and errors remain in order behind a pending Notify.
 replies=[]
 def finished(bus,result,label):
  try:replies.append((label,bus.call_finish(result).unpack()))
  except GLib.Error as error:replies.append((label,str(error)))
 for method,args,label in (
  ('Notify',notification_args('slow notification'),'notify'),
  ('CloseNotification',GLib.Variant('(u)',(closing,)),'denied'),
  ('GetCapabilities',None,'capabilities')):
  a.call(NAME,PATH,NAME,method,args,None,Gio.DBusCallFlags.NONE,5000,None,finished,label)
 wait(lambda:len(replies)==3)
 assert [item[0] for item in replies]==['notify','denied','capabilities'],replies
 assert 'AccessDenied' in replies[1][1],replies
 assert replies[2][1]==(['body','actions'],),replies
 proof['queued_requests_and_errors_preserve_client_call_order']=True
 before=control(a,'count')
 message=Gio.DBusMessage.new_method_call(NAME,PATH,NAME,'Notify')
 message.set_body(notification_args('no reply expected'))
 message.set_flags(Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
 a.send_message(message,Gio.DBusSendMessageFlags.NONE)
 a.flush_sync(None)
 wait(lambda:control(a,'count')==before+1)
 proof['no_reply_notify_still_reaches_the_physical_daemon']=True
 # Malformed bodies fail in the relay, without reaching the daemon.
 try:call(a,'Notify',GLib.Variant('(s)',('bad',)))
 except GLib.Error as error:assert 'InvalidArgs' in str(error),str(error)
 else:raise AssertionError('Malformed notification accepted')
 assert control(a,'payloads')>=6
 proof['opaque_image_hints_survive_and_private_window_hint_is_removed']=True
 print(json.dumps(proof),flush=True)

def host(bridge):
 bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
 def name(method,name):
  body=GLib.Variant('(su)',(name,4)) if method=='RequestName' else GLib.Variant('(s)',(name,))
  return bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',method,body,None,Gio.DBusCallFlags.NONE,5000,None)
 name('RequestName',NAME);name('RequestName',PORTAL)
 active={};counter=[100];payloads=[0]
 def emit(member,body):bus.emit_signal(None,PATH,NAME,member,body)
 def dispatch(_bus,_sender,_path,_iface,method,args,invocation):
  try:
   if method=='GetCapabilities':invocation.return_value(GLib.Variant('(as)',(['body','actions'],)))
   elif method=='GetServerInformation':invocation.return_value(GLib.Variant('(ssss)',('Owned fixture','Kilix','1','1.2')))
   elif method=='Notify':
    replace=args.get_child_value(1).get_uint32()
    if replace:assert replace in active
    else:replace=counter[0];counter[0]+=1
    hints=args.get_child_value(6)
    assert hints.lookup_value('window-xid',None) is None
    image=hints.lookup_value('image-data',None)
    assert image.get_type_string()=='(iiibiiay)' and image.unpack()[-1]==[0,254,255]
    payloads[0]+=1
    title=args.get_child_value(3).get_string();active[replace]=title
    if title=='closes during replacement':
     active.pop(replace);emit('NotificationClosed',GLib.Variant('(uu)',(replace,1)))
     GLib.timeout_add(30,lambda:(invocation.return_value(GLib.Variant('(u)',(replace,))),False)[1])
    elif title=='slow notification':
     GLib.timeout_add(30,lambda:(invocation.return_value(GLib.Variant('(u)',(replace,))),False)[1])
    elif title=='early':
     emit('ActionInvoked',GLib.Variant('(us)',(replace,'default')))
     GLib.timeout_add(30,lambda:(invocation.return_value(GLib.Variant('(u)',(replace,))),False)[1])
    else:invocation.return_value(GLib.Variant('(u)',(replace,)))
   elif method=='CloseNotification':
    number=args.unpack()[0];assert number in active
    active.pop(number);emit('NotificationClosed',GLib.Variant('(uu)',(number,3)))
    invocation.return_value(None)
  except Exception as error:invocation.return_dbus_error('org.example.FixtureError',str(error))
 def ctrl(_bus,_sender,_path,_iface,_method,args,invocation):
  action,title=args.unpack()
  if action=='action':
   number=next(number for number,text in active.items() if text==title)
   emit('ActionInvoked',GLib.Variant('(us)',(number,'default')))
  elif action=='restart':
   active.clear();name('ReleaseName',NAME);name('RequestName',NAME)
  value=payloads[0] if action=='payloads' else len(active)
  invocation.return_value(GLib.Variant('(u)',(value,)))
 bus.register_object(PATH,Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],dispatch,None,None)
 bus.register_object(ROOT,Gio.DBusNodeInfo.new_for_xml(CXML).interfaces[0],ctrl,None,None)
 env=sandbox_env(KILIX_PORTAL_HOST_BUS=os.environ['DBUS_SESSION_BUS_ADDRESS'])
 app=subprocess.Popen(['dbus-run-session','--','/usr/bin/python3',bridge,'--wrap','--','/usr/bin/python3',__file__,'client'],
  env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
 try:
  deadline=time.monotonic()+20
  while app.poll() is None and time.monotonic()<deadline:
   GLib.MainContext.default().iteration(False);time.sleep(.002)
  assert app.poll() is not None,'Native notification fixture timed out'
  stdout,stderr=app.communicate(timeout=3)
  if stderr:print(stderr,file=sys.stderr)
  assert app.returncode==0,stdout+stderr
  print(stdout.strip())
 finally:
  if app.poll() is None:os.killpg(app.pid,signal.SIGTERM)
  try:app.communicate(timeout=2)
  except subprocess.TimeoutExpired:os.killpg(app.pid,signal.SIGKILL);app.communicate(timeout=2)

if __name__=='__main__':
 if sys.argv[1]=='host':host(sys.argv[2])
 else:client()
