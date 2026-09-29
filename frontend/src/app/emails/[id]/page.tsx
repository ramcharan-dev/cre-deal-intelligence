import Link from "next/link";
import { notFound } from "next/navigation";

import { ExtractionResult } from "@/components/extraction-result";
import { getEmailResult } from "@/lib/api";

export default async function EmailResultPage(props: PageProps<"/emails/[id]">) {
  const { id } = await props.params;
  const result = await getEmailResult(id);
  if (!result) notFound();

  return (
    <main className="mx-auto w-full max-w-5xl space-y-4 px-4 py-10">
      <Link href="/emails" className="text-muted-foreground text-sm hover:underline">
        ← Emails
      </Link>
      <ExtractionResult result={{ ...result, duplicate: false }} />
    </main>
  );
}
