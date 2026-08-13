const state = { config: null, runs: [], selected: null, snapshot: null, stream: null, token: sessionStorage.getItem("qteam-token") || "" };
const $ = (id) => document.getElementById(id);
const canControl = () => Boolean(state.config?.controls_enabled);

function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: authHeaders(options.headers || {}) });
  if (response.status === 401) {
    const token = prompt("This QTeam Web server requires its token:");
    if (!token) throw new Error("authentication required");
    state.token = token;
    sessionStorage.setItem("qteam-token", token);
    return api(path, options);
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || payload.stderr || `HTTP ${response.status}`);
  return payload;
}

function text(tag, value, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? "—" : String(value);
  return node;
}

function empty(container, label = "Nothing to show.") {
  container.replaceChildren(text("p", label, "empty-row"));
}

function setConnection(label, kind) {
  $("connection").textContent = label;
  $("connection").className = `pill ${kind}`;
}

function renderRuns() {
  const root = $("runs");
  root.replaceChildren();
  for (const run of state.runs) {
    const button = document.createElement("button");
    button.className = `run-link${run.run_id === state.selected ? " active" : ""}`;
    button.append(text("strong", run.run_id), text("span", `${run.phase} · wave ${run.current_wave || 0}`));
    button.addEventListener("click", () => selectRun(run.run_id));
    root.append(button);
  }
  if (!state.runs.length) empty(root, "No QTeam runs found.");
}

function statusNode(value) { return text("span", value || "unknown", `status ${value || ""}`); }

function renderGraph(snapshot) {
  const graph = $("graph");
  graph.replaceChildren();
  const waves = [...new Set(snapshot.tasks.map((task) => task.wave))].sort((a, b) => a - b);
  if (!waves.length) return empty(graph, "No tasks registered.");
  for (const wave of waves) {
    const column = text("div", "", "wave-column");
    const heading = text("div", "", "wave-label");
    heading.append(text("strong", `Wave ${wave}`), text("span", snapshot.waves[String(wave)]?.workflow_shape || "legacy"));
    column.append(heading);
    for (const task of snapshot.tasks.filter((item) => item.wave === wave)) {
      const card = text("article", "", `task-card ${task.workflow_shape}`);
      const top = text("div", "", "task-top");
      top.append(text("strong", task.id), statusNode(task.status));
      card.append(top, text("p", task.title || task.work_kind, "task-title"));
      const meta = text("div", "", "task-meta");
      meta.append(text("span", task.work_kind, "tag"), text("span", task.execution_tier, "tag"), text("span", task.reversibility, "tag"));
      for (const dep of task.depends_on || []) meta.append(text("span", `← ${dep}`, "tag"));
      card.append(meta);
      column.append(card);
    }
    graph.append(column);
  }
}

function renderQuality(snapshot) {
  const root = $("quality"); root.replaceChildren();
  for (const [wave, lanes] of Object.entries(snapshot.quality_lanes || {}).sort(([a], [b]) => Number(a) - Number(b))) {
    for (const [name, lane] of Object.entries(lanes)) {
      const row = text("article", "", "row");
      const head = text("div", "", "row-head");
      head.append(text("strong", `Wave ${wave} · ${name}`), statusNode(lane.status));
      if (canControl() && name === "refactor" && lane.assessment?.head_sha !== snapshot.integration_head) {
        const assess = text("button", "Assess", "ghost small");
        assess.addEventListener("click", () => {
          const rationale = prompt("Why is no behavior-preserving refactor needed at this head?");
          if (rationale) action("quality-assess", { wave: Number(wave), lane: name, outcome: "not-needed", rationale });
        });
        head.append(assess);
      }
      if (canControl() && (lane.status !== "passed" || lane.head_sha !== snapshot.integration_head)) {
        const button = text("button", "Run frozen checks", "ghost small");
        button.addEventListener("click", () => action("quality-check", { wave: Number(wave), lane: name }));
        head.append(button);
      }
      const tags = text("div", "", "tags");
      for (const task of lane.required_by || []) tags.append(text("span", task, "tag"));
      row.append(head, text("p", `${lane.command_count || 0} frozen command(s) · head ${lane.head_sha || "not checked"}`), tags);
      root.append(row);
    }
  }
  if (!root.children.length) empty(root, "No conditional quality lane was triggered.");
}

function renderWorkers(snapshot) {
  const root = $("workers"); root.replaceChildren();
  for (const worker of snapshot.workers || []) {
    const row = text("article", "", "row");
    const head = text("div", "", "row-head");
    head.append(text("strong", `${worker.task} · ${worker.role}`), statusNode(worker.status));
    if (canControl() && ["launching", "running"].includes(worker.status)) {
      const cancel = text("button", "Cancel", "ghost small");
      cancel.addEventListener("click", () => action("worker-cancel", { task: worker.task }));
      head.append(cancel);
    }
    const execution = worker.execution || {};
    const tags = text("div", "", "tags");
    tags.append(text("span", execution.model || "unlaunched", "tag"));
    if (execution.thinking) tags.append(text("span", execution.thinking, "tag"));
    if (worker.pid) tags.append(text("span", `pid ${worker.pid}`, "tag"));
    row.append(head, tags);
    root.append(row);
  }
  if (!root.children.length) empty(root, "No isolated worker has launched.");
}

function renderGates(snapshot) {
  const root = $("gates"); root.replaceChildren();
  for (const [name, gate] of Object.entries(snapshot.gates || {}).sort(([a], [b]) => a.localeCompare(b))) {
    const row = text("article", "", "row");
    const head = text("div", "", "row-head");
    head.append(text("strong", name.replaceAll("_", " ")), statusNode(gate.status));
    const tags = text("div", "", "tags");
    if (gate.head_sha) tags.append(text("span", `head ${gate.head_sha}`, "tag"));
    if (gate.through_wave) tags.append(text("span", `through wave ${gate.through_wave}`, "tag"));
    for (const axis of gate.axes || []) tags.append(text("span", axis, "tag"));
    row.append(head, tags);
    root.append(row);
  }
  if (!root.children.length) empty(root, "No delivery gate state recorded.");
}

function renderQueue(snapshot) {
  const root = $("queue"); root.replaceChildren();
  for (const item of snapshot.work_queue || []) {
    const row = text("article", "", "row");
    const head = text("div", "", "row-head");
    head.append(text("strong", item.id), statusNode(item.status));
    const tags = text("div", "", "tags");
    tags.append(text("span", item.kind, "tag"), text("span", `priority ${item.priority}`, "tag"));
    for (const target of item.targets || []) tags.append(text("span", target, "tag"));
    row.append(head, tags);
    if (canControl() && item.status === "claimed") {
      const button = text("button", "Complete", "ghost small");
      button.addEventListener("click", async () => {
        const evidence = prompt("Bounded completion evidence:");
        if (evidence) await action("queue-complete", { item: item.id, consumer: item.claimed_by, outcome: "completed", evidence });
      });
      row.append(button);
    }
    root.append(row);
  }
  if (!root.children.length) empty(root, "The coordinator queue is empty.");
}

function renderDecisions(snapshot) {
  const root = $("decisions"); root.replaceChildren();
  if (snapshot.decisions_meta?.truncated) {
    root.append(text("p", `Showing ${snapshot.decisions_meta.shown} of ${snapshot.decisions_meta.total} decisions; all ${snapshot.decisions_meta.open_total} open blockers are counted.`, "warning"));
  }
  for (const decision of snapshot.decisions || []) {
    const row = text("article", "", "row");
    const head = text("div", "", "row-head");
    head.append(text("strong", decision.id), statusNode(decision.status));
    row.append(head, text("p", decision.question));
    if (canControl() && decision.status === "open") {
      const form = text("form", "", "decision-form");
      const outcome = document.createElement("select");
      for (const value of ["allow", "deny"]) { const option = text("option", value); option.value = value; outcome.append(option); }
      const choice = document.createElement("input"); choice.placeholder = "Concrete choice"; choice.required = true;
      const evidence = document.createElement("textarea"); evidence.placeholder = "Bounded authority evidence"; evidence.required = true;
      const submit = text("button", "Resolve", "primary"); submit.type = "submit";
      form.append(outcome, choice, evidence, submit);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await action("decision-resolve", { decision: decision.id, outcome: outcome.value, choice: choice.value, evidence: evidence.value });
      });
      row.append(form);
    }
    root.append(row);
  }
  if (!root.children.length) empty(root, "No durable decision gates.");
}

function renderReviews(snapshot) {
  const root = $("reviews"); root.replaceChildren();
  for (const review of snapshot.reviews || []) {
    const row = text("article", "", "row");
    const head = text("div", "", "row-head");
    head.append(text("strong", `Wave ${review.wave} · ${review.axis}`), statusNode(review.status));
    row.append(head, text("p", `${review.open_findings} open finding(s) · iteration ${review.iteration || 1}`));
    root.append(row);
  }
  if (!root.children.length) empty(root, "No review ledger created yet.");
}

function renderEvents(snapshot) {
  const root = $("events"); root.replaceChildren();
  for (const event of [...(snapshot.events || [])].reverse().slice(0, 30)) {
    const item = document.createElement("li");
    const details = Object.entries(event).filter(([key]) => !["ts", "event"].includes(key)).map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(",") : value}`).join(" · ");
    item.append(text("strong", event.event), text("span", details ? ` ${details}` : ""), text("time", event.ts));
    root.append(item);
  }
  if (!root.children.length) empty(root, "No lifecycle events.");
}

function render(snapshot) {
  state.snapshot = snapshot;
  $("empty").hidden = true; $("dashboard").hidden = false;
  $("run-id").textContent = snapshot.run_id; $("goal").textContent = snapshot.goal || "Untitled goal";
  $("phase").textContent = snapshot.phase; $("wave").textContent = snapshot.current_wave || 0; $("updated").textContent = snapshot.updated_at || "—";
  const taskCounts = snapshot.tasks.reduce((acc, item) => { acc[item.status] = (acc[item.status] || 0) + 1; return acc; }, {});
  const stats = [["Tasks", snapshot.tasks.length], ["Active", taskCounts.running || 0], ["Open decisions", snapshot.decisions_meta?.open_total ?? snapshot.decisions.filter((d) => d.status === "open").length], ["Open findings", snapshot.reviews.reduce((n, item) => n + item.open_findings, 0)]];
  $("stats").replaceChildren(...stats.map(([label, value]) => { const node = text("div", "", "stat"); node.append(text("span", label), text("strong", value)); return node; }));
  renderGraph(snapshot); renderWorkers(snapshot); renderGates(snapshot); renderQuality(snapshot); renderQueue(snapshot); renderDecisions(snapshot); renderReviews(snapshot); renderEvents(snapshot);
}

async function loadSnapshot() {
  if (!state.selected) return;
  render(await api(`/api/runs/${encodeURIComponent(state.selected)}/snapshot`));
}

function openStream() {
  if (state.stream) state.stream.close();
  if (!state.selected || state.token) {
    setConnection(state.token ? "authenticated polling" : "idle", state.token ? "live" : "quiet");
    return;
  }
  state.stream = new EventSource(`/api/runs/${encodeURIComponent(state.selected)}/stream`);
  state.stream.addEventListener("snapshot", (event) => { render(JSON.parse(event.data)); setConnection("live", "live"); });
  state.stream.onerror = () => setConnection("reconnecting", "error");
}

async function selectRun(runId) {
  state.selected = runId; location.hash = encodeURIComponent(runId); renderRuns();
  await loadSnapshot(); openStream();
}

async function action(name, payload) {
  if (!canControl()) {
    $("action-output").textContent = "read-only: restart QTeam Web with --token-file to enable controls";
    return;
  }
  try {
    const result = await api(`/api/runs/${encodeURIComponent(state.selected)}/actions/${name}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-QTeam-CSRF": state.config.csrf }, body: JSON.stringify(payload),
    });
    $("action-output").textContent = result.stdout || result.stderr || JSON.stringify(result, null, 2);
    await loadSnapshot();
  } catch (error) { $("action-output").textContent = `error: ${error.message}`; }
}

async function boot() {
  try {
    state.config = await api("/api/config");
    $("finish-check").disabled = !canControl();
    $("claim-queue").disabled = !canControl();
    state.runs = (await api("/api/runs")).runs;
    renderRuns();
    const requested = decodeURIComponent(location.hash.slice(1));
    const selected = state.runs.find((run) => run.run_id === requested)?.run_id || state.runs[0]?.run_id;
    if (selected) await selectRun(selected); else setConnection("ready", "live");
    if (state.token) setInterval(loadSnapshot, 2500);
  } catch (error) { setConnection(error.message, "error"); }
}

$("refresh").addEventListener("click", loadSnapshot);
$("finish-check").addEventListener("click", () => action("finish-check", {}));
$("claim-queue").addEventListener("click", () => action("queue-claim", { consumer: "coordinator", limit: 4 }));
boot();
