'use client';

import React from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { yaml } from '@codemirror/lang-yaml';

/**
 * YAML Editor component with syntax highlighting.
 * Drop-in replacement for Textarea when editing YAML content.
 */
export function YamlEditor({
  value,
  onChange,
  className,
  maxHeight = '400px',
  minHeight,
  disabled = false,
}) {
  return (
    <div
      className={`rounded-md border border-gray-300 overflow-hidden ${className || ''}`}
      style={{
        width: '100%',
        maxWidth: '100%',
        minWidth: 0,
      }}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={[yaml()]}
        editable={!disabled}
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLineGutter: false,
          highlightActiveLine: false,
          indentOnInput: true,
          bracketMatching: true,
          autocompletion: false,
        }}
        style={{
          fontSize: '13px',
          maxHeight,
          minHeight,
          overflow: 'auto',
        }}
        theme="light"
      />
    </div>
  );
}

export default YamlEditor;
