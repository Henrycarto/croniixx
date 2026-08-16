/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The shared packages ship TypeScript source rather than a build step, so
  // Next compiles them alongside the app.
  transpilePackages: ['@croniixx/shared-types', '@croniixx/fhir-client'],
  eslint: {
    dirs: ['app', 'components', 'lib'],
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
    ]
  },
}

export default nextConfig
