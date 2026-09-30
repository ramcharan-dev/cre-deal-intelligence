import type { SyncStats as Stats } from "@/lib/gmail-mock";

const ITEMS: { key: keyof Stats; label: string; hint: string }[] = [
  { key: "scanned", label: "Emails scanned", hint: "In the selected mailbox window" },
  { key: "relevant", label: "Relevant emails", hint: "Relevance score ≥ 50" },
  { key: "processed", label: "Processed", hint: "Extracted with AI" },
  { key: "dealsUpdated", label: "Deals updated", hint: "From processed emails" },
];

/** `stats` is null before the first sync; `loading` shows placeholders during it. */
export function SyncStats({ stats, loading }: { stats: Stats | null; loading: boolean }) {
  return (
    <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {ITEMS.map((item) => (
        <div key={item.key} className="bg-card rounded-xl px-4 py-3 ring-1 ring-foreground/10">
          <dt className="text-muted-foreground text-xs font-medium">{item.label}</dt>
          <dd className="mt-1">
            {loading && !stats ? (
              <span className="bg-muted block h-7 w-12 animate-pulse rounded" aria-label="Loading" />
            ) : (
              <span className="text-2xl font-semibold tracking-tight tabular-nums">
                {stats ? stats[item.key].toLocaleString("en-US") : "—"}
              </span>
            )}
            <span className="text-muted-foreground block truncate text-xs">{item.hint}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}
