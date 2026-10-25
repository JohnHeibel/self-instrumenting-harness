/** Playback rate of the Sisyphus animation: 1 while the agent's hands are busy, easing to 0 when they are not.
 *  The rate chases its target as a critically damped spring (no overshoot), so it starts and stops gently and covers the
 *  full range, 0 → 1 or 1 → 0, in about three seconds. If the target flips mid-ramp, the rate turns toward the new target
 *  from its current value: it never jumps, and velocity still carrying it the old way is dropped rather than finished. */
export const PUSH_OMEGA = 1.95       // rad/s: 1 − (1 + ωt)e^(−ωt) reaches 0.98 at t ≈ 3 s
export const PUSH_OFF_HOLD_S = 0.4   // hysteresis: hands must stay idle this long (wall time) before the target drops; with
                                     // the 5 s run-time window in Run.handsActive this keeps replay at speed from flickering

export type PushState = { rate: number; vel: number; target: number; offFor: number }
export const pushStart = (): PushState => ({ rate: 0, vel: 0, target: 0, offFor: 0 })

/** Advance by `dt` wall seconds with the agent's hands busy (`active`) or not; `still` (reduced motion) holds at 0. */
export function pushStep(s: PushState, dt: number, active: boolean, still = false): PushState {
  let { rate, vel, target, offFor } = s
  offFor = active ? 0 : offFor + dt
  const next = still ? 0 : active ? 1 : offFor >= PUSH_OFF_HOLD_S ? 0 : target
  if (next !== target) { target = next; if ((target - rate) * vel < 0) vel = 0 }
  if (still) return { rate: 0, vel: 0, target, offFor }
  for (let i = 0, h = dt / 4; i < 4; i++) {  // semi-implicit Euler, four substeps
    vel += (PUSH_OMEGA * PUSH_OMEGA * (target - rate) - 2 * PUSH_OMEGA * vel) * h
    rate += vel * h
  }
  if (Math.abs(target - rate) < 1e-3 && Math.abs(vel) < 1e-3) { rate = target; vel = 0 }
  return { rate: Math.max(0, Math.min(1, rate)), vel, target, offFor }
}
