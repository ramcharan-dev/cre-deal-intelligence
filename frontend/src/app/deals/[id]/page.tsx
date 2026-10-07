import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { CopilotAsk } from "@/components/copilot-ask";
import {
  Activities,
  DealStatusBadge,
  Documents,
  EmailTimeline,
  LendersApproached,
  PendingActions,
} from "@/components/deal-sections";
import { FieldTable } from "@/components/field-table";
import { QuoteComparison } from "@/components/quote-comparison";
import { SourceLinks } from "@/components/source-links";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDeal } from "@/lib/api";
import { formatDate, formatValue, humanize } from "@/lib/format";
import type { DealDetail, SourcedValue } from "@/lib/types";

const PROPERTY_FIELDS = [
  "property_name",
  "property_address",
  "city",
  "state",
  "property_type",
  "units",
  "square_feet",
  "year_built",
  "occupancy_pct",
  "purchase_price",
  "property_value",
  "noi",
  "cap_rate",
];
const BORROWER_FIELDS = ["sponsor_name", "broker_name"];
const REQUEST_FIELDS = ["transaction_type", "loan_amount_requested", "target_ltv", "target_closing_date"];

function pick(fields: SourcedValue[], names: string[]): SourcedValue[] {
  return names.flatMap((n) => fields.filter((f) => f.field === n));
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string | null }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-xl tabular-nums">{value}</CardTitle>
        {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      </CardHeader>
    </Card>
  );
}

function kpis(deal: DealDetail) {
  const f = Object.fromEntries(deal.fields.map((v) => [v.field, v]));
  const live = deal.quotes.filter(
    (q) => !["declined", "withdrawn"].includes(q.fields.find((x) => x.field === "quote_status")?.value ?? ""),
  );
  const val = (q: DealDetail["quotes"][number], field: string) => q.fields.find((x) => x.field === field)?.value;
  const fixed = live
    .filter((q) => val(q, "rate_type") === "fixed" && val(q, "interest_rate"))
    .sort((a, b) => Number(val(a, "interest_rate")) - Number(val(b, "interest_rate")))[0];
  const proceeds = live
    .filter((q) => val(q, "loan_amount"))
    .sort((a, b) => Number(val(b, "loan_amount")) - Number(val(a, "loan_amount")))[0];
  const next = deal.pending_actions.find((a) => a.due_date);
  return {
    request: f.loan_amount_requested ? formatValue("money", f.loan_amount_requested.value) : "—",
    purpose: f.transaction_type ? humanize(f.transaction_type.value) : null,
    fixed,
    fixedRate: fixed ? formatValue("percent", val(fixed, "interest_rate")!) : "—",
    proceeds,
    maxProceeds: proceeds ? formatValue("money", val(proceeds, "loan_amount")!) : "—",
    next,
  };
}

export default async function DealPage(props: PageProps<"/deals/[id]">) {
  const { id } = await props.params;
  const deal = await getDeal(id);
  if (!deal) notFound();

  const f = Object.fromEntries(deal.fields.map((v) => [v.field, v]));
  const k = kpis(deal);
  const lendersApproached = deal.lenders.length;
  const responded = deal.lenders.filter((l) => l.status !== "awaiting_response").length;
  const property = pick(deal.fields, PROPERTY_FIELDS);
  const borrower = pick(deal.fields, BORROWER_FIELDS);
  const request = pick(deal.fields, REQUEST_FIELDS);
  const subtitle = [
    f.property_type && humanize(f.property_type.value),
    [f.city?.value, f.state?.value].filter(Boolean).join(", "),
    f.sponsor_name && `Borrower: ${f.sponsor_name.value}`,
  ].filter(Boolean);

  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 px-4 py-10">
      <AutoRefresh />
      <div>
        <Link href="/deals" className="text-muted-foreground text-sm hover:underline">
          ← Deal dashboard
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{deal.deal_name}</h1>
          {deal.status && <DealStatusBadge code={deal.status.code} label={deal.status.label} />}
        </div>
        {subtitle.length > 0 && <p className="mt-1 text-sm">{subtitle.join(" · ")}</p>}
        <p className="text-muted-foreground mt-1 text-sm">
          {deal.status?.reason} Created {formatDate(deal.created_at, { dateStyle: "medium" })} · updated{" "}
          {formatDate(deal.updated_at)}
        </p>
        {deal.status && <SourceLinks sources={deal.status.sources} />}
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Kpi label="Loan request" value={k.request} hint={k.purpose} />
        <Kpi
          label="Lenders"
          value={`${responded} / ${lendersApproached}`}
          hint={`responded · ${deal.quotes.length} quote(s)`}
        />
        <Kpi label="Lowest fixed rate" value={k.fixedRate} hint={k.fixed?.lender_name} />
        <Kpi label="Max proceeds" value={k.maxProceeds} hint={k.proceeds?.lender_name} />
        <Kpi
          label="Next deadline"
          value={k.next ? formatDate(k.next.due_date, { dateStyle: "medium", timeZone: "UTC" }) : "—"}
          hint={k.next?.title}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>AI deal summary</CardTitle>
            <CardDescription>
              Built from AI-extracted, validated values and AI email summaries; each line links to its source.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {deal.summary ? (
              <>
                <p className="mb-3 font-medium">{deal.summary.headline}</p>
                <ul className="list-disc space-y-3 pl-5 text-sm">
                  {deal.summary.points.map((p, i) => (
                    <li key={i}>
                      {p.text}
                      <SourceLinks sources={p.sources} />
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="text-muted-foreground text-sm">No summary yet.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Pending actions ({deal.pending_actions.length})</CardTitle>
            <CardDescription>
              Quote expirations, target closing and requests from emails. A request is cleared once its addressees
              reply.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PendingActions actions={deal.pending_actions} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Lender quote comparison ({deal.quotes.length})</CardTitle>
          <CardDescription>All lender quotes side by side, newest stated value for each term.</CardDescription>
        </CardHeader>
        <CardContent>
          {deal.quotes.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No quotes yet. They appear here as soon as a lender&apos;s email is processed.
            </p>
          ) : (
            <QuoteComparison quotes={deal.quotes} />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Lenders approached ({lendersApproached})</CardTitle>
            <CardDescription>Lenders that responded, and recipients of the deal package yet to reply.</CardDescription>
          </CardHeader>
          <CardContent>
            <LendersApproached lenders={deal.lenders} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Borrower / client</CardTitle>
            <CardDescription>Sponsor, broker and the financing requested.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <FieldTable rows={borrower} empty="No borrower or broker extracted yet." />
            {request.length > 0 && <FieldTable rows={request} />}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Property details</CardTitle>
          <CardDescription>Current value of each field and the email and text it came from.</CardDescription>
        </CardHeader>
        <CardContent>
          <FieldTable rows={property} empty="No property details extracted yet." />
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Email communication ({deal.emails.length})</CardTitle>
            <CardDescription>Newest first.</CardDescription>
          </CardHeader>
          <CardContent>
            <EmailTimeline emails={deal.emails} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Activities & meetings</CardTitle>
            <CardDescription>Quotes, declines and updates, plus meetings and calls mentioned in emails.</CardDescription>
          </CardHeader>
          <CardContent>
            <Activities activities={deal.activities} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Documents ({deal.documents.length})</CardTitle>
          <CardDescription>Attachments on this deal&apos;s emails (listed, not read by AI).</CardDescription>
        </CardHeader>
        <CardContent>
          <Documents documents={deal.documents} />
        </CardContent>
      </Card>

      {deal.quotes.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Extracted quote terms with sources</CardTitle>
            <CardDescription>Every value extracted for each quote, with the verbatim text and source email.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-8">
            {deal.quotes.map((q) => (
              <details key={q.id} className="group space-y-2">
                <summary className="cursor-pointer font-medium">
                  {q.lender_name}
                  {q.option_label && <span className="text-muted-foreground font-normal"> · {q.option_label}</span>}
                  <span className="text-muted-foreground font-normal">
                    {" "}
                    · {q.fields.length} term(s)
                    {q.quote_date && <> · quoted {formatDate(q.quote_date, { dateStyle: "medium" })}</>}
                  </span>
                </summary>
                {(q.lender_contact_name || q.lender_contact_email) && (
                  <p className="text-muted-foreground text-sm">
                    {[q.lender_contact_name, q.lender_contact_email].filter(Boolean).join(" · ")}
                  </p>
                )}
                <FieldTable rows={q.fields} empty="No terms extracted." />
              </details>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Ask Copilot about this deal</CardTitle>
        </CardHeader>
        <CardContent>
          <CopilotAsk dealId={deal.id} />
        </CardContent>
      </Card>
    </main>
  );
}
