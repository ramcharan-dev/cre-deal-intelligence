import Link from "next/link";
import { connection } from "next/server";

import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDeals } from "@/lib/api";
import { formatDate, formatValue, humanize } from "@/lib/format";

export const metadata = { title: "Deals · CRE Deal Intelligence" };

export default async function DealsPage() {
  await connection();
  const deals = await getDeals();

  return (
    <main className="mx-auto w-full max-w-5xl space-y-4 px-4 py-10">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Deals</h1>
          <p className="text-muted-foreground mt-1 text-sm">Created and updated from processed emails.</p>
        </div>
        <Button render={<Link href="/emails" />}>Upload email</Button>
      </div>

      {deals.length === 0 ? (
        <p className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
          No deals yet. <Link href="/emails" className="underline underline-offset-4">Upload an email</Link> to
          create one.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Deal</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Location</TableHead>
              <TableHead className="text-right">Loan request</TableHead>
              <TableHead className="text-right">Quotes</TableHead>
              <TableHead className="text-right">Emails</TableHead>
              <TableHead>Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {deals.map((d) => (
              <TableRow key={d.id}>
                <TableCell className="whitespace-normal">
                  <Link href={`/deals/${d.id}`} className="font-medium underline-offset-4 hover:underline">
                    {d.deal_name}
                  </Link>
                </TableCell>
                <TableCell>{d.property_type ? humanize(d.property_type) : "—"}</TableCell>
                <TableCell>{[d.city, d.state].filter(Boolean).join(", ") || "—"}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {d.loan_amount_requested ? formatValue("money", d.loan_amount_requested) : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">{d.quote_count}</TableCell>
                <TableCell className="text-right tabular-nums">{d.email_count}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(d.updated_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </main>
  );
}
