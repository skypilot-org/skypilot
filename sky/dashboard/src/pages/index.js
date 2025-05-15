import { useEffect } from 'react';
import { useRouter } from 'next/router';

export default function Index() {
  const router = useRouter();

  useEffect(() => {
    if (router.asPath === '/') {
      router.push('/clusters');
    } else {
      router.push(router.asPath);
    }
  }, [router]);

  // Return null or a loading state while redirecting
  return null;
}
