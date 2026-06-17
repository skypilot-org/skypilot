'use client';

import React from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { Prec } from '@codemirror/state';
import { yaml } from '@codemirror/lang-yaml';
import { syntaxHighlighting } from '@codemirror/language';
import { getNonce } from '@/utils/csp';
import { yamlHighlightStyle } from './yaml-editor';

const editorTheme = EditorView.theme({
  '&': {
    height: '100%',
    backgroundColor: '#f9fafb',
  },
  '.cm-scroller': {
    minHeight: '100%',
    overflow: 'auto',
    backgroundColor: '#f9fafb',
  },
  '.cm-content': {
    minHeight: '100%',
    backgroundColor: '#f9fafb',
    padding: '8px 0',
  },
  '.cm-gutters': {
    backgroundColor: '#f3f4f6',
    borderRight: '1px solid #e5e7eb',
    color: '#9ca3af',
    minHeight: '100%',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 12px 0 8px',
    minWidth: '2.5em',
  },
  '&.cm-focused': {
    outline: 'none',
  },
});

export function YamlCodeBlock({
  value,
  onChange,
  height,
  maxHeight = '400px',
  readOnly = false,
  className,
}) {
  const fixed = !!height;
  return (
    <div
      className={`rounded-md border border-gray-200 overflow-hidden ${fixed ? 'flex flex-col' : ''} ${className || ''}`}
      style={{
        height: fixed ? height : undefined,
        maxHeight: fixed ? undefined : maxHeight,
        width: '100%',
        minWidth: 0,
      }}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={[
          yaml(),
          editorTheme,
          Prec.highest(syntaxHighlighting(yamlHighlightStyle)),
          ...(getNonce() ? [EditorView.cspNonce.of(getNonce())] : []),
        ]}
        readOnly={readOnly}
        height={fixed ? '100%' : undefined}
        maxHeight={fixed ? undefined : maxHeight}
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
          ...(fixed ? { flex: 1, minHeight: 0 } : {}),
        }}
        theme="light"
      />
    </div>
  );
}

export default YamlCodeBlock;
