import React from 'react';
import { render, screen, act } from '@testing-library/react';

// Make next/dynamic a pass-through in tests: resolve the loader synchronously
// via a require()-based trick so the wrapped component renders immediately.
jest.mock('next/dynamic', () => (loader) => {
  // Capture the resolved module synchronously by abusing the fact that Jest
  // module mocks are evaluated in a synchronous context and the loader's
  // import() will resolve to the already-mocked module.
  let Resolved = null;
  // We can't await here, but the mock for @/components/infra is synchronous,
  // so we store the promise and return a wrapper that reads it.
  const promise = loader().then((mod) => {
    Resolved = mod;
  });
  const Dynamic = (props) => {
    const [Comp, setComp] = React.useState(() => Resolved);
    React.useEffect(() => {
      if (!Comp) {
        promise.then(() => setComp(() => Resolved));
      }
    }, []); // eslint-disable-line react-hooks/exhaustive-deps
    if (!Comp) return null;
    return React.createElement(Comp, props);
  };
  Dynamic.displayName = 'DynamicComponent';
  return Dynamic;
});

// Mock the heavy GPUs component
jest.mock('@/components/infra', () => ({
  GPUs: () => <div data-testid="gpus-component">GPUs</div>,
}));

let infraPage;
beforeAll(async () => {
  // Defer require until after window mock setup
  infraPage = (await import('../infra')).default;
});

beforeEach(() => {
  delete window.__skyDashboardPluginsLoaded;
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('InfraPage plugin-loaded gate', () => {
  it('renders GPUs immediately when plugins already loaded', async () => {
    window.__skyDashboardPluginsLoaded = true;
    const { findByTestId } = render(React.createElement(infraPage));
    expect(await findByTestId('gpus-component')).toBeInTheDocument();
  });

  it('shows loading spinner until the plugins-loaded event fires', () => {
    render(React.createElement(infraPage));
    expect(screen.queryByTestId('gpus-component')).not.toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new Event('skydashboard:plugins-loaded'));
    });
    expect(screen.getByTestId('gpus-component')).toBeInTheDocument();
  });

  it('falls through after 2s if the plugins-loaded event never fires', () => {
    render(React.createElement(infraPage));
    expect(screen.queryByTestId('gpus-component')).not.toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(screen.getByTestId('gpus-component')).toBeInTheDocument();
  });
});
