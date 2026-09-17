import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge conditional Tailwind classes (used by our copied kit components). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
