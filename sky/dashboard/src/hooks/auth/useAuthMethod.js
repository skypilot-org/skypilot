import { useCallback } from 'react';
import * as cookie from 'cookie';

export function useAuthMethod() {
  const getBrowserCookie = useCallback((name) => {
    const cookies = cookie.parse(document.cookie || '');
    return name in cookies ? cookies[name] : undefined;
  }, []);

  // Only check OAuth2
  // Cannot check other methods (e.g., basic auth) from the browser
  return getBrowserCookie('_oauth2_proxy') ? 'oauth2' : undefined;
}
