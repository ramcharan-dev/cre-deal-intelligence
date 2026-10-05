import Link from "next/link";

import { Button } from "@/components/ui/button";

export const SUGGESTED_QUESTIONS = [
  "Compare lender quotes for this deal",
  "Which lender has the lowest fixed rate?",
  "Which lender declined?",
  "What are the pending actions?",
  "What lenders are associated with this deal?",
];

const copilotHref = (q: string, dealId?: string) =>
  `/copilot?${new URLSearchParams({ q, ...(dealId ? { deal: dealId } : {}) })}`;

/** Plain GET form to /copilot (works without client JavaScript). */
export function CopilotAsk({
  dealId,
  defaultValue,
  suggestions = SUGGESTED_QUESTIONS,
}: {
  dealId?: string;
  defaultValue?: string;
  suggestions?: string[];
}) {
  return (
    <div className="space-y-3">
      <form action="/copilot" method="get" className="flex gap-2">
        {dealId && <input type="hidden" name="deal" value={dealId} />}
        <input
          name="q"
          defaultValue={defaultValue}
          required
          minLength={2}
          maxLength={500}
          placeholder="Ask about quotes, lenders, deadlines… or search past deals"
          aria-label="Question"
          className="border-input focus-visible:ring-ring/50 h-9 w-full min-w-0 rounded-md border bg-transparent px-3 text-sm outline-none focus-visible:ring-[3px]"
        />
        <Button type="submit">Ask</Button>
      </form>
      <div className="flex flex-wrap gap-2 text-xs">
        {suggestions.map((s) => (
          <Link
            key={s}
            href={copilotHref(s, dealId)}
            className="hover:bg-muted text-muted-foreground rounded-full border px-2.5 py-1"
          >
            {s}
          </Link>
        ))}
      </div>
    </div>
  );
}
