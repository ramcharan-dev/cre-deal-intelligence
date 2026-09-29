import Link from "next/link";
import { connection } from "next/server";

import { EmailUploader } from "@/components/email-uploader";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getEmails } from "@/lib/api";
import { formatDate, humanize } from "@/lib/format";

export const metadata = { title: "Emails · CRE Deal Intelligence" };

export default async function EmailsPage() {
  await connection();
  const emails = await getEmails();

  return (
    <main className="mx-auto w-full max-w-5xl space-y-10 px-4 py-10">
      <section className="space-y-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Email intelligence</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Upload a broker or lender email. Claude extracts deal and quote terms, matches it to a deal, and records
            the exact source text for every value.
          </p>
        </div>
        <EmailUploader />
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">Recent emails</h2>
        {emails.length === 0 ? (
          <p className="text-muted-foreground text-sm">No emails processed yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Subject</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Deal</TableHead>
                <TableHead>Sent</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {emails.map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="max-w-72 whitespace-normal">
                    {e.status === "processed" ? (
                      <Link href={`/emails/${e.id}`} className="font-medium underline-offset-4 hover:underline">
                        {e.subject || "(no subject)"}
                      </Link>
                    ) : (
                      <span className="font-medium">{e.subject || "(no subject)"}</span>
                    )}
                    <div className="text-muted-foreground text-xs">{e.sender}</div>
                  </TableCell>
                  <TableCell>
                    {e.status === "failed" ? (
                      <Badge variant="destructive" title={e.error ?? undefined}>
                        Failed
                      </Badge>
                    ) : e.email_type ? (
                      humanize(e.email_type)
                    ) : (
                      humanize(e.status)
                    )}
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    {e.deal_id ? (
                      <Link href={`/deals/${e.deal_id}`} className="underline-offset-4 hover:underline">
                        {e.deal_name}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDate(e.sent_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </main>
  );
}
