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
 * The "Setup" block of the lab editor: the teacher's own setup steps of a lab (`setup` of gns3lab.lab/2,
 * docs/lab-setup/README.md §4, §6a; plugin docs/lab-setup.md).
 *
 * The main path is the prompt for a neural network (promptwindow.js): the teacher describes what to set up, copies the
 * prompt, pastes the answer, sees its steps as cards with the problems the editor finds itself, asks the lab manager and
 * replaces the steps of the lab (or appends to them) in one change the editor can undo. The fields of each step and the
 * JSON of the whole list are folded under the cards for small edits. The block never talks to the lab manager itself:
 * the host checks a candidate lab (validate).
 *
 * @module     mod_gns3lab/setupform
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {answerJSON, open as openWindow} from 'mod_gns3lab/promptwindow';

/** Limits of the setup (CONTRACTS §2.6): steps, title length, bytes of `run`, timeout of a step and of all of them. */
export const LIMITS = {steps: 30, title: 120, runBytes: 8000, timeout: 120, timeoutDefault: 60, timeoutTotal: 600};
const ID_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const FIELDS = ["id", "title", "node", "run", "shell", "timeout", "config"];
const RUN_ONLY = ["run", "shell", "timeout"];
const SHELLS = ["sh", "powershell", "routeros"];
const PATH_RE = /^setup\[(\d+)\](?:\.(.+))?$/;

const clone = v => JSON.parse(JSON.stringify(v));
const isObject = v => !!v && typeof v === "object" && !Array.isArray(v);
const bytes = text => new TextEncoder().encode(String(text)).length;

/**
 * What a device of the lab takes as a step: `run` (QEMU with a guest agent, not the external connection), `config` (IOL
 * and VPCS) or "" (none). The lab manager checks it again: a QEMU image without a guest agent says so there.
 *
 * @param {Object|null} node device {kind, uplink}
 * @return {string}
 */
export const stepKind = node => !node ? "" : node.kind === "qemu" ? (node.uplink ? "" : "run")
  : node.kind === "iou" || node.kind === "vpcs" ? "config" : "";

/**
 * The shell of a `run` step on a device: RouterOS for a MikroTik guest, else `sh` (the slot runs /bin/sh).
 *
 * @param {Object|null} node device {guest}
 * @return {string}
 */
export const familyShell = node => node && node.guest === "routeros" ? "routeros" : "sh";

/**
 * The problems of a list of steps the editor sees without the lab manager (docs/lab-setup/README.md §4).
 *
 * @param {Array} list steps
 * @param {Array} nodes devices of the lab [{name, kind, guest, uplink}]
 * @param {Function} str texts
 * @return {{steps: Array<Array<string>>, general: Array<string>}} texts per step and of the whole list
 */
export const problemsOf = (list, nodes, str) => {
  const byName = new Map(nodes.map(n => [n.name, n]));
  const seen = new Set();
  let total = 0;
  const steps = list.map(step => {
    const out = [];
    if (!isObject(step)) {
      out.push(str("ed_setup_p_notobject"));
      return out;
    }
    Object.keys(step).filter(k => !FIELDS.includes(k)).forEach(k => out.push(str("ed_setup_p_unknownfield", k)));
    if (typeof step.id !== "string" || !ID_RE.test(step.id)) {
      out.push(str("ed_setup_p_badid", step.id === undefined ? "" : String(step.id)));
    } else if (seen.has(step.id)) {
      out.push(str("ed_setup_p_dupid", step.id));
    }
    seen.add(step.id);
    const title = typeof step.title === "string" ? step.title.trim() : "";
    if (!title || title.length > LIMITS.title) {
      out.push(str("ed_setup_p_title", LIMITS.title));
    }
    const node = typeof step.node === "string" ? byName.get(step.node) : null;
    const kind = stepKind(node);
    if (typeof step.node !== "string" || !step.node) {
      out.push(str("ed_setup_p_nonode"));
    } else if (!node) {
      out.push(str("ed_setup_p_unknownnode", step.node));
    } else if (!kind) {
      out.push(str("ed_setup_p_nosteps", step.node));
    }
    if (kind === "run") {
      if (step.config !== undefined) {
        out.push(str("ed_setup_p_field", "config"));
      }
      if (typeof step.run !== "string" || !step.run.trim()) {
        out.push(str("ed_setup_p_needrun"));
      } else if (bytes(step.run) > LIMITS.runBytes) {
        out.push(str("ed_setup_p_runlarge", LIMITS.runBytes));
      }
      if (step.shell !== undefined && !SHELLS.includes(step.shell)) {
        out.push(str("ed_setup_p_badshell", String(step.shell)));
      } else if (step.shell !== undefined && step.shell !== familyShell(node)) {
        out.push(str("ed_setup_p_shell", familyShell(node)));
      }
      if (step.timeout !== undefined && !(Number.isInteger(step.timeout) && step.timeout >= 1
          && step.timeout <= LIMITS.timeout)) {
        out.push(str("ed_setup_p_timeout", LIMITS.timeout));
      }
      total += Number.isInteger(step.timeout) ? step.timeout : LIMITS.timeoutDefault;
    } else if (kind === "config") {
      RUN_ONLY.filter(k => step[k] !== undefined).forEach(k => out.push(str("ed_setup_p_field", k)));
      if (typeof step.config !== "string" || !step.config.trim()) {
        out.push(str("ed_setup_p_needconfig"));
      } else if (/[^\n\x20-\x7e]/.test(step.config)) {
        out.push(str("ed_setup_p_ascii"));
      }
    }
    return out;
  });
  const general = [];
  if (list.length > LIMITS.steps) {
    general.push(str("ed_setup_p_toomany", LIMITS.steps));
  }
  if (total > LIMITS.timeoutTotal) {
    general.push(str("ed_setup_p_total", {total, max: LIMITS.timeoutTotal}));
  }
  return {steps, general};
};

/**
 * The steps of an answer of a neural network: {"setup": [...]} or a bare list, also inside a ```json block.
 *
 * @param {string} raw answer
 * @return {Array<Object>}
 * @throws {Error} when it is not a list of objects
 */
export const answerSteps = raw => {
  const value = answerJSON(raw);
  const list = Array.isArray(value) ? value : isObject(value) && Array.isArray(value.setup) ? value.setup : null;
  if (!list || !list.every(isObject)) {
    throw new Error("not a list of setup steps");
  }
  return list;
};

/**
 * The block for the lab editor.
 *
 * @param {Object} o the host: esc, str and S (strings), get() (the steps, a list) and set(list) (one undoable change),
 *     lab() with nodes [{name, image, kind, category, guest, uplink}], links [[end, end]] and segments (with the
 *     addresses of their plan), editable (the lab manager has `lab-setup`), prompt (text of prompts/setup.<lang>.md or
 *     ""), validate(list) giving a promise of the manager's problems of the lab with these steps, problems() (the
 *     manager's problems of the last check of the lab, {path, code, message}), fieldError(text) (used only when the
 *     block is not in root), root (where the block and the window of the prompt are)
 * @return {{html: Function, change: Function, action: Function, fix: Function}} the HTML of the block, a change of one
 *     of its fields and a click on one of its buttons (both true when the block took it), and the window that fixes one
 *     step that failed in an authoring run (false when the lab has no such step)
 */
export const create = o => {
  const esc = o.esc, str = o.str, S = o.S || {};
  const editable = !!o.editable;
  const steps = () => (Array.isArray(o.get()) ? o.get() : []);
  const nodes = () => (o.lab() || {}).nodes || [];
  const byName = name => nodes().find(n => n.name === name) || null;
  const memo = {task: "", answer: ""};
  const open = {manual: false, json: false};

  /* ---------- the prompt ---------- */
  const labJSON = () => {
    const ctx = o.lab() || {};
    const lab = {
      nodes: nodes().map(n => {
        const kind = stepKind(n);
        const out = {name: n.name, image: n.image || "", kind: n.kind || "", category: n.category || "", step: kind || null};
        if (kind === "run") {
          out.shell = familyShell(n);
          out.os = out.shell === "routeros" ? "MikroTik RouterOS" : "Linux";
        } else if (kind === "config") {
          out.os = n.kind === "iou" ? "Cisco IOS (IOL)" : "VPCS";
        }
        return out;
      }),
      links: Array.isArray(ctx.links) ? ctx.links : [],
    };
    if (Array.isArray(ctx.segments) && ctx.segments.length) {
      lab.segments = ctx.segments;
    }
    return JSON.stringify(lab, null, 2);
  };
  const promptText = (task, list = steps()) => String(o.prompt).replace("[[LAB]]", () => labJSON())
    .replace("[[SETUP]]", () => (list.length ? JSON.stringify({setup: list}, null, 2) : str("ed_setup_prompt_nosetup")))
    .replace("[[TASK]]", () => task || str("ed_setup_prompt_notask"));

  /* ---------- a step as a card ---------- */
  const problemLine = p => `${S["problem_" + p.code] || p.code}${p.message ? " (" + p.message + ")" : ""}`;
  const card = (step, i, problems, attr) => {
    const node = byName(step.node);
    const kind = stepKind(node) || (step.run !== undefined ? "run" : step.config !== undefined ? "config" : "");
    const meta = [step.node || "—", node ? node.image || node.kind : ""];
    if (kind === "run") {
      meta.push(step.shell || familyShell(node), str("ed_setup_seconds", Number.isInteger(step.timeout) ? step.timeout
        : LIMITS.timeoutDefault));
    } else if (kind === "config") {
      meta.push(str("ed_setup_kind_config"));
    }
    const code = kind === "config" ? step.config : step.run !== undefined ? step.run : step.config;
    return `<article class="ed-ans-script ed-setup-card${problems.length ? " bad" : ""}" ${attr}="${i}">
      <header><b>${esc(typeof step.title === "string" && step.title.trim() ? step.title : str("ed_setup_untitled"))}</b>
        <code>${esc(typeof step.id === "string" ? step.id : "")}</code></header>
      <div class="ed-ans-meta">${meta.filter(Boolean).map(x => `<span>${esc(x)}</span>`).join("")}</div>
      <pre class="mono">${esc(typeof code === "string" ? code : "")}</pre>
      ${problems.length ? `<ul class="ed-ans-problems" role="alert">${problems.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`
        : ""}
    </article>`;
  };

  /* ---------- the window of the prompt ---------- */
  const windowOf = (extra) => openWindow(Object.assign({
    esc, root: o.root, t: (key, a) => str("ed_setup_prompt" + key, a),
    parse: answerSteps,
    local: list => problemsOf(list, nodes(), str).steps,
    general: list => problemsOf(list, nodes(), str).general,
    card: (step, i, problems) => card(step, i, problems, "data-answer-step"),
    problemName: code => S["problem_" + code] || code,
  }, extra));
  /* the steps of the answer in place of those of the lab, or after them */
  const openPrompt = tab => {
    const actions = [{name: "replace", label: str("ed_setup_prompt_replace"), primary: true, run: list => o.set(clone(list))}];
    if (steps().length) {
      actions.push({
        name: "append", label: str("ed_setup_prompt_append"),
        blocked: list => {
          const ids = new Set(steps().map(x => x.id));
          const clash = list.map(x => x.id).filter(id => ids.has(id));
          return clash.length ? str("ed_setup_prompt_appendclash", clash.join(", ")) : "";
        },
        run: list => o.set(steps().concat(clone(list))),
      });
    }
    return windowOf({
      memo, tab, promptText,
      /* the answer as the whole setup of the lab: what "Replace" gives */
      validate: list => o.validate(clone(list)),
      locate: (p, list) => {
        const m = PATH_RE.exec(p.path || "");
        /* a device the lab lacks: the editor has said so in its own words */
        if (m && m[2] === "node" && p.code === "unknown_node" && list[+m[1]]) {
          return false;
        }
        return m ? {i: +m[1], field: m[2] || ""} : null;
      },
      actions,
    });
  };
  /**
   * The window that fixes one step: the task names the step, its code and the error, the answer replaces that step only.
   *
   * @param {{step: string, node: string, exitcode: number, message: string}} err setup_error of an authoring run
   * @return {boolean} false when the lab has no such step
   */
  const fix = err => {
    const list = steps();
    const at = list.findIndex(x => x.id === err.step);
    if (at < 0 || !editable || !o.prompt) {
      return false;
    }
    const step = list[at];
    const code = typeof step.run === "string" ? step.run : typeof step.config === "string" ? step.config : "";
    const fixMemo = {answer: "", task: [str("ed_setup_fix_task", {step: err.step, node: err.node || step.node || "",
      code: err.exitcode, message: err.message || str("ed_setup_fix_nomessage")}), "", str("ed_setup_fix_code"), code, "",
    str("ed_setup_fix_ask")].join("\n")};
    const chosen = answer => Math.max(0, answer.findIndex(x => x.id === err.step));
    windowOf({
      memo: fixMemo, tab: "prompt", note: str("ed_setup_fix_note", err.step), promptText,
      validate: answer => {
        const next = steps().slice();
        next[at] = clone(answer[chosen(answer)]);
        return o.validate(next);
      },
      /* the problems of the fixed step go onto its card; those of the other steps are not the answer's */
      locate: (p, answer) => {
        const m = PATH_RE.exec(p.path || "");
        return m && +m[1] === at ? {i: chosen(answer), field: m[2] || ""} : null;
      },
      actions: [{
        name: "replacestep", label: str("ed_setup_fix_apply", err.step), primary: true,
        blocked: answer => answer.length === 1 || answer.some(x => x.id === err.step) ? "" : str("ed_setup_fix_one", err.step),
        run: answer => {
          const next = steps().slice();
          const i = next.findIndex(x => x.id === err.step);
          next[i < 0 ? at : i] = clone(answer[chosen(answer)]);
          o.set(next);
        },
      }],
    });
    return true;
  };

  /* ---------- the block ---------- */
  const managerProblems = () => {
    const out = new Map();
    (o.problems ? o.problems() : []).forEach(p => {
      const m = PATH_RE.exec(p.path || "");
      if (m) {
        const list = out.get(+m[1]) || [];
        list.push(`${m[2] ? m[2] + ": " : ""}${problemLine(p)}`);
        out.set(+m[1], list);
      }
    });
    return out;
  };
  const manualHTML = list => {
    const opt = (value, label, current) =>
      `<option value="${esc(value)}"${value === current ? " selected" : ""}>${esc(label)}</option>`;
    const field = (label, control, help) => `<label class="ed-field"><span>${esc(label)}</span>${control}${
      help ? `<small>${esc(help)}</small>` : ""}</label>`;
    const input = (name, value, extra) => `<input class="form-control form-control-sm" data-stfield="${name}"
      value="${esc(value === undefined || value === null ? "" : value)}"${extra || ""}>`;
    const area = (name, value, rows) => `<textarea class="form-control form-control-sm mono" data-stfield="${name}"
      rows="${rows}" spellcheck="false">${esc(value === undefined || value === null ? "" : value)}</textarea>`;
    const takers = nodes().filter(n => stepKind(n));
    const items = list.map((step, i) => {
      const node = byName(step.node);
      const kind = stepKind(node) || (step.config !== undefined ? "config" : "run");
      const nodeOpts = (step.node && !takers.some(n => n.name === step.node) ? opt(step.node, step.node, step.node) : "")
        + takers.map(n => opt(n.name, n.name, step.node)).join("");
      const shellOpts = opt("", str("ed_setup_shell_auto"), step.shell || "")
        + ["sh", "routeros"].map(x => opt(x, x, step.shell)).join("");
      const code = kind === "run"
        ? field(str("ed_setup_run"), area("run", step.run, 5), str("ed_setup_run_help"))
          + field(str("ed_setup_shell"), `<select class="form-select form-select-sm" data-stfield="shell">${shellOpts}</select>`)
          + field(str("ed_setup_timeout"), input("timeout", step.timeout, ` inputmode="numeric" placeholder="${
            LIMITS.timeoutDefault}"`), str("ed_setup_timeout_help"))
        : field(str("ed_setup_config"), area("config", step.config, 5),
          str(node && node.kind === "vpcs" ? "ed_setup_config_help_vpcs" : "ed_setup_config_help_iou"));
      return `<fieldset class="ed-script ed-setup-step" data-step="${i}">
        <legend>${esc(step.title || step.id || str("ed_setup_untitled"))}</legend>
        ${field(str("ed_setup_node"), `<select class="form-select form-select-sm" data-stfield="node">${
          step.node ? "" : opt("", "—", "")}${nodeOpts}</select>`)}
        ${field(str("ed_setup_title"), input("title", step.title, ` maxlength="${LIMITS.title}"`))}
        ${field(str("ed_setup_id"), input("id", step.id, ' maxlength="32"'), str("ed_setup_id_help"))}
        ${code}
        <div class="ed-setup-move">
          <button type="button" class="btn btn-sm btn-outline-secondary" data-act="stepup" data-step="${i}"${
            i ? "" : " disabled"}>${esc(str("ed_setup_up"))}</button>
          <button type="button" class="btn btn-sm btn-outline-secondary" data-act="stepdown" data-step="${i}"${
            i < list.length - 1 ? "" : " disabled"}>${esc(str("ed_setup_down"))}</button>
          <button type="button" class="btn btn-sm btn-outline-danger" data-act="delstep" data-step="${i}">${
            esc(str("ed_setup_delete"))}</button>
        </div>
      </fieldset>`;
    }).join("");
    return `<details class="ed-setup-fold" data-setup-fold="manual"${open.manual ? " open" : ""}>
      <summary data-act="setupfold" data-fold-name="manual">${esc(str("ed_setup_manual"))}</summary>
      ${items}
      <button type="button" class="btn btn-sm btn-outline-primary" data-act="addstep">${esc(str("ed_setup_add"))}</button>
    </details>`;
  };
  const html = () => {
    const list = steps();
    if (!editable && !list.length) {
      return "";
    }
    const own = problemsOf(list, nodes(), str);
    const theirs = managerProblems();
    const cards = list.map((step, i) => card(step, i, own.steps[i].concat(theirs.get(i) || []), "data-setup-step")).join("");
    const general = own.general.length ? `<ul class="ed-ans-problems" role="alert">${
      own.general.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
    return `<section class="ed-setup" data-region="setup">
      <h3>${esc(str("ed_setup"))}</h3>
      <p class="muted">${esc(str(editable ? "ed_setup_help" : "ed_setup_kept"))}</p>
      ${editable && o.prompt ? `<div class="ed-prompt-open ed-setup-main">
        <button type="button" class="btn btn-sm btn-primary" data-act="setupprompt">${esc(str("ed_setup_prompt"))}</button>
        <button type="button" class="btn btn-sm btn-outline-secondary" data-act="setupanswer">${
          esc(str("ed_setup_prompt_paste"))}</button></div>` : ""}
      ${general}
      <div class="ed-setup-steps">${cards || `<p class="muted">${esc(str("ed_setup_none"))}</p>`}</div>
      ${editable ? manualHTML(list) : ""}
      <details class="ed-setup-fold" data-setup-fold="json"${open.json ? " open" : ""}>
        <summary data-act="setupfold" data-fold-name="json">${esc(str("ed_setup_json"))}</summary>
        <label class="ed-field"><textarea class="form-control form-control-sm mono" data-setupjson rows="8"
          spellcheck="false" placeholder="[]"${editable ? "" : " readonly"}>${esc(list.length ? JSON.stringify(list, null, 2)
          : "")}</textarea><small>${esc(str("ed_setup_json_help"))}</small></label>
      </details>
      <p class="ed-error" data-region="setupfielderror" role="alert" hidden></p>
    </section>`;
  };

  /* ---------- changes ---------- */
  /* a field the block refuses says why under the block itself (the checks block below has its own place) */
  const fieldError = message => {
    const p = o.root && o.root.querySelector('[data-region="setupfielderror"]');
    if (p) {
      p.textContent = message;
      p.hidden = !message;
    } else if (o.fieldError) {
      o.fieldError(message);
    }
  };
  const jsonChange = t => {
    const raw = t.value.trim();
    let list = [];
    if (raw !== "") {
      try {
        const value = JSON.parse(raw);
        list = Array.isArray(value) ? value : isObject(value) && Array.isArray(value.setup) ? value.setup : null;
      } catch (err) {
        list = null;
      }
      if (!list || !list.every(isObject)) {
        fieldError(str("ed_setup_jsoninvalid"));
        return;
      }
    }
    if (JSON.stringify(list) !== JSON.stringify(steps())) {
      o.set(list);
    }
  };
  /* one field of a step; an empty value leaves the field out, a device of another kind moves the code to its field */
  const stepChange = t => {
    const box = t.closest("[data-step]");
    const list = steps();
    const i = box ? +box.dataset.step : -1;
    if (!list[i]) {
      return;
    }
    const f = t.dataset.stfield, raw = t.value;
    const next = clone(list[i]);
    if (f === "timeout") {
      const text = raw.trim(), v = Number(text);
      if (text !== "" && !(Number.isInteger(v) && v >= 1 && v <= LIMITS.timeout)) {
        fieldError(str("ed_setup_timeoutinvalid", LIMITS.timeout));
        return;
      }
      if (text === "") {
        delete next.timeout;
      } else {
        next.timeout = v;
      }
    } else if (f === "run" || f === "config") {
      if (raw.trim() === "") {
        delete next[f];
      } else {
        next[f] = raw.replace(/\r\n?/g, "\n");
      }
    } else if (f === "node") {
      next.node = raw;
      const kind = stepKind(byName(raw));
      if (kind === "config" && next.config === undefined) {
        if (next.run !== undefined) {
          next.config = next.run;
        }
        RUN_ONLY.forEach(k => delete next[k]);
      } else if (kind === "run" && next.run === undefined && next.config !== undefined) {
        next.run = next.config;
        delete next.config;
      }
      if (kind === "run" && next.shell !== undefined && next.shell !== familyShell(byName(raw))) {
        delete next.shell; // the shell of the new device
      }
    } else if (raw.trim() === "") {
      delete next[f];
    } else {
      next[f] = raw.trim();
    }
    if (JSON.stringify(next) === JSON.stringify(list[i])) {
      return;
    }
    const all = list.slice();
    all[i] = next;
    o.set(all);
  };
  const addStep = () => {
    const list = steps();
    const taken = new Set(list.map(x => x.id));
    let k = list.length + 1;
    while (taken.has("step" + k)) {
      k++;
    }
    const node = nodes().find(n => stepKind(n));
    const step = {id: "step" + k, title: str("ed_setup_newtitle", k)};
    if (node) {
      step.node = node.name;
      step[stepKind(node)] = stepKind(node) === "run" ? "true" : "!";
    }
    open.manual = true;
    o.set(list.concat([step]));
  };
  const moveStep = (i, by) => {
    const list = steps().slice();
    const j = i + by;
    if (!list[i] || !list[j]) {
      return;
    }
    [list[i], list[j]] = [list[j], list[i]];
    o.set(list);
  };

  return {
    html,
    change: t => {
      if (t.dataset.setupjson !== undefined && editable) {
        jsonChange(t);
        return true;
      }
      if (t.dataset.stfield !== undefined && editable) {
        stepChange(t);
        return true;
      }
      return false;
    },
    action: (name, el) => {
      if ((name === "setupprompt" || name === "setupanswer") && editable && o.prompt) {
        openPrompt(name === "setupanswer" ? "answer" : "prompt");
      } else if (name === "setupfold") {
        open[el.dataset.foldName] = !open[el.dataset.foldName];
      } else if (name === "addstep" && editable) {
        addStep();
      } else if (name === "delstep" && editable) {
        o.set(steps().filter((x, i) => i !== +el.dataset.step));
      } else if ((name === "stepup" || name === "stepdown") && editable) {
        moveStep(+el.dataset.step, name === "stepup" ? -1 : 1);
      } else {
        return false;
      }
      return true;
    },
    fix,
    /**
     * The name of a place of a problem of the lab manager in the setup: the title (or id) of the step and the field.
     *
     * @param {string} path path of the problem
     * @return {string|null} null when it is not a path in the setup
     */
    where: path => {
      if (path === "setup") {
        return str("ed_setup");
      }
      const m = PATH_RE.exec(path || "");
      if (!m) {
        return null;
      }
      const step = steps()[+m[1]] || {};
      return str("ed_setup_where", (step.title || step.id || "#" + (+m[1] + 1)) + (m[2] ? " · " + m[2] : ""));
    },
  };
};
