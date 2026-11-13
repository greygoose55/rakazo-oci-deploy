#!/usr/bin/env python3
"""Guarded Rakazo infrastructure deployment. Never print provider responses or config."""
import base64
import hashlib
import html
import re
import json
import os
import sys
import time
import urllib.request
import uuid
from datetime import datetime

class Pause(Exception):
    pass

def require(test):
    if not test:
        raise Pause()

def verify_free_policy():
    """Fail closed if the official free allowance cannot be confirmed."""
    url = 'https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm'
    with urllib.request.urlopen(url, timeout=25) as response:
        raw = response.read(2_000_000).decode('utf-8')
    text = re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', raw)))
    for phrase in ('1,500 OCPU hours', '9,000 GB hours', '200 GB', 'Ubuntu',
                   'must create the Always Free compute instances in your home region'):
        require(phrase in text)

class Ledger:
    """Durable generic deployment state. No infrastructure identifiers are published."""
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.token = os.environ['GH_TOKEN']
    def api(self, path, data=None, method=None):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request('https://api.github.com/repos/' + self.repo + path,
            data=body, method=method,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    def pending(self):
        ds = self.api('/deployments?environment=foundation&per_page=1')
        if not ds:
            return False
        states = self.api('/deployments/' + str(ds[0]['id']) + '/statuses?per_page=1')
        return not states or states[0]['state'] not in ('failure', 'inactive')
    def window_open(self):
        # Delayed schedules must not bunch more than 3 requests into 10 minutes.
        ds = self.api('/deployments?environment=foundation&per_page=3')
        if len(ds) < 3:
            return True
        oldest = datetime.fromisoformat(ds[-1]['created_at'].replace('Z', '+00:00')).timestamp()
        return time.time() - oldest >= 600
    def begin(self):
        d = self.api('/deployments', {'ref': os.environ['GITHUB_SHA'],
            'environment': 'foundation', 'auto_merge': False,
            'required_contexts': [], 'description': 'Guarded infrastructure deployment',
            'transient_environment': False, 'production_environment': False})
        self.status(d['id'], 'in_progress')
        return d['id']
    def status(self, ident, state):
        self.api('/deployments/' + str(ident) + '/statuses',
            {'state': state, 'auto_inactive': False,
             'description': 'Generic deployment state; no account information included.'})
    def disable(self):
        self.api('/actions/workflows/deploy.yml/disable', method='PUT')

def main():
    import oci
    cfg = json.loads(os.environ['DEPLOY_CONFIG'])
    require(set(cfg) == {'auth','target'})
    a, t = cfg['auth'], cfg['target']
    a = dict(a, key_content=os.environ['OCI_KEY_PEM'])
    # The owner must verify current Always Free coverage and authorize this exact target.
    require(t['free_only'] is True)
    require(t['shape'] == 'VM.Standard.A1.Flex')
    require((t['ocpus'], t['memory_gb'], t['boot_gb']) == (1, 6, 50))
    require(t['region'] == t['home_region'] == a['region'])
    verify_free_policy()
    require(t['dedicated_empty_tenancy_verified'] is True)
    require(hashlib.sha256(t['ssh_public_key'].encode()).hexdigest() == t['ssh_sha256'])
    signer = oci.signer.Signer(a['tenancy'], a['user'], a['fingerprint'],
        private_key_file_location=None, private_key_content=os.environ['OCI_KEY_PEM'])
    opts = {'signer': signer, 'retry_strategy': oci.retry.NoneRetryStrategy(),
            'timeout': (10, 45)}
    cc = oci.core.ComputeClient(a, **opts)
    bc = oci.core.BlockstorageClient(a, **opts)
    nc = oci.core.VirtualNetworkClient(a, **opts)
    ledger = Ledger()
    def all_items(fn, **kw):
        return oci.pagination.list_call_get_all_results(fn, **kw).data
    def inspect():
        instances = [x for x in all_items(cc.list_instances, compartment_id=t['compartment'])
                     if x.lifecycle_state != 'TERMINATED']
        boots = [x for x in all_items(bc.list_boot_volumes, compartment_id=t['compartment'],
                  availability_domain=t['ad']) if x.lifecycle_state != 'TERMINATED']
        blocks = [x for x in all_items(bc.list_volumes, compartment_id=t['compartment'])
                   if x.lifecycle_state != 'TERMINATED']
        require(not blocks)
        if instances:
            require(len(instances) == 1 and len(boots) == 1)
            i = instances[0]
            require(i.display_name == t['name'] and i.shape == t['shape'])
            require(i.shape_config.ocpus == 1 and i.shape_config.memory_in_gbs == 6)
            require(i.image_id == t['image'] and boots[0].size_in_gbs == 50)
            require(i.metadata.get('ssh_authorized_keys') == t['ssh_public_key'])
            require(i.instance_options.are_legacy_imds_endpoints_disabled is True)
            if i.lifecycle_state == 'RUNNING':
                ba = [x for x in all_items(cc.list_boot_volume_attachments,
                    compartment_id=t['compartment'], availability_domain=t['ad'],
                    instance_id=i.id) if x.lifecycle_state == 'ATTACHED']
                require(len(ba) == 1 and ba[0].boot_volume_id == boots[0].id)
                require(ba[0].is_pv_encryption_in_transit_enabled is True)
                va = [x for x in all_items(cc.list_vnic_attachments,
                    compartment_id=t['compartment'], instance_id=i.id)
                    if x.lifecycle_state == 'ATTACHED']
                require(len(va) == 1)
                vnic = nc.get_vnic(va[0].vnic_id).data
                require(vnic.subnet_id == t['subnet'] and bool(vnic.public_ip))
                require(not vnic.nsg_ids)
                ledger.disable()
                print('HOST_READY_AUTOMATION_STOPPED')
            else:
                require(i.lifecycle_state in ('PROVISIONING', 'STARTING'))
                print('HOST_PENDING_NO_NEW_SUBMISSION')
            return True
        require(not boots)
        return False
    if inspect():
        return
    # An interrupted request leaves durable pending state. Never overwrite it blindly.
    require(not ledger.pending())
    sub = nc.get_subnet(t['subnet']).data
    require(sub.lifecycle_state == 'AVAILABLE' and sub.vcn_id == t['vcn'])
    require(not sub.prohibit_public_ip_on_vnic)
    image = cc.get_image(t['image']).data
    require(image.lifecycle_state == 'AVAILABLE')
    require(image.operating_system == 'Canonical Ubuntu')
    require('24.04' in image.operating_system_version)
    require('aarch64' in image.display_name)
    require(any(s.shape == t['shape'] for s in all_items(cc.list_shapes,
        compartment_id=t['compartment'], image_id=t['image'], availability_domain=t['ad'])))
    if os.environ.get('VALIDATE_ONLY', 'true') == 'true':
        print('VALIDATED_NO_SUBMISSION')
        return
    for attempt in range(3):
        if attempt:
            time.sleep(180)
        if inspect():
            return
        require(not ledger.pending())
        verify_free_policy()
        if not ledger.window_open():
            print('RATE_WINDOW_WAIT_NO_SUBMISSION')
            return
        marker = ledger.begin()  # Must commit before the resource-creating API call.
        request = oci.core.models.LaunchInstanceDetails(
            availability_domain=t['ad'], compartment_id=t['compartment'],
            display_name=t['name'], shape=t['shape'],
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=1,memory_in_gbs=6),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type='image', image_id=t['image'], boot_volume_size_in_gbs=50,
                boot_volume_vpus_per_gb=10),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=t['subnet'], assign_public_ip=True),
            instance_options=oci.core.models.InstanceOptions(are_legacy_imds_endpoints_disabled=True),
            is_pv_encryption_in_transit_enabled=True,
            metadata={'ssh_authorized_keys':t['ssh_public_key'],
                      'user_data':base64.b64encode(t['cloud_init'].encode()).decode()},
            agent_config=oci.core.models.LaunchInstanceAgentConfigDetails(
                are_all_plugins_disabled=True))
        try:
            cc.launch_instance(request, opc_retry_token=str(uuid.uuid4()))
        except oci.exceptions.ServiceError as e:
            # Only this definite response permits another scheduled capacity attempt.
            if e.status == 500 and e.message.strip().lower().rstrip('.') == 'out of host capacity':
                require(not inspect())
                ledger.status(marker, 'failure')
                print('CAPACITY_UNAVAILABLE')
                continue
            raise Pause() from None
        # Accepted creates remain pending until reconciled RUNNING, never retried.
        print('REQUEST_ACCEPTED_AWAITING_HOST')
        inspect()
        return

if __name__ == '__main__':
    try:
        main()
    except BaseException:
        # Credentials, identifiers, metadata, API responses and tracebacks must stay private.
        try:
            Ledger().disable()
        except BaseException:
            pass
        print('PAUSED_REQUIRES_PRIVATE_RECONCILIATION')
        sys.exit(1)
