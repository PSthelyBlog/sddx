// sddx Subscription Billing demo — vanilla JS, polls every 1 second.

const POLL_INTERVAL_MS = 1000;
const EVENT_LIMIT = 30;
const STATE_BADGES = {
  pending:    "pending",
  active:     "ok",
  suspended:  "warn",
  cancelled:  "muted",
  churned:    "bad",
};

// --- API helpers --------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* may be empty */ }
  return { ok: res.ok, status: res.status, data };
}

const get  = (p)    => api("GET", p);
const post = (p, b) => api("POST", p, b);
const del  = (p, b) => api("DELETE", p, b);

// --- Rendering -----------------------------------------------------------

function badge(state) {
  const cls = STATE_BADGES[state] ?? "muted";
  return `<span class="badge badge-${cls}">${state}</span>`;
}

function renderSubs(subs) {
  const list = document.getElementById("sub-list");
  if (!subs.length) {
    list.innerHTML = "<p><em>None yet — create one above.</em></p>";
    return;
  }
  list.innerHTML = subs.map(s => `
    <article class="sub-card" data-id="${s.subscription_id}">
      <header>
        <strong>${s.subscription_id}</strong>
        ${badge(s.state)}
      </header>
      <p class="muted">
        ${s.plan_id ?? ""} &middot; $${(s.amount ?? 0).toFixed(2)} &middot;
        ${s.cycles_started} cycle(s)
      </p>
      <div class="sub-actions">
        <button class="bill" ${s.state === "cancelled" || s.state === "churned" ? "disabled" : ""}>Bill now</button>
        <button class="cancel secondary" ${s.state === "cancelled" || s.state === "churned" ? "disabled" : ""}>Cancel</button>
      </div>
    </article>
  `).join("");

  for (const card of list.querySelectorAll(".sub-card")) {
    const id = card.dataset.id;
    card.querySelector(".bill")?.addEventListener("click", () => billCycle(id));
    card.querySelector(".cancel")?.addEventListener("click", () => cancelSub(id));
  }
}

function renderEvents(events) {
  const log = document.getElementById("event-rows");
  if (!events.length) {
    log.innerHTML = "<p><em>No events.</em></p>";
    return;
  }
  log.innerHTML = events.slice().reverse().map(e => {
    const t = e.timestamp.toFixed(1).padStart(7, " ");
    const detail = e.source_instance ? `<code>${e.source_instance}</code>` : "";
    return `<div class="event"><code class="t">+${t}s</code> <span class="ev-name">${e.name}</span> ${detail}</div>`;
  }).join("");
}

function renderClock(clock) {
  document.getElementById("clock-now").textContent = clock.now.toFixed(1);
  document.getElementById("clock-pending").textContent = clock.pending_timers.length;
}

// --- Actions -------------------------------------------------------------

async function refresh() {
  const [{ data: subData }, { data: evData }, { data: clkData }] = await Promise.all([
    get("/subscriptions"),
    get(`/events?limit=${EVENT_LIMIT}`),
    get("/clock"),
  ]);
  renderSubs(subData.subscriptions ?? []);
  renderEvents(evData.events ?? []);
  renderClock(clkData);
}

async function createSub(ev) {
  ev.preventDefault();
  const id = document.getElementById("new-sub-id").value.trim();
  const plan = document.getElementById("new-sub-plan").value.trim();
  const amount = parseFloat(document.getElementById("new-sub-amount").value);
  const outcomes = document.getElementById("new-sub-outcomes").value.split(",");
  const { ok, status, data } = await post("/subscriptions", {
    subscription_id: id,
    plan_id: plan,
    amount,
    outcomes,
  });
  if (!ok) flash(`${status}: ${data.error || "create failed"}`);
  else document.getElementById("new-sub-id").value = "";
  refresh();
}

async function billCycle(id) {
  const outcomes = ["succeed"]; // demo default; could surface in UI later
  const { ok, status, data } = await post(`/subscriptions/${encodeURIComponent(id)}/cycles`, { outcomes });
  if (!ok) flash(`${status}: ${data.error || "bill failed"}`);
  refresh();
}

async function cancelSub(id) {
  const { ok, status, data } = await del(`/subscriptions/${encodeURIComponent(id)}`, { reason: "user_request" });
  if (!ok) flash(`${status}: ${data.error || "cancel failed"}`);
  refresh();
}

async function advanceTime(seconds) {
  const { ok, status, data } = await post("/admin/advance_time", { seconds });
  if (!ok) flash(`${status}: ${data.error || "advance failed"}`);
  refresh();
}

function flash(msg) {
  console.warn(msg);
  // Simple flash via the page title for now.
  const orig = document.title;
  document.title = `⚠ ${msg}`;
  setTimeout(() => { document.title = orig; }, 2000);
}

// --- Wire up -------------------------------------------------------------

document.getElementById("new-sub").addEventListener("submit", createSub);
document.getElementById("advance-custom").addEventListener("click", () => {
  const s = parseFloat(document.getElementById("advance-seconds").value);
  if (Number.isFinite(s) && s >= 0) advanceTime(s);
});
for (const btn of document.querySelectorAll(".advance-preset")) {
  btn.addEventListener("click", () => advanceTime(parseInt(btn.dataset.seconds, 10)));
}

refresh();
setInterval(refresh, POLL_INTERVAL_MS);
