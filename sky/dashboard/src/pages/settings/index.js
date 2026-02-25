import { useEffect } from 'react';
import { useRouter } from 'next/router';

export default function SettingsIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/settings/config');
  }, [router]);
  return null;
}
