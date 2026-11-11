// The DeepSeek Harness side of Gamma: this package's skills/ and, when this
// computer is connected, the read-only Gamma MCP server. A patch row can name
// only a plugin module, not a folder or a file to read, so this module finds
// both itself and mounts dsh's own providers on them.
//
// The connection is the one Gamma's setup command saved in
// $DSH_HOME/gamma.json ({"url": ".../mcp", "token": "gamma_..."}), read when
// dsh starts, so every profile and every way of starting dsh finds it.
// GAMMA_URL and GAMMA_TOKEN in the environment take its place. Without either,
// dsh starts without Gamma's tools rather than failing to boot.
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import * as McpClient from '@deepseek-ai/dsh-mcp-client'
import * as SkillFileSystem from '@deepseek-ai/dsh-skill-filesystem'

export const name = 'gamma'

export function connection(env = process.env) {
  if (env.GAMMA_URL) return { url: env.GAMMA_URL, token: env.GAMMA_TOKEN || '' }
  const file = join(env.DSH_HOME || join(homedir(), '.dsh'), 'gamma.json')
  let saved
  try {
    saved = JSON.parse(readFileSync(file, 'utf8'))
  } catch {
    return null
  }
  return typeof saved?.url === 'string' && typeof saved?.token === 'string' && saved.url && saved.token
    ? { url: saved.url, token: saved.token }
    : null
}

export function apply(ctx) {
  ctx.plugin(SkillFileSystem, {
    providerName: 'gamma',
    includeDefaultRoots: false,
    customSkillDirs: [fileURLToPath(new URL('./skills', import.meta.url))],
    watch: false,
  })
  const found = connection()
  if (!found) {
    ctx.logger.warn('gamma: not connected; run the setup command in Gamma, Settings → AI → Integrations → DeepSeek Harness')
    return
  }
  ctx.plugin(McpClient, {
    serverName: 'gamma',
    transport: 'streamable-http',
    url: found.url,
    headers: { Authorization: `Bearer ${found.token}` },
  })
}
