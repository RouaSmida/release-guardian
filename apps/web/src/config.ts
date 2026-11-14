export const FAULT_MODES = ["none", "errors", "latency"] as const;
export type FaultMode = (typeof FAULT_MODES)[number];

export interface Config {
  serviceName: string;
  version: string;
  port: number;
  logLevel: string;
  apiUrl: string;
  faultMode: FaultMode;
  /** Share of requests that fail when faultMode is "errors". */
  faultRate: number;
  /** Delay added to each request when faultMode is "latency". */
  faultLatencyMs: number;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const faultMode = (env.FAULT_MODE ?? "none").trim().toLowerCase();
  if (!FAULT_MODES.includes(faultMode as FaultMode)) {
    throw new Error(`FAULT_MODE must be one of ${FAULT_MODES.join(", ")}, got "${faultMode}"`);
  }
  return {
    serviceName: env.SERVICE_NAME ?? "web",
    version: env.APP_VERSION ?? "dev",
    port: Number(env.PORT ?? 8080),
    logLevel: env.LOG_LEVEL ?? "info",
    apiUrl: env.API_URL ?? "http://localhost:8081",
    faultMode: faultMode as FaultMode,
    faultRate: Number(env.FAULT_RATE ?? 0.5),
    faultLatencyMs: Number(env.FAULT_LATENCY_MS ?? 800),
  };
}
