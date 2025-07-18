import { useEffect, useRef } from 'react';
import { useRouter } from 'next/router';
import Shepherd from 'shepherd.js';
import { useFirstVisit } from '@/hooks/useFirstVisit';

export function useTour() {
  const tourRef = useRef(null);
  const router = useRouter();
  const { markTourCompleted } = useFirstVisit();

  useEffect(() => {
    // Initialize the tour only once
    if (!tourRef.current) {
      tourRef.current = new Shepherd.Tour({
        useModalOverlay: true,
        defaultStepOptions: {
          cancelIcon: {
            enabled: true,
          },
          scrollTo: { behavior: 'smooth', block: 'center' },
          modalOverlayOpeningPadding: 4,
        },
      });

      // Add tour event listeners
      tourRef.current.on('complete', () => {
        markTourCompleted();
      });

      tourRef.current.on('cancel', () => {
        markTourCompleted();
      });

      // Define tour steps
      const steps = [
        {
          title: 'Welcome to SkyPilot Dashboard',
          text: `
            <p>This guided tour will show you the key features of your SkyPilot control center.</p>
            <p>Let's start with the navigation bar.</p>
          `,
          buttons: [
            {
              text: 'Skip Tour',
              action() {
                this.cancel();
              },
              classes: 'shepherd-button-secondary',
            },
            {
              text: 'Start Tour',
              action() {
                this.next();
              },
            },
          ],
        },
        {
          title: 'Navigation Bar',
          text: `
            <p>This is your main navigation bar containing all the core sections of SkyPilot.</p>
            <p>Let's explore each section step by step.</p>
          `,
          attachTo: {
            element: '.fixed.top-0.left-0.right-0.bg-white',
            on: 'bottom',
          },
          buttons: [
            {
              text: 'Back',
              action() {
                this.back();
              },
              classes: 'shepherd-button-secondary',
            },
            {
              text: 'Next',
              action() {
                this.next();
              },
            },
          ],
        },
        {
          title: 'Clusters Section',
          text: `
            <p><strong>Clusters</strong> is where you manage your compute resources.</p>
            <p>Here you can view, create, and manage your cloud clusters across different providers.</p>
          `,
          attachTo: {
            element: function() {
              // Find the clusters link in the top navigation
              const clustersLinks = document.querySelectorAll('a[href="/clusters"]');
              // Return the first one found (should be in the top nav)
              return clustersLinks[0] || 'a[href="/clusters"]';
            },
            on: 'bottom',
          },
          modalOverlayOpeningPadding: 8,
          when: {
            show: function() {
              // Find all clusters links and highlight the one in the top navigation
              const clustersLinks = document.querySelectorAll('a[href="/clusters"]');
              clustersLinks.forEach((link, index) => {
                if (link instanceof HTMLElement) {
                  // Apply stronger highlighting with !important
                  link.style.setProperty('outline', '3px solid #2563eb', 'important');
                  link.style.setProperty('outline-offset', '2px', 'important');
                  link.style.setProperty('border-radius', '0.375rem', 'important');
                  link.style.setProperty('background-color', 'rgba(37, 99, 235, 0.15)', 'important');
                  link.style.setProperty('position', 'relative', 'important');
                  link.style.setProperty('z-index', '9999', 'important');
                  link.style.setProperty('box-shadow', '0 0 0 1px rgba(37, 99, 235, 0.3)', 'important');
                }
              });
            },
            hide: function() {
              // Remove highlighting from all clusters links
              const clustersLinks = document.querySelectorAll('a[href="/clusters"]');
              clustersLinks.forEach((link) => {
                if (link instanceof HTMLElement) {
                  link.style.removeProperty('outline');
                  link.style.removeProperty('outline-offset');
                  link.style.removeProperty('border-radius');
                  link.style.removeProperty('background-color');
                  link.style.removeProperty('position');
                  link.style.removeProperty('z-index');
                  link.style.removeProperty('box-shadow');
                }
              });
            }
          },
          buttons: [
            {
              text: 'Back',
              action() {
                this.back();
              },
              classes: 'shepherd-button-secondary',
            },
            {
              text: 'Next',
              action() {
                this.next();
              },
            },
          ],
        },
        {
          title: 'Infrastructure Column',
          text: `
            <p>In the clusters page, the <strong>Infra</strong> column shows you the underlying infrastructure details.</p>
            <p>This displays information about instance types, regions, and cloud providers for each cluster.</p>
          `,
          attachTo: {
            element: 'th',
            on: 'bottom',
          },
          beforeShowPromise: function() {
            // Navigate to clusters page if not already there
            if (window.location.pathname !== '/clusters') {
              return new Promise((resolve) => {
                window.location.href = '/clusters';
                setTimeout(resolve, 1000);
              });
            }
            return Promise.resolve();
          },
          when: {
            show: function() {
              // Try to find the Infra column header after the page loads
              const infraHeaders = Array.from(document.querySelectorAll('th')).filter(th => 
                th.textContent.includes('Infra')
              );
              if (infraHeaders.length > 0) {
                // Update the attachment to the actual Infra header
                this.options.attachTo.element = infraHeaders[0];
              }
            }
          },
          buttons: [
            {
              text: 'Back',
              action() {
                this.back();
              },
              classes: 'shepherd-button-secondary',
            },
            {
              text: 'Next',
              action() {
                this.next();
              },
            },
          ],
        },
        {
          title: 'Jobs Section',
          text: `
            <p><strong>Jobs</strong> is your job management center.</p>
            <p>View all submitted jobs, monitor their status and progress, and manage job execution.</p>
          `,
          attachTo: {
            element: function() {
              const jobsLinks = document.querySelectorAll('a[href="/jobs"]');
              return jobsLinks[0] || 'a[href="/jobs"]';
            },
            on: 'bottom',
          },
          modalOverlayOpeningPadding: 8,
          when: {
            show: function() {
              const jobsLinks = document.querySelectorAll('a[href="/jobs"]');
              jobsLinks.forEach((link) => {
                if (link instanceof HTMLElement) {
                  link.style.setProperty('outline', '3px solid #2563eb', 'important');
                  link.style.setProperty('outline-offset', '2px', 'important');
                  link.style.setProperty('border-radius', '0.375rem', 'important');
                  link.style.setProperty('background-color', 'rgba(37, 99, 235, 0.15)', 'important');
                  link.style.setProperty('position', 'relative', 'important');
                  link.style.setProperty('z-index', '9999', 'important');
                  link.style.setProperty('box-shadow', '0 0 0 1px rgba(37, 99, 235, 0.3)', 'important');
                }
              });
            },
            hide: function() {
              const jobsLinks = document.querySelectorAll('a[href="/jobs"]');
              jobsLinks.forEach((link) => {
                if (link instanceof HTMLElement) {
                  link.style.removeProperty('outline');
                  link.style.removeProperty('outline-offset');
                  link.style.removeProperty('border-radius');
                  link.style.removeProperty('background-color');
                  link.style.removeProperty('position');
                  link.style.removeProperty('z-index');
                  link.style.removeProperty('box-shadow');
                }
              });
            }
          },
          buttons: [
            {
              text: 'Back',
              action() {
                this.back();
              },
              classes: 'shepherd-button-secondary',
            },
            {
              text: 'Next',
              action() {
                this.next();
              },
            },
          ],
        },
        {
          title: 'Settings',
          text: `
            <p><strong>Settings</strong> allows you to configure your SkyPilot dashboard.</p>
            <p>Access configuration options and customize your experience.</p>
          `,
          attachTo: {
            element: function() {
              const configLinks = document.querySelectorAll('a[href="/config"]');
              return configLinks[0] || 'a[href="/config"]';
            },
            on: 'bottom',
          },
          modalOverlayOpeningPadding: 8,
          when: {
            show: function() {
              const configLinks = document.querySelectorAll('a[href="/config"]');
              configLinks.forEach((link) => {
                if (link instanceof HTMLElement) {
                  link.style.setProperty('outline', '3px solid #2563eb', 'important');
                  link.style.setProperty('outline-offset', '2px', 'important');
                  link.style.setProperty('border-radius', '0.375rem', 'important');
                  link.style.setProperty('background-color', 'rgba(37, 99, 235, 0.15)', 'important');
                  link.style.setProperty('position', 'relative', 'important');
                  link.style.setProperty('z-index', '9999', 'important');
                  link.style.setProperty('box-shadow', '0 0 0 1px rgba(37, 99, 235, 0.3)', 'important');
                }
              });
            },
            hide: function() {
              const configLinks = document.querySelectorAll('a[href="/config"]');
              configLinks.forEach((link) => {
                if (link instanceof HTMLElement) {
                  link.style.removeProperty('outline');
                  link.style.removeProperty('outline-offset');
                  link.style.removeProperty('border-radius');
                  link.style.removeProperty('background-color');
                  link.style.removeProperty('position');
                  link.style.removeProperty('z-index');
                  link.style.removeProperty('box-shadow');
                }
              });
            }
          },
          buttons: [
            {
              text: 'Back',
              action() {
                this.back();
              },
              classes: 'shepherd-button-secondary',
            },
            {
              text: 'Next',
              action() {
                this.next();
              },
            },
          ],
        },
        {
          title: 'Additional Resources',
          text: `
            <p>Don't miss these helpful external resources:</p>
            <ul>
              <li><strong>Docs</strong> - Complete documentation</li>
              <li><strong>GitHub</strong> - Source code and issues</li>
              <li><strong>Slack</strong> - Community support</li>
              <li><strong>Feedback</strong> - Report bugs or request features</li>
            </ul>
          `,
          attachTo: {
            element: 'a[href="https://skypilot.readthedocs.io/en/latest/"]',
            on: 'bottom',
          },
          buttons: [
            {
              text: 'Back',
              action() {
                this.back();
              },
              classes: 'shepherd-button-secondary',
            },
            {
              text: 'Finish',
              action() {
                this.complete();
              },
            },
          ],
        },
      ];

      // Add steps to the tour
      steps.forEach((step) => {
        tourRef.current.addStep(step);
      });
    }

    return () => {
      // Cleanup tour on unmount
      if (tourRef.current) {
        tourRef.current.complete();
      }
    };
  }, [markTourCompleted]);

  const startTour = () => {
    if (tourRef.current) {
      // Navigate to clusters page before starting tour
      router.push('/clusters').then(() => {
        // Small delay to ensure page is loaded
        setTimeout(() => {
          tourRef.current.start();
        }, 500);
      });
    }
  };

  const completeTour = () => {
    if (tourRef.current) {
      tourRef.current.complete();
    }
  };

  return {
    startTour,
    completeTour,
    tour: tourRef.current,
  };
} 
