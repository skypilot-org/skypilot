import * as React from 'react';
import { Inbox } from 'lucide-react';

import { cn } from '@/lib/utils';

// Standardized empty-state block: a generously sized, centered column with a
// subtle neutral icon, a title, and an optional description and action. Used
// both inside tables (via EmptyTableState) and as a full-section placeholder.
//
// Colors are inline hex on purpose — the dashboard's theme CSS variables can
// resolve to white in some render contexts, which would make the text and
// icon disappear.
export function EmptyState({
  title,
  description,
  icon,
  action,
  minHeight = 280,
  className,
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 text-center',
        className
      )}
      style={{ minHeight }}
    >
      <div
        className="flex items-center justify-center"
        style={{
          width: 40,
          height: 40,
          marginBottom: 16,
          borderRadius: 9999,
          backgroundColor: '#f9fafb',
          border: '1px solid #e5e7eb',
          color: '#9ca3af',
        }}
      >
        {icon || <Inbox size={20} strokeWidth={1.75} />}
      </div>
      <div style={{ fontSize: 16, fontWeight: 500, color: '#111827' }}>
        {title}
      </div>
      {description ? (
        <div
          style={{
            marginTop: 6,
            fontSize: 14,
            lineHeight: 1.55,
            color: '#6b7280',
            maxWidth: 460,
          }}
        >
          {description}
        </div>
      ) : null}
      {action ? <div style={{ marginTop: 20 }}>{action}</div> : null}
    </div>
  );
}
