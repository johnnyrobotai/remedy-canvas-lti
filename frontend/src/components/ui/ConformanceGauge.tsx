import { useMemo } from 'react'

export type ConformanceBand = 'excellent' | 'good' | 'needs_work' | 'poor'

interface ConformanceGaugeProps {
  percentage: number
  size?: number
  strokeWidth?: number
  /** Backend-supplied band label. When provided, overrides the fallback
   * threshold logic so the verbal label can never drift from the number.
   * Map via scoring_service.score_band on the server (CLU-55). */
  band?: ConformanceBand
}

const BAND_STYLE: Record<ConformanceBand, { color: string; label: string }> = {
  excellent: { color: '#10B981', label: 'Excellent' },
  good: { color: '#F59E0B', label: 'Good' },
  needs_work: { color: '#F97316', label: 'Needs Work' },
  poor: { color: '#EF4444', label: 'Poor' },
}

function bandFromPercentage(percentage: number): ConformanceBand {
  if (percentage >= 90) return 'excellent'
  if (percentage >= 70) return 'good'
  if (percentage >= 40) return 'needs_work'
  return 'poor'
}

export function ConformanceGauge({
  percentage,
  size = 160,
  strokeWidth = 12,
  band,
}: ConformanceGaugeProps) {
  const { color, label } = useMemo(() => {
    const resolved = band ?? bandFromPercentage(percentage)
    return BAND_STYLE[resolved]
  }, [percentage, band])

  const radius = (size - strokeWidth) / 2
  const circumference = radius * 2 * Math.PI
  const offset = circumference - (percentage / 100) * circumference

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="transform -rotate-90">
          {/* Background circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="#E5E7EB"
            strokeWidth={strokeWidth}
          />
          {/* Progress circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            className="transition-all duration-1000 ease-out"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="font-bold text-text"
            style={{ fontSize: size * 0.22 }}
          >
            {percentage.toFixed(1)}%
          </span>
          <span
            className="font-medium text-text-subtle"
            style={{ fontSize: size * 0.08 }}
          >
            {label}
          </span>
        </div>
      </div>
    </div>
  )
}
