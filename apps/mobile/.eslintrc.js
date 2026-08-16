// eslint-config-expo references TypeScript rules that only resolve when the
// plugin is present. In a workspace the plugin hoists to the repo root, so it
// is declared explicitly here rather than relying on where npm happens to
// place it.
module.exports = {
  root: true,
  extends: ['expo'],
  plugins: ['@typescript-eslint'],
  ignorePatterns: ['metro.config.js', 'babel.config.js', '.eslintrc.js', 'expo-env.d.ts'],
  rules: {
    'import/no-unresolved': 'off',
  },
}
