import type { Breadcrumb, DataCollection, Event, Transport, TransportOptions } from "@sentry/react";

export const PRIVATE_DATA_COLLECTION: DataCollection;
export function monitoringSampleRate(value?: string | number | null): number;
export function monitoringLabel(value: unknown, fallback?: string): string | undefined;
export function monitoringUser(id: unknown): { id: string } | null;
export function safeUrl(value: unknown): string | undefined;
export function scrubBreadcrumb(value: Breadcrumb): Breadcrumb | null;
export function scrubEvent<T extends Event>(event: T): T;
export function privateTransport<T extends TransportOptions>(factory: (options: T) => Transport): (options: T) => Transport;
