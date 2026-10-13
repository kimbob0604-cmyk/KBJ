// scripts/merge-public-data.mjs 의 형(시험에서 import 하려고).
export const NAME_RE: RegExp;
export const SUBDIRS: string[];
export const SUBDIR_JSON_RE: RegExp;
export const SUBDIR_XLSX_RE: RegExp;
export const DART_ONLY: Set<string>;
export const QUALITIES: string[];
export const LOGIN_SOURCES: RegExp;
export function manifestEntries(manifest: unknown): ({ name: string } & Record<string, unknown>)[];
export function manifestDirs(manifest: unknown): ({ name: string } & Record<string, unknown>)[];
export function loginSourcePaths(v: unknown, path?: string): string[];
export function nonDartSourcePaths(v: unknown, path?: string): string[];
export function watchTracePaths(v: unknown, path?: string): string[];
export function sha256(data: string | Uint8Array): string;
export function dirDigest(items: [string, string][]): string;
export function planMerge(
  manifest: unknown,
  files: Record<string, string | Uint8Array>,
): { copy: string[]; errors: string[]; ignored: string[] };
export function readDataDir(srcAbs: string): Record<string, string | Uint8Array>;
