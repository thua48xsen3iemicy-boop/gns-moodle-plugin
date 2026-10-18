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

/**
 * Console keyboard: an input line like in a messenger and a panel of commands instead of the system keyboard.
 *
 * The line sends a whole command with Enter, keeps a history (↑ ↓) and shows the ports of the device when a
 * command needs one. The panel holds the commands of the device type in groups (mod_gns3lab/commands); a
 * command without placeholders is sent at once, one with placeholders goes to the line to be filled in.
 * On Cisco IOS the keyboard follows the prompt of the device and enters the mode a command needs.
 *
 * @module     mod_gns3lab/keyboard
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {PLACEHOLDERS, ciscoSequence, parsePrompt, setFor} from 'mod_gns3lab/commands';

/** Placeholder in the input line: "<IP>", "<маска>". */
const HOLE_RE = /<[^<>\s][^<>]*>/;

/** Commands that take a port next: the port chips are offered after them. */
const PORT_CMD_RE = /(?:^|\s)(?:interface|int|passive-interface|show interfaces)\s+(?:<[^<>]*>|\S*)$/i;

/** History length per console. */
const HISTORY = 50;

/**
 * Keyboard of the console panel.
 */
export class CommandBar {
    /**
     * Constructor.
     *
     * @param {object} options
     * @param {HTMLElement} options.root console section with the keyboard regions
     * @param {object} options.strings language strings of the plugin
     * @param {Function} options.send sends a sequence to the active console
     * @param {Function} options.focusTerminal gives the focus back to the active terminal
     * @param {Function} options.toast short message over the terminal
     * @param {Function} options.onLayout called when the keyboard takes or frees space
     */
    constructor({root, strings, send, focusTerminal, toast, onLayout}) {
        this.root = root;
        this.strings = strings;
        this.send = send;
        this.focusTerminal = focusTerminal;
        this.toast = toast;
        this.onLayout = onLayout;
        this.form = root.querySelector('[data-region="inputbar"]');
        this.line = root.querySelector('[data-region="line"]');
        this.toggle = root.querySelector('[data-region="cmdtoggle"]');
        this.modeEl = root.querySelector('[data-region="mode"]');
        this.suggest = root.querySelector('[data-region="suggest"]');
        this.pad = root.querySelector('[data-region="cmdpad"]');
        this.groupsEl = root.querySelector('[data-region="groups"]');
        this.grid = root.querySelector('[data-region="grid"]');
        this.tab = null;
        this.set = null;
        this.group = {};
        this.pending = null;
        this.resume = null;
        this.readonly = false;
        this.labels = Object.fromEntries(PLACEHOLDERS.map(p => [p, `<${strings['ph_' + p] || p}>`]));
        this.toggle.setAttribute('aria-label', strings.kb_commands);
        this.toggle.title = strings.kb_commands;
        this.form.querySelector('[type="submit"]').setAttribute('aria-label', strings.kb_send);
        this.pad.setAttribute('aria-label', strings.kb_commands);
        this.suggest.setAttribute('aria-label', strings.kb_ports);
        this.bind();
    }

    /**
     * Wires the events of the line, the toggle and the panel.
     */
    bind() {
        this.form.addEventListener('submit', event => {
            event.preventDefault();
            this.submit();
        });
        this.line.addEventListener('input', () => {
            if (!this.line.value) {
                this.pending = null;
            }
            this.renderSuggest();
        });
        this.line.addEventListener('keydown', event => this.lineKey(event));
        this.line.addEventListener('focus', () => {
            this.setPad(false, false);
            this.renderSuggest();
        });
        this.line.addEventListener('click', () => this.renderSuggest());
        this.toggle.addEventListener('pointerdown', event => event.preventDefault());
        this.toggle.addEventListener('click', () => this.setPad(this.pad.hidden, true));
        // Buttons of the panel and the chips never take the focus: the system keyboard stays as it is.
        [this.pad, this.suggest].forEach(el => el.addEventListener('pointerdown', event => {
            if (event.target.closest('button')) {
                event.preventDefault();
            }
        }));
        this.groupsEl.addEventListener('click', event => {
            const b = event.target.closest('[data-group]');
            if (b && this.set) {
                this.group[this.set.id] = b.dataset.group;
                this.renderPad();
            }
        });
        this.grid.addEventListener('click', event => {
            const b = event.target.closest('[data-item]');
            if (b && this.set) {
                const [g, i] = b.dataset.item.split(':').map(Number);
                this.runItem(this.set.groups[g].items[i]);
            }
        });
        this.suggest.addEventListener('click', event => {
            const b = event.target.closest('[data-port]');
            if (b) {
                this.putPort(b.dataset.port);
            }
        });
    }

    /**
     * Follows the active console.
     *
     * @param {object|null} tab console tab: nodeId, name, nodeType, ports, term, history
     */
    setTab(tab) {
        this.tab = tab;
        this.pending = null;
        this.line.value = '';
        this.modeEl.textContent = '';
        this.set = tab ? setFor(tab.nodeType) : null;
        this.line.placeholder = tab ? this.strings.kb_placeholder.replace('{$a}', tab.name) : '';
        this.toggle.hidden = !this.set;
        if (!this.set) {
            this.setPad(false, false);
        }
        this.renderPad();
        this.renderSuggest();
        this.updatePrompt();
    }

    /**
     * Observer mode: no input at all.
     *
     * @param {boolean} readonly observer
     */
    setReadonly(readonly) {
        this.readonly = readonly;
        this.form.hidden = readonly;
        if (readonly) {
            this.setPad(false, false);
            this.suggest.hidden = true;
        }
    }

    /**
     * Shows or hides the command panel; it takes the place of the system keyboard.
     *
     * @param {boolean} open show the panel
     * @param {boolean} moveFocus focus the line when the panel closes, blur it when it opens
     */
    setPad(open, moveFocus) {
        open = open && !!this.set && !this.readonly;
        const changed = this.pad.hidden === open;
        this.pad.hidden = !open;
        this.toggle.setAttribute('aria-expanded', String(open));
        this.toggle.classList.toggle('on', open);
        this.toggle.textContent = open ? '⌨' : '☰';
        const label = open ? this.strings.kb_keyboard : this.strings.kb_commands;
        this.toggle.title = label;
        this.toggle.setAttribute('aria-label', label);
        if (moveFocus) {
            if (open) {
                this.line.blur();
                if (document.activeElement && this.root.contains(document.activeElement)) {
                    document.activeElement.blur();
                }
            } else {
                this.line.focus();
            }
        }
        if (changed) {
            this.onLayout();
        }
    }

    /**
     * Whether the command panel is open.
     *
     * @returns {boolean}
     */
    isPadOpen() {
        return !this.pad.hidden;
    }

    /**
     * Draws the groups and the commands of the active set.
     */
    renderPad() {
        this.groupsEl.textContent = '';
        this.grid.textContent = '';
        if (!this.set) {
            return;
        }
        const current = this.group[this.set.id] || this.set.groups[0].key;
        this.set.groups.forEach((group, g) => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'cgroup';
            chip.dataset.group = group.key;
            chip.setAttribute('role', 'tab');
            chip.setAttribute('aria-selected', String(group.key === current));
            chip.textContent = this.strings['kbg_' + group.key] || group.key;
            this.groupsEl.appendChild(chip);
            if (group.key !== current) {
                return;
            }
            group.items.forEach((item, i) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'kcmd';
                b.dataset.item = `${g}:${i}`;
                const text = this.fill(item.cmd);
                b.textContent = text;
                if (HOLE_RE.test(text)) {
                    b.classList.add('partial');
                }
                this.grid.appendChild(b);
            });
        });
    }

    /**
     * Command text with the placeholders shown as "<IP>", "<маска>".
     *
     * @param {string} cmd command template
     * @returns {string}
     */
    fill(cmd) {
        return cmd.replace(/\{(\w+)\}/g, (m, p) => this.labels[p] || m);
    }

    /**
     * Runs a command of the panel: at once, or into the line when it has placeholders.
     *
     * An interface or routing command outside its mode first asks for the interface (or the process);
     * once that is sent, the command comes back to the line.
     *
     * @param {object} item command
     * @param {boolean} [resumed] the command comes back after its mode was entered: do not check the mode again
     */
    runItem(item, resumed = false) {
        if (!resumed && this.set && this.set.prompt && (item.mode === 'if' || item.mode === 'router')) {
            const prompt = this.prompt();
            if (prompt && prompt.mode !== item.mode) {
                this.resume = item;
                this.toast(item.mode === 'if' ? this.strings.kb_needif : this.strings.kb_needrouter);
                const enter = item.mode === 'if' ? 'interface {port}' : 'router ospf {n}';
                this.runItem({cmd: enter, mode: 'config'});
                return;
            }
        }
        const text = this.fill(item.cmd);
        if (HOLE_RE.test(text)) {
            this.pending = item;
            this.line.value = text;
            this.setPad(false, false);
            this.line.focus();
            this.selectHole();
            this.renderSuggest();
            return;
        }
        this.execute(item, text);
    }

    /**
     * Sends a command, entering the Cisco mode it needs.
     *
     * @param {object|null} item command of the panel, null for typed text
     * @param {string} text command text
     */
    execute(item, text) {
        if (this.set && this.set.prompt && item) {
            const result = ciscoSequence(item, text, this.prompt());
            if (result.need) {
                this.runItem(item);
                return;
            }
            this.send(result.seq);
        } else {
            this.send(text + '\r');
        }
        this.remember(text);
        const next = this.resume;
        this.resume = null;
        if (next && item && /^(interface|router) /.test(item.cmd)) {
            // The device answers with the new prompt a moment later; the command does not wait for it.
            setTimeout(() => this.runItem(next, true), 250);
        }
    }

    /**
     * Sends the line: Enter on an empty line is a plain Enter for the device.
     */
    submit() {
        if (this.readonly || !this.tab) {
            return;
        }
        const text = this.line.value.trim();
        const hole = HOLE_RE.exec(text);
        if (hole) {
            this.toast(this.strings.kb_fill.replace('{$a}', hole[0]));
            this.selectHole();
            return;
        }
        const item = this.pending;
        this.pending = null;
        this.line.value = '';
        this.renderSuggest();
        if (text === '') {
            this.send('\r');
            return;
        }
        this.execute(item, text);
    }

    /**
     * Keys of the line: history and leaving the line.
     *
     * @param {KeyboardEvent} event key
     */
    lineKey(event) {
        const tab = this.tab;
        if (!tab) {
            return;
        }
        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            const history = tab.history;
            if (!history.length) {
                return;
            }
            event.preventDefault();
            const step = event.key === 'ArrowUp' ? -1 : 1;
            tab.historyPos = Math.min(history.length, Math.max(0, (tab.historyPos ?? history.length) + step));
            this.line.value = history[tab.historyPos] || '';
            this.pending = null;
            this.renderSuggest();
        } else if (event.key === 'Escape') {
            event.preventDefault();
            this.line.value = '';
            this.pending = null;
            this.resume = null;
            this.renderSuggest();
            this.focusTerminal();
        } else if (event.key === 'Tab' && HOLE_RE.test(this.line.value)) {
            // Tab jumps to the next placeholder.
            event.preventDefault();
            this.selectHole();
        }
    }

    /**
     * Keeps a sent command in the history of the console.
     *
     * @param {string} text command
     */
    remember(text) {
        const history = this.tab.history;
        if (history[history.length - 1] !== text) {
            history.push(text);
            if (history.length > HISTORY) {
                history.shift();
            }
        }
        this.tab.historyPos = history.length;
    }

    /**
     * Selects the first placeholder of the line, so typing replaces it.
     */
    selectHole() {
        const m = HOLE_RE.exec(this.line.value);
        if (m) {
            this.line.setSelectionRange(m.index, m.index + m[0].length);
        }
    }

    /**
     * Puts a port into the line: instead of the selected or first port placeholder, or after the command.
     *
     * @param {string} port port name
     */
    putPort(port) {
        const value = this.line.value;
        const hole = this.labels.port;
        const at = value.indexOf(hole);
        if (at >= 0) {
            this.line.value = value.slice(0, at) + port + value.slice(at + hole.length);
        } else {
            this.line.value = value.replace(/\S*$/, '') + port;
        }
        this.line.focus();
        if (HOLE_RE.test(this.line.value)) {
            this.selectHole();
        } else {
            this.line.setSelectionRange(this.line.value.length, this.line.value.length);
        }
        this.renderSuggest();
    }

    /**
     * Shows the ports of the device when the command in the line needs one.
     */
    renderSuggest() {
        const ports = (this.tab && this.set && this.set.prompt && this.tab.ports) || [];
        const value = this.line.value;
        const wants = ports.length && !this.readonly && (value.includes(this.labels.port) || PORT_CMD_RE.test(value));
        this.suggest.hidden = !wants;
        if (!wants) {
            this.suggest.textContent = '';
            return;
        }
        if (this.suggest.dataset.node === this.tab.nodeId && this.suggest.childElementCount) {
            return;
        }
        this.suggest.textContent = '';
        this.suggest.dataset.node = this.tab.nodeId;
        ports.forEach(port => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'cport' + (port.peer ? ' linked' : '');
            b.dataset.port = port.name;
            const name = document.createElement('b');
            name.textContent = port.name;
            b.appendChild(name);
            if (port.peer) {
                const peer = document.createElement('span');
                peer.textContent = '→ ' + port.peer;
                b.appendChild(peer);
            }
            this.suggest.appendChild(b);
        });
    }

    /**
     * The Cisco prompt on the cursor line of the active terminal.
     *
     * @returns {object|null} parsed prompt
     */
    prompt() {
        const term = this.tab && this.tab.term;
        if (!term) {
            return null;
        }
        const buffer = term.buffer.active;
        const line = buffer.getLine(buffer.baseY + buffer.cursorY);
        return line ? parsePrompt(line.translateToString(true).slice(0, buffer.cursorX + 1)) : null;
    }

    /**
     * Shows the mode of the device next to the line, for Cisco IOS.
     */
    updatePrompt() {
        const prompt = this.set && this.set.prompt ? this.prompt() : null;
        if (prompt) {
            this.modeEl.textContent = prompt.text;
            this.modeEl.dataset.mode = prompt.mode;
        }
        // The last known mode stays while the device prints something else.
        this.modeEl.hidden = !(this.set && this.set.prompt && this.modeEl.textContent) || this.readonly;
    }
}
