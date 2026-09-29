import { build } from '../../frontend/node_modules/esbuild/lib/main.js';
import { mkdir, copyFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
const root = fileURLToPath(new URL('../', import.meta.url));
await mkdir(root + 'GammaIPad/Resources', { recursive: true });
await build({ entryPoints: [root + 'scripts/ink-entry.mjs'], outfile: root + 'GammaIPad/Resources/gamma-ink.js',
  bundle: true, platform: 'neutral', format: 'iife', target: 'es2020', minify: true });
const icons = root + 'GammaIPad/Resources/Assets.xcassets/AppIcon.appiconset/';
await mkdir(icons, { recursive: true });
await copyFile(root + '../frontend/public/media/icons/icon-512.png', icons + 'icon.png');
if (process.platform === 'darwin') execFileSync('sips', ['-z','1024','1024',icons + 'icon.png']);
await writeFile(icons + 'Contents.json', JSON.stringify({images:[{filename:'icon.png',idiom:'universal',platform:'ios',size:'1024x1024'}],info:{author:'xcode',version:1}}));
