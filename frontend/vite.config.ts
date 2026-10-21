// המסך ב-React. הבנייה נכתבת אל backend/app/web/dist — השרת (FastAPI) מגיש אותה, כך שאין צורך ב-Node בהרצה.
// העיצוב עצמו נשאר /static/app.css (אותו קובץ כמו קודם) — לכן המראה זהה.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react()],
  base: '/app/',
  build: {
    outDir: resolve(__dirname, '../backend/app/web/dist'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        help: resolve(__dirname, 'help.html'),
      },
    },
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000', '/static': 'http://127.0.0.1:8000' },
  },
});
