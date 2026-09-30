import { GmailIntegration } from "@/components/gmail/gmail-integration";
import { Badge } from "@/components/ui/badge";
import type { Simulation } from "@/lib/gmail-mock";

export const metadata = { title: "Gmail · CRE Deal Intelligence" };

const SIMULATIONS = new Set<Simulation>(["connect-error", "sync-error", "empty"]);

export default async function GmailIntegrationPage({ searchParams }: PageProps<"/integrations/gmail">) {
  // `?simulate=connect-error|sync-error|empty` exercises the error and empty states of the mock flow.
  const { simulate } = await searchParams;
  const simulation = typeof simulate === "string" && SIMULATIONS.has(simulate as Simulation) ? (simulate as Simulation) : null;

  return (
    <main className="mx-auto w-full max-w-5xl space-y-8 px-4 py-10">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Gmail integration</h1>
          <Badge variant="secondary">Preview · mock data</Badge>
        </div>
        <p className="text-muted-foreground mt-1 text-sm">
          Sync broker and lender correspondence straight from your inbox and turn it into deal updates.
        </p>
      </div>
      <GmailIntegration simulation={simulation} />
    </main>
  );
}
