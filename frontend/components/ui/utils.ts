import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge Tailwind classes so a caller's override actually wins.
 *
 * Plain string concatenation leaves both `p-4` and `p-6` in the class list and
 * lets specificity order decide, which makes component props unreliable.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
