import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { solarizedlight } from 'react-syntax-highlighter/dist/cjs/styles/prism';

export function YamlHighlighter({ children, className = '' }) {
  return (
    <SyntaxHighlighter
      language="yaml"
      style={solarizedlight}
      customStyle={{
        margin: 0,
        padding: 0,
        background: 'transparent',
        fontSize: '0.875rem', // text-sm equivalent
        fontFamily:
          'ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono", Menlo, monospace',
      }}
      className={className}
      wrapLines={true}
      wrapLongLines={true}
    >
      {children}
    </SyntaxHighlighter>
  );
}
