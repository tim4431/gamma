// The one thing the static `_redirects` file cannot express: a host- and
// scheme-based redirect. www and plain http both go to https://<apex> in a
// single 301. Everything else is answered from the assets binding, so
// `_redirects` (short links) and `_headers` still apply.
export default {
  fetch(request, env) {
    const url = new URL(request.url);
    if (url.protocol === 'http:' || url.hostname.startsWith('www.')) {
      url.protocol = 'https:';
      url.hostname = url.hostname.replace(/^www\./, '');
      return Response.redirect(url.href, 301);
    }
    return env.ASSETS.fetch(request);
  },
};
