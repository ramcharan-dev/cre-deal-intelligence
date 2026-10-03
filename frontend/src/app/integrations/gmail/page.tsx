import { GmailIntegration, type OAuthOutcome } from "@/components/gmail/gmail-integration";

export const metadata = { title: "Gmail · CRE Deal Intelligence" };

export default async function GmailIntegrationPage({ searchParams }: PageProps<"/integrations/gmail">) {
  // The OAuth callback redirects back here with ?gmail=connected or ?gmail_error=<code>.
  const { gmail, gmail_error } = await searchParams;
  const oauthOutcome: OAuthOutcome =
    typeof gmail_error === "string" ? { error: gmail_error } : gmail === "connected" ? { connected: true } : null;

  return (
    <main className="mx-auto w-full max-w-5xl space-y-8 px-4 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Gmail integration</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Sync broker and lender correspondence straight from your inbox and turn it into deal updates.
        </p>
      </div>
      <GmailIntegration oauthOutcome={oauthOutcome} />
    </main>
  );
}
