import { forwardRef, type ComponentPropsWithoutRef } from 'react'
import { Spinner } from './Spinner'
import type { LucideIcon } from 'lucide-react'

const VARIANT_CLASSES = {
  primary: 'bg-brand-primary text-white hover:bg-brand-primary/90',
  secondary: 'bg-brand-secondary text-white hover:bg-brand-secondary/90',
  outline: 'border border-border text-text-muted hover:bg-surface-muted',
  ghost: 'text-text-muted hover:bg-surface-muted',
  danger: 'bg-red-600 text-white hover:bg-red-700',
} as const

const SIZE_CLASSES = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-2.5 text-sm',
} as const

interface ButtonProps extends ComponentPropsWithoutRef<'button'> {
  variant?: keyof typeof VARIANT_CLASSES
  size?: keyof typeof SIZE_CLASSES
  loading?: boolean
  icon?: LucideIcon
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', loading = false, disabled, className = '', children, icon: Icon, ...props }, ref) => {
    const sizeMap = { sm: 14, md: 16, lg: 20 }
    const iconSize = sizeMap[size] ?? 16
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]} ${className}`}
        {...props}
      >
        {loading && <Spinner size="sm" label="Loading" />}
        {!loading && Icon && <Icon size={iconSize} />}
        {children}
      </button>
    )
  },
)

Button.displayName = 'Button'
