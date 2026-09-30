/**
 * Mock Gmail integration. UI-only stand-in for the future OAuth + Gmail API flow:
 * no network calls are made and nothing is persisted.
 */

export const EMAIL_CATEGORIES = ["lender_quote", "deal_update", "financing", "term_sheet", "follow_up", "other"] as const;
export type EmailCategory = (typeof EMAIL_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<EmailCategory, string> = {
  lender_quote: "Lender Quote",
  deal_update: "Deal Update",
  financing: "Financing",
  term_sheet: "Term Sheet",
  follow_up: "Follow-up",
  other: "Other",
};

export type ProcessingStatus = "pending" | "processing" | "processed" | "failed" | "skipped";

export type GmailMessage = {
  id: string;
  senderName: string;
  senderEmail: string;
  subject: string;
  sentAt: string; // ISO
  category: EmailCategory;
  relevance: number; // 0–100
  status: ProcessingStatus;
  error: string | null;
  dealName: string | null;
  attachments: string[];
  body: string;
};

export type SyncStats = {
  scanned: number;
  relevant: number;
  processed: number;
  dealsUpdated: number;
};

export type SyncResult = {
  messages: GmailMessage[];
  scanned: number;
  syncedAt: string;
};

/** `?simulate=` values that let the loading/error/empty states be exercised without a real backend. */
export type Simulation = "connect-error" | "sync-error" | "empty" | null;

export const MOCK_ACCOUNT = "priya.raman@harborviewcap.com";

export class MockGmailError extends Error {}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function connectGmail(simulation: Simulation, attempt: number): Promise<string> {
  await wait(1200);
  if (simulation === "connect-error" && attempt === 1) {
    throw new MockGmailError("Google declined the authorization request. The consent window was closed before access was granted.");
  }
  return MOCK_ACCOUNT;
}

export async function syncEmails(simulation: Simulation, attempt: number): Promise<SyncResult> {
  await wait(1600);
  if (simulation === "sync-error" && attempt === 1) {
    throw new MockGmailError("Gmail rate limit reached (429). No messages were changed — try again in a minute.");
  }
  const now = Date.now();
  const seed = simulation === "empty" ? [] : SEED;
  const messages = seed.map(({ hoursAgo, ...m }) => ({ ...m, sentAt: new Date(now - hoursAgo * 3_600_000).toISOString() }));
  return { messages, scanned: simulation === "empty" ? 64 : 248, syncedAt: new Date(now).toISOString() };
}

/** Process one message "with AI". The Granite Federal message fails on its first attempt to show the retry path. */
export async function processEmail(message: GmailMessage): Promise<Pick<GmailMessage, "status" | "error" | "dealName">> {
  await wait(1800);
  if (message.id === "m-09" && message.status !== "failed") {
    throw new MockGmailError("Attachment “Granite_Indicative_Terms.pdf” could not be read (password protected).");
  }
  return { status: "processed", error: null, dealName: message.dealName ?? "The Lofts at Riverbend" };
}

export function computeStats(messages: GmailMessage[], scanned: number): SyncStats {
  const processed = messages.filter((m) => m.status === "processed");
  return {
    scanned,
    relevant: messages.filter((m) => m.relevance >= 50).length,
    processed: processed.length,
    dealsUpdated: new Set(processed.map((m) => m.dealName).filter(Boolean)).size,
  };
}

export function relevanceLevel(score: number): "High" | "Medium" | "Low" {
  return score >= 75 ? "High" : score >= 50 ? "Medium" : "Low";
}

type Seed = Omit<GmailMessage, "sentAt"> & { hoursAgo: number };

const SEED: Seed[] = [
  {
    id: "m-01",
    hoursAgo: 2,
    senderName: "Daniel Okafor",
    senderEmail: "dokafor@northmarklife.com",
    subject: "RE: The Lofts at Riverbend – revised spread UST + 145",
    category: "lender_quote",
    relevance: 97,
    status: "pending",
    error: null,
    dealName: "The Lofts at Riverbend",
    attachments: [],
    body: `Priya,

Following up on our call — committee approved a tighter spread on Riverbend. Updated indicative terms from Northmark Life:

Loan amount: $48,000,000
Max LTV: 61.5%
Term: 10 years
Rate: 10-yr UST + 145 bps (5.25% all-in at today's Treasury)
Amortization: 30 years, 5 years interest-only
Min DSCR: 1.35x; min debt yield 8.5%
Origination fee: 0.50%
Recourse: non-recourse with standard carve-outs

Terms remain indicative through October 30, 2026.

Best,
Daniel Okafor
Northmark Life Insurance Company`,
  },
  {
    id: "m-02",
    hoursAgo: 6,
    senderName: "Rachel Nguyen",
    senderEmail: "rnguyen@beaconagency.com",
    subject: "Term sheet – 1850 Riverside Drive, Sacramento (Freddie Mac conventional)",
    category: "term_sheet",
    relevance: 95,
    status: "pending",
    error: null,
    dealName: "The Lofts at Riverbend",
    attachments: ["Beacon_TermSheet_Riverbend.pdf"],
    body: `Priya,

Attached is our signed term sheet for The Lofts at Riverbend.

- Loan amount: $50,000,000 (64% LTV)
- 10-year fixed at 5.18%
- 35-year amortization, 3 years IO (we can revisit a 4th year with a 1.30x DSCR)
- Good-faith deposit: $100,000 upon execution
- Rate lock available at application

Please have the sponsor countersign by Friday so we can order third-party reports.

Rachel Nguyen
Beacon Agency Lending`,
  },
  {
    id: "m-03",
    hoursAgo: 20,
    senderName: "Marcus Lee",
    senderEmail: "mlee@cascaderidgepartners.com",
    subject: "Riverbend – updated rent roll and T12 (Sept)",
    category: "deal_update",
    relevance: 88,
    status: "processed",
    error: null,
    dealName: "The Lofts at Riverbend",
    attachments: ["Riverbend_RentRoll_0930.xlsx", "Riverbend_T12_Sep26.xlsx"],
    body: `Priya,

Sending over the September rent roll and T12. Occupancy ticked up to 96.1% and T12 NOI is now $4,162,000.

We also signed the parking garage lease extension, which adds roughly $38K annually starting November.

Let me know if lenders need anything else.

Marcus`,
  },
  {
    id: "m-04",
    hoursAgo: 28,
    senderName: "Tom Whitaker",
    senderEmail: "twhitaker@summitbridgecap.com",
    subject: "Summit Bridge – floating rate option for Riverbend",
    category: "lender_quote",
    relevance: 91,
    status: "processed",
    error: null,
    dealName: "The Lofts at Riverbend",
    attachments: [],
    body: `Priya,

Our bridge option for Riverbend:

- Up to 70% LTV ($54.6MM)
- SOFR + 285 bps, 4.00% SOFR floor
- 3-year initial term + two 1-year extensions
- 1.00% origination, 0.50% exit fee
- Full-term IO, non-recourse

Happy to jump on a call with the sponsor.

Tom Whitaker
Summit Bridge Capital`,
  },
  {
    id: "m-05",
    hoursAgo: 44,
    senderName: "Aisha Patel",
    senderEmail: "apatel@harborviewcap.com",
    subject: "Harbor Point Industrial – financing memo draft for review",
    category: "financing",
    relevance: 84,
    status: "pending",
    error: null,
    dealName: "Harbor Point Industrial",
    attachments: ["HarborPoint_FinancingMemo_v2.docx"],
    body: `Priya,

Draft financing memo for Harbor Point Industrial (Stockton, 410,000 SF, 100% leased to two tenants) is attached.

Headline ask is a $32MM acquisition loan at 60% LTC. I've targeted life companies and two regional banks. WALT is 6.8 years, so I think we can push for 5 years IO.

Could you review sections 3 and 4 before I circulate?

Aisha`,
  },
  {
    id: "m-06",
    hoursAgo: 52,
    senderName: "Kevin Brandt",
    senderEmail: "kbrandt@pacificwestbank.com",
    subject: "Following up – Harbor Point Industrial debt request",
    category: "follow_up",
    relevance: 72,
    status: "pending",
    error: null,
    dealName: "Harbor Point Industrial",
    attachments: [],
    body: `Hi Priya,

Circling back on Harbor Point. Our credit team would like the tenant estoppels and the Phase I before we can issue terms.

Do you have an ETA on those? We're hoping to get this to committee the week of October 12.

Thanks,
Kevin Brandt
Pacific West Bank`,
  },
  {
    id: "m-07",
    hoursAgo: 70,
    senderName: "Laura Chen",
    senderEmail: "lchen@meridianretailpartners.com",
    subject: "Oakmont Plaza – anchor tenant renewal signed",
    category: "deal_update",
    relevance: 79,
    status: "processed",
    error: null,
    dealName: "Oakmont Plaza",
    attachments: ["Oakmont_Anchor_Renewal_Summary.pdf"],
    body: `Priya,

Good news — Safeway signed a 10-year renewal at Oakmont Plaza with two 5-year options. Base rent increases 8% over the expiring rate.

This should resolve the rollover concern lenders raised in round one. Can we reopen conversations with the two banks that passed?

Laura`,
  },
  {
    id: "m-08",
    hoursAgo: 96,
    senderName: "Samuel Ortiz",
    senderEmail: "sortiz@crestlinelending.com",
    subject: "Oakmont Plaza – indicative terms (CMBS)",
    category: "lender_quote",
    relevance: 86,
    status: "pending",
    error: null,
    dealName: "Oakmont Plaza",
    attachments: [],
    body: `Priya,

Indicative CMBS terms for Oakmont Plaza:

- $22,500,000, 65% LTV
- 10-year fixed, swap + 265 bps (approx. 6.40%)
- Full-term IO at 1.40x DSCR
- Defeasance after the 2-year lockout

These assume the Safeway renewal is executed.

Samuel Ortiz
Crestline Lending`,
  },
  {
    id: "m-09",
    hoursAgo: 120,
    senderName: "Megan Russo",
    senderEmail: "mrusso@granitefederal.com",
    subject: "Granite Federal – terms for Harbor Point (see attached)",
    category: "lender_quote",
    relevance: 81,
    status: "pending",
    error: null,
    dealName: "Harbor Point Industrial",
    attachments: ["Granite_Indicative_Terms.pdf"],
    body: `Priya,

Please see our indicative terms for Harbor Point Industrial attached. Headline is $30MM at 5-year fixed, 5.95%, 25-year amortization with 2 years IO, full recourse burning off at 1.50x DSCR.

Let me know if you have questions.

Megan Russo
Granite Federal Bank`,
  },
  {
    id: "m-10",
    hoursAgo: 150,
    senderName: "Priya Raman",
    senderEmail: "priya.raman@harborviewcap.com",
    subject: "Riverbend – lender feedback round 1",
    category: "financing",
    relevance: 77,
    status: "processed",
    error: null,
    dealName: "The Lofts at Riverbend",
    attachments: [],
    body: `Marcus,

Round-one feedback on 1850 Riverside Drive is in. Beacon is leading on proceeds at $50MM / 5.18%, Northmark is the conservative option at $48MM, and Summit Bridge is available if you'd rather stay floating.

Granite Federal passed — they're out of multifamily for the rest of the year.

Let's discuss Monday.

Priya`,
  },
  {
    id: "m-11",
    hoursAgo: 190,
    senderName: "Jordan Blake",
    senderEmail: "jblake@cbre-events.com",
    subject: "Invitation: West Coast Capital Markets Forum, Nov 4",
    category: "other",
    relevance: 34,
    status: "skipped",
    error: null,
    dealName: null,
    attachments: [],
    body: `Hi Priya,

You're invited to the West Coast Capital Markets Forum on November 4 in San Francisco. Panels include life company allocations for 2027 and the agency outlook.

RSVP by October 20.

Jordan Blake`,
  },
  {
    id: "m-12",
    hoursAgo: 230,
    senderName: "Nina Castillo",
    senderEmail: "ncastillo@harborviewcap.com",
    subject: "Q4 pipeline review – agenda",
    category: "other",
    relevance: 41,
    status: "skipped",
    error: null,
    dealName: null,
    attachments: [],
    body: `Team,

Agenda for Thursday's pipeline review:

1. Riverbend – term sheet selection
2. Harbor Point – lender outreach status
3. Oakmont Plaza – re-trade after anchor renewal
4. New mandates

Please update the tracker by Wednesday EOD.

Nina`,
  },
];
