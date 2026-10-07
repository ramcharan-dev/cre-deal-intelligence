import Link from "next/link";
import { connection } from "next/server";

import { AutoRefresh } from "@/components/auto-refresh";
import { CopilotAsk } from "@/components/copilot-ask";
import { DealStatusBadge } from "@/components/deal-sections";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDeals } from "@/lib/api";
import { formatDate, formatValue, humanize } from "@/lib/format";

export const metadata = { title: "Deals · CRE Deal Intelligence" };

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}

export default async function DealsPage() {
  await connection();
  const deals = await getDeals();
  const active = deals.filter((d) => !d.is_historical);
  const sum = (key: "quote_count" | "email_count") => deals.reduce((n, d) => n + d[key], 0);

  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 px-4 py-10">
      <AutoRefresh />
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Deal dashboard</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            One row per transaction, created and updated as emails are processed with AI.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/integrations/gmail" className={buttonVariants()}>
            Process Gmail
          </Link>
          <Link href="/emails" className={buttonVariants({ variant: "outline" })}>
            Upload email
          </Link>
        </div>
      </div>

      {deals.length === 0 ? (
        <p className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
          No deals yet. <Link href="/emails" className="underline underline-offset-4">Upload an email</Link> or load
          the demo data (<code>python -m app.scripts.seed_demo</code>).
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Stat label="Active deals" value={active.length} />
            <Stat label="Historical deals" value={deals.length - active.length} />
            <Stat label="Lender quotes" value={sum("quote_count")} />
            <Stat label="Emails processed" value={sum("email_count")} />
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Deal</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Location</TableHead>
                <TableHead className="text-right">Loan request</TableHead>
                <TableHead className="text-right">Quotes</TableHead>
                <TableHead>Lowest fixed rate</TableHead>
                <TableHead className="text-right">Max proceeds</TableHead>
                <TableHead>Last email</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {deals.map((d) => (
                <TableRow key={d.id} className={d.is_historical ? "text-muted-foreground" : undefined}>
                  <TableCell className="whitespace-normal">
                    <Link href={`/deals/${d.id}`} className="font-medium underline-offset-4 hover:underline">
                      {d.deal_name}
                    </Link>
                    {d.is_historical && (
                      <Badge variant="outline" className="ml-2">
                        Historical
                      </Badge>
                    )}
                    {d.sponsor_name && (
                      <span className="text-muted-foreground block text-xs">Borrower: {d.sponsor_name}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {d.status && d.status_label ? <DealStatusBadge code={d.status} label={d.status_label} /> : "—"}
                  </TableCell>
                  <TableCell>{d.property_type ? humanize(d.property_type) : "—"}</TableCell>
                  <TableCell>{[d.city, d.state].filter(Boolean).join(", ") || "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.loan_amount_requested ? formatValue("money", d.loan_amount_requested) : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.quote_count}
                    <span className="text-muted-foreground text-xs"> / {d.lender_count} lenders</span>
                  </TableCell>
                  <TableCell className="whitespace-normal tabular-nums">
                    {d.lowest_fixed_rate ? (
                      <>
                        {formatValue("percent", d.lowest_fixed_rate)}
                        <span className="text-muted-foreground block text-xs">{d.lowest_fixed_rate_lender}</span>
                      </>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.max_loan_amount ? formatValue("money", d.max_loan_amount) : "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatDate(d.last_activity_at, { dateStyle: "medium" })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <Card>
            <CardHeader>
              <CardTitle>Copilot</CardTitle>
              <CardDescription>Ask across all deals, or search historical deal information.</CardDescription>
            </CardHeader>
            <CardContent>
              <CopilotAsk
                suggestions={[
                  "Which lender has the lowest fixed rate on Riverbend?",
                  "Which lender declined?",
                  "What are the pending actions?",
                  "When did Cedar Grove close?",
                  "SOFR floor",
                ]}
              />
            </CardContent>
          </Card>
        </>
      )}
    </main>
  );
}
