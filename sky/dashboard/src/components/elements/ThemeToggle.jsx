import React, { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Check, Monitor, Moon, Sun } from 'lucide-react';
import { SegmentedToggle } from '@/components/elements/SegmentedToggle';
import {
  THEME_DARK,
  THEME_LIGHT,
  THEME_SYSTEM,
  useTheme,
} from '@/hooks/useTheme';

const OPTIONS = [
  { value: THEME_LIGHT, label: 'Light', Icon: Sun },
  { value: THEME_DARK, label: 'Dark', Icon: Moon },
  { value: THEME_SYSTEM, label: 'System', Icon: Monitor },
];

/**
 * Light / Dark / System switch. With `showLabels` false (top bar) only the
 * icons are drawn; the text stays available to screen readers and as a
 * tooltip.
 */
export function ThemeToggle({ showLabels = false }) {
  const { theme, setTheme } = useTheme();
  return (
    <SegmentedToggle
      ariaLabel="Color theme"
      value={theme}
      onChange={setTheme}
      options={OPTIONS.map(({ value, label, Icon }) => ({
        value,
        label: (
          <span className="inline-flex items-center gap-1.5" title={label}>
            <Icon className="w-3.5 h-3.5" aria-hidden="true" />
            <span className={showLabels ? undefined : 'sr-only'}>{label}</span>
          </span>
        ),
      }))}
    />
  );
}

ThemeToggle.propTypes = {
  showLabels: PropTypes.bool,
};

/**
 * Top-bar theme control: one icon button (sun or moon, for the theme in
 * effect) that opens a Light / Dark / System menu, like the user menu next to it.
 */
export function ThemeMenu() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onMouseDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKeyDown = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const TriggerIcon = resolvedTheme === THEME_DARK ? Moon : Sun;
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label="Theme"
        title="Theme"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="inline-flex items-center justify-center p-2 rounded-full text-content-secondary hover:bg-surface-muted transition-colors duration-150 cursor-pointer"
      >
        <TriggerIcon className="w-5 h-5" aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Theme"
          className="absolute right-0 mt-2 w-40 py-1 bg-surface rounded-md shadow-lg z-50 border border-line"
        >
          {OPTIONS.map(({ value, label, Icon }) => (
            <button
              key={value}
              type="button"
              role="menuitemradio"
              aria-checked={theme === value}
              onClick={() => {
                setTheme(value);
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-content hover:bg-surface-muted"
            >
              <Icon className="w-4 h-4" aria-hidden="true" />
              <span className="flex-1 text-left">{label}</span>
              {theme === value && (
                <Check className="w-4 h-4 text-blue-fg" aria-hidden="true" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
