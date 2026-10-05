import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatValue, sourceHref } from "@/lib/format";
import type { FieldType } from "@/lib/types";

export type FieldRow = {
  field: string;
  label: string;
  type: FieldType;
  value: string;
  source_text: string | null;
  applied?: boolean;
  source_email_id?: string | null;
  source_email_subject?: string | null;
  source_email_sender?: string | null;
  source_email_sent_at?: string | null;
};

/** Field / value / verbatim source text, optionally linking to the source email. */
export function FieldTable({ rows, empty = "No values extracted." }: { rows: FieldRow[]; empty?: string }) {
  if (rows.length === 0) return <p className="text-muted-foreground text-sm">{empty}</p>;
  const showEmail = rows.some((r) => r.source_email_id);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-44">Field</TableHead>
          <TableHead className="w-44">Value</TableHead>
          <TableHead>Source text</TableHead>
          {showEmail && <TableHead className="w-56">Source email</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.field} className="align-top">
            <TableCell className="text-muted-foreground">{r.label}</TableCell>
            <TableCell className="font-medium whitespace-normal">
              {formatValue(r.type, r.value)}
              {r.applied === false && (
                <Badge variant="outline" className="ml-2" title="A newer email already set this field">
                  older
                </Badge>
              )}
            </TableCell>
            <TableCell className="whitespace-normal">
              {r.source_text ? (
                <q className="text-muted-foreground border-l-2 pl-2 italic before:content-none after:content-none">
                  {r.source_text}
                </q>
              ) : (
                "—"
              )}
            </TableCell>
            {showEmail && (
              <TableCell className="whitespace-normal">
                {r.source_email_id ? (
                  <>
                    <Link
                      href={sourceHref(r.source_email_id, r.source_text)}
                      className="underline-offset-4 hover:underline"
                      title="Open the email with this text highlighted"
                    >
                      {r.source_email_subject || "(no subject)"}
                    </Link>
                    {(r.source_email_sender || r.source_email_sent_at) && (
                      <span className="text-muted-foreground block text-xs">
                        {[r.source_email_sender, r.source_email_sent_at && formatDate(r.source_email_sent_at, { dateStyle: "medium" })]
                          .filter(Boolean)
                          .join(" — ")}
                      </span>
                    )}
                  </>
                ) : (
                  "—"
                )}
              </TableCell>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
