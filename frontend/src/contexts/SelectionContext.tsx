import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

/**
 * SelectionContext holds ephemeral per-scan selection state for the
 * CLU-85 selective remediation feature. State is keyed by
 * `scan_report_id` so that a new scan automatically resets all user
 * selections (per Option A of the spec — per-scan lifecycle).
 *
 * Two lists are tracked per content type:
 *   - `unchecked`: items the user explicitly deselected
 *   - `rechecked`: items the user explicitly re-checked despite being
 *     default-off (e.g. a soft keyword exclusion they want to override
 *     for this run)
 *
 * localStorage persistence survives browser refresh within the same
 * scan. When the scan_report_id changes, old keys age out naturally
 * as scans accumulate — no explicit cleanup.
 *
 * Cross-tab sync happens via the `storage` event so that an uncheck
 * in one tab is visible in another tab on the same browser.
 */

export interface SelectionState {
  scan_report_id: string
  file_report_id: string | null
  unchecked_items: Record<string, string[]>
  rechecked_items: Record<string, string[]>
  touched_content_types: string[]
  updated_at: string
}

interface SelectionContextValue {
  isUnchecked: (contentType: string, identifier: string) => boolean
  isRechecked: (contentType: string, identifier: string) => boolean
  markUnchecked: (contentType: string, identifier: string) => void
  markRechecked: (contentType: string, identifier: string) => void
  clearIdentifier: (contentType: string, identifier: string) => void
  getUncheckedList: (contentType: string) => string[]
  getRecheckedList: (contentType: string) => string[]
  getTouchedContentTypes: () => string[]
  markContentTypeTouched: (contentType: string) => void
  reset: () => void
}

const SelectionContext = createContext<SelectionContextValue | null>(null)

function storageKey(scanReportId: string): string {
  return `clu-selections-${scanReportId}`
}

function emptyState(
  scanReportId: string,
  fileReportId: string | null,
): SelectionState {
  return {
    scan_report_id: scanReportId,
    file_report_id: fileReportId,
    unchecked_items: {},
    rechecked_items: {},
    touched_content_types: [],
    updated_at: new Date().toISOString(),
  }
}

function hydrate(
  scanReportId: string,
  fileReportId: string | null,
): SelectionState {
  if (!scanReportId) return emptyState('', fileReportId)
  try {
    const raw = localStorage.getItem(storageKey(scanReportId))
    if (!raw) return emptyState(scanReportId, fileReportId)
    const parsed = JSON.parse(raw) as SelectionState
    // Sanity check — if the scan_report_id mismatches, treat as fresh
    if (parsed.scan_report_id !== scanReportId) {
      return emptyState(scanReportId, fileReportId)
    }
    return parsed
  } catch {
    return emptyState(scanReportId, fileReportId)
  }
}

interface SelectionProviderProps {
  scanReportId: string
  fileReportId?: string | null
  children: ReactNode
}

export function SelectionProvider({
  scanReportId,
  fileReportId = null,
  children,
}: SelectionProviderProps) {
  const [state, setState] = useState<SelectionState>(() =>
    hydrate(scanReportId, fileReportId),
  )
  const lastReportIdRef = useRef(scanReportId)

  // Re-hydrate (resetting selections) when the scan_report_id changes.
  // This is the "reset on new scan" behavior from Option A of the spec.
  useEffect(() => {
    if (lastReportIdRef.current !== scanReportId) {
      setState(hydrate(scanReportId, fileReportId))
      lastReportIdRef.current = scanReportId
    }
  }, [scanReportId, fileReportId])

  // Persist on every state change
  useEffect(() => {
    if (!state.scan_report_id) return
    try {
      localStorage.setItem(
        storageKey(state.scan_report_id),
        JSON.stringify(state),
      )
    } catch {
      // Ignore quota errors — ephemeral draft is best-effort
    }
  }, [state])

  // Cross-tab sync via storage event
  useEffect(() => {
    if (!state.scan_report_id) return
    const key = storageKey(state.scan_report_id)
    const handler = (e: StorageEvent) => {
      if (e.key !== key || !e.newValue) return
      try {
        const parsed = JSON.parse(e.newValue) as SelectionState
        if (parsed.scan_report_id === state.scan_report_id) {
          setState(parsed)
        }
      } catch {
        // noop
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [state.scan_report_id])

  const isUnchecked = useCallback(
    (contentType: string, identifier: string): boolean => {
      return state.unchecked_items[contentType]?.includes(identifier) ?? false
    },
    [state.unchecked_items],
  )

  const isRechecked = useCallback(
    (contentType: string, identifier: string): boolean => {
      return state.rechecked_items[contentType]?.includes(identifier) ?? false
    },
    [state.rechecked_items],
  )

  const markUnchecked = useCallback(
    (contentType: string, identifier: string) => {
      setState((prev) => {
        const list = prev.unchecked_items[contentType] ?? []
        if (list.includes(identifier)) return prev
        const rechecked = prev.rechecked_items[contentType] ?? []
        return {
          ...prev,
          unchecked_items: {
            ...prev.unchecked_items,
            [contentType]: [...list, identifier],
          },
          rechecked_items: {
            ...prev.rechecked_items,
            [contentType]: rechecked.filter((id) => id !== identifier),
          },
          touched_content_types: prev.touched_content_types.includes(contentType)
            ? prev.touched_content_types
            : [...prev.touched_content_types, contentType],
          updated_at: new Date().toISOString(),
        }
      })
    },
    [],
  )

  const markRechecked = useCallback(
    (contentType: string, identifier: string) => {
      setState((prev) => {
        const list = prev.rechecked_items[contentType] ?? []
        if (list.includes(identifier)) return prev
        const unchecked = prev.unchecked_items[contentType] ?? []
        return {
          ...prev,
          rechecked_items: {
            ...prev.rechecked_items,
            [contentType]: [...list, identifier],
          },
          unchecked_items: {
            ...prev.unchecked_items,
            [contentType]: unchecked.filter((id) => id !== identifier),
          },
          touched_content_types: prev.touched_content_types.includes(contentType)
            ? prev.touched_content_types
            : [...prev.touched_content_types, contentType],
          updated_at: new Date().toISOString(),
        }
      })
    },
    [],
  )

  const clearIdentifier = useCallback(
    (contentType: string, identifier: string) => {
      setState((prev) => ({
        ...prev,
        unchecked_items: {
          ...prev.unchecked_items,
          [contentType]: (prev.unchecked_items[contentType] ?? []).filter(
            (id) => id !== identifier,
          ),
        },
        rechecked_items: {
          ...prev.rechecked_items,
          [contentType]: (prev.rechecked_items[contentType] ?? []).filter(
            (id) => id !== identifier,
          ),
        },
        updated_at: new Date().toISOString(),
      }))
    },
    [],
  )

  const getUncheckedList = useCallback(
    (contentType: string): string[] => {
      return state.unchecked_items[contentType] ?? []
    },
    [state.unchecked_items],
  )

  const getRecheckedList = useCallback(
    (contentType: string): string[] => {
      return state.rechecked_items[contentType] ?? []
    },
    [state.rechecked_items],
  )

  const getTouchedContentTypes = useCallback(
    () => state.touched_content_types,
    [state.touched_content_types],
  )

  const markContentTypeTouched = useCallback((contentType: string) => {
    setState((prev) => {
      if (prev.touched_content_types.includes(contentType)) return prev
      return {
        ...prev,
        touched_content_types: [...prev.touched_content_types, contentType],
        updated_at: new Date().toISOString(),
      }
    })
  }, [])

  const reset = useCallback(() => {
    setState(emptyState(scanReportId, fileReportId))
    try {
      localStorage.removeItem(storageKey(scanReportId))
    } catch {
      // noop
    }
  }, [scanReportId, fileReportId])

  const value = useMemo<SelectionContextValue>(
    () => ({
      isUnchecked,
      isRechecked,
      markUnchecked,
      markRechecked,
      clearIdentifier,
      getUncheckedList,
      getRecheckedList,
      getTouchedContentTypes,
      markContentTypeTouched,
      reset,
    }),
    [
      isUnchecked,
      isRechecked,
      markUnchecked,
      markRechecked,
      clearIdentifier,
      getUncheckedList,
      getRecheckedList,
      getTouchedContentTypes,
      markContentTypeTouched,
      reset,
    ],
  )

  return (
    <SelectionContext.Provider value={value}>
      {children}
    </SelectionContext.Provider>
  )
}

export function useSelection(): SelectionContextValue {
  const ctx = useContext(SelectionContext)
  if (!ctx) {
    throw new Error('useSelection must be used inside a SelectionProvider')
  }
  return ctx
}
