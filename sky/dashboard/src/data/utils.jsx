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
  const compare = (valueA, valueB) => {
    if (valueA < valueB) return direction === 'ascending' ? -1 : 1;
    if (valueA > valueB) return direction === 'ascending' ? 1 : -1;
    return 0;
  };
  if (typeof accessor !== 'function') {
    return [...data].sort((a, b) => compare(a[accessor], b[accessor]));
  }
  // A function accessor computes the value to sort a row by: once per row, as
  // it may be a lookup. A value of null means the row has none: it goes last
  // in either direction, so a sparse column does not open on a page of blanks.
  return data
    .map((row) => ({ row, value: accessor(row) }))
    .sort((a, b) => {
      if (a.value == null || b.value == null) {
        return (a.value == null) - (b.value == null);
      }
      return compare(a.value, b.value);
    })
    .map(({ row }) => row);
}

/**
 * Extracts error message from API response, handling nested JSON parsing
 * @param {Response} fetchedData - The API response object
 * @returns {Promise<string>} The extracted error message
 */
export async function getErrorMessageFromResponse(fetchedData) {
  let errorMessage = fetchedData.statusText;

  if (fetchedData.status === 500) {
    try {
      const data = await fetchedData.json();
      if (data.detail && data.detail.error) {
        try {
          const error = JSON.parse(data.detail.error);
          errorMessage = error.message || String(data.detail.error);
        } catch (jsonError) {
          console.error(
            'Error parsing JSON from data.detail.error:',
            jsonError
          );
          errorMessage = String(data.detail.error);
        }
      }
    } catch (parseError) {
      console.error('Error parsing response JSON:', parseError);
      errorMessage = String(parseError);
    }
  }

  return errorMessage;
}
