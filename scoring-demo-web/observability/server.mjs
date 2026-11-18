import { PRIVATE_DATA_COLLECTION, monitoringLabel, monitoringSampleRate, privateTransport, scrubBreadcrumb, scrubEvent } from "./privacy.mjs";

export async function initializeServerMonitoring(env = process.env, transport) {
  if (!env.SENTRY_FRONTEND_DSN?.trim()) return;
  const Sentry = await import("@sentry/node");
  if (Sentry.getClient()) return Sentry.getClient();
  return Sentry.init({
    dsn: env.SENTRY_FRONTEND_DSN.trim(),
    environment: monitoringLabel(env.SENTRY_FRONTEND_ENVIRONMENT, "production"),
    release: monitoringLabel(env.SENTRY_FRONTEND_RELEASE),
    tracesSampleRate: monitoringSampleRate(env.SENTRY_FRONTEND_TRACES_SAMPLE_RATE),
    traceLifecycle: "static", tracePropagationTargets: [], sendClientReports: false,
    debug: false, spotlight: false, enableRuntimeChannelInjection: false,
    dataCollection: PRIVATE_DATA_COLLECTION,
    defaultIntegrations: false,
    integrations: [Sentry.onUncaughtExceptionIntegration(), Sentry.onUnhandledRejectionIntegration({ mode: "strict" }),
      // Vinext catches SSR errors and reports their Error objects via console.error.
      Sentry.captureConsoleIntegration({ levels: ["error"] }), Sentry.dedupeIntegration()],
    transport: privateTransport(transport ?? Sentry.makeNodeTransport),
    beforeSend: scrubEvent, beforeSendTransaction: scrubEvent, beforeBreadcrumb: scrubBreadcrumb,
    beforeSendLog: () => null, beforeSendMetric: () => null,
  });
}
