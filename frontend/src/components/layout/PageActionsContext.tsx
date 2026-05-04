import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

// Split into two contexts to avoid render loops:
// - WriteContext: pages write actions here (no re-render triggered for readers)
// - ReadContext: TopBar reads actions from here

type SetActionsFn = (actions: ReactNode | null) => void

const WriteContext = createContext<SetActionsFn>(() => {})
const ReadContext = createContext<ReactNode | null>(null)

export function PageActionsProvider({ children }: { children: ReactNode }) {
  const [actions, setActions] = useState<ReactNode | null>(null)

  return (
    <WriteContext.Provider value={setActions}>
      <ReadContext.Provider value={actions}>
        {children}
      </ReadContext.Provider>
    </WriteContext.Provider>
  )
}

/**
 * Pages call this to inject action buttons into the TopBar.
 * Cleans up automatically on unmount. Supports dynamic content.
 */
export function usePageActions(actions: ReactNode) {
  // Only subscribes to WriteContext (the setter) — never re-renders
  // when the actions state changes, breaking the loop.
  const setActions = useContext(WriteContext)

  // Update on every render so dynamic content (like disabled state) propagates
  setActions(actions)

  // Clear on unmount
  useEffect(() => {
    return () => setActions(null)
  }, [setActions])
}

/**
 * TopBar calls this to read the current page actions.
 * Re-renders when actions change (subscribes to ReadContext).
 */
export function usePageActionsSlot(): ReactNode | null {
  return useContext(ReadContext)
}
