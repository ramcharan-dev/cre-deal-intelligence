import Link from "next/link";
import { notFound } from "next/navigation";

import { EmailSource } from "@/components/email-source";
import { ExtractionResult } from "@/components/extraction-result";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getEmailResult, getEmailSource } from "@/lib/api";

export default async function EmailResultPage(props: PageProps<"/emails/[id]">) {
  const { id } = await props.params;
  const { highlight } = await props.searchParams;
  const [result, source] = await Promise.all([getEmailResult(id), getEmailSource(id)]);
  if (!result || !source) notFound();
  const highlightText = typeof highlight === "string" ? highlight : undefined;

  const original = (
    <Card id="source" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Original email</CardTitle>
        <CardDescription>
          {highlightText ? "The highlighted text is the source of the selected value." : "Exactly as the extractor saw it."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <EmailSource email={source} highlight={highlightText} />
      </CardContent>
    </Card>
  );

  return (
    <main className="mx-auto w-full max-w-5xl space-y-4 px-4 py-10">
      <Link href="/emails" className="text-muted-foreground text-sm hover:underline">
        ← Emails
      </Link>
      {highlightText && original}
      <ExtractionResult result={{ ...result, duplicate: false }} />
      {!highlightText && original}
    </main>
  );
}
