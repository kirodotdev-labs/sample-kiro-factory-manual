import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { FactorySettings } from './settings';

const MICROVM_DIR = path.join(__dirname, '..', 'microvm');

/**
 * The worker image's build context: microvm/'s files, and bundle/,
 * the instance rendered against the Manual for this host. A Manual or instance change changes the
 * context, so Lambda rebuilds the image.
 */
export function renderWorkerContext(settings: FactorySettings): string {
  const render = path.join(settings.manualDir, 'runtime', 'render', 'render.py');
  if (!existsSync(render)) throw new Error(`${settings.manualDir} has no runtime/render/render.py`);
  const context = mkdtempSync(path.join(tmpdir(), `${settings.name}-worker-`));
  for (const file of ['Dockerfile', 'requirements.txt', 'worker.py', 'run_credentials.py', 'gh-wrapper.sh']) {
    copyFileSync(path.join(MICROVM_DIR, file), path.join(context, file));
  }
  const modules = Object.entries(settings.modules).flatMap(([name, dir]) => ['--module', `${name}=${dir}`]);
  execFileSync(
    'python3',
    [
      render,
      '--manual',
      settings.manualDir,
      '--instance',
      settings.instanceDir,
      '--host',
      settings.hostProfile,
      '--out',
      path.join(context, 'bundle'),
      ...modules,
    ],
    { stdio: ['ignore', 'ignore', 'inherit'] },
  );
  return context;
}
