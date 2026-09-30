"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import {
  CircleAlert,
  FilterX,
  Inbox,
  LoaderCircle,
  Mail,
  MailSearch,
  RefreshCw,
  Search,
  ShieldCheck,
  Unplug,
} from "lucide-react";

import { EmailPreview } from "@/components/gmail/email-preview";
import { EmailTable, EmailTableSkeleton } from "@/components/gmail/email-table";
import { SyncStats } from "@/components/gmail/sync-stats";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { formatDate } from "@/lib/format";
import {
  CATEGORY_LABELS,
  EMAIL_CATEGORIES,
  computeStats,
  connectGmail,
  processEmail,
  syncEmails,
  type EmailCategory,
  type GmailMessage,
  type Simulation,
} from "@/lib/gmail-mock";
import { cn } from "@/lib/utils";

type Connection = { state: "disconnected" | "connecting" } | { state: "connected"; account: string; connectedAt: string };

type Synced = { messages: GmailMessage[]; scanned: number; syncedAt: string };

const localDay = (iso: string) => new Date(iso).toLocaleDateString("en-CA"); // YYYY-MM-DD

export function GmailIntegration({ simulation }: { simulation: Simulation }) {
  const [connection, setConnection] = useState<Connection>({ state: "disconnected" });
  const [connectError, setConnectError] = useState<string | null>(null);
  const connectAttempts = useRef(0);

  const [synced, setSynced] = useState<Synced | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const syncAttempts = useRef(0);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [categories, setCategories] = useState<Set<EmailCategory>>(new Set());

  async function connect() {
    setConnection({ state: "connecting" });
    setConnectError(null);
    try {
      const account = await connectGmail(simulation, ++connectAttempts.current);
      setConnection({ state: "connected", account, connectedAt: new Date().toISOString() });
    } catch (e) {
      setConnection({ state: "disconnected" });
      setConnectError(e instanceof Error ? e.message : "Could not connect to Gmail.");
    }
  }

  function disconnect() {
    setConnection({ state: "disconnected" });
    setSynced(null);
    setSyncError(null);
    setSelectedId(null);
    setQuery("");
    setFrom("");
    setTo("");
    setCategories(new Set());
  }

  async function sync() {
    setSyncing(true);
    setSyncError(null);
    try {
      const result = await syncEmails(simulation, ++syncAttempts.current);
      // Keep processing progress for messages already seen in an earlier sync.
      setSynced((prev) => {
        const known = new Map(prev?.messages.map((m) => [m.id, m]));
        const messages = result.messages.map((m) => {
          const k = known.get(m.id);
          return k ? { ...m, status: k.status, error: k.error, dealName: k.dealName } : m;
        });
        return { ...result, messages };
      });
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : "Sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  const updateMessage = useCallback((id: string, patch: Partial<GmailMessage>) => {
    setSynced((prev) => prev && { ...prev, messages: prev.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) });
  }, []);

  async function processMessage(id: string) {
    const message = synced?.messages.find((m) => m.id === id);
    if (!message || message.status === "processing") return;
    updateMessage(id, { status: "processing", error: null });
    try {
      updateMessage(id, await processEmail(message));
    } catch (e) {
      updateMessage(id, { status: "failed", error: e instanceof Error ? e.message : "Processing failed." });
    }
  }

  const closePreview = useCallback(() => setSelectedId(null), []);

  const dateRangeInvalid = Boolean(from && to && from > to);
  const filtersActive = Boolean(query.trim() || from || to || categories.size);

  const visible = useMemo(() => {
    if (!synced) return [];
    const q = query.trim().toLowerCase();
    return synced.messages
      .filter((m) => {
        if (categories.size && !categories.has(m.category)) return false;
        const day = localDay(m.sentAt);
        if (!dateRangeInvalid && ((from && day < from) || (to && day > to))) return false;
        if (!q) return true;
        return [m.subject, m.senderName, m.senderEmail, m.body, m.dealName ?? ""].some((s) => s.toLowerCase().includes(q));
      })
      .sort((a, b) => b.sentAt.localeCompare(a.sentAt));
  }, [synced, query, from, to, categories, dateRangeInvalid]);

  const categoryCounts = useMemo(() => {
    const counts = new Map<EmailCategory, number>();
    for (const m of synced?.messages ?? []) counts.set(m.category, (counts.get(m.category) ?? 0) + 1);
    return counts;
  }, [synced]);

  const stats = synced ? computeStats(synced.messages, synced.scanned) : null;
  const selected = visible.find((m) => m.id === selectedId) ?? null;

  function toggleCategory(c: EmailCategory) {
    setCategories((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  }

  function clearFilters() {
    setQuery("");
    setFrom("");
    setTo("");
    setCategories(new Set());
  }

  return (
    <div className="space-y-8">
      <ConnectionCard
        connection={connection}
        error={connectError}
        lastSyncedAt={synced?.syncedAt ?? null}
        onConnect={connect}
        onDisconnect={disconnect}
      />

      {connection.state === "connected" && (
        <section className="space-y-4" aria-labelledby="email-intel-heading">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 id="email-intel-heading" className="text-lg font-semibold tracking-tight">
                Email intelligence
              </h2>
              <p className="text-muted-foreground text-sm">
                CRE-relevant messages from your inbox, classified and scored for relevance.
              </p>
            </div>
            <Button onClick={sync} disabled={syncing}>
              {syncing ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
              {syncing ? "Syncing…" : "Sync Emails"}
            </Button>
          </div>

          <SyncStats stats={stats} loading={syncing} />

          {syncError && (
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Sync failed</AlertTitle>
              <AlertDescription>{syncError}</AlertDescription>
              <AlertAction>
                <Button size="sm" variant="outline" onClick={sync} disabled={syncing}>
                  Retry
                </Button>
              </AlertAction>
            </Alert>
          )}

          {/* Filters */}
          <div className="space-y-3 rounded-xl p-3 ring-1 ring-foreground/10">
            <div className="flex flex-col gap-3 md:flex-row md:items-end">
              <label className="flex-1 space-y-1">
                <span className="text-muted-foreground text-xs font-medium">Search</span>
                <div className="relative">
                  <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" aria-hidden />
                  <Input
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Sender, subject, property, lender…"
                    className="pl-8"
                    disabled={!synced}
                  />
                </div>
              </label>
              <fieldset className="space-y-1" disabled={!synced}>
                <legend className="text-muted-foreground mb-1 text-xs font-medium">Date range</legend>
                <div className="flex items-center gap-2">
                  <Input
                    type="date"
                    value={from}
                    max={to || undefined}
                    onChange={(e) => setFrom(e.target.value)}
                    aria-label="From date"
                    aria-invalid={dateRangeInvalid || undefined}
                    className="md:w-38"
                  />
                  <span className="text-muted-foreground text-sm">to</span>
                  <Input
                    type="date"
                    value={to}
                    min={from || undefined}
                    onChange={(e) => setTo(e.target.value)}
                    aria-label="To date"
                    aria-invalid={dateRangeInvalid || undefined}
                    className="md:w-38"
                  />
                </div>
              </fieldset>
            </div>
            {dateRangeInvalid && (
              <p className="text-destructive text-xs" role="alert">
                The start date is after the end date — the date filter is ignored until it’s fixed.
              </p>
            )}
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by category">
              {EMAIL_CATEGORIES.map((c) => {
                const active = categories.has(c);
                return (
                  <Button
                    key={c}
                    size="sm"
                    variant={active ? "default" : "outline"}
                    aria-pressed={active}
                    onClick={() => toggleCategory(c)}
                    disabled={!synced}
                    className="rounded-full"
                  >
                    {CATEGORY_LABELS[c]}
                    {synced && <span className={cn("tabular-nums", active ? "opacity-70" : "text-muted-foreground")}>{categoryCounts.get(c) ?? 0}</span>}
                  </Button>
                );
              })}
              {filtersActive && (
                <Button size="sm" variant="ghost" onClick={clearFilters} className="ml-auto">
                  <FilterX data-icon="inline-start" />
                  Clear filters
                </Button>
              )}
            </div>
          </div>

          {/* Results */}
          {!synced && syncing ? (
            <EmailTableSkeleton />
          ) : !synced ? (
            <EmptyState
              icon={<MailSearch />}
              title="No emails synced yet"
              description="Run a sync to scan your inbox for lender quotes, term sheets and deal updates."
              action={
                <Button onClick={sync} disabled={syncing}>
                  <RefreshCw data-icon="inline-start" />
                  Sync Emails
                </Button>
              }
            />
          ) : synced.messages.length === 0 ? (
            <EmptyState
              icon={<Inbox />}
              title="No CRE-relevant emails found"
              description={`Scanned ${synced.scanned.toLocaleString("en-US")} emails and none looked like deal or financing correspondence.`}
            />
          ) : visible.length === 0 ? (
            <EmptyState
              icon={<FilterX />}
              title="No emails match your filters"
              description="Try a different search term, widen the date range or clear the category filters."
              action={
                <Button variant="outline" onClick={clearFilters}>
                  Clear filters
                </Button>
              }
            />
          ) : (
            <div className={cn("grid items-start gap-4", selected && "lg:grid-cols-[minmax(0,1fr)_22rem]")}>
              <div className={cn("space-y-2 transition-opacity", syncing && "pointer-events-none opacity-60")} aria-busy={syncing}>
                <p className="text-muted-foreground text-xs">
                  Showing {visible.length} of {synced.messages.length} emails · synced{" "}
                  {formatDate(synced.syncedAt, { hour: "numeric", minute: "2-digit" })}
                </p>
                <EmailTable messages={visible} selectedId={selectedId} compact={Boolean(selected)} onSelect={setSelectedId} />
              </div>
              {selected && <EmailPreview message={selected} onClose={closePreview} onProcess={processMessage} />}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function ConnectionCard({
  connection,
  error,
  lastSyncedAt,
  onConnect,
  onDisconnect,
}: {
  connection: Connection;
  error: string | null;
  lastSyncedAt: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  if (connection.state === "connected") {
    return (
      <Card>
        <CardHeader>
          <div className="flex min-w-0 items-center gap-3">
            <div className="bg-muted flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold uppercase">
              {connection.account.charAt(0)}
            </div>
            <div className="min-w-0">
              <CardTitle className="flex flex-wrap items-center gap-2">
                <span className="truncate">{connection.account}</span>
                <Badge variant="outline">
                  <span className="size-1.5 rounded-full bg-emerald-500" aria-hidden />
                  Connected
                </Badge>
              </CardTitle>
              <CardDescription>
                Read-only access · connected {formatDate(connection.connectedAt, { hour: "numeric", minute: "2-digit" })}
                {lastSyncedAt && ` · last synced ${formatDate(lastSyncedAt, { hour: "numeric", minute: "2-digit" })}`}
              </CardDescription>
            </div>
          </div>
          <CardAction>
            <Button variant="outline" onClick={onDisconnect}>
              <Unplug data-icon="inline-start" />
              Disconnect
            </Button>
          </CardAction>
        </CardHeader>
      </Card>
    );
  }

  const connecting = connection.state === "connecting";
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="bg-muted flex size-10 shrink-0 items-center justify-center rounded-lg">
            <Mail className="size-5" aria-hidden />
          </div>
          <div>
            <CardTitle className="flex items-center gap-2">
              Gmail
              <Badge variant="outline">Not connected</Badge>
            </CardTitle>
            <CardDescription>
              Connect a mailbox to find lender quotes, term sheets and deal updates automatically — no more exporting
              .eml files.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="text-muted-foreground grid gap-2 text-sm sm:grid-cols-2">
          <li className="flex gap-2">
            <ShieldCheck className="text-foreground mt-0.5 size-4 shrink-0" aria-hidden />
            Read-only access. We never send, delete or modify email.
          </li>
          <li className="flex gap-2">
            <MailSearch className="text-foreground mt-0.5 size-4 shrink-0" aria-hidden />
            Only CRE-relevant messages are processed; everything else is skipped.
          </li>
        </ul>
        {error && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>Couldn’t connect to Gmail</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button size="lg" onClick={onConnect} disabled={connecting}>
            {connecting ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Mail data-icon="inline-start" />}
            {connecting ? "Waiting for Google…" : error ? "Try again" : "Connect Gmail"}
          </Button>
          {connecting && (
            <span className="text-muted-foreground text-sm" role="status">
              Complete sign-in in the Google window.
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-12 text-center">
      <div className="bg-muted text-muted-foreground mb-1 flex size-10 items-center justify-center rounded-full [&_svg]:size-5">
        {icon}
      </div>
      <p className="font-medium">{title}</p>
      <p className="text-muted-foreground max-w-sm text-sm">{description}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
