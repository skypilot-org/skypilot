import { useMemo } from 'react';
import { useAuthMethod } from './useAuthMethod';
import { useRouter } from 'next/router';
import { ENDPOINT } from '@/data/connectors/constants';

export function useSignOut(props) {
  const { redirect } = props || {};

  const authMethod = useAuthMethod();
  const router = useRouter();

  const signOutUrl = useMemo(() => {
    if (authMethod === 'oauth2') {
      const url = new URL(`${ENDPOINT}/oauth2/sign_out`, window.location.href);

      if (redirect) {
        url.searchParams.append('rd', redirect);
      }

      return url;
    } else {
      return undefined;
    }
  }, [authMethod, router, redirect]);

  return { signOutUrl };
}
