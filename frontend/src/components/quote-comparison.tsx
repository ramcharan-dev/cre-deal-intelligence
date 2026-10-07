import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatValue, humanize, sourceHref, sourceLabel } from "@/lib/format";
import type { DealQuote as Quote, QuoteValidity, SourcedValue } from "@/lib/types";

/** Terms every comparison shows, in this order, even when no lender stated them ("—"). */
const KEY_ROWS: { field: string; label: string }[] = [
  { field: "loan_amount", label: "Loan amount" },
  { field: "interest_rate", label: "Interest rate" },
  { field: "ltv", label: "LTV" },
  { field: "term_months", label: "Tenure (months)" },
  { field: "origination_fee_pct", label: "Fees · origination" },
  { field: "exit_fee_pct", label: "Fees · exit" },
  { field: "security", label: "Security" },
  { field: "conditions", label: "Conditions" },
  { field: "prepayment_terms", label: "Prepayment" },
];

/** Further terms, shown only when at least one lender stated them. */
const MORE_ROWS = [
  "rate_type",
  "index_name",
  "spread_bps",
  "rate_floor",
  "ltc",
  "amortization_months",
  "interest_only_months",
  "min_dscr",
  "debt_yield",
  "recourse",
  "extension_options",
  "quote_status",
];

/**
 * Which value is "best" per row. Rates are only compared like-for-like: fixed coupons with fixed coupons,
 * floating spreads with floating spreads. Declined quotes are excluded.
 */
const BEST: Record<string, { pick: "max" | "min"; rateType?: "fixed" | "floating" }> = {
  loan_amount: { pick: "max" },
  ltv: { pick: "max" },
  ltc: { pick: "max" },
  interest_rate: { pick: "min", rateType: "fixed" },
  spread_bps: { pick: "min", rateType: "floating" },
  interest_only_months: { pick: "max" },
  origination_fee_pct: { pick: "min" },
  exit_fee_pct: { pick: "min" },
};

const get = (q: Quote, field: string) => q.fields.find((f) => f.field === field);
const isDeclined = (q: Quote) => ["declined", "withdrawn"].includes(get(q, "quote_status")?.value ?? "");

function bestQuoteIds(quotes: Quote[], field: string): Set<string> {
  const rule = BEST[field];
  if (!rule) return new Set();
  const candidates = quotes
    .filter((q) => !isDeclined(q) && (!rule.rateType || get(q, "rate_type")?.value === rule.rateType))
    .map((q) => ({ id: q.id, n: Number(get(q, field)?.value) }))
    .filter((c) => Number.isFinite(c.n));
  if (candidates.length < 2) return new Set();
  const target = rule.pick === "max" ? Math.max(...candidates.map((c) => c.n)) : Math.min(...candidates.map((c) => c.n));
  return new Set(candidates.filter((c) => c.n === target).map((c) => c.id));
}

function SourcedCell({ value }: { value: SourcedValue }) {
  const text = formatValue(value.type, value.value);
  if (!value.source_email_id) return <>{text}</>;
  return (
    <Link
      href={sourceHref(value.source_email_id, value.source_text)}
      title={`“${value.source_text}” — ${sourceLabel(value.source_email_sender, value.source_email_sent_at)}`}
      className="decoration-muted-foreground/50 underline decoration-dotted underline-offset-4"
    >
      {text}
    </Link>
  );
}

/** Interest rate cell: the fixed coupon, or index + spread for floating quotes. */
function RateCell({ q }: { q: Quote }) {
  const rate = get(q, "interest_rate");
  const spread = get(q, "spread_bps");
  const index = get(q, "index_name");
  const type = get(q, "rate_type")?.value;
  if (!rate && !spread) return <span className="text-muted-foreground">—</span>;
  return (
    <>
      {rate ? (
        <SourcedCell value={rate} />
      ) : (
        <>
          {index ? index.value : "Index"} + <SourcedCell value={spread!} />
        </>
      )}
      {type && <span className="text-muted-foreground block text-xs">{humanize(type)}</span>}
    </>
  );
}

const VALIDITY: Record<QuoteValidity, { label: string; className: string }> = {
  valid: { label: "Valid", className: "text-emerald-700 dark:text-emerald-400" },
  expiring_soon: { label: "Expiring soon", className: "text-amber-700 dark:text-amber-400" },
  expired: { label: "Expired", className: "text-destructive" },
  no_expiry: { label: "No expiry stated", className: "text-muted-foreground" },
};

function ValidityCell({ q }: { q: Quote }) {
  const exp = get(q, "expiration_date");
  const v = VALIDITY[q.validity];
  return (
    <>
      {exp && <SourcedCell value={exp} />}
      <span className={`block text-xs ${v.className}`}>
        {v.label}
        {q.days_to_expiry !== null && q.days_to_expiry >= 0 && ` · ${q.days_to_expiry} day(s) left`}
      </span>
    </>
  );
}

function QuoteDateCell({ q }: { q: Quote }) {
  if (!q.quote_date) return <span className="text-muted-foreground">—</span>;
  const updated = q.last_updated_at && q.last_updated_at !== q.quote_date ? q.last_updated_at : null;
  return (
    <>
      {q.source ? (
        <Link
          href={sourceHref(q.source.email_id)}
          title={`${q.source.email_subject} — ${sourceLabel(q.source.email_sender, q.source.email_sent_at)}`}
          className="decoration-muted-foreground/50 underline decoration-dotted underline-offset-4"
        >
          {formatDate(q.quote_date, { dateStyle: "medium" })}
        </Link>
      ) : (
        formatDate(q.quote_date, { dateStyle: "medium" })
      )}
      {updated && (
        <span className="text-muted-foreground block text-xs">
          Revised {formatDate(updated, { dateStyle: "medium" })}
        </span>
      )}
    </>
  );
}

type Row = { key: string; label: string; best?: Set<string>; render: (q: Quote) => React.ReactNode };

function fieldRow(field: string, label: string, quotes: Quote[]): Row {
  return {
    key: field,
    label,
    best: bestQuoteIds(quotes, field),
    render: (q) => {
      const v = get(q, field);
      return v ? <SourcedCell value={v} /> : <span className="text-muted-foreground">—</span>;
    },
  };
}

/**
 * Lenders side by side, one column per quote (several options from one lender are separate columns). Every
 * value links to the email it came from; best comparable values are highlighted.
 */
export function QuoteComparison({ quotes }: { quotes: Quote[] }) {
  const labels = Object.fromEntries(quotes.flatMap((q) => q.fields.map((f) => [f.field, f.label])));
  const rows: Row[] = [
    ...KEY_ROWS.map(({ field, label }) =>
      field === "interest_rate"
        ? { key: field, label, best: bestQuoteIds(quotes, field), render: (q: Quote) => <RateCell q={q} /> }
        : fieldRow(field, label, quotes),
    ),
    { key: "quote_date", label: "Quote date", render: (q) => <QuoteDateCell q={q} /> },
    { key: "validity", label: "Validity", render: (q) => <ValidityCell q={q} /> },
  ];
  const more = MORE_ROWS.filter((field) => quotes.some((q) => get(q, field))).map((field) =>
    fieldRow(field, labels[field] ?? humanize(field), quotes),
  );

  return (
    <div className="space-y-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-40">Term</TableHead>
            {quotes.map((q) => (
              <TableHead key={q.id} className="min-w-44 whitespace-normal align-bottom">
                <span className={isDeclined(q) ? "text-muted-foreground" : "text-foreground"}>{q.lender_name}</span>
                {q.option_label && <span className="text-muted-foreground font-normal"> · {q.option_label}</span>}
                {isDeclined(q) && (
                  <Badge variant="destructive" className="ml-2">
                    Declined
                  </Badge>
                )}
                {q.lender_contact_name && (
                  <span className="text-muted-foreground block text-xs font-normal">{q.lender_contact_name}</span>
                )}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {[...rows, ...more].map((row, i) => (
            <TableRow key={row.key} className={`align-top ${i === rows.length ? "border-t-2" : ""}`}>
              <TableCell className="text-muted-foreground">{row.label}</TableCell>
              {quotes.map((q) => (
                <TableCell
                  key={q.id}
                  className={[
                    "whitespace-normal tabular-nums",
                    isDeclined(q) ? "text-muted-foreground" : "",
                    row.best?.has(q.id)
                      ? "bg-emerald-50 font-semibold text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100"
                      : "",
                  ].join(" ")}
                >
                  {row.render(q)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-muted-foreground text-xs">
        Highlighted: best comparable value (highest proceeds/leverage/IO, lowest fees; fixed rates compared only with
        fixed, floating spreads only with floating). Declined quotes are excluded. Hover or click a value to see its
        source email and verbatim text. Updates automatically as new quote emails are processed.
      </p>
    </div>
  );
}
