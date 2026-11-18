import * as Sentry from "@sentry/react";
import { PRIVATE_DATA_COLLECTION, monitoringLabel, monitoringSampleRate, monitoringUser, privateTransport, scrubBreadcrumb, scrubEvent } from "../../observability/privacy.mjs";

type MonitoringEnvironment = { VITE_SENTRY_DSN?: string; VITE_SENTRY_ENVIRONMENT?: string; VITE_SENTRY_RELEASE?: string; VITE_SENTRY_TRACES_SAMPLE_RATE?: string };

export function initializeBrowserMonitoring(env: MonitoringEnvironment, transport?: Sentry.BrowserOptions["transport"]) {
  if (typeof window === "undefined" || !env.VITE_SENTRY_DSN?.trim() || Sentry.getClient()) return;
  return Sentry.init({
    dsn: env.VITE_SENTRY_DSN.trim(),
    environment: monitoringLabel(env.VITE_SENTRY_ENVIRONMENT, "production"),
    release: monitoringLabel(env.VITE_SENTRY_RELEASE),
    tracesSampleRate: monitoringSampleRate(env.VITE_SENTRY_TRACES_SAMPLE_RATE),
    // Static transactions pass through the same final allowlist as errors.
    traceLifecycle: "static", tracePropagationTargets: [], sendClientReports: false,
    debug: false, spotlight: false,
    dataCollection: PRIVATE_DATA_COLLECTION,
    defaultIntegrations: false,
    integrations: [Sentry.globalHandlersIntegration(), Sentry.dedupeIntegration(),
      Sentry.breadcrumbsIntegration({ dom: false, fetch: true, xhr: true, history: true, sentry: false }),
      Sentry.browserTracingIntegration()],
    transport: privateTransport(transport ?? Sentry.makeFetchTransport),
    transportOptions: { fetchOptions: { credentials: "omit", referrerPolicy: "no-referrer" } },
    beforeSend: scrubEvent, beforeSendTransaction: scrubEvent, beforeBreadcrumb: scrubBreadcrumb,
    beforeSendLog: () => null, beforeSendMetric: () => null,
  });
}

export function setMonitoringUser(userId: string | null) {
  if (typeof window !== "undefined" && Sentry.getClient()) Sentry.setUser(monitoringUser(userId));
}

initializeBrowserMonitoring((import.meta as ImportMeta & { env?: MonitoringEnvironment }).env ?? {});
