import Link from "next/link";

import { FieldTable } from "@/components/field-table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate, humanize, MATCH_METHOD_LABELS } from "@/lib/format";
import type { EmailProcessingResult } from "@/lib/types";

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_1fr] gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

export function ExtractionResult({ result }: { result: EmailProcessingResult }) {
  const { email, deal } = result;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{humanize(result.email_type)}</Badge>
            {result.duplicate && <Badge variant="outline">Already processed — showing stored result</Badge>}
          </div>
          <CardTitle className="mt-1">{email.subject || "(no subject)"}</CardTitle>
          <CardDescription>{result.summary}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1">
          <Meta label="From">{email.sender ?? "—"}</Meta>
          <Meta label="To">{email.to.join(", ") || "—"}</Meta>
          {email.cc.length > 0 && <Meta label="Cc">{email.cc.join(", ")}</Meta>}
          <Meta label="Date">{formatDate(email.sent_at)}</Meta>
          {email.attachment_names.length > 0 && (
            <Meta label="Attached">{email.attachment_names.join(", ")} (not read)</Meta>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardDescription>Deal</CardDescription>
          {deal ? (
            <>
              <CardTitle className="flex flex-wrap items-center gap-2">
                <Link href={`/deals/${deal.id}`} className="underline-offset-4 hover:underline">
                  {deal.deal_name}
                </Link>
                <Badge variant={deal.created ? "default" : "secondary"}>
                  {deal.created ? "New deal created" : "Existing deal updated"}
                </Badge>
              </CardTitle>
              <p className="text-muted-foreground text-sm">
                <span className="text-foreground font-medium">{MATCH_METHOD_LABELS[deal.match_method]}</span>
                {" — "}
                {deal.match_reason}
              </p>
            </>
          ) : (
            <CardTitle>No deal — this email doesn&apos;t describe a CRE financing deal</CardTitle>
          )}
        </CardHeader>
        {deal && (
          <CardContent>
            <FieldTable rows={result.deal_fields} empty="No deal fields in this email." />
          </CardContent>
        )}
      </Card>

      {result.quotes.map((q) => (
        <Card key={q.id}>
          <CardHeader>
            <CardDescription>Lender quote</CardDescription>
            <CardTitle className="flex flex-wrap items-center gap-2">
              {q.lender.name}
              {q.option_label && <span className="text-muted-foreground font-normal">· {q.option_label}</span>}
              <Badge variant={q.created ? "default" : "secondary"}>{q.created ? "New quote" : "Quote updated"}</Badge>
              {q.lender.created && <Badge variant="outline">New lender</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <FieldTable rows={[...q.lender_contact, ...q.fields]} empty="No quote terms extracted." />
          </CardContent>
        </Card>
      ))}

      {result.issues.length > 0 && (
        <Alert>
          <AlertTitle>
            {result.issues.length} extracted value{result.issues.length === 1 ? " was" : "s were"} rejected by
            validation
          </AlertTitle>
          <AlertDescription>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {result.issues.map((i, n) => (
                <li key={n}>
                  <span className="font-mono text-xs">
                    {i.scope}
                    {i.field ? `.${i.field}` : ""}
                  </span>
                  {i.raw_value ? ` = "${i.raw_value}"` : ""} — {i.reason}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
