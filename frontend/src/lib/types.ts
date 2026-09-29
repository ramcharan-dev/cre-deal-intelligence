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
};

export type DealDetail = {
  id: string;
  deal_name: string;
  created_at: string;
  updated_at: string;
  fields: SourcedValue[];
  quotes: {
    id: string;
    lender_id: string;
    lender_name: string;
    lender_contact_name: string | null;
    lender_contact_email: string | null;
    option_label: string | null;
    fields: SourcedValue[];
    updated_at: string;
  }[];
  emails: {
    id: string;
    subject: string;
    sender: string | null;
    sent_at: string | null;
    email_type: string | null;
    summary: string | null;
  }[];
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
