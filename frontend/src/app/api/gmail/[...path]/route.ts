import { BACKEND_URL } from "@/lib/api";

/**
 * Proxies /api/gmail/* to the backend so the browser only talks to this origin.
 *
 * Redirects are passed through instead of followed (OAuth start → Google, callback → Gmail page), along with
 * cookies, so the OAuth state cookie lives on this origin. This is also why the default Google redirect URI
 * is `<frontend>/api/gmail/oauth/callback`.
 */
async function proxy(request: Request, ctx: RouteContext<"/api/gmail/[...path]">) {
  const { path } = await ctx.params;
  const url = `${BACKEND_URL}/api/gmail/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`;

  const headers = new Headers();
  for (const name of ["cookie", "content-type", "x-request-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let res: Response;
  try {
    res = await fetch(url, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "DELETE" ? undefined : await request.text(),
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return Response.json(
      { detail: { code: "backend_unreachable", message: "Backend is unreachable.", retryable: true } },
      { status: 502 },
    );
  }

  const out = new Headers();
  for (const name of ["content-type", "location", "x-request-id"]) {
    const value = res.headers.get(name);
    if (value) out.set(name, value);
  }
  for (const cookie of res.headers.getSetCookie()) out.append("set-cookie", cookie);
  return new Response(res.body, { status: res.status, headers: out });
}

export { proxy as DELETE, proxy as GET, proxy as POST };
