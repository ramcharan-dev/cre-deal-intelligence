/** Mirrors backend/app/api/schemas.py. Safe to import from client components. */

export type FieldType = "text" | "money" | "percent" | "bps" | "ratio" | "integer" | "date" | "enum";

export type ExtractedField = {
  field: string;
  label: string;
  type: FieldType;
  value: string;
  source_text: string;
  applied: boolean;
};

export type ValidationIssue = {
  scope: string;
  field: string | null;
  reason: string;
  raw_value: string | null;
};

export type MatchMethod = "email_thread" | "address" | "claude" | "property_name" | "new" | "none";

export type EmailProcessingResult = {
  email: {
    id: string;
    subject: string;
    sender: string | null;
    to: string[];
    cc: string[];
    sent_at: string | null;
    message_id: string | null;
    filename: string | null;
    attachment_names: string[];
  };
  status: string;
  email_type: "deal_submission" | "lender_quote" | "deal_update" | "other";
  summary: string;
  model: string | null;
  duplicate: boolean;
  deal: {
    id: string;
    deal_name: string;
    created: boolean;
    match_method: MatchMethod;
    match_reason: string;
  } | null;
  deal_match_reasoning: string;
  deal_fields: ExtractedField[];
  quotes: {
    id: string;
    created: boolean;
    option_label: string | null;
    lender: { id: string; name: string; created: boolean };
    lender_contact: ExtractedField[];
    fields: ExtractedField[];
  }[];
  issues: ValidationIssue[];
};

export type EmailListItem = {
  id: string;
  subject: string;
  sender: string | null;
  sent_at: string | null;
  created_at: string;
  status: "received" | "processed" | "failed";
  email_type: string | null;
  error: string | null;
  deal_id: string | null;
  deal_name: string | null;
};

export type SourcedValue = {
  field: string;
  label: string;
  type: FieldType;
  value: string;
  source_email_id: string | null;
  source_email_subject: string | null;
  source_text: string | null;
  extracted_at: string | null;
  source_email_sender?: string | null;
  source_email_sent_at?: string | null;
};

export type DealListItem = {
  id: string;
  deal_name: string;
  property_type: string | null;
  city: string | null;
  state: string | null;
  loan_amount_requested: string | null;
  quote_count: number;
  email_count: number;
  updated_at: string;
  lender_count: number;
  max_loan_amount: string | null;
  lowest_fixed_rate: string | null;
  lowest_fixed_rate_lender: string | null;
  last_activity_at: string | null;
  is_historical: boolean;
  status: DealStatusCode | null;
  status_label: string | null;
  sponsor_name: string | null;
  property_name: string | null;
};

export type DealStatusCode =
  | "intake"
  | "marketing"
  | "quotes_received"
  | "term_sheet"
  | "application"
  | "all_declined"
  | "closed"
  | "inactive";

export type DealStatus = { code: DealStatusCode; label: string; reason: string; sources: SourceRef[] };

export type QuoteValidity = "valid" | "expiring_soon" | "expired" | "no_expiry";

export type DealQuote = {
  id: string;
  lender_id: string;
  lender_name: string;
  lender_contact_name: string | null;
  lender_contact_email: string | null;
  option_label: string | null;
  fields: SourcedValue[];
  updated_at: string;
  /** Sent date of the first email that stated this quote. */
  quote_date: string | null;
  last_updated_at: string | null;
  source: SourceRef | null;
  validity: QuoteValidity;
  days_to_expiry: number | null;
};

export type LenderApproach = {
  name: string;
  lender_id: string | null;
  contact_name: string | null;
  contact_email: string | null;
  status: "quoted" | "term_sheet" | "application" | "declined" | "awaiting_response";
  quote_count: number;
  first_contact_at: string | null;
  last_contact_at: string | null;
  source: SourceRef | null;
};

export type DealDocument = {
  name: string;
  category: string;
  email_id: string;
  email_subject: string;
  email_sender: string | null;
  email_sent_at: string | null;
};

export type DealActivity = {
  kind: "submission" | "quote" | "decline" | "update" | "meeting" | "email";
  title: string;
  text: string | null;
  at: string | null;
  scheduled_for: string | null;
  source: SourceRef;
};

export type PendingAction = {
  kind: "deadline" | "request";
  title: string;
  text: string | null;
  responsible: string | null;
  due_date: string | null;
  overdue: boolean;
  source: SourceRef | null;
};

export type DealDetail = {
  id: string;
  deal_name: string;
  created_at: string;
  updated_at: string;
  fields: SourcedValue[];
  quotes: DealQuote[];
  emails: {
    id: string;
    subject: string;
    sender: string | null;
    sent_at: string | null;
    email_type: string | null;
    summary: string | null;
    sender_name: string | null;
    to: string[];
    attachment_names: string[];
  }[];
  summary: DealSummary | null;
  status: DealStatus | null;
  lenders: LenderApproach[];
  documents: DealDocument[];
  activities: DealActivity[];
  pending_actions: PendingAction[];
};

/** Where a statement comes from: the email and, when available, the verbatim text. */
export type SourceRef = {
  email_id: string;
  email_subject: string;
  email_sender: string | null;
  email_sent_at: string | null;
  source_text: string | null;
  label: string | null;
};

export type DealSummary = {
  headline: string;
  points: { text: string; sources: SourceRef[] }[];
};

export type EmailSource = {
  id: string;
  subject: string;
  sender: string | null;
  sent_at: string | null;
  deal_id: string | null;
  email_type: string | null;
  summary: string | null;
  attachment_names: string[];
  text: string;
};

export type CopilotAnswer = {
  question: string;
  intent: string;
  mode: "structured" | "search" | "llm";
  deal_id: string | null;
  deal_name: string | null;
  answer: string;
  items: { title: string | null; text: string; deal_id: string | null; sources: SourceRef[] }[];
};

export type ApiErrorDetail = {
  code: string;
  message: string;
  retryable: boolean;
  email_id: string | null;
  request_id: string | null;
};

/** Normalizes backend error bodies: `{detail: ApiErrorDetail}`, or FastAPI's 422 `{detail: [...]}`. */
export function parseApiError(body: unknown, status: number): ApiErrorDetail {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const base = { code: `http_${status}`, retryable: false, email_id: null, request_id: null };
  if (detail && typeof detail === "object" && !Array.isArray(detail) && "message" in detail) {
    return { ...base, ...(detail as Partial<ApiErrorDetail>), message: String((detail as ApiErrorDetail).message) };
  }
  if (Array.isArray(detail)) {
    return { ...base, code: "validation_error", message: detail.map((d) => d?.msg ?? String(d)).join("; ") };
  }
  if (typeof detail === "string") return { ...base, message: detail };
  return { ...base, message: `Request failed (${status})` };
}
