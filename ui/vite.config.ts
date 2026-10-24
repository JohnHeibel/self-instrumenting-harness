import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const RUNS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../runs')

/** Serves local run directories (docs/interfaces/event-log.md) to the UI in development.
 *  GET /api/runs                  -> [{name, files}]
 *  GET /api/runs/<name>/records   -> all *.jsonl records in the run, merged and ordered
 *  GET /runs/<name>/<artifact>    -> saved files such as screenshot thumbnails */
function localRuns(): Plugin {
  return {
    name: 'local-runs',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = decodeURIComponent((req.url ?? '').split('?')[0])
        if (url === '/api/runs') {
          const runs = fs.existsSync(RUNS)
            ? fs.readdirSync(RUNS, { withFileTypes: true }).filter(d => d.isDirectory())
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
          const records = fs.readdirSync(dir).filter(f => f.endsWith('.jsonl')).flatMap(f =>
            fs.readFileSync(path.join(dir, f), 'utf8').split('\n').filter(Boolean).flatMap(line => {
              try { return [JSON.parse(line)] } catch { return [] }  // a line still being written
            }))
          return send(res, 200, 'application/json', JSON.stringify(records))
        }
        const a = url.match(/^\/runs\/(.+)$/)
        if (a) {
          const file = safeJoin(RUNS, a[1])
          if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, 'text/plain', 'not found')
          const type = file.endsWith('.png') ? 'image/png' : file.endsWith('.jpg') ? 'image/jpeg' : 'application/octet-stream'
          res.setHeader('Cache-Control', 'max-age=3600')
          return send(res, 200, type, fs.readFileSync(file))
        }
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
