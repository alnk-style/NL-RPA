import {
  forwardRef,
  type HTMLAttributes,
} from 'react';

import { cn } from '../../lib/utils';

export type BadgeVariant =
  | 'default'
  | 'secondary'
  | 'success'
  | 'warning'
  | 'danger'
  | 'outline';

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

const variantClasses: Record<BadgeVariant, string> = {
  default:
    'border-transparent bg-brand-100 text-brand-700 dark:bg-brand-900/50 dark:text-brand-100',
  secondary:
    'border-transparent bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200',
  success:
    'border-transparent bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-200',
  warning:
    'border-transparent bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200',
  danger:
    'border-transparent bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-200',
  outline:
    'border-slate-200 bg-transparent text-slate-600 dark:border-slate-700 dark:text-slate-300',
};

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant = 'default', ...props }, ref) => (
    <span
      ref={ref}
      className={cn(
        'inline-flex h-6 items-center rounded-full border px-2.5 text-xs font-medium leading-none',
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  ),
);

Badge.displayName = 'Badge';

export default Badge;
