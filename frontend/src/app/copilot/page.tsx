import Link from "next/link";

import { CopilotAsk, SUGGESTED_QUESTIONS } from "@/components/copilot-ask";
import { SourceLinks } from "@/components/source-links";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { askCopilot, getDeals } from "@/lib/api";

export const metadata = { title: "Copilot · CRE Deal Intelligence" };

const MODE_LABELS: Record<string, string> = {
  structured: "Answered from stored deal data",
  search: "Search results (no AI model configured)",
  llm: "AI-generated from sources",
};

export default async function CopilotPage(props: PageProps<"/copilot">) {
  const params = await props.searchParams;
  const q = typeof params.q === "string" ? params.q.trim() : "";
  const dealId = typeof params.deal === "string" ? params.deal : undefined;

  const deals = await getDeals();
  const deal = deals.find((d) => d.id === dealId);
  const answer = q.length >= 2 ? await askCopilot(q, deal?.id) : null;

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 px-4 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Copilot</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Ask about deals, lender quotes and deadlines, or search historical deal information. Every answer links to
          its source emails.
        </p>
      </div>

      <Card>
        <CardContent className="space-y-3">
          {deal && (
            <p className="text-sm">
              Scope: <span className="font-medium">{deal.deal_name}</span>{" "}
              <Link href={`/copilot${q ? `?q=${encodeURIComponent(q)}` : ""}`} className="text-muted-foreground text-xs underline">
                (ask across all deals)
              </Link>
            </p>
          )}
          <CopilotAsk dealId={deal?.id} defaultValue={q} suggestions={SUGGESTED_QUESTIONS} />
        </CardContent>
      </Card>

      {answer && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{MODE_LABELS[answer.mode] ?? answer.mode}</Badge>
              {answer.deal_id && answer.deal_name && (
                <Link href={`/deals/${answer.deal_id}`} className="text-sm underline-offset-4 hover:underline">
                  {answer.deal_name}
                </Link>
              )}
            </div>
            <CardTitle className="mt-1 text-base">{answer.question}</CardTitle>
            <CardDescription className="text-foreground whitespace-pre-line">{answer.answer}</CardDescription>
          </CardHeader>
          {answer.items.length > 0 && (
            <CardContent>
              <ul className="divide-y">
                {answer.items.map((item, i) => (
                  <li key={i} className="py-3 text-sm first:pt-0 last:pb-0">
                    {item.title && <div className="font-medium">{item.title}</div>}
                    <div className={item.title ? "text-muted-foreground" : undefined}>{item.text}</div>
                    <SourceLinks sources={item.sources} />
                  </li>
                ))}
              </ul>
            </CardContent>
          )}
        </Card>
      )}
    </main>
  );
}
