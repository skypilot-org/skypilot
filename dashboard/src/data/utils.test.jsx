import { expect, describe, test } from '@jest/globals';
import { parseResources, parseCloudResources } from './utils';

describe('parseResources', () => {
  test('should parse resources with all fields present', () => {
    const resourcesStr =
      '3x AWS(t2.medium, cpus=2, mem=16, {V100:1}, disk_size=500)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 3,
        infra: 'AWS',
        instance_type: 't2.medium',
        cpus: '2',
        mem: '16',
        gpus: { V100: 1 },
        other_resources: 'cpus=2,mem=16,{V100:1}',
      },
    });
  });

  test('should handle float CPU values', () => {
    const resourcesStr = 'x aws (t2.medium, cpus=0.5x, mem=16x, {V100:1})';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 1,
        infra: 'aws',
        instance_type: 't2.medium',
        cpus: '0.5x',
        mem: '16x',
        gpus: { V100: 1 },
        other_resources: 'cpus=0.5x,mem=16x,{V100:1}',
      },
    });
  });

  test('should handle missing GPU configuration', () => {
    const resourcesStr = 'x aws (t2.medium, cpus=2, mem=16)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 1,
        infra: 'aws',
        instance_type: 't2.medium',
        cpus: '2',
        mem: '16',
        gpus: {},
        other_resources: 'cpus=2,mem=16',
      },
    });
  });

  test('should handle missing GPU and memory configuration', () => {
    const resourcesStr = 'x aws (t2.medium, cpus=2+)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 1,
        infra: 'aws',
        instance_type: 't2.medium',
        cpus: '2+',
        mem: null,
        gpus: {},
        other_resources: 'cpus=2+',
      },
    });
  });

  test('should handle missing cpu, gpu and memory configuration', () => {
    const resourcesStr = 'x aws (t2.medium)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 1,
        infra: 'aws',
        instance_type: 't2.medium',
        cpus: null,
        mem: null,
        gpus: {},
        other_resources: '',
      },
    });
  });

  test('should handle missing CPU and memory', () => {
    const resourcesStr = 'x aws (t2.medium, {V100:1})';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 1,
        infra: 'aws',
        instance_type: 't2.medium',
        cpus: null,
        mem: null,
        gpus: { V100: 1 },
        other_resources: '{V100:1}',
      },
    });
  });

  test('should handle malformed GPU string', () => {
    const resourcesStr =
      'x aws (t2.medium, cpus=2, mem=16, {invalid:gpu:format})';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 1,
        infra: 'aws',
        instance_type: 't2.medium',
        cpus: '2',
        mem: '16',
        gpus: {},
        other_resources: 'cpus=2,mem=16',
      },
    });
  });

  test('should handle empty resources string', () => {
    const resourcesStr = '';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 0,
        infra: '-',
        instance_type: '-',
        cpus: null,
        mem: null,
        gpus: {},
      },
    });
  });

  test('should handle resources with multiple cloud providers', () => {
    const resourcesStr =
      'x aws[gcp,azure] (t2.medium, cpus=2, mem=16, {V100:1})';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        num_nodes: 1,
        infra: 'aws[gcp,azure]',
        instance_type: 't2.medium',
        cpus: '2',
        mem: '16',
        gpus: { V100: 1 },
        other_resources: 'cpus=2,mem=16,{V100:1}',
      },
    });
  });

  test('should handle case 1: AWS(g4dn.xlarge, cpus=1, mem=2, {T4: 1},disk=100)', () => {
    const resourcesStr = '1x AWS(g4dn.xlarge, cpus=1, mem=2, {T4: 1},disk=100)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        infra: 'AWS',
        instance_type: 'g4dn.xlarge',
        cpus: '1',
        mem: '2',
        gpus: { T4: 1 },
        num_nodes: 1,
        other_resources: 'cpus=1,mem=2,{T4: 1}',
      },
    });
  });

  test('should handle case 2: AWS(g4dn.xlarge, cpus=1, mem=2, {T4: 1})', () => {
    const resourcesStr = '1x AWS(g4dn.xlarge, cpus=1, mem=2, {T4: 1})';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        infra: 'AWS',
        instance_type: 'g4dn.xlarge',
        cpus: '1',
        mem: '2',
        gpus: { T4: 1 },
        num_nodes: 1,
        other_resources: 'cpus=1,mem=2,{T4: 1}',
      },
    });
  });

  test('should handle case 3: AWS(g4dn.xlarge, cpus=1x, mem=2x)', () => {
    const resourcesStr = '1x AWS(g4dn.xlarge, cpus=1x, mem=2x)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        infra: 'AWS',
        instance_type: 'g4dn.xlarge',
        cpus: '1x',
        mem: '2x',
        gpus: {},
        num_nodes: 1,
        other_resources: 'cpus=1x,mem=2x',
      },
    });
  });

  test('should handle case 4: AWS(g4dn.xlarge, cpus=1x)', () => {
    const resourcesStr = '1x AWS(g4dn.xlarge, cpus=1x)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        infra: 'AWS',
        instance_type: 'g4dn.xlarge',
        cpus: '1x',
        mem: null,
        gpus: {},
        num_nodes: 1,
        other_resources: 'cpus=1x',
      },
    });
  });

  test('should handle case 5: AWS(g4dn.xlarge)', () => {
    const resourcesStr = '1x AWS(g4dn.xlarge)';
    const result = parseResources(resourcesStr);

    expect(result).toEqual({
      parsed_resources: {
        infra: 'AWS',
        instance_type: 'g4dn.xlarge',
        cpus: null,
        mem: null,
        gpus: {},
        num_nodes: 1,
        other_resources: '',
      },
    });
  });
});

describe('parseCloudResources', () => {
  test('should parse string with two fields', () => {
    const result = parseCloudResources('ab(cd,ef)');
    expect(result).toEqual({
      infra: 'ab',
      resourceList: ['cd', 'ef'],
    });
  });

  test('should handle empty fields', () => {
    const result = parseCloudResources('()');
    expect(result).toBeNull();
  });

  test('should handle single item in second field', () => {
    const result = parseCloudResources('ab(cd)');
    expect(result).toEqual({
      infra: 'ab',
      resourceList: ['cd'],
    });
  });

  test('should return null for invalid format', () => {
    const result = parseCloudResources('invalid');
    expect(result).toBeNull();
  });
});
