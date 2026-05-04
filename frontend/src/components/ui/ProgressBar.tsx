interface ProgressBarProps {
  progress: number
  label?: string
  sublabel?: string
  color?: string
  className?: string
}

export function ProgressBar({
  progress,
  label,
  sublabel,
  color = 'bg-brand-primary',
  className = '',
}: ProgressBarProps) {
  const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100)

  return (
    <div className={className}>
      {(label || sublabel) && (
        <div className="mb-1 flex justify-between text-xs text-gray-500">
          {label && <span>{label}</span>}
          {sublabel ? <span>{sublabel}</span> : <span>{pct}%</span>}
        </div>
      )}
      <div className="h-2.5 overflow-hidden rounded-full bg-gray-200">
        <div
          className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
