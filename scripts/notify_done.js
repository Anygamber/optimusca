#!/usr/bin/env node
/**
 * Play completion sound after Node automation scripts.
 * Audio: C:\Users\user\Desktop\Custom\block_changes.wav
 */
const { spawnSync } = require('child_process');
const path = require('path');

const SOUND = process.env.COMPLETION_SOUND || 'C:\\Users\\user\\Desktop\\Custom\\block_changes.wav';
const HELPER = path.join('C:\\Users\\user\\Desktop\\Custom', 'notify_done.py');

function notifyDone() {
  const py = spawnSync('python', [HELPER], { stdio: 'ignore', windowsHide: true });
  if (py.status === 0) return true;

  const ps = `
Add-Type -AssemblyName PresentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([uri]'${SOUND.replace(/'/g, "''")}')
$p.Play()
Start-Sleep -Milliseconds 1500
$p.Stop(); $p.Close()
`;
  const r = spawnSync(
    'powershell',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
    { stdio: 'ignore', windowsHide: true }
  );
  return r.status === 0;
}

if (require.main === module) {
  process.exit(notifyDone() ? 0 : 1);
}

module.exports = { notifyDone, SOUND };
