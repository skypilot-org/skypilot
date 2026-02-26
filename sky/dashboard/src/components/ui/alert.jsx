import React from 'react';

const Alert = ({ className = '', variant = 'default', children, ...props }) => {
  const baseClasses =
    'relative w-full rounded-lg border p-4 flex items-start space-x-2';
  const variantClasses = {
    default:
      'bg-blue-50 dark:bg-blue-900/30 border-blue-200 dark:border-blue-800 text-blue-800 dark:text-blue-200',
    destructive:
      'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-800 text-red-800 dark:text-red-200',
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
