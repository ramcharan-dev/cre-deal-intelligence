import Link from "next/link";
import { CalendarClock, FileText, Handshake, Mail, MessageSquareQuote, Send, XCircle } from "lucide-react";

import { SourceLinks } from "@/components/source-links";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, humanize, sourceHref } from "@/lib/format";
import type { DealActivity, DealDetail, DealDocument, DealStatusCode, LenderApproach, PendingAction } from "@/lib/types";

const short = (d: string | null | undefined) => formatDate(d, { dateStyle: "medium" });
const day = (d: string | null | undefined) => formatDate(d, { dateStyle: "medium", timeZone: "UTC" });

const STATUS_STYLE: Record<DealStatusCode, string> = {
  intake: "bg-muted text-foreground",
  marketing: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-100",
  quotes_received: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100",
  term_sheet: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  application: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  all_declined: "bg-destructive/10 text-destructive",
  closed: "bg-emerald-600 text-white dark:bg-emerald-500 dark:text-emerald-950",
  inactive: "bg-muted text-muted-foreground",
};

export function DealStatusBadge({ code, label }: { code: DealStatusCode; label: string }) {
  return <Badge className={STATUS_STYLE[code]}>{label}</Badge>;
}

// ---------------------------------------------------------------- pending actions

export function PendingActions({ actions }: { actions: PendingAction[] }) {
  if (actions.length === 0) return <p className="text-muted-foreground text-sm">No pending actions found.</p>;
  return (
    <ul className="divide-y text-sm">
      {actions.map((a, i) => (
        <li key={i} className="py-3 first:pt-0 last:pb-0">
          <div className="flex items-start justify-between gap-3">
            <span className="font-medium">{a.title}</span>
            {a.due_date ? (
              <Badge
                variant={a.overdue ? "destructive" : "outline"}
                className="shrink-0"
                title={a.overdue ? "Overdue" : "Due date"}
              >
                {a.overdue ? "Overdue · " : "Due "}
                {day(a.due_date)}
              </Badge>
            ) : (
              <Badge variant="outline" className="text-muted-foreground shrink-0">
                No date
              </Badge>
            )}
          </div>
          {a.text && <p className="text-muted-foreground mt-1">{a.kind === "request" ? <q>{a.text}</q> : a.text}</p>}
          <p className="mt-1 text-xs">
            <span className="text-muted-foreground">Responsible: </span>
            {a.responsible || "—"}
          </p>
          {a.source && <SourceLinks sources={[a.source]} />}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- lenders

const LENDER_STATUS: Record<LenderApproach["status"], { label: string; variant: "secondary" | "outline" | "destructive" | "default" }> = {
  term_sheet: { label: "Term sheet", variant: "default" },
  application: { label: "Application", variant: "default" },
  quoted: { label: "Quoted", variant: "secondary" },
  awaiting_response: { label: "Awaiting response", variant: "outline" },
  declined: { label: "Declined", variant: "destructive" },
};

export function LendersApproached({ lenders }: { lenders: LenderApproach[] }) {
  if (lenders.length === 0) return <p className="text-muted-foreground text-sm">No lenders contacted yet.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Lender</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="text-right">Quotes</TableHead>
          <TableHead>Last contact</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lenders.map((l) => (
          <TableRow key={`${l.name}-${l.contact_email}`} className="align-top">
            <TableCell className="whitespace-normal">
              <span className="font-medium">{l.name}</span>
              {(l.contact_name || l.contact_email) && (
                <span className="text-muted-foreground block text-xs">
                  {[l.contact_name, l.contact_email].filter(Boolean).join(" · ")}
                </span>
              )}
              {l.source && <SourceLinks sources={[l.source]} />}
            </TableCell>
            <TableCell>
              <Badge variant={LENDER_STATUS[l.status].variant}>{LENDER_STATUS[l.status].label}</Badge>
            </TableCell>
            <TableCell className="text-right tabular-nums">{l.quote_count}</TableCell>
            <TableCell className="text-muted-foreground">{short(l.last_contact_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------- emails, activities, documents

export function EmailTimeline({ emails }: { emails: DealDetail["emails"] }) {
  if (emails.length === 0) return <p className="text-muted-foreground text-sm">No emails yet.</p>;
  return (
    <ol className="relative space-y-4 border-l pl-4 text-sm">
      {emails.map((e) => (
        <li key={e.id} className="relative">
          <span className="bg-primary absolute top-1.5 -left-[21px] size-2.5 rounded-full" aria-hidden />
          <div className="text-muted-foreground text-xs">
            {formatDate(e.sent_at)} {e.email_type && <>· {humanize(e.email_type)}</>}
          </div>
          <Link href={`/emails/${e.id}`} className="font-medium underline-offset-4 hover:underline">
            {e.subject || "(no subject)"}
          </Link>
          <div className="text-muted-foreground text-xs">
            From {e.sender_name || e.sender}
            {e.to.length > 0 && <> to {e.to.join(", ")}</>}
          </div>
          {e.summary && <p className="mt-1">{e.summary}</p>}
          {e.attachment_names.length > 0 && (
            <p className="text-muted-foreground mt-1 text-xs">📎 {e.attachment_names.join(", ")}</p>
          )}
        </li>
      ))}
    </ol>
  );
}

const ACTIVITY_ICON: Record<DealActivity["kind"], typeof Mail> = {
  submission: Send,
  quote: MessageSquareQuote,
  decline: XCircle,
  update: Handshake,
  meeting: CalendarClock,
  email: Mail,
};

export function Activities({ activities }: { activities: DealActivity[] }) {
  if (activities.length === 0) return <p className="text-muted-foreground text-sm">No activity yet.</p>;
  const meetings = activities.filter((a) => a.kind === "meeting");
  const rest = activities.filter((a) => a.kind !== "meeting");
  return (
    <div className="space-y-5 text-sm">
      {meetings.length > 0 && (
        <div>
          <h3 className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">Meetings & calls</h3>
          <ul className="space-y-3">
            {meetings.map((a, i) => (
              <ActivityItem key={i} activity={a} />
            ))}
          </ul>
        </div>
      )}
      <div>
        <h3 className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">Activity log</h3>
        <ul className="space-y-3">
          {rest.map((a, i) => (
            <ActivityItem key={i} activity={a} />
          ))}
        </ul>
      </div>
    </div>
  );
}

function ActivityItem({ activity: a }: { activity: DealActivity }) {
  const Icon = ACTIVITY_ICON[a.kind];
  return (
    <li className="flex gap-3">
      <Icon className={`mt-0.5 size-4 shrink-0 ${a.kind === "decline" ? "text-destructive" : "text-muted-foreground"}`} aria-hidden />
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{a.title}</span>
          <span className="text-muted-foreground text-xs">{short(a.at)}</span>
          {a.scheduled_for && <Badge variant="outline">Scheduled {day(a.scheduled_for)}</Badge>}
        </div>
        {a.text && <p className="text-muted-foreground mt-0.5">{a.kind === "meeting" ? <q>{a.text}</q> : a.text}</p>}
        <SourceLinks sources={[a.source]} />
      </div>
    </li>
  );
}

export function Documents({ documents }: { documents: DealDocument[] }) {
  if (documents.length === 0)
    return <p className="text-muted-foreground text-sm">No attachments on this deal&apos;s emails.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Document</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>From email</TableHead>
          <TableHead>Received</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((d, i) => (
          <TableRow key={`${d.email_id}-${i}`}>
            <TableCell className="whitespace-normal">
              <span className="inline-flex items-center gap-1.5 font-medium">
                <FileText className="text-muted-foreground size-3.5" aria-hidden />
                {d.name}
              </span>
            </TableCell>
            <TableCell>
              <Badge variant="outline">{d.category}</Badge>
            </TableCell>
            <TableCell className="whitespace-normal">
              <Link href={sourceHref(d.email_id)} className="underline-offset-4 hover:underline">
                {d.email_subject || "(no subject)"}
              </Link>
              {d.email_sender && <span className="text-muted-foreground block text-xs">{d.email_sender}</span>}
            </TableCell>
            <TableCell className="text-muted-foreground">{short(d.email_sent_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
