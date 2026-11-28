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
 * Editor of an auto-checks document gns3lab.checks/1 (docs/autochecks.md §12).
 *
 * The same block in two places: the settings form of an activity, where the checks of the activity live (`init`), and
 * the lab editor, which still edits the checks of a lab for a lab manager without `activity-checks` (`create`). The
 * block shows the default network check, the teacher's scripts as fields, the whole document as JSON and the window of
 * the prompt for a neural network. It never talks to the lab manager itself: the host checks a document (`validate`).
 *
 * @module     mod_gns3lab/checksform
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';

const CHECKS_FORMAT = "gns3lab.checks/1";
const SCRIPT_SHELLS = ["sh", "powershell", "routeros"];
const SCRIPT_GROUPS = ["host", "l1", "l2", "addressing", "gateways", "routing"];
const SCRIPT_CLASSES = ["major", "critical", "minor", "note"];

const clone = v => v === null || v === undefined ? null : JSON.parse(JSON.stringify(v));

/**
 * The block for a host page.
 *
 * @param {Object} o the host: esc, str and S (strings), get() and set(doc) of the document (null: none), lab() with
 *     nodes [{name, image, kind, category, scripts}], links [[end, end]] and segments, editable, withScripts, withNeeds,
 *     prompt (text of prompts/checks.<lang>.md or ""), validate(doc) giving a promise of the manager's problems,
 *     fieldError(text), root (where the window of the prompt goes), helpKey (the help under the JSON)
 * @return {{html: Function, change: Function, action: Function}} the HTML of the block, a change of one of its fields
 *     and a click on one of its buttons (both true when the block took it)
 */
export const create = o => {
  const esc = o.esc, str = o.str, S = o.S || {};
  const withScripts = !!o.editable && !!o.withScripts;
  const withNeeds = withScripts && !!o.withNeeds;
  const labNodes = () => (o.lab() || {}).nodes || [];
  /* The document is the host's. Reads see it as it is; a change works on a copy and hands it back with set(). */
  let draft = null;
  const st = {lab: {}, promptTask: "", promptAnswer: ""};
  Object.defineProperty(st.lab, "checks", {
    get: () => draft ? draft.v : o.get(),
    set: v => {
      if (draft) {
        draft.v = v;
      }
    },
  });
  const change = fn => {
    draft = {v: clone(o.get())};
    try {
      fn();
    } finally {
      const v = draft.v;
      draft = null;
      o.set(v);
    }
  };

  /* nodes a script may run on: QEMU with a guest agent, not the ISP (the manager checks it again) */
  const scriptNodes = () => labNodes().filter(n => n.scripts);

  /* the teacher's scripts (docs/autochecks/README.md §7): one fieldset each, the same document as the JSON below */
  const scriptsHTML = () => {
    const list = st.lab.checks && Array.isArray(st.lab.checks.scripts) ? st.lab.checks.scripts : [];
    if (!withScripts && !list.length) {
      return "";
    }
    const off = withScripts ? "" : " disabled";
    const opt = (value, label, current) =>
      `<option value="${esc(value)}"${value === current ? " selected" : ""}>${esc(label)}</option>`;
    const field = (label, control, help) => `<label class="ed-field"><span>${esc(label)}</span>${control}${
      help ? `<small>${esc(help)}</small>` : ""}</label>`;
    const input = (name, value, extra) => `<input class="form-control form-control-sm" data-sfield="${name}"
      value="${esc(value === undefined || value === null ? "" : value)}"${extra || ""}${off}>`;
    const area = (name, value, rows, extra) => `<textarea class="form-control form-control-sm mono" data-sfield="${name}"
      rows="${rows}" spellcheck="false"${extra || ""}${off}>${esc(value)}</textarea>`;
    const nodes = scriptNodes();
    /* the scripts above this one, as boxes: a script waits only for earlier ones */
    const needsField = (sc, i) => {
      const own = Array.isArray(sc.needs) ? sc.needs : [];
      const earlier = list.slice(0, i).map(x => x.id).filter(Boolean);
      const ids = earlier.concat(own.filter(x => !earlier.includes(x)));
      if (!ids.length || (!withNeeds && !own.length)) {
        return "";
      }
      const offNeeds = withNeeds ? "" : " disabled";
      const boxes = ids.map(id => `<label class="ed-need"><input type="checkbox" data-sfield="needs" value="${esc(id)}"${
        own.includes(id) ? " checked" : ""}${offNeeds}> ${esc(id)}</label>`).join(" ");
      return `<div class="ed-field"><span>${esc(str("ed_script_needs"))}</span><div>${boxes}</div>
        <small>${esc(str("ed_script_needs_help"))}</small></div>`;
    };
    const items = list.map((sc, i) => {
      const e = sc.expect || {};
      const nodeOpts = (sc.node && !nodes.some(n => n.name === sc.node) ? opt(sc.node, sc.node, sc.node) : "")
        + nodes.map(n => opt(n.name, n.name, sc.node)).join("");
      const shellOpts = opt("", str("ed_script_shell_auto"), sc.shell || "")
        + SCRIPT_SHELLS.map(x => opt(x, x, sc.shell)).join("");
      const groupOpts = SCRIPT_GROUPS.map(x => opt(x, x, sc.group || "host")).join("");
      const classOpts = SCRIPT_CLASSES.map(x => opt(x, str("chk_class_" + x), sc.class || "major")).join("");
      return `<fieldset class="ed-script" data-script="${i}">
        <legend>${esc(sc.title || sc.id || str("ed_script_untitled"))}</legend>
        ${field(str("ed_script_title"), input("title", sc.title, ' maxlength="120"'))}
        ${field(str("ed_script_id"), input("id", sc.id, ' maxlength="32"'), str("ed_script_id_help"))}
        ${field(str("ed_script_node"), `<select class="form-select form-select-sm" data-sfield="node"${off}>${
          sc.node ? "" : opt("", "—", "")}${nodeOpts}</select>`)}
        ${field(str("ed_script_shell"), `<select class="form-select form-select-sm" data-sfield="shell"${off}>${
          shellOpts}</select>`)}
        ${field(str("ed_script_run"), area("run", sc.run || "", 4), str("ed_script_run_help"))}
        ${field(str("ed_script_timeout"), input("timeout", sc.timeout, ' inputmode="numeric" placeholder="20"'))}
        ${field(str("ed_script_exit"), input("exit", e.exit, ' inputmode="numeric"'), str("ed_script_expect_help"))}
        ${field(str("ed_script_match"), input("match", e.match, ' maxlength="500" spellcheck="false"'),
          str("ed_script_match_help") + (withNeeds ? " " + str("ed_script_capture_help") : ""))}
        ${field(str("ed_script_contains"), area("contains", (e.contains || []).join("\n"), 2))}
        ${field(str("ed_script_notcontains"), area("not_contains", (e.not_contains || []).join("\n"), 2),
          str("ed_script_lines_help"))}
        ${field(str("ed_script_group"), `<select class="form-select form-select-sm" data-sfield="group"${off}>${
          groupOpts}</select>`)}
        ${field(str("ed_script_class"), `<select class="form-select form-select-sm" data-sfield="class"${off}>${
          classOpts}</select>`)}
        ${needsField(sc, i)}
        ${withScripts ? `<button type="button" class="btn btn-sm btn-outline-danger" data-act="delscript"
          data-script="${i}">${esc(str("ed_script_delete"))}</button>` : ""}
      </fieldset>`;
    }).join("");
    return `<h3>${esc(str("ed_scripts"))}</h3>
      <p class="muted">${esc(str(withScripts ? "ed_scripts_help" : "ed_scripts_kept"))}</p>${items}
      ${withScripts ? `<button type="button" class="btn btn-sm btn-outline-primary" data-act="addscript">${
        esc(str("ed_script_add"))}</button>` : ""}
      ${withScripts && o.prompt ? `<div class="ed-prompt-open">
        <button type="button" class="btn btn-sm btn-outline-secondary" data-act="checksprompt">${
          esc(str("ed_script_prompt"))}</button>
        <button type="button" class="btn btn-sm btn-outline-secondary" data-act="checksanswer">${
          esc(str("ed_script_prompt_paste"))}</button></div>` : ""}`;
  };

  /* The prompt for a neural network (docs/autochecks/README.md §7.8): the text of prompts/checks.<lang>.md with the lab,
     its scripts and the teacher's task in it. The answer replaces the scripts of the lab; the manager checks them. */
  const promptLab = () => {
    const ctx = o.lab();
    const lab = {
      nodes: labNodes().map(n => ({name: n.name, image: n.image || "", kind: n.kind || "", category: n.category || "",
        scripts: !!n.scripts})),
      links: Array.isArray(ctx.links) ? ctx.links : [],
    };
    if (Array.isArray(ctx.segments) && ctx.segments.length) {
      lab.segments = ctx.segments;
    }
    return JSON.stringify(lab, null, 2);
  };
  const promptText = task => {
    const list = st.lab.checks && Array.isArray(st.lab.checks.scripts) ? st.lab.checks.scripts : [];
    const checks = list.length ? JSON.stringify({scripts: list}, null, 2) : str("ed_script_prompt_nochecks");
    return String(o.prompt).replace("[[LAB]]", () => promptLab()).replace("[[CHECKS]]", () => checks)
      .replace("[[TASK]]", () => task || str("ed_script_prompt_notask"));
  };
  /* the scripts of an answer: {"scripts": [...]} or a bare list, also inside a ```json block */
  const answerScripts = raw => {
    const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/);
    const value = JSON.parse((fenced ? fenced[1] : raw).trim());
    const list = Array.isArray(value) ? value : value && Array.isArray(value.scripts) ? value.scripts : null;
    if (!list || !list.every(x => x && typeof x === "object" && !Array.isArray(x))) {
      throw new Error("not a list of scripts");
    }
    return list;
  };
  const copyText = async area => {
    try {
      await navigator.clipboard.writeText(area.value);
    } catch (e) {
      area.select(); // no clipboard API (http, an old browser): the selection and the older command
      if (!document.execCommand("copy")) {
        throw e;
      }
    }
  };
  /* problems of a candidate answer the editor sees without the manager: devices, ids, needs */
  const localProblems = list => {
    const allowed = new Set(scriptNodes().map(n => n.name));
    const names = new Set(labNodes().map(n => n.name));
    const seen = new Set();
    return list.map(sc => {
      const out = [];
      if (sc.node && !names.has(sc.node)) {
        out.push(str("ed_script_prompt_unknownnode", sc.node));
      } else if (sc.node && !allowed.has(sc.node)) {
        out.push(str("ed_script_prompt_noscripts", sc.node));
      }
      if (seen.has(sc.id)) {
        out.push(str("ed_script_prompt_dupid", sc.id));
      }
      (Array.isArray(sc.needs) ? sc.needs : []).filter(x => !seen.has(x))
        .forEach(x => out.push(str("ed_script_prompt_badneeds", x)));
      seen.add(sc.id);
      return out;
    });
  };
  /* one script of an answer as a card: what it checks, where, the code and the conditions */
  const answerCard = (sc, i, problems) => {
    const e = sc.expect && typeof sc.expect === "object" ? sc.expect : {};
    const meta = [sc.node, sc.shell || str("ed_script_shell_auto"), sc.group || "host",
      str("chk_class_" + (CLASSES_OK.includes(sc.class) ? sc.class : "major")),
      str("ed_script_prompt_seconds", sc.timeout || 20)];
    if (Array.isArray(sc.needs) && sc.needs.length) {
      meta.push(str("ed_script_prompt_needs", sc.needs.join(", ")));
    }
    const conds = [];
    if (e.exit !== undefined) {
      conds.push(`${str("ed_script_exit")}: ${e.exit}`);
    }
    if (e.match !== undefined) {
      conds.push(`${str("ed_script_match")}: ${e.match}`);
    }
    (e.contains || []).forEach(x => conds.push(`${str("ed_script_contains")}: ${x}`));
    (e.not_contains || []).forEach(x => conds.push(`${str("ed_script_notcontains")}: ${x}`));
    if (!conds.length) {
      conds.push(str("ed_script_prompt_noexpect"));
    }
    return `<article class="ed-ans-script${problems.length ? " bad" : ""}" data-answer-script="${i}">
      <header><b>${esc(sc.title || str("ed_script_untitled"))}</b> <code>${esc(sc.id || "")}</code></header>
      <div class="ed-ans-meta">${meta.map(x => `<span>${esc(x)}</span>`).join("")}</div>
      <pre class="mono">${esc(sc.run || "")}</pre>
      <ul class="ed-ans-expect">${conds.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
      ${problems.length ? `<ul class="ed-ans-problems" role="alert">${problems.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    </article>`;
  };
  const CLASSES_OK = ["critical", "major", "minor", "note"];
  /* the window of the prompt (tab 1) and of the answer (tab 2); the task and the answer stay for the next opening */
  const openPrompt = tab => {
    const dialog = document.createElement("dialog");
    dialog.className = "ed-prompt-dialog";
    dialog.setAttribute("aria-label", str("ed_script_prompt"));
    dialog.innerHTML = `<div class="ed-prompt-head">
        <div class="ed-prompt-tabs" role="tablist">
          <button type="button" role="tab" data-prompt-tab="prompt">${esc(str("ed_script_prompt_tab_prompt"))}</button>
          <button type="button" role="tab" data-prompt-tab="answer">${esc(str("ed_script_prompt_tab_answer"))}</button>
        </div>
        <button type="button" class="gbtn" data-cancel>${esc(str("ed_script_prompt_close"))}</button>
      </div>
      <section class="ed-prompt-pane" data-pane="prompt">
        <p class="muted">${esc(str("ed_script_prompt_help"))}</p>
        <label>${esc(str("ed_script_prompt_task"))}<textarea data-prompt-task rows="3"
          placeholder="${esc(str("ed_script_prompt_task_placeholder"))}"></textarea></label>
        <label class="grow">${esc(str("ed_script_prompt_text"))}<textarea data-prompt-text class="mono" readonly
          spellcheck="false"></textarea></label>
        <div class="ed-prompt-actions"><button class="gbtn primary" type="button" data-prompt-copy>${
          esc(str("ed_script_prompt_copy"))}</button><span data-prompt-copied role="status"></span>
          <button class="gbtn" type="button" data-prompt-tab="answer">${esc(str("ed_script_prompt_next"))}</button></div>
      </section>
      <section class="ed-prompt-pane" data-pane="answer" hidden>
        <div class="ed-prompt-split">
          <label class="grow">${esc(str("ed_script_prompt_answer"))}<textarea data-prompt-answer class="mono"
            spellcheck="false" placeholder="${esc(str("ed_script_prompt_answer_placeholder"))}"></textarea></label>
          <div class="ed-ans-review"><h3>${esc(str("ed_script_prompt_review"))}</h3>
            <p data-answer-status role="status"></p><div data-answer-list></div></div>
        </div>
        <div class="ed-prompt-actions">
          <button class="gbtn" type="button" data-prompt-validate disabled>${esc(str("ed_script_prompt_validate"))}</button>
          <button class="gbtn primary" type="button" data-prompt-apply disabled>${esc(str("ed_script_prompt_apply"))}</button>
          <span data-prompt-checked role="status"></span>
        </div>
      </section>`;
    const $d = sel => dialog.querySelector(sel);
    const task = $d("[data-prompt-task]"), text = $d("[data-prompt-text]"), answer = $d("[data-prompt-answer]");
    const status = $d("[data-answer-status]"), checked = $d("[data-prompt-checked]");
    let parsed = null, managerProblems = null;
    const show = name => {
      dialog.querySelectorAll("[data-pane]").forEach(p => {
        p.hidden = p.dataset.pane !== name;
      });
      dialog.querySelectorAll('[role="tab"]').forEach(b => b.setAttribute("aria-selected", String(b.dataset.promptTab === name)));
      (name === "answer" ? answer : task).focus();
    };
    const fill = () => {
      text.value = promptText(task.value.trim());
      $d("[data-prompt-copied]").textContent = "";
    };
    /* the parse of the answer and the problems of each script: ours at once, the manager's after "Check" */
    const review = () => {
      const raw = answer.value.trim();
      parsed = null;
      if (!raw) {
        status.textContent = str("ed_script_prompt_empty");
        $d("[data-answer-list]").innerHTML = "";
      } else {
        try {
          parsed = answerScripts(raw);
        } catch (e) {
          status.textContent = str("ed_script_prompt_badanswer");
          $d("[data-answer-list]").innerHTML = "";
        }
      }
      if (parsed) {
        const local = localProblems(parsed);
        const other = [];
        (managerProblems || []).forEach(p => {
          const m = /^checks\.scripts\[(\d+)\](?:\.(.+))?$/.exec(p.path || "");
          /* a device of a script the lab lacks: the editor has said so in its own words */
          if (m && m[2] === "node" && p.code === "unknown_node" && parsed[+m[1]]) {
            return;
          }
          const line = `${m && m[2] ? m[2] + ": " : (m ? "" : (p.path || "") + ": ")}${S["problem_" + p.code] || p.code}${
            p.message ? " (" + p.message + ")" : ""}`;
          if (m && local[+m[1]]) {
            local[+m[1]].push(line);
          } else {
            other.push(line);
          }
        });
        status.textContent = str("ed_script_prompt_count", parsed.length);
        $d("[data-answer-list]").innerHTML = (other.length ? `<ul class="ed-ans-problems" role="alert">${
          other.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "") + parsed.map((sc, i) => answerCard(sc, i, local[i])).join("");
      }
      $d("[data-prompt-validate]").disabled = !parsed;
      $d("[data-prompt-apply]").disabled = !parsed;
    };
    task.value = st.promptTask || "";
    answer.value = st.promptAnswer || "";
    task.addEventListener("input", () => {
      st.promptTask = task.value;
      fill();
    });
    answer.addEventListener("input", () => {
      st.promptAnswer = answer.value;
      managerProblems = null;
      checked.textContent = "";
      review();
    });
    fill();
    review();
    dialog.querySelectorAll("[data-prompt-tab]").forEach(b => {
      b.onclick = () => show(b.dataset.promptTab);
    });
    $d("[data-prompt-copy]").onclick = () => copyText(text)
      .then(() => {
        $d("[data-prompt-copied]").textContent = str("ed_script_prompt_copied");
      })
      .catch(() => {
        $d("[data-prompt-copied]").textContent = str("ed_script_prompt_nocopy");
      });
    const candidate = () => {
      const checks = Object.assign({format: CHECKS_FORMAT}, st.lab.checks || {});
      if (parsed.length) {
        checks.scripts = parsed;
      } else {
        delete checks.scripts;
      }
      return checks;
    };
    /* the manager checks the lab with these scripts, the lab itself stays as it is */
    $d("[data-prompt-validate]").onclick = async() => {
      if (!parsed) {
        return;
      }
      checked.textContent = str("ed_script_prompt_validating");
      try {
        managerProblems = await o.validate(candidate());
        checked.textContent = managerProblems.length ? str("ed_script_prompt_invalid", managerProblems.length)
          : str("ed_script_prompt_valid");
        review();
      } catch (err) {
        checked.textContent = str("ed_script_prompt_nocheck", err && err.message ? err.message : String(err));
      }
    };
    $d("[data-prompt-apply]").onclick = () => {
      if (!parsed) {
        return;
      }
      const checks = candidate();
      change(() => {
        st.lab.checks = Object.keys(checks).length > 1 ? checks : null;
      });
      st.promptAnswer = "";
      dialog.close();
    };
    $d("[data-cancel]").onclick = () => dialog.close();
    dialog.addEventListener("close", () => dialog.remove());
    (o.root || document.body).append(dialog);
    dialog.showModal();
    show(tab === "answer" ? "answer" : "prompt");
  };

  /* the `checks` document of the lab (docs/autochecks/README.md §4) as JSON; the lab manager checks it on "Check" */
  const checksHTML = () => {
    if (!o.editable && !st.lab.checks) {
      return "";
    }
    const text = st.lab.checks ? JSON.stringify(st.lab.checks, null, 2) : "";
    /* the default network check (docs/autochecks/README.md §4.6): on unless the lab says `network: "off"` */
    const off = !!st.lab.checks && st.lab.checks.network === "off";
    const network = `<label class="ed-field"><span>${esc(str("ed_network"))}</span>
        <select class="form-select form-select-sm" data-network${o.editable ? "" : " disabled"}>
          <option value="auto"${off ? "" : " selected"}>${esc(str("ed_network_auto"))}</option>
          <option value="off"${off ? " selected" : ""}>${esc(str("ed_network_off"))}</option>
        </select><small>${esc(str("ed_network_help"))}</small></label>`;
    return `<h3>${esc(str("ed_checks"))}</h3>
      ${network}${scriptsHTML()}
      <label class="ed-field"><textarea class="form-control form-control-sm mono" data-checks rows="8" spellcheck="false"
        placeholder='{"format": "${CHECKS_FORMAT}"}'${o.editable ? "" : " readonly"}>${esc(text)}</textarea>
        <small>${esc(str(o.editable ? o.helpKey || "ed_checks_help" : "ed_checkskept"))}</small></label>
      <p class="ed-error" data-region="fielderror" role="alert" hidden></p>`;
  };

  const intField = (t, min, max) => {
    const raw = t.value.trim();
    if (raw === "") {
      return null;
    }
    const v = Number(raw);
    return Number.isInteger(v) && v >= min && v <= max ? v : undefined;
  };
  const checksChange = t => {
    const raw = t.value.trim();
    let value = null;
    if (raw !== "") {
      try {
        value = JSON.parse(raw);
      } catch (err) {
        value = undefined;
      }
      if (!value || typeof value !== "object" || Array.isArray(value) || value.format !== CHECKS_FORMAT) {
        o.fieldError(str("ed_checksinvalid", CHECKS_FORMAT));
        return;
      }
    }
    if (JSON.stringify(value) === JSON.stringify(st.lab.checks)) {
      return;
    }
    change(() => {
      st.lab.checks = value;
    });
  };
  /* one field of a teacher's script; a default value leaves the field out, as the manager would fill it in */
  const setOrDrop = (obj, key, value) => {
    if (value === null) {
      delete obj[key];
    } else {
      obj[key] = value;
    }
  };
  const DEFAULTS = {group: "host", class: "major", shell: ""};
  const scriptChange = t => {
    const box = t.closest("[data-script]");
    const list = st.lab.checks && st.lab.checks.scripts;
    const i = box ? +box.dataset.script : -1;
    if (!Array.isArray(list) || !list[i]) {
      return;
    }
    const f = t.dataset.sfield, raw = t.value;
    let edit;
    if (f === "timeout" || f === "exit") {
      const v = f === "timeout" ? intField(t, 1, 60) : intField(t, -2147483648, 2147483647);
      if (v === undefined) {
        o.fieldError(str(f === "timeout" ? "ed_script_timeoutinvalid" : "ed_script_exitinvalid"));
        return;
      }
      edit = sc => setOrDrop(f === "exit" ? sc.expect : sc, f, v);
    } else if (f === "contains" || f === "not_contains") {
      const lines = raw.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      edit = sc => setOrDrop(sc.expect, f, lines.length ? lines : null);
    } else if (f === "match") {
      edit = sc => setOrDrop(sc.expect, f, raw === "" ? null : raw);
    } else if (f === "needs") {
      if (!withNeeds) {
        return;
      }
      const ids = Array.from(box.querySelectorAll('[data-sfield="needs"]:checked'), x => x.value);
      edit = sc => setOrDrop(sc, f, ids.length ? ids : null);
    } else if (f === "run") {
      edit = sc => setOrDrop(sc, f, raw.trim() === "" ? null : raw);
    } else if (f in DEFAULTS) {
      edit = sc => setOrDrop(sc, f, raw === DEFAULTS[f] ? null : raw);
    } else {
      edit = sc => setOrDrop(sc, f, raw.trim() === "" ? null : raw.trim());
    }
    const next = JSON.parse(JSON.stringify(list[i]));
    next.expect = next.expect || {};
    edit(next);
    if (!Object.keys(next.expect).length) {
      delete next.expect; // no condition left: the manager checks the exit code 0
    }
    if (JSON.stringify(next) === JSON.stringify(list[i])) {
      return;
    }
    change(() => {
      st.lab.checks.scripts[i] = next;
    });
  };
  const addScript = () => {
    change(() => {
      const checks = st.lab.checks || (st.lab.checks = {format: CHECKS_FORMAT});
      const list = checks.scripts || (checks.scripts = []);
      const taken = new Set(list.map(x => x.id));
      let k = list.length + 1;
      while (taken.has("s" + k)) {
        k++;
      }
      const node = scriptNodes()[0];
      list.push(Object.assign({id: "s" + k, title: str("ed_script_newtitle", k)}, node ? {node: node.name} : {}));
    });
  };
  const deleteScript = i => {
    change(() => {
      const checks = st.lab.checks;
      checks.scripts.splice(i, 1);
      if (!checks.scripts.length) {
        delete checks.scripts;
      }
      if (Object.keys(checks).length === 1) {
        st.lab.checks = null; // only "format" left: the lab has no checks document
      }
    });
  };
  const networkChange = t => {
    const checks = Object.assign({format: CHECKS_FORMAT}, st.lab.checks || {});
    if (t.value === "off") {
      checks.network = "off";
    } else {
      delete checks.network;
    }
    const value = Object.keys(checks).length > 1 ? checks : null;
    if (JSON.stringify(value) === JSON.stringify(st.lab.checks)) {
      return;
    }
    change(() => {
      st.lab.checks = value;
    });
  };

  return {
    html: checksHTML,
    change: t => {
      if (t.dataset.checks !== undefined && o.editable) {
        checksChange(t);
        return true;
      }
      if (t.dataset.network !== undefined && o.editable) {
        networkChange(t);
        return true;
      }
      if (t.dataset.sfield !== undefined && withScripts) {
        scriptChange(t);
        return true;
      }
      return false;
    },
    action: (name, el) => {
      if ((name === "checksprompt" || name === "checksanswer") && withScripts && o.prompt) {
        openPrompt(name === "checksanswer" ? "answer" : "prompt");
      } else if (name === "addscript" && withScripts) {
        addScript();
      } else if (name === "delscript" && withScripts) {
        deleteScript(+el.dataset.script);
      } else {
        return false;
      }
      return true;
    },
  };
};

const escape = s => String(s === null || s === undefined ? "" : s)
  .replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const parse = text => {
  try {
    const v = JSON.parse(text || "");
    return v && typeof v === "object" && !Array.isArray(v) ? v : null;
  } catch (e) {
    return null;
  }
};
/* the lab as get_checks_lab gives it, for the block: links as pairs of ends, segments and the lab's own checks parsed */
const labOf = raw => !raw ? null : {
  found: !!raw.found,
  catalog: !!raw.catalog,
  nodes: Array.isArray(raw.nodes) ? raw.nodes : [],
  links: (Array.isArray(raw.links) ? raw.links : []).map(l => [l.a, l.b]),
  segments: (() => {
    try {
      const v = JSON.parse(raw.segments || "[]");
      return Array.isArray(v) ? v : [];
    } catch (e) {
      return [];
    }
  })(),
  labchecks: parse(raw.labchecks),
};

/**
 * The checks of an activity in its settings form (docs/autochecks.md §12).
 *
 * The document travels in the hidden field `checksdef`: empty means the checks of the lab. The block follows the lab
 * chosen in the form and asks the plugin (get_checks_lab) for its devices. Moving to a version without checks while
 * the activity used those of its earlier version takes them into the activity, so they are not lost silently.
 *
 * @param {string} id id of the element of the block
 */
export const init = id => {
  const root = document.getElementById(id);
  const form = root && root.closest("form");
  const input = form && form.querySelector('input[name="checksdef"]');
  if (!root || !input || root.dataset.ready) {
    return;
  }
  root.dataset.ready = "1";
  const D = JSON.parse(root.dataset.json || "{}");
  const S = D.strings || {};
  const str = (key, a) => {
    let s = S[key] || key;
    if (a !== undefined && a !== null) {
      s = s.split("{$a}").join(String(a));
    }
    return s;
  };
  const call = (methodname, args) => Ajax.call([{methodname, args}])[0];
  const version = form.querySelector('select[name="labversionid"]');
  const labref = form.querySelector('input[name="labref"]');
  const chosen = () => ({labversionid: version ? +version.value || 0 : 0,
    labref: version && +version.value ? "" : (labref ? labref.value.trim() : "")});
  const st = {
    doc: parse(input.value),
    lab: labOf(D.lab),
    of: {labversionid: D.labversionid || 0, labref: D.labversionid ? "" : D.labref || ""},
    status: "", problems: [], note: "",
  };

  const write = () => {
    input.value = st.doc ? JSON.stringify(st.doc) : "";
  };
  const fieldError = text => {
    const p = root.querySelector('[data-region="fielderror"]');
    if (p) {
      p.textContent = text;
      p.hidden = !text;
    }
  };
  const validate = async doc => {
    const r = await call("mod_gns3lab_validate_checks", Object.assign({courseid: D.courseid, checks: JSON.stringify(doc)},
      chosen()));
    if (r.error) {
      throw new Error(r.error);
    }
    return r.problems || [];
  };
  const block = create({
    esc: escape, str, S,
    get: () => st.doc,
    /* in the activity's own checks an empty document stays: only the general rules, not the checks of the lab */
    set: v => {
      st.doc = v || {format: CHECKS_FORMAT};
      st.status = "";
      st.problems = [];
      write();
      render();
    },
    lab: () => st.lab || {nodes: [], links: [], segments: []},
    editable: true,
    withScripts: !!D.withscripts,
    withNeeds: !!D.withneeds,
    prompt: D.prompt || "",
    validate,
    fieldError,
    root: document.body,
    helpKey: "checksdef_json_help",
  });

  const labSummary = () => {
    const lab = st.lab;
    if (!lab || !lab.found) {
      return str("checksdef_nolab");
    }
    if (lab.labchecks) {
      const n = Array.isArray(lab.labchecks.scripts) ? lab.labchecks.scripts.length : 0;
      return str("checksdef_labhas", n);
    }
    return str(lab.catalog ? "checksdef_labcatalog" : "checksdef_labnone");
  };
  const problemsHTML = () => !st.problems.length ? "" : `<ul class="ed-problems" role="alert">${st.problems.map(p =>
    `<li><b>${escape(p.path || "checks")}</b>: ${escape(S["problem_" + p.code] || p.code)}${
      p.message ? ` <small>(${escape(p.message)})</small>` : ""}</li>`).join("")}</ul>`;
  const render = () => {
    const note = st.note ? `<p class="alert alert-info" role="status">${escape(st.note)}</p>` : "";
    if (!st.doc) {
      root.innerHTML = `${note}<p data-region="checkssource">${escape(labSummary())}</p>
        <button type="button" class="btn btn-secondary btn-sm" data-act="own">${escape(str("checksdef_own"))}</button>
        <p class="text-muted small">${escape(str("checksdef_own_help"))}</p>`;
      return;
    }
    root.innerHTML = `${note}<div class="gns3lab-checksform-head"><p data-region="checkssource">${
      escape(str("checksdef_activity"))}</p>
      <button type="button" class="btn btn-link btn-sm" data-act="labchecks">${escape(str("checksdef_back"))}</button></div>
      <div class="gns3lab-checksform-body">${block.html()}</div>
      <div class="gns3lab-checksform-actions">
        <button type="button" class="btn btn-secondary btn-sm" data-act="validate">${escape(str("checksdef_validate"))}</button>
        <span data-region="checksstatus" role="status">${escape(st.status)}</span></div>
      ${problemsHTML()}`;
  };

  /* another lab in the form: its devices, and the checks of the earlier version kept if the new one has none */
  let asked = 0;
  const follow = async() => {
    const want = chosen();
    if (want.labversionid === st.of.labversionid && want.labref === st.of.labref) {
      return;
    }
    const ticket = ++asked;
    const before = st.lab;
    let lab = null;
    try {
      lab = labOf(await call("mod_gns3lab_get_checks_lab", Object.assign({courseid: D.courseid}, want)));
    } catch (e) {
      lab = null;
    }
    if (ticket !== asked) {
      return; // A later choice is on its way
    }
    st.of = want;
    st.lab = lab;
    st.note = "";
    st.problems = [];
    st.status = "";
    if (!st.doc && before && before.labchecks && !(lab && lab.labchecks)) {
      st.doc = clone(before.labchecks);
      st.note = str("checksdef_kept");
      write();
    }
    render();
  };

  root.addEventListener("change", e => {
    block.change(e.target);
  });
  root.addEventListener("click", async e => {
    const act = e.target.closest("[data-act]");
    if (!act || !root.contains(act)) {
      return;
    }
    e.preventDefault();
    const name = act.dataset.act;
    if (name === "own") {
      st.doc = clone(st.lab && st.lab.labchecks) || {format: CHECKS_FORMAT};
      st.note = "";
      write();
      render();
    } else if (name === "labchecks") {
      st.doc = null;
      st.problems = [];
      st.status = "";
      write();
      render();
    } else if (name === "validate") {
      st.status = str("checksdef_validating");
      render();
      try {
        st.problems = await validate(st.doc);
        st.status = str(st.problems.length ? "checksdef_invalid" : "checksdef_valid", st.problems.length);
      } catch (err) {
        st.problems = [];
        st.status = str("checksdef_nocheck", err && err.message ? err.message : String(err));
      }
      render();
    } else {
      block.action(name, act);
    }
  });
  if (version) {
    version.addEventListener("change", follow);
  }
  if (labref) {
    labref.addEventListener("change", follow);
  }
  render();
  follow(); // A form shown again after an error may have another lab than the activity
};
