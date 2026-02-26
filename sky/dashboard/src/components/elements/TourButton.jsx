import React from 'react';
import { useTour } from '@/hooks/useTour';
import { HelpCircle } from 'lucide-react';
import { Tooltip } from './Tooltip';

export function TourButton() {
  const { startTour } = useTour();

  return (
    <Tooltip text="Start a tour">
      <button
        onClick={startTour}
        className="fixed bottom-4 right-4 bg-transparent text-gray-400 dark:text-gray-500 p-2 rounded-full hover:text-gray-500 dark:hover:text-gray-400 focus:outline-none"
        aria-label="Start Tour"
      >
        <HelpCircle className="h-5 w-5" />
      </button>
    </Tooltip>
  );
}
