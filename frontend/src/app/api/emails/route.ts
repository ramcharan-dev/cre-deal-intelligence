import { BACKEND_URL } from "@/lib/api";

/** Proxies email uploads to the backend so the browser only talks to this origin. */
export async function POST(request: Request) {
  const form = await request.formData();
  const file = form.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return Response.json(
      { detail: { code: "invalid_email", message: "Choose a .eml file or paste the raw email source.", retryable: false } },
      { status: 400 },
    );
  }

  let res: Response;
  try {
    const requestId = request.headers.get("x-request-id");
    res = await fetch(`${BACKEND_URL}/api/emails`, {
      method: "POST",
      body: form,
      headers: requestId ? { "x-request-id": requestId } : undefined,
    });
  } catch {
    return Response.json(
      { detail: { code: "backend_unreachable", message: "Backend is unreachable.", retryable: true } },
      { status: 502 },
    );
  }
  return new Response(res.body, {
    status: res.status,
    headers: {
      "content-type": res.headers.get("content-type") ?? "application/json",
      "x-request-id": res.headers.get("x-request-id") ?? "",
    },
  });
}
