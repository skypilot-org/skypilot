import React from 'react';
import { useTour } from '@/hooks/useTour';
import { useFirstVisit } from '@/hooks/useFirstVisit';

// Development helper component for testing tour functionality
// Remove or disable in production
export function TourDevTools() {
  const { startTour } = useTour();
  const { resetFirstVisit, isFirstVisit, tourCompleted } = useFirstVisit();

  // Only show in development mode
  if (process.env.NODE_ENV !== 'development') {
    return null;
  }

  return (
    <div className="fixed bottom-4 left-4 bg-white p-3 rounded-lg shadow-lg border border-gray-200 z-50">
      <h4 className="text-sm font-semibold text-gray-900 mb-2">Tour Dev Tools</h4>
      
      <div className="text-xs text-gray-600 mb-2">
        <div>First visit: {isFirstVisit ? 'Yes' : 'No'}</div>
        <div>Tour completed: {tourCompleted ? 'Yes' : 'No'}</div>
      </div>
      
      <div className="flex flex-col space-y-2">
        <button
          onClick={startTour}
          className="px-2 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700"
        >
          Start Tour
        </button>
        <button
          onClick={resetFirstVisit}
          className="px-2 py-1 bg-gray-600 text-white text-xs rounded hover:bg-gray-700"
        >
          Reset First Visit
        </button>
      </div>
    </div>
  );
}

export default TourDevTools; 
