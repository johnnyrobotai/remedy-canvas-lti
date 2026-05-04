import { useState, useEffect, useCallback, useRef } from 'react'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Card, Badge, ProgressBar } from '@/components/ui'
import { Film, Download, Captions, Search, Video, MonitorPlay } from 'lucide-react'
import {
  scanCourseVideos,
  startTranscription,
  getTranscriptionJob,
  listTranscriptionJobs,
  injectCaptions,
  type YouTubeVideo,
  type TranscriptionJob,
} from '@/api/client'

// Phase display metadata
const PHASE_LABELS: Record<string, string> = {
  downloading: 'Downloading',
  extracting: 'Extracting',
  segmenting: 'Segmenting',
  transcribing: 'Transcribing',
  generating: 'Generating',
  complete: 'Complete',
}

const PHASE_BADGE_VARIANT: Record<string, 'info' | 'warning' | 'success' | 'neutral'> = {
  downloading: 'info',
  extracting: 'info',
  segmenting: 'warning',
  transcribing: 'warning',
  generating: 'info',
  complete: 'success',
}

function phaseBadge(job: TranscriptionJob) {
  const label = PHASE_LABELS[job.phase] ?? job.phase
  const variant = PHASE_BADGE_VARIANT[job.phase] ?? 'neutral'
  return <Badge variant={variant}>{label}</Badge>
}

function downloadBlob(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// Single video row in the table
function VideoRow({
  video,
  job,
  onTranscribe,
  onInject,
}: {
  video: YouTubeVideo
  job: TranscriptionJob | null
  onTranscribe: (video: YouTubeVideo) => void
  onInject: (job: TranscriptionJob) => void
}) {
  const isRunning = job && (job.status === 'pending' || job.status === 'running')
  const isComplete = job?.status === 'completed'
  const isFailed = job?.status === 'failed'

  const progressPct =
    isRunning && job.chunks_total > 0
      ? Math.round((job.chunks_transcribed / job.chunks_total) * 100)
      : job?.progress ?? 0

  return (
    <tr className="border-b border-border-muted last:border-0">
      {/* Thumbnail */}
      <td className="py-3 pr-4">
        <a href={video.video_url} target="_blank" rel="noopener noreferrer">
          <img
            src={video.thumbnail_url}
            alt={video.video_id}
            className="h-14 w-24 rounded object-cover"
            loading="lazy"
          />
        </a>
      </td>

      {/* Video info */}
      <td className="py-3 pr-4">
        <div className="flex items-center gap-1.5">
          {video.source_type === 'studio' ? (
            <Badge variant="info" className="text-[10px] px-1.5 py-0">
              <MonitorPlay className="mr-0.5 h-3 w-3" />Studio
            </Badge>
          ) : (
            <Badge variant="neutral" className="text-[10px] px-1.5 py-0">
              <Video className="mr-0.5 h-3 w-3" />YouTube
            </Badge>
          )}
          <p className="text-sm font-medium text-text">{job?.video_title ?? video.video_id}</p>
        </div>
        <p className="mt-0.5 text-xs text-text-subtle">{video.video_url}</p>
      </td>

      {/* Source page */}
      <td className="py-3 pr-4">
        <p className="text-sm text-text-muted">{video.page_title}</p>
        <p className="text-xs text-text-subtle">{video.content_type}</p>
      </td>

      {/* Status / Progress */}
      <td className="py-3 pr-4 min-w-[140px]">
        {!job && <Badge variant="neutral">Not started</Badge>}
        {isRunning && (
          <div className="space-y-1">
            {phaseBadge(job)}
            <ProgressBar progress={progressPct} className="mt-1" />
            {job.chunks_total > 0 && (
              <p className="text-xs text-text-subtle">
                {job.chunks_transcribed}/{job.chunks_total} chunks
              </p>
            )}
          </div>
        )}
        {isComplete && phaseBadge(job)}
        {isFailed && (
          <div>
            <Badge variant="error">Failed</Badge>
            {job.error && <p className="mt-0.5 text-xs text-red-500">{job.error}</p>}
          </div>
        )}
      </td>

      {/* Actions */}
      <td className="py-3">
        <div className="flex flex-wrap gap-2">
          {(!job || isFailed) && (
            <Button size="sm" onClick={() => onTranscribe(video)} disabled={!!isRunning}>
              <Film className="h-3.5 w-3.5" />
              Transcribe
            </Button>
          )}

          {isComplete && (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    downloadBlob(job.vtt_content, `${job.video_id}.vtt`, 'text/vtt')
                  }
                >
                  <Download className="h-3.5 w-3.5" />
                  VTT
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    downloadBlob(job.srt_content, `${job.video_id}.srt`, 'text/plain')
                  }
                >
                  <Download className="h-3.5 w-3.5" />
                  SRT
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => onInject(job)}
                  disabled={job.page_ids_injected.length > 0}
                  title={
                    job.page_ids_injected.length > 0
                      ? 'Already injected into Canvas pages'
                      : 'Inject captions overlay into Canvas pages'
                  }
                >
                  <Captions className="h-3.5 w-3.5" />
                  {job.page_ids_injected.length > 0 ? 'Injected' : 'Inject Overlay'}
                </Button>
              </div>
              {video.source_type === 'studio' && (
                <p className="text-xs text-blue-600">
                  Download SRT and upload to Canvas Studio for native captions
                </p>
              )}
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}

export function CaptionsPage() {
  const { session } = useLTI()
  const courseId = session?.canvasCourseId ? String(session.canvasCourseId) : null

  // Page state
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)
  const [videos, setVideos] = useState<YouTubeVideo[] | null>(null)

  // Map from video_id -> active job
  const [jobs, setJobs] = useState<Record<string, TranscriptionJob>>({})
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // On mount: scan for videos and load any existing jobs
  const doScan = useCallback(async () => {
    if (!courseId) return
    setScanning(true)
    setScanError(null)
    try {
      const [vids, existingJobs] = await Promise.all([
        scanCourseVideos(courseId),
        listTranscriptionJobs(courseId).catch(() => [] as TranscriptionJob[]),
      ])
      setVideos(vids)
      // Index existing jobs by video_id — keep the latest per video
      const jobMap: Record<string, TranscriptionJob> = {}
      for (const j of existingJobs) {
        const prev = jobMap[j.video_id]
        if (!prev || j.created_at > prev.created_at) {
          jobMap[j.video_id] = j
        }
      }
      setJobs(jobMap)
    } catch (err) {
      setScanError(err instanceof Error ? err.message : 'Failed to scan course videos')
    } finally {
      setScanning(false)
    }
  }, [courseId])

  useEffect(() => {
    doScan()
  }, [doScan])

  // Poll in-progress jobs
  useEffect(() => {
    if (!courseId) return
    const activeIds = Object.values(jobs)
      .filter((j) => j.status === 'pending' || j.status === 'running')
      .map((j) => j.id)

    if (activeIds.length === 0) {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }

    if (pollRef.current) clearInterval(pollRef.current)

    pollRef.current = setInterval(async () => {
      const updates = await Promise.allSettled(
        activeIds.map((id) => getTranscriptionJob(courseId, id)),
      )
      setJobs((prev) => {
        const next = { ...prev }
        for (const result of updates) {
          if (result.status === 'fulfilled') {
            const updated = result.value
            next[updated.video_id] = updated
          }
        }
        return next
      })
    }, 2500)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [courseId, JSON.stringify(Object.keys(jobs).sort())])

  const handleTranscribe = useCallback(
    async (video: YouTubeVideo) => {
      if (!courseId) return
      try {
        const job = await startTranscription(courseId, video.video_url, video.video_id, video.source_type || 'youtube')
        setJobs((prev) => ({ ...prev, [video.video_id]: job }))
      } catch (err) {
        console.error('Failed to start transcription:', err)
      }
    },
    [courseId],
  )

  const handleInject = useCallback(
    async (job: TranscriptionJob) => {
      if (!courseId) return
      // Find the video's page_id(s) from our video list
      const video = videos?.find((v) => v.video_id === job.video_id)
      const pageIds = video ? [video.page_id] : []
      if (pageIds.length === 0) return
      try {
        await injectCaptions(courseId, job.id, pageIds)
        // Refresh the job to pick up page_ids_injected
        const updated = await getTranscriptionJob(courseId, job.id)
        setJobs((prev) => ({ ...prev, [job.video_id]: updated }))
      } catch (err) {
        console.error('Failed to inject captions:', err)
      }
    },
    [courseId, videos],
  )

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">Video Captions</h1>
          <p className="mt-1 text-sm text-text-subtle">
            Transcribe YouTube and Canvas Studio videos embedded in course pages.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={doScan} disabled={scanning}>
          <Search className="h-4 w-4" />
          {scanning ? 'Scanning...' : 'Re-scan'}
        </Button>
      </div>

      {/* Scanning state */}
      {scanning && (
        <Card className="py-12 text-center">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-brand-primary border-t-transparent" />
          <p className="mt-3 text-sm text-text-subtle">Scanning course for videos...</p>
        </Card>
      )}

      {/* Error state */}
      {!scanning && scanError && (
        <Card className="border-red-200 bg-red-50">
          <p className="text-sm font-medium text-red-700">Scan failed</p>
          <p className="mt-1 text-sm text-red-500">{scanError}</p>
          <Button size="sm" variant="danger" className="mt-3" onClick={doScan}>
            Retry
          </Button>
        </Card>
      )}

      {/* Empty state */}
      {!scanning && !scanError && videos !== null && videos.length === 0 && (
        <Card className="py-12 text-center">
          <Film className="mx-auto h-10 w-10 text-gray-300" />
          <p className="mt-3 text-sm font-medium text-text-subtle">No videos found</p>
          <p className="mt-1 text-xs text-text-subtle">
            This course doesn't have any YouTube or Canvas Studio videos embedded in pages, assignments, or discussions.
          </p>
        </Card>
      )}

      {/* Video table */}
      {!scanning && !scanError && videos && videos.length > 0 && (
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-text-muted">
              {videos.length} video{videos.length !== 1 ? 's' : ''} found
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border text-left text-xs font-medium uppercase tracking-wide text-text-subtle">
                  <th className="pb-2 pr-4">Thumbnail</th>
                  <th className="pb-2 pr-4">Video</th>
                  <th className="pb-2 pr-4">Source Page</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {videos.map((video) => (
                  <VideoRow
                    key={video.video_id + video.page_id}
                    video={video}
                    job={jobs[video.video_id] ?? null}
                    onTranscribe={handleTranscribe}
                    onInject={handleInject}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
