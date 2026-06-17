'use client';

import React from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView, GutterMarker, gutterLineClass } from '@codemirror/view';
import { Prec, RangeSet } from '@codemirror/state';
import { yaml } from '@codemirror/lang-yaml';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';
import { getNonce } from '@/utils/csp';

export const yamlHighlightStyle = HighlightStyle.define([
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

class SelectedLineGutterMarker extends GutterMarker {}
SelectedLineGutterMarker.prototype.elementClass = 'cm-selectedLineGutter';
const selectedLineGutterMarker = new SelectedLineGutterMarker();

export const selectionGutterHighlighter = gutterLineClass.compute(
  ['selection'],
  (state) => {
    const marks = [];
    let lastMarked = -1;
    for (const range of state.selection.ranges) {
      if (range.empty) continue;
      const fromLine = state.doc.lineAt(range.from).number;
      let toLine = state.doc.lineAt(range.to).number;
      // Selection that ends at the very start of a line doesn't actually
      // cover that line's content — exclude it.
      if (toLine > fromLine && state.doc.line(toLine).from === range.to) {
        toLine -= 1;
      }
      for (let n = Math.max(fromLine, lastMarked + 1); n <= toLine; n++) {
        marks.push(selectedLineGutterMarker.range(state.doc.line(n).from));
        lastMarked = n;
      }
    }
    return RangeSet.of(marks);
  }
);

export const yamlGutterTheme = EditorView.theme({
  '.cm-gutters': {
    backgroundColor: '#ffffff',
    border: 'none',
    color: '#8c959f',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 16px 0 16px',
    minWidth: '3em',
  },
  '.cm-selectedLineGutter': {
    backgroundColor: '#d7d4f0',
  },
  '&.cm-focused': {
    outline: 'none',
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
      className={`rounded-md border border-gray-200 overflow-hidden flex flex-col ${className || ''}`}
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
          yamlGutterTheme,
          selectionGutterHighlighter,
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
          foldGutter: false,
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
