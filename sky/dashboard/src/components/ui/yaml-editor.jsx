'use client';

import React, { useMemo } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { Prec } from '@codemirror/state';
import { yaml } from '@codemirror/lang-yaml';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { search } from '@codemirror/search';
import { tags as t } from '@lezer/highlight';
import { getNonce } from '@/utils/csp';
import { THEME_DARK, useTheme } from '@/hooks/useTheme';

// Dock the search panel at the top of the editor (like the browser's find bar
// and most code editors) instead of CodeMirror's default bottom placement.
export const yamlSearchTop = search({ top: true });

export const yamlHighlightStyle = HighlightStyle.define([
  { tag: t.propertyName, color: 'hsl(var(--brand-bright))' },
  { tag: t.string, color: 'hsl(var(--code-string))' },
  { tag: t.content, color: 'hsl(var(--content))' },
  {
    tag: t.lineComment,
    color: 'hsl(var(--content-tertiary))',
    fontStyle: 'italic',
  },
  { tag: t.keyword, color: 'hsl(var(--code-string))' },
  { tag: t.meta, color: 'hsl(var(--content-faint))' },
  { tag: t.brace, color: 'hsl(var(--content-tertiary))' },
  { tag: t.squareBracket, color: 'hsl(var(--content-tertiary))' },
  { tag: t.punctuation, color: 'hsl(var(--content-tertiary))' },
]);

// Base editor colors from the dashboard tokens. Replaces CodeMirror's bundled
// 'light'/'dark' themes (white / One Dark), which ignore our palette. Light
// keeps CodeMirror's own defaults for text, caret and selection; only dark
// overrides them.
export const yamlColorTheme = (dark) =>
  EditorView.theme(
    {
      '&': {
        backgroundColor: 'hsl(var(--surface))',
        ...(dark ? { color: 'hsl(var(--content))' } : {}),
      },
      ...(dark
        ? {
            '.cm-cursor, .cm-dropCursor': {
              borderLeftColor: 'hsl(var(--content-strong))',
            },
            '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground':
              { backgroundColor: 'hsl(var(--blue-tint-strong))' },
          }
        : {}),
    },
    { dark }
  );

// I-beam over the entire editor box (gutter + content + empty area)
// so the whole surface reads as a single text region. CodeMirror's
// default only puts the I-beam on `.cm-content` (the contenteditable
// text), leaving the gutter on the default arrow — fine for IDEs,
// but in a small embedded YAML viewer the box feels split otherwise.
export const yamlTextCursorTheme = EditorView.theme({
  '&': { cursor: 'text' },
});

export const yamlGutterTheme = EditorView.theme({
  '.cm-gutters': {
    backgroundColor: 'hsl(var(--surface))',
    border: 'none',
    color: 'hsl(var(--code-gutter))',
  },
  // Fixed-width line-number column so the gutter doesn't jitter when the
  // line count crosses 1 → 2 → 3 digits. Right-aligned within a 4em box
  // (fits 3-digit numbers at 13px font without growing).
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 16px 0 8px',
    minWidth: '4em',
    boxSizing: 'border-box',
    textAlign: 'right',
  },
  '&.cm-focused': {
    outline: 'none',
  },
});

// Cmd/Ctrl+F opens CodeMirror's built-in search panel (provided by basicSetup's
// search keymap). We keep that panel and its behavior exactly — Find, next /
// previous / all, and the match-case / regexp / by-word toggles — and only
// restyle it so it doesn't look dated, plus hide the replace controls (the YAML
// editor is find-only). Match highlights are also colored to match the UI.
export const yamlSearchPanelTheme = EditorView.theme({
  '.cm-panels': {
    backgroundColor: 'hsl(var(--surface))',
    color: 'hsl(var(--content))',
  },
  '.cm-panels.cm-panels-bottom': {
    borderTop: '1px solid hsl(var(--line))',
  },
  '.cm-panels.cm-panels-top': {
    borderBottom: '1px solid hsl(var(--line))',
  },
  '.cm-panel.cm-search': {
    position: 'relative',
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: '6px',
    // Right padding leaves room for the absolutely-positioned close button.
    padding: '8px 34px 8px 10px',
    fontSize: '13px',
    fontFamily: 'inherit',
  },
  // Find-only: hide CodeMirror's built-in replace row. Selectors include
  // `.cm-textfield` / `.cm-button` so they outrank the styling rules below
  // (same base specificity) and actually win.
  '.cm-panel.cm-search br': { display: 'none' },
  '.cm-panel.cm-search input.cm-textfield[name="replace"]': {
    display: 'none',
  },
  '.cm-panel.cm-search button.cm-button[name="replace"]': { display: 'none' },
  '.cm-panel.cm-search button.cm-button[name="replaceAll"]': {
    display: 'none',
  },
  // A shared 30px control height keeps the field, buttons and checkboxes on one
  // baseline so the row reads as vertically centered.
  '.cm-panel.cm-search input.cm-textfield': {
    height: '30px',
    boxSizing: 'border-box',
    margin: '0',
    border: '1px solid hsl(var(--input))',
    borderRadius: 'calc(var(--radius) - 2px)',
    padding: '0 10px',
    fontSize: '13px',
    backgroundColor: 'hsl(var(--surface))',
    outline: 'none',
  },
  // Subtle focus that hugs the field at its own radius (no offset ring): a soft
  // neutral halo plus a slightly darker border, derived from the --ring token —
  // not a heavy black ring, not a custom colour.
  '.cm-panel.cm-search input.cm-textfield:focus-visible': {
    borderColor: 'hsl(var(--ring) / 0.4)',
    boxShadow: '0 0 0 3px hsl(var(--ring) / 0.12)',
  },
  '.cm-panel.cm-search button.cm-button': {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '30px',
    boxSizing: 'border-box',
    margin: '0',
    backgroundImage: 'none',
    backgroundColor: 'hsl(var(--surface))',
    border: '1px solid hsl(var(--input))',
    borderRadius: 'calc(var(--radius) - 2px)',
    color: 'hsl(var(--content))',
    padding: '0 12px',
    fontSize: '13px',
    lineHeight: '1',
    cursor: 'pointer',
  },
  '.cm-panel.cm-search button.cm-button:hover': {
    backgroundColor: 'hsl(var(--surface-muted))',
  },
  '.cm-panel.cm-search button.cm-button:active': {
    backgroundImage: 'none',
    backgroundColor: 'hsl(var(--line))',
  },
  '.cm-panel.cm-search button.cm-button:focus-visible': {
    outline: 'none',
    borderColor: 'hsl(var(--ring) / 0.4)',
    boxShadow: '0 0 0 3px hsl(var(--ring) / 0.12)',
  },
  // Extra left margin gives the three checkbox toggles room to breathe, both
  // from the buttons and from each other.
  '.cm-panel.cm-search label': {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '5px',
    height: '30px',
    margin: '0 0 0 8px',
    fontSize: '13px',
    color: 'hsl(var(--content-secondary))',
    cursor: 'pointer',
  },
  '.cm-panel.cm-search label input[type="checkbox"]': {
    width: '15px',
    height: '15px',
    margin: '0',
    accentColor: 'hsl(var(--blue-solid))',
    cursor: 'pointer',
  },
  '.cm-panel.cm-search button[name="close"]': {
    position: 'absolute',
    top: '50%',
    right: '8px',
    transform: 'translateY(-50%)',
    margin: '0',
    padding: '2px 6px',
    backgroundColor: 'transparent',
    backgroundImage: 'none',
    border: 'none',
    color: 'hsl(var(--content-faint))',
    fontSize: '18px',
    lineHeight: '1',
    cursor: 'pointer',
  },
  '.cm-panel.cm-search button[name="close"]:hover': {
    color: 'hsl(var(--content))',
    backgroundColor: 'transparent',
  },
  '.cm-searchMatch': {
    backgroundColor: 'rgba(250, 204, 21, 0.35)',
    borderRadius: '2px',
  },
  '.cm-searchMatch.cm-searchMatch-selected': {
    backgroundColor: 'rgba(59, 130, 246, 0.4)',
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
  const { resolvedTheme } = useTheme();
  const colorTheme = useMemo(
    () => yamlColorTheme(resolvedTheme === THEME_DARK),
    [resolvedTheme]
  );
  return (
    <div
      className={`rounded-md border border-line overflow-hidden flex flex-col ${className || ''}`}
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
          yamlTextCursorTheme,
          yamlSearchPanelTheme,
          Prec.high(yamlSearchTop),
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
        theme={colorTheme}
      />
    </div>
  );
}

export default YamlEditor;
