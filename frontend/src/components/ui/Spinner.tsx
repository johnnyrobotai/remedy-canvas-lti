import type { ComponentPropsWithoutRef } from 'react'

const SIZES = {
  sm: 'h-4 w-4 border-2',
  md: 'h-8 w-8 border-[3px]',
  lg: 'h-12 w-12 border-4',
} as const

interface SpinnerProps extends ComponentPropsWithoutRef<'div'> {
  size?: keyof typeof SIZES
  label?: string
}

export function Spinner({ size = 'md', label = 'Loading', className = '', ...props }: SpinnerProps) {
  return (
    <div
      role="status"
      aria-label={label}
      className={`animate-spin rounded-full border-border border-t-brand-primary ${SIZES[size]} ${className}`}
      {...props}
    />
  )
}
