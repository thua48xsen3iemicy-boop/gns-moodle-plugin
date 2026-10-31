"""Durable capture operations and a per-site catalog layered over administrator images."""
import asyncio
from datetime import timezone
import logging
import secrets
import time

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .agent_client import AgentError, AgentUncertain

log = logging.getLogger(__name__)


class CaptureNode(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    nodes: list[CaptureNode] = Field(min_length=1, max_length=100)


class ImagePatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    featured: bool | None = None
    retired: bool | None = None


class DerivedImages:
    def __init__(self, services):
        self.services = services
        self.store = services.store
        self.settings = services.settings
        self._caps = {}

    async def capabilities(self, node):
        cached = self._caps.get(node)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        body = await self.services.agent.capture_capabilities(node)
        budget = body.get('capture_timeout_seconds')
        valid = body.get('images_capture') is True and type(budget) is int and 60 <= budget <= 3600
        result = body if valid else {}
        self._caps[node] = (time.monotonic(), result)
        return result

    async def enabled(self):
        if self.settings.images is None:
            return False
        try:
            return any(await asyncio.gather(*(self.capabilities(n) for n in await self.services.pve.online_nodes())))
        except Exception:  # Capability discovery cannot disable the existing API.
            return False

    async def catalog(self, site):
        images = dict(self.settings.images or {})
        async with self.store.pool.connection() as conn:
            cur = await conn.execute('SELECT * FROM derived_images WHERE site = %s', (site,))
            rows = await cur.fetchall()
        for row in rows:
            base = images.get(row['base'])
            if base is None or base.derived:
                continue
            metadata = {k: row[k] for k in ('base', 'base_sha256', 'sha256', 'size', 'author_user_id',
                                           'description', 'supersedes')}
            metadata['published_at'] = row['published_at'].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            metadata['source'] = {'library_lab_id': row['library_lab_id'], 'node': row['node']}
            images[row['id']] = base.model_copy(update=dict(id=row['id'], title=row['title'], file=row['file'],
                                                            derived=metadata, featured=row['featured'],
                                                            retired=row['retired']))
        return images

    async def pending(self):
        async with self.store.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM image_captures WHERE state = 'running'")
            return await cur.fetchall()

    async def get(self, sid):
        async with self.store.pool.connection() as conn:
            cur = await conn.execute('SELECT * FROM image_captures WHERE session_id = %s', (sid,))
            return await cur.fetchone()

    @staticmethod
    def public(row):
        body = {'schema': 'gns3lab.capture/1', 'state': row['state'], 'error': row['error']}
        if row['state'] == 'done':
            body['images'] = row['result']
        return body

    async def begin(self, session, body, error):
        if session.get('mode') != 'authoring':
            raise error(409, 'not_authoring', 'Only authoring sessions may capture images')
        if session['status'] not in ('running', 'stopped'):
            raise error(409, 'session_not_running', 'Session must be running or stopped')
        if len(body.nodes) > self.settings.image_capture_max_nodes or len({n.name for n in body.nodes}) != len(body.nodes):
            raise error(400, 'invalid_request', 'Too many or duplicate capture nodes')
        ws = await self.store.workspace(session['workspace_id'])
        nodes = {n['name']: n for n in ws['definition']['nodes']}
        catalog = await self.catalog(session['site'])
        request = []
        for item in body.nodes:
            node = nodes.get(item.name)
            problem = 'unknown_node' if node is None else None
            image = catalog.get(node['image']) if node else None
            base = catalog.get(image.derived['base']) if image and image.derived else image
            if not problem and (not image or image.kind != 'qemu' or image.capture is False or not base.sha256):
                problem = 'not_capturable'
            if problem:
                raise error(422, 'lab_invalid', 'Node cannot be captured (base SHA-256 is required)',
                            problems=[{'path': 'nodes', 'code': problem, 'message': item.name}])
            identity = 'd-' + secrets.token_hex(6)
            supersedes = image.id if image.derived and image.derived['author_user_id'] == session['user_id'] and (
                image.derived['source'] == {'library_lab_id': session['library_lab_id'], 'node': item.name}) else None
            request.append(dict(item.model_dump(), id=identity, file=identity + '.qcow2', base=base.id,
                                base_file=base.file, base_sha256=base.sha256, parent_file=image.file,
                                supersedes=supersedes))
        async with self.store.pool.connection() as conn, conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (session['student_key'],))
            existing = await conn.execute('SELECT * FROM image_captures WHERE session_id = %s FOR UPDATE', (session['id'],))
            previous = await existing.fetchone()
            if previous and previous['state'] != 'failed':
                code = 'capture_done' if previous['state'] == 'done' else 'capture_in_progress'
                raise error(409, code, code)
            # An old stopped session must not capture a project since resumed by a newer one.
            newer = await conn.execute("SELECT 1 FROM sessions WHERE student_key = %s AND id != %s AND (status NOT IN ('stopped', 'failed') OR created_at > %s)",
                                       (session['student_key'], session['id'], session['created_at']))
            if await newer.fetchone():
                raise error(409, 'session_not_running', 'Authoring project has a newer session')
            active = await conn.execute("SELECT 1 FROM image_captures WHERE student_key = %s AND state = 'running'", (session['student_key'],))
            if await active.fetchone():
                raise error(409, 'capture_in_progress', 'Workspace capture is already running')
            await conn.execute("INSERT INTO image_captures(session_id, student_key, state, request) VALUES (%s, %s, 'running', %s) ON CONFLICT(session_id) DO UPDATE SET state = 'running', request = excluded.request, completed = '[]', result = NULL, error = NULL, created_at = now(), updated_at = now()",
                               (session['id'], session['student_key'], Jsonb(request)))
            await conn.execute("UPDATE sessions SET stop_requested = true, stop_reason = 'image_capture' WHERE id = %s AND status = 'running'", (session['id'],))
        return self.public(await self.get(session['id']))

    async def fail(self, sid, code, node=None):
        error = {'code': code, 'retryable': code in ('TIMEOUT', 'INTERNAL'), 'node': node}
        async with self.store.pool.connection() as conn:
            await conn.execute("UPDATE image_captures SET state = 'failed', error = %s, updated_at = now() WHERE session_id = %s AND state = 'running'", (Jsonb(error), sid))

    async def step(self, sid):
        # A separate connection avoids exhausting the request pool while a capture
        # waits on a long qemu-img call. Its advisory lock also excludes a worker
        # from an old leader until that connection is closed.
        async with await self.services.leader_connection() as guard:
            key = "image-capture:" + sid
            cur = await guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok", (key,))
            if not (await cur.fetchone())["ok"]:
                return
            try:
                await self._step(sid, guard)
            finally:
                await guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    async def _step(self, sid, guard):
        row = await self.get(sid)
        if not row or row['state'] != 'running':
            return
        session = await self.store.session(sid)
        if session['status'] in ('running', 'stopping'):
            # The usual orchestrator performs lab_stop, ct_stop, reset and park.
            return
        if session['status'] != 'stopped':
            await self.fail(sid, 'AGENT_REJECTED')
            return
        current_node = None
        try:
            if await self.store.slot_holding_student(session['student_key']) is not None:
                await self.fail(sid, 'STUDENT_DIR_BUSY')
                return
            node = session['slot_node']
            caps = await self.capabilities(node)
            if not caps:
                await self.fail(sid, 'AGENT_REJECTED')
                return
            completed = row['completed']
            for item in row['request'][len(completed):]:
                current_node = item['name']
                result = await self.services.agent.capture_image(node, dict(
                    student_key=session['student_key'], project_id=str(session['project_id']), node_name=item['name'],
                    parent_file=item['parent_file'], base_file=item['base_file'], base_sha256=item['base_sha256'],
                    image_file=item['file'], max_bytes=self.settings.image_max_bytes), caps['capture_timeout_seconds'] + 60)
                if (result.get('image_file') != item['file'] or result.get('backing') != item['base_file'] or
                        not isinstance(result.get('sha256'), str) or type(result.get('size')) is not int):
                    raise ValueError('Invalid capture result')
                completed.append(result)
                async with self.store.pool.connection() as conn:
                    await conn.execute('UPDATE image_captures SET completed = %s, updated_at = now() WHERE session_id = %s',
                                       (Jsonb(completed), sid))
            await guard.execute('SELECT 1')
            async with self.store.pool.connection() as conn, conn.transaction():
                for item, result in zip(row['request'], completed):
                    await conn.execute('INSERT INTO derived_images(id, site, file, base, base_file, base_sha256, sha256, size, author_user_id, title, description, library_lab_id, node, supersedes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                                       (item['id'], session['site'], item['file'], item['base'], item['base_file'], item['base_sha256'], result['sha256'], result['size'], session['user_id'], item['title'], item['description'], session['library_lab_id'], item['name'], item['supersedes']))
                await conn.execute("UPDATE image_captures SET state = 'done', result = %s, updated_at = now() WHERE session_id = %s", (Jsonb({i['name']: i['id'] for i in row['request']}), sid))
        except AgentError as exc:
            code = exc.code if exc.code in ('CAPTURE_NODE_MISSING', 'CAPTURE_BASE_MISMATCH', 'CAPTURE_TOO_LARGE', 'STUDENT_DIR_BUSY') else 'TIMEOUT' if isinstance(exc, AgentUncertain) else 'AGENT_REJECTED'
            await self.fail(sid, code, current_node)
        except asyncio.CancelledError:
            # Persisted request/checkpoints are picked up by the next leader. An
            # existing uncheckpointed output fails safely; retry uses fresh IDs.
            raise
        except Exception:
            log.exception('Image capture failed for session %s', sid)
            await self.fail(sid, 'INTERNAL', current_node)


def register_routes(app, services, site_of, own_session, error):
    from fastapi import Depends
    from fastapi.responses import JSONResponse

    images = services.derived

    @app.post('/api/v1/sessions/{session_id}/capture')
    async def capture(session_id: str, body: CaptureRequest, site=Depends(site_of)):
        session = await own_session(session_id, site)
        if not await images.enabled():
            raise error(400, 'unsupported_field', 'Image capture is unavailable')
        result = await images.begin(session, body, error)
        services.orchestrator.wake()
        return JSONResponse(status_code=202, content=result)

    @app.get('/api/v1/sessions/{session_id}/capture')
    async def capture_status(session_id: str, site=Depends(site_of)):
        await own_session(session_id, site)
        row = await images.get(session_id)
        if row is None:
            raise error(404, 'capture_not_found', 'No capture for this session')
        return images.public(row)

    @app.patch('/api/v1/images/{image_id}')
    async def patch_image(image_id: str, body: ImagePatch, site=Depends(site_of)):
        values = body.model_dump(exclude_unset=True)
        if not values or any(v is None for v in values.values()):
            raise error(400, 'invalid_request', 'At least one non-null property is required')
        catalog = await images.catalog(site.slug)
        image = catalog.get(image_id)
        if not image or not site.limits.allows(image.derived['base'] if image.derived else image.id):
            raise error(404, 'not_found', 'Unknown image')
        if not image.derived:
            raise error(409, 'image_static', 'Administrator image is immutable')
        # The authenticated Moodle server checks author/admin capabilities; no
        # caller-supplied user ID is trusted here for a second authorization model.
        async with services.store.pool.connection() as conn:
            assignments = ', '.join(k + ' = %s' for k in values)  # Fixed Pydantic field allowlist.
            await conn.execute('UPDATE derived_images SET ' + assignments + ' WHERE id = %s AND site = %s',
                               (*values.values(), image_id, site.slug))
        return (await images.catalog(site.slug))[image_id].public()
