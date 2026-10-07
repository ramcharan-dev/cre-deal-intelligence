import type { EmailSource as EmailSourceData } from "@/lib/types";

/**
 * Finds `highlight` in `text` the way the backend validates source_text: case-insensitive, any whitespace,
 * reply markers (">") and straight/curly quotes treated alike.
 */
function findSpan(text: string, highlight: string): [number, number] | null {
  const words = highlight.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const pattern = words
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/['‘’]/g, "['‘’]").replace(/["“”]/g, '["“”]'))
    .join("[\\s>]+");
  const match = new RegExp(pattern, "i").exec(text);
  return match ? [match.index, match.index + match[0].length] : null;
}

/** The email exactly as the extractor saw it, with the cited source text highlighted. */
export function EmailSource({ email, highlight }: { email: EmailSourceData; highlight?: string }) {
  const span = highlight ? findSpan(email.text, highlight) : null;
  return (
    <div className="space-y-2">
      {highlight && !span && (
        <p className="text-muted-foreground text-xs">Could not locate “{highlight}” verbatim in this email.</p>
      )}
      <pre className="bg-muted/40 max-h-[32rem] overflow-auto rounded-md border p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap">
        {span ? (
          <>
            {email.text.slice(0, span[0])}
            <mark className="rounded bg-amber-200 px-0.5 text-black dark:bg-amber-400/35 dark:text-amber-50">
              {email.text.slice(span[0], span[1])}
            </mark>
            {email.text.slice(span[1])}
          </>
        ) : (
          email.text
        )}
      </pre>
      {email.attachment_names.length > 0 && (
        <p className="text-muted-foreground text-xs">
          Supporting documents attached: {email.attachment_names.join(", ")} (listed, not read by the POC)
        </p>
      )}
    </div>
  );
}
