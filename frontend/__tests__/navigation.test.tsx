/**
 * Navigation after the Recovery Messages merge.
 *
 * Recovery Messages was a separate page showing the reasoning, tone, urgency
 * and compliance verdict for a case — split from the conversation those things
 * describe. It is now part of Communications, and these tests hold that: the
 * route is gone, the sidebar entry is gone, and nothing still links to either.
 */

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const APP = path.join(process.cwd(), 'app');
const COMPONENTS = path.join(process.cwd(), 'components');

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sourceFiles(full));
    else if (/\.tsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

const ALL = [...sourceFiles(APP), ...sourceFiles(COMPONENTS)];

describe('the Recovery Messages route is gone', () => {
  it('has no /scripts page', () => {
    expect(fs.existsSync(path.join(APP, 'scripts'))).toBe(false);
  });

  it('is not linked from anywhere', () => {
    const offenders = ALL.filter((file) =>
      /href=(["'`])\/scripts\1|href=\{`\/scripts/.test(fs.readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('is not in the sidebar', () => {
    const header = fs.readFileSync(
      path.join(COMPONENTS, 'ui', 'site-header.tsx'),
      'utf8',
    );
    expect(header).not.toContain('Recovery Messages');
    expect(header).not.toContain("'/scripts'");
  });

  it('leaves no dangling label for a page that no longer exists', () => {
    const offenders = ALL.filter((file) =>
      fs.readFileSync(file, 'utf8').includes('Back to Recovery Messages'),
    );
    expect(offenders).toEqual([]);
  });

  it('still routes an old origin somewhere real', () => {
    // Bookmarks and older links carry ?from=scripts. They should land where
    // that content now lives rather than on a dead route.
    const detail = fs.readFileSync(
      path.join(APP, 'events', '[id]', 'page.tsx'),
      'utf8',
    );
    expect(detail).toMatch(/scripts:\s*\{[^}]*'\/communications'/s);
  });
});

describe('Communications is still reachable', () => {
  it('has a page', () => {
    expect(fs.existsSync(path.join(APP, 'communications', 'page.tsx'))).toBe(true);
  });

  it('is in the sidebar exactly once', () => {
    const header = fs.readFileSync(
      path.join(COMPONENTS, 'ui', 'site-header.tsx'),
      'utf8',
    );
    const matches = header.match(/label: 'Communications'/g) ?? [];
    expect(matches).toHaveLength(1);
  });
});
