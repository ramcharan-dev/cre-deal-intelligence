import Link from "next/link";
import { notFound } from "next/navigation";

import { FieldTable } from "@/components/field-table";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDeal } from "@/lib/api";
import { formatDate, formatValue, humanize } from "@/lib/format";
import type { DealDetail } from "@/lib/types";

const COMPARE_FIELDS = ["loan_amount", "ltv", "interest_rate", "spread_bps", "term_months", "recourse", "quote_status"];

function QuoteComparison({ quotes }: { quotes: DealDetail["quotes"] }) {
  const cell = (q: DealDetail["quotes"][number], field: string) => {
    const f = q.fields.find((x) => x.field === field);
    return f ? formatValue(f.type, f.value) : "—";
  };
  const labels = Object.fromEntries(quotes.flatMap((q) => q.fields.map((f) => [f.field, f.label])));
  const columns = COMPARE_FIELDS.filter((f) => labels[f]);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Lender</TableHead>
          {columns.map((f) => (
            <TableHead key={f}>{labels[f]}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {quotes.map((q) => (
          <TableRow key={q.id}>
            <TableCell className="font-medium">
              {q.lender_name}
              {q.option_label && <span className="text-muted-foreground font-normal"> · {q.option_label}</span>}
            </TableCell>
            {columns.map((f) => (
              <TableCell key={f} className="tabular-nums">
                {cell(q, f)}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default async function DealPage(props: PageProps<"/deals/[id]">) {
  const { id } = await props.params;
  const deal = await getDeal(id);
  if (!deal) notFound();

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 px-4 py-10">
      <div>
        <Link href="/deals" className="text-muted-foreground text-sm hover:underline">
          ← Deals
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">{deal.deal_name}</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Created {formatDate(deal.created_at)} · updated {formatDate(deal.updated_at)}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Deal details</CardTitle>
          <CardDescription>Current value of each field and the email it came from.</CardDescription>
        </CardHeader>
        <CardContent>
          <FieldTable rows={deal.fields} empty="No deal fields extracted yet." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Lender quotes ({deal.quotes.length})</CardTitle>
        </CardHeader>
        <CardContent className="space-y-8">
          {deal.quotes.length === 0 ? (
            <p className="text-muted-foreground text-sm">No quotes yet.</p>
          ) : (
            <>
              {deal.quotes.length > 1 && <QuoteComparison quotes={deal.quotes} />}
              {deal.quotes.map((q) => (
                <div key={q.id} className="space-y-2">
                  <h3 className="font-medium">
                    {q.lender_name}
                    {q.option_label && <span className="text-muted-foreground font-normal"> · {q.option_label}</span>}
                  </h3>
                  {(q.lender_contact_name || q.lender_contact_email) && (
                    <p className="text-muted-foreground text-sm">
                      {[q.lender_contact_name, q.lender_contact_email].filter(Boolean).join(" · ")}
                    </p>
                  )}
                  <FieldTable rows={q.fields} empty="No terms extracted." />
                </div>
              ))}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Emails ({deal.emails.length})</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y">
            {deal.emails.map((e) => (
              <li key={e.id} className="py-3 text-sm first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <Link href={`/emails/${e.id}`} className="font-medium underline-offset-4 hover:underline">
                    {e.subject || "(no subject)"}
                  </Link>
                  <span className="text-muted-foreground text-xs">{formatDate(e.sent_at)}</span>
                </div>
                <div className="text-muted-foreground">
                  {e.email_type ? humanize(e.email_type) : ""} · {e.sender}
                </div>
                {e.summary && <p className="mt-1">{e.summary}</p>}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </main>
  );
}
