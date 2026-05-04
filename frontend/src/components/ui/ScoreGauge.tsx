interface ScoreGaugeProps {
  score: number
  size?: number
}

export function ScoreGauge({ score, size = 128 }: ScoreGaugeProps) {
  const center = size / 2
  const radius = center - 10
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (score / 100) * circumference
  const color =
    score >= 80 ? 'text-green-500' : score >= 50 ? 'text-yellow-500' : 'text-red-500'

  const fontSize = size >= 128 ? 'text-3xl' : size >= 80 ? 'text-xl' : 'text-sm'

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="8"
          className="text-gray-200"
        />
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="8"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className={color}
        />
      </svg>
      <span className={`absolute font-bold text-text ${fontSize}`}>{score}</span>
    </div>
  )
}
