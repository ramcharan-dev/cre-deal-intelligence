import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatValue, humanize, sourceHref, sourceLabel } from "@/lib/format";
import type { DealDetail, SourcedValue } from "@/lib/types";

type Quote = DealDetail["quotes"][number];

const ROWS = [
  "loan_amount",
  "ltv",
  "ltc",
  "rate_type",
  "interest_rate",
  "index_name",
  "spread_bps",
  "rate_floor",
  "term_months",
  "amortization_months",
  "interest_only_months",
  "min_dscr",
  "debt_yield",
  "origination_fee_pct",
  "exit_fee_pct",
  "prepayment_terms",
  "recourse",
  "extension_options",
  "quote_status",
  "expiration_date",
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

/** Lenders side by side. Every value links to the email it came from; best comparable values are marked. */
export function QuoteComparison({ quotes }: { quotes: Quote[] }) {
  const rows = ROWS.filter((field) => quotes.some((q) => get(q, field)));
  const labels = Object.fromEntries(quotes.flatMap((q) => q.fields.map((f) => [f.field, f.label])));

  return (
    <div className="space-y-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-44">Term</TableHead>
            {quotes.map((q) => (
              <TableHead key={q.id} className="min-w-40 whitespace-normal align-bottom">
                <span className={isDeclined(q) ? "text-muted-foreground" : "text-foreground"}>{q.lender_name}</span>
                {q.option_label && <span className="text-muted-foreground font-normal"> · {q.option_label}</span>}
                {isDeclined(q) && (
                  <Badge variant="destructive" className="ml-2">
                    Declined
                  </Badge>
                )}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((field) => {
            const best = bestQuoteIds(quotes, field);
            return (
              <TableRow key={field} className="align-top">
                <TableCell className="text-muted-foreground">{labels[field] ?? humanize(field)}</TableCell>
                {quotes.map((q) => {
                  const v = get(q, field);
                  return (
                    <TableCell
                      key={q.id}
                      className={[
                        "whitespace-normal tabular-nums",
                        isDeclined(q) ? "text-muted-foreground" : "",
                        best.has(q.id) ? "bg-emerald-50 font-semibold text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100" : "",
                      ].join(" ")}
                    >
                      {v ? <SourcedCell value={v} /> : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <p className="text-muted-foreground text-xs">
        Highlighted: best comparable value (highest proceeds/leverage/IO, lowest fees; fixed rates compared only with
        fixed, floating spreads only with floating). Declined quotes are excluded. Hover or click a value to see its
        source email and verbatim text.
      </p>
    </div>
  );
}
