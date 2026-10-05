import type { FieldType } from "@/lib/types";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const num = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });

export function formatValue(type: FieldType, value: string): string {
  const n = Number(value);
  switch (type) {
    case "money":
      return Number.isFinite(n) ? usd.format(n) : value;
    case "percent":
      return Number.isFinite(n) ? `${num.format(n)}%` : value;
    case "bps":
      return Number.isFinite(n) ? `${num.format(n)} bps` : value;
    case "ratio":
      return Number.isFinite(n) ? `${num.format(n)}x` : value;
    case "integer":
      return Number.isFinite(n) ? num.format(n) : value;
    case "enum":
      return humanize(value);
    case "date":
      return formatDate(value, { dateStyle: "medium", timeZone: "UTC" });
    default:
      return value;
  }
}

export function humanize(value: string): string {
  const s = value.replaceAll("_", " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function formatDate(
  value: string | null | undefined,
  opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" },
): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat("en-US", opts).format(d);
}

export const MATCH_METHOD_LABELS: Record<string, string> = {
  email_thread: "Same email thread",
  address: "Property address match",
  claude: "Matched by AI model",
  property_name: "Property name match",
  new: "New deal",
  none: "Not a deal email",
};

/** Link to the source email with the cited text highlighted. */
export function sourceHref(emailId: string, sourceText?: string | null): string {
  const q = sourceText ? `?highlight=${encodeURIComponent(sourceText)}` : "";
  return `/emails/${emailId}${q}#source`;
}

/** "dokafor@northmarklife.com — Sep 22, 2026": who sent the source email, and when. */
export function sourceLabel(sender: string | null | undefined, sentAt: string | null | undefined): string {
  return `${sender ?? "Email"} — ${formatDate(sentAt, { dateStyle: "medium" })}`;
}
