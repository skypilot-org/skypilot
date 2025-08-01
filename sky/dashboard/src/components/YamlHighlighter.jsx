import React, { useEffect, useRef } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { solarizedlight } from 'react-syntax-highlighter/dist/cjs/styles/prism';

// Process YAML for highlighting (adds markers for syntax highlighting)
const processYamlForHighlighting = (yamlContent) => {
  if (!yamlContent || typeof yamlContent !== 'string') {
    return yamlContent;
  }

  const lines = yamlContent.split('\n');
  const result = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // If this is a completely empty line, check if we're inside a multiline block
    if (trimmed === '') {
      // Look backward to see if we're in a multiline string
      let inMultilineBlock = false;
      let expectedIndent = 0;

      // Look for the most recent multiline block start
      for (let j = i - 1; j >= 0; j--) {
        const prevLine = lines[j];
        const prevTrimmed = prevLine.trim();

        // Found a multiline block indicator
        if (prevTrimmed && prevLine.match(/:\s*[|>]\s*$/)) {
          inMultilineBlock = true;
          // Find the expected indent by looking at the next non-empty line after the |/>
          for (let k = j + 1; k < lines.length; k++) {
            const nextLine = lines[k];
            if (nextLine.trim()) {
              expectedIndent = nextLine.search(/\S/);
              break;
            }
          }
          break;
        }

        // If we hit a line that's not indented properly, we're out of the block
        if (prevTrimmed && prevLine.search(/\S/) === 0) {
          break;
        }
      }

      // If we're in a multiline block, look forward to confirm
      if (inMultilineBlock) {
        for (let k = i + 1; k < lines.length; k++) {
          const nextLine = lines[k];
          if (nextLine.trim()) {
            const nextIndent = nextLine.search(/\S/);
            if (nextIndent >= expectedIndent) {
              // Still in the block, add marker for highlighting (will be hidden)
              result.push('  #YAML_BLANK_LINE_MARKER#');
            } else {
              // Out of the block
              result.push(line);
            }
            break;
          }
        }
      } else {
        result.push(line);
      }
    } else {
      result.push(line);
    }
  }

  return result.join('\n');
};

export function YamlHighlighter({ children, className = '' }) {
  const containerRef = useRef(null);
  const processedContent = processYamlForHighlighting(children);

  // Post-process the rendered HTML to hide our markers
  useEffect(() => {
    if (containerRef.current) {
      const container = containerRef.current;

      // Use setTimeout to ensure the highlighting has been applied
      setTimeout(() => {
        // Find and hide all our marker comments
        const walker = document.createTreeWalker(
          container,
          NodeFilter.SHOW_TEXT,
          null
        );

        let node;
        while ((node = walker.nextNode())) {
          if (node.textContent && node.textContent.includes('#YAML_BLANK_LINE_MARKER#')) {
            // Replace the marker with empty string to hide it
            node.textContent = node.textContent.replace(/#YAML_BLANK_LINE_MARKER#/g, '');
          }
        }
      }, 0);
    }
  }, [processedContent]);

  const enhancedStyle = {
    ...solarizedlight,
    // Ensure whitespace is preserved
    'code[class*="language-"]': {
      ...solarizedlight['code[class*="language-"]'],
      whiteSpace: 'pre !important',
    },
    'pre[class*="language-"]': {
      ...solarizedlight['pre[class*="language-"]'],
      whiteSpace: 'pre !important',
    },
  };

  return (
    <div ref={containerRef} className={className}>
      <SyntaxHighlighter
        language="yaml"
        style={enhancedStyle}
        customStyle={{
          margin: 0,
          padding: 0,
          background: 'transparent',
          fontSize: '0.875rem',
          fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono", Menlo, monospace',
          whiteSpace: 'pre',
        }}
        wrapLines={true}
        wrapLongLines={true}
        showLineNumbers={false}
        useInlineStyles={true}
      >
        {processedContent}
      </SyntaxHighlighter>
    </div>
  );
}
