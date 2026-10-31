// This file is part of Moodle - https://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <https://www.gnu.org/licenses/>.

/** Image capture controls of the library editor. @module mod_gns3lab/editorimages */
import Ajax from 'core/ajax';
import Notification from 'core/notification';

/** Connect capture to the editor's draft and catalog without changing published versions. */
export const init = (root, data, host) => {
    if (!data.imagecapture) {
        return;
    }
    const text = key => data.strings[key] || key;
    const call = (action, args = {}) => Ajax.call([{
        methodname: 'mod_gns3lab_authoring', args: {labid: data.labid, action, ...args},
    }])[0];
    const controls = document.createElement('div');
    controls.className = 'ed-authoring';
    const label = document.createElement('span');
    label.setAttribute('role', 'status');
    controls.append(label);
    root.querySelector('.bar').append(controls);
    const button = (key, handler) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'gbtn';
        item.textContent = text(key);
        item.addEventListener('click', () => Promise.resolve(handler()).catch(Notification.exception));
        controls.append(item);
        return item;
    };
    let working = false;
    let candidates = [];
    let session = null;
    let completedSession = null;
    let poll = null;
    let pendingDefinition = null;
    let pendingNodes = [];
    const storageKey = 'gns3lab-capture-' + data.userid + '-' + data.labid;
    try {
        pendingDefinition = sessionStorage.getItem(storageKey);
        pendingNodes = JSON.parse(sessionStorage.getItem(storageKey + '-nodes') || '[]');
    } catch (error) {
        pendingDefinition = null;
    }
    const refresh = async() => {
        const result = await call('state');
        session = result.state.session || null;
        candidates = result.capturecandidates || candidates;
        label.textContent = session ? session.statusmessage : '';
        if (result.capture?.state === 'done') {
            completedSession = session?.sessionid;
        }
        const live = session && ['queued', 'starting', 'running', 'stopping'].includes(session.status);
        start.disabled = working || !!live;
        stop.disabled = working || !live;
        capture.disabled = working || result.capture?.state === 'running' || !session || session.sessionid === completedSession
            || !['running', 'stopped'].includes(session.status) || !candidates.length;
        resume.hidden = !pendingDefinition;
        clearTimeout(poll);
        if (live) {
            poll = setTimeout(() => refresh().catch(Notification.exception), 3000);
        }
    };
    const withBusy = async operation => {
        working = true;
        start.disabled = stop.disabled = capture.disabled = true;
        try {
            await operation();
        } finally {
            working = false;
            await refresh();
        }
    };
    const start = button('authoringrun', () => withBusy(async() => {
        const definition = host.definition();
        const result = await call('start', {definition});
        host.saved(result.version);
        // Open the same viewer and terminal/screen modules used by students.
        const dialog = document.createElement('dialog');
        dialog.className = 'ed-authoring-dialog';
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'gbtn';
        close.textContent = text('authoringback');
        close.onclick = () => dialog.close();
        const frame = document.createElement('iframe');
        frame.title = text('authoringrun');
        frame.src = data.authoringurl;
        dialog.append(close, frame);
        dialog.addEventListener('close', () => { dialog.remove(); refresh().catch(Notification.exception); });
        root.append(dialog);
        dialog.showModal();
    }));
    const stop = button('stoplab', () => withBusy(() => call('stop')));
    const waitForCapture = async expected => {
        let result;
        do {
            await new Promise(resolve => setTimeout(resolve, 1500));
            result = (await call('capturestate')).capture;
            label.textContent = text(result.stage === 'quiesce' ? 'authoringquiescing' : 'authoringcapturing');
        } while (result.state === 'running');
        if (result.state === 'failed' && result.errorcode === 'GUEST_NOT_STOPPED') {
            return result;
        }
        if (result.state === 'failed') {
            throw new Error(result.errorcode + (result.errornode ? ': ' + result.errornode : ''));
        }
        if (host.definition() !== expected) {
            throw new Error(text('authoringdraftconflict'));
        }
        const applied = await call('apply', {definition: expected});
        host.applied(applied);
        completedSession = session?.sessionid;
        pendingDefinition = null;
        try {
            sessionStorage.removeItem(storageKey);
            sessionStorage.removeItem(storageKey + '-nodes');
        } catch (error) {
            // Session storage is optional; the durable capture remains on the manager.
        }
        label.textContent = text('authoringsaved');
        return null;
    };
    const blockedDialog = (failure, expected, nodes) => {
        const dialog = document.createElement('dialog');
        dialog.className = 'ed-capture-dialog ed-shutdown-dialog';
        const heading = document.createElement('h2');
        heading.textContent = text('authoringblocked');
        const list = document.createElement('ul');
        (failure.nodes || []).forEach(node => {
            const item = document.createElement('li');
            const key = 'authoringreason_' + node.reason;
            item.textContent = node.name + ': ' + (data.strings[key] || text('authoringreason_other'));
            list.append(item);
        });
        const warning = document.createElement('p');
        warning.textContent = text('authoringforcewarning');
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'gbtn'; retry.textContent = text('authoringretry');
        retry.onclick = () => { dialog.close(); submitCapture(expected, nodes, false).catch(Notification.exception); };
        const force = document.createElement('button');
        force.type = 'button'; force.className = 'gbtn danger'; force.textContent = text('authoringforce');
        force.onclick = () => { dialog.close(); submitCapture(expected, nodes, true).catch(Notification.exception); };
        const cancel = document.createElement('button');
        cancel.type = 'button'; cancel.className = 'gbtn'; cancel.textContent = text('authoringcancel');
        cancel.onclick = () => dialog.close();
        dialog.append(heading, list, warning, retry, force, cancel);
        dialog.addEventListener('close', () => dialog.remove());
        root.append(dialog); dialog.showModal();
    };
    const submitCapture = async(expected, nodes, force) => {
        let blocked = null;
        await withBusy(async() => {
            if (host.definition() !== expected) throw new Error(text('authoringdraftconflict'));
            const response = await call('capture', {nodes, definition: expected, force});
            if (response.capture.state === 'failed') {
                blocked = response.capture;
                return;
            }
            pendingDefinition = expected;
            pendingNodes = nodes;
            try {
                sessionStorage.setItem(storageKey, expected);
                sessionStorage.setItem(storageKey + '-nodes', JSON.stringify(nodes));
            } catch (error) {
                // The manager owns the durable capture; storage only aids this tab.
            }
            blocked = await waitForCapture(expected);
        });
        if (blocked) blockedDialog(blocked, expected, nodes);
    };
    const recover = async() => {
        let blocked;
        const expected = pendingDefinition || host.definition();
        await withBusy(async() => { blocked = await waitForCapture(expected); });
        if (blocked) blockedDialog(blocked, expected, pendingNodes);
    };
    const capture = button('authoringcapture', async() => {
        if (working) {
            return;
        }
        await refresh();
        // Saving the topology first gives application a precise draft to compare.
        const expected = host.definition();
        const draft = await Ajax.call([{methodname: 'mod_gns3lab_save_lab_draft',
            args: {labid: data.labid, definition: expected}}])[0];
        host.saved(draft.version);
        const dialog = document.createElement('dialog');
        dialog.className = 'ed-capture-dialog';
        const heading = document.createElement('h2');
        heading.textContent = text('authoringcapture');
        const note = document.createElement('p');
        note.textContent = text('authoringquiescehint') + ' ' + text('authoringsealhint');
        const form = document.createElement('form');
        const fields = [];
        const statuses = new Map(candidates.map(node => [node.name, node.status]));
        host.nodes().filter(node => statuses.has(node.name)).forEach(node => {
            const image = host.image(node.image);
            if (!image || image.kind !== 'qemu' || image.capture === false) {
                return;
            }
            const row = document.createElement('fieldset');
            const legend = document.createElement('legend');
            const checked = document.createElement('input');
            checked.type = 'checkbox'; checked.checked = true;
            legend.append(checked, document.createTextNode(' ' + node.name + ' — ' + text('authoringstatus_' + statuses.get(node.name))));
            const title = document.createElement('input');
            title.type = 'text'; title.required = true; title.maxLength = 100;
            title.value = Array.from(data.labname + ' — ' + node.name).slice(0, 100).join('');
            title.setAttribute('aria-label', text('authoringtitle'));
            const description = document.createElement('textarea');
            description.maxLength = 2000;
            description.setAttribute('aria-label', text('authoringdescription'));
            const titleLabel = document.createElement('label');
            titleLabel.append(document.createTextNode(text('authoringtitle')), title);
            const descriptionLabel = document.createElement('label');
            descriptionLabel.append(document.createTextNode(text('authoringdescription')), description);
            checked.setAttribute('aria-label', node.name);
            checked.onchange = () => {
                title.required = checked.checked;
                title.disabled = description.disabled = !checked.checked;
                submit.disabled = !fields.some(field => field.checked.checked);
            };
            row.append(legend, titleLabel, descriptionLabel); form.append(row);
            fields.push({name: node.name, checked, title, description});
        });
        const submit = document.createElement('button');
        submit.className = 'gbtn primary'; submit.textContent = text('authoringcapture');
        submit.disabled = !fields.length;
        const cancel = document.createElement('button');
        cancel.type = 'button'; cancel.className = 'gbtn'; cancel.textContent = text('authoringcancel');
        cancel.onclick = () => dialog.close();
        form.append(submit, cancel);
        form.onsubmit = event => {
            event.preventDefault();
            const nodes = fields.filter(field => field.checked.checked).map(field => ({
                name: field.name, title: field.title.value, description: field.description.value,
            }));
            if (!nodes.length) {
                return;
            }
            dialog.close();
            submitCapture(expected, nodes, false).catch(Notification.exception);
        };
        dialog.append(heading, note, form);
        dialog.addEventListener('close', () => dialog.remove());
        root.append(dialog); dialog.showModal();
    });
    button('authoringconsoles', () => { window.open(data.authoringurl, '_blank', 'noopener'); });
    const resume = button('authoringresume', recover);
    resume.title = text('authoringresumehint');
    call('images').then(result => host.catalog(result.images)).catch(Notification.exception);
    refresh().catch(Notification.exception);
    if (pendingDefinition) {
        recover().catch(Notification.exception);
    }
};
