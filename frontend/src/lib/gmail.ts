/** Browser-side client for the Gmail integration (via the /api/gmail proxy). Mirrors backend/app/api/schemas.py. */

import { parseApiError, type ApiErrorDetail, type EmailProcessingResult } from "@/lib/types";

export const EMAIL_CATEGORIES = ["lender_quote", "deal_update", "financing", "term_sheet", "follow_up", "other"] as const;
export type EmailCategory = (typeof EMAIL_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<EmailCategory, string> = {
  lender_quote: "Lender Quote",
  deal_update: "Deal Update",
  financing: "Financing",
  term_sheet: "Term Sheet",
  follow_up: "Follow-up",
  other: "Other",
};

/** `processing` exists only client-side, while a process request is in flight. */
export type ProcessingStatus = "pending" | "processing" | "processed" | "failed" | "skipped";

export type GmailLastSync = { at: string; scanned: number; new: number };

export type GmailConnection = {
  configured: boolean;
  connected: boolean;
  status: "not_connected" | "connected" | "reauth_required";
  email_address: string | null;
  scopes: string[];
  connected_at: string | null;
  last_sync: GmailLastSync | null;
};

export type GmailStats = { scanned: number; relevant: number; processed: number; deals_updated: number };

export type GmailMessage = {
  id: string;
  gmail_id: string;
  thread_id: string | null;
  subject: string;
  sender_name: string | null;
  sender_email: string | null;
  sent_at: string | null;
  snippet: string;
  category: EmailCategory;
  relevance: number;
  status: ProcessingStatus;
  error: string | null;
  attachment_names: string[];
  email_id: string | null;
  email_type: string | null;
  deal_id: string | null;
  deal_name: string | null;
};

export type GmailMessageDetail = GmailMessage & {
  to: string[];
  cc: string[];
  body_text: string | null;
  gmail_url: string;
};

export type GmailMessageList = { messages: GmailMessage[]; stats: GmailStats; last_sync: GmailLastSync | null };

export type GmailSyncResult = {
  scanned: number;
  new: number;
  duplicates: number;
  relevant_new: number;
  synced_at: string;
  stats: GmailStats;
};

export type GmailProcessResult = { message: GmailMessage; result: EmailProcessingResult };

/** Error thrown by every call below; `detail` is the backend's uniform error body. */
export class GmailApiError extends Error {
  constructor(
    readonly detail: ApiErrorDetail,
    readonly status: number,
  ) {
    super(detail.message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/gmail${path}`, { cache: "no-store", ...init });
  } catch {
    throw new GmailApiError(
      { code: "network", message: "Could not reach the server.", retryable: true, email_id: null, request_id: null },
      0,
    );
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new GmailApiError(parseApiError(body, res.status), res.status);
  return body as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

/** Full-page navigation target that starts Google OAuth. */
export const OAUTH_START_URL = "/api/gmail/oauth/start";

export const getConnection = () => request<GmailConnection>("/connection");
export const disconnectGmail = () => request<{ disconnected: boolean; revoked: boolean }>("/connection", { method: "DELETE" });
export const syncGmail = (range: { after?: string; before?: string }) => request<GmailSyncResult>("/sync", json(range));
export const listMessages = () => request<GmailMessageList>("/messages");
export const getMessage = (id: string) => request<GmailMessageDetail>(`/messages/${encodeURIComponent(id)}`);
export const processMessage = (id: string) =>
  request<GmailProcessResult>(`/messages/${encodeURIComponent(id)}/process`, { method: "POST" });

export function relevanceLevel(score: number): "High" | "Medium" | "Low" {
  return score >= 75 ? "High" : score >= 40 ? "Medium" : "Low";
}

/** Messages for `?gmail_error=` codes set by the OAuth callback redirect. */
export const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  access_denied: "Google sign-in was cancelled, so Gmail wasn’t connected.",
  oauth_error: "Google couldn’t complete the sign-in. Please try again.",
  invalid_state: "The sign-in session expired or didn’t match. Please start again from this page.",
  invalid_request: "Google didn’t return an authorization code. Please try again.",
  scope_denied: "Read-only Gmail access wasn’t granted. Tick the Gmail permission on Google’s consent screen.",
  no_refresh_token: "Google didn’t issue offline access. Remove this app at myaccount.google.com/permissions and reconnect.",
  oauth_failed: "Google rejected the authorization code. Please try again.",
  gmail_not_configured: "Gmail integration isn’t configured on the server yet.",
  gmail_permission_denied: "Google denied Gmail access. Check that the Gmail API is enabled for the OAuth project.",
  gmail_rate_limited: "Google is rate limiting requests. Try again in a minute.",
  gmail_upstream_error: "Google returned an error. Please try again.",
  gmail_unreachable: "Couldn’t reach Google. Check the server’s network and try again.",
  gmail_timeout: "Google didn’t respond in time. Please try again.",
};
