/**
 * Enhanced YAML syntax highlighting configuration for Prism.js
 * Fixes issues with multiline strings containing blank lines
 */

import Prism from 'prismjs';

export const configurePrismYaml = () => {
  // Only configure once
  if (typeof window !== 'undefined' && !window.__prismYamlConfigured) {
    // Shared value patterns for reuse
    const valuePatterns = {
      string: [/"(?:[^"\\]|\\.)*"/, /'(?:[^'\\]|\\.)*'/],
      number:
        /\b(?:0x[\da-f]+|0o[0-7]+|(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\b/i,
      boolean: /\b(?:true|false|yes|no|on|off)\b/i,
      null: /\b(?:null|~)\b/i,
    };

    // Create a completely new YAML grammar that handles multiline strings better
    Prism.languages.yaml = {
      // Comments - must come first
      comment: /#.*/,

      // Document separators
      'document-start': /^---[ \t]*$/m,
      'document-end': /^\.\.\.[ \t]*$/m,

      // Multiline literal blocks with comprehensive pattern that includes blank lines
      'literal-multiline': {
        pattern:
          /(^[ \t]*)([a-zA-Z_][\w-]*[ \t]*:[ \t]*)\|[ \t]*\r?\n(?:(?:[ \t]*\r?\n)*(?:[ \t]+[^\r\n]*(?:\r?\n|$)))+/gm,
        lookbehind: true,
        greedy: true,
        alias: 'string',
        inside: {
          key: {
            pattern: /^[a-zA-Z_][\w-]*(?=[ \t]*:)/,
            alias: 'property',
          },
          punctuation: /[:|]/,
          content: {
            pattern: /(?<=\|\s*\n)[\s\S]*/,
            alias: 'string',
          },
        },
      },

      // Multiline folded blocks
      'folded-multiline': {
        pattern:
          /(^[ \t]*)([a-zA-Z_][\w-]*[ \t]*:[ \t]*)>[ \t]*\r?\n(?:(?:[ \t]*\r?\n)*(?:[ \t]+[^\r\n]*(?:\r?\n|$)))+/gm,
        lookbehind: true,
        greedy: true,
        alias: 'string',
        inside: {
          key: {
            pattern: /^[a-zA-Z_][\w-]*(?=[ \t]*:)/,
            alias: 'property',
          },
          punctuation: /[:|>]/,
          content: {
            pattern: /(?<=>\s*\n)[\s\S]*/,
            alias: 'string',
          },
        },
      },

      // Regular key-value pairs
      'key-value': {
        pattern: /(^[ \t]*)([a-zA-Z_][\w-]*)([ \t]*:)[ \t]*(.+)$/gm,
        lookbehind: true,
        inside: {
          key: {
            pattern: /^[a-zA-Z_][\w-]*/,
            alias: 'property',
          },
          punctuation: /:/,
          value: {
            pattern: /[ \t]*(.+)$/,
            inside: valuePatterns,
          },
        },
      },

      // Use shared patterns
      ...valuePatterns,

      // Punctuation
      punctuation: /[{}[\],:]/,

      // Anchors and aliases
      anchor: /[&*][a-zA-Z_][\w-]*/,

      // Tags
      tag: /![a-zA-Z_][\w-]*/,
    };

    window.__prismYamlConfigured = true;
  }
};

// Auto-configure when imported
configurePrismYaml();
