import React, { useState, useEffect } from 'react';
import { X, Play, Sparkles } from 'lucide-react';
import { useTour } from '@/hooks/useTour';
import { useFirstVisit } from '@/hooks/useFirstVisit';

export function WelcomeNotification() {
  const { startTour } = useTour();
  const { shouldShowTourPrompt, markTourCompleted } = useFirstVisit();
  const [isVisible, setIsVisible] = useState(false);
  const [isClient, setIsClient] = useState(false);

  useEffect(() => {
    setIsClient(true);
    if (shouldShowTourPrompt) {
      // Show notification after a short delay for better UX
      const timer = setTimeout(() => {
        setIsVisible(true);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [shouldShowTourPrompt]);

  const handleStartTour = () => {
    setIsVisible(false);
    startTour();
  };

  const handleDismiss = () => {
    setIsVisible(false);
    markTourCompleted();
  };

  // Don't render on server or if not needed
  if (!isClient || !shouldShowTourPrompt || !isVisible) {
    return null;
  }

  return (
    <div className="fixed top-20 right-6 z-50 max-w-sm">
      <div className="bg-white rounded-md shadow-lg border border-gray-200 p-4 transform transition-all duration-300 ease-out">
        {/* Close button */}
        <button
          onClick={handleDismiss}
          className="absolute top-3 right-3 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-full p-1 transition-all duration-150"
          aria-label="Dismiss notification"
        >
          <X className="w-3 h-3" />
        </button>

        {/* Content */}
        <div className="pr-6">
          <div className="flex items-start mb-3">
            <div className="flex items-center justify-center w-7 h-7 bg-blue-50 rounded-full mr-3 mt-0.5">
              <Sparkles className="w-3.5 h-3.5 text-blue-600" />
            </div>
            <div>
              <h3 className="text-sm font-medium text-gray-900 mb-1">
                Welcome to SkyPilot!
              </h3>
              <p className="text-sm text-gray-600 leading-relaxed">
                New to the dashboard? Take a quick guided tour to discover all
                the features.
              </p>
            </div>
          </div>

          <div className="flex space-x-2 ml-10">
            <button
              onClick={handleStartTour}
              className="flex items-center px-3 py-1.5 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700 transition-colors duration-150"
            >
              <Play className="w-3 h-3 mr-1.5" />
              Start Tour
            </button>
            <button
              onClick={handleDismiss}
              className="px-3 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded transition-colors duration-150"
            >
              Maybe Later
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default WelcomeNotification;
