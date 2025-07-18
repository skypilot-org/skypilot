import { useEffect, useRef } from 'react';
import { useRouter } from 'next/router';
import Shepherd from 'shepherd.js';
import { useFirstVisit } from '@/hooks/useFirstVisit';

// Global function for copying code blocks in tour
if (typeof window !== 'undefined') {
  window['copyDashboardCodeBlock'] = function(button) {
    const codeContainer = button.closest('.bg-gray-50').querySelector('pre');
    const codeBlock = codeContainer.querySelector('code.block');
    const text = codeBlock.textContent;
    navigator.clipboard.writeText(text).then(() => {
      const originalSvg = button.innerHTML;
      button.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-check h-4 w-4"><path d="m9 12 2 2 4-4"/><path d="m21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9c2.87 0 5.42 1.35 7.07 3.45"/></svg>';
      setTimeout(() => {
        button.innerHTML = originalSvg;
      }, 2000);
    });
  };
}

export function useTour() {
  const tourRef = useRef(null);
  const router = useRouter();
  const { markTourCompleted } = useFirstVisit();

  useEffect(() => {
    // Initialize the tour only once
    if (!tourRef.current) {
      tourRef.current = new Shepherd.Tour({
        useModalOverlay: false,
        defaultStepOptions: {
          cancelIcon: {
            enabled: true,
          },
          scrollTo: { behavior: 'smooth', block: 'center' },
          when: {
            show() {
              const currentStep = Shepherd.activeTour?.getCurrentStep();
              const currentStepElement = currentStep?.getElement();
              const footer = currentStepElement?.querySelector('.shepherd-footer');
              const progress = document.createElement('span');
              progress.className = 'shepherd-progress';
              progress.innerText = `${Shepherd.activeTour?.steps.indexOf(currentStep) + 1} of ${Shepherd.activeTour?.steps.length}`;
              footer?.insertBefore(progress, footer.firstChild);
            }
          },
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
            text: `
            <p><strong>Welcome to SkyPilot!</strong></p>
            <p>SkyPilot is a framework for managing AI workloads on any cluster and cloud infrastructure.</p>
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
        // {
        //   title: 'Navigation Bar',
        //   text: `
        //     <p>This is your main navigation bar containing all the core sections of SkyPilot.</p>
        //     <p>Let's explore each section step by step.</p>
        //   `,
        //   attachTo: {
        //     element: '.fixed.top-0.left-0.right-0.bg-white',
        //     on: 'bottom',
        //   },
        //   buttons: [
        //     {
        //       text: 'Back',
        //       action() {
        //         this.back();
        //       },
        //       classes: 'shepherd-button-secondary',
        //     },
        //     {
        //       text: 'Next',
        //       action() {
        //         this.next();
        //       },
        //     },
        //   ],
        // },
        {
          title: 'Clusters',
          text: `
            <p>Spin up <strong>Sky Clusters</strong> on any infrastructure you have access to.</p>
            <p>You can SSH into any node, connect an IDE, or queue development jobs on it.</p>
          `,
          attachTo: {
            element: 'a[href="/dashboard/clusters"]',
            on: 'bottom',
            offset: { skidding: 0, distance: 0 },
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
          title: 'SkyPilot is infra-agnostic',
          text: `
            <p>Manage compute on any hyperscaler, neocloud, or Kubernetes cluster using a unified interface.</p>
          `,
          attachTo: {
            element: function() {
              // Target the Infra column header (th element with "Infra" text and sortable class)
              const infraHeader = Array.from(document.querySelectorAll('th.sortable')).find(th =>
                th.textContent && th.textContent.trim() === 'Infra'
              );
              if (infraHeader) {
                return infraHeader;
              }
              // Fallback: find any th with "Infra" text
              return Array.from(document.querySelectorAll('th')).find(th =>
                th.textContent && th.textContent.trim() === 'Infra'
              ) || 'th';
            },
            on: 'bottom',
            offset: { skidding: 0, distance: 0 },
          },
          beforeShowPromise: function() {
            // Navigate to clusters page if not already there
            if (window.location.pathname !== '/dashboard/clusters') {
              return new Promise((resolve) => {
                window.location.href = '/dashboard/clusters';
                setTimeout(resolve, 1000);
              });
            }
            return Promise.resolve();
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
            text: `
            <p><strong>Launch your first cluster</strong></p>
            <p>Spin up clusters using the Python SDK or the CLI.</p>
            <div class="space-y-2">
              <div class="rounded-lg border text-card-foreground shadow-sm p-3 bg-gray-50">
                <div class="flex items-center justify-between">
                  <pre class="text-sm w-full whitespace-pre-wrap">
                    <code class="block">sky launch</code>
                  </pre>
                  <button class="inline-flex items-center justify-center whitespace-nowrap text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 hover:bg-accent hover:text-accent-foreground h-8 w-8 rounded-full" onclick="copyDashboardCodeBlock(this)" title="Copy to clipboard">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-copy h-4 w-4">
                      <rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect>
                      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>
                    </svg>
                  </button>
                </div>
              </div>
              <div class="rounded-lg border text-card-foreground shadow-sm p-3 bg-gray-50">
                <div class="flex items-center justify-between">
                  <pre class="text-sm w-full whitespace-pre-wrap">
                    <code class="block">sky launch --gpus L4:8</code>
                  </pre>
                  <button class="inline-flex items-center justify-center whitespace-nowrap text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 hover:bg-accent hover:text-accent-foreground h-8 w-8 rounded-full" onclick="copyDashboardCodeBlock(this)" title="Copy to clipboard">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-copy h-4 w-4">
                      <rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect>
                      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>
                    </svg>
                  </button>
                </div>
              </div>
              <div class="rounded-lg border text-card-foreground shadow-sm p-3 bg-gray-50">
                <div class="flex items-center justify-between">
                  <pre class="text-sm w-full whitespace-pre-wrap">
                    <code class="block">sky launch --num-nodes 10 --cpus 32+</code>
                  </pre>
                  <button class="inline-flex items-center justify-center whitespace-nowrap text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 hover:bg-accent hover:text-accent-foreground h-8 w-8 rounded-full" onclick="copyDashboardCodeBlock(this)" title="Copy to clipboard">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-copy h-4 w-4">
                      <rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect>
                      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>
                    </svg>
                  </button>
                </div>
              </div>
            </div>
            `,
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
          title: 'Jobs',
          text: `
            <p>Use <strong>Managed Jobs</strong> for long-running workloads.</p>
            <p>They provide automatic recovery against failures, such as recovering from preemptions or GPU errors.</p>
          `,
          attachTo: {
                          element: function() {
                // Target the jobs link with the correct href
                const jobsLink = document.querySelector('a[href="/dashboard/jobs"]');
                if (jobsLink) {
                  return jobsLink;
                }
                // Fallback to original selector
                return document.querySelector('a[href="/dashboard/jobs"]') || 'a[href="/dashboard/jobs"]';
              },
            on: 'bottom',
            offset: { skidding: 0, distance: -16 },
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
          title: 'Infra',
          text: `
            <p>Bring your Kubernetes clusters, VMs (on 17+ supported clouds), or SSH nodes into SkyPilot.</p>
            <p>You can monitor your infrastructure here.</p>
          `,
          attachTo: {
                          element: function() {
                // Target the config link with the correct href
                const configLink = document.querySelector('a[href="/dashboard/infra"]');
                if (configLink) {
                  return configLink;
                }
                // Fallback to original selector
                return document.querySelector('a[href="/dashboard/infra"]') || 'a[href="/dashboard/infra"]';
              },
            on: 'bottom',
            offset: { skidding: 0, distance: -16 },
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
          title: 'Workspaces',
          text: `
            <p>Use Workspaces to isolate teams or projects.</p>
          `,
          attachTo: {
            element: 'a[href="/dashboard/workspaces"]',
            on: 'bottom',
            offset: { skidding: 0, distance: -16 },
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
          {
              title: 'Users',
              text: `
            <p>SkyPilot provides user management with RBAC and SSO support.</p>
          `,
              attachTo: {
                  element: 'a[href="/dashboard/users"]',
                  on: 'bottom',
                  offset: { skidding: 0, distance: -16 },
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
          {
            title: 'Happy SkyPilot!',
            text: `
              <p>To get started, refer to <a href="https://docs.skypilot.co/en/latest/getting-started/installation.html">Installation</a> and <a href="https://docs.skypilot.co/en/latest/getting-started/quickstart.html">Quickstart</a> docs.</p>
              <p>To reach out, join the <a href="https://skypilot.slack.com">SkyPilot Slack</a> to chat with the community.</p>
            `,
            buttons: [
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
