import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// HARNESS_RUNS points the dev server at another directory of runs (default: ../runs).
const RUNS = path.resolve(process.env.HARNESS_RUNS ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '../runs'))

/** Serves local run directories (docs/interfaces/event-log.md) to the UI in development.
 *  GET /api/runs                  -> [{name, files}]
 *  GET /api/runs/<name>/records   -> all *.jsonl records in the run (?since=<ISO ts>: only newer ones)
 *  GET /runs/<name>/shots/<img>   -> screenshot thumbnails
 *  GET /runs/<name>/live/latest.jpg -> the spectator frame (the host window, every few seconds)
 *  GET /runs/<name>/video.json and the video file it names -> the run's recording (byte ranges)
 *  Nothing else under runs/ is served (a run directory also holds credentials). */
function localRuns(): Plugin {
  return {
    name: 'local-runs',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = decodeURIComponent((req.url ?? '').split('?')[0])
        if (url === '/api/runs') {
          const runs = fs.existsSync(RUNS)
            ? fs.readdirSync(RUNS, { withFileTypes: true })
                .filter(d => fs.statSync(path.join(RUNS, d.name)).isDirectory())  // follows symlinked run directories
                .filter(d => fs.readdirSync(path.join(RUNS, d.name)).some(f => f.endsWith('.jsonl')))
                .map(d => ({ name: d.name, mtime: fs.statSync(path.join(RUNS, d.name)).mtimeMs }))
                .sort((a, b) => b.mtime - a.mtime)
            : []
          return send(res, 200, 'application/json', JSON.stringify(runs))
        }
        const m = url.match(/^\/api\/runs\/([^/]+)\/records$/)
        if (m) {
          const dir = safeJoin(RUNS, m[1])
          if (!dir || !fs.existsSync(dir)) return send(res, 404, 'text/plain', 'no such run')
          // ?since=<ISO ts>: only records at or after it, for live polling (clients dedupe by writer+seq).
          const since = new URLSearchParams((req.url ?? '').split('?')[1] ?? '').get('since') ?? ''
          // Estimates made afterwards (tools/leverage_from_commands.py) live in <run>/estimates/ and are read too: their
          // records say `source: "estimated_from_host_commands"`, so the UI labels them.
          const files = [...fs.readdirSync(dir).filter(f => f.endsWith('.jsonl')),
            ...(fs.existsSync(path.join(dir, 'estimates')) ? fs.readdirSync(path.join(dir, 'estimates')).filter(f => f.endsWith('.jsonl')).map(f => `estimates/${f}`) : [])]
          const records = files.flatMap(f =>
            fs.readFileSync(path.join(dir, f), 'utf8').split('\n').filter(Boolean).flatMap(line => {
              try { return [JSON.parse(line)] } catch { return [] }  // a line still being written
            })).filter(r => !since || (typeof r.ts === 'string' && r.ts >= since))
          res.setHeader('Cache-Control', 'no-store')
          return send(res, 200, 'application/json', JSON.stringify(records))
        }
        // A run's recorded video: <run>/video.json and the file it names (<run>/<name>.mp4|webm or <run>/video/<name>),
        // with byte ranges so the player can seek.
        const vid = url.match(/^\/runs\/([^/]+)\/(video\.json|(?:video\/)?[A-Za-z0-9_.-]+\.(?:mp4|webm))$/)
        if (vid) {
          const file = safeJoin(RUNS, `${vid[1]}/${vid[2]}`)
          if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, 'text/plain', 'not found')
          if (!fs.realpathSync(file).startsWith(fs.realpathSync(path.join(RUNS, vid[1])) + path.sep)) return send(res, 404, 'text/plain', 'not found')
          if (vid[2] === 'video.json') { res.setHeader('Cache-Control', 'no-store'); return send(res, 200, 'application/json', fs.readFileSync(file)) }
          const size = fs.statSync(file).size, type = file.endsWith('.webm') ? 'video/webm' : 'video/mp4'
          const range = /bytes=(\d*)-(\d*)/.exec(String(req.headers.range ?? ''))
          res.setHeader('Accept-Ranges', 'bytes'); res.setHeader('Content-Type', type)
          if (!range) { res.setHeader('Content-Length', size); res.statusCode = 200; return void fs.createReadStream(file).pipe(res) }
          const startB = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]))
          const endB = range[1] && range[2] ? Math.min(size - 1, Number(range[2])) : size - 1
          res.statusCode = 206
          res.setHeader('Content-Range', `bytes ${startB}-${endB}/${size}`); res.setHeader('Content-Length', endB - startB + 1)
          return void fs.createReadStream(file, { start: startB, end: endB }).pipe(res)
        }
        // Saved artifacts only: screenshots under <run>/shots/. A run directory also holds
        // credentials (codex-home/auth.json, host/rcon.json), so nothing else is served.
        const a = url.match(/^\/runs\/([^/]+)\/(shots\/[A-Za-z0-9_.-]+\.(?:png|jpe?g)|live\/latest\.jpg)$/)
        if (a) {
          const file = safeJoin(RUNS, `${a[1]}/${a[2]}`)
          if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, 'text/plain', 'not found')
          // A symlink inside a run directory must not lead out of it (the run directory itself may be a symlink).
          if (!fs.realpathSync(file).startsWith(fs.realpathSync(path.join(RUNS, a[1])) + path.sep)) return send(res, 404, 'text/plain', 'not found')
          const type = file.endsWith('.png') ? 'image/png' : 'image/jpeg'
          // Thumbnails never change; the spectator frame is rewritten every few seconds.
          const spectator = a[2].startsWith('live/')
          res.setHeader('Cache-Control', spectator ? 'no-store' : 'max-age=3600')
          if (spectator) res.setHeader('Last-Modified', new Date(fs.statSync(file).mtimeMs).toUTCString())
          return send(res, 200, type, req.method === 'HEAD' ? '' : fs.readFileSync(file))
        }
        if (url.startsWith('/runs/')) return send(res, 404, 'text/plain', 'not found')
        next()
      })
    },
  }
}

function safeJoin(root: string, rel: string): string | null {
  const p = path.resolve(root, rel)
  return p.startsWith(root + path.sep) ? p : null
}

function send(res: import('node:http').ServerResponse, status: number, type: string, body: string | Buffer) {
  res.statusCode = status
  res.setHeader('Content-Type', type)
  res.end(body)
}

export default defineConfig({
  plugins: [react(), localRuns()],
})
