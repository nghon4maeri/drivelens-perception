// Test client/adapter pilot không DOM (CONTRACTS §21.16, #96): HTTP giả + request log. Chạy: npm test
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: { environment: 'node', include: ['src/**/*.test.{ts,tsx}'] },
})
