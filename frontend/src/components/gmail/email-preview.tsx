"use client";

import { useEffect } from "react";
import { Building2, CircleAlert, LoaderCircle, Paperclip, Sparkles, X } from "lucide-react";

import { CategoryBadge, RelevanceMeter, StatusBadge } from "@/components/gmail/email-table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { formatDate } from "@/lib/format";
import type { GmailMessage } from "@/lib/gmail-mock";

function processLabel(m: GmailMessage) {
  switch (m.status) {
    case "processing":
      return "Processing…";
    case "processed":
      return "Process again";
    case "failed":
      return "Retry processing";
    case "skipped":
      return "Process anyway";
    default:
      return "Process with AI";
  }
}

/**
 * Side panel on large screens; full-screen sheet below `lg`.
 */
export function EmailPreview({
  message,
  onClose,
  onProcess,
}: {
  message: GmailMessage;
  onClose: () => void;
  onProcess: (id: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const busy = message.status === "processing";

  return (
    <aside
      aria-label="Email preview"
      className="bg-background fixed inset-0 z-50 flex flex-col overflow-y-auto lg:sticky lg:top-4 lg:z-auto lg:max-h-[calc(100vh-2rem)] lg:rounded-xl lg:ring-1 lg:ring-foreground/10"
    >
      <div className="bg-background sticky top-0 flex items-start justify-between gap-3 border-b px-4 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <CategoryBadge category={message.category} />
          <StatusBadge status={message.status} />
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close preview">
          <X />
        </Button>
      </div>

      <div className="space-y-4 px-4 py-4">
        <div className="space-y-2">
          <h3 className="text-base leading-snug font-semibold">{message.subject}</h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted-foreground">From</dt>
            <dd className="min-w-0">
              <span className="font-medium">{message.senderName}</span>{" "}
              <span className="text-muted-foreground break-all">&lt;{message.senderEmail}&gt;</span>
            </dd>
            <dt className="text-muted-foreground">Date</dt>
            <dd>{formatDate(message.sentAt, { dateStyle: "medium", timeStyle: "short" })}</dd>
            <dt className="text-muted-foreground">Relevance</dt>
            <dd>
              <RelevanceMeter score={message.relevance} />
            </dd>
            {message.dealName && (
              <>
                <dt className="text-muted-foreground">Deal</dt>
                <dd className="flex items-center gap-1.5">
                  <Building2 className="text-muted-foreground size-3.5" aria-hidden />
                  {message.dealName}
                  {message.status !== "processed" && <span className="text-muted-foreground text-xs">(suggested)</span>}
                </dd>
              </>
            )}
          </dl>
        </div>

        {message.attachments.length > 0 && (
          <ul className="flex flex-wrap gap-1.5">
            {message.attachments.map((a) => (
              <li key={a} className="bg-muted/50 flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
                <Paperclip className="text-muted-foreground size-3 shrink-0" aria-hidden />
                <span className="truncate">{a}</span>
              </li>
            ))}
          </ul>
        )}

        {message.status === "failed" && message.error && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>Processing failed</AlertTitle>
            <AlertDescription>{message.error}</AlertDescription>
          </Alert>
        )}

        <div className="flex flex-col gap-1.5">
          <Button onClick={() => onProcess(message.id)} disabled={busy} className="w-full sm:w-auto sm:self-start">
            {busy ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Sparkles data-icon="inline-start" />}
            {processLabel(message)}
          </Button>
          <p className="text-muted-foreground text-xs" role="status">
            {busy
              ? "Extracting deal and quote terms…"
              : message.status === "processed"
                ? `Terms extracted and applied to ${message.dealName ?? "the matched deal"}.`
                : "Extracts deal and lender terms and matches the email to a deal."}
          </p>
        </div>

        <Separator />

        <div className="text-sm leading-relaxed whitespace-pre-wrap">{message.body}</div>
      </div>
    </aside>
  );
}
