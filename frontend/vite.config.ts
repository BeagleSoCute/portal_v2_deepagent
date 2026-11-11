import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The backend of this service. Port 8000 belongs to the Portal on a dev machine.
const backend = 'http://127.0.0.1:8100';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5190,
    strictPort: true,
    proxy: { '/api': backend, '/auth': backend, '/downloads': backend },
  },
});
