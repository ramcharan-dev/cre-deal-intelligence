"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { ExtractionResult } from "@/components/extraction-result";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { parseApiError, type ApiErrorDetail, type EmailProcessingResult } from "@/lib/types";

type Mode = "file" | "paste";

function formatPastedSource(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const hasHeaders = /^(?:From|Subject|Date|To):/im.test(trimmed);
  if (hasHeaders) return trimmed;

  const firstLine = trimmed.split("\n")[0].replace(/^[#*\s-]+/, "").slice(0, 75).trim();
  const subject = firstLine.length > 3 ? firstLine : "Commercial Real Estate Opportunity";
  const now = new Date().toUTCString();
  return `From: broker@deal-intake.com\nTo: acquisitions@ourfirm.com\nSubject: ${subject}\nDate: ${now}\n\n${trimmed}`;
}

export function EmailUploader() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiErrorDetail | null>(null);
  const [result, setResult] = useState<EmailProcessingResult | null>(null);

  const payload =
    mode === "file"
      ? file
      : source.trim()
        ? new File([formatPastedSource(source)], "pasted.eml", { type: "message/rfc822" })
        : null;


  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!payload) return;
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", payload, payload.name);
      const res = await fetch("/api/emails", { method: "POST", body: form });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        setError(parseApiError(body, res.status));
        if (res.status >= 422) router.refresh(); // a failed email was recorded; show it in the list
        return;
      }
      setResult(body as EmailProcessingResult);
      setFile(null);
      setSource("");
      if (fileInput.current) fileInput.current.value = "";
      router.refresh(); // update the recent emails list
    } catch {
      setError({ code: "network", message: "Could not reach the server.", retryable: true, email_id: null, request_id: null });
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="space-y-3">
        <Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
          <TabsList>
            <TabsTrigger value="file" type="button" onClick={() => setMode("file")}>
              Upload .eml
            </TabsTrigger>
            <TabsTrigger value="paste" type="button" onClick={() => setMode("paste")}>
              Paste source
            </TabsTrigger>
          </TabsList>
          {mode === "file" && (
            <div role="tabpanel" data-slot="tabs-content" className="flex-1 text-sm outline-none">
              <label
                htmlFor="eml"
                className="hover:bg-muted/50 flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-4 py-8 text-center text-sm"
              >
                <span className="font-medium">{file ? file.name : "Choose an email file"}</span>
                <span className="text-muted-foreground">
                  {file ? `${(file.size / 1024).toFixed(1)} KB` : ".eml exported from Outlook, Gmail or Apple Mail"}
                </span>
              </label>
              <input
                id="eml"
                ref={fileInput}
                type="file"
                accept=".eml,message/rfc822"
                className="sr-only"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
          )}

          {mode === "paste" && (
            <div role="tabpanel" data-slot="tabs-content" className="flex-1 text-sm outline-none">
              <Textarea
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder={"Paste email message source or plain text here...\n\nExample:\nFrom: broker@capital.com\nSubject: The Oaks Apartments - $20MM Refinance\n\nSeeking $20,000,000 refinance for The Oaks Apartments, 1200 Oak St, Austin, TX.\nMultifamily, 160 units, built in 2017, 95% occupancy.\nSponsor: Lone Star Holdings."}
                className="min-h-56 font-mono text-xs"
                aria-label="Raw email source or plain text"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Paste email text, a broker memo, or raw RFC-822 message source with headers.
              </p>
            </div>
          )}
        </Tabs>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!payload || pending}>
            {pending ? "Extracting…" : "Extract deal data"}
          </Button>
          {pending && (
            <span className="text-muted-foreground text-sm" role="status">
              Extracting deal data from the email. With an AI model this can take up to a minute.
            </span>
          )}
        </div>
      </form>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Extraction failed</AlertTitle>
          <AlertDescription>
            <p>{error.message}</p>
            <p className="mt-1 text-xs opacity-80">
              {error.retryable ? "This is usually temporary — try again. " : ""}
              <span className="font-mono">
                {error.code}
                {error.request_id ? ` · request ${error.request_id}` : ""}
              </span>
            </p>
          </AlertDescription>
        </Alert>
      )}
      {result && <ExtractionResult result={result} />}
    </div>
  );
}
