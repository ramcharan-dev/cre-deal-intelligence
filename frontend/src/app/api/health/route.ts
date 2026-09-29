import { getBackendHealth } from "@/lib/api";

/** Frontend health: the Next.js server is up, plus a pass-through of backend readiness. */
export async function GET() {
  const backend = await getBackendHealth();
  return Response.json({ status: "ok", service: "frontend", backend });
}
