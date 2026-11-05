import React from 'react';
import { CheckIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

function StepCircle({ state, children }) {
  const base =
    'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px]';
  if (state === 'done') {
    return (
      <span className={cn(base, 'bg-green-600 text-white')}>
        <CheckIcon size={12} strokeWidth={3} aria-hidden="true" />
      </span>
    );
  }
  if (state === 'current') {
    return (
      <span className={cn(base, 'bg-sky-600 text-white')}>{children}</span>
    );
  }
  return (
    <span className={cn(base, 'border border-gray-300 text-gray-500')}>
      {children}
    </span>
  );
}

// A row of numbered steps for a multi-step flow: the steps before `current`
// are ticked off, `current` is highlighted, the rest are outlined.
export function Stepper({ steps, current, label = 'Steps' }) {
  return (
    <ol aria-label={label} className="flex items-center gap-2.5 text-[13px]">
      {steps.map((name, i) => {
        const n = i + 1;
        const state = n < current ? 'done' : n === current ? 'current' : 'todo';
        return (
          <li
            key={name}
            aria-current={state === 'current' ? 'step' : undefined}
            className="flex items-center gap-2.5"
          >
            {i > 0 && (
              <span aria-hidden="true" className="h-px w-10 bg-gray-300" />
            )}
            <span
              className={cn(
                'flex items-center gap-1.5',
                state === 'done'
                  ? 'text-green-600'
                  : state === 'current'
                    ? 'font-medium text-sky-600'
                    : 'text-gray-500'
              )}
            >
              <StepCircle state={state}>{n}</StepCircle>
              {name}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
