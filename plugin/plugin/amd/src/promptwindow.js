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
 * The window of a prompt for a neural network (docs/autochecks.md §12, docs/lab-setup.md).
 *
 * Two tabs: the prompt (the teacher's task, the prompt built from it, "Copy") and the answer of the neural network (the
 * answer as pasted, its items as cards with the problems the page sees itself, "Check with the lab manager" and the
 * buttons that apply it). The checks block (checksform.js) and the setup block (setupform.js) open the same window; what
 * an item is, how a card looks and what applying does is theirs.
 *
 * @module     mod_gns3lab/promptwindow
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Copies the text of a text area: the clipboard API, else the selection and the older command.
 *
 * @param {HTMLTextAreaElement} area text area
 * @return {Promise<void>}
 */
export const copyText = async area => {
  try {
    await navigator.clipboard.writeText(area.value);
  } catch (e) {
    area.select(); // no clipboard API (http, an old browser): the selection and the older command
    if (!document.execCommand("copy")) {
      throw e;
    }
  }
};

/**
 * The value of an answer: the JSON of the whole text, or of its first ```json block.
 *
 * @param {string} raw answer as pasted
 * @return {*} parsed JSON
 * @throws {Error} when there is no JSON
 */
export const answerJSON = raw => {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/);
  return JSON.parse((fenced ? fenced[1] : raw).trim());
};

/**
 * Opens the window.
 *
 * @param {Object} o esc, t (text of a key suffix: "" is the title, "_tab_prompt", "_copy"…; the second argument fills
 *     {$a}), root (where the dialog goes), memo ({task, answer}: kept for the next opening), tab ("prompt" or "answer"),
 *     note (a text above the task, optional), promptText(task), parse(raw) (the items of an answer; throws when it is
 *     not one), local(items) (the page's own problems: a list of texts per item), general(items) (the page's own
 *     problems of the whole answer, optional), card(item, i, problems) (HTML),
 *     locate(problem, items) (where a problem of the lab manager goes: {i, field} on a card, null in the general list,
 *     false nowhere), problemName(code), validate(items) (a promise of the problems of the lab manager), actions
 *     ([{name, label, primary, blocked(items) → reason or "", run(items)}]: the buttons that apply the answer)
 * @return {HTMLDialogElement} the dialog, open
 */
export const open = o => {
  const esc = o.esc, t = o.t, memo = o.memo;
  const actions = o.actions || [];
  const dialog = document.createElement("dialog");
  dialog.className = "ed-prompt-dialog";
  dialog.setAttribute("aria-label", t(""));
  dialog.innerHTML = `<div class="ed-prompt-head">
      <div class="ed-prompt-tabs" role="tablist">
        <button type="button" role="tab" data-prompt-tab="prompt">${esc(t("_tab_prompt"))}</button>
        <button type="button" role="tab" data-prompt-tab="answer">${esc(t("_tab_answer"))}</button>
      </div>
      <button type="button" class="gbtn" data-cancel>${esc(t("_close"))}</button>
    </div>
    <section class="ed-prompt-pane" data-pane="prompt">
      ${o.note ? `<p class="ed-prompt-note" data-prompt-note role="status">${esc(o.note)}</p>` : ""}
      <p class="muted">${esc(t("_help"))}</p>
      <label>${esc(t("_task"))}<textarea data-prompt-task rows="3"
        placeholder="${esc(t("_task_placeholder"))}"></textarea></label>
      <label class="grow">${esc(t("_text"))}<textarea data-prompt-text class="mono" readonly
        spellcheck="false"></textarea></label>
      <div class="ed-prompt-actions"><button class="gbtn primary" type="button" data-prompt-copy>${
        esc(t("_copy"))}</button><span data-prompt-copied role="status"></span>
        <button class="gbtn" type="button" data-prompt-tab="answer">${esc(t("_next"))}</button></div>
    </section>
    <section class="ed-prompt-pane" data-pane="answer" hidden>
      <div class="ed-prompt-split">
        <label class="grow">${esc(t("_answer"))}<textarea data-prompt-answer class="mono"
          spellcheck="false" placeholder="${esc(t("_answer_placeholder"))}"></textarea></label>
        <div class="ed-ans-review"><h3>${esc(t("_review"))}</h3>
          <p data-answer-status role="status"></p><div data-answer-list></div></div>
      </div>
      <div class="ed-prompt-actions">
        <button class="gbtn" type="button" data-prompt-validate disabled>${esc(t("_validate"))}</button>
        ${actions.map(a => `<button class="gbtn${a.primary ? " primary" : ""}" type="button" data-prompt-apply="${
          esc(a.name)}" disabled>${esc(a.label)}</button>`).join("")}
        <span data-prompt-checked role="status"></span>
      </div>
      <p class="muted" data-prompt-blocked role="status" hidden></p>
    </section>`;
  const $d = sel => dialog.querySelector(sel);
  const task = $d("[data-prompt-task]"), text = $d("[data-prompt-text]"), answer = $d("[data-prompt-answer]");
  const status = $d("[data-answer-status]"), checked = $d("[data-prompt-checked]"), blocked = $d("[data-prompt-blocked]");
  let parsed = null, managerProblems = null;
  const show = name => {
    dialog.querySelectorAll("[data-pane]").forEach(p => {
      p.hidden = p.dataset.pane !== name;
    });
    dialog.querySelectorAll('[role="tab"]').forEach(b => b.setAttribute("aria-selected", String(b.dataset.promptTab === name)));
    (name === "answer" ? answer : task).focus();
  };
  const fill = () => {
    text.value = o.promptText(task.value.trim());
    $d("[data-prompt-copied]").textContent = "";
  };
  /* the parse of the answer and the problems of each item: ours at once, the manager's after "Check" */
  const review = () => {
    const raw = answer.value.trim();
    parsed = null;
    if (!raw) {
      status.textContent = t("_empty");
      $d("[data-answer-list]").innerHTML = "";
    } else {
      try {
        parsed = o.parse(raw);
      } catch (e) {
        status.textContent = t("_badanswer");
        $d("[data-answer-list]").innerHTML = "";
      }
    }
    const reasons = [];
    if (parsed) {
      const local = o.local(parsed);
      const other = o.general ? o.general(parsed) : [];
      (managerProblems || []).forEach(p => {
        const at = o.locate(p, parsed);
        if (at === false) {
          return;
        }
        const line = `${at && at.field ? at.field + ": " : (at ? "" : (p.path || "") + ": ")}${o.problemName(p.code)}${
          p.message ? " (" + p.message + ")" : ""}`;
        if (at && local[at.i]) {
          local[at.i].push(line);
        } else {
          other.push(line);
        }
      });
      status.textContent = t("_count", parsed.length);
      $d("[data-answer-list]").innerHTML = (other.length ? `<ul class="ed-ans-problems" role="alert">${
        other.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "") + parsed.map((x, i) => o.card(x, i, local[i])).join("");
    }
    $d("[data-prompt-validate]").disabled = !parsed;
    actions.forEach(a => {
      const why = parsed && a.blocked ? a.blocked(parsed) : "";
      if (why) {
        reasons.push(why);
      }
      $d(`[data-prompt-apply="${a.name}"]`).disabled = !parsed || !!why;
    });
    blocked.textContent = reasons.join(" ");
    blocked.hidden = !reasons.length;
  };
  task.value = memo.task || "";
  answer.value = memo.answer || "";
  task.addEventListener("input", () => {
    memo.task = task.value;
    fill();
  });
  answer.addEventListener("input", () => {
    memo.answer = answer.value;
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
      $d("[data-prompt-copied]").textContent = t("_copied");
    })
    .catch(() => {
      $d("[data-prompt-copied]").textContent = t("_nocopy");
    });
  /* the manager checks the lab with these items, the lab itself stays as it is */
  $d("[data-prompt-validate]").onclick = async() => {
    if (!parsed) {
      return;
    }
    checked.textContent = t("_validating");
    try {
      managerProblems = await o.validate(parsed);
      checked.textContent = managerProblems.length ? t("_invalid", managerProblems.length) : t("_valid");
      review();
    } catch (err) {
      checked.textContent = t("_nocheck", err && err.message ? err.message : String(err));
    }
  };
  actions.forEach(a => {
    $d(`[data-prompt-apply="${a.name}"]`).onclick = () => {
      if (!parsed || (a.blocked && a.blocked(parsed))) {
        return;
      }
      a.run(parsed);
      memo.answer = "";
      dialog.close();
    };
  });
  $d("[data-cancel]").onclick = () => dialog.close();
  dialog.addEventListener("close", () => dialog.remove());
  (o.root || document.body).append(dialog);
  dialog.showModal();
  show(o.tab === "answer" ? "answer" : "prompt");
  return dialog;
};
