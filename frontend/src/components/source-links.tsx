import Link from "next/link";

import { sourceHref, sourceLabel } from "@/lib/format";
import type { SourceRef } from "@/lib/types";

/** "Source: dokafor@northmarklife.com — Sep 22, 2026" links that open the email with the text highlighted. */
export function SourceLinks({ sources }: { sources: SourceRef[] }) {
  const unique = sources.filter(
    (s, i) => sources.findIndex((o) => o.email_id === s.email_id && o.source_text === s.source_text) === i,
  );
  if (unique.length === 0) return null;
  return (
    <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {unique.map((s, i) => (
        <Link
          key={i}
          href={sourceHref(s.email_id, s.source_text)}
          title={s.source_text ? `“${s.source_text}” — ${s.email_subject}` : s.email_subject}
          className="underline-offset-4 hover:underline"
        >
          Source{s.label ? ` (${s.label})` : ""}: {sourceLabel(s.email_sender, s.email_sent_at)}
        </Link>
      ))}
    </div>
  );
}
