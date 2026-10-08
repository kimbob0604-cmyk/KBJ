// ESLint(평면 설정) — 타입 정보 규칙 + KBJ 구조 규칙.
//
// 구조 규칙(docs/p3_design.md §6.6):
// - data/api·data/auth(로그인 코드)는 boot/login.ts 만 import 한다 → 공개 번들에 섞이지 않게.
// - pages/tradingview(공개 전용)는 boot/public.ts 만 import 한다(형 import 는 허용) → 로그인 번들에 TradingView 0.
// - innerHTML·outerHTML·insertAdjacentHTML·document.write 금지 → 문자열 HTML 없이 ui/dom.ts 의 h() 로.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';

const NODE_GLOBALS = {
  process: 'readonly',
  console: 'readonly',
  URL: 'readonly',
  Buffer: 'readonly',
};

const restrictedImports = (allow) => ({
  '@typescript-eslint/no-restricted-imports': [
    'error',
    {
      patterns: [
        ...(allow.includes('login')
          ? []
          : [
              {
                group: ['**/data/api', '**/data/auth', '**/boot/login'],
                message: '로그인 코드는 boot/login.ts 만 import 한다(공개 번들 분리 — §6.6).',
              },
            ]),
        ...(allow.includes('public')
          ? []
          : [
              {
                group: ['**/pages/tradingview', '**/boot/public'],
                message: 'TradingView 는 boot/public.ts 만 import 한다(로그인 번들 분리). 형은 import type 으로.',
                allowTypeImports: true,
              },
            ]),
      ],
    },
  ],
});

export default tseslint.config(
  { ignores: ['dist-public/**', 'dist-login/**', 'node_modules/**', 'coverage/**', 'src/api/types.gen.ts'] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: { allowDefaultProject: ['eslint.config.js', 'scripts/*.mjs'] },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      'no-restricted-properties': [
        'error',
        { property: 'innerHTML', message: '문자열 HTML 금지 — ui/dom.ts 의 h() 를 쓴다.' },
        { property: 'outerHTML', message: '문자열 HTML 금지.' },
        { property: 'insertAdjacentHTML', message: '문자열 HTML 금지.' },
        { object: 'document', property: 'write', message: 'document.write 금지.' },
      ],
      'no-console': ['error', { allow: ['error', 'warn'] }],
      eqeqeq: ['error', 'always'],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      '@typescript-eslint/no-confusing-void-expression': 'off',
      ...restrictedImports([]),
    },
  },
  { files: ['src/boot/login.ts', 'src/main.ts'], rules: restrictedImports(['login']) },
  { files: ['src/boot/public.ts', 'src/main.ts'], rules: restrictedImports(['public']) },
  { files: ['src/main.ts'], rules: restrictedImports(['login', 'public']) },
  {
    // 등급별 코드를 직접 시험한다
    files: ['test/**/*.ts'],
    rules: {
      ...restrictedImports(['login', 'public']),
      '@typescript-eslint/no-non-null-assertion': 'off',
      '@typescript-eslint/require-await': 'off',
    },
  },
  {
    files: ['scripts/*.mjs', 'eslint.config.js'],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: { globals: NODE_GLOBALS },
    rules: { ...tseslint.configs.disableTypeChecked.rules, 'no-console': 'off' },
  },
  {
    files: ['scripts/*.d.mts'],
    rules: { '@typescript-eslint/no-unused-vars': 'off' },
  },
);
