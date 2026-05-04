import type { ComponentPropsWithoutRef, ReactNode } from 'react'

interface CardProps extends Omit<ComponentPropsWithoutRef<'div'>, 'title'> {
  title?: ReactNode
}

export function Card({ title, className = '', children, ...props }: CardProps) {
  return (
    <div
      className={`rounded-xl border border-border bg-surface shadow-sm ${className}`}
      {...props}
    >
      {title && (
        <div className="border-b border-border-muted px-6 py-4">
          {typeof title === 'string' ? (
            <h3 className="text-sm font-medium uppercase tracking-wide text-text-subtle">{title}</h3>
          ) : (
            title
          )}
        </div>
      )}
      <div className="px-6 py-5">{children}</div>
    </div>
  )
}
