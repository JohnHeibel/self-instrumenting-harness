import { useEffect, useState } from 'react'

/** Replay speeds, in run seconds per real second. */
export const SPEEDS = [60, 300, 600, 1800]

export const two = (n: number) => String(Math.floor(n)).padStart(2, '0')

/** Elapsed run time as h:mm:ss. */
export function hms(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 3600)}:${two((s % 3600) / 60)}:${two(s % 60)}`
}

/** Short elapsed label for axes and feeds: 3h 05m, 12m, 40s. */
export function span(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${two(m)}m` : m ? `${m}m` : `${s}s`
}

export function big(n: number): string {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e4) return Math.round(n / 1e3) + 'k'
  return Math.round(n).toLocaleString('en-US')
}

export type DiffLine = { op: ' ' | '+' | '-'; text: string }

/** Line diff by longest common subsequence. Hook sources are small, so O(n·m) is fine. */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n'), b = after.split('\n')
  const n = a.length, m = b.length
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
  const out: DiffLine[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ op: ' ', text: a[i] }); i++; j++ }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: '-', text: a[i++] })
    else out.push({ op: '+', text: b[j++] })
  }
  while (i < n) out.push({ op: '-', text: a[i++] })
  while (j < m) out.push({ op: '+', text: b[j++] })
  return out
}

/** Share of change from the first value to the latest, comparing the mean of the first few with the mean of the last few. */
export function trend(values: number[]): { pct: number } | undefined {
  if (values.length < 2) return undefined
  const k = Math.min(3, Math.floor(values.length / 2))
  const mean = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length
  const first = mean(values.slice(0, k)), last = mean(values.slice(-k))
  return first > 0 ? { pct: (last - first) / first } : undefined
}

/** Least-squares line through (i, v): returns the fitted value at x = 0 and x = n - 1. */
export function fitLine(values: number[]): [number, number] | undefined {
  const n = values.length
  if (n < 3) return undefined
  const mx = (n - 1) / 2, my = values.reduce((s, v) => s + v, 0) / n
  let sxy = 0, sxx = 0
  values.forEach((v, i) => { sxy += (i - mx) * (v - my); sxx += (i - mx) ** 2 })
  const b = sxy / sxx, a = my - b * mx
  return [a, a + b * (n - 1)]
}

/** Re-renders every `ms`, so ages ("2m ago", "behind by 12m") keep counting while nothing else changes. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(id) }, [ms])
  return now
}
