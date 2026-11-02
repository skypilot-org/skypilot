import React from 'react';

const Alert = ({ className = '', variant = 'default', children, ...props }) => {
  const baseClasses =
    'relative w-full rounded-lg border p-4 flex items-start space-x-2';
  const variantClasses = {
    default: 'bg-blue-tint border-blue-line text-blue-fg-stronger',
    destructive: 'bg-red-tint border-red-line text-red-fg-stronger',
  };

  return (
    <div
      role="alert"
      className={`${baseClasses} ${variantClasses[variant]} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
};

const AlertDescription = ({ className = '', children, ...props }) => (
  <div className={`text-sm leading-relaxed ${className}`} {...props}>
    {children}
  </div>
);

export { Alert, AlertDescription };
