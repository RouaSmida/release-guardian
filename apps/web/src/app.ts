import http from "node:http";
import path from "node:path";

import express, { type Express, type NextFunction, type Request, type Response } from "express";
import pino, { type Logger } from "pino";
import { Counter, Gauge, Histogram, Registry } from "prom-client";

import type { Config } from "./config";

// Probes and scrapes are not user traffic: they are neither measured nor faulted.
const UNMEASURED_PATHS = new Set(["/healthz", "/readyz", "/metrics"]);
const LATENCY_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5];

export function createLogger(config: Config): Logger {
  return pino({
    level: config.logLevel,
    messageKey: "msg",
    base: { service: config.serviceName, version: config.version },
    timestamp: pino.stdTimeFunctions.isoTime,
    formatters: { level: (label) => ({ level: label }) },
  });
}

/** Map a request to a small, fixed set of route labels (bounded metric cardinality). */
function routeLabel(req: Request, status: number): string {
  if (req.path === "/" || req.path === "/info") return req.path;
  if (req.path.startsWith("/api/")) return "/api/*";
  return status === 404 ? "unmatched" : "static";
}

/** Forward /api/* to the API service. A new connection per request lets the
 *  Kubernetes Service spread traffic over stable and canary pods. */
function apiProxy(apiUrl: URL, logger: Logger) {
  return (req: Request, res: Response): void => {
    const headers = { ...req.headers, host: apiUrl.host };
    delete headers.connection;
    const upstream = http.request(
      {
        hostname: apiUrl.hostname,
        port: apiUrl.port || 80,
        path: req.originalUrl,
        method: req.method,
        headers,
        agent: false,
        timeout: 5000,
      },
      (upstreamRes) => {
        res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.headers);
        upstreamRes.pipe(res);
      },
    );
    upstream.on("timeout", () => upstream.destroy(new Error("upstream timeout")));
    upstream.on("error", (err) => {
      logger.error({ err: err.message, path: req.originalUrl }, "api request failed");
      if (res.headersSent) res.end();
      else res.status(502).json({ detail: "api unavailable" });
    });
    req.pipe(upstream);
  };
}

export interface AppOptions {
  logger?: Logger;
  /** Injectable randomness so tests are deterministic. */
  random?: () => number;
}

export function createApp(config: Config, options: AppOptions = {}): Express {
  const logger = options.logger ?? createLogger(config);
  const random = options.random ?? Math.random;

  // A registry per app instance keeps unit tests isolated from each other.
  const registry = new Registry();
  const labelNames = ["service", "method", "route"] as const;
  const requestsTotal = new Counter({
    name: "http_requests_total",
    help: "HTTP requests handled.",
    labelNames: [...labelNames, "status"],
    registers: [registry],
  });
  const errorsTotal = new Counter({
    name: "http_request_errors_total",
    help: "HTTP requests answered with 5xx.",
    labelNames,
    registers: [registry],
  });
  const duration = new Histogram({
    name: "http_request_duration_seconds",
    help: "HTTP request duration in seconds.",
    labelNames,
    buckets: LATENCY_BUCKETS,
    registers: [registry],
  });
  new Gauge({
    name: "app_info",
    help: "Build information.",
    labelNames: ["service", "version"],
    registers: [registry],
  })
    .labels(config.serviceName, config.version)
    .set(1);

  const app = express();
  app.disable("x-powered-by");

  app.get("/healthz", (_req, res) => {
    res.json({ status: "ok" });
  });
  // The web tier is ready as soon as it can serve pages. It deliberately does
  // not check the API: a failing dependency must not take the frontend down too.
  app.get("/readyz", (_req, res) => {
    res.json({ status: "ready" });
  });
  app.get("/metrics", async (_req, res) => {
    res.type(registry.contentType).send(await registry.metrics());
  });

  // Metrics, access log and fault injection for all user-facing routes.
  app.use((req: Request, res: Response, next: NextFunction) => {
    if (UNMEASURED_PATHS.has(req.path)) return next();
    const start = process.hrtime.bigint();
    let fault: string | null = null;

    res.on("finish", () => {
      const seconds = Number(process.hrtime.bigint() - start) / 1e9;
      const labels = {
        service: config.serviceName,
        method: req.method,
        route: routeLabel(req, res.statusCode),
      };
      requestsTotal.inc({ ...labels, status: String(res.statusCode) });
      duration.observe(labels, seconds);
      if (res.statusCode >= 500) errorsTotal.inc(labels);
      logger.info(
        {
          method: req.method,
          path: req.path,
          status: res.statusCode,
          duration_ms: Math.round(seconds * 1e5) / 100,
          fault,
        },
        "request",
      );
    });

    if (config.faultMode === "errors" && random() < config.faultRate) {
      fault = "errors";
      res.status(500).json({ detail: "injected fault" });
      return undefined;
    }
    if (config.faultMode === "latency") {
      fault = "latency";
      setTimeout(next, config.faultLatencyMs);
      return undefined;
    }
    return next();
  });

  app.get("/info", (_req, res) => {
    res.json({
      service: config.serviceName,
      version: config.version,
      fault_mode: config.faultMode,
    });
  });
  app.use("/api", apiProxy(new URL(config.apiUrl), logger));
  app.use(express.static(path.join(__dirname, "..", "public")));
  app.use((_req, res) => {
    res.status(404).json({ detail: "not found" });
  });

  return app;
}
