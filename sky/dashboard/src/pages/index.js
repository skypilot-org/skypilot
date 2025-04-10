import { useEffect } from 'react';
import { useRouter } from 'next/router';

export default function Index() {
  const router = useRouter();

  useEffect(() => {
    router.push('/clusters');
  }, [router]);

  // Return null or a loading state while redirecting
  return null;
}
