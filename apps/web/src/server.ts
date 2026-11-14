import { createApp, createLogger } from "./app";
import { loadConfig } from "./config";

const config = loadConfig();
const logger = createLogger(config);
const server = createApp(config, { logger }).listen(config.port, () => {
  logger.info({ port: config.port, fault_mode: config.faultMode }, "starting");
});

// Kubernetes sends SIGTERM first: stop accepting connections, finish in-flight requests.
for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => {
    logger.info({ signal }, "shutting down");
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 10_000).unref();
  });
}
