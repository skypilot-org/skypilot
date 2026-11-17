import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

// The code editor does not render in jsdom; show its value as text instead.
jest.mock('@/components/ui/yaml-code-block', () => ({
  __esModule: true,
  YamlCodeBlock: ({ value }) => <pre data-testid="yaml">{value}</pre>,
}));

import { EntrypointAndYaml } from './EntrypointYaml';

describe('EntrypointAndYaml', () => {
  it('renders nothing without an entrypoint or YAML', () => {
    const { container } = render(<EntrypointAndYaml />);
    expect(container).toBeEmptyDOMElement();
  });

  it('hides an empty {} YAML', () => {
    render(<EntrypointAndYaml entrypoint="sky exec c x" taskYaml="{}" />);
    expect(screen.getByText('sky exec c x')).toBeInTheDocument();
    expect(screen.queryByText('Show SkyPilot YAML')).toBeNull();
  });

  it('shows the YAML only once expanded, as written', () => {
    render(
      <EntrypointAndYaml
        entrypoint="sky exec c --env LR=1e-3 train.yaml"
        taskYaml={'envs:\n  LR: 1e-3\nrun: echo hi\n'}
      />
    );
    expect(screen.queryByTestId('yaml')).toBeNull();

    fireEvent.click(screen.getByText('Show SkyPilot YAML'));

    expect(screen.getByTestId('yaml')).toHaveTextContent('LR: 1e-3');
  });
});
