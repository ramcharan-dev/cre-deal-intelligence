import Link from "next/link";

const LINKS = [
  { href: "/integrations/gmail", label: "Gmail" },
  { href: "/deals", label: "Dashboard" },
  { href: "/copilot", label: "Copilot" },
  { href: "/", label: "Status" },
];

export function SiteNav() {
  return (
    <header className="border-b">
      <nav className="mx-auto flex w-full max-w-5xl items-center gap-6 px-4 py-3 text-sm">
        <Link href="/deals" className="font-semibold tracking-tight">
          CRE Deal Intelligence
        </Link>
        <div className="flex gap-4">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="text-muted-foreground hover:text-foreground"
            >
              {l.label}
            </Link>
          ))}
        </div>
      </nav>
    </header>
  );
}