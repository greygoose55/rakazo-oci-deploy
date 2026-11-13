import importlib.util,json,os,hashlib,types,unittest
from pathlib import Path
from unittest.mock import patch
import oci
S=types.SimpleNamespace
spec=importlib.util.spec_from_file_location('deploy',str(Path(__file__).with_name('deploy.py'))); d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
class SafetyTests(unittest.TestCase):
 def run_case(self,error=None,pending=False,target_override=None,instances=None,boots=None,boot_id='boot'):
  calls=[]; states=[]
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
   def window_open(self): return True
   def begin(self): states.append('begin'); return 1
   def status(self,*a): states.append(a[1])
   def disable(self): states.append('disabled')
  env={'DEPLOY_CONFIG':json.dumps({'auth':{'region':'r','user':'u','tenancy':'t','fingerprint':'f'},'target':target}),'OCI_KEY_PEM':'NOT_A_KEY','VALIDATE_ONLY':'false'}
  with patch.dict(os.environ,env),patch.object(d,'Ledger',L),patch.object(d,'verify_free_policy',lambda:None),patch.object(oci.signer,'Signer',lambda *a,**kw:None),patch.object(oci.core,'ComputeClient',lambda *a,**kw:C()),patch.object(oci.core,'BlockstorageClient',lambda *a,**kw:B()),patch.object(oci.core,'VirtualNetworkClient',lambda *a,**kw:N()),patch.object(oci.pagination,'list_call_get_all_results',lambda fn,**kw:S(data=fn(**kw))),patch.object(d.time,'sleep',lambda x:None):
   try:d.main()
   except (d.Pause,oci.exceptions.ServiceError,TimeoutError):pass
  return calls,states
 def test_capacity_exactly_three(self):
  c,s=self.run_case(oci.exceptions.ServiceError(500,'InternalError',{},'Out of host capacity.')); self.assertEqual(len(c),3); self.assertEqual(s,['begin','failure']*3)
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
