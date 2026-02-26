'use client';

import React from 'react';
import { useTheme } from 'next-themes';
import { Sun, Moon, Monitor } from 'lucide-react';
import { CustomTooltip } from '@/components/utils';

const themes = [
  { value: 'light', icon: Sun, label: 'Light mode' },
  { value: 'dark', icon: Moon, label: 'Dark mode' },
  { value: 'system', icon: Monitor, label: 'System theme' },
];

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => setMounted(true), []);

  const cycle = () => {
    const idx = themes.findIndex((t) => t.value === theme);
    const next = themes[(idx + 1) % themes.length];
    setTheme(next.value);
  };

  // Avoid hydration mismatch — render a placeholder until mounted
  if (!mounted) {
    return (
      <div className="inline-flex items-center justify-center align-middle p-2 rounded-full w-9 h-9" />
    );
  }

  const current = themes.find((t) => t.value === theme) || themes[0];
  const Icon = current.icon;

  return (
    <CustomTooltip
      content={current.label}
      className="text-sm text-muted-foreground"
    >
      <button
        onClick={cycle}
        className="inline-flex items-center justify-center align-middle p-2 rounded-full text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors duration-150 cursor-pointer"
        title={current.label}
      >
        <Icon className="w-5 h-5" />
      </button>
    </CustomTooltip>
  );
}

export default ThemeToggle;
