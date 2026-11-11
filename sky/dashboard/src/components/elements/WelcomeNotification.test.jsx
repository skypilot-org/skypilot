/**
 * Tests for the first-visit welcome tour prompt.
 *
 * The app tree is built only after plugin registration has settled (the gate
 * in _app.js), with a backstop timeout that opens the gate anyway on slow
 * loads. When a plugin registers an `app.providers` wrapper after the gate
 * opened, PluginWrapperSlot wraps the live tree in a new component type, and
 * React remounts everything below it -- including this component.
 *
 * A first visit must survive that remount: the visited marker is the promise
 * that the prompt was seen, so consuming it on a mount whose show-timer never
 * fires silently swallows the tour, and no later visit can bring it back.
 */

import React from 'react';
import { act, render, renderHook, screen } from '@testing-library/react';
import { WelcomeNotification } from './WelcomeNotification';
import { useFirstVisit } from '@/hooks/useFirstVisit';

let mockTour = null;

jest.mock('@/hooks/useTour', () => ({
  useTour: () => ({ startTour: jest.fn(), tour: mockTour }),
}));

const FIRST_VISIT_KEY = 'skypilot-dashboard-first-visit';
const TOUR_COMPLETED_KEY = 'skypilot-dashboard-tour-completed';

// The delay from mount to prompt; the prompt must appear within the same
// window after any remount that the show-timer had not completed.
const SHOW_DELAY_MS = 2000;

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  mockTour = null;
});

afterEach(() => {
  jest.useRealTimers();
});

const welcomePrompt = () => screen.queryByText('Welcome to SkyPilot!');

test('a first visit shows the welcome prompt', () => {
  render(<WelcomeNotification />);
  expect(welcomePrompt()).toBeNull(); // not before the show delay

  act(() => {
    jest.advanceTimersByTime(SHOW_DELAY_MS);
  });

  expect(welcomePrompt()).toBeInTheDocument();
});

test('the prompt still shows when the tree is remounted before it appeared', () => {
  // Mount the tree from the bootstrap backstop (or any pre-settlement build),
  // then a late app.providers wrapper remounts it before the prompt showed.
  const first = render(<WelcomeNotification />);
  first.unmount();
  render(<WelcomeNotification />);

  act(() => {
    jest.advanceTimersByTime(SHOW_DELAY_MS);
  });

  expect(welcomePrompt()).toBeInTheDocument();
});

test('a reload after the prompt has been shown does not show it again', () => {
  const first = render(<WelcomeNotification />);
  act(() => {
    jest.advanceTimersByTime(SHOW_DELAY_MS);
  });
  expect(welcomePrompt()).toBeInTheDocument();

  first.unmount();
  render(<WelcomeNotification />);
  act(() => {
    jest.advanceTimersByTime(SHOW_DELAY_MS);
  });
  expect(welcomePrompt()).toBeNull();
});

test('a completed tour never prompts again', () => {
  localStorage.setItem(FIRST_VISIT_KEY, 'true');
  localStorage.setItem(TOUR_COMPLETED_KEY, 'true');
  render(<WelcomeNotification />);

  act(() => {
    jest.advanceTimersByTime(SHOW_DELAY_MS);
  });

  expect(welcomePrompt()).toBeNull();
});

test('a visit with a completed tour is not a first visit', () => {
  // TourProvider auto-starts the tour whenever isFirstVisit is true, so a
  // completed tour must suppress it even if the visited marker is missing
  // (the tour can be completed before the prompt ever shows).
  localStorage.setItem(TOUR_COMPLETED_KEY, 'true');
  const { result } = renderHook(() => useFirstVisit());
  expect(result.current.isFirstVisit).toBe(false);
});

test('a first visit with no markers is a first visit', () => {
  const { result } = renderHook(() => useFirstVisit());
  expect(result.current.isFirstVisit).toBe(true);
});
