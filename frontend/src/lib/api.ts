import "server-only";

import type {
  CopilotAnswer,
  DealDetail,
  DealListItem,
  EmailListItem,
  EmailProcessingResult,
  EmailSource,
} from "@/lib/types";

/** Base URL for server-side calls to the FastAPI backend (container network in Docker). */
export const BACKEND_URL = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8001";

export type ProviderStatus = { configured: boolean; model: string; used_by: string };

export type ReadinessResponse = {
  status: "ok" | "degraded";
  database: {
    status: "ok" | "error";
    latency_ms: number | null;
    server_version: string | null;
    pgvector_version: string | null;
    alembic_revision: string | null;
    error: string | null;
  };
  providers: Record<string, ProviderStatus>;
};

export type BackendHealth =
  | { reachable: true; data: ReadinessResponse }
  | { reachable: false; error: string };

export async function getBackendHealth(): Promise<BackendHealth> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/health/ready`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    // 503 still carries a readiness body describing what is degraded.
    const body = (await res.json().catch(() => null)) as ReadinessResponse | null;
    if ((!res.ok && res.status !== 503) || !body?.database || !body.providers) {
      // e.g. BACKEND_INTERNAL_URL points at a different service
      return { reachable: false, error: `${BACKEND_URL} returned HTTP ${res.status}, not a readiness report` };
    }
    return { reachable: true, data: body };
  } catch (err) {
    return { reachable: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/** GET a backend path; returns null on 404 so pages can call notFound(). */
async function getJson<T>(path: string): Promise<T | null> {
  const res = await fetch(`${BACKEND_URL}${path}`, { cache: "no-store", signal: AbortSignal.timeout(10000) });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Backend ${path} returned ${res.status}`);
  return (await res.json()) as T;
}

export const getDeals = async () => (await getJson<DealListItem[]>("/api/deals")) ?? [];
export const getDeal = (id: string) => getJson<DealDetail>(`/api/deals/${encodeURIComponent(id)}`);
export const getEmails = async () => (await getJson<EmailListItem[]>("/api/emails?limit=25")) ?? [];
export const getEmailResult = (id: string) =>
  getJson<EmailProcessingResult>(`/api/emails/${encodeURIComponent(id)}`);
export const getEmailSource = (id: string) =>
  getJson<EmailSource>(`/api/emails/${encodeURIComponent(id)}/source`);

export async function askCopilot(question: string, dealId?: string): Promise<CopilotAnswer> {
  const res = await fetch(`${BACKEND_URL}/api/copilot`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, deal_id: dealId || null }),
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`Backend /api/copilot returned ${res.status}`);
  return (await res.json()) as CopilotAnswer;
}
