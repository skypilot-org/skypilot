/**
 * Read the CSP nonce injected by the server into a <meta> tag.
 *
 * The server adds <meta name="csp-nonce" content="..."> to every HTML
 * response so that client-side code can propagate the nonce to
 * dynamically created <style> elements (e.g. Emotion, Shepherd tour).
 *
 * @returns {string|undefined} The nonce value, or undefined when running
 *   outside a browser or when the meta tag is absent.
 */
export function getNonce() {
  if (typeof document === 'undefined') return undefined;
  const meta = document.querySelector('meta[name="csp-nonce"]');
  return meta ? meta.getAttribute('content') : undefined;
}
