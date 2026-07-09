'use client';

import React from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { Prec } from '@codemirror/state';
import { yaml } from '@codemirror/lang-yaml';
import { syntaxHighlighting } from '@codemirror/language';
import { getNonce } from '@/utils/csp';
import {
  yamlHighlightStyle,
  yamlGutterTheme,
  yamlTextCursorTheme,
} from './yaml-editor';

// In read-only mode, suppress CodeMirror's drawn caret so the viewer
// doesn't look editable when focused. Selection backgrounds still render
// (they're drawn by a separate layer). `!important` is required because
// CodeMirror's base theme has a more-specific
// `&.cm-focused > .cm-scroller > .cm-cursorLayer .cm-cursor { display: block }`
// rule that re-enables the cursor on focus.
const hideCaretTheme = EditorView.theme({
  '.cm-cursor, .cm-cursor-primary': { display: 'none !important' },
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
          yamlGutterTheme,
          ...(readOnly ? [hideCaretTheme] : [yamlTextCursorTheme]),
          Prec.highest(syntaxHighlighting(yamlHighlightStyle)),
          ...(getNonce() ? [EditorView.cspNonce.of(getNonce())] : []),
        ]}
        readOnly={readOnly}
        height={fixed ? '100%' : undefined}
        maxHeight={fixed ? undefined : maxHeight}
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
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
