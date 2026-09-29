import { connection } from "next/server";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBackendHealth } from "@/lib/api";

function StatusBadge({ ok, label }: { ok: boolean; label?: string }) {
  return <Badge variant={ok ? "secondary" : "destructive"}>{label ?? (ok ? "Healthy" : "Down")}</Badge>;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-right">{value ?? "—"}</span>
    </div>
  );
}

export default async function Home() {
  await connection(); // always render at request time
  const backend = await getBackendHealth();
  const db = backend.reachable ? backend.data.database : null;

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">CRE AI Deal Intelligence</h1>
        <p className="text-muted-foreground mt-1 text-sm">Phase 1 foundation · system status</p>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Frontend</CardTitle>
            <CardDescription>Next.js server</CardDescription>
          </CardHeader>
          <CardContent>
            <StatusBadge ok />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Backend</CardTitle>
            <CardDescription>FastAPI</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <StatusBadge ok={backend.reachable} />
            {!backend.reachable && <p className="text-destructive text-xs break-words">{backend.error}</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Database</CardTitle>
            <CardDescription>PostgreSQL + pgvector</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <StatusBadge ok={db?.status === "ok"} label={db ? undefined : "Unknown"} />
            {db?.error && <p className="text-destructive text-xs break-words">{db.error}</p>}
          </CardContent>
        </Card>
      </div>

      {backend.reachable && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Database details</CardTitle>
            </CardHeader>
            <CardContent className="divide-y">
              <Row label="PostgreSQL" value={db?.server_version} />
              <Row label="pgvector" value={db?.pgvector_version} />
              <Row label="Migration" value={db?.alembic_revision} />
              <Row label="Latency" value={db?.latency_ms != null ? `${db.latency_ms} ms` : null} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>AI providers</CardTitle>
              <CardDescription>Key present (not validated here)</CardDescription>
            </CardHeader>
            <CardContent className="divide-y">
              {Object.entries(backend.data.providers).map(([name, p]) => (
                <div key={name} className="flex items-center justify-between gap-4 py-1.5 text-sm">
                  <span>
                    <span className="capitalize">{name}</span>
                    <span className="text-muted-foreground block text-xs">{p.used_by}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-muted-foreground font-mono text-xs">{p.model}</span>
                    <Badge variant={p.configured ? "secondary" : "outline"}>
                      {p.configured ? "Key set" : "No key"}
                    </Badge>
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
    </main>
  );
}
