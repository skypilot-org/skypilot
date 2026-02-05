'use client';

import React, { createContext, useContext, useState, useCallback } from 'react';

const UpgradeDetectionContext = createContext(undefined);

export function UpgradeDetectionProvider({ children }) {
  const [isUpgrading, setIsUpgrading] = useState(false);

  const reportUpgrade = useCallback(() => {
    setIsUpgrading(true);
  }, []);

  const clearUpgrade = useCallback(() => {
    setIsUpgrading(false);
  }, []);

  return (
    <UpgradeDetectionContext.Provider
      value={{ isUpgrading, reportUpgrade, clearUpgrade }}
    >
      {children}
    </UpgradeDetectionContext.Provider>
  );
}

export function useUpgradeDetection() {
  const context = useContext(UpgradeDetectionContext);
  if (!context) {
    throw new Error(
      'useUpgradeDetection must be used within UpgradeDetectionProvider'
    );
  }
  return context;
}
