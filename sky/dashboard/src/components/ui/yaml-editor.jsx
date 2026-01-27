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
  disabled = false,
}) {
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={[yaml()]}
      editable={!disabled}
      className={`rounded-md border border-gray-300 overflow-hidden ${className || ''}`}
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
        overflow: 'auto',
      }}
      theme="light"
    />
  );
}

export default YamlEditor;