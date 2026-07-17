// Legacy ESLint config (ESLint 8). Kept as .cjs because the workspace is ESM ("type": "module").
// Type-aware linting is intentionally NOT enabled here to keep lint fast; correctness is covered by `tsc`.
module.exports = {
  root: true,
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
  plugins: ['@typescript-eslint'],
  extends: ['eslint:recommended', 'plugin:@typescript-eslint/recommended', 'prettier'],
  env: {
    node: true,
    es2022: true,
  },
  ignorePatterns: [
    'dist/**',
    'node_modules/**',
    'coverage/**',
    '**/*.cjs',
    // kit files — never lint/format these
    'docs/**',
    'refs/**',
    '.claude/**',
    'packages/db/migrations/**',
  ],
  rules: {
    // error-handling rule: an empty catch {} is forbidden (silent failure).
    'no-empty': ['error', { allowEmptyCatch: false }],
    '@typescript-eslint/no-unused-vars': [
      'warn',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    '@typescript-eslint/no-explicit-any': 'warn',
  },
};
