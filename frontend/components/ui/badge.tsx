import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from './utils';

/**
 * Status pill.
 *
 * The semantic variants are exactly Section 13's four colours — amber pending,
 * green recovered, red unrecoverable, grey stopped — so a status means the same
 * thing on every page of the product.
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-micro font-medium uppercase transition-colors',
  {
    variants: {
      variant: {
        neutral: 'border-line bg-surface-raised text-ink-muted',
        accent: 'border-accent/25 bg-accent/10 text-accent',
        recovered: 'border-recovered/25 bg-recovered/10 text-recovered',
        pending: 'border-pending/25 bg-pending/10 text-pending',
        unrecoverable: 'border-unrecoverable/25 bg-unrecoverable/10 text-unrecoverable',
        stopped: 'border-stopped/25 bg-stopped/10 text-stopped',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
