// worker.mjs
import { DurableObject } from "cloudflare:workers";

// core.mjs
var CLAIM = "UPDATE timer_state SET phase='checking' WHERE id=1 AND phase IN ('ready','waiting') RETURNING last_run,last_slot";
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
      await save("waiting", "EARLY");
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
    const deployments = await read("/deployments?per_page=3");
    if (!Array.isArray(deployments) || deployments.length !== 3) throw new Error("LEDGER");
    const knownStart = Date.parse(known.created_at);
    if (!Number.isFinite(knownStart)) throw new Error("RUN_TIME");
    for (const d of deployments) {
      if (Date.parse(d.created_at) < knownStart) throw new Error("INCOMPLETE_CYCLE");
      const statuses = await read(`/deployments/${d.id}/statuses?per_page=1`);
      if (statuses[0]?.state !== "failure") throw new Error("PENDING_OR_HOST");
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
