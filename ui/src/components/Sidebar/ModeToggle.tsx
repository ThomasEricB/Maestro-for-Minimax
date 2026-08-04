import { useEffect } from 'react'
import { useStore } from '../../stores/useStore'

/** Studio Video sub-modes and what a model must support to run them.
 *
 *  Frames    — text-only or start/end frame conditioning. Every video model.
 *  Multi-Shot— one generation per clip, each optionally anchored by a start
 *              image, so the model must accept "S".
 *  Extend    — continues an existing clip from a video source ("V" plus
 *              video_continuation).
 *  Blend     — generates a transition pinned by a first AND last frame, so
 *              the model must accept both "S" and "E".
 *
 *  MiniMax H3 Ref2VA allows only "T" (its conditioning is reference
 *  images/videos/audio, not timeline positions), so it lands on Frames
 *  alone rather than offering three tabs that would fail at generate time.
 */
const MODES = [
  { value: 0, label: 'Frames', supported: () => true },
  { value: 2, label: 'Multi-Shot', supported: (o: ModeCaps) => o.allowed.includes('S') },
  { value: 3, label: 'Extend', supported: (o: ModeCaps) => o.allowed.includes('V') || o.videoContinuation },
  { value: 4, label: 'Blend', supported: (o: ModeCaps) => o.allowed.includes('S') && o.allowed.includes('E') },
]

interface ModeCaps { allowed: string; videoContinuation: boolean }

export function ModeToggle() {
  const imageMode = useStore(s => s.params.image_mode)
  const setParam = useStore(s => s.setParam)
  const modelOptions = useStore(s => s.modelOptions)

  // Older backends don't send image_prompt_types_allowed. Treat "unknown" as
  // "everything allowed" so this never hides tabs that used to work.
  const allowed = modelOptions?.image_prompt_types_allowed ?? 'TSEVL'
  const caps: ModeCaps = { allowed, videoContinuation: modelOptions?.video_continuation ?? false }
  const modes = MODES.filter(m => m.supported(caps))

  // Switching to a model that can't do the active sub-mode would otherwise
  // leave the sidebar on a tab with no button to leave it.
  useEffect(() => {
    if (!modes.some(m => m.value === imageMode)) setParam('image_mode', 0)
  }, [allowed, imageMode]) // eslint-disable-line react-hooks/exhaustive-deps

  if (modes.length <= 1) return null

  return (
    <div className="flex bg-bg-tertiary rounded-lg p-0.5 border border-border">
      {modes.map(m => (
        <button
          key={m.value}
          onClick={() => setParam('image_mode', m.value)}
          className={`flex-1 text-xs py-1.5 rounded-md transition-all ${
            imageMode === m.value
              ? 'bg-bg-active text-text-primary'
              : 'text-text-secondary hover:text-text-primary'
          }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  )
}
