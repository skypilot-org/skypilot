import React from 'react';
import { render, screen } from '@testing-library/react';
import { PluginPageSlot } from '../PluginPageSlot';

// Mock usePluginComponents — controlled per test via the variable below.
let mockRegistered = [];
jest.mock('../PluginProvider', () => ({
  usePluginComponents: () => mockRegistered,
}));

afterEach(() => {
  mockRegistered = [];
  jest.clearAllMocks();
});

describe('PluginPageSlot', () => {
  it('renders fallback when no plugins are registered', () => {
    mockRegistered = [];
    render(
      <PluginPageSlot name="infra.page" fallback={<div>Fallback content</div>} />
    );
    expect(screen.getByText('Fallback content')).toBeInTheDocument();
  });

  it('renders the registered plugin component instead of fallback', () => {
    const PluginPage = () => <div>Plugin content</div>;
    mockRegistered = [{ id: 'plugin-a', component: PluginPage }];
    render(
      <PluginPageSlot name="infra.page" fallback={<div>Fallback content</div>} />
    );
    expect(screen.getByText('Plugin content')).toBeInTheDocument();
    expect(screen.queryByText('Fallback content')).not.toBeInTheDocument();
  });

  it('renders only the first registered component and warns when more than one is registered', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const First = () => <div>First plugin</div>;
    const Second = () => <div>Second plugin</div>;
    mockRegistered = [
      { id: 'plugin-a', component: First },
      { id: 'plugin-b', component: Second },
    ];
    render(<PluginPageSlot name="infra.page" fallback={<div>Fallback</div>} />);
    expect(screen.getByText('First plugin')).toBeInTheDocument();
    expect(screen.queryByText('Second plugin')).not.toBeInTheDocument();
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('infra.page'),
      expect.anything()
    );
    warn.mockRestore();
  });
});
