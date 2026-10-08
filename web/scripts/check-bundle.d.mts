// scripts/check-bundle.mjs 의 형(시험에서 import 하려고).
export interface Rule {
  id: string;
  needle: string;
  why: string;
  ci?: boolean;
}
export interface Finding {
  file: string;
  line: number;
  rule: string;
  why: string;
}
export const PUBLIC_FORBIDDEN: Rule[];
export const LOGIN_FORBIDDEN: Rule[];
export const BUDGET_GZIP: { public: { js: number; css: number }; login: { js: number; css: number } };
export function scanText(name: string, text: string, rules: readonly Rule[]): Finding[];
export function checkBudget(
  files: readonly { name: string; bytes: number }[],
  budget: { js: number; css: number },
): { js: number; css: number; findings: { rule: string; why: string }[] };
