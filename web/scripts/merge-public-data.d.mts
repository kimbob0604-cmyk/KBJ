// scripts/merge-public-data.mjs 의 형(시험에서 import 하려고).
export const NAME_RE: RegExp;
export const QUALITIES: string[];
export const LOGIN_SOURCES: RegExp;
export function manifestEntries(manifest: unknown): ({ name: string } & Record<string, unknown>)[];
export function loginSourcePaths(v: unknown, path?: string): string[];
export function planMerge(
  manifest: unknown,
  files: Record<string, string>,
): { copy: string[]; errors: string[]; ignored: string[] };
