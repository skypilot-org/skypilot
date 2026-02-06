'use client';

import React from 'react';
import { useUpgradeDetection } from '@/hooks/useUpgradeDetection';
import { useMobile } from '@/hooks/useMobile';

export function UpgradeBanner() {
  const { isUpgrading } = useUpgradeDetection();
  const isMobile = useMobile();

  if (!isUpgrading) {
    return null;
  }

  return (
    <div
      className={`fixed z-40 bg-yellow-50 border-b border-yellow-200 ${
        isMobile ? 'top-[56px] left-0 right-0' : 'top-0 left-56 right-0'
      }`}
    >
      <div className="max-w-7xl mx-auto py-3 px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-center">
          <div className="flex items-center">
            <svg
              className="h-5 w-5 text-yellow-600 mr-2 animate-spin"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              ></circle>
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              ></path>
            </svg>
            <span className="text-sm font-medium text-yellow-800">
              Your SkyPilot deployment is undergoing upgrades. Refresh in a few
              moments.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
