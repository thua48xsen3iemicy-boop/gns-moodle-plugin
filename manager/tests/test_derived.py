"""Authoring identity, durable capture, atomic catalog publication and inherited limits."""
import copy

import pytest
from conftest import make_env, SITE_URL, SITE_SECRET
from test_labdef import IMAGES
from lab_manager.config import SiteConfig, SiteLimits


def catalog():
    result = dict(IMAGES)
    result['debian12'] = result['debian12'].model_copy(update={'sha256': 'sha256:' + 'a' * 64})
    return result


def lab(count=1):
    return {'format': 'gns3lab.lab/1', 'nodes': [
        {'name': 'PC' + str(i), 'image': 'debian12', 'x': i * 100, 'y': 0} for i in range(count)], 'links': []}


async def start(env, definition=None, library=12):
    return await env.client.post('/api/v1/sessions', json={
        'user_id': 42, 'library_lab_id': library, 'options': {'mode': 'authoring'}, 'lab': definition or lab()})


async def capture(env, sid, count=1):
    return await env.client.post(f'/api/v1/sessions/{sid}/capture', json={
        'nodes': [{'name': 'PC' + str(i), 'title': 'Prepared PC ' + str(i)} for i in range(count)]})


async def test_authoring_identity_limits_and_resume(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog()) as env:
        info = (await env.client.get('/api/v1/info')).json()
        assert 'image_capture' in info['features'] and 'mode' in info['options']
        first = await start(env)
        assert first.status_code == 201, first.text
        assert first.json()['mode'] == 'authoring'
        sid = first.json()['session_id']
        row = await env.services.store.session(sid)
        assert row['student_key'].startswith('a-') and row['max_duration'] == 240
        assert row['instance_id'] is None
        ordinary = await env.client.post('/api/v1/sessions', json={'user_id': 42, 'instance_id': 5, 'lab': lab()})
        assert ordinary.status_code == 201, ordinary.text
        assert (await start(env)).status_code == 200
        assert (await start(env, library=13)).status_code == 201
        changed = lab(); changed['nodes'][0]['x'] = 17
        assert (await start(env, changed)).status_code == 409
        await env.run()
        await env.client.post(f'/api/v1/sessions/{sid}/stop')
        await env.run()
        again = await start(env)
        nextrow = await env.services.store.session(again.json()['session_id'])
        assert nextrow['project_id'] == row['project_id']
        await env.run()
        await env.client.post(f"/api/v1/sessions/{nextrow['id']}/stop")
        await env.run()
        new = await start(env, changed)
        assert (await env.services.store.session(new.json()['session_id']))['project_id'] != row['project_id']
        lookup = await env.client.get('/api/v1/sessions', params={'user_id': 42, 'library_lab_id': 12})
        assert lookup.json()['session_id'] == new.json()['session_id']


@pytest.mark.parametrize('change', [
    {'instance_id': 3}, {'library_lab_id': None}, {'lab_ref': 'cisco-3x2'},
    {'options': {'mode': 'exam'}}, {'options': {}},
])
async def test_authoring_invalid_combinations(tmp_path, dsn, change):
    async with make_env(tmp_path, dsn, images=catalog()) as env:
        body = {'user_id': 42, 'library_lab_id': 12, 'options': {'mode': 'authoring'}, 'lab': lab()}
        body.update(change)
        response = await env.client.post('/api/v1/sessions', json=body)
        assert response.status_code == 400, response.text


async def test_capture_publish_patch_validate_and_repeat(tmp_path, dsn):
    sites = {SITE_URL: SiteConfig(url=SITE_URL, slug='main', secret=SITE_SECRET,
                                 limits=SiteLimits(images=['debian12']))}
    async with make_env(tmp_path, dsn, images=catalog(), sites=sites) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        assert (await env.client.get(f'/api/v1/sessions/{sid}')).json()['capture_nodes'] == [{'name': 'PC0', 'status': 'started'}]
        assert (await capture(env, sid)).status_code == 202
        assert (await capture(env, sid)).json()['error'] == 'capture_in_progress'
        await env.run()
        response = await env.client.get(f'/api/v1/sessions/{sid}/capture')
        result = response.json()
        assert result['state'] == 'done', result
        image_id = result['images']['PC0']
        images = (await env.client.get('/api/v1/images')).json()['images']
        derived = next(i for i in images if i['id'] == image_id)
        assert derived['derived']['base'] == 'debian12'
        assert derived['derived']['author_user_id'] == 42
        assert derived['ram_mb'] == env.settings.images['debian12'].public()['ram_mb']
        changed = lab(); changed['nodes'][0]['image'] = image_id
        assert (await env.client.post('/api/v1/labs/validate', json={'lab': changed})).status_code == 200
        patched = await env.client.patch('/api/v1/images/' + image_id, json={'title': 'New title', 'featured': True, 'retired': True})
        assert patched.status_code == 200 and patched.json()['featured'], patched.text
        assert (await env.client.patch('/api/v1/images/debian12', json={'title': 'No'})).status_code == 409
        assert (await capture(env, sid)).json()['error'] == 'capture_done'
        # A retired image remains valid in existing labs, including the inherited base limit.
        nextsid = (await start(env, changed)).json()['session_id']
        await env.run()
        assert (await capture(env, nextsid)).status_code == 202
        await env.run()
        newresult = (await env.client.get(f'/api/v1/sessions/{nextsid}/capture')).json()
        assert newresult['state'] == 'done', newresult
        image = next(i for i in (await env.client.get('/api/v1/images')).json()['images'] if i['id'] == newresult['images']['PC0'])
        assert image['derived']['supersedes'] == image_id
        assert image['derived']['base'] == 'debian12'


async def test_capture_failure_is_atomic_and_retry_uses_new_files(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog()) as env:
        sid = (await start(env, lab(2))).json()['session_id']
        await env.run()
        env.agent.capture_fail_node = 'PC1'
        assert (await capture(env, sid, 2)).status_code == 202
        await env.run()
        result = (await env.client.get(f'/api/v1/sessions/{sid}/capture')).json()
        assert result['state'] == 'failed' and result['error']['node'] == 'PC1', result
        assert len(env.agent.captured_images) == 1
        assert not any(i.get('derived') for i in (await env.client.get('/api/v1/images')).json()['images'])
        env.agent.capture_fail_node = None
        assert (await capture(env, sid, 2)).status_code == 202
        blocked = await start(env)
        assert blocked.status_code == 409 and blocked.json()['error'] == 'capture_in_progress'
        await env.run()
        assert (await env.client.get(f'/api/v1/sessions/{sid}/capture')).json()['state'] == 'done'


async def test_capability_gate(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog()) as env:
        env.agent.images_capture = False
        assert 'image_capture' not in (await env.client.get('/api/v1/info')).json()['features']
        assert (await start(env)).status_code == 400


async def test_capture_resumes_checkpoints_after_worker_restart(tmp_path, dsn):
    import asyncio
    from lab_manager.derived import DerivedImages

    async with make_env(tmp_path, dsn, images=catalog()) as env:
        sid = (await start(env, lab(2))).json()['session_id']
        await env.run()
        assert (await capture(env, sid, 2)).status_code == 202
        env.orch.derived = None  # Drive only the normal stopping lifecycle first.
        async with env.services.pool.connection() as conn:
            await conn.execute("UPDATE image_captures SET phase = 'stopping' WHERE session_id = %s", (sid,))
        await env.services.store.request_stop(sid, 'main', 'test')
        await env.run()
        original = env.services.agent.capture_image
        calls = []

        async def interrupted(node, body, timeout):
            calls.append(body['node_name'])
            if len(calls) == 2:
                raise asyncio.CancelledError()
            return await original(node, body, timeout)

        env.services.agent.capture_image = interrupted
        with pytest.raises(asyncio.CancelledError):
            await env.services.derived.step(sid)
        assert len((await env.services.derived.get(sid))['completed']) == 1
        assert not any(i.get('derived') for i in (await env.client.get('/api/v1/images')).json()['images'])
        env.services.agent.capture_image = original
        restarted = DerivedImages(env.services)
        await restarted.step(sid)
        assert (await restarted.get(sid))['state'] == 'done'
        assert len(env.agent.captured_images) == 2  # The first completed file was not captured twice.


async def test_lost_agent_response_fails_safely_and_can_retry(tmp_path, dsn):
    from lab_manager.agent_client import AgentUncertain

    async with make_env(tmp_path, dsn, images=catalog()) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        original = env.services.agent.capture_image

        async def lost(node, body, timeout):
            await original(node, body, timeout)
            raise AgentUncertain(None, 'Response lost after agent publication')

        env.services.agent.capture_image = lost
        assert (await capture(env, sid)).status_code == 202
        await env.run()
        result = (await env.client.get(f'/api/v1/sessions/{sid}/capture')).json()
        assert result['state'] == 'failed' and result['error']['code'] == 'TIMEOUT'
        assert not any(i.get('derived') for i in (await env.client.get('/api/v1/images')).json()['images'])
        env.services.agent.capture_image = original
        assert (await capture(env, sid)).status_code == 202
        await env.run()
        assert (await env.services.derived.get(sid))['state'] == 'done'
        assert len(env.agent.captured_images) == 2


async def test_poweroff_candidate_and_history_survive_resume(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        env.slots[201].stopped_nodes.add('PC0')
        state = (await env.client.get(f'/api/v1/sessions/{sid}')).json()
        # The initial cached snapshot can be at most three seconds old; capture always refreshes.
        env.orch._live_cache.clear()
        state = (await env.client.get(f'/api/v1/sessions/{sid}')).json()
        assert state['capture_nodes'] == [{'name': 'PC0', 'status': 'stopped'}]
        await env.client.post(f'/api/v1/sessions/{sid}/stop')
        await env.run()
        nextsid = (await start(env)).json()['session_id']
        await env.run()
        ws = await env.services.store.workspace((await env.services.store.session(nextsid))['workspace_id'])
        assert ws['started_nodes'] == ['PC0']
        assert (await env.client.get(f'/api/v1/sessions/{nextsid}')).json()['capture_nodes'] == [{'name': 'PC0', 'status': 'started'}]


async def test_partial_guest_failure_keeps_session_running_then_manual_poweroff_retries(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env, lab(2))).json()['session_id']
        await env.run()
        env.slots[201].shutdown_errors['PC1'] = 'GUEST_AGENT_UNAVAILABLE'
        assert (await capture(env, sid, 2)).status_code == 202
        assert not (await env.services.store.session(sid))['stop_requested']
        await env.run()
        row = await env.services.derived.get(sid)
        assert row['state'] == 'failed' and row['error']['code'] == 'GUEST_NOT_STOPPED'
        assert row['error']['nodes'] == [{'name': 'PC1', 'reason': 'GUEST_AGENT_UNAVAILABLE'}]
        assert row['error']['retryable']
        assert (await env.services.store.session(sid))['status'] == 'running'
        assert not env.agent.captured_images
        assert env.slots[201].stopped_nodes == {'PC0'}
        env.slots[201].stopped_nodes.add('PC1')  # poweroff from the console
        assert (await capture(env, sid, 2)).status_code == 202
        await env.run()
        row = await env.services.derived.get(sid)
        assert row['state'] == 'done' and row['forced'] == []
        assert all(not i.derived['forced'] for i in (await env.services.derived.catalog('main')).values() if i.derived)


async def test_force_marks_only_guests_that_failed_shutdown(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env, lab(2))).json()['session_id']
        await env.run()
        env.slots[201].shutdown_errors['PC1'] = 'GUEST_SHUTDOWN_TIMEOUT'
        response = await env.client.post(f'/api/v1/sessions/{sid}/capture', json={
            'nodes': [{'name': 'PC0', 'title': 'Clean'}, {'name': 'PC1', 'title': 'Forced'}], 'force': True})
        assert response.status_code == 202
        await env.run()
        result = (await env.client.get(f'/api/v1/sessions/{sid}/capture')).json()
        assert result['state'] == 'done' and result['forced'] == ['PC1'], result
        images = {i['title']: i for i in (await env.client.get('/api/v1/images')).json()['images'] if i.get('derived')}
        assert not images['Clean']['derived']['forced'] and images['Forced']['derived']['forced']
        names = [entry[0] for entry in env.timeline]
        assert names.index('slot:guest-shutdown') < names.index('slot:stop')


async def test_stopped_session_requires_force_unless_guest_was_already_off(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        await env.client.post(f'/api/v1/sessions/{sid}/stop')
        await env.run()
        refused = await capture(env, sid)
        assert refused.status_code == 409 and refused.json()['error'] == 'guest_not_stopped'
        assert refused.json()['nodes'] == [{'name': 'PC0', 'reason': 'SESSION_STOPPED'}]
        forced = await env.client.post(f'/api/v1/sessions/{sid}/capture', json={
            'nodes': [{'name': 'PC0', 'title': 'Forced after stop'}], 'force': True})
        assert forced.status_code == 202
        await env.run()
        assert (await env.services.derived.get(sid))['forced'] == ['PC0']


@pytest.mark.parametrize('advertise,route', [(False, False), (True, False)])
async def test_legacy_slot_leaves_session_running(tmp_path, dsn, advertise, route):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        env.slots[201].guest_shutdown = advertise
        env.slots[201].shutdown_route = route
        assert (await capture(env, sid)).status_code == 202
        await env.run()
        row = await env.services.derived.get(sid)
        assert row['error']['nodes'] == [{'name': 'PC0', 'reason': 'GUEST_SHUTDOWN_UNSUPPORTED'}]
        assert (await env.services.store.session(sid))['status'] == 'running'
        assert not env.agent.captured_images


async def test_never_started_node_is_rejected_before_capture(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        env.slots[201].stopped_nodes.add('PC0')
        async with env.services.pool.connection() as conn:
            await conn.execute('UPDATE workspaces SET started_nodes = %s', ([],))
        response = await capture(env, sid)
        assert response.status_code == 422 and response.json()['problems'][0]['code'] == 'never_started'
        assert not env.agent.captured_images


async def test_late_status_response_cannot_replace_newer_pre_stop_snapshot(tmp_path, dsn):
    from datetime import datetime, timezone, timedelta
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        row = await env.services.store.session(sid)
        stamp = datetime.now(timezone.utc)
        await env.services.store.observe_nodes(row, {'PC0': 'started'}, observed_at=stamp)
        await env.services.store.observe_nodes(row, {'PC0': 'stopped'}, observed_at=stamp - timedelta(seconds=1))
        assert (await env.services.store.session(sid))['node_statuses'] == {'PC0': 'started'}


async def test_unreachable_status_before_stop_requires_force(tmp_path, dsn):
    from lab_manager.slot_client import SlotUnavailable
    async with make_env(tmp_path, dsn, images=catalog(), vmids=(201,)) as env:
        sid = (await start(env)).json()['session_id']
        await env.run()
        env.slots[201].stopped_nodes.add('PC0')
        await env.orch.live_nodes(await env.services.store.session(sid), fresh=True)
        original = env.services.slots[201].topology
        async def unavailable(pid):
            raise SlotUnavailable('Status temporarily unavailable')
        env.services.slots[201].topology = unavailable
        await env.client.post(f'/api/v1/sessions/{sid}/stop')
        await env.run()
        env.services.slots[201].topology = original
        response = await capture(env, sid)
        assert response.status_code == 409 and response.json()['error'] == 'guest_not_stopped'
