import { useState } from 'react'
import type { HookView, Run } from '../data/model'
import { diffLines, span } from '../util'

/** One hook: why the agent wrote it, every version, and the source with changes from the previous version. */
export function HookDetail({ hook, run, onClose }: { hook: HookView; run: Run; onClose: () => void }) {
  const [vi, setVi] = useState(hook.versions.length - 1)
  const v = hook.versions[vi], prev = vi > 0 ? hook.versions[vi - 1] : undefined
  return (
    <>
      <div className="modal-backdrop" onClick={onClose} />
      <div className="modal" role="dialog" aria-label={`Hook ${hook.name}`}>
        <div className="hd">
          <span className="name">{hook.name}</span>
          <span className="lang">{v.language}</span>
          <span className="lang">{v.kind === 'in_host' ? 'runs inside the host program' : 'helper outside the host program'}</span>
          {v.dependsOn.length > 0 && <span style={{ color: 'var(--text-muted)' }}>built on {v.dependsOn.join(', ')}</span>}
          <span style={{ color: 'var(--text-muted)' }}>· {hook.calls} calls</span>
          <button className="btn close" onClick={onClose}>Close</button>
        </div>
        <div>
          <div className="versions">
            Version
            {hook.versions.map((x, i) => (
              <button key={x.version} className={`btn ${i === vi ? 'on' : ''}`} onClick={() => setVi(i)}>
                v{x.version} · {span(x.t - run.t0)}
              </button>
            ))}
            {prev && <span>showing changes from v{prev.version}</span>}
          </div>
          <div className="reason" style={{ marginTop: 12 }}>“{v.reason}”</div>
        </div>
        <pre className="code">
          {v.files.map(f => {
            const before = prev?.files.find(p => p.path === f.path)?.content
            const lines = before !== undefined ? diffLines(before, f.content) : f.content.split('\n').map(text => ({ op: ' ' as const, text }))
            return <div key={f.path}>
              <div className="file">{f.path}</div>
              {lines.map((l, i) => (
                <div key={i} className={`ln ${l.op === '+' ? 'add' : l.op === '-' ? 'del' : ''}`}>
                  <span className="g">{l.op === ' ' ? '' : l.op}</span>{l.text}
                </div>
              ))}
            </div>
          })}
        </pre>
      </div>
    </>
  )
}
