"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Building2, CircleAlert, ExternalLink, FileText, LoaderCircle, Paperclip, Sparkles, X } from "lucide-react";

import { CategoryBadge, RelevanceMeter, StatusBadge } from "@/components/gmail/email-table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { formatDate } from "@/lib/format";
import type { GmailMessage, GmailMessageDetail } from "@/lib/gmail";
import type { ApiErrorDetail } from "@/lib/types";

function processLabel(m: GmailMessage) {
  switch (m.status) {
    case "processing":
      return "Processing…";
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
 * `detail` is the loaded message (with body); null while loading or when loading failed.
 */
export function EmailPreview({
  message,
  detail,
  detailError,
  processError,
  onClose,
  onProcess,
  onRetryDetail,
}: {
  message: GmailMessage;
  detail: GmailMessageDetail | null;
  detailError: ApiErrorDetail | null;
  processError: ApiErrorDetail | null;
  onClose: () => void;
  onProcess: (id: string) => void;
  onRetryDetail: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const busy = message.status === "processing";
  const error = processError?.message ?? (message.status === "failed" ? message.error : null);

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
          <h3 className="text-base leading-snug font-semibold">{message.subject || "(no subject)"}</h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted-foreground">From</dt>
            <dd className="min-w-0">
              {message.sender_name && <span className="font-medium">{message.sender_name} </span>}
              <span className={message.sender_name ? "text-muted-foreground break-all" : "break-all"}>
                {message.sender_name ? `<${message.sender_email}>` : (message.sender_email ?? "—")}
              </span>
            </dd>
            {detail && detail.to.length > 0 && (
              <>
                <dt className="text-muted-foreground">To</dt>
                <dd className="text-muted-foreground min-w-0 break-words">{detail.to.join(", ")}</dd>
              </>
            )}
            <dt className="text-muted-foreground">Date</dt>
            <dd>{formatDate(message.sent_at, { dateStyle: "medium", timeStyle: "short" })}</dd>
            <dt className="text-muted-foreground">Relevance</dt>
            <dd>
              <RelevanceMeter score={message.relevance} />
            </dd>
            {message.deal_name && message.deal_id && (
              <>
                <dt className="text-muted-foreground">Deal</dt>
                <dd>
                  <Link
                    href={`/deals/${message.deal_id}`}
                    className="inline-flex items-center gap-1.5 underline-offset-4 hover:underline"
                  >
                    <Building2 className="text-muted-foreground size-3.5" aria-hidden />
                    {message.deal_name}
                  </Link>
                </dd>
              </>
            )}
          </dl>
        </div>

        {message.attachment_names.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Attachments (not read by AI)">
            {message.attachment_names.map((a) => (
              <li key={a} className="bg-muted/50 flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
                <Paperclip className="text-muted-foreground size-3 shrink-0" aria-hidden />
                <span className="truncate">{a}</span>
              </li>
            ))}
          </ul>
        )}

        {error && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>Processing failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-2">
            {message.status === "processed" && message.email_id ? (
              <>
                {message.deal_id && (
                  <Link href={`/deals/${message.deal_id}`} className={buttonVariants()}>
                    <Building2 data-icon="inline-start" />
                    Open deal dashboard
                  </Link>
                )}
                <Link
                  href={`/emails/${message.email_id}`}
                  className={buttonVariants({ variant: message.deal_id ? "outline" : "default" })}
                >
                  <FileText data-icon="inline-start" />
                  View extraction
                </Link>
              </>
            ) : (
              <Button onClick={() => onProcess(message.id)} disabled={busy}>
                {busy ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Sparkles data-icon="inline-start" />}
                {processLabel(message)}
              </Button>
            )}
            {detail && (
              <a
                href={detail.gmail_url}
                target="_blank"
                rel="noreferrer"
                className={buttonVariants({ variant: "outline" })}
              >
                <ExternalLink data-icon="inline-start" />
                Open in Gmail
              </a>
            )}
          </div>
          <p className="text-muted-foreground text-xs" role="status">
            {busy
              ? "AI is extracting deal and quote terms. With a local model this can take a couple of minutes."
              : message.status === "processed"
                ? message.deal_name
                  ? `Terms extracted and applied to ${message.deal_name}.`
                  : "Processed — no CRE deal was found in this email."
                : message.status === "skipped"
                  ? "Below the relevance threshold. Processing fetches the full message from Gmail first."
                  : "Extracts deal and lender terms and matches the email to a deal."}
          </p>
        </div>

        <Separator />

        {detail ? (
          detail.body_text !== null ? (
            <div className="text-sm leading-relaxed break-words whitespace-pre-wrap">{detail.body_text}</div>
          ) : (
            <div className="space-y-1.5">
              <p className="text-muted-foreground text-xs">Preview only — this message wasn’t stored in full.</p>
              <p className="text-sm leading-relaxed">{detail.snippet}</p>
            </div>
          )
        ) : detailError ? (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>Couldn’t load the message</AlertTitle>
            <AlertDescription>
              <p>{detailError.message}</p>
              <Button size="sm" variant="outline" className="mt-2" onClick={onRetryDetail}>
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : (
          <div className="animate-pulse space-y-2" role="status" aria-label="Loading message">
            {[92, 100, 85, 96, 60].map((w, i) => (
              <div key={i} className="bg-muted h-3 rounded" style={{ width: `${w}%` }} />
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
