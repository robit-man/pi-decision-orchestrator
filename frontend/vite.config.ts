import { defineConfig } from 'vite';
export default defineConfig({oxc:{jsx:{runtime:'automatic',importSource:'preact'}},build:{outDir:'../backend/static',emptyOutDir:true,sourcemap:false},server:{port:5173,proxy:{'/api':'http://127.0.0.1:8766'}}});
