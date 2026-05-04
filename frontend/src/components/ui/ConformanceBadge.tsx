import { CheckCircle, AlertTriangle, XCircle, MinusCircle } from 'lucide-react'
import type { ConformanceLevel } from '@/api/client'

interface ConformanceBadgeProps {
  level: ConformanceLevel
  showIcon?: boolean
  size?: 'sm' | 'md' | 'lg'
}

const config = {
  Supports: {
    icon: CheckCircle,
    bg: 'bg-green-100',
    text: 'text-green-800',
    border: 'border-green-200',
    label: 'Supports',
  },
  'Partially Supports': {
    icon: AlertTriangle,
    bg: 'bg-amber-100',
    text: 'text-amber-800',
    border: 'border-amber-200',
    label: 'Partially',
  },
  'Does Not Support': {
    icon: XCircle,
    bg: 'bg-red-100',
    text: 'text-red-800',
    border: 'border-red-200',
    label: 'Does Not Support',
  },
  'Not Applicable': {
    icon: MinusCircle,
    bg: 'bg-surface-muted',
    text: 'text-text',
    border: 'border-border',
    label: 'N/A',
  },
}

export function ConformanceBadge({
  level,
  showIcon = true,
  size = 'md',
}: ConformanceBadgeProps) {
  const cfg = config[level] || config['Not Applicable']
  const Icon = cfg.icon

  const sizeClasses = {
    sm: 'px-2 py-0.5 text-xs gap-1',
    md: 'px-2.5 py-1 text-sm gap-1.5',
    lg: 'px-3 py-1.5 text-base gap-2',
  }

  const iconSizes = {
    sm: 12,
    md: 16,
    lg: 20,
  }

  return (
    <span
      className={`inline-flex items-center rounded-full font-medium border ${cfg.bg} ${cfg.text} ${cfg.border} ${sizeClasses[size]}`}
    >
      {showIcon && <Icon size={iconSizes[size]} className="shrink-0" />}
      <span>{cfg.label}</span>
    </span>
  )
}
