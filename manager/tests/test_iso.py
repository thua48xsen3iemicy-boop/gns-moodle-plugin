"""ISO image of a guest directory into the CD-ROM drive of other nodes (CONTRACTS 2.12, 4.7)."""
import pytest
from conftest import make_env
from lab_manager.images import Image
from test_labdef import IMAGES


def catalog(**debian):
    result = dict(IMAGES)
    result['debian12'] = result['debian12'].model_copy(
        update={'iso_source': '/root/to-iso', 'iso_target': True, **debian})
    return result


def lab():
    return {'format': 'gns3lab.lab/1', 'links': [], 'nodes': [
        {'name': 'PC0', 'image': 'debian12', 'x': 0, 'y': 0},
        {'name': 'PC1', 'image': 'debian12', 'x': 100, 'y': 0},
        {'name': 'V', 'image': 'vpcs', 'x': 200, 'y': 0}]}


async def running(env):
    created = await env.client.post('/api/v1/sessions', json={'user_id': 42, 'instance_id': 1, 'course_id': 3, 'lab': lab()})
    assert created.status_code in (200, 201, 202), created.text
    sid = created.json()['session_id']
    await env.run()
    body = (await env.client.get(f'/api/v1/sessions/{sid}')).json()
    assert body['status'] == 'running', body
    ids = {n['name']: n['node_id'] for n in body['topology']['topology']['nodes']}
    return sid, body, ids


async def test_session_names_sources_and_targets_and_iso_runs(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid, body, ids = await running(env)
        assert 'iso' in body['features']
        assert body['iso'] == {'sources': sorted([ids['PC0'], ids['PC1']]), 'targets': [ids['PC0'], ids['PC1']]}  # by node name
        started = await env.client.post(f'/api/v1/sessions/{sid}/iso', json={'node_id': ids['PC0'], 'requested_by_user_id': 42})
        assert started.status_code == 202, started.text
        op = started.json()
        assert op['schema'] == 'gns3lab.iso/1' and op['status'] == 'running'
        pid, source, request = env.slots[201].iso_requests[-1]
        assert source == ids['PC0'] and request == {'path': '/root/to-iso', 'targets': [ids['PC1']]}
        url = f"/api/v1/sessions/{sid}/iso/{op['operation_id']}"
        assert (await env.client.get(url)).json()['status'] == 'running'
        done = (await env.client.get(url)).json()
        assert done == {'schema': 'gns3lab.iso/1', 'operation_id': op['operation_id'], 'status': 'succeeded',
                        'result': {'files': 2, 'bytes': 4096, 'targets': [{'node_id': ids['PC1'], 'status': 'attached'}]}}
        assert (await env.client.get(f"/api/v1/sessions/{sid}/iso/../../v1/fence")).status_code == 404
        assert (await env.client.get(f"/api/v1/sessions/{sid}/iso/{'0' * 8}-0000-0000-0000-{'0' * 12}")).status_code == 404


async def test_failure_and_refusals(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid, _, ids = await running(env)
        post = lambda node: env.client.post(f'/api/v1/sessions/{sid}/iso', json={'node_id': node})
        assert (await post(ids['V'])).json()['error'] == 'iso_not_available'
        assert (await post('nope')).status_code == 404
        env.slots[201].iso_result = {'code': 'ISO_SOURCE_MISSING', 'message': 'No directory /root/to-iso'}
        op = (await post(ids['PC0'])).json()
        busy = await post(ids['PC1'])
        assert busy.status_code == 409 and busy.json()['error'] == 'iso_in_progress'
        url = f"/api/v1/sessions/{sid}/iso/{op['operation_id']}"
        await env.client.get(url)
        failed = (await env.client.get(url)).json()
        assert failed['status'] == 'failed' and failed['error']['code'] == 'ISO_SOURCE_MISSING'
        env.slots[201].iso_mount = False
        refused = await post(ids['PC0'])
        assert refused.status_code == 409 and refused.json()['error'] == 'iso_not_supported'


async def test_lab_without_target_has_no_iso(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(iso_target=False), vmids=(201,)) as env:
        sid, body, ids = await running(env)
        assert 'iso' not in body['features'] and 'iso' not in body
        response = await env.client.post(f'/api/v1/sessions/{sid}/iso', json={'node_id': ids['PC0']})
        assert response.status_code == 409 and response.json()['error'] == 'iso_not_available'


@pytest.mark.parametrize('fields', [
    {'kind': 'iou', 'file': 'x.bin', 'ram_mb': {'default': 512, 'min': 256, 'max': 1024},
     'adapters': {'default': 1, 'min': 1, 'max': 4}, 'iso_target': True},
    {'qga': False, 'iso_source': '/root/x'},
    {'iso_source': 'relative/dir'},
    {'iso_source': '/root/x\nrm -rf /'},
    {'iso_target': 'yes'},
])
def test_catalog_refuses(fields):
    base = {'id': 'x', 'title': 'X', 'kind': 'qemu', 'file': 'x.qcow2',
            'ram_mb': {'default': 512, 'min': 256, 'max': 1024}, 'adapters': {'default': 1, 'min': 1, 'max': 4}}
    with pytest.raises(ValueError):
        Image.model_validate({**base, **fields})


def test_public_flags_without_the_path():
    image = catalog()['debian12']
    public = image.public()
    assert public['iso_source'] is True and public['iso_target'] is True
    assert '/root/to-iso' not in str(public)
    windows = Image.model_validate({'id': 'w', 'title': 'W', 'kind': 'qemu', 'file': 'w.qcow2', 'iso_source': 'C:\\ToISO',
                                    'ram_mb': {'default': 2048, 'min': 1024, 'max': 4096},
                                    'adapters': {'default': 1, 'min': 1, 'max': 4}})
    assert windows.iso_source == 'C:\\ToISO' and 'iso_target' not in windows.public()
