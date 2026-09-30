"use client";

import { LoaderCircle, Paperclip } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import {
  CATEGORY_LABELS,
  relevanceLevel,
  type EmailCategory,
  type GmailMessage,
  type ProcessingStatus,
} from "@/lib/gmail-mock";
import { cn } from "@/lib/utils";

const STATUS: Record<ProcessingStatus, { label: string; dot: string }> = {
  pending: { label: "Pending", dot: "bg-amber-500" },
  processing: { label: "Processing", dot: "bg-sky-500" },
  processed: { label: "Processed", dot: "bg-emerald-500" },
  failed: { label: "Failed", dot: "bg-destructive" },
  skipped: { label: "Skipped", dot: "bg-muted-foreground/40" },
};

export function CategoryBadge({ category }: { category: EmailCategory }) {
  return <Badge variant={category === "other" ? "outline" : "secondary"}>{CATEGORY_LABELS[category]}</Badge>;
}

export function StatusBadge({ status }: { status: ProcessingStatus }) {
  const s = STATUS[status];
  if (status === "failed") return <Badge variant="destructive">{s.label}</Badge>;
  return (
    <Badge variant="outline">
      {status === "processing" ? (
        <LoaderCircle className="animate-spin" aria-hidden />
      ) : (
        <span className={cn("size-1.5 rounded-full", s.dot)} aria-hidden />
      )}
      {s.label}
    </Badge>
  );
}

export function RelevanceMeter({ score }: { score: number }) {
  const level = relevanceLevel(score);
  return (
    <div className="flex items-center gap-2" title={`Relevance ${score}/100`}>
      <div className="bg-muted h-1.5 w-12 overflow-hidden rounded-full" aria-hidden>
        <div
          className={cn("h-full rounded-full", level === "Low" ? "bg-muted-foreground/40" : "bg-foreground")}
          style={{ width: `${score}%`, opacity: level === "Medium" ? 0.55 : 1 }}
        />
      </div>
      <span className="text-muted-foreground text-xs tabular-nums">
        {score}
        <span className="sr-only"> of 100, {level} relevance</span>
      </span>
    </div>
  );
}

export function EmailTable({
  messages,
  selectedId,
  compact,
  onSelect,
}: {
  messages: GmailMessage[];
  selectedId: string | null;
  /** Preview panel is open beside the table: fold the sender under the subject. */
  compact: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className={cn(compact ? "hidden" : "hidden md:table-cell")}>Sender</TableHead>
          <TableHead>Subject</TableHead>
          <TableHead>Date</TableHead>
          <TableHead>Category</TableHead>
          <TableHead>Relevance</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {messages.map((m) => (
          <TableRow
            key={m.id}
            data-state={m.id === selectedId ? "selected" : undefined}
            className="cursor-pointer"
            onClick={() => onSelect(m.id)}
          >
            <TableCell className={cn("max-w-40", compact ? "hidden" : "hidden md:table-cell")}>
              <div className="truncate font-medium">{m.senderName}</div>
              <div className="text-muted-foreground truncate text-xs">{m.senderEmail}</div>
            </TableCell>
            <TableCell className="max-w-72 min-w-56 whitespace-normal">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(m.id);
                }}
                aria-current={m.id === selectedId ? "true" : undefined}
                className="focus-visible:ring-ring/50 line-clamp-2 rounded-sm text-left font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-3"
              >
                {m.subject}
              </button>
              <div className={cn("text-muted-foreground flex items-center gap-1 text-xs", !compact && "md:hidden")}>
                <span className="truncate">{m.senderName}</span>
                {m.attachments.length > 0 && <Paperclip className="size-3 shrink-0" aria-label="Has attachments" />}
              </div>
              {!compact && m.attachments.length > 0 && (
                <div className="text-muted-foreground hidden items-center gap-1 text-xs md:flex">
                  <Paperclip className="size-3" aria-hidden />
                  {m.attachments.length} attachment{m.attachments.length > 1 ? "s" : ""}
                </div>
              )}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {formatDate(m.sentAt, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
            </TableCell>
            <TableCell>
              <CategoryBadge category={m.category} />
            </TableCell>
            <TableCell>
              <RelevanceMeter score={m.relevance} />
            </TableCell>
            <TableCell>
              <StatusBadge status={m.status} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function EmailTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="divide-y rounded-lg border" role="status" aria-label="Loading emails">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex animate-pulse items-center gap-4 px-3 py-3">
          <div className="hidden w-32 space-y-1.5 md:block">
            <div className="bg-muted h-3 w-24 rounded" />
            <div className="bg-muted h-2.5 w-32 rounded" />
          </div>
          <div className="flex-1 space-y-1.5">
            <div className="bg-muted h-3 rounded" style={{ width: `${85 - ((i * 13) % 35)}%` }} />
            <div className="bg-muted h-2.5 w-24 rounded md:hidden" />
          </div>
          <div className="bg-muted hidden h-3 w-16 rounded sm:block" />
          <div className="bg-muted h-5 w-20 rounded-full" />
          <div className="bg-muted hidden h-1.5 w-12 rounded-full sm:block" />
        </div>
      ))}
    </div>
  );
}
