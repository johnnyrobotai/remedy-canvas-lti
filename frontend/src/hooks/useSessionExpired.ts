/**
 * CLU-84: React hook that tracks whether the API session has expired.
 *
 * Returns `true` once any API call receives a 401 response. Polling
 * pages use this to (a) stop their interval and (b) render the
 * SessionExpiredBanner.
 */

import { useState, useEffect } from 'react'
import { isSessionExpired, onSessionExpired } from '@/api/client'

export function useSessionExpired(): boolean {
  const [expired, setExpired] = useState(isSessionExpired)

  useEffect(() => {
    // If already expired before mount, state is already true via the
    // initial useState call. Subscribe for future transitions.
    const unsub = onSessionExpired(setExpired)
    return unsub
  }, [])

  return expired
}
