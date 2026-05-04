import { useCallback, useEffect, useState } from 'react'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Card } from '@/components/ui'
import {
  listExclusions,
  removeExclusion,
  type ExclusionSummary,
} from '@/api/client'
import { Trash2, ShieldOff } from 'lucide-react'

/**
 * CLU-85: manage the permanent exclusion list for the current course.
 *
 * Items on this list are pre-unchecked on every content-type review
 * view AND merged into the skip lists at the start of every AutoRemedy
 * run (including Fix My Course). Removing an exclusion here takes
 * effect immediately — on the next scan, the item will be default-
 * checked again.
 */
export function ExclusionsPanel() {
  const { session } = useLTI()
  const courseId = session?.canvasCourseId

  const [exclusions, setExclusions] = useState<ExclusionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [removingIdentifier, setRemovingIdentifier] = useState<string | null>(
    null,
  )

  const refresh = useCallback(async () => {
    if (!courseId) return
    setLoading(true)
    setError(null)
    try {
      const res = await listExclusions(courseId)
      setExclusions(res.exclusions)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load exclusions')
    } finally {
      setLoading(false)
    }
  }, [courseId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleRemove = useCallback(
    async (item: ExclusionSummary) => {
      if (!courseId) return
      setRemovingIdentifier(item.item_identifier)
      try {
        await removeExclusion(courseId, item.item_identifier)
        setExclusions((prev) =>
          prev.filter((x) => x.item_identifier !== item.item_identifier),
        )
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : 'Failed to remove exclusion'
        alert(`Could not remove: ${msg}`)
      } finally {
        setRemovingIdentifier(null)
      }
    },
    [courseId],
  )

  return (
    <div>
      <div className="mb-6 flex items-center gap-3">
        <ShieldOff
          className="h-6 w-6 text-brand-primary"
          aria-hidden="true"
        />
        <div>
          <h2 className="text-xl font-bold text-text">
            Permanent Exclusions
          </h2>
          <p className="text-sm text-text-subtle">
            Items AutoRemedy will always skip on this course.
          </p>
        </div>
      </div>

      {error && (
        <Card className="mb-4 p-4 text-sm text-red-600" role="alert">
          {error}
        </Card>
      )}

      {loading ? (
        <Card className="py-12 text-center text-sm text-text-subtle">
          Loading…
        </Card>
      ) : exclusions.length === 0 ? (
        <Card className="py-12 text-center">
          <p className="text-text-subtle">No permanent exclusions.</p>
          <p className="mt-2 text-xs text-text-subtle">
            Use the &quot;Exclude permanently&quot; action on any item row in
            a content-type review view to add one.
          </p>
        </Card>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-surface shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-surface-muted">
              <tr>
                <th
                  className="px-4 py-3 font-medium text-text-subtle"
                  scope="col"
                >
                  Item
                </th>
                <th
                  className="px-4 py-3 font-medium text-text-subtle"
                  scope="col"
                >
                  Type
                </th>
                <th
                  className="px-4 py-3 font-medium text-text-subtle"
                  scope="col"
                >
                  Reason
                </th>
                <th
                  className="px-4 py-3 font-medium text-text-subtle"
                  scope="col"
                >
                  Excluded
                </th>
                <th className="w-24 px-4 py-3" scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {exclusions.map((x) => {
                const isRemoving = removingIdentifier === x.item_identifier
                return (
                  <tr key={x.item_identifier}>
                    <td className="px-4 py-3 font-mono text-xs text-text">
                      {x.item_identifier}
                    </td>
                    <td className="px-4 py-3 text-xs text-text-subtle">
                      {x.item_type}
                    </td>
                    <td className="px-4 py-3 text-xs text-text-subtle">
                      {x.reason || '—'}
                    </td>
                    <td className="px-4 py-3 text-xs text-text-subtle">
                      {new Date(x.excluded_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3">
                      <Button
                        onClick={() => handleRemove(x)}
                        variant="secondary"
                        size="sm"
                        icon={Trash2}
                        disabled={isRemoving}
                      >
                        {isRemoving ? 'Removing…' : 'Remove'}
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
