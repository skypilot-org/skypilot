import React, { useState, useEffect } from 'react';
import { HelpCircle } from 'lucide-react';
import { useTour } from '@/hooks/useTour';
import { useFirstVisit } from '@/hooks/useFirstVisit';
import { CustomTooltip } from '@/components/utils';

export function TourHelpButton({ className = '', showPulse = false }) {
  const { startTour } = useTour();
  const { shouldPulseHelpButton } = useFirstVisit();
  const [isClient, setIsClient] = useState(false);

  useEffect(() => {
    setIsClient(true);
  }, []);

  const handleStartTour = () => {
    startTour();
  };

  // Don't render on server to avoid hydration issues
  if (!isClient) {
    return null;
  }

  return (
    <CustomTooltip
      content="Take a guided tour of the dashboard"
      className="text-sm text-muted-foreground"
    >
      <button
        onClick={handleStartTour}
        className={`tour-help-button ${showPulse || shouldPulseHelpButton ? 'pulse' : ''} ${className}`}
        title="Help & Tour"
        aria-label="Start guided tour"
      >
        <HelpCircle className="w-4 h-4" />
      </button>
    </CustomTooltip>
  );
}

export default TourHelpButton; 
