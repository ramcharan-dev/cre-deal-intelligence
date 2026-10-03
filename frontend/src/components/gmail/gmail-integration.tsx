"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleAlert,
  CircleCheck,
  FilterX,
  Inbox,
  LoaderCircle,
  Mail,
  MailSearch,
  RefreshCw,
  Search,
  ShieldCheck,
  TriangleAlert,
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
  GmailApiError,
  OAUTH_ERROR_MESSAGES,
  OAUTH_START_URL,
  disconnectGmail,
  getConnection,
  getMessage,
  listMessages,
  processMessage,
  syncGmail,
  type EmailCategory,
  type GmailConnection,
  type GmailMessage,
  type GmailMessageDetail,
  type GmailMessageList,
  type GmailSyncResult,
} from "@/lib/gmail";
import type { ApiErrorDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Set from `?gmail=connected` / `?gmail_error=<code>` after the OAuth callback redirect. */
export type OAuthOutcome = { connected: true } | { error: string } | null;

const localDay = (iso: string) => new Date(iso).toLocaleDateString("en-CA"); // YYYY-MM-DD

const toDetail = (e: unknown): ApiErrorDetail =>
  e instanceof GmailApiError
    ? e.detail
    : { code: "unknown", message: "Something went wrong.", retryable: true, email_id: null, request_id: null };

export function GmailIntegration({ oauthOutcome }: { oauthOutcome: OAuthOutcome }) {
  const router = useRouter();

  const [connection, setConnection] = useState<GmailConnection | null>(null);
  const [connectionError, setConnectionError] = useState<ApiErrorDetail | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectError, setDisconnectError] = useState<ApiErrorDetail | null>(null);
  const [banner, setBanner] = useState<OAuthOutcome>(oauthOutcome);

  const [list, setList] = useState<GmailMessageList | null>(null);
  const [listError, setListError] = useState<ApiErrorDetail | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<ApiErrorDetail | null>(null);
  const [lastSync, setLastSync] = useState<GmailSyncResult | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, GmailMessageDetail>>({});
  const [detailError, setDetailError] = useState<ApiErrorDetail | null>(null);
  const [processing, setProcessing] = useState<Set<string>>(new Set());
  const [processErrors, setProcessErrors] = useState<Record<string, ApiErrorDetail>>({});

  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [categories, setCategories] = useState<Set<EmailCategory>>(new Set());

  const loadList = useCallback(async () => {
    try {
      setList(await listMessages());
      setListError(null);
    } catch (e) {
      setListError(toDetail(e));
    }
  }, []);

  const loadConnection = useCallback(async () => {
    try {
      const c = await getConnection();
      setConnection(c);
      setConnectionError(null);
      if (c.connected) await loadList();
    } catch (e) {
      setConnectionError(toDetail(e));
    }
  }, [loadList]);

  // Initial load. (State is set in promise callbacks, after the effect has run.)
  useEffect(() => {
    let active = true;
    getConnection().then(
      (c) => {
        if (!active) return;
        setConnection(c);
        if (c.connected)
          listMessages().then(
            (l) => active && setList(l),
            (e) => active && setListError(toDetail(e)),
          );
      },
      (e) => active && setConnectionError(toDetail(e)),
    );
    return () => {
      active = false;
    };
  }, []);

  // Coming back from Google with the Back button restores this page from the bfcache mid-"Redirecting…".
  useEffect(() => {
    const onShow = (e: PageTransitionEvent) => e.persisted && setConnecting(false);
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  // Drop ?gmail=… from the URL so a refresh doesn't show the banner again.
  useEffect(() => {
    if (oauthOutcome) router.replace("/integrations/gmail", { scroll: false });
  }, [oauthOutcome, router]);

  /** Any call can discover that Google revoked access; re-read the connection to show the reconnect state. */
  const handleError = useCallback(
    (e: unknown): ApiErrorDetail => {
      const detail = toDetail(e);
      if (detail.code === "gmail_reauth_required" || detail.code === "gmail_not_connected") void loadConnection();
      return detail;
    },
    [loadConnection],
  );

  function connect() {
    setConnecting(true);
    setBanner(null);
    window.location.assign(OAUTH_START_URL); // full navigation: backend → Google consent → callback → here
  }

  async function disconnect() {
    setDisconnecting(true);
    setDisconnectError(null);
    try {
      await disconnectGmail();
      setList(null);
      setLastSync(null);
      setSelectedId(null);
      setDetails({});
      setProcessErrors({});
      setBanner(null);
      clearFilters();
      await loadConnection();
    } catch (e) {
      setDisconnectError(handleError(e));
    } finally {
      setDisconnecting(false);
    }
  }

  async function sync() {
    setSyncing(true);
    setSyncError(null);
    try {
      setLastSync(await syncGmail({ after: from || undefined, before: to || undefined }));
      await loadList();
      void getConnection().then(setConnection, () => {}); // refresh "last synced"
    } catch (e) {
      setSyncError(handleError(e));
    } finally {
      setSyncing(false);
    }
  }

  const loadDetail = useCallback(
    async (id: string) => {
      setDetailError(null);
      try {
        const d = await getMessage(id);
        setDetails((prev) => ({ ...prev, [id]: d }));
      } catch (e) {
        setDetailError(handleError(e));
      }
    },
    [handleError],
  );

  function select(id: string) {
    setSelectedId(id);
    if (!details[id]) void loadDetail(id);
  }

  async function process(id: string) {
    setProcessing((prev) => new Set(prev).add(id));
    setProcessErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    try {
      await processMessage(id);
      // A skipped message is now stored in full; reload its body along with the list and stats.
      await Promise.all([loadList(), loadDetail(id)]);
    } catch (e) {
      const detail = handleError(e);
      setProcessErrors((prev) => ({ ...prev, [id]: detail }));
      if (detail.email_id) void loadList(); // the pipeline recorded the failure on the email
    } finally {
      setProcessing((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  const closePreview = useCallback(() => setSelectedId(null), []);

  const dateRangeInvalid = Boolean(from && to && from > to);
  const filtersActive = Boolean(query.trim() || from || to || categories.size);

  const messages: GmailMessage[] = useMemo(
    () => (list?.messages ?? []).map((m) => (processing.has(m.id) ? { ...m, status: "processing" } : m)),
    [list, processing],
  );

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return messages.filter((m) => {
      if (categories.size && !categories.has(m.category)) return false;
      if (!dateRangeInvalid && (from || to)) {
        if (!m.sent_at) return false;
        const day = localDay(m.sent_at);
        if ((from && day < from) || (to && day > to)) return false;
      }
      if (!q) return true;
      return [m.subject, m.sender_name, m.sender_email, m.snippet, m.deal_name].some((s) => s?.toLowerCase().includes(q));
    });
  }, [messages, query, from, to, categories, dateRangeInvalid]);

  const categoryCounts = useMemo(() => {
    const counts = new Map<EmailCategory, number>();
    for (const m of messages) counts.set(m.category, (counts.get(m.category) ?? 0) + 1);
    return counts;
  }, [messages]);

  const selected = visible.find((m) => m.id === selectedId) ?? null;
  const hasSynced = Boolean(list?.last_sync);

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
      {banner && "connected" in banner && (
        <Alert>
          <CircleCheck />
          <AlertTitle>Gmail connected</AlertTitle>
          <AlertDescription>Run a sync to pull in recent lender and deal correspondence.</AlertDescription>
          <AlertAction>
            <Button size="sm" variant="ghost" onClick={() => setBanner(null)}>
              Dismiss
            </Button>
          </AlertAction>
        </Alert>
      )}

      <ConnectionCard
        connection={connection}
        loadError={connectionError}
        oauthError={banner && "error" in banner ? banner.error : null}
        connecting={connecting}
        disconnecting={disconnecting}
        disconnectError={disconnectError}
        onRetry={loadConnection}
        onConnect={connect}
        onDisconnect={disconnect}
      />

      {connection?.connected && (
        <section className="space-y-4" aria-labelledby="email-intel-heading">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 id="email-intel-heading" className="text-lg font-semibold tracking-tight">
                Email intelligence
              </h2>
              <p className="text-muted-foreground text-sm">
                CRE-relevant messages from your inbox, classified and scored with keyword rules.
              </p>
            </div>
            <Button onClick={sync} disabled={syncing || dateRangeInvalid || connection.status !== "connected"}>
              {syncing ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
              {syncing ? "Syncing…" : "Sync Emails"}
            </Button>
          </div>

          <SyncStats stats={list?.stats ?? null} loading={!list && !listError} />

          {syncError && (
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Sync failed</AlertTitle>
              <AlertDescription>{syncError.message}</AlertDescription>
              {syncError.code !== "gmail_reauth_required" && (
                <AlertAction>
                  <Button size="sm" variant="outline" onClick={sync} disabled={syncing}>
                    Retry
                  </Button>
                </AlertAction>
              )}
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
                    disabled={!hasSynced}
                  />
                </div>
              </label>
              <fieldset className="space-y-1">
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
            {dateRangeInvalid ? (
              <p className="text-destructive text-xs" role="alert">
                The start date is after the end date — fix it to filter or sync.
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">
                Sync scans {from || to ? "this date range" : "the last 30 days"}; the range also filters the list.
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
                    disabled={!hasSynced}
                    className="rounded-full"
                  >
                    {CATEGORY_LABELS[c]}
                    {hasSynced && (
                      <span className={cn("tabular-nums", active ? "opacity-70" : "text-muted-foreground")}>
                        {categoryCounts.get(c) ?? 0}
                      </span>
                    )}
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
          {listError && !list ? (
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Couldn’t load synced emails</AlertTitle>
              <AlertDescription>{listError.message}</AlertDescription>
              <AlertAction>
                <Button size="sm" variant="outline" onClick={loadList}>
                  Retry
                </Button>
              </AlertAction>
            </Alert>
          ) : !list || (syncing && messages.length === 0) ? (
            <EmailTableSkeleton />
          ) : !hasSynced ? (
            <EmptyState
              icon={<MailSearch />}
              title="No emails synced yet"
              description="Run a sync to scan your inbox for lender quotes, term sheets and deal updates."
              action={
                <Button onClick={sync} disabled={syncing || dateRangeInvalid}>
                  <RefreshCw data-icon="inline-start" />
                  Sync Emails
                </Button>
              }
            />
          ) : messages.length === 0 ? (
            <EmptyState
              icon={<Inbox />}
              title="No emails found"
              description={`The last sync scanned ${list.last_sync?.scanned ?? 0} messages. Try a wider date range.`}
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
                <p className="text-muted-foreground text-xs" role="status">
                  Showing {visible.length} of {messages.length} emails
                  {list.last_sync && ` · synced ${formatDate(list.last_sync.at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`}
                  {lastSync && ` · last run: ${lastSync.new} new, ${lastSync.duplicates} already synced`}
                </p>
                <EmailTable messages={visible} selectedId={selectedId} compact={Boolean(selected)} onSelect={select} />
              </div>
              {selected && (
                <EmailPreview
                  message={selected}
                  detail={details[selected.id] ?? null}
                  detailError={details[selected.id] ? null : detailError}
                  processError={processErrors[selected.id] ?? null}
                  onClose={closePreview}
                  onProcess={process}
                  onRetryDetail={() => loadDetail(selected.id)}
                />
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function ConnectionCard({
  connection,
  loadError,
  oauthError,
  connecting,
  disconnecting,
  disconnectError,
  onRetry,
  onConnect,
  onDisconnect,
}: {
  connection: GmailConnection | null;
  loadError: ApiErrorDetail | null;
  oauthError: string | null;
  connecting: boolean;
  disconnecting: boolean;
  disconnectError: ApiErrorDetail | null;
  onRetry: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  if (!connection) {
    if (loadError) {
      return (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Couldn’t check the Gmail connection</AlertTitle>
          <AlertDescription>{loadError.message}</AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={onRetry}>
              Retry
            </Button>
          </AlertAction>
        </Alert>
      );
    }
    return (
      <Card aria-busy role="status" aria-label="Checking Gmail connection">
        <CardHeader>
          <div className="flex animate-pulse items-center gap-3">
            <div className="bg-muted size-10 rounded-full" />
            <div className="space-y-2">
              <div className="bg-muted h-4 w-48 rounded" />
              <div className="bg-muted h-3 w-64 rounded" />
            </div>
          </div>
        </CardHeader>
      </Card>
    );
  }

  if (connection.connected) {
    const needsReauth = connection.status === "reauth_required";
    return (
      <Card>
        <CardHeader>
          <div className="flex min-w-0 items-center gap-3">
            <div className="bg-muted flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold uppercase">
              {connection.email_address?.charAt(0)}
            </div>
            <div className="min-w-0">
              <CardTitle className="flex flex-wrap items-center gap-2">
                <span className="truncate">{connection.email_address}</span>
                <Badge variant={needsReauth ? "destructive" : "outline"}>
                  {!needsReauth && <span className="size-1.5 rounded-full bg-emerald-500" aria-hidden />}
                  {needsReauth ? "Reconnect needed" : "Connected"}
                </Badge>
              </CardTitle>
              <CardDescription>
                Read-only access · connected {formatDate(connection.connected_at, { dateStyle: "medium" })}
                {connection.last_sync &&
                  ` · last synced ${formatDate(connection.last_sync.at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`}
              </CardDescription>
            </div>
          </div>
          <CardAction>
            <Button variant="outline" onClick={onDisconnect} disabled={disconnecting}>
              {disconnecting ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Unplug data-icon="inline-start" />}
              {disconnecting ? "Disconnecting…" : "Disconnect"}
            </Button>
          </CardAction>
        </CardHeader>
        {(needsReauth || disconnectError) && (
          <CardContent className="space-y-3">
            {needsReauth && (
              <Alert variant="destructive">
                <TriangleAlert />
                <AlertTitle>Gmail access expired or was revoked</AlertTitle>
                <AlertDescription>Reconnect to keep syncing. Already synced emails stay available.</AlertDescription>
                <AlertAction>
                  <Button size="sm" onClick={onConnect} disabled={connecting}>
                    {connecting ? "Redirecting…" : "Reconnect"}
                  </Button>
                </AlertAction>
              </Alert>
            )}
            {disconnectError && (
              <Alert variant="destructive">
                <CircleAlert />
                <AlertTitle>Couldn’t disconnect</AlertTitle>
                <AlertDescription>{disconnectError.message}</AlertDescription>
              </Alert>
            )}
          </CardContent>
        )}
      </Card>
    );
  }

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
            Only CRE-relevant messages are stored for processing; the rest are skipped.
          </li>
        </ul>
        {oauthError && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>Couldn’t connect to Gmail</AlertTitle>
            <AlertDescription>
              {OAUTH_ERROR_MESSAGES[oauthError] ?? "Google sign-in failed. Please try again."}
            </AlertDescription>
          </Alert>
        )}
        {!connection.configured && (
          <Alert>
            <CircleAlert />
            <AlertTitle>Gmail integration isn’t configured</AlertTitle>
            <AlertDescription>
              Set <code className="font-mono text-xs">GOOGLE_CLIENT_ID</code>,{" "}
              <code className="font-mono text-xs">GOOGLE_CLIENT_SECRET</code> and{" "}
              <code className="font-mono text-xs">TOKEN_ENCRYPTION_KEY</code> on the backend, then restart it.
            </AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button size="lg" onClick={onConnect} disabled={connecting || !connection.configured}>
            {connecting ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Mail data-icon="inline-start" />}
            {connecting ? "Redirecting to Google…" : oauthError ? "Try again" : "Connect Gmail"}
          </Button>
          {connecting && (
            <span className="text-muted-foreground text-sm" role="status">
              You’ll come back here after signing in.
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
