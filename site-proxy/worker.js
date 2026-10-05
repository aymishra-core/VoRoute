export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, "https://voroute.pages.dev");
    const headers = new Headers(request.headers);
    headers.delete("host");
    return fetch(target, {
      method: request.method,
      headers,
      redirect: "follow",
    });
  },
};
