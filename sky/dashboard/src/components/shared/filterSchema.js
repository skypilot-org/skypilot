/**
 * Schema-driven encoding of filter chips as named URL query parameters.
 *
 * A page declares its filterable properties once:
 *
 *   [{ key: 'status', label: 'Status', kind: 'enum', multi: true }, ...]
 *
 * An entry may also carry `legacyKeys: ['old spelling']` for a property whose
 * old `property=` value was neither its key nor its lowercased label.
 *
 * and that one declaration drives the dropdown, the URL, and the chip bar, so
 * the key a page writes is by construction the key it can read back.
 *
 * The in-memory chip shape is unchanged -- `{ property, operator, value }`
 * where `property` is the schema entry's label -- because `evaluateCondition`,
 * `filterData` and the table connectors all consume it. Only the URL form
 * changes here.
 */

// The legacy encoding: three parallel arrays zipped by index. Kept readable so
// links already pasted into docs and chat keep working.
const LEGACY_KEYS = ['property', 'operator', 'value'];

const DEFAULT_OPERATOR = ':';

/** Look up a schema entry by its URL key. */
export const entryByKey = (schema, key) =>
  schema.find((entry) => entry.key === key);

/** Look up a schema entry by the label carried on a chip. */
export const entryByLabel = (schema, label) =>
  schema.find((entry) => entry.label === label);

/**
 * Turn chips into `{ key: value }` pairs.
 *
 * - `enum` with `multi` joins its values with a comma: several values on one
 *   key mean "match any of these".
 * - `kv` (labels) repeats the key instead, because a label value may itself
 *   contain a comma.
 * - Everything else is single-valued; a page replaces rather than stacks a
 *   text chip, so a second value here means the caller changed the filter.
 */
export const filtersToQuery = (schema, filters) => {
  const query = {};
  for (const entry of schema) {
    const values = (filters || [])
      .filter((f) => f.property === entry.label)
      .map((f) => f.value)
      .filter((v) => v !== undefined && v !== null && v !== '');
    if (values.length === 0) {
      continue;
    }
    if (entry.kind === 'kv' || entry.multi === 'repeat') {
      query[entry.key] = values;
    } else if (entry.kind === 'enum' && entry.multi) {
      query[entry.key] = values.join(',');
    } else {
      query[entry.key] = values[values.length - 1];
    }
  }
  return query;
};

/** Turn `{ key: value }` pairs back into chips, dropping anything unknown. */
export const queryToFilters = (schema, query) => {
  const filters = [];
  for (const entry of schema) {
    const raw = query?.[entry.key];
    if (raw === undefined || raw === null || raw === '') {
      continue;
    }
    let values;
    if (Array.isArray(raw)) {
      values = raw;
    } else if (entry.kind === 'enum' && entry.multi) {
      values = String(raw).split(',');
    } else {
      values = [String(raw)];
    }
    for (const value of values) {
      const trimmed = String(value).trim();
      if (!trimmed) {
        continue;
      }
      filters.push({
        property: entry.label,
        operator: DEFAULT_OPERATOR,
        value: trimmed,
      });
    }
  }
  return filters;
};

/** True when a URL still carries the legacy triple arrays. */
export const hasLegacyFilters = (query) =>
  query?.property !== undefined && query.property !== '';

/**
 * Translate legacy triples into named params.
 *
 * The operator is discarded: no UI could ever set it to anything but ':'.
 * A property the schema does not know about is dropped rather than carried as
 * an undefined one -- the same rule `updateFiltersByURLParams` applies.
 */
export const legacyFiltersToQuery = (schema, query) => {
  if (!hasLegacyFilters(query)) {
    return {};
  }
  const raw = query.property;
  const properties = Array.isArray(raw) ? raw : [raw];
  const rawValues = query.value;
  const values = Array.isArray(rawValues) ? rawValues : [rawValues];

  const filters = [];
  properties.forEach((property, i) => {
    // Legacy URLs carry the lowercased label, which for most pages equals the
    // schema key. Where it does not -- the users page wrote `gpu type` for a
    // property now keyed `gpu` -- the entry declares the old spelling in
    // `legacyKeys`. Match all three so links already pasted somewhere survive.
    const lower = String(property ?? '').toLowerCase();
    const entry =
      entryByKey(schema, lower) ||
      schema.find((e) => e.label.toLowerCase() === lower) ||
      schema.find((e) =>
        (e.legacyKeys || []).some((k) => k.toLowerCase() === lower)
      );
    const value = properties.length === 1 ? values[0] : values[i];
    if (!entry || value === undefined || value === null || value === '') {
      return;
    }
    filters.push({
      property: entry.label,
      operator: DEFAULT_OPERATOR,
      value: String(value),
    });
  });
  return filtersToQuery(schema, filters);
};

/**
 * Serialize `{ key: value }` pairs to a query string.
 *
 * Built by hand rather than with URLSearchParams so a comma stays a comma:
 * `?status=RUNNING,STOPPED` is the point of the exercise, and
 * `URLSearchParams` would percent-encode it to `%2C`. Everything else is
 * encoded normally, so values containing `&`, `=` or spaces round-trip.
 */
export const buildQueryString = (query) => {
  const parts = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') {
      continue;
    }
    const values = Array.isArray(value) ? value : [value];
    for (const v of values) {
      if (v === undefined || v === null || v === '') {
        continue;
      }
      const encoded = String(v)
        .split(',')
        .map((part) => encodeURIComponent(part))
        .join(',');
      parts.push(`${encodeURIComponent(key)}=${encoded}`);
    }
  }
  return parts.length ? `?${parts.join('&')}` : '';
};

/**
 * Parse a `location.search` string into the `{ key: value }` shape the helpers
 * above use, collapsing a repeated key into an array.
 */
export const parseSearch = (search) => {
  const params = new URLSearchParams(search);
  const query = {};
  for (const key of new Set(params.keys())) {
    const all = params.getAll(key);
    query[key] = all.length > 1 ? all : all[0];
  }
  return query;
};

/**
 * Build an href for a same-page navigation that changes one query key, keeping
 * every other key the address bar already carries.
 *
 * Pages whose filters live in the URL write them straight to history, so
 * `router.query` can lag behind and rebuilding the query from it drops them.
 * Reading `location.search` avoids that, and going back out through
 * `buildQueryString` keeps a comma list readable rather than percent-encoding
 * it the way `URLSearchParams.toString()` would.
 */
export const hrefWithQueryKey = (pathname, search, key, value) => {
  const query = parseSearch(search);
  if (value === undefined || value === null || value === '') {
    delete query[key];
  } else {
    query[key] = value;
  }
  return `${pathname}${buildQueryString(query)}`;
};

/** Strip the legacy triple keys from a query object. */
export const withoutLegacyKeys = (query) => {
  const next = { ...query };
  for (const key of LEGACY_KEYS) {
    delete next[key];
  }
  return next;
};
