export const SKY_SERVE_CONTROLLER_PREFIX = 'sky-serve-controller-';
export const JOB_CONTROLLER_PREFIX = 'sky-jobs-controller-';

export function isController(name) {
  return (
    name.startsWith(SKY_SERVE_CONTROLLER_PREFIX) ||
    name.startsWith(JOB_CONTROLLER_PREFIX)
  );
}

export function isSkyServeController(name) {
  return name.startsWith(SKY_SERVE_CONTROLLER_PREFIX);
}

export function isJobController(name) {
  return name.startsWith(JOB_CONTROLLER_PREFIX);
}

export function sortData(data, accessor, direction) {
  // if accessor is null return the data
  if (accessor === null) {
    return data;
  }
  return [...data].sort((a, b) => {
    if (a[accessor] < b[accessor]) return direction === 'ascending' ? -1 : 1;
    if (a[accessor] > b[accessor]) return direction === 'ascending' ? 1 : -1;
    return 0;
  });
}

export function parseResources(resources_str) {
  if (!resources_str) {
    return {
      parsed_resources: {
        infra: '-',
        instance_type: '-',
        cpus: null,
        mem: null,
        gpus: {},
        num_nodes: 0,
      },
    };
  }

  // Split the resources string to get the number of nodes and the rest
  const parts = resources_str.split('x ');
  const num_nodes = parts[0].trim().match(/\d+/) ? parseInt(parts[0]) : 1;
  const resources = parts[1] || '';

  const parsed_resources = parseCloudResources(resources);
  if (!parsed_resources) {
    return {
      parsed_resources: {
        infra: '-',
        instance_type: '-',
        cpus: null,
        mem: null,
        gpus: {},
        num_nodes: 0,
      },
    };
  }

  const { infra: infra, resourceList: all_resources } = parsed_resources;
  if (all_resources.length <= 0) {
    return {
      parsed_resources: {
        infra: infra,
        instance_type: '-',
        cpus: null,
        mem: null,
        gpus: {},
        num_nodes: 0,
      },
    };
  }

  const instance_type = all_resources[0];
  let cpus = null;
  let mem = null;
  let gpus = {};
  let other_resources = [];
  for (let i = 1; i < all_resources.length; i++) {
    const resource = all_resources[i];
    const keyValue = resource.split('=');
    if (keyValue.length === 2) {
      const key = keyValue[0].trim();
      const value = keyValue[1].trim();
      if (key === 'cpus') {
        cpus = value;
        other_resources.push(resource);
      } else if (key === 'mem') {
        mem = value;
        other_resources.push(resource);
      }
    } else {
      const gpuMatch = resource.match(/{([^}]+)}/);
      if (gpuMatch) {
        const gpuStr = gpuMatch[1];
        const gpuPairs = gpuStr.split(',').map((pair) => pair.trim());
        gpus = gpuPairs.reduce((acc, pair) => {
          const [key, value] = pair.split(':').map((s) => s.trim());
          if (key && value) {
            try {
              const value_int = parseInt(value);
              if (isNaN(value_int)) {
                console.error('Invalid GPU value:', value);
                return acc;
              }
              acc[key] = value_int;
            } catch (e) {
              console.error('Error parsing GPU value:', e);
            }
          }
          other_resources.push(resource);
          return acc;
        }, {});
      }
    }
  }

  const result = {
    parsed_resources: {
      infra,
      instance_type,
      cpus,
      mem,
      gpus,
      num_nodes,
      other_resources: other_resources.join(','),
    },
  };

  return result;
}

export function parseCloudResources(str) {
  if (!str) {
    return null;
  }

  const match = str.match(/^([^(]+)\(([^)]+)\)$/);

  if (!match) {
    return null;
  }

  const infra = match[1].trim();
  const resourceList = match[2].split(',').map((s) => s.trim());

  return {
    infra,
    resourceList,
  };
}
