import {
  buildJobGroupHeader,
  formatYaml,
  formatSingleYamlDocument,
} from '@/lib/yamlUtils';

// YAML as the server writes it with PyYAML: strings that are not floats in
// YAML 1.1 (no dot) are left unquoted.
const SERVER_YAML = [
  'envs:',
  '  LR: 1e-3',
  '  WD: 3e-4',
  '  RATIO: 0.5',
  'resources:',
  '  cpus: 2',
  'run: echo hi',
  '',
].join('\n');

describe('formatYaml', () => {
  it('keeps YAML 1.1 strings such as 1e-3 as written', () => {
    const formatted = formatYaml(SERVER_YAML);
    expect(formatted).toContain('LR: 1e-3');
    expect(formatted).toContain('WD: 3e-4');
    expect(formatted).not.toContain('0.001');
  });

  it('still formats real numbers', () => {
    const formatted = formatYaml(SERVER_YAML);
    expect(formatted).toContain('RATIO: 0.5');
    expect(formatted).toContain('cpus: 2');
  });

  it('applies the same rule to multi-document YAML', () => {
    const { content } = formatSingleYamlDocument(SERVER_YAML, 0);
    expect(content).toContain('LR: 1e-3');
  });
});

describe('buildJobGroupHeader', () => {
  it('lists name and execution, then a document separator', () => {
    expect(buildJobGroupHeader({ name: 'grp', execution: 'parallel' })).toBe(
      'name: grp\nexecution: parallel\n---\n'
    );
    expect(buildJobGroupHeader({ name: 'grp' })).toBe('name: grp\n---\n');
  });

  it('is empty without either', () => {
    expect(buildJobGroupHeader({})).toBe('');
    expect(buildJobGroupHeader(null)).toBe('');
  });
});
