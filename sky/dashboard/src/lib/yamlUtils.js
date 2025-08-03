import yaml from 'js-yaml';

// Common YAML dump configuration
const YAML_DUMP_OPTIONS = {
  lineWidth: -1, // Disable line wrapping
  quotingType: "'", // Use single quotes for strings that need quoting
  forceQuotes: false, // Only quote when necessary
  noRefs: true, // Disable YAML references
  sortKeys: false, // Preserve original key order
  condenseFlow: false, // Don't condense flow style
  indent: 2, // Use 2 spaces for indentation
  // Let js-yaml automatically choose the best style for multi-line strings
  // This avoids syntax highlighting issues with blank lines in literal blocks
};

/**
 * Formats YAML string for better display
 * @param {string} yamlString - The YAML string to format
 * @returns {string} - Formatted YAML string
 */
export const formatYaml = (yamlString) => {
  if (!yamlString) return 'No YAML available';

  try {
    // Parse the YAML string into an object
    const parsed = yaml.load(yamlString);

    // Re-serialize with better handling for multi-line strings
    const formatted = yaml.dump(parsed, YAML_DUMP_OPTIONS);

    // Add blank lines between top-level sections for better readability
    const lines = formatted.split('\n');
    const result = [];
    let prevIndent = -1;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const currentIndent = line.search(/\S/); // Find first non-whitespace

      // Add blank line before new top-level sections (indent = 0)
      if (currentIndent === 0 && prevIndent >= 0 && i > 0) {
        result.push('');
      }

      result.push(line);
      prevIndent = currentIndent;
    }

    return result.join('\n').trim();
  } catch (e) {
    console.error('YAML formatting error:', e);
    // If parsing fails, return the original string
    return yamlString;
  }
};

/**
 * Helper function to get a preview of the YAML content for job documents
 * @param {object} parsed - The parsed YAML object
 * @returns {string} - A brief preview of the YAML content
 */
export const getYamlPreview = (parsed) => {
  if (typeof parsed === 'string') {
    return parsed.substring(0, 50) + '...';
  }
  if (parsed && parsed.name) {
    return `name: ${parsed.name}`;
  }
  if (parsed && parsed.resources) {
    return 'Task configuration';
  }
  return 'YAML document';
};

/**
 * Formats a single YAML document for display
 * @param {string} doc - The YAML document string to format
 * @param {number} index - The index of the document
 * @returns {object} - Formatted document object with content and preview
 */
export const formatSingleYamlDocument = (doc, index) => {
  try {
    // Parse the YAML string into an object
    const parsed = yaml.load(doc);

    // Re-serialize with better handling for multi-line strings
    const formatted = yaml.dump(parsed, YAML_DUMP_OPTIONS);

    // Add blank lines between top-level sections for better readability
    const lines = formatted.split('\n');
    const result = [];
    let prevIndent = -1;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const currentIndent = line.search(/\S/); // Find first non-whitespace

      // Add blank line before new top-level sections (indent = 0)
      if (currentIndent === 0 && prevIndent >= 0 && i > 0) {
        result.push('');
      }

      result.push(line);
      prevIndent = currentIndent;
    }

    return {
      index: index,
      content: result.join('\n').trim(),
      preview: getYamlPreview(parsed),
    };
  } catch (e) {
    console.error(`YAML formatting error for document ${index}:`, e);
    // If parsing fails, return the original string
    return {
      index: index,
      content: doc,
      preview: 'Invalid YAML',
    };
  }
};

/**
 * Formats YAML string for job display, handling multiple documents
 * @param {string} yamlString - The YAML string to format
 * @returns {Array} - Array of formatted document objects
 */
export const formatJobYaml = (yamlString) => {
  if (!yamlString) return [];

  try {
    // Split the YAML into multiple documents
    const documents = [];
    const parts = yamlString.split(/^---$/m);

    for (let i = 0; i < parts.length; i++) {
      const part = parts[i].trim();
      if (part && part !== '') {
        documents.push(part);
      }
    }

    // Skip the first document (which is typically just the task name)
    const docsToFormat = documents.length > 1 ? documents.slice(1) : documents;

    // Format each document
    const formattedDocs = docsToFormat.map((doc, index) =>
      formatSingleYamlDocument(doc, index)
    );

    return formattedDocs;
  } catch (e) {
    console.error('YAML formatting error:', e);
    // If parsing fails, return the original string as single document
    return [
      {
        index: 0,
        content: yamlString,
        preview: 'Invalid YAML',
      },
    ];
  }
};
