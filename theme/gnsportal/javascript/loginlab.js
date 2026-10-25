// This file is part of Moodle - http://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

/**
 * Public, local-only topology and console preview for the login page.
 * It never contacts the lab manager or handles Moodle credentials.
 *
 * @package theme_gnsportal
 */
(() => {
    'use strict';

    const root = document.querySelector('.gnsportal-lab');
    if (!root) {
        return;
    }

    const svg = root.querySelector('.gnsportal-topology');
    const view = root.querySelector('.gnsportal-topology-view');
    const output = root.querySelector('.gnsportal-console-output');
    const form = root.querySelector('.gnsportal-console-form');
    const prompt = root.querySelector('.gnsportal-prompt');
    const command = root.querySelector('.gnsportal-command');
    const title = root.querySelector('.gnsportal-console-title');
    const scale = root.querySelector('.gnsportal-scale');
    const ns = 'http://www.w3.org/2000/svg';
    const nodes = [
        {id: 'NAT1', kind: 'cloud', x: 104, y: 176, ip: '203.0.113.1'},
        {id: 'R1', kind: 'router', x: 282, y: 178, ip: '10.0.12.1'},
        {id: 'R2', kind: 'router', x: 510, y: 110, ip: '10.0.12.2'},
        {id: 'R3', kind: 'router', x: 525, y: 305, ip: '10.0.13.2'},
        {id: 'SW1', kind: 'switch', x: 260, y: 384, ip: '10.0.10.2'},
        {id: 'SW2', kind: 'switch', x: 715, y: 304, ip: '10.0.20.2'},
        {id: 'PC1', kind: 'pc', x: 104, y: 555, ip: '10.0.10.11'},
        {id: 'PC2', kind: 'pc', x: 368, y: 555, ip: '10.0.10.12'},
        {id: 'SRV1', kind: 'server', x: 768, y: 525, ip: '10.0.20.10'},
    ];
    const links = [
        ['NAT1', 'R1'], ['R1', 'R2'], ['R1', 'R3'], ['R1', 'SW1'],
        ['R2', 'R3'], ['R3', 'SW2'], ['SW1', 'PC1'], ['SW1', 'PC2'], ['SW2', 'SRV1'],
    ];
    const byId = Object.fromEntries(nodes.map(node => [node.id, node]));
    const mobile = window.matchMedia('(max-width: 720px)').matches;
    const initial = mobile ? {x: 20, y: 70, k: 1.6} : {x: 38, y: 12, k: .92};
    const state = {...initial, selected: 'R1', drag: null};
    const pointers = new Map();
    let pinch = null;

    function element(name, attributes = {}, parent = view) {
        const item = document.createElementNS(ns, name);
        Object.entries(attributes).forEach(([key, value]) => item.setAttribute(key, value));
        parent.appendChild(item);
        return item;
    }

    function text(parent, content, attributes = {}) {
        const item = element('text', attributes, parent);
        item.textContent = content;
        return item;
    }

    function draw() {
        const defs = element('defs');
        const pattern = element('pattern', {id: 'gnsportal-grid', width: 24, height: 24, patternUnits: 'userSpaceOnUse'}, defs);
        element('circle', {cx: 1.5, cy: 1.5, r: 1.1, fill: '#aebbc5'}, pattern);
        element('rect', {x: -1000, y: -1000, width: 3000, height: 2500, fill: 'url(#gnsportal-grid)', class: 'gnsportal-grid'}, view);

        for (const area of [
            {x: 26, y: 80, w: 550, h: 240, labelx: 330, name: 'AREA 0 / BACKBONE', color: '#5aa8f2'},
            {x: 25, y: 340, w: 442, h: 280, name: 'AREA 1 / VLAN 10', color: '#3cc47a'},
            {x: 630, y: 250, w: 235, h: 370, name: 'AREA 2 / DMZ', color: '#e0b13a'},
        ]) {
            element('rect', {x: area.x, y: area.y, width: area.w, height: area.h, rx: 18,
                fill: area.color, 'fill-opacity': .04, stroke: area.color, 'stroke-opacity': .38,
                'stroke-width': 1.5, 'stroke-dasharray': '7 6'}, view);
            text(view, area.name, {x: area.labelx || area.x + 17, y: area.y + 25, fill: area.color,
                class: 'gnsportal-area-label'});
        }

        for (const [from, to] of links) {
            const a = byId[from];
            const b = byId[to];
            element('line', {x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: 'gnsportal-wire'}, view);
            element('circle', {cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, r: 2.8,
                class: 'gnsportal-wire-packet'}, view);
        }

        for (const node of nodes) {
            const group = element('g', {class: 'gnsportal-node', 'data-node': node.id,
                transform: `translate(${node.x} ${node.y})`, role: 'button', tabindex: '0',
                'aria-label': `${node.id}, ${node.kind}, ${node.ip}`}, view);
            element('circle', {r: 36, class: 'gnsportal-node-halo'}, group);
            if (node.kind === 'router' || node.kind === 'cloud') {
                element('circle', {r: 25, class: 'gnsportal-node-body'}, group);
                text(group, node.kind === 'cloud' ? 'N' : 'R', {y: 8, class: 'gnsportal-node-glyph'});
            } else if (node.kind === 'switch') {
                element('rect', {x: -26, y: -17, width: 52, height: 34, rx: 8,
                    class: 'gnsportal-node-body'}, group);
                text(group, '⇄', {y: 8, class: 'gnsportal-node-glyph'});
            } else {
                element('rect', {x: -22, y: -20, width: 44, height: 36, rx: 5,
                    class: 'gnsportal-node-body'}, group);
                text(group, node.kind === 'server' ? 'S' : 'P', {y: 7, class: 'gnsportal-node-glyph'});
                element('line', {x1: -13, y1: 21, x2: 13, y2: 21, class: 'gnsportal-node-stand'}, group);
            }
            element('circle', {cx: 21, cy: -22, r: 5, class: 'gnsportal-node-status'}, group);
            text(group, node.id, {y: 54, class: 'gnsportal-node-name'});
            text(group, node.ip, {y: 69, class: 'gnsportal-node-ip'});
        }
        applyView();
        select('R1', false);
    }

    function applyView() {
        view.setAttribute('transform', `translate(${state.x} ${state.y}) scale(${state.k})`);
        scale.textContent = `${Math.round(state.k * 100)}%`;
    }

    function zoom(factor, x = 430, y = 320) {
        const next = Math.max(.55, Math.min(2.4, state.k * factor));
        const ratio = next / state.k;
        state.x = x - (x - state.x) * ratio;
        state.y = y - (y - state.y) * ratio;
        state.k = next;
        applyView();
    }

    function line(content, kind = '') {
        const entry = document.createElement('div');
        entry.className = `gnsportal-console-line ${kind}`;
        entry.textContent = content;
        output.appendChild(entry);
        while (output.childElementCount > 80) {
            output.firstElementChild.remove();
        }
        output.scrollTop = output.scrollHeight;
    }

    function select(id, announce = true) {
        state.selected = id;
        view.querySelectorAll('.gnsportal-node').forEach(item => {
            item.classList.toggle('selected', item.dataset.node === id);
        });
        title.textContent = id;
        prompt.textContent = `${id}#`;
        output.replaceChildren();
        line(`Connecting to ${id} ...`);
        line(`${id} console ready  ·  preview session`, 'info');
        line('Type help to see available commands.', 'info');
        if (announce) {
            command.focus();
        }
    }

    function run(input) {
        const value = input.trim().toLowerCase().replace(/\s+/g, ' ');
        if (!value) {
            return;
        }
        const id = state.selected;
        line(`${id}# ${input}`, 'prompt');
        if (value === 'clear' || value === 'cls') {
            output.replaceChildren();
            return;
        }
        if (value === 'help' || value === '?') {
            line('show ip interface brief   show ip route   show ip ospf neighbor');
            line('show vlan brief           ping 10.0.20.10   clear');
        } else if (value === 'show ip interface brief') {
            line('Interface              IP-Address      Status   Protocol');
            line(`GigabitEthernet0/0     ${byId[id].ip.padEnd(15)} up       up`);
            line('GigabitEthernet0/1     10.0.12.1       up       up');
        } else if (value === 'show ip route') {
            line('Codes: C - connected, O - OSPF, S - static');
            line('C  10.0.12.0/30 is directly connected, GigabitEthernet0/0');
            line('O  10.0.20.0/24 [110/20] via 10.0.12.2, GigabitEthernet0/1');
            line('O  10.0.10.0/24 [110/30] via 10.0.13.2, GigabitEthernet0/2');
        } else if (value === 'show ip ospf neighbor') {
            line('Neighbor ID      Pri   State         Address       Interface');
            line('10.0.12.2         1   FULL/DR       10.0.12.2     Gi0/1');
            line('10.0.13.2         1   FULL/DR       10.0.13.2     Gi0/2');
        } else if (value === 'show vlan brief') {
            line('VLAN Name                             Status    Ports');
            line('1    default                          active    Gi0/0');
            line('10   students                         active    Gi0/1');
            line('20   dmz                              active    Gi0/2');
        } else if (value === 'ping 10.0.20.10') {
            line('Sending 5 ICMP Echos to 10.0.20.10, timeout 2 seconds:');
            line('!!!!!');
            line('Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/4 ms');
        } else {
            line('% Unknown preview command. Type help.', 'error');
        }
    }

    draw();
    view.addEventListener('click', event => {
        const node = event.target.closest('.gnsportal-node');
        if (node && !state.drag?.moved) {
            select(node.dataset.node);
        }
    });
    view.addEventListener('keydown', event => {
        const node = event.target.closest('.gnsportal-node');
        if (node && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            select(node.dataset.node);
        }
    });
    svg.addEventListener('pointerdown', event => {
        pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
        const onNode = event.target.closest('.gnsportal-node');
        if (!onNode) {
            svg.setPointerCapture(event.pointerId);
        }
        if (pointers.size === 1 && !onNode) {
            state.drag = {x: event.clientX, y: event.clientY};
        } else if (pointers.size === 2) {
            state.drag = null;
            const [a, b] = [...pointers.values()];
            pinch = {distance: Math.hypot(a.x - b.x, a.y - b.y)};
        }
    });
    svg.addEventListener('pointermove', event => {
        if (pointers.has(event.pointerId)) {
            pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
        }
        if (pinch && pointers.size === 2) {
            const [a, b] = [...pointers.values()];
            const distance = Math.hypot(a.x - b.x, a.y - b.y);
            const box = svg.getBoundingClientRect();
            zoom(distance / pinch.distance, ((a.x + b.x) / 2 - box.left) * 1100 / box.width,
                ((a.y + b.y) / 2 - box.top) * 650 / box.height);
            pinch.distance = distance;
            return;
        }
        if (!state.drag) {
            return;
        }
        const box = svg.getBoundingClientRect();
        state.x += (event.clientX - state.drag.x) * 1100 / box.width;
        state.y += (event.clientY - state.drag.y) * 650 / box.height;
        state.drag.x = event.clientX;
        state.drag.y = event.clientY;
        applyView();
    });
    function endPointer(event) {
        pointers.delete(event.pointerId);
        state.drag = null;
        if (pointers.size < 2) {
            pinch = null;
        }
    }
    svg.addEventListener('pointerup', endPointer);
    svg.addEventListener('pointercancel', endPointer);
    svg.addEventListener('wheel', event => {
        event.preventDefault();
        const box = svg.getBoundingClientRect();
        zoom(Math.exp(-event.deltaY * .0013), (event.clientX - box.left) * 1100 / box.width,
            (event.clientY - box.top) * 650 / box.height);
    }, {passive: false});
    root.querySelectorAll('[data-zoom]').forEach(button => button.addEventListener('click', () => {
        if (button.dataset.zoom === 'fit') {
            state.x = initial.x;
            state.y = initial.y;
            state.k = initial.k;
            applyView();
        } else {
            zoom(button.dataset.zoom === 'in' ? 1.25 : .8);
        }
    }));
    form.addEventListener('submit', event => {
        event.preventDefault();
        run(command.value);
        command.value = '';
        command.focus();
    });
})();
