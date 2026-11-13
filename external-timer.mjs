// worker.mjs
import { DurableObject } from "cloudflare:workers";

// core.mjs
var CLAIM = "UPDATE timer_state SET phase='checking' WHERE id=1 AND phase IN ('ready','waiting') RETURNING last_run,last_slot,reason";
var SET = "UPDATE timer_state SET phase=?,last_run=?,last_slot=?,reason=? WHERE id=1";
var schema = `CREATE TABLE IF NOT EXISTS timer_state(id INTEGER PRIMARY KEY CHECK(id=1),phase TEXT NOT NULL,last_run INTEGER,last_slot INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '');`;
async function tick(event, env, transport = fetch) {
  if (env.ENABLED !== "true") return "DISABLED";
  const slot = Number(event.scheduledTime);
  if (!Number.isSafeInteger(slot) || !env.GH_TOKEN || !env.OWNER || !env.REPO) throw new Error("CONFIG");
  const prior = await env.STATE.prepare(CLAIM).first();
  if (!prior) return "LATCHED";
  let run = prior.last_run, lastSlot = prior.last_slot;
  const save = (phase, reason = "") => env.STATE.prepare(SET).bind(phase, run, lastSlot, reason).run();
  const root = `https://api.github.com/repos/${encodeURIComponent(env.OWNER)}/${encodeURIComponent(env.REPO)}`;
  const workflow = encodeURIComponent(env.WORKFLOW || "deploy.yml");
  async function request(path, options = {}) {
    const response = await transport(root + path, {
      ...options,
      redirect: "manual",
      signal: AbortSignal.timeout(12e3),
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "guarded-foundation-timer",
        "X-GitHub-Api-Version": "2026-03-10",
        ...options.body ? { "Content-Type": "application/json" } : {}
      }
    });
    if (!response.ok) throw new Error("REMOTE_STATUS");
    return response;
  }
  const read = async (path) => (await request(path)).json();
  try {
    if (slot < lastSlot + 6e5) {
      await save("waiting", prior.reason?.startsWith("REVIEWED_RECOVERY:") ? prior.reason : "EARLY");
      return "EARLY";
    }
    const wf = await read(`/actions/workflows/${workflow}`);
    if (wf.state !== "active") {
      await save("paused", "WORKFLOW_DISABLED");
      return "PAUSED";
    }
    const file = await read(`/${"contents"}/.github/workflows/${workflow}?ref=main`);
    if (file.encoding !== "base64" || typeof file.content !== "string") throw new Error("SOURCE");
    if (/^\s+schedule\s*:/m.test(atob(file.content.replace(/\s/g, "")))) throw new Error("SECOND_SCHEDULER");
    const listing = await read(`/actions/workflows/${workflow}/runs?per_page=10`);
    if (!Array.isArray(listing.workflow_runs) || !listing.workflow_runs.length) throw new Error("RUNS");
    if (!Number.isSafeInteger(run)) throw new Error("BASELINE_REQUIRED");
    const known = await read(`/actions/runs/${run}`);
    if (listing.workflow_runs.some((r) => r.id > run)) throw new Error("UNTRACKED_RUN");
    if (known.status !== "completed") {
      await save("waiting", "IN_PROGRESS");
      return "WAITING";
    }
    if (known.conclusion !== "success" || known.head_branch !== "main") throw new Error("RUN_FAILED");
    const recovery = prior.reason === "REVIEWED_RECOVERY:" + env.RECOVERY_NONCE;
    if (recovery) {
      const reviewed = Number(env.RECOVERY_REVIEWED_AT), expires = Number(env.RECOVERY_EXPIRES);
      const ids = JSON.parse(env.RECOVERY_PREVIOUS_MARKERS || "null");
      if (run !== Number(env.RECOVERY_RUN) || known.head_sha !== env.RECOVERY_SHA || known.run_attempt !== 1 || !Number.isSafeInteger(reviewed) || !Number.isSafeInteger(expires) || expires <= reviewed || expires > reviewed + 9e5 || slot < reviewed || slot > expires || Date.now() > expires || !Array.isArray(ids) || ids.length !== 3 || new Set(ids).size !== 3 || ids.some((x) => !Number.isSafeInteger(x))) throw new Error("RECOVERY_SCOPE");
      const history = await read("/deployments?environment=foundation&per_page=3");
      const start = Date.parse(known.run_started_at);
      if (!Number.isFinite(start) || history.length !== 3 || history.some((d, i) => d.id !== ids[i] || d.environment !== "foundation" || !Number.isFinite(Date.parse(d.created_at)) || Date.parse(d.created_at) >= start)) throw new Error("RECOVERY_LEDGER");
      for (const d of history) {
        const states = await read(`/deployments/${d.id}/statuses?per_page=1`);
        if (states[0]?.state !== "failure" || !(Date.parse(states[0].created_at) < start)) throw new Error("RECOVERY_PENDING");
      }
    } else {
      const cycles = await read("/deployments?environment=foundation-cycle-v2&per_page=1");
      const cycle = cycles[0], proof = cycle?.payload;
      if (!proof || proof.schema !== 2 || proof.run_id !== run || proof.run_attempt !== known.run_attempt || proof.sha !== known.head_sha || cycle.sha !== known.head_sha || cycle.environment !== "foundation-cycle-v2" || !["capacity_only", "rate_limited"].includes(proof.outcome) || !Array.isArray(proof.attempts) || proof.attempts.length > 3 || new Set(proof.attempts).size !== proof.attempts.length || proof.attempts.some((x) => !Number.isSafeInteger(x)) || proof.outcome === "capacity_only" && proof.attempts.length !== 3) throw new Error("CYCLE_PROOF");
      const start = Date.parse(known.run_started_at), end = Date.parse(known.updated_at);
      const completed = Date.parse(cycle.created_at);
      if (![start, end, completed].every(Number.isFinite) || completed < start || completed > end) throw new Error("CYCLE_TIME");
      const cycleStatus = await read(`/deployments/${cycle.id}/statuses?per_page=1`);
      if (cycleStatus[0]?.state !== "success") throw new Error("CYCLE_NOT_COMMITTED");
      const deployments = await read("/deployments?environment=foundation&per_page=3");
      if (!Array.isArray(deployments)) throw new Error("LEDGER");
      if (deployments.some((d) => !Number.isSafeInteger(d.id) || !Number.isFinite(Date.parse(d.created_at)))) throw new Error("LEDGER_TIME");
      const current = deployments.filter((d) => Date.parse(d.created_at) >= start).map((d) => d.id);
      if (current.length !== proof.attempts.length || current.some((x) => !proof.attempts.includes(x))) throw new Error("CYCLE_ATTEMPTS");
      for (const d of deployments) {
        if (d.environment !== "foundation") throw new Error("LEDGER_ENV");
        const statuses = await read(`/deployments/${d.id}/statuses?per_page=1`);
        if (statuses[0]?.state !== "failure" || !Number.isFinite(Date.parse(statuses[0].created_at)) || Date.parse(statuses[0].created_at) > completed) throw new Error("PENDING_OR_HOST");
      }
    }
    lastSlot = slot;
    await save("dispatch_pending", "DISPATCH_MAY_BE_ACCEPTED");
    const accepted = await request(`/actions/workflows/${workflow}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: "main", inputs: { validate_only: false } })
    });
    const result = await accepted.json();
    if (!Number.isSafeInteger(result.workflow_run_id) || result.workflow_run_id <= run) throw new Error("AMBIGUOUS_DISPATCH");
    run = result.workflow_run_id;
    await save("waiting", "ACCEPTED");
    return "ACCEPTED";
  } catch (_) {
    await save("paused", "REVIEW_REQUIRED");
    return "PAUSED";
  }
}

// worker.mjs
var DispatchGate = class extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.env = env;
    ctx.storage.sql.exec(schema);
    const baseline = Number(env.BASELINE_RUN);
    ctx.storage.sql.exec(
      "INSERT OR IGNORE INTO timer_state(id,phase,last_run,last_slot) VALUES(1,?,?,0)",
      Number.isSafeInteger(baseline) ? "waiting" : "paused",
      Number.isSafeInteger(baseline) ? baseline : null
    );
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS recovery_receipts(nonce TEXT PRIMARY KEY,run INTEGER NOT NULL)");
    if (env.RECOVERY_NONCE && /^[-a-zA-Z0-9_]{12,100}$/.test(env.RECOVERY_NONCE)) {
      ctx.storage.transactionSync(() => {
        const row = ctx.storage.sql.exec("SELECT phase,last_run,reason FROM timer_state WHERE id=1").toArray()[0];
        if (row?.phase !== "paused" || row.last_run !== Number(env.RECOVERY_RUN) || row.reason !== "REVIEW_REQUIRED") return;
        const receipt = ctx.storage.sql.exec("INSERT OR IGNORE INTO recovery_receipts(nonce,run) VALUES(?,?) RETURNING nonce", env.RECOVERY_NONCE, row.last_run).toArray();
        if (receipt.length !== 1) return;
        ctx.storage.sql.exec("UPDATE timer_state SET phase='waiting',reason=? WHERE id=1", "REVIEWED_RECOVERY:" + env.RECOVERY_NONCE);
      });
    }
    this.stateAdapter = {
      prepare(sql) {
        let values = [];
        return {
          bind(...v) {
            values = v;
            return this;
          },
          async first() {
            return ctx.storage.sql.exec(sql, ...values).toArray()[0] || null;
          },
          async run() {
            ctx.storage.sql.exec(sql, ...values);
            return { success: true };
          }
        };
      }
    };
  }
  async fetch(request) {
    const body = await request.json();
    const result = await tick({ scheduledTime: body.scheduledTime }, { ...this.env, STATE: this.stateAdapter });
    return Response.json({ result });
  }
};
var worker_default = {
  fetch() {
    return new Response("Not found", { status: 404 });
  },
  async scheduled(event, env, ctx) {
    if (env.ENABLED !== "true") return;
    const stub = env.GATE.get(env.GATE.idFromName("singleton-dispatch-gate"));
    ctx.waitUntil(stub.fetch("https://internal/tick", {
      method: "POST",
      body: JSON.stringify({ scheduledTime: event.scheduledTime })
    }).then(async (response) => {
      const status = await response.json();
      console.info(status.result);
    }));
  }
};
export {
  DispatchGate,
  worker_default as default
};
