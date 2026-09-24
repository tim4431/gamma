// Serves this package's skills/ to DeepSeek Harness. A patch row can locate
// only a plugin module, not a folder, so this module finds its own directory
// and mounts dsh's filesystem skill provider on it, beside the default one.
import { fileURLToPath } from 'node:url'
import * as SkillFileSystem from '@deepseek-ai/dsh-skill-filesystem'

export const name = 'gamma-skill'

export function apply(ctx) {
  ctx.plugin(SkillFileSystem, {
    providerName: 'gamma',
    includeDefaultRoots: false,
    customSkillDirs: [fileURLToPath(new URL('./skills', import.meta.url))],
    watch: false,
  })
}
