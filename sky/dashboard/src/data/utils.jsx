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
