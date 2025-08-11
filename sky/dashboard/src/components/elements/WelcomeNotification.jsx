import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';
import { X, Play, Sparkles } from 'lucide-react';
import { useTour } from '@/hooks/useTour';
import { useFirstVisit } from '@/hooks/useFirstVisit';
import { apiClient } from '@/data/connectors/client';

const AUTH_FIRST_VISIT_KEY = 'skypilot-dashboard-auth-first-visit';

export function WelcomeNotification({ isAuthenticated }) {
  const { startTour } = useTour();
  const [isVisible, setIsVisible] = useState(false);
  const [isClient, setIsClient] = useState(false);
  const [authed, setAuthed] = useState(false);

  // Defer first-visit prompt until authenticated
  const { shouldShowTourPrompt, markTourCompleted } = useFirstVisit({
    enabled: authed,
  });

  useEffect(() => {
    let canceled = false;
    let timerId = null;
    setIsClient(true);

    const decide = async () => {
      let effectiveAuth =
        typeof isAuthenticated === 'boolean' ? isAuthenticated : false;
      if (typeof isAuthenticated !== 'boolean') {
        try {
          const res = await apiClient.get('/api/health');
          effectiveAuth = !!(res && res.ok === true);
        } catch (_) {
          effectiveAuth = false;
        }
      }
      if (canceled) return;
      setAuthed(effectiveAuth);

      if (effectiveAuth) {
        // Show when either general first-visit is true or it's the first authenticated visit
        const isFirstAuthedVisit =
          !sessionStorage.getItem(AUTH_FIRST_VISIT_KEY);
        const shouldShow = shouldShowTourPrompt || isFirstAuthedVisit;
        if (shouldShow) {
          // Mark first authenticated visit so we only show once post-auth
          if (isFirstAuthedVisit) {
            sessionStorage.setItem(AUTH_FIRST_VISIT_KEY, 'true');
          }
          timerId = setTimeout(() => {
            if (!canceled) setIsVisible(true);
          }, 2000);
        }
      }
    };

    decide();

    return () => {
      canceled = true;
      if (timerId) clearTimeout(timerId);
    };
  }, [shouldShowTourPrompt, isAuthenticated]);

  const handleStartTour = () => {
    setIsVisible(false);
    startTour();
  };

  const handleDismiss = () => {
    setIsVisible(false);
    markTourCompleted();
  };

  if (!isClient || !authed || !isVisible) {
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

WelcomeNotification.propTypes = {
  isAuthenticated: PropTypes.bool,
};

export default WelcomeNotification;
