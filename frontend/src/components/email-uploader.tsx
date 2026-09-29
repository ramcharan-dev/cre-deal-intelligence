"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { ExtractionResult } from "@/components/extraction-result";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { parseApiError, type ApiErrorDetail, type EmailProcessingResult } from "@/lib/types";

type Mode = "file" | "paste";

export function EmailUploader() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiErrorDetail | null>(null);
  const [result, setResult] = useState<EmailProcessingResult | null>(null);

  const payload = mode === "file" ? file : source.trim() ? new File([source], "pasted.eml") : null;

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
            <TabsTrigger value="file">Upload .eml</TabsTrigger>
            <TabsTrigger value="paste">Paste source</TabsTrigger>
          </TabsList>
          <TabsContent value="file">
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
          </TabsContent>
          <TabsContent value="paste">
            <Textarea
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder={"From: jane@broker.com\nTo: deals@yourfirm.com\nSubject: ...\nDate: ...\n\nEmail body..."}
              className="min-h-56 font-mono text-xs"
              aria-label="Raw email source, including headers"
            />
            <p className="text-muted-foreground mt-1 text-xs">
              Include the headers (From, To, Subject, Date). In Gmail: ⋮ → Show original.
            </p>
          </TabsContent>
        </Tabs>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!payload || pending}>
            {pending ? "Extracting…" : "Extract deal data"}
          </Button>
          {pending && (
            <span className="text-muted-foreground text-sm" role="status">
              Claude is reading the email. This can take up to a minute.
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
