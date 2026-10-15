/**
 * Recursively converts .jpg / .jpeg / .png to .webp next to each original.
 * Quality: 85
 *
 * Usage: npm run webp
 *    or: node scripts/convert-to-webp.js [optional-root-dir]
 */

const fs = require('fs/promises');
const path = require('path');
const sharp = require('sharp');

const ROOT = path.resolve(process.argv[2] || process.cwd());
const QUALITY = 85;
const EXTENSIONS = new Set(['.jpg', '.jpeg', '.png']);
const SKIP_DIRS = new Set([
  'node_modules',
  '.git',
  '.cursor',
  'dist',
  'build',
  '.next',
]);

async function walk(dir, files = []) {
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (err) {
    console.warn(`Skip unreadable dir: ${dir} (${err.message})`);
    return files;
  }

  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      await walk(full, files);
    } else if (entry.isFile()) {
      const ext = path.extname(entry.name).toLowerCase();
      if (EXTENSIONS.has(ext)) files.push(full);
    }
  }
  return files;
}

async function convertOne(srcPath) {
  const dir = path.dirname(srcPath);
  const base = path.basename(srcPath, path.extname(srcPath));
  const outPath = path.join(dir, `${base}.webp`);

  await sharp(srcPath)
    .webp({ quality: QUALITY, effort: 4 })
    .toFile(outPath);

  const [srcStat, outStat] = await Promise.all([
    fs.stat(srcPath),
    fs.stat(outPath),
  ]);

  const saved = ((1 - outStat.size / srcStat.size) * 100).toFixed(1);
  return { srcPath, outPath, srcSize: srcStat.size, outSize: outStat.size, saved };
}

async function main() {
  console.log(`Scanning: ${ROOT}`);
  console.log(`WebP quality: ${QUALITY}\n`);

  const images = await walk(ROOT);
  if (!images.length) {
    console.log('No .jpg / .jpeg / .png files found.');
    return;
  }

  let ok = 0;
  let fail = 0;

  for (const src of images) {
    const rel = path.relative(ROOT, src);
    try {
      const result = await convertOne(src);
      const outRel = path.relative(ROOT, result.outPath);
      console.log(
        `✓ ${rel} → ${outRel} ` +
          `(${formatKb(result.srcSize)} → ${formatKb(result.outSize)}, −${result.saved}%)`
      );
      ok += 1;
    } catch (err) {
      console.error(`✗ ${rel}: ${err.message}`);
      fail += 1;
    }
  }

  console.log(`\nDone: ${ok} created, ${fail} failed, ${images.length} total.`);
  if (ok > 0) {
    try {
      require('./notify_done.js').notifyDone();
    } catch (_) { /* ignore */ }
  }
}

function formatKb(bytes) {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
