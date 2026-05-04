interface FilterChipProps {
  label: string
  active: boolean
  onClick: () => void
  count?: number
}

export function FilterChip({ label, active, onClick, count }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1 text-xs font-medium transition ${
        active
          ? 'bg-brand-primary text-white'
          : 'bg-surface-muted text-text-muted hover:bg-gray-200'
      }`}
    >
      {label}
      {count !== undefined && <span className="ml-1">({count})</span>}
    </button>
  )
}
