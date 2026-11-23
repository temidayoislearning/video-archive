function unavailable(message) {
  return new Response(message, {
    status: 503,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export default {
  async fetch(request, env) {
    const publicUrl = new URL(request.url);
    let origin;
    try {
      origin = new URL(env.ARCHIVE_BACKEND);
    } catch {
      return unavailable("Video Archive is waiting for its server connection.");
    }
    if (origin.protocol !== "https:" || origin.origin === publicUrl.origin ||
        origin.pathname !== "/" || origin.search || origin.hash || origin.username || origin.password) {
      return unavailable("Video Archive server configuration is invalid.");
    }

    const target = new URL(origin);
    target.pathname = publicUrl.pathname;
    target.search = publicUrl.search;
    const headers = new Headers(request.headers);
    headers.delete("Host");

    let upstream;
    try {
      upstream = await fetch(target, {
        method: request.method,
        headers,
        body: request.body,
        redirect: "manual",
        cache: "no-store",
      });
    } catch {
      return unavailable("Video Archive cannot reach its server.");
    }

    const response = new Response(upstream.body, upstream);
    response.headers.set("Cache-Control", "private, no-store");
    const location = response.headers.get("Location");
    if (location) {
      const redirect = new URL(location, target);
      if (redirect.origin === origin.origin) {
        response.headers.set("Location", publicUrl.origin + redirect.pathname + redirect.search + redirect.hash);
      }
    }
    return response;
  },
};
