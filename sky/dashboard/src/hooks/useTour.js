import React, {
  useEffect,
  useRef,
  createContext,
  useContext,
  useState,
} from 'react';
import { useRouter } from 'next/router';
import Shepherd from 'shepherd.js';
import { useFirstVisit } from '@/hooks/useFirstVisit';

const TourContext = createContext(null);

export function useTour() {
  const context = useContext(TourContext);
  if (!context) {
    throw new Error('useTour must be used within a TourProvider');
  }
  return context;
}

// Global function for copying code blocks in tour
if (typeof window !== 'undefined') {
  window['copyDashboardCodeBlock'] = function (button) {
    const codeContainer = button.closest('.bg-gray-50').querySelector('pre');
    const codeBlock = codeContainer.querySelector('code.block');
    const text = codeBlock.textContent;
    navigator.clipboard.writeText(text).then(() => {
      const originalSvg = button.innerHTML;
      button.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-check h-4 w-4"><path d="m9 12 2 2 4-4"/><path d="m21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9c2.87 0 5.42 1.35 7.07 3.45"/></svg>';
      setTimeout(() => {
        button.innerHTML = originalSvg;
      }, 2000);
    });
  };
}

export function TourProvider({ children }) {
  const tourRef = useRef(null);
  const router = useRouter();
  const { isFirstVisit, markTourCompleted } = useFirstVisit();
  const [tourAutoStarted, setTourAutoStarted] = useState(false);

  const startTour = () => {
    if (tourRef.current) {
      // Small delay to ensure page is loaded
      setTimeout(() => {
        tourRef.current.start();
      }, 100);
    }
  };

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
          arrow: false,
          highlightClass: 'shepherd-highlight',
          when: {
            show() {
              const currentStep = Shepherd.activeTour?.getCurrentStep();
              const currentStepElement = currentStep?.getElement();
              const footer =
                currentStepElement?.querySelector('.shepherd-footer');
              const progress = document.createElement('span');
              progress.className = 'shepherd-progress';
              progress.innerText = `${Shepherd.activeTour?.steps.indexOf(currentStep) + 1} of ${Shepherd.activeTour?.steps.length}`;
              footer?.insertBefore(progress, footer.firstChild);

              // Set CSS custom property for dialog height to help mobile menu positioning
              if (currentStepElement) {
                const dialogHeight = currentStepElement.offsetHeight;
                document.documentElement.style.setProperty(
                  '--shepherd-dialog-height',
                  `${dialogHeight + 20}px`
                );

                // Programmatically adjust mobile menu height for better reliability
                if (window.innerWidth < 768) {
                  // Try multiple ways to find the mobile menu
                  let mobileMenu = null;
                  const selectors = [
                    '.fixed.top-14.left-0.w-64',
                    'div.fixed.w-64.bg-white.border-r',
                    '.fixed.w-64.transform',
                    '[class*="fixed"][class*="w-64"][class*="bg-white"]',
                    'div[class*="fixed"][class*="top-14"][class*="left-0"][class*="w-64"]',
                  ];

                  for (const selector of selectors) {
                    mobileMenu = document.querySelector(selector);
                    if (mobileMenu) break;
                  }

                  // If still not found, try finding by position and size
                  if (!mobileMenu) {
                    const allDivs = document.querySelectorAll('div.fixed');
                    for (const div of allDivs) {
                      const rect = div.getBoundingClientRect();
                      if (
                        rect.width === 256 &&
                        rect.left === 0 &&
                        rect.top >= 50
                      ) {
                        // w-64 = 256px
                        mobileMenu = div;
                        break;
                      }
                    }
                  }

                  if (mobileMenu && mobileMenu instanceof HTMLElement) {
                    // Calculate available height from top bar to dialog top
                    const dialogRect =
                      currentStepElement.getBoundingClientRect();
                    const topBarHeight = 56;
                    const availableHeight = dialogRect.top - topBarHeight;

                    // Use direct pixel height instead of calc() to avoid calc issues
                    mobileMenu.style.setProperty(
                      'height',
                      `${availableHeight}px`,
                      'important'
                    );
                    mobileMenu.style.setProperty(
                      'max-height',
                      `${availableHeight}px`,
                      'important'
                    );
                  }
                }
              }

              // Add custom highlight styling to the target element
              const targetElement = currentStep?.getTarget();
              if (targetElement && targetElement instanceof HTMLElement) {
                targetElement.style.outline = '3px solid #3b82f6';
                targetElement.style.outlineOffset = '2px';
                targetElement.style.borderRadius = '8px';
                targetElement.style.position = 'relative';
                targetElement.style.zIndex = '9999';
                targetElement.setAttribute('data-shepherd-highlighted', 'true');
              }
            },
            hide() {
              // Remove custom highlight styling when step is hidden
              const targetElement = document.querySelector(
                '[data-shepherd-highlighted="true"]'
              );
              if (targetElement && targetElement instanceof HTMLElement) {
                targetElement.style.outline = '';
                targetElement.style.outlineOffset = '';
                targetElement.style.borderRadius = '';
                targetElement.style.boxShadow = '';
                targetElement.style.position = '';
                targetElement.style.zIndex = '';
                targetElement.removeAttribute('data-shepherd-highlighted');
              }

              // Clean up CSS custom property for dialog height
              document.documentElement.style.removeProperty(
                '--shepherd-dialog-height'
              );

              // Restore mobile menu height
              const mobileMenu =
                document.querySelector('.fixed.top-14.left-0.w-64') ||
                document.querySelector('div.fixed.w-64.bg-white.border-r') ||
                document.querySelector('.fixed.w-64.transform') ||
                document.querySelector(
                  '[class*="fixed"][class*="w-64"][class*="bg-white"]'
                );
              if (mobileMenu && mobileMenu instanceof HTMLElement) {
                mobileMenu.style.removeProperty('height');
                mobileMenu.style.removeProperty('max-height');
              }
            },
          },
        },
      });

      // Add global CSS styling for tour
      const globalStyle = document.createElement('style');
      globalStyle.id = 'shepherd-global-custom-style';
      globalStyle.textContent = `
          .shepherd-element {
            /* Uniform 1px border using inner box-shadow so corners stay consistent */
            border: none !important;
            border-radius: 10px !important;
            z-index: 30000 !important;
            box-shadow: 0 0 0 1px #d1d5db inset, 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05) !important;
            overflow: visible !important;
            background-clip: padding-box !important;
          }

          .shepherd-title {
              font-weight: bold;
              color: #111827;
              margin: 0;
          }

          .shepherd-element .shepherd-header {
              padding: 1rem 1rem 0.5rem 1rem;
          }

          .shepherd-element .shepherd-text {
              padding: 0.5rem 1rem 1rem 1rem;
          }

          /* Fix mobile menu gap when tour dialog is present */
          @media (max-width: 767px) {
            /* Very specific selector to override Tailwind's h-[calc(100vh-56px)] class */
            div.fixed.top-14.left-0.w-64.bg-white.border-r.shadow-lg.z-50.transform,
            .fixed.top-14.left-0.w-64.bg-white.shadow-lg.z-50,
            div[class*="fixed"][class*="top-14"][class*="left-0"][class*="w-64"][class*="bg-white"][class*="shadow-lg"][class*="z-50"] {
              height: calc(100vh - 56px - var(--shepherd-dialog-height, 200px)) !important;
              max-height: calc(100vh - 56px - var(--shepherd-dialog-height, 200px)) !important;
            }
            
            /* Target the mobile menu by its exact class combination from the HTML */
            .fixed.w-64.bg-white.border-r.border-gray-200.shadow-lg.z-50.transform,
            .fixed[class*="w-64"][class*="bg-white"][class*="border-r"][class*="shadow-lg"][class*="z-50"][class*="transform"] {
              height: calc(100vh - 56px - var(--shepherd-dialog-height, 200px)) !important;
            }
            
            /* Even more specific - target by multiple class combinations */
            .fixed.top-14.left-0[class*="w-64"],
            div.fixed[class*="top-14"][class*="left-0"][class*="w-64"] {
              height: calc(100vh - 56px - var(--shepherd-dialog-height, 200px)) !important;
            }
            
            /* Super aggressive approach - use high specificity to override Tailwind */
            body div.fixed.w-64:not(.shepherd-element),
            html body div.fixed.w-64:not(.shepherd-element) {
              height: calc(100vh - 56px - var(--shepherd-dialog-height, 200px)) !important;
            }
            
            /* Fallback selectors for other mobile menu patterns */
            nav[data-state="open"],
            .mobile-menu.open,
            [data-mobile-menu="true"] {
              height: calc(100vh - var(--shepherd-dialog-height, 200px)) !important;
              max-height: calc(100vh - var(--shepherd-dialog-height, 200px)) !important;
            }
            
            /* Ensure mobile menu content flows properly */
            .fixed.w-64 nav,
            .fixed[class*="w-64"] nav {
              height: 100% !important;
              overflow-y: auto !important;
            }
          }

        `;
      if (!document.getElementById('shepherd-global-custom-style')) {
        document.head.appendChild(globalStyle);
      }

      // Add tour event listeners
      tourRef.current.on('complete', () => {
        // Remove any remaining highlights
        const targetElement = document.querySelector(
          '[data-shepherd-highlighted="true"]'
        );
        if (targetElement && targetElement instanceof HTMLElement) {
          targetElement.style.outline = '';
          targetElement.style.outlineOffset = '';
          targetElement.style.borderRadius = '';
          targetElement.style.boxShadow = '';
          targetElement.style.position = '';
          targetElement.style.zIndex = '';
          targetElement.removeAttribute('data-shepherd-highlighted');
        }
        // Remove column overlay and related elements
        const overlay = document.getElementById('shepherd-column-overlay');
        if (overlay) {
          overlay.remove();
        }
        const anchorPoint = document.getElementById('shepherd-column-anchor');
        if (anchorPoint) {
          anchorPoint.remove();
        }
        const globalStyle = document.getElementById(
          'shepherd-global-custom-style'
        );
        if (globalStyle) {
          globalStyle.remove();
        }
        // Clean up CSS custom property for dialog height
        document.documentElement.style.removeProperty(
          '--shepherd-dialog-height'
        );

        // Restore mobile menu height
        const mobileMenu =
          document.querySelector('.fixed.top-14.left-0.w-64') ||
          document.querySelector('div.fixed.w-64.bg-white.border-r') ||
          document.querySelector('.fixed.w-64.transform') ||
          document.querySelector(
            '[class*="fixed"][class*="w-64"][class*="bg-white"]'
          );
        if (mobileMenu && mobileMenu instanceof HTMLElement) {
          mobileMenu.style.removeProperty('height');
          mobileMenu.style.removeProperty('max-height');
        }

        markTourCompleted();
      });

      tourRef.current.on('cancel', () => {
        // Remove any remaining highlights when tour is cancelled
        const targetElement = document.querySelector(
          '[data-shepherd-highlighted="true"]'
        );
        if (targetElement && targetElement instanceof HTMLElement) {
          targetElement.style.outline = '';
          targetElement.style.outlineOffset = '';
          targetElement.style.borderRadius = '';
          targetElement.style.boxShadow = '';
          targetElement.style.position = '';
          targetElement.style.zIndex = '';
          targetElement.removeAttribute('data-shepherd-highlighted');
        }
        // Remove column overlay and related elements
        const overlay = document.getElementById('shepherd-column-overlay');
        if (overlay) {
          overlay.remove();
        }
        const anchorPoint = document.getElementById('shepherd-column-anchor');
        if (anchorPoint) {
          anchorPoint.remove();
        }
        const globalStyle = document.getElementById(
          'shepherd-global-custom-style'
        );
        if (globalStyle) {
          globalStyle.remove();
        }
        // Clean up CSS custom property for dialog height
        document.documentElement.style.removeProperty(
          '--shepherd-dialog-height'
        );

        // Restore mobile menu height
        const mobileMenu =
          document.querySelector('.fixed.top-14.left-0.w-64') ||
          document.querySelector('div.fixed.w-64.bg-white.border-r') ||
          document.querySelector('.fixed.w-64.transform') ||
          document.querySelector(
            '[class*="fixed"][class*="w-64"][class*="bg-white"]'
          );
        if (mobileMenu && mobileMenu instanceof HTMLElement) {
          mobileMenu.style.removeProperty('height');
          mobileMenu.style.removeProperty('max-height');
        }

        markTourCompleted();
      });

      // Define tour steps
      const steps = [
        {
          title: '👋 Welcome to SkyPilot!',
          text: `
              <p>SkyPilot is a system for managing AI workloads on any cluster and cloud infrastructure.</p>
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
          title: 'Clusters',
          text: `
              <p>Spin up <strong>Sky Clusters</strong> on any infrastructure you have access to.</p>
              <p>Easily SSH into any node, connect an IDE, or queue development jobs.</p>
            `,
          attachTo: {
            element: 'a[href="/dashboard/clusters"]',
            on: 'bottom',
            offset: { skidding: 0, distance: 10 },
          },
          beforeShowPromise: function () {
            return new Promise((resolve) => {
              const setupClustersStep = () => {
                // Check if we're on mobile by looking for hamburger menu
                const isMobile = window.innerWidth < 768; // Tailwind md breakpoint
                const hamburgerButton = document.querySelector(
                  '[data-testid="mobile-menu-button"], button[aria-label*="menu"], button[aria-label*="Menu"], .mobile-menu-button, [role="button"][aria-expanded]'
                );

                if (isMobile && hamburgerButton) {
                  // Check if menu is already expanded
                  const isExpanded =
                    hamburgerButton.getAttribute('aria-expanded') === 'true' ||
                    hamburgerButton.classList.contains('open') ||
                    document.querySelector(
                      'nav[data-state="open"], .mobile-menu.open, [data-mobile-menu="true"]'
                    );

                  if (!isExpanded) {
                    // Click hamburger to expand menu
                    if (hamburgerButton instanceof HTMLElement) {
                      hamburgerButton.click();
                    }
                    // Wait for menu animation to complete
                    setTimeout(() => {
                      resolve();
                    }, 300);
                    return;
                  }
                }
                resolve();
              };

              if (router.pathname !== '/clusters') {
                router.push('/clusters').then(() => {
                  setTimeout(setupClustersStep, 500); // Wait for page to render
                });
              } else {
                setupClustersStep();
              }
            });
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
              <p>Run compute on any hyperscaler, neocloud, or Kubernetes cluster — all within a unified system.</p>
            `,
          attachTo: {
            element: function () {
              // Target the anchor point at the bottom edge of the column highlight
              const anchorPoint = document.getElementById(
                'shepherd-column-anchor'
              );
              if (anchorPoint) {
                return anchorPoint;
              }

              // Fallback to the bottom cell of the Infra column
              const infraHeader = Array.from(
                document.querySelectorAll('thead th')
              ).find(
                (th) => th.textContent && th.textContent.trim() === 'Infra'
              );

              if (infraHeader) {
                const table = infraHeader.closest('table');
                const headerRow = infraHeader.parentElement;
                const columnIndex = Array.from(headerRow.children).indexOf(
                  infraHeader
                );

                if (table) {
                  // Find the last row with data in this column
                  const rows = table.querySelectorAll('tbody tr');
                  let lastCell = null;

                  // Iterate through rows to find the last one with a cell in this column
                  for (let i = rows.length - 1; i >= 0; i--) {
                    const cell = rows[i].children[columnIndex];
                    if (cell) {
                      lastCell = cell;
                      break;
                    }
                  }

                  if (lastCell) {
                    return lastCell;
                  }
                }

                // Fallback to header if no data cells found
                return infraHeader;
              }

              // Fallback to table
              return document.querySelector('table') || 'body';
            },
            on: 'bottom',
            offset: { skidding: 0, distance: 15 },
          },
          beforeShowPromise: function () {
            return new Promise((resolve) => {
              const setupElements = () => {
                // Find the Infra column and create a unified column highlight
                const infraHeader = Array.from(
                  document.querySelectorAll('thead th')
                ).find(
                  (th) => th.textContent && th.textContent.trim() === 'Infra'
                );

                if (infraHeader && infraHeader instanceof HTMLElement) {
                  const table = infraHeader.closest('table');
                  if (table) {
                    // Check if the infra column is visible and scroll if needed
                    const headerRect = infraHeader.getBoundingClientRect();
                    const viewportWidth = window.innerWidth;
                    const scrollContainer =
                      table.closest(
                        '.overflow-x-auto, .overflow-auto, [style*="overflow"]'
                      ) || table.parentElement;

                    // If the column is not fully visible (cut off on the right)
                    if (
                      headerRect.right > viewportWidth ||
                      headerRect.left < 0
                    ) {
                      if (
                        scrollContainer &&
                        scrollContainer instanceof HTMLElement
                      ) {
                        // Calculate how much to scroll to center the column
                        const containerRect =
                          scrollContainer.getBoundingClientRect();
                        const targetScrollLeft =
                          infraHeader.offsetLeft -
                          containerRect.width / 2 +
                          headerRect.width / 2;

                        // Smooth scroll to make the column visible
                        scrollContainer.scrollTo({
                          left: Math.max(0, targetScrollLeft),
                          behavior: 'smooth',
                        });

                        // Wait for scroll animation to complete before proceeding
                        setTimeout(() => {
                          setupInfraColumnOverlay();
                        }, 500);
                        return;
                      }
                    }

                    setupInfraColumnOverlay();
                  }
                } else {
                  resolve();
                }

                function setupInfraColumnOverlay() {
                  const infraHeaderUpdated = Array.from(
                    document.querySelectorAll('thead th')
                  ).find(
                    (th) => th.textContent && th.textContent.trim() === 'Infra'
                  );

                  if (
                    infraHeaderUpdated &&
                    infraHeaderUpdated instanceof HTMLElement
                  ) {
                    const table = infraHeaderUpdated.closest('table');
                    if (table) {
                      const headerRow = infraHeaderUpdated.parentElement;
                      const columnIndex = Array.from(
                        headerRow.children
                      ).indexOf(infraHeaderUpdated);
                      const headerRect =
                        infraHeaderUpdated.getBoundingClientRect();
                      const rows = table.querySelectorAll('tbody tr');
                      let lastCellRect = headerRect;

                      rows.forEach((row) => {
                        const cell = row.children[columnIndex];
                        if (cell) {
                          lastCellRect = cell.getBoundingClientRect();
                        }
                      });

                      // Create a single overlay for the entire column
                      const overlay = document.createElement('div');
                      overlay.id = 'shepherd-column-overlay';
                      overlay.style.position = 'fixed';
                      overlay.style.left = `${headerRect.left - 4}px`;
                      overlay.style.top = `${headerRect.top - 4}px`;
                      overlay.style.width = `${headerRect.width + 8}px`;
                      overlay.style.height = `${
                        lastCellRect.bottom - headerRect.top + 8
                      }px`;
                      overlay.style.outline = '3px solid #3b82f6';
                      overlay.style.outlineOffset = '2px';
                      overlay.style.borderRadius = '8px';
                      overlay.style.zIndex = '9998';
                      overlay.style.pointerEvents = 'none';
                      overlay.style.backgroundColor = 'transparent';
                      document.body.appendChild(overlay);

                      // Create invisible anchor point at the bottom edge of the highlighted column
                      const overlayBottom = lastCellRect.bottom + 5; // +4 padding, +2 offset, +3 outline
                      const anchorPoint = document.createElement('div');
                      anchorPoint.id = 'shepherd-column-anchor';
                      anchorPoint.style.position = 'fixed';
                      anchorPoint.style.left = `${
                        headerRect.left + headerRect.width / 2
                      }px`;
                      anchorPoint.style.top = `${overlayBottom}px`;
                      anchorPoint.style.width = '1px';
                      anchorPoint.style.height = '1px';
                      anchorPoint.style.zIndex = '9999';
                      anchorPoint.style.pointerEvents = 'none';
                      anchorPoint.style.backgroundColor = 'transparent';
                      anchorPoint.style.transform = 'translate(-50%, -50%)';
                      document.body.appendChild(anchorPoint);
                    }
                  }
                  resolve();
                }
              };

              // Navigate to clusters page if not already there, then set up elements
              if (window.location.pathname !== '/dashboard/clusters') {
                router.push('/dashboard/clusters').then(() => {
                  setTimeout(setupElements, 500); // Wait for page to render
                });
              } else {
                setupElements(); // Page is already loaded
              }
            });
          },
          when: {
            show() {
              // Add progress indicator (same as default behavior)
              const currentStep = Shepherd.activeTour?.getCurrentStep();
              const currentStepElement = currentStep?.getElement();
              const footer =
                currentStepElement?.querySelector('.shepherd-footer');
              const progress = document.createElement('span');
              progress.className = 'shepherd-progress';
              progress.innerText = `${
                Shepherd.activeTour?.steps.indexOf(currentStep) + 1
              } of ${Shepherd.activeTour?.steps.length}`;
              footer?.insertBefore(progress, footer.firstChild);

              // Set CSS custom property for dialog height to help mobile menu positioning
              if (currentStepElement) {
                const dialogHeight = currentStepElement.offsetHeight;
                document.documentElement.style.setProperty(
                  '--shepherd-dialog-height',
                  `${dialogHeight + 20}px`
                );

                // Programmatically adjust mobile menu height for better reliability
                if (window.innerWidth < 768) {
                  // Try multiple ways to find the mobile menu
                  let mobileMenu = null;
                  const selectors = [
                    '.fixed.top-14.left-0.w-64',
                    'div.fixed.w-64.bg-white.border-r',
                    '.fixed.w-64.transform',
                    '[class*="fixed"][class*="w-64"][class*="bg-white"]',
                    'div[class*="fixed"][class*="top-14"][class*="left-0"][class*="w-64"]',
                  ];

                  for (const selector of selectors) {
                    mobileMenu = document.querySelector(selector);
                    if (mobileMenu) break;
                  }

                  // If still not found, try finding by position and size
                  if (!mobileMenu) {
                    const allDivs = document.querySelectorAll('div.fixed');
                    for (const div of allDivs) {
                      const rect = div.getBoundingClientRect();
                      if (
                        rect.width === 256 &&
                        rect.left === 0 &&
                        rect.top >= 50
                      ) {
                        // w-64 = 256px
                        mobileMenu = div;
                        break;
                      }
                    }
                  }

                  if (mobileMenu && mobileMenu instanceof HTMLElement) {
                    // Calculate available height from top bar to dialog top
                    const dialogRect =
                      currentStepElement.getBoundingClientRect();
                    const topBarHeight = 56;
                    const availableHeight = dialogRect.top - topBarHeight;

                    // Use direct pixel height instead of calc() to avoid calc issues
                    mobileMenu.style.setProperty(
                      'height',
                      `${availableHeight}px`,
                      'important'
                    );
                    mobileMenu.style.setProperty(
                      'max-height',
                      `${availableHeight}px`,
                      'important'
                    );
                  }
                }
              }
            },
            hide() {
              // Remove the column overlay
              const overlay = document.getElementById(
                'shepherd-column-overlay'
              );
              if (overlay) {
                overlay.remove();
              }

              // Remove the anchor point
              const anchorPoint = document.getElementById(
                'shepherd-column-anchor'
              );
              if (anchorPoint) {
                anchorPoint.remove();
              }

              // Clean up CSS custom property for dialog height
              document.documentElement.style.removeProperty(
                '--shepherd-dialog-height'
              );

              // Restore mobile menu height
              const mobileMenu =
                document.querySelector('.fixed.top-14.left-0.w-64') ||
                document.querySelector('div.fixed.w-64.bg-white.border-r') ||
                document.querySelector('.fixed.w-64.transform') ||
                document.querySelector(
                  '[class*="fixed"][class*="w-64"][class*="bg-white"]'
                );
              if (mobileMenu && mobileMenu instanceof HTMLElement) {
                mobileMenu.style.removeProperty('height');
                mobileMenu.style.removeProperty('max-height');
              }

              // Remove custom styles
              const globalStyle = document.getElementById(
                'shepherd-global-custom-style'
              );
              if (globalStyle) {
                globalStyle.remove();
              }
            },
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
          title: 'Spin up compute in seconds',
          text: `
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
            element: function () {
              // Target the jobs link with the correct href
              const jobsLink = document.querySelector(
                'a[href="/dashboard/jobs"]'
              );
              if (jobsLink) {
                return jobsLink;
              }
              // Fallback to original selector
              return (
                document.querySelector('a[href="/dashboard/jobs"]') ||
                'a[href="/dashboard/jobs"]'
              );
            },
            on: 'bottom',
            offset: { skidding: 0, distance: 10 },
          },
          beforeShowPromise: function () {
            return new Promise((resolve) => {
              const setupJobsStep = () => {
                // Check if we're on mobile by looking for hamburger menu
                const isMobile = window.innerWidth < 768; // Tailwind md breakpoint
                const hamburgerButton = document.querySelector(
                  '[data-testid="mobile-menu-button"], button[aria-label*="menu"], button[aria-label*="Menu"], .mobile-menu-button, [role="button"][aria-expanded]'
                );

                if (isMobile && hamburgerButton) {
                  // Check if menu is already expanded
                  const isExpanded =
                    hamburgerButton.getAttribute('aria-expanded') === 'true' ||
                    hamburgerButton.classList.contains('open') ||
                    document.querySelector(
                      'nav[data-state="open"], .mobile-menu.open, [data-mobile-menu="true"]'
                    );

                  if (!isExpanded) {
                    // Click hamburger to expand menu
                    if (hamburgerButton instanceof HTMLElement) {
                      hamburgerButton.click();
                    }
                    // Wait for menu animation to complete
                    setTimeout(() => {
                      resolve();
                    }, 300);
                    return;
                  }
                }
                resolve();
              };

              setupJobsStep();
            });
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
          title: 'Bring one or many infrastructure',
          text: `
            <p>SkyPilot combines your Kubernetes clusters, VMs (17+ supported clouds), or SSH nodes into a unified pool.</p>
            <p>You can monitor them in this page.</p>
          `,
          attachTo: {
            element: function () {
              // Target the config link with the correct href
              const configLink = document.querySelector(
                'a[href="/dashboard/infra"]'
              );
              if (configLink) {
                return configLink;
              }
              // Fallback to original selector
              return (
                document.querySelector('a[href="/dashboard/infra"]') ||
                'a[href="/dashboard/infra"]'
              );
            },
            on: 'bottom',
            offset: { skidding: 0, distance: 10 },
          },
          beforeShowPromise: function () {
            return new Promise((resolve) => {
              const setupInfraStep = () => {
                // Check if we're on mobile by looking for hamburger menu
                const isMobile = window.innerWidth < 768; // Tailwind md breakpoint
                const hamburgerButton = document.querySelector(
                  '[data-testid="mobile-menu-button"], button[aria-label*="menu"], button[aria-label*="Menu"], .mobile-menu-button, [role="button"][aria-expanded]'
                );

                if (isMobile && hamburgerButton) {
                  // Check if menu is already expanded
                  const isExpanded =
                    hamburgerButton.getAttribute('aria-expanded') === 'true' ||
                    hamburgerButton.classList.contains('open') ||
                    document.querySelector(
                      'nav[data-state="open"], .mobile-menu.open, [data-mobile-menu="true"]'
                    );

                  if (!isExpanded) {
                    // Click hamburger to expand menu
                    if (hamburgerButton instanceof HTMLElement) {
                      hamburgerButton.click();
                    }
                    // Wait for menu animation to complete
                    setTimeout(() => {
                      resolve();
                    }, 300);
                    return;
                  }
                }
                resolve();
              };

              setupInfraStep();
            });
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
            <p>Admins can use Workspaces to isolate teams or projects.</p>
          `,
          attachTo: {
            element: 'a[href="/dashboard/workspaces"]',
            on: 'bottom',
            offset: { skidding: 0, distance: 10 },
          },
          beforeShowPromise: function () {
            return new Promise((resolve) => {
              const setupWorkspacesStep = () => {
                // Check if we're on mobile by looking for hamburger menu
                const isMobile = window.innerWidth < 768; // Tailwind md breakpoint
                const hamburgerButton = document.querySelector(
                  '[data-testid="mobile-menu-button"], button[aria-label*="menu"], button[aria-label*="Menu"], .mobile-menu-button, [role="button"][aria-expanded]'
                );

                if (isMobile && hamburgerButton) {
                  // Check if menu is already expanded
                  const isExpanded =
                    hamburgerButton.getAttribute('aria-expanded') === 'true' ||
                    hamburgerButton.classList.contains('open') ||
                    document.querySelector(
                      'nav[data-state="open"], .mobile-menu.open, [data-mobile-menu="true"]'
                    );

                  if (!isExpanded) {
                    // Click hamburger to expand menu
                    if (hamburgerButton instanceof HTMLElement) {
                      hamburgerButton.click();
                    }
                    // Wait for menu animation to complete
                    setTimeout(() => {
                      resolve();
                    }, 300);
                    return;
                  }
                }
                resolve();
              };

              setupWorkspacesStep();
            });
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
          title: 'Users',
          text: `
            <p>SkyPilot provides user management with RBAC and SSO support. Admins can manage users in this page.</p>
          `,
          attachTo: {
            element: 'a[href="/dashboard/users"]',
            on: 'bottom',
            offset: { skidding: 0, distance: 10 },
          },
          beforeShowPromise: function () {
            return new Promise((resolve) => {
              const setupUsersStep = () => {
                // Check if we're on mobile by looking for hamburger menu
                const isMobile = window.innerWidth < 768; // Tailwind md breakpoint
                const hamburgerButton = document.querySelector(
                  '[data-testid="mobile-menu-button"], button[aria-label*="menu"], button[aria-label*="Menu"], .mobile-menu-button, [role="button"][aria-expanded]'
                );

                if (isMobile && hamburgerButton) {
                  // Check if menu is already expanded
                  const isExpanded =
                    hamburgerButton.getAttribute('aria-expanded') === 'true' ||
                    hamburgerButton.classList.contains('open') ||
                    document.querySelector(
                      'nav[data-state="open"], .mobile-menu.open, [data-mobile-menu="true"]'
                    );

                  if (!isExpanded) {
                    // Click hamburger to expand menu
                    if (hamburgerButton instanceof HTMLElement) {
                      hamburgerButton.click();
                    }
                    // Wait for menu animation to complete
                    setTimeout(() => {
                      resolve();
                    }, 300);
                    return;
                  }
                }
                resolve();
              };

              setupUsersStep();
            });
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
          title: '🎉 Happy SkyPilot!',
          text: `
              <p>We invite you to to click around and explore the dashboard.</p>
              <p>To get started, refer to <a href="https://docs.skypilot.co/en/latest/getting-started/quickstart.html">Quickstart</a> docs and join the <a href="https://skypilot.slack.com">SkyPilot Slack</a> to chat with the community.</p>
              <p>Restart the tour by clicking the <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block h-4 w-4 align-middle"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><path d="M12 17h.01"></path></svg> icon in the bottom right corner.</p>
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

    if (isFirstVisit && !tourAutoStarted) {
      startTour();
      setTourAutoStarted(true);
    }

    return () => {
      // Cleanup tour on unmount
      if (tourRef.current) {
        tourRef.current.complete();
      }
    };
  }, [isFirstVisit, markTourCompleted, tourAutoStarted]);

  const completeTour = () => {
    if (tourRef.current) {
      tourRef.current.complete();
    }
  };

  const value = {
    startTour,
    completeTour,
    tour: tourRef.current,
  };

  return <TourContext.Provider value={value}>{children}</TourContext.Provider>;
}
