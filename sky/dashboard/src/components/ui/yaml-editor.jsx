'use client';

import React from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { Prec } from '@codemirror/state';
import { yaml } from '@codemirror/lang-yaml';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';
import { getNonce } from '@/utils/csp';

const yamlHighlightStyle = HighlightStyle.define([
  { tag: t.propertyName, color: '#1E62CC' },
  { tag: t.string, color: '#188038' },
  { tag: t.content, color: '#374151' },
  { tag: t.lineComment, color: '#6b7280', fontStyle: 'italic' },
  { tag: t.keyword, color: '#188038' },
  { tag: t.meta, color: '#9ca3af' },
  { tag: t.brace, color: '#6b7280' },
  { tag: t.squareBracket, color: '#6b7280' },
  { tag: t.punctuation, color: '#6b7280' },
]);

const gutterTheme = EditorView.theme({
  '.cm-gutters': {
    backgroundColor: '#f9fafb',
    borderRight: '1px solid #e5e7eb',
    color: '#9ca3af',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 12px 0 8px',
    minWidth: '2.5em',
  },
});

/**
 * YAML Editor component with syntax highlighting.
 * Drop-in replacement for Textarea when editing YAML content.
 */
export function YamlEditor({
  value,
  onChange,
  className,
  height,
  maxHeight = '400px',
  minHeight,
  disabled = false,
}) {
  return (
    <div
      className={`rounded-md border border-gray-300 overflow-hidden flex flex-col ${className || ''}`}
      style={{
        width: '100%',
        maxWidth: '100%',
        minWidth: 0,
        height,
        minHeight,
        maxHeight: height ? undefined : maxHeight,
      }}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={[
          yaml(),
          gutterTheme,
          Prec.highest(syntaxHighlighting(yamlHighlightStyle)),
          // Pass CSP nonce so CodeMirror's injected <style> tags are allowed.
          ...(getNonce() ? [EditorView.cspNonce.of(getNonce())] : []),
        ]}
        editable={!disabled}
        height={height ? '100%' : undefined}
        minHeight={minHeight}
        maxHeight={height ? undefined : maxHeight}
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLineGutter: false,
          highlightActiveLine: false,
          indentOnInput: true,
          bracketMatching: true,
          autocompletion: false,
        }}
        style={{ fontSize: '13px', flex: 1, minHeight: 0 }}
        theme="light"
      />
    </div>
  );
}

export default YamlEditor;
