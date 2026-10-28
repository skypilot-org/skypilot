import React, { useLayoutEffect, useRef, useState } from 'react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { BASE_PATH } from '@/data/connectors/constants';

// Matches the page's bottom padding (p-6), so the block ends where the page
// content would.
const BOTTOM_GAP = 24;

export function PageNotFound() {
  const ref = useRef(null);
  const [minHeight, setMinHeight] = useState(400);

  // Fill the rest of the window below wherever the layout puts this block,
  // so the content sits in the middle of what the viewer can see whatever
  // header sits above it. Measured before paint so it doesn't jump, and again
  // when elements are added or removed elsewhere on the page (a banner
  // appearing above it, say). Body has a fixed height, so a ResizeObserver
  // on it would not see that.
  useLayoutEffect(() => {
    const fit = () => {
      if (!ref.current) return;
      const top = ref.current.getBoundingClientRect().top + window.scrollY;
      setMinHeight(Math.max(400, window.innerHeight - top - BOTTOM_GAP));
    };
    fit();
    const observer = new MutationObserver(fit);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', fit);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', fit);
    };
  }, []);

  return (
    <div
      ref={ref}
      className="flex flex-col items-center justify-center text-center"
      style={{ minHeight, gap: 20, padding: 32 }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`${BASE_PATH}/gpu-not-found.svg`}
        alt=""
        width={150}
        height={113}
      />
      <div style={{ fontSize: 14, lineHeight: '21px', color: '#525252' }}>
        This page has wandered off.
      </div>
      <Button
        asChild
        variant="outline"
        size="sm"
        className="h-8 border-gray-300 bg-white text-gray-900 hover:bg-gray-50"
      >
        <Link href="/infra">Go to Infra</Link>
      </Button>
    </div>
  );
}
