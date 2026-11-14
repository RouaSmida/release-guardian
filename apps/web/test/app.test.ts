import assert from "node:assert/strict";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, describe, test } from "node:test";

import pino from "pino";

import { createApp } from "../src/app";
import { loadConfig, type Config } from "../src/config";

const servers: http.Server[] = [];
let apiUrl = "";

function listen(handler: http.RequestListener): Promise<string> {
  return new Promise((resolve) => {
    const server = http.createServer(handler).listen(0, "127.0.0.1", () => {
      servers.push(server);
      resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`);
    });
  });
}

function startWeb(overrides: Partial<Config> = {}, random?: () => number): Promise<string> {
  const config = { ...loadConfig({ API_URL: apiUrl }), ...overrides };
  return listen(createApp(config, { logger: pino({ level: "silent" }), random }));
}

/** Sum every sample of a metric family from the /metrics text output. */
async function metric(base: string, name: string): Promise<number> {
  const text = await (await fetch(`${base}/metrics`)).text();
  return text
    .split("\n")
    .filter((line) => line.startsWith(`${name}{`) || line.startsWith(`${name} `))
    .reduce((sum, line) => sum + Number(line.slice(line.lastIndexOf(" ") + 1)), 0);
}

before(async () => {
  // A stand-in for the API service: echoes what it received.
  apiUrl = await listen((req, res) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ method: req.method, url: req.url, body }));
    });
  });
});

after(() => {
  for (const server of servers) server.close();
});

describe("web", () => {
  test("health, readiness and info endpoints", async () => {
    const base = await startWeb({ version: "1.2.3" });
    assert.deepEqual(await (await fetch(`${base}/healthz`)).json(), { status: "ok" });
    assert.equal((await fetch(`${base}/readyz`)).status, 200);
    assert.deepEqual(await (await fetch(`${base}/info`)).json(), {
      service: "web",
      version: "1.2.3",
      fault_mode: "none",
    });
  });

  test("serves the UI", async () => {
    const base = await startWeb();
    const response = await fetch(`${base}/`);
    assert.equal(response.status, 200);
    assert.match(await response.text(), /Release Guardian/);
    assert.equal((await fetch(`${base}/nope`)).status, 404);
  });

  test("proxies /api requests to the API service", async () => {
    const base = await startWeb();
    const response = await fetch(`${base}/api/jobs?limit=5`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "hello" }),
    });
    assert.deepEqual(await response.json(), {
      method: "POST",
      url: "/api/jobs?limit=5",
      body: '{"text":"hello"}',
    });
  });

  test("returns 502 when the API is unreachable", async () => {
    const base = await startWeb({ apiUrl: "http://127.0.0.1:1" });
    assert.equal((await fetch(`${base}/api/jobs`)).status, 502);
    assert.equal(await metric(base, "http_request_errors_total"), 1);
  });

  test("metrics count requests but not probes", async () => {
    const base = await startWeb();
    await fetch(`${base}/info`);
    await fetch(`${base}/healthz`);
    assert.equal(await metric(base, "http_requests_total"), 1);
    assert.equal(await metric(base, "http_request_errors_total"), 0);
    assert.equal(await metric(base, "http_request_duration_seconds_count"), 1);
  });

  test("FAULT_MODE=errors fails requests but not probes", async () => {
    const base = await startWeb({ faultMode: "errors", faultRate: 0.5 }, () => 0.1);
    assert.equal((await fetch(`${base}/info`)).status, 500);
    assert.equal((await fetch(`${base}/healthz`)).status, 200);
    assert.equal(await metric(base, "http_request_errors_total"), 1);
  });

  test("FAULT_MODE=latency delays requests", async () => {
    const base = await startWeb({ faultMode: "latency", faultLatencyMs: 80 });
    const start = Date.now();
    assert.equal((await fetch(`${base}/info`)).status, 200);
    assert.ok(Date.now() - start >= 75);
  });

  test("rejects an invalid FAULT_MODE", () => {
    assert.throws(() => loadConfig({ FAULT_MODE: "chaos" }), /FAULT_MODE/);
  });
});
