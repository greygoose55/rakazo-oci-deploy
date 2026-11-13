import importlib.util,json,os,hashlib,types,unittest
from pathlib import Path
from unittest.mock import patch
import oci
S=types.SimpleNamespace
spec=importlib.util.spec_from_file_location('deploy',str(Path(__file__).with_name('deploy.py'))); d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
class SafetyTests(unittest.TestCase):
 def run_case(self,error=None,pending=False,target_override=None,instances=None,boots=None,boot_id='boot',window_until=0,start=1000,window_plan=None):
  calls=[]; states=[]; clock=[start]; plan=list(window_plan or [])
  target=dict(free_only=True,shape='VM.Standard.A1.Flex',ocpus=1,memory_gb=6,boot_gb=50,region='r',home_region='r',dedicated_empty_tenancy_verified=True,ssh_public_key='PUBLIC_TEST_ONLY',ssh_sha256=hashlib.sha256(b'PUBLIC_TEST_ONLY').hexdigest(),compartment='c',ad='ad',name='rakazo',subnet='s',vcn='v',image='i',cloud_init='')
  target.update(target_override or {})
  class C:
   def list_instances(self,**kw): return instances or []
   def list_boot_volume_attachments(self,**kw): return [S(lifecycle_state='ATTACHED',boot_volume_id=boot_id,is_pv_encryption_in_transit_enabled=True)]
   def list_vnic_attachments(self,**kw): return [S(lifecycle_state='ATTACHED',vnic_id='vn')]
   def list_shapes(self,**kw): return [S(shape='VM.Standard.A1.Flex')]
   def get_image(self,*a): return S(data=S(lifecycle_state='AVAILABLE',operating_system='Canonical Ubuntu',operating_system_version='24.04 Minimal aarch64',display_name='aarch64'))
   def launch_instance(self,*a,**kw):
    calls.append((a,kw))
    if error: raise error
    return S(data=S(id='not-public'))
  class B:
   def list_boot_volumes(self,**kw): return boots or []
   def list_volumes(self,**kw): return []
  class N:
   def get_vnic(self,*a): return S(data=S(subnet_id='s',public_ip='TEST_ONLY',nsg_ids=[]))
   def get_subnet(self,*a): return S(data=S(lifecycle_state='AVAILABLE',vcn_id='v',prohibit_public_ip_on_vnic=False))
  class L:
   def pending(self): return pending
   def window_open(self): return self.window_wait()==0
   def window_wait(self): return plan.pop(0) if plan else max(0,window_until-clock[0])
   def complete_cycle(self,outcome,attempts): states.append(outcome)
   def begin(self): states.append('begin'); return len([x for x in states if x=='begin'])
   def status(self,*a): states.append(a[1])
   def disable(self): states.append('disabled')
  env={'DEPLOY_CONFIG':json.dumps({'auth':{'region':'r','user':'u','tenancy':'t','fingerprint':'f'},'target':target}),'OCI_KEY_PEM':'NOT_A_KEY','VALIDATE_ONLY':'false'}
  with patch.dict(os.environ,env),patch.object(d,'Ledger',L),patch.object(d,'verify_free_policy',lambda:None),patch.object(oci.signer,'Signer',lambda *a,**kw:None),patch.object(oci.core,'ComputeClient',lambda *a,**kw:C()),patch.object(oci.core,'BlockstorageClient',lambda *a,**kw:B()),patch.object(oci.core,'VirtualNetworkClient',lambda *a,**kw:N()),patch.object(oci.pagination,'list_call_get_all_results',lambda fn,**kw:S(data=fn(**kw))),patch.object(d.time,'sleep',lambda x:clock.__setitem__(0,clock[0]+x)):
   try:d.main()
   except (d.Pause,oci.exceptions.ServiceError,TimeoutError):pass
  return calls,states
 def test_startup_jitter_waits_then_full_cycle(self):
  for delay in (1,6,15,49,60):
   c,s=self.run_case(oci.exceptions.ServiceError(500,'InternalError',{},'Out of host capacity.'),window_until=1000+delay)
   self.assertEqual(len(c),3);self.assertEqual(s[-1],'capacity_only')
 def test_exhausted_window_has_explicit_zero_attempt_proof(self):
  c,s=self.run_case(window_until=1061);self.assertEqual(c,[]);self.assertEqual(s,['rate_limited'])
 def test_unknown_has_no_safe_completion(self):
  c,s=self.run_case(TimeoutError());self.assertNotIn('capacity_only',s);self.assertNotIn('rate_limited',s)
 def test_window_uses_definite_failure_completion_time(self):
  l=object.__new__(d.Ledger)
  def api(path):
   if '/statuses' in path:return [{'state':'failure','created_at':'1970-01-01T00:16:40Z'}]
   return [{'id':3},{'id':2},{'id':1}]
  l.api=api
  for now,want in [(1000,601),(1599,2),(1600,1),(1601,0),(1700,0)]:
   with patch.object(d.time,'time',lambda:now):self.assertEqual(l.window_wait(),want)
 def test_pending_completion_timestamp_never_opens_window(self):
  l=object.__new__(d.Ledger);l.api=lambda path:([{'state':'in_progress'}] if '/statuses' in path else [{'id':3},{'id':2},{'id':1}])
  with self.assertRaises(d.Pause):l.window_wait()
 def test_partial_rate_limited_records_only_definitive_attempts(self):
  c,s=self.run_case(oci.exceptions.ServiceError(500,'InternalError',{},'Out of host capacity.'),window_plan=[0,0,61,61])
  self.assertEqual(len(c),1);self.assertEqual(s,['begin','failure','rate_limited'])
 def test_many_jittered_cycles_never_exceed_three_in600(self):
  import random
  rng=random.Random(713); clock=[1000.0]; records=[]; sent=[]; l=object.__new__(d.Ledger)
  def api(path):
   if '/statuses' in path:
    ident=int(path.split('/')[2]);return [{'state':'failure','created_at':datetime_iso(records[ident-1])}]
   return [{'id':i} for i in range(len(records),max(0,len(records)-3),-1)]
  def datetime_iso(ts):
   from datetime import datetime,timezone
   return datetime.fromtimestamp(ts,timezone.utc).isoformat()
  l.api=api
  with patch.object(d.time,'time',lambda:clock[0]):
   for cycle in range(100):
    clock[0]=max(clock[0],1000+cycle*600+rng.choice([0,1,10,49,100,500,650]))
    for attempt in range(3):
     if attempt:clock[0]+=180
     wait=l.window_wait()
     if wait>60:break
     clock[0]+=wait
     self.assertTrue(l.window_open())
     sent.append(clock[0]);clock[0]+=rng.randrange(1,46);records.append(clock[0])
  self.assertGreater(len(sent),100)
  self.assertTrue(all(t-sent[i-3]>=600 for i,t in enumerate(sent) if i>=3))
 def test_committed_cycle_record_contains_exact_run_sha_attempts(self):
  l=object.__new__(d.Ledger);writes=[]
  def api(path,data=None,method=None):
   if data is not None:
    writes.append((path,data));return {'id':99}
   if '/statuses' in path:return [{'state':'failure'}]
   return [{'id':3}]
  l.api=api
  with patch.dict(os.environ,{'GITHUB_SHA':'test-sha','GITHUB_RUN_ID':'100','GITHUB_RUN_ATTEMPT':'1'}):
   l.complete_cycle('rate_limited',[3])
  self.assertEqual(writes[0][1]['payload'],{'schema':2,'run_id':100,'run_attempt':1,'sha':'test-sha','outcome':'rate_limited','attempts':[3]})
  self.assertEqual(writes[-1][0],'/deployments/99/statuses');self.assertEqual(writes[-1][1]['state'],'success')
 def test_pending_prevents_committed_cycle_record(self):
  l=object.__new__(d.Ledger);l.api=lambda path,data=None:([{'state':'in_progress'}] if '/statuses' in path else [{'id':1}])
  with self.assertRaises(d.Pause):l.complete_cycle('rate_limited',[])
 def test_partial_is_not_full_capacity_proof(self):
  l=object.__new__(d.Ledger)
  with self.assertRaises(d.Pause):l.complete_cycle('capacity_only',[1])
 def test_capacity_exactly_three(self):
  c,s=self.run_case(oci.exceptions.ServiceError(500,'InternalError',{},'Out of host capacity.')); self.assertEqual(len(c),3); self.assertEqual(s,['begin','failure']*3+['capacity_only'])
 def test_throttle_never_retries(self):
  c,s=self.run_case(oci.exceptions.ServiceError(429,'TooManyRequests',{},'throttle')); self.assertEqual(len(c),1); self.assertEqual(s,['begin'])
 def test_unknown_never_retries(self):
  c,s=self.run_case(TimeoutError()); self.assertEqual(len(c),1); self.assertEqual(s,['begin'])
 def test_pending_prevents_submission(self):
  c,s=self.run_case(pending=True); self.assertEqual(c,[])
 def test_accepted_not_repeated(self):
  c,s=self.run_case(); self.assertEqual(len(c),1); self.assertEqual(s,['begin'])
 def test_wrong_shape_prevents_submission(self):
  c,s=self.run_case(target_override={'shape':'PAID_TEST_SHAPE'});self.assertEqual(c,[])
 def test_wrong_size_prevents_submission(self):
  c,s=self.run_case(target_override={'ocpus':2});self.assertEqual(c,[])
 def test_wrong_region_prevents_submission(self):
  c,s=self.run_case(target_override={'home_region':'another'});self.assertEqual(c,[])
 def test_non_dedicated_prevents_submission(self):
  c,s=self.run_case(target_override={'dedicated_empty_tenancy_verified':False});self.assertEqual(c,[])
 def test_wrong_ssh_prevents_submission(self):
  c,s=self.run_case(target_override={'ssh_sha256':'bad'});self.assertEqual(c,[])
 def host(self,state='RUNNING'):
  return S(id='host',display_name='rakazo',shape='VM.Standard.A1.Flex',shape_config=S(ocpus=1,memory_in_gbs=6),image_id='i',metadata={'ssh_authorized_keys':'PUBLIC_TEST_ONLY'},instance_options=S(are_legacy_imds_endpoints_disabled=True),lifecycle_state=state)
 def test_exact_running_stops(self):
  c,s=self.run_case(instances=[self.host()],boots=[S(id='boot',size_in_gbs=50,lifecycle_state='AVAILABLE')]);self.assertEqual(c,[]);self.assertEqual(s,['disabled'])
 def test_wrong_boot_relationship_not_ready(self):
  c,s=self.run_case(instances=[self.host()],boots=[S(id='boot',size_in_gbs=50,lifecycle_state='AVAILABLE')],boot_id='different');self.assertEqual(c,[]);self.assertNotIn('disabled',s)
 def test_provisioning_not_repeated(self):
  c,s=self.run_case(instances=[self.host('PROVISIONING')],boots=[S(id='boot',size_in_gbs=50,lifecycle_state='PROVISIONING')]);self.assertEqual(c,[]);self.assertNotIn('disabled',s)
if __name__=='__main__': unittest.main()
