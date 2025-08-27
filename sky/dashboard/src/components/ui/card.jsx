import * as React from 'react';
import PropTypes from 'prop-types';

import { cn } from '@/lib/utils';

const Card = React.forwardRef(({ className, children, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      'rounded-lg border bg-card text-card-foreground shadow-sm',
      className
    )}
    {...props}
  >
    {children}
  </div>
));
Card.displayName = 'Card';
Card.propTypes = {
  className: PropTypes.string,
  children: PropTypes.node,
};

const CardHeader = React.forwardRef(
  ({ className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('flex flex-col space-y-1.5 p-6', className)}
      {...props}
    >
      {children}
    </div>
  )
);
CardHeader.displayName = 'CardHeader';
CardHeader.propTypes = {
  className: PropTypes.string,
  children: PropTypes.node,
};

const CardTitle = React.forwardRef(({ className, children, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn(
      'text-2xl font-semibold leading-none tracking-tight',
      className
    )}
    {...props}
  >
    {children}
  </h3>
));
CardTitle.displayName = 'CardTitle';
CardTitle.propTypes = {
  className: PropTypes.string,
  children: PropTypes.node,
};

const CardDescription = React.forwardRef(
  ({ className, children, ...props }, ref) => (
    <p
      ref={ref}
      className={cn('text-sm text-muted-foreground', className)}
      {...props}
    >
      {children}
    </p>
  )
);
CardDescription.displayName = 'CardDescription';
CardDescription.propTypes = {
  className: PropTypes.string,
  children: PropTypes.node,
};

const CardContent = React.forwardRef(
  ({ className, children, ...props }, ref) => (
    <div ref={ref} className={cn('p-6 pt-0', className)} {...props}>
      {children}
    </div>
  )
);
CardContent.displayName = 'CardContent';
CardContent.propTypes = {
  className: PropTypes.string,
  children: PropTypes.node,
};

const CardFooter = React.forwardRef(
  ({ className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('flex items-center p-6 pt-0', className)}
      {...props}
    >
      {children}
    </div>
  )
);
CardFooter.displayName = 'CardFooter';
CardFooter.propTypes = {
  className: PropTypes.string,
  children: PropTypes.node,
};

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardDescription,
  CardContent,
};
